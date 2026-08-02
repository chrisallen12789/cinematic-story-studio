from __future__ import annotations

import concurrent.futures
import hashlib
import threading
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import cinematic_story_service.audition_repository as audition_repository_module
from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.database import Database
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.models import (
    AudioArtifactRow,
    AudioQualityRecordRow,
    AuditionCacheRecordRow,
    AuditionClipRow,
    AuditionScriptRow,
    AuditionSessionRow,
    JobCheckpointRow,
    JobRow,
    SpeechProviderRequestRow,
)
from cinematic_story_service.schemas import CreateAuditionScriptRequest
from cinematic_story_service.util import sha256_text
from tests.conftest import wait_for_job
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _clips,
    _create_session_and_script,
    _establish_approved_cast,
    _workspace,
)

_BOUNDARY_TIMEOUT_SECONDS = 30.0


def test_startup_reconciliation_removes_only_owned_atomic_audio_staging(
    settings: ServiceSettings,
) -> None:
    project_id = "11111111-1111-4111-8111-111111111111"
    artifact_id = "22222222-2222-4222-8222-222222222222"
    staging_root = settings.data_dir / "projects" / project_id / "auditions" / "audio-staging"
    staging_root.mkdir(parents=True)
    pending = staging_root / f"{artifact_id}.wav.pending"
    atomic_temporary = staging_root / f".{artifact_id}.wav.pending.{'a' * 32}.tmp"
    unrelated = staging_root / "unrelated-synthetic-evidence.tmp"
    pending.write_bytes(b"repository-owned partial WAV")
    atomic_temporary.write_bytes(b"repository-owned partial atomic WAV")
    unrelated.write_bytes(b"repository-owned unrelated sentinel")

    create_app(settings)

    assert not pending.exists()
    assert not atomic_temporary.exists()
    assert unrelated.read_bytes() == b"repository-owned unrelated sentinel"


def test_audio_reconciliation_deletes_only_exact_owned_final_orphans(
    client: TestClient,
    settings: ServiceSettings,
) -> None:
    project_id = str(uuid4())
    audio_root = settings.data_dir / "projects" / project_id / "auditions" / "audio"
    audio_root.mkdir(parents=True)
    owned_orphan = audio_root / f"{uuid4()}.wav"
    unrelated_wav = audio_root / "repository-owned-unrelated-sentinel.wav"
    owned_orphan.write_bytes(b"repository-owned orphan WAV")
    unrelated_wav.write_bytes(b"repository-owned unrelated WAV")

    client.app.state.auditions._reconcile_audio_storage()

    assert not owned_orphan.exists()
    assert unrelated_wav.read_bytes() == b"repository-owned unrelated WAV"


def test_audio_reconciliation_bound_fails_before_partial_cleanup(
    client: TestClient,
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = str(uuid4())
    audio_root = settings.data_dir / "projects" / project_id / "auditions" / "audio"
    audio_root.mkdir(parents=True)
    owned_orphan = audio_root / f"{uuid4()}.wav"
    unrelated_wav = audio_root / "repository-owned-unrelated-sentinel.wav"
    owned_orphan.write_bytes(b"repository-owned orphan WAV")
    unrelated_wav.write_bytes(b"repository-owned unrelated WAV")
    monkeypatch.setattr(
        audition_repository_module,
        "_MAX_AUDIO_STORAGE_RECONCILIATION_ENTRIES",
        2,
    )

    with pytest.raises(ServiceError) as raised:
        client.app.state.auditions._reconcile_audio_storage()

    assert raised.value.code == "AUDITION_STORAGE_RECONCILIATION_FAILED"
    assert owned_orphan.read_bytes() == b"repository-owned orphan WAV"
    assert unrelated_wav.read_bytes() == b"repository-owned unrelated WAV"


def _prepare_generation(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    key: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    project_id, _run = _establish_approved_cast(client, auth_headers, key=key)
    _activate_fixture_model(
        client,
        auth_headers,
        project_id=project_id,
        key=key,
    )
    role_id = _workspace(client, auth_headers, project_id)["roles"]["items"][0]["roleId"]
    session, _script, generation_request = _create_session_and_script(
        client,
        auth_headers,
        project_id=project_id,
        role_id=role_id,
        text=f"Repository-owned atomic publication signal for {key}.",
        key=key,
    )
    return project_id, session, generation_request


def _prepare_script_session(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    key: str,
) -> tuple[str, dict[str, Any]]:
    project_id, _run = _establish_approved_cast(client, auth_headers, key=key)
    _activate_fixture_model(
        client,
        auth_headers,
        project_id=project_id,
        key=key,
    )
    workspace = _workspace(client, auth_headers, project_id)
    role = workspace["roles"]["items"][0]
    assert role["sessionEvidence"] is not None
    response = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": role["roleId"],
            "evidence": role["sessionEvidence"],
            "idempotencyKey": f"{key}-session",
        },
    )
    assert response.status_code == 200, response.text
    return project_id, cast(dict[str, Any], response.json()["session"])


