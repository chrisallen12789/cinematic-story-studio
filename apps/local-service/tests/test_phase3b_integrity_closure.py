from __future__ import annotations

import copy
import hashlib
import io
import os
import time
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import cinematic_story_service.audition_repository as audition_repository_module
from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.auditions import inspect_audition_wav_bytes
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.models import (
    AudioArtifactRow,
    AuditionCacheRecordRow,
    AuditionScriptRow,
    JobRow,
    SpeechProviderRequestRow,
    SpeechRuntimeInstanceRow,
    SpeechRuntimeProfileRow,
)
from cinematic_story_service.speech_providers import FIXTURE_PROVIDER_ID
from cinematic_story_service.util import new_id, parse_json, request_fingerprint
from tests.conftest import wait_for_job
from tests.test_phase3b_atomic_publication import _prepare_generation
from tests.test_phase3b_workflow import (
    _clips,
    _create_session_and_script,
    _generate,
    _workspace,
)


def _synthetic_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(24_000)
        destination.writeframes((1000).to_bytes(2, "little", signed=True) * 2_400)
    return output.getvalue()


def test_bounded_reader_rejects_oversize_and_detects_entry_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    payload = _synthetic_wav()
    artifact = managed / "artifact.wav"
    artifact.write_bytes(payload)

    _resolved, exact_payload = audition_repository_module._read_bounded_stable_regular_file(
        managed,
        artifact,
        maximum_bytes=audition_repository_module._MAX_AUDIO_BYTES,
        expected_byte_size=len(payload),
    )
    assert exact_payload == payload

    oversize = managed / "oversize.wav"
    with oversize.open("wb") as destination:
        destination.seek(audition_repository_module._MAX_AUDIO_BYTES)
        destination.write(b"x")
    with pytest.raises(ValueError, match="byte size"):
        audition_repository_module._read_bounded_stable_regular_file(
            managed,
            oversize,
            maximum_bytes=audition_repository_module._MAX_AUDIO_BYTES,
        )

    original_verifier = audition_repository_module._verified_storage_path
    verification_calls = 0

    def replace_after_read(
        root: Path,
        candidate: Path,
        *,
        require_directory: bool = False,
    ) -> Path:
        nonlocal verification_calls
        resolved = original_verifier(
            root,
            candidate,
            require_directory=require_directory,
        )
        verification_calls += 1
        if verification_calls == 2:
            replacement = candidate.with_suffix(".replacement")
            replacement.write_bytes(b"z" * len(payload))
            os.replace(replacement, candidate)
        return resolved

    monkeypatch.setattr(
        audition_repository_module,
        "_verified_storage_path",
        replace_after_read,
    )
    with pytest.raises(ValueError, match="entry changed"):
        audition_repository_module._read_bounded_stable_regular_file(
            managed,
            artifact,
            maximum_bytes=audition_repository_module._MAX_AUDIO_BYTES,
            expected_byte_size=len(payload),
        )

    artifact.write_bytes(b"q" * (audition_repository_module._MAX_AUDIO_BYTES + 1))
    qc = inspect_audition_wav_bytes(exact_payload)
    assert qc.measurements.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert qc.byte_size == len(payload)


