from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings
from cinematic_story_service.audition_repository import AuditionRepository
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.local_speech import (
    SPEECH_SAMPLE_RATE_HZ,
    SpeechArtifact,
    SpeechInvocationContext,
    SpeechSynthesisRequest,
    encode_pcm16_wav,
)
from cinematic_story_service.model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    ModelPackageManager,
    ModelPackageVerification,
)
from cinematic_story_service.schemas import (
    InstallModelPackageRequest,
    ModelInstallationOperationRequest,
)
from cinematic_story_service.speech_providers import KOKORO_ADAPTER_VERSION
from cinematic_story_service.speech_runtime import (
    SPEECH_RUNTIME_PROTOCOL_VERSION,
    ManagedSpeechRuntime,
    SpeechRuntimeConfig,
    SpeechRuntimeExitEvidence,
    SpeechWorkerIdentity,
)
from cinematic_story_service.util import (
    request_fingerprint,
    sha256_bytes,
    utc_now,
)
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _establish_approved_cast,
    _workspace,
)


class _VerifiedModelPackageManager:
    def __init__(self, package_path: Path) -> None:
        self.package_path = package_path
        self.active = False

    def _verification(self) -> ModelPackageVerification:
        return ModelPackageVerification(
            package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            package_version=KOKORO_LOCAL_ONNX_MANIFEST.package_version,
            package_path=self.package_path,
            manifest_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
            valid=True,
            error_codes=(),
            verified_files=tuple(
                artifact.path for artifact in KOKORO_LOCAL_ONNX_MANIFEST.artifacts
            ),
            total_size_bytes=KOKORO_LOCAL_ONNX_MANIFEST.total_size_bytes,
        )

    def install_from_archive(
        self,
        _archive: Path,
        _manifest: object,
    ) -> ModelPackageVerification:
        return self._verification()

    def repair(
        self,
        _archive: Path,
        _manifest: object,
    ) -> ModelPackageVerification:
        return self._verification()

    def verify(self, _manifest: object) -> ModelPackageVerification:
        return self._verification()

    def activate(self, _manifest: object) -> Path:
        self.active = True
        return self.package_path

    def deactivate(self) -> bool:
        was_active = self.active
        self.active = False
        return was_active

    def remove(self, _manifest: object) -> bool:
        return True


class _CapturingRuntime:
    def __init__(
        self,
        config: SpeechRuntimeConfig,
        calls: list[tuple[SpeechSynthesisRequest, SpeechInvocationContext]],
    ) -> None:
        self.config = config
        self.calls = calls
        self.running = False
        self._identity: SpeechWorkerIdentity | None = None
        self._last_exit: SpeechRuntimeExitEvidence | None = None

    @property
    def is_running(self) -> bool:
        return self.running

    @property
    def last_exit(self) -> SpeechRuntimeExitEvidence | None:
        return self._last_exit

    @property
    def denied_network_attempt_count(self) -> int:
        return 0

    def start(self) -> SpeechWorkerIdentity:
        self.running = True
        if self._identity is None:
            self._identity = SpeechWorkerIdentity(
                pid=os.getpid(),
                parent_pid=os.getppid(),
                launcher_pid=os.getpid(),
                process_parent_pid=os.getppid(),
                executable=Path(sys.executable),
                created_at_unix_ns=time.time_ns(),
                creation_nonce="real-provider-focused-test",
                protocol_version=SPEECH_RUNTIME_PROTOCOL_VERSION,
                provider_id=self.config.provider_id,
                runtime_id=self.config.runtime_id,
                runtime_version=self.config.runtime_version,
                model_id=self.config.model_id,
                model_version=self.config.model_version,
                model_manifest_fingerprint=self.config.model_manifest_fingerprint,
                launch_mode="python-module",
                ownership_job_name="phase3b-real-provider-focused-test",
                job_object_assigned=True,
                denied_network_attempt_count=0,
            )
        return self._identity

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
        *,
        on_dispatch_committed: Callable[[], None] | None = None,
        expected_identity: SpeechWorkerIdentity | None = None,
    ) -> SpeechArtifact:
        assert expected_identity is None or expected_identity == self._identity
        if on_dispatch_committed is not None:
            on_dispatch_committed()
        self.calls.append((request, context))
        samples = tuple(1_200 if index % 2 == 0 else -1_200 for index in range(4_800))
        wav_bytes = encode_pcm16_wav(samples, sample_rate_hz=SPEECH_SAMPLE_RATE_HZ)
        model_artifact = next(
            artifact
            for artifact in KOKORO_LOCAL_ONNX_MANIFEST.artifacts
            if artifact.role == "model"
        )
        voice_artifact = next(
            artifact
            for artifact in KOKORO_LOCAL_ONNX_MANIFEST.artifacts
            if artifact.role == "voice"
        )
        now = utc_now()
        return SpeechArtifact(
            provider_id=self.config.provider_id,
            adapter_id=self.config.provider_id,
            adapter_version=KOKORO_ADAPTER_VERSION,
            runtime_id=self.config.runtime_id,
            runtime_version=self.config.runtime_version,
            model_id=self.config.model_id,
            model_version=self.config.model_version,
            model_sha256=model_artifact.sha256,
            voice_id=request.voice_id,
            voice_sha256=voice_artifact.sha256,
            input_fingerprint=request.input_fingerprint(),
            configuration_fingerprint=request_fingerprint(
                {
                    "providerId": self.config.provider_id,
                    "runtimeId": self.config.runtime_id,
                    "voiceId": request.voice_id,
                }
            ),
            wav_bytes=wav_bytes,
            wav_sha256=sha256_bytes(wav_bytes),
            sample_rate_hz=SPEECH_SAMPLE_RATE_HZ,
            channels=1,
            sample_width_bytes=2,
            frame_count=len(samples),
            deterministic=False,
            warnings=(),
            started_at=now,
            completed_at=now,
        )

    def stop(self, *, reason: str = "clean") -> SpeechRuntimeExitEvidence:
        del reason
        self.running = False
        self._last_exit = SpeechRuntimeExitEvidence(
            pid=os.getpid(),
            launcher_pid=os.getpid(),
            exit_code=0,
            reason="clean",
            ownership_confirmed=True,
            shutdown_acknowledged=True,
            graceful_shutdown_confirmed=True,
            terminated_by_parent=False,
            confirmed_exited=True,
            job_object_assigned=True,
            owned_processes_confirmed_exited=True,
            denied_network_attempt_count=0,
        )
        return self._last_exit