def _script_request(
    session: dict[str, Any],
    *,
    text: str,
    key: str,
) -> CreateAuditionScriptRequest:
    return CreateAuditionScriptRequest.model_validate(
        {
            "auditionSessionId": session["auditionSessionId"],
            "expectedSessionRevision": session["revision"],
            "kind": "standardized_synthetic",
            "text": text,
            "sourceDocumentId": None,
            "sourceRevision": None,
            "sourceSpan": None,
            "sourceTextSha256": sha256_text(text),
            "acceptedOptionalNormalizationIds": [],
            "idempotencyKey": f"{key}-script",
        }
    )


def test_script_publication_compensates_exact_staging_on_database_commit_failure(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, audition_session = _prepare_script_session(
        client,
        auth_headers,
        key="phase3b-script-commit-failure",
    )
    text = "Repository-owned script commit compensation signal."
    request = _script_request(
        audition_session,
        text=text,
        key="phase3b-script-commit-failure",
    )
    repository = client.app.state.auditions
    database = client.app.state.database
    original_commit = Session.commit
    failure_pending = True
    observed_files: list[tuple[int, int]] = []

    def fail_script_commit(database_session: Session) -> None:
        nonlocal failure_pending
        script = next(
            (
                value
                for value in database_session.identity_map.values()
                if isinstance(value, AuditionScriptRow)
                and value.exact_text_sha256 == request.source_text_sha256
            ),
            None,
        )
        if failure_pending and script is not None:
            script_directory = settings.data_dir / "projects" / project_id / "auditions" / "scripts"
            observed_files.append(
                (
                    len(list(script_directory.glob(".*.utf8.pending.*.tmp"))),
                    len(list(script_directory.glob("*.utf8"))),
                )
            )
            failure_pending = False
            raise RuntimeError("injected audition script commit failure")
        original_commit(database_session)

    monkeypatch.setattr(Session, "commit", fail_script_commit)

    with pytest.raises(RuntimeError, match="injected audition script commit failure"):
        repository.create_script(project_id=project_id, request=request)

    script_directory = settings.data_dir / "projects" / project_id / "auditions" / "scripts"
    assert observed_files == [(1, 0)]
    assert list(script_directory.glob(".*.utf8.pending.*.tmp")) == []
    assert list(script_directory.glob("*.utf8")) == []
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditionScriptRow)
                .where(AuditionScriptRow.exact_text_sha256 == request.source_text_sha256)
            )
            == 0
        )


def test_script_startup_reconciliation_deletes_only_exact_uncommitted_orphans(
    client: TestClient,
    settings: ServiceSettings,
) -> None:
    repository = client.app.state.auditions
    project_id = str(uuid4())
    script_directory = settings.data_dir / "projects" / project_id / "auditions" / "scripts"
    script_directory.mkdir(parents=True)
    storage_id = str(uuid4())
    record_id = str(uuid4())
    pending = script_directory / f".{storage_id}.utf8.pending.{record_id}.tmp"
    final = script_directory / f"{uuid4()}.utf8"
    unrelated = script_directory / "repository-owned-unrelated-sentinel.txt"
    pending.write_text("Repository-owned uncommitted pending text.", encoding="utf-8")
    final.write_text("Repository-owned uncommitted final text.", encoding="utf-8")
    unrelated.write_text("preserve", encoding="utf-8")

    repository._reconcile_script_storage()

    assert not pending.exists()
    assert not final.exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve"