def test_profile_and_fixture_manifest_fingerprints_bind_complete_behavior() -> None:
    profile = audition_repository_module._runtime_profile_fingerprint_material(
        profile_id=audition_repository_module._FIXTURE_PROFILE_ID,
        provider_id=FIXTURE_PROVIDER_ID,
        provider_version="1.0.0",
        runtime_id=audition_repository_module._FIXTURE_RUNTIME_ID,
        runtime_version=audition_repository_module._FIXTURE_RUNTIME_VERSION,
    )
    assert request_fingerprint(profile) == (audition_repository_module._FIXTURE_PROFILE_FINGERPRINT)
    for mutation in (
        ("providerVersion", "9.9.9"),
        ("networkPolicy", "allow"),
        ("outputFormats", ["other"]),
    ):
        changed = copy.deepcopy(profile)
        changed[mutation[0]] = mutation[1]
        assert request_fingerprint(changed) != request_fingerprint(profile)
    for limit_name in (
        "maximumAudioBytes",
        "maximumDurationMilliseconds",
        "maximumRetryAttempts",
        "maximumScriptCodePoints",
    ):
        changed = copy.deepcopy(profile)
        limits = changed["limits"]
        assert isinstance(limits, dict)
        limits[limit_name] = int(limits[limit_name]) + 1
        assert request_fingerprint(changed) != request_fingerprint(profile)

    manifest = audition_repository_module._fixture_manifest_fingerprint_material()
    assert request_fingerprint(manifest) == (
        audition_repository_module._FIXTURE_MANIFEST_FINGERPRINT
    )
    for key in (
        "providerVersion",
        "runtimeId",
        "runtimeVersion",
        "officialSourceReference",
        "attributionRequirements",
        "requiredRuntimeDependencies",
        "compatibilityConstraints",
        "revocationState",
        "totalExpandedSize",
        "files",
    ):
        changed = copy.deepcopy(manifest)
        value = changed[key]
        changed[key] = ["changed"] if isinstance(value, (list, tuple)) else "changed"
        if isinstance(value, int):
            changed[key] = value + 1
        assert request_fingerprint(changed) != request_fingerprint(manifest)


def test_existing_runtime_profile_tamper_fails_seed_integrity(
    settings: ServiceSettings,
) -> None:
    with TestClient(create_app(settings)) as client:
        with client.app.state.database.immediate_session() as database_session:
            profile = database_session.scalar(
                select(SpeechRuntimeProfileRow).where(
                    SpeechRuntimeProfileRow.provider_id == FIXTURE_PROVIDER_ID
                )
            )
            assert profile is not None
            profile.request_timeout_ms += 1

        with pytest.raises(ServiceError) as raised:
            client.app.state.auditions.ensure_model_packages()
        assert raised.value.status_code == 503
        assert raised.value.code == "RUNTIME_PROFILE_CONFLICT"


