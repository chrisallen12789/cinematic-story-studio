from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import pytest

from cinematic_story_service.local_speech import (
    SpeechArtifact,
    SpeechInvocationContext,
    SpeechSynthesisRequest,
    encode_pcm16_wav,
    inspect_pcm_wav,
)
from cinematic_story_service.model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    ModelPackageVerification,
)
from cinematic_story_service.speech_runtime import (
    SPEECH_RUNTIME_PROTOCOL_VERSION,
    SpeechRuntimeConfig,
    SpeechRuntimeExitEvidence,
    SpeechWorkerIdentity,
)
from cinematic_story_service.util import sha256_bytes


def _load_verifier() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "verify_real_speech_provider.py"
    spec = importlib.util.spec_from_file_location("phase3b_real_provider_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()

_TIMESTAMP = "2026-07-31T15:30:00.000Z"
_NONCE = "a" * 32
_EXECUTABLE_SHA256 = hashlib.sha256(b"injected-worker-executable").hexdigest()


def _prepare_fixed_storage(repository_root: Path) -> Path:
    package_path = repository_root / "local-models" / "kokoro-phase3b"
    package_path.mkdir(parents=True)
    (repository_root / "local-renders").mkdir()
    return package_path


def _verified_package(package_path: Path) -> ModelPackageVerification:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    return ModelPackageVerification(
        package_id=manifest.package_id,
        package_version=manifest.package_version,
        package_path=package_path,
        manifest_fingerprint=manifest.fingerprint,
        valid=True,
        error_codes=(),
        verified_files=tuple(sorted(value.path for value in manifest.artifacts)),
        total_size_bytes=manifest.total_size_bytes,
    )


def _artifact(request: SpeechSynthesisRequest) -> SpeechArtifact:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    model = next(value for value in manifest.artifacts if value.role == "model")
    voice = next(value for value in manifest.artifacts if value.role == "voice")
    samples = tuple(
        0 if index < 120 or index >= 5_880 else (4_000 if index % 2 else -4_000)
        for index in range(6_000)
    )
    wav_bytes = encode_pcm16_wav(samples, sample_rate_hz=24_000)
    return SpeechArtifact(
        provider_id=manifest.provider_id,
        adapter_id=manifest.provider_id,
        adapter_version=manifest.provider_version,
        runtime_id=manifest.runtime_id,
        runtime_version=manifest.runtime_version,
        model_id=manifest.model_id,
        model_version=manifest.model_version,
        model_sha256=model.sha256,
        voice_id=manifest.voice_id,
        voice_sha256=voice.sha256,
        input_fingerprint=request.input_fingerprint(),
        configuration_fingerprint=hashlib.sha256(b"injected-configuration").hexdigest(),
        wav_bytes=wav_bytes,
        wav_sha256=sha256_bytes(wav_bytes),
        sample_rate_hz=24_000,
        channels=1,
        sample_width_bytes=2,
        frame_count=6_000,
        deterministic=False,
        warnings=(),
        started_at="2026-07-31T15:29:59.000Z",
        completed_at="2026-07-31T15:30:00.000Z",
    )


class _InjectedRuntime:
    def __init__(
        self,
        config: SpeechRuntimeConfig,
        *,
        ownership_confirmed: bool = True,
        shutdown_acknowledged: bool = True,
    ) -> None:
        self.config = config
        self._ownership_confirmed = ownership_confirmed
        self._shutdown_acknowledged = shutdown_acknowledged
        self._running = False
        self.requests: list[tuple[SpeechSynthesisRequest, SpeechInvocationContext]] = []
        self.stop_calls = 0
        self._identity: SpeechWorkerIdentity | None = None
        self._last_exit: SpeechRuntimeExitEvidence | None = None

    @property
    def identity(self) -> SpeechWorkerIdentity | None:
        return self._identity

    @property
    def last_exit(self) -> SpeechRuntimeExitEvidence | None:
        return self._last_exit

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> SpeechWorkerIdentity:
        self._running = True
        self._identity = SpeechWorkerIdentity(
            pid=41_001,
            parent_pid=os.getpid(),
            launcher_pid=41_000,
            process_parent_pid=41_000,
            executable=Path(sys.executable).resolve(strict=True),
            created_at_unix_ns=1_786_000_000_000_000_000,
            creation_nonce="injected-authenticated-creation-nonce",
            protocol_version=SPEECH_RUNTIME_PROTOCOL_VERSION,
            provider_id=self.config.provider_id,
            runtime_id=self.config.runtime_id,
            runtime_version=self.config.runtime_version,
            model_id=self.config.model_id,
            model_version=self.config.model_version,
            model_manifest_fingerprint=self.config.model_manifest_fingerprint,
            launch_mode="python-module",
            ownership_job_name="phase3b-injected-owned-job",
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
        assert self._running
        assert expected_identity is None or expected_identity == self._identity
        if on_dispatch_committed is not None:
            on_dispatch_committed()
        self.requests.append((request, context))
        return _artifact(request)

    def stop(
        self,
        *,
        reason: Literal["clean", "idle"] = "clean",
    ) -> SpeechRuntimeExitEvidence | None:
        self.stop_calls += 1
        self._running = False
        assert self._identity is not None
        self._last_exit = SpeechRuntimeExitEvidence(
            pid=self._identity.pid,
            launcher_pid=self._identity.launcher_pid,
            exit_code=0,
            reason=reason,
            ownership_confirmed=self._ownership_confirmed,
            shutdown_acknowledged=self._shutdown_acknowledged,
            graceful_shutdown_confirmed=(self._ownership_confirmed and self._shutdown_acknowledged),
            terminated_by_parent=False,
            confirmed_exited=True,
            job_object_assigned=True,
            owned_processes_confirmed_exited=True,
            denied_network_attempt_count=0,
        )
        return self._last_exit


def _run(
    repository_root: Path,
    runtime_factory: Any,
    **overrides: Any,
) -> Any:
    package_path = repository_root / "local-models" / "kokoro-phase3b"
    arguments: dict[str, Any] = {
        "restricted_local_use_acknowledged": True,
        "repository_root": repository_root,
        "runtime_factory": runtime_factory,
        "model_verifier": lambda path, _manifest: _verified_package(path),
        "platform_verifier": lambda: None,
        "timestamp_factory": lambda: _TIMESTAMP,
        "nonce_factory": lambda: _NONCE,
        "executable_hasher": lambda _path: (27, _EXECUTABLE_SHA256),
    }
    arguments.update(overrides)
    assert package_path.is_dir()
    return verifier.run_verification(**arguments)


def test_command_uses_only_fixed_input_and_publishes_redacted_atomic_evidence(
    tmp_path: Path,
) -> None:
    package_path = _prepare_fixed_storage(tmp_path)
    runtimes: list[_InjectedRuntime] = []

    def runtime_factory(config: SpeechRuntimeConfig) -> _InjectedRuntime:
        runtime = _InjectedRuntime(config)
        runtimes.append(runtime)
        return runtime

    result = _run(tmp_path, runtime_factory)

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert runtime.config.model_package_path == package_path
    assert runtime.config.max_retries == 0
    assert runtime.config.request_timeout_seconds == verifier.REQUEST_TIMEOUT_SECONDS
    assert runtime.stop_calls == 1
    request, context = runtime.requests[0]
    assert request.text == verifier.SYNTHETIC_PHRASE
    assert request.pronunciation_overrides == (verifier.SYNTHETIC_OVERRIDE,)
    assert request.pronunciation_overrides[0].representation == "neutral"
    assert context.invocation_purpose == "component_verification"
    assert context.restricted_voice_acknowledged is True
    assert context.rights_record_id is None
    assert context.rights_record_revision is None
    assert context.network_access_permitted is False

    evidence_path = tmp_path / Path(result.evidence_path)
    wav_path = tmp_path / Path(result.wav_path)
    assert evidence_path.is_file()
    assert wav_path.is_file()
    assert inspect_pcm_wav(wav_path.read_bytes()) == (24_000, 1, 2, 6_000)
    evidence_text = evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert evidence == result.evidence
    assert evidence["result"] == "passed"
    assert evidence["evidenceClassification"] == "component_only_unbound_real_provider_verification"
    assert evidence["scope"]["componentOnlyVerification"] is True
    assert evidence["scope"]["governedPhase3aVoiceProfileBound"] is False
    assert evidence["scope"]["governedRightsRecordBound"] is False
    assert evidence["scope"]["restrictedLocalUseAcknowledged"] is True
    assert evidence["scope"]["humanListeningPerformed"] is False
    assert evidence["request"]["inputTextRecorded"] is False
    assert (
        evidence["request"]["inputTextSha256"]
        == hashlib.sha256(verifier.SYNTHETIC_PHRASE.encode()).hexdigest()
    )
    assert evidence["runtimeWorker"]["shutdown"] == {
        "confirmedExited": True,
        "exitCode": 0,
        "gracefulShutdownConfirmed": True,
        "ownedProcessesConfirmedExited": True,
        "ownershipConfirmed": True,
        "reason": "clean",
        "shutdownAcknowledged": True,
        "terminatedByParent": False,
    }
    assert evidence["artifact"]["blockingFindingCodes"] == []
    assert evidence["artifact"]["nonSilentFrameCount"] > 0
    assert evidence["modelPackage"]["verifiedFileCount"] == len(
        KOKORO_LOCAL_ONNX_MANIFEST.artifacts
    )
    assert evidence["modelPackage"]["totalSizeBytes"] == (
        KOKORO_LOCAL_ONNX_MANIFEST.total_size_bytes
    )
    assert verifier.SYNTHETIC_PHRASE not in evidence_text
    assert '"pronunciation":' not in evidence_text
    assert '"grapheme":' not in evidence_text
    assert str(tmp_path) not in evidence_text
    assert "https://" not in evidence_text
    assert not any(
        child.name.startswith(".") and child.name.endswith(".tmp")
        for child in evidence_path.parent.parent.iterdir()
    )


def test_command_requires_acknowledgement_before_storage_or_runtime(tmp_path: Path) -> None:
    called = False

    def runtime_factory(_config: SpeechRuntimeConfig) -> _InjectedRuntime:
        nonlocal called
        called = True
        raise AssertionError("runtime must not be constructed")

    with pytest.raises(verifier.VerificationFailure) as error:
        verifier.run_verification(
            restricted_local_use_acknowledged=False,
            repository_root=tmp_path,
            runtime_factory=runtime_factory,
            platform_verifier=lambda: None,
        )

    assert error.value.code == "RESTRICTED_LOCAL_USE_ACKNOWLEDGEMENT_REQUIRED"
    assert called is False
    assert list(tmp_path.iterdir()) == []


def test_command_rejects_inexact_manifest_before_runtime(tmp_path: Path) -> None:
    package_path = _prepare_fixed_storage(tmp_path)
    runtime_constructed = False

    def invalid_verifier(
        _path: Path,
        _manifest: Any,
    ) -> ModelPackageVerification:
        valid = _verified_package(package_path)
        return ModelPackageVerification(
            package_id=valid.package_id,
            package_version=valid.package_version,
            package_path=valid.package_path,
            manifest_fingerprint=valid.manifest_fingerprint,
            valid=False,
            error_codes=("MODEL_ARTIFACT_HASH_MISMATCH",),
            verified_files=valid.verified_files[:-1],
            total_size_bytes=valid.total_size_bytes - 1,
        )

    def runtime_factory(_config: SpeechRuntimeConfig) -> _InjectedRuntime:
        nonlocal runtime_constructed
        runtime_constructed = True
        raise AssertionError("runtime must not be constructed")

    with pytest.raises(verifier.VerificationFailure) as error:
        _run(
            tmp_path,
            runtime_factory,
            model_verifier=invalid_verifier,
        )

    assert error.value.code == "MODEL_PACKAGE_VERIFICATION_FAILED"
    assert runtime_constructed is False
    assert list((tmp_path / "local-renders" / "phase3b-real-provider").iterdir()) == []


def test_command_rejects_unsafe_fixed_output_before_runtime(tmp_path: Path) -> None:
    _prepare_fixed_storage(tmp_path)
    (tmp_path / "local-renders" / "phase3b-real-provider").write_text(
        "not a directory",
        encoding="utf-8",
    )
    runtime_constructed = False

    def runtime_factory(_config: SpeechRuntimeConfig) -> _InjectedRuntime:
        nonlocal runtime_constructed
        runtime_constructed = True
        raise AssertionError("runtime must not be constructed")

    with pytest.raises(verifier.VerificationFailure) as error:
        _run(tmp_path, runtime_factory)

    assert error.value.code == "OUTPUT_DIRECTORY_UNAVAILABLE"
    assert runtime_constructed is False


def test_command_fails_closed_when_exact_owned_exit_is_not_proven(tmp_path: Path) -> None:
    _prepare_fixed_storage(tmp_path)
    runtimes: list[_InjectedRuntime] = []

    def runtime_factory(config: SpeechRuntimeConfig) -> _InjectedRuntime:
        runtime = _InjectedRuntime(config, ownership_confirmed=False)
        runtimes.append(runtime)
        return runtime

    with pytest.raises(verifier.VerificationFailure) as error:
        _run(tmp_path, runtime_factory)

    assert error.value.code == "WORKER_EXIT_NOT_PROVEN"
    assert runtimes[0].stop_calls == 1
    assert runtimes[0].is_running is False
    assert list((tmp_path / "local-renders" / "phase3b-real-provider").iterdir()) == []


def test_command_fails_closed_without_authenticated_shutdown_acknowledgement(
    tmp_path: Path,
) -> None:
    _prepare_fixed_storage(tmp_path)
    runtimes: list[_InjectedRuntime] = []

    def runtime_factory(config: SpeechRuntimeConfig) -> _InjectedRuntime:
        runtime = _InjectedRuntime(config, shutdown_acknowledged=False)
        runtimes.append(runtime)
        return runtime

    with pytest.raises(verifier.VerificationFailure) as error:
        _run(tmp_path, runtime_factory)

    assert error.value.code == "WORKER_EXIT_NOT_PROVEN"
    assert runtimes[0].stop_calls == 1
    assert runtimes[0].is_running is False
    assert list((tmp_path / "local-renders" / "phase3b-real-provider").iterdir()) == []


def test_atomic_directory_publication_cleans_staging_on_rename_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_fixed_storage(tmp_path)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("injected atomic publication failure")

    monkeypatch.setattr(verifier.os, "replace", fail_replace)

    with pytest.raises(verifier.VerificationFailure) as error:
        _run(tmp_path, _InjectedRuntime)

    assert error.value.code == "OUTPUT_PUBLICATION_FAILED"
    output_root = tmp_path / "local-renders" / "phase3b-real-provider"
    assert list(output_root.iterdir()) == []


def test_owned_directory_cleanup_is_bounded_before_mutation(tmp_path: Path) -> None:
    owned = tmp_path / "run-contents"
    owned.mkdir()
    files = [owned / name for name in (verifier.WAV_FILENAME, verifier.EVIDENCE_FILENAME, "x")]
    for path in files:
        path.write_bytes(b"preserve")

    assert verifier._cleanup_owned_directory(owned) is False
    assert all(path.read_bytes() == b"preserve" for path in files)


def test_startup_reconciliation_removes_fully_written_abrupt_staging(
    tmp_path: Path,
) -> None:
    _prepare_fixed_storage(tmp_path)
    interrupted = _run(tmp_path, _InjectedRuntime)
    interrupted_final = (tmp_path / Path(interrupted.evidence_path)).parent
    interrupted_staging = interrupted_final.with_name(f".{interrupted_final.name}.tmp")
    os.replace(interrupted_final, interrupted_staging)

    completed = _run(
        tmp_path,
        _InjectedRuntime,
        nonce_factory=lambda: "b" * 32,
    )

    assert interrupted_staging.exists() is False
    assert (tmp_path / Path(completed.evidence_path)).is_file()


def test_startup_reconciliation_preserves_valid_acknowledged_final_run(
    tmp_path: Path,
) -> None:
    _prepare_fixed_storage(tmp_path)
    acknowledged = _run(tmp_path, _InjectedRuntime)
    acknowledged_evidence = tmp_path / Path(acknowledged.evidence_path)
    acknowledged_bytes = acknowledged_evidence.read_bytes()

    completed = _run(
        tmp_path,
        _InjectedRuntime,
        nonce_factory=lambda: "b" * 32,
    )

    assert acknowledged_evidence.read_bytes() == acknowledged_bytes
    assert (tmp_path / Path(completed.evidence_path)).is_file()
    output_root = tmp_path / "local-renders" / "phase3b-real-provider"
    assert len([path for path in output_root.iterdir() if path.name.startswith("run-")]) == 2


def test_startup_reconciliation_removes_invalid_exact_owned_final(
    tmp_path: Path,
) -> None:
    _prepare_fixed_storage(tmp_path)
    invalid = _run(tmp_path, _InjectedRuntime)
    invalid_evidence = tmp_path / Path(invalid.evidence_path)
    invalid_final = invalid_evidence.parent
    invalid_wav = invalid_final / verifier.WAV_FILENAME
    corrupted = bytearray(invalid_wav.read_bytes())
    corrupted[-1] ^= 0x01
    invalid_wav.write_bytes(corrupted)

    completed = _run(
        tmp_path,
        _InjectedRuntime,
        nonce_factory=lambda: "b" * 32,
    )

    assert invalid_final.exists() is False
    assert (tmp_path / Path(completed.evidence_path)).is_file()


def test_startup_reconciliation_removes_final_without_authenticated_shutdown(
    tmp_path: Path,
) -> None:
    _prepare_fixed_storage(tmp_path)
    invalid = _run(tmp_path, _InjectedRuntime)
    invalid_evidence = tmp_path / Path(invalid.evidence_path)
    invalid_final = invalid_evidence.parent
    payload = json.loads(invalid_evidence.read_text(encoding="utf-8"))
    payload["runtimeWorker"]["shutdown"]["shutdownAcknowledged"] = False
    invalid_evidence.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    completed = _run(
        tmp_path,
        _InjectedRuntime,
        nonce_factory=lambda: "b" * 32,
    )

    assert invalid_final.exists() is False
    assert (tmp_path / Path(completed.evidence_path)).is_file()


def test_startup_reconciliation_preserves_unknown_exact_name_contents(
    tmp_path: Path,
) -> None:
    _prepare_fixed_storage(tmp_path)
    output_root = tmp_path / "local-renders" / "phase3b-real-provider"
    output_root.mkdir()
    run_component = verifier._timestamp_run_component(_TIMESTAMP)
    unknown_staging = output_root / f".run-{run_component}-{'c' * 32}.tmp"
    unknown_staging.mkdir()
    unknown_file = unknown_staging / "do-not-touch.txt"
    unknown_file.write_text("unrelated private material", encoding="utf-8")

    completed = _run(tmp_path, _InjectedRuntime)

    assert unknown_file.read_text(encoding="utf-8") == "unrelated private material"
    assert (tmp_path / Path(completed.evidence_path)).is_file()


def test_startup_reconciliation_is_bounded_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_fixed_storage(tmp_path)
    output_root = tmp_path / "local-renders" / "phase3b-real-provider"
    output_root.mkdir()
    unknown_files = [output_root / f"unknown-{index}.txt" for index in range(3)]
    for path in unknown_files:
        path.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(verifier, "MAX_RECONCILIATION_ENTRIES", 2)
    runtime_constructed = False

    def runtime_factory(_config: SpeechRuntimeConfig) -> _InjectedRuntime:
        nonlocal runtime_constructed
        runtime_constructed = True
        raise AssertionError("runtime must not be constructed")

    with pytest.raises(verifier.VerificationFailure) as error:
        _run(tmp_path, runtime_factory)

    assert error.value.code == "OUTPUT_RECONCILIATION_LIMIT_EXCEEDED"
    assert runtime_constructed is False
    assert all(path.read_text(encoding="utf-8") == "preserve" for path in unknown_files)


def test_cli_exposes_no_text_model_url_or_output_path_arguments() -> None:
    with pytest.raises(SystemExit):
        verifier.build_parser().parse_args([])
    with pytest.raises(SystemExit):
        verifier.build_parser().parse_args(
            [
                "--acknowledge-restricted-local-use",
                "--text",
                "untrusted text",
            ]
        )
    with pytest.raises(SystemExit):
        verifier.build_parser().parse_args(
            [
                "--acknowledge-restricted-local-use",
                "--model-url",
                "https://example.invalid/model",
            ]
        )