def test_script_reconciliation_bound_fails_before_partial_cleanup(
    client: TestClient,
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = client.app.state.auditions
    project_id = str(uuid4())
    script_directory = settings.data_dir / "projects" / project_id / "auditions" / "scripts"
    script_directory.mkdir(parents=True)
    owned_orphan = script_directory / f"{uuid4()}.utf8"
    unrelated = script_directory / "repository-owned-unrelated-sentinel.txt"
    owned_orphan.write_text("Repository-owned orphan script.", encoding="utf-8")
    unrelated.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(
        audition_repository_module,
        "_MAX_SCRIPT_STORAGE_RECONCILIATION_RECORDS",
        5,
    )

    with pytest.raises(ServiceError) as raised:
        repository._reconcile_script_storage()

    assert raised.value.code == "AUDITION_SCRIPT_STORAGE_RECONCILIATION_FAILED"
    assert owned_orphan.read_text(encoding="utf-8") == "Repository-owned orphan script."
    assert unrelated.read_text(encoding="utf-8") == "preserve"


def test_script_startup_reconciliation_restores_exact_committed_staging_bytes(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
) -> None:
    project_id, _run = _establish_approved_cast(
        client,
        auth_headers,
        key="phase3b-script-committed-staging",
    )
    _activate_fixture_model(
        client,
        auth_headers,
        project_id=project_id,
        key="phase3b-script-committed-staging",
    )
    role_id = _workspace(client, auth_headers, project_id)["roles"]["items"][0]["roleId"]
    text = "Repository-owned committed staging recovery signal."
    _session, script, _generation_request = _create_session_and_script(
        client,
        auth_headers,
        project_id=project_id,
        role_id=role_id,
        text=text,
        key="phase3b-script-committed-staging",
    )
    database = client.app.state.database
    with database.session() as session:
        row = session.get(AuditionScriptRow, script["auditionScriptId"])
        assert row is not None
        assert row.text_storage_key is not None
        record_id = row.id
        storage_key = row.text_storage_key
        expected_sha256 = row.exact_text_sha256
    final = settings.data_dir / Path(storage_key)
    staged = final.parent / f".{final.name}.pending.{record_id}.tmp"
    expected_bytes = final.read_bytes()
    final.replace(staged)
    assert not final.exists()
    assert sha256_text(expected_bytes.decode("utf-8")) == expected_sha256

    client.app.state.auditions._reconcile_script_storage()

    assert not staged.exists()
    assert final.read_bytes() == expected_bytes
    with database.session() as session:
        restored = session.get(AuditionScriptRow, script["auditionScriptId"])
        assert restored is not None
        assert restored.text_storage_key == storage_key
        assert client.app.state.auditions._read_script_text(restored) == text


def _queue_generation(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    session_id: str,
    generation_request: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions/{session_id}/generate",
        headers=auth_headers,
        json={"preview": generation_request},
    )
    assert response.status_code == 202, response.text
    return cast(dict[str, Any], response.json())


def _audio_files(settings: ServiceSettings, project_id: str) -> tuple[list[Path], list[Path]]:
    audition_root = settings.data_dir / "projects" / project_id / "auditions"
    final_root = audition_root / "audio"
    staging_root = audition_root / "audio-staging"
    final_files = sorted(path for path in final_root.glob("*") if path.is_file())
    staging_files = sorted(path for path in staging_root.glob("*") if path.is_file())
    return final_files, staging_files


def test_generation_job_is_not_claimable_before_provider_evidence_commits(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-atomic-generation-queue",
        )
        jobs = client.app.state.jobs
        worker = client.app.state.worker
        database = client.app.state.database
        worker.controls.claim_gate.clear()
        job_created = threading.Event()
        release_queue_transaction = threading.Event()
        claim_started = threading.Event()
        created_job_ids: list[str] = []
        original_create = jobs.create_audition_job
        original_claim = jobs.claim_next

        def create_and_hold_transaction(**kwargs: Any) -> dict[str, Any]:
            assert kwargs.get("transaction_session") is not None
            created = cast(dict[str, Any], original_create(**kwargs))
            created_job_ids.append(created["jobId"])
            job_created.set()
            assert release_queue_transaction.wait(timeout=_BOUNDARY_TIMEOUT_SECONDS)
            return created

        def observe_claim_attempt() -> dict[str, Any] | None:
            if job_created.is_set():
                claim_started.set()
            return cast(dict[str, Any] | None, original_claim())

        monkeypatch.setattr(jobs, "create_audition_job", create_and_hold_transaction)
        monkeypatch.setattr(jobs, "claim_next", observe_claim_attempt)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            queued_future = pool.submit(
                _queue_generation,
                client,
                auth_headers,
                project_id=project_id,
                session_id=audition_session["auditionSessionId"],
                generation_request=generation_request,
            )
            try:
                assert job_created.wait(timeout=_BOUNDARY_TIMEOUT_SECONDS)
                assert len(created_job_ids) == 1
                job_id = created_job_ids[0]
                with database.session() as database_session:
                    assert database_session.get(JobRow, job_id) is None
                    assert (
                        database_session.scalar(
                            select(func.count())
                            .select_from(SpeechProviderRequestRow)
                            .where(SpeechProviderRequestRow.job_id == job_id)
                        )
                        == 0
                    )

                worker.controls.claim_gate.set()
                worker.wake()
                assert claim_started.wait(timeout=_BOUNDARY_TIMEOUT_SECONDS)
                assert not queued_future.done()
            finally:
                release_queue_transaction.set()
                worker.controls.claim_gate.set()
                worker.wake()
            queued = queued_future.result(timeout=_BOUNDARY_TIMEOUT_SECONDS)

        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=_BOUNDARY_TIMEOUT_SECONDS,
        )
        assert terminal["state"] == "succeeded"
        with database.session() as database_session:
            persisted_job = database_session.get(JobRow, queued["jobId"])
            persisted_request = database_session.scalar(
                select(SpeechProviderRequestRow).where(
                    SpeechProviderRequestRow.job_id == queued["jobId"]
                )
            )
            assert persisted_job is not None
            assert persisted_job.state == "succeeded"
            assert persisted_job.error_code is None
            assert persisted_request is not None
            assert persisted_request.outcome == "succeeded"