def test_cache_lookup_execution_and_cache_graph_tampering_fail_closed(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        key = "phase3b-cache-integrity-closure"
        project_id, first_session, first_request = _prepare_generation(
            client,
            auth_headers,
            key=key,
        )
        first_queued, first_terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=first_session["auditionSessionId"],
            generation_request=first_request,
        )
        assert first_terminal["state"] == "succeeded", first_terminal

        roles = _workspace(client, auth_headers, project_id)["roles"]["items"]
        role_id = roles[0]["roleId"]
        repeated_text = f"Repository-owned atomic publication signal for {key}."
        second_session, _second_script, second_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text=repeated_text,
            key=f"{key}-lookup",
        )
        with client.app.state.database.session() as database_session:
            runtime_count_before = int(
                database_session.scalar(select(func.count()).select_from(SpeechRuntimeInstanceRow))
                or 0
            )
        second_queued, second_terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=second_session["auditionSessionId"],
            generation_request=second_request,
        )
        assert second_terminal["state"] == "succeeded", second_terminal

        with client.app.state.database.session() as database_session:
            runtime_count_after = int(
                database_session.scalar(select(func.count()).select_from(SpeechRuntimeInstanceRow))
                or 0
            )
            source_request = database_session.scalar(
                select(SpeechProviderRequestRow).where(
                    SpeechProviderRequestRow.job_id == first_queued["jobId"]
                )
            )
            lookup_request = database_session.scalar(
                select(SpeechProviderRequestRow).where(
                    SpeechProviderRequestRow.job_id == second_queued["jobId"]
                )
            )
            assert source_request is not None
            assert lookup_request is not None
            source_details = parse_json(source_request.provenance_json, {})["details"]
            lookup_details = parse_json(lookup_request.provenance_json, {})["details"]
            assert source_details["executionClassification"] == "provider_execution"
            assert source_details["providerDispatchCount"] == 1
            assert source_request.runtime_instance_id is not None
            assert source_request.started_at is not None
            assert lookup_details == {
                **{
                    key: value
                    for key, value in lookup_details.items()
                    if key
                    not in {
                        "executionClassification",
                        "providerDispatchCount",
                        "sourceProviderRequestId",
                    }
                },
                "executionClassification": "verified_cache_lookup",
                "providerDispatchCount": 0,
                "sourceProviderRequestId": source_request.id,
            }
            assert lookup_request.runtime_instance_id is None
            assert lookup_request.started_at is not None
        assert runtime_count_after == runtime_count_before
        second_clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=second_session["auditionSessionId"],
        )[0]
        assert second_clip["cacheStatus"] == "verified_hit"

        with client.app.state.database.session() as database_session:
            cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.project_id == project_id
                )
            )
            assert cache is not None
            artifact = database_session.get(AudioArtifactRow, cache.artifact_id)
            assert artifact is not None
            client.app.state.auditions._validated_cache_graph(
                database_session,
                cache=cache,
                artifact=artifact,
            )
            mutations: tuple[tuple[Any, str, Any], ...] = (
                (cache, "provider_request_id", new_id()),
                (cache, "verification_fingerprint", "0" * 64),
                (cache, "artifact_id", new_id()),
                (cache, "expected_artifact_sha256", "1" * 64),
                (cache, "expected_byte_count", cache.expected_byte_count + 1),
                (artifact, "provider_request_id", new_id()),
            )
            for target, attribute, changed_value in mutations:
                original_value = getattr(target, attribute)
                setattr(target, attribute, changed_value)
                with database_session.no_autoflush:
                    with pytest.raises(ValueError):
                        client.app.state.auditions._validated_cache_graph(
                            database_session,
                            cache=cache,
                            artifact=artifact,
                        )
                setattr(target, attribute, original_value)

        failure_session, failure_script, failure_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned preflight failure must not count synthesis.",
            key=f"{key}-preflight",
        )
        worker = client.app.state.worker
        worker.controls.execution_gate.clear()
        try:
            queued_response = client.post(
                (
                    f"/api/v1/projects/{project_id}/audition-sessions/"
                    f"{failure_session['auditionSessionId']}/generate"
                ),
                headers=auth_headers,
                json={"preview": failure_request},
            )
            assert queued_response.status_code == 202, queued_response.text
            failure_job_id = queued_response.json()["jobId"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with client.app.state.database.session() as database_session:
                    job = database_session.get(JobRow, failure_job_id)
                    if job is not None and job.state == "running":
                        break
                time.sleep(0.02)
            else:
                raise AssertionError("The preflight-failure job was not claimed.")
            with client.app.state.database.session() as database_session:
                script = database_session.get(
                    AuditionScriptRow,
                    failure_script["auditionScriptId"],
                )
                assert script is not None
                assert script.text_storage_key is not None
                script_path = settings.data_dir / script.text_storage_key
            damaged = bytearray(script_path.read_bytes())
            damaged[0] = ord("X") if damaged[0] != ord("X") else ord("Y")
            script_path.write_bytes(damaged)
        finally:
            worker.controls.execution_gate.set()
            worker.wake()
        failure_terminal = wait_for_job(
            client,
            auth_headers,
            failure_job_id,
            {"failed"},
            timeout=30,
        )
        assert failure_terminal["error"]["code"] == "AUDITION_SCRIPT_STORAGE_INVALID"
        with client.app.state.database.session() as database_session:
            failed_request = database_session.scalar(
                select(SpeechProviderRequestRow).where(
                    SpeechProviderRequestRow.job_id == failure_job_id
                )
            )
            assert failed_request is not None
            failed_details = parse_json(failed_request.provenance_json, {})["details"]
            assert failed_details["executionClassification"] == "provider_execution"
            assert failed_details["providerDispatchCount"] == 0
            assert failed_request.runtime_instance_id is None
            assert failed_request.started_at is None

        with client.app.state.database.immediate_session() as database_session:
            cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.project_id == project_id
                )
            )
            assert cache is not None
            cache.verification_fingerprint = "f" * 64
            cache_key = cache.cache_key
        verified = client.app.state.auditions._verified_cache_hit(
            project_id=project_id,
            cache_key=cache_key,
        )
        assert verified == (None, None, None)
        with client.app.state.database.session() as database_session:
            cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.project_id == project_id
                )
            )
            assert cache is not None
            assert cache.state == "corrupt"