def test_real_provider_reports_incompatible_synthetic_cast_without_silent_fallback(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auditions = cast(AuditionRepository, app.state.auditions)
    package_path = (
        settings.data_dir
        / "models"
        / "packages"
        / KOKORO_LOCAL_ONNX_MANIFEST.package_id
        / KOKORO_LOCAL_ONNX_MANIFEST.package_version
    )
    package_path.mkdir(parents=True)
    manager = _VerifiedModelPackageManager(package_path)
    monkeypatch.setattr(
        auditions,
        "_model_package_manager",
        cast(ModelPackageManager, manager),
    )
    synthesis_calls: list[tuple[SpeechSynthesisRequest, SpeechInvocationContext]] = []

    def runtime_factory(config: SpeechRuntimeConfig) -> ManagedSpeechRuntime:
        return cast(ManagedSpeechRuntime, _CapturingRuntime(config, synthesis_calls))

    monkeypatch.setattr(auditions, "_runtime_factory", runtime_factory)

    project_id, _casting_run = _establish_approved_cast(
        client,
        auth_headers,
        key="phase3b-real-provider",
    )
    _activate_fixture_model(
        client,
        auth_headers,
        project_id=project_id,
        key="phase3b-real-provider",
    )
    fixture_workspace = _workspace(client, auth_headers, project_id)
    role = next(
        item for item in fixture_workspace["roles"]["items"] if item["sessionEvidence"] is not None
    )
    fixture_evidence = role["sessionEvidence"]
    assert fixture_evidence["providerId"] == "deterministic-pcm-wav-fixture"
    fixture_session_response = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": role["roleId"],
            "evidence": fixture_evidence,
            "idempotencyKey": "phase3b-real-provider-fixture-session",
        },
    )
    assert fixture_session_response.status_code == 200, fixture_session_response.text
    fixture_session = fixture_session_response.json()["session"]
    assert fixture_session["providerId"] == "deterministic-pcm-wav-fixture"
    unavailable = next(
        item for item in fixture_workspace["providers"] if item["providerId"] == "kokoro-local-onnx"
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["statusReasonCode"] == "MANAGED_MODEL_INSTALLATION_REQUIRED"

    packages, _cursor, _total = auditions.list_model_packages(
        project_id=project_id,
        cursor=None,
        limit=200,
    )
    kokoro_manifest = next(
        item["manifest"]
        for item in packages
        if item["manifest"]["providerId"] == "kokoro-local-onnx"
    )
    archive_path = settings.data_dir / "model-staging" / "focused-real-provider.zip"
    archive_path.write_bytes(b"focused private model archive")
    with pytest.raises(ServiceError) as acknowledgement_error:
        auditions.install_model_package(
            project_id=project_id,
            model_package_id=kokoro_manifest["modelPackageId"],
            request=InstallModelPackageRequest(
                expected_manifest_fingerprint=kokoro_manifest["manifestFingerprint"],
                expected_installation_revision=None,
                acknowledge_restricted_local_use=False,
                reason="Reject a missing restricted-local-use acknowledgement.",
                idempotency_key="phase3b-real-provider-missing-ack",
            ),
            archive_path=archive_path,
            actor_id="local_user",
        )
    assert acknowledgement_error.value.code == "RESTRICTED_MODEL_ACKNOWLEDGEMENT_REQUIRED"
    installed = auditions.install_model_package(
        project_id=project_id,
        model_package_id=kokoro_manifest["modelPackageId"],
        request=InstallModelPackageRequest(
            expected_manifest_fingerprint=kokoro_manifest["manifestFingerprint"],
            expected_installation_revision=None,
            acknowledge_restricted_local_use=True,
            reason="Install the focused restricted local provider fixture.",
            idempotency_key="phase3b-real-provider-install",
        ),
        archive_path=archive_path,
        actor_id="local_user",
    )
    acknowledgement_event_id = installed["installation"]["immutableEventId"]
    assert installed["verification"]["status"] == "verified"
    assert installed["installation"]["provenance"]["details"] == {
        "acknowledgementScope": "managed_model_installation",
        "projectId": project_id,
        "restrictedLocalUseAcknowledged": True,
    }
    activated = auditions.perform_model_package_action(
        project_id=project_id,
        request=ModelInstallationOperationRequest(
            model_package_id=kokoro_manifest["modelPackageId"],
            expected_manifest_fingerprint=kokoro_manifest["manifestFingerprint"],
            expected_installation_revision=installed["installation"]["installationRevision"],
            action="activate",
            reason="Activate the exact verified restricted local provider.",
            idempotency_key="phase3b-real-provider-activate",
        ),
        actor_id="local_user",
    )
    assert activated["installation"]["status"] == "active"

    real_workspace = _workspace(client, auth_headers, project_id)
    available = next(
        item for item in real_workspace["providers"] if item["providerId"] == "kokoro-local-onnx"
    )
    assert available["status"] == "available"
    assert available["statusReasonCode"] is None
    incompatible_role = next(
        item for item in real_workspace["roles"]["items"] if item["roleId"] == role["roleId"]
    )
    assert incompatible_role["sessionEvidence"] is None
    assert incompatible_role["generationRequest"] is None
    assert incompatible_role["voiceRuntimeBinding"] is None
    assert incompatible_role["runtimeBindingStatus"] == "incompatible"
    assert incompatible_role["runtimeBindingReasonCode"] == "VOICE_RUNTIME_BINDING_INCOMPATIBLE"
    assert synthesis_calls == []

    deactivated = auditions.perform_model_package_action(
        project_id=project_id,
        request=ModelInstallationOperationRequest(
            model_package_id=kokoro_manifest["modelPackageId"],
            expected_manifest_fingerprint=kokoro_manifest["manifestFingerprint"],
            expected_installation_revision=activated["installation"]["installationRevision"],
            action="deactivate",
            reason="Return new sessions to the deterministic fixture.",
            idempotency_key="phase3b-real-provider-deactivate",
        ),
        actor_id="local_user",
    )
    assert deactivated["installation"]["status"] == "inactive"
    fallback_workspace = _workspace(client, auth_headers, project_id)
    fallback_provider = next(
        item
        for item in fallback_workspace["providers"]
        if item["providerId"] == "kokoro-local-onnx"
    )
    assert fallback_provider["status"] == "unavailable"
    fallback_role = next(
        item for item in fallback_workspace["roles"]["items"] if item["roleId"] == role["roleId"]
    )
    fallback_evidence = fallback_role["sessionEvidence"]
    assert fallback_evidence["providerId"] == "deterministic-pcm-wav-fixture"
    fallback_session_response = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": role["roleId"],
            "evidence": fallback_evidence,
            "idempotencyKey": "phase3b-real-provider-fallback-session",
        },
    )
    assert fallback_session_response.status_code == 200, fallback_session_response.text
    assert (
        fallback_session_response.json()["session"]["providerId"] == "deterministic-pcm-wav-fixture"
    )
    assert acknowledgement_event_id