def test_generation_precondition_validation_and_queue_commit_are_serialized(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-generation-precondition-serialization",
        )
        repository = client.app.state.auditions
        database = client.app.state.database
        worker = client.app.state.worker
        worker.controls.claim_gate.clear()
        preconditions_validated = threading.Event()
        release_precondition_boundary = threading.Event()
        mutation_started = threading.Event()
        original_validate = repository._validate_session_evidence
        hold_once = True

        def validate_and_hold(*args: Any, **kwargs: Any) -> None:
            nonlocal hold_once
            original_validate(*args, **kwargs)
            if hold_once:
                hold_once = False
                preconditions_validated.set()
                assert release_precondition_boundary.wait(timeout=_BOUNDARY_TIMEOUT_SECONDS)

        def mutate_validated_session() -> None:
            mutation_started.set()
            with database.immediate_session() as database_session:
                row = database_session.get(
                    AuditionSessionRow,
                    audition_session["auditionSessionId"],
                )
                assert row is not None
                row.pronunciation_dictionary_fingerprint = (
                    "f" * 64 if row.pronunciation_dictionary_fingerprint != "f" * 64 else "e" * 64
                )

        monkeypatch.setattr(repository, "_validate_session_evidence", validate_and_hold)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            queued_future = pool.submit(
                _queue_generation,
                client,
                auth_headers,
                project_id=project_id,
                session_id=audition_session["auditionSessionId"],
                generation_request=generation_request,
            )
            mutation_future: concurrent.futures.Future[None] | None = None
            try:
                assert preconditions_validated.wait(timeout=_BOUNDARY_TIMEOUT_SECONDS)
                mutation_future = pool.submit(mutate_validated_session)
                assert mutation_started.wait(timeout=_BOUNDARY_TIMEOUT_SECONDS)
                time.sleep(0.1)
                assert not mutation_future.done()
                assert not queued_future.done()
            finally:
                release_precondition_boundary.set()
            queued = queued_future.result(timeout=_BOUNDARY_TIMEOUT_SECONDS)
            assert mutation_future is not None
            mutation_future.result(timeout=_BOUNDARY_TIMEOUT_SECONDS)

        worker.controls.claim_gate.set()
        worker.wake()
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"failed"},
            timeout=_BOUNDARY_TIMEOUT_SECONDS,
        )
        assert terminal["state"] == "failed"
        assert terminal["error"]["code"] != "AUDITION_JOB_EVIDENCE_INVALID"
        with database.session() as database_session:
            persisted_request = database_session.scalar(
                select(SpeechProviderRequestRow).where(
                    SpeechProviderRequestRow.job_id == queued["jobId"]
                )
            )
            assert persisted_request is not None
            assert persisted_request.outcome == "failed"


def _wait_for_staging_file(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
    *,
    project_id: str,
    job_id: str,
) -> Path:
    deadline = time.monotonic() + _BOUNDARY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        _final_files, staging_files = _audio_files(settings, project_id)
        published_staging_files = [
            path for path in staging_files if path.name.endswith(".wav.pending")
        ]
        if len(published_staging_files) == 1:
            return published_staging_files[0]
        job_response = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert job_response.status_code == 200, job_response.text
        assert job_response.json()["job"]["state"] not in {
            "failed",
            "cancelled",
            "interrupted",
            "succeeded",
        }
        time.sleep(0.01)
    pytest.fail("The audition worker did not reach its staged publication boundary.")


def _wait_for_no_audio_files(settings: ServiceSettings, project_id: str) -> None:
    deadline = time.monotonic() + _BOUNDARY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        final_files, staging_files = _audio_files(settings, project_id)
        if not final_files and not staging_files:
            return
        time.sleep(0.01)
    pytest.fail("Atomic publication left final or staging audio bytes behind.")


def _publication_counts(
    client: TestClient,
    *,
    project_id: str,
    job_id: str,
) -> tuple[dict[str, int], str]:
    database = cast(Database, client.app.state.database)
    with database.session() as session:
        counts = {
            "artifacts": int(
                session.scalar(
                    select(func.count())
                    .select_from(AudioArtifactRow)
                    .where(AudioArtifactRow.project_id == project_id)
                )
                or 0
            ),
            "cache": int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionCacheRecordRow)
                    .where(AuditionCacheRecordRow.project_id == project_id)
                )
                or 0
            ),
            "clips": int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionClipRow)
                    .where(AuditionClipRow.project_id == project_id)
                )
                or 0
            ),
            "quality": int(
                session.scalar(
                    select(func.count())
                    .select_from(AudioQualityRecordRow)
                    .where(AudioQualityRecordRow.project_id == project_id)
                )
                or 0
            ),
            "finalCheckpoints": int(
                session.scalar(
                    select(func.count())
                    .select_from(JobCheckpointRow)
                    .where(JobCheckpointRow.job_id == job_id)
                )
                or 0
            ),
        }
        provider_outcome = session.scalar(
            select(SpeechProviderRequestRow.outcome).where(
                SpeechProviderRequestRow.job_id == job_id
            )
        )
    assert isinstance(provider_outcome, str)
    return counts, provider_outcome


def test_cancellation_before_publication_removes_all_staged_evidence(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-cancel-before-publication",
        )
        worker = client.app.state.worker
        worker.controls.before_publication_gate.clear()
        try:
            queued = _queue_generation(
                client,
                auth_headers,
                project_id=project_id,
                session_id=session["auditionSessionId"],
                generation_request=generation_request,
            )
            staging_path = _wait_for_staging_file(
                client,
                auth_headers,
                settings,
                project_id=project_id,
                job_id=queued["jobId"],
            )
            assert staging_path.name.endswith(".wav.pending")
            cancellation = client.post(
                f"/api/v1/jobs/{queued['jobId']}/cancel",
                headers=auth_headers,
            )
            assert cancellation.status_code == 200, cancellation.text
            terminal = wait_for_job(
                client,
                auth_headers,
                queued["jobId"],
                {"cancelled"},
                timeout=_BOUNDARY_TIMEOUT_SECONDS,
            )
            assert terminal["state"] == "cancelled"
        finally:
            worker.controls.before_publication_gate.set()
            worker.wake()

        _wait_for_no_audio_files(settings, project_id)
        counts, provider_outcome = _publication_counts(
            client,
            project_id=project_id,
            job_id=queued["jobId"],
        )
        assert counts == {
            "artifacts": 0,
            "cache": 0,
            "clips": 0,
            "quality": 0,
            "finalCheckpoints": 0,
        }
        assert provider_outcome == "cancelled"
        assert _clips(client, auth_headers, project_id=project_id) == []


def test_cancellation_after_publication_claim_loses_to_complete_publication(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-cancel-after-publication-claim",
        )
        worker = client.app.state.worker
        worker.controls.publication_claimed.clear()
        worker.controls.publication_claim_gate.clear()
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session["auditionSessionId"],
            generation_request=generation_request,
        )
        assert worker.controls.publication_claimed.wait(timeout=_BOUNDARY_TIMEOUT_SECONDS)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            cancellation = pool.submit(
                client.post,
                f"/api/v1/jobs/{queued['jobId']}/cancel",
                headers=auth_headers,
            )
            try:
                time.sleep(0.1)
                assert not cancellation.done()
            finally:
                worker.controls.publication_claim_gate.set()
                worker.wake()
            cancellation_response = cancellation.result(timeout=_BOUNDARY_TIMEOUT_SECONDS)

        assert cancellation_response.status_code == 409, cancellation_response.text
        assert cancellation_response.json()["error"]["code"] == "JOB_STATE_CONFLICT"
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=_BOUNDARY_TIMEOUT_SECONDS,
        )
        assert terminal["state"] == "succeeded"
        counts, provider_outcome = _publication_counts(
            client,
            project_id=project_id,
            job_id=queued["jobId"],
        )
        assert counts == {
            "artifacts": 1,
            "cache": 1,
            "clips": 1,
            "quality": 1,
            "finalCheckpoints": 1,
        }
        assert provider_outcome == "succeeded"
        final_files, staging_files = _audio_files(settings, project_id)
        assert len(final_files) == 1
        assert staging_files == []
        clips = _clips(client, auth_headers, project_id=project_id)
        assert len(clips) == 1
        artifact = clips[0]["audioArtifact"]
        payload = final_files[0].read_bytes()
        assert len(payload) == artifact["byteSize"]
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]


def test_commit_failure_rolls_back_publication_rows_and_audio_bytes(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-publication-commit-failure",
        )
        commit_failure_injected = threading.Event()
        files_at_failure: list[tuple[int, int]] = []
        original_commit = Session.commit

        def fail_atomic_publication_commit(database_session: Session) -> None:
            if not commit_failure_injected.is_set():
                connection = database_session.connection()
                constructed_counts = tuple(
                    int(
                        connection.execute(
                            select(func.count())
                            .select_from(model)
                            .where(model.project_id == project_id)
                        ).scalar_one()
                    )
                    for model in (
                        AudioArtifactRow,
                        AuditionCacheRecordRow,
                        AuditionClipRow,
                        AudioQualityRecordRow,
                    )
                )
                if constructed_counts == (1, 1, 1, 1):
                    final_files, staging_files = _audio_files(settings, project_id)
                    files_at_failure.append((len(final_files), len(staging_files)))
                    commit_failure_injected.set()
                    raise RuntimeError("injected atomic audition publication commit failure")
            original_commit(database_session)

        monkeypatch.setattr(Session, "commit", fail_atomic_publication_commit)
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"failed"},
            timeout=_BOUNDARY_TIMEOUT_SECONDS,
        )

        assert commit_failure_injected.is_set()
        assert files_at_failure == [(1, 0)]
        assert terminal["state"] == "failed"
        assert terminal["error"]["code"] == "AUDITION_FAILED"
        _wait_for_no_audio_files(settings, project_id)
        counts, provider_outcome = _publication_counts(
            client,
            project_id=project_id,
            job_id=queued["jobId"],
        )
        assert counts == {
            "artifacts": 0,
            "cache": 0,
            "clips": 0,
            "quality": 0,
            "finalCheckpoints": 0,
        }
        assert provider_outcome == "failed"
        assert _clips(client, auth_headers, project_id=project_id) == []
