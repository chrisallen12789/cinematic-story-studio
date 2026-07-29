from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service import jobs as jobs_module
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.jobs import JobRepository
from cinematic_story_service.models import JobAttemptRow, JobCheckpointRow, JobRow

from .conftest import (
    TOKEN,
    create_analysis_job,
    create_imported_project,
    wait_for_job,
)


def _repository_pair(app: Any) -> tuple[JobRepository, JobRepository]:
    return (
        JobRepository(app.state.database, app.state.projects, "repository-one"),
        JobRepository(app.state.database, app.state.projects, "repository-two"),
    )


def test_concurrent_different_keys_cannot_create_duplicate_active_job(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "duplicate-active-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        repositories = _repository_pair(app)
        start = threading.Barrier(3)

        def create(repository: JobRepository, key: str) -> tuple[str, object]:
            start.wait()
            try:
                return (
                    "created",
                    repository.create_job(
                        project_id=imported["project"]["projectId"],
                        job_type="analyze_story",
                        input_revision=imported["story"]["revision"],
                        idempotency_key=key,
                    ),
                )
            except ServiceError as exc:
                return ("error", exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create, repositories[0], "concurrent-create-one")
            second = pool.submit(create, repositories[1], "concurrent-create-two")
            start.wait()
            outcomes = [first.result(timeout=5), second.result(timeout=5)]

        assert [kind for kind, _value in outcomes].count("created") == 1
        loser = next(value for kind, value in outcomes if kind == "error")
        assert isinstance(loser, ServiceError)
        assert (loser.status_code, loser.code) == (409, "JOB_ALREADY_ACTIVE")
        with app.state.database.session() as session:
            active = session.query(JobRow).filter(JobRow.state.in_(["queued", "running"])).all()
            assert len(active) == 1


def test_two_repositories_cannot_double_claim_one_queued_job(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "double-claim-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="double-claim",
        )
        repositories = _repository_pair(app)
        start = threading.Barrier(3)

        def claim(repository: JobRepository) -> dict[str, Any] | None:
            start.wait()
            return repository.claim_next()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(claim, repositories[0])
            second = pool.submit(claim, repositories[1])
            start.wait()
            claims = [first.result(timeout=5), second.result(timeout=5)]

        assert sum(claimed is not None for claimed in claims) == 1
        claimed = next(value for value in claims if value is not None)
        assert claimed["jobId"] == job["jobId"]
        events, _last = repositories[0].get_events(job["jobId"], after_sequence=0)
        assert sum(event.get("state") == "running" for event in events) == 1


def test_progress_vs_cancel_never_appends_running_event_after_cancel_wins(
    tmp_path: Path,
) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "progress-cancel-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="progress-cancel",
        )
        repositories = _repository_pair(app)
        assert repositories[0].claim_next() is not None
        start = threading.Barrier(3)

        def cancel() -> dict[str, Any]:
            start.wait()
            return repositories[0].cancel(job["jobId"])

        def progress() -> bool:
            start.wait()
            return repositories[1].update_progress(
                job["jobId"],
                stage="racing_progress",
                progress=0.2,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            cancellation = pool.submit(cancel)
            progress_update = pool.submit(progress)
            start.wait()
            cancellation.result(timeout=5)
            progress_update.result(timeout=5)

        repositories[0].settle_pending_cancellation()
        events, _last = repositories[0].get_events(job["jobId"], after_sequence=0)
        cancel_sequence = next(
            event["sequence"]
            for event in events
            if event.get("state") == "cancel_requested"
        )
        assert not any(
            event["sequence"] > cancel_sequence
            and event["type"] == "progress"
            and event.get("state") == "running"
            for event in events
        )
        assert repositories[0].get_job(job["jobId"])["state"] == "cancelled"


def test_concurrent_event_allocation_is_contiguous_and_unique(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "event-sequence-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="event-sequence",
        )
        assert app.state.jobs.claim_next() is not None
        repository_count = 8
        repositories = [
            JobRepository(app.state.database, app.state.projects, f"sequence-{index}")
            for index in range(repository_count)
        ]
        start = threading.Barrier(repository_count + 1)

        def update_once(repository: JobRepository, index: int) -> bool:
            start.wait()
            return repository.update_progress(
                job["jobId"],
                stage=f"parallel-{index}",
                progress=0.2,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=repository_count) as pool:
            futures = [
                pool.submit(update_once, repository, index)
                for index, repository in enumerate(repositories)
            ]
            start.wait()
            assert all(future.result(timeout=10) for future in futures)

        events, last_sequence = app.state.jobs.get_events(job["jobId"], after_sequence=0)
        sequences = [event["sequence"] for event in events]
        assert sequences == list(range(1, len(events) + 1))
        assert last_sequence == sequences[-1]
        assert sum(event["type"] == "progress" for event in events) == repository_count


def test_concurrent_retry_has_one_cas_winner_and_one_new_attempt(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "retry-cas-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="retry-cas",
        )
        assert app.state.jobs.claim_next() is not None
        app.state.jobs.finish_failed(job["jobId"])
        repositories = _repository_pair(app)
        start = threading.Barrier(3)

        def retry(repository: JobRepository) -> tuple[str, object]:
            start.wait()
            try:
                return ("queued", repository.retry(job["jobId"]))
            except ServiceError as exc:
                return ("error", exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(retry, repositories[0])
            second = pool.submit(retry, repositories[1])
            start.wait()
            outcomes = [first.result(timeout=5), second.result(timeout=5)]

        assert [kind for kind, _value in outcomes].count("queued") == 1
        loser = next(value for kind, value in outcomes if kind == "error")
        assert isinstance(loser, ServiceError)
        assert loser.code == "JOB_STATE_CONFLICT"
        with app.state.database.session() as session:
            attempts = (
                session.query(JobAttemptRow)
                .filter_by(job_id=job["jobId"])
                .order_by(JobAttemptRow.number)
                .all()
            )
            assert [attempt.number for attempt in attempts] == [1, 2]


def test_job_lifecycle_events_progress_and_reconnect(
    client: TestClient, app: object, auth_headers: dict[str, str]
) -> None:
    app.state.worker.controls.claim_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="lifecycle",
    )
    assert job["state"] == "queued"
    app.state.worker.controls.claim_gate.set()
    app.state.worker.wake()
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    assert terminal["progress"] == 1

    all_events = client.get(f"/api/v1/jobs/{job['jobId']}/events", headers=auth_headers).json()
    sequences = [event["sequence"] for event in all_events["events"]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    progress = [
        event["progress"]
        for event in all_events["events"]
        if event["attempt"] == 1 and "progress" in event
    ]
    assert progress == sorted(progress)
    assert all(0 <= value <= 1 for value in progress)
    assert progress[-1] == 1

    split_sequence = sequences[len(sequences) // 2]
    later = client.get(
        f"/api/v1/jobs/{job['jobId']}/events",
        headers=auth_headers,
        params={"afterSequence": split_sequence},
    ).json()
    assert later["events"]
    assert all(event["sequence"] > split_sequence for event in later["events"])
    assert later["lastSequence"] == all_events["lastSequence"]


def test_cancel_is_idempotent_and_does_not_publish(
    client: TestClient, app: object, auth_headers: dict[str, str]
) -> None:
    app.state.worker.controls.execution_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="cancel",
    )
    wait_for_job(client, auth_headers, job["jobId"], {"running", "cancel_requested"})
    first = client.post(f"/api/v1/jobs/{job['jobId']}/cancel", headers=auth_headers)
    second = client.post(f"/api/v1/jobs/{job['jobId']}/cancel", headers=auth_headers)
    assert first.status_code == second.status_code == 200
    app.state.worker.controls.execution_gate.set()
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"cancelled"})
    assert terminal["progress"] < 1
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}", headers=auth_headers
    ).json()
    assert detail["chapters"] == []

    later = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="after-cancel",
    )
    assert wait_for_job(client, auth_headers, later["jobId"], {"succeeded"})["state"] == "succeeded"


def test_cancel_at_final_publication_boundary_cannot_leave_results(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    app.state.worker.controls.before_publication_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="cancel-publication-race",
    )
    deadline = time.monotonic() + 5
    current = job
    while time.monotonic() < deadline:
        current = client.get(f"/api/v1/jobs/{job['jobId']}", headers=auth_headers).json()["job"]
        if current["stage"] == "publishing_analysis":
            break
        time.sleep(0.01)
    assert current["stage"] == "publishing_analysis"

    requested = client.post(f"/api/v1/jobs/{job['jobId']}/cancel", headers=auth_headers)
    assert requested.status_code == 200
    assert requested.json()["job"]["state"] == "cancel_requested"
    app.state.worker.controls.before_publication_gate.set()
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"cancelled"})
    assert terminal["state"] == "cancelled"
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}", headers=auth_headers
    ).json()
    assert detail["chapters"] == []
    assert detail["scenes"] == []
    assert detail["dialogueLines"] == []


def test_publication_write_claim_orders_a_racing_cancel_after_success(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    app.state.worker.controls.publication_claim_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="publication-write-claim-race",
    )
    assert app.state.worker.controls.publication_claimed.wait(timeout=5)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        cancellation = pool.submit(
            client.post,
            f"/api/v1/jobs/{job['jobId']}/cancel",
            headers=auth_headers,
        )
        time.sleep(0.1)
        assert not cancellation.done()
        app.state.worker.controls.publication_claim_gate.set()
        cancellation_response = cancellation.result(timeout=5)

    assert cancellation_response.status_code == 409
    assert cancellation_response.json()["error"]["code"] == "JOB_STATE_CONFLICT"
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    assert terminal["state"] == "succeeded"
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}", headers=auth_headers
    ).json()
    assert detail["chapters"]
    assert detail["dialogueLines"]


def test_failed_attempt_can_retry_without_duplicate_job_or_history_loss(
    client: TestClient, app: object, auth_headers: dict[str, str]
) -> None:
    imported = create_imported_project(client, auth_headers)
    app.state.worker.fail_next_attempt()
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="retry",
    )
    failed = wait_for_job(client, auth_headers, job["jobId"], {"failed"})
    assert failed["attempt"] == 1
    assert failed["error"] == {
        "code": "ANALYSIS_FAILED",
        "message": "Story analysis could not be completed.",
        "retryable": True,
    }
    retry = client.post(f"/api/v1/jobs/{job['jobId']}/retry", headers=auth_headers)
    assert retry.status_code == 200
    assert retry.json()["job"]["jobId"] == job["jobId"]
    assert retry.json()["job"]["attempt"] == 2
    succeeded = wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    assert succeeded["attempt"] == 2
    events = client.get(f"/api/v1/jobs/{job['jobId']}/events", headers=auth_headers).json()[
        "events"
    ]
    assert {event["attempt"] for event in events} == {1, 2}
    assert any(event["type"] == "failed" and event["attempt"] == 1 for event in events)


def test_checkpoint_is_interrupted_on_shutdown_then_resumes(tmp_path: Path) -> None:
    data_dir = tmp_path / "resume-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        first_app.state.worker.controls.after_checkpoint_gate.clear()
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="resume",
        )
        checkpointed = wait_for_job(first, headers, job["jobId"], {"running"})
        while not checkpointed["checkpointAvailable"]:
            checkpointed = wait_for_job(first, headers, job["jobId"], {"running"})
        assert checkpointed["checkpointAvailable"] is True

    second_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(second_app) as second:
        interrupted = second.get(f"/api/v1/jobs/{job['jobId']}", headers=headers).json()["job"]
        assert interrupted["state"] == "interrupted"
        resumed = second.post(f"/api/v1/jobs/{job['jobId']}/resume", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["job"]["attempt"] == 2
        terminal = wait_for_job(second, headers, job["jobId"], {"succeeded"})
        assert terminal["attempt"] == 2
        events = second.get(f"/api/v1/jobs/{job['jobId']}/events", headers=headers).json()["events"]
        assert any(
            event["stage"] == "checkpoint_restored" for event in events if event.get("stage")
        )


def test_stop_before_first_checkpoint_restarts_as_clean_attempt_and_succeeds(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "clean-restart-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        first_app.state.worker.controls.execution_gate.clear()
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="clean-restart",
        )
        running = wait_for_job(first, headers, job["jobId"], {"running"})
        assert running["checkpointAvailable"] is False
        first_app.state.worker.stop(timeout=5)
        interrupted = first.get(
            f"/api/v1/jobs/{job['jobId']}",
            headers=headers,
        ).json()["job"]
        assert interrupted["state"] == "interrupted"
        assert interrupted["checkpointAvailable"] is False

    second_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(second_app) as second:
        resumed = second.post(f"/api/v1/jobs/{job['jobId']}/resume", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["job"]["attempt"] == 2
        terminal = wait_for_job(second, headers, job["jobId"], {"succeeded"})
        assert terminal["attempt"] == 2
        events = second.get(
            f"/api/v1/jobs/{job['jobId']}/events",
            headers=headers,
        ).json()["events"]
        attempt_two_stages = {
            event.get("stage") for event in events if event["attempt"] == 2
        }
        assert "analyzing_story" in attempt_two_stages
        assert "checkpoint_restored" not in attempt_two_stages


def test_worker_stop_timeout_retains_live_thread_and_storage_ownership(
    client: TestClient,
    app: Any,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original_analyze_story = jobs_module.analyze_story

    def blocked_analysis(**kwargs: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(timeout=5)
        return original_analyze_story(**kwargs)

    monkeypatch.setattr(jobs_module, "analyze_story", blocked_analysis)
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="stop-timeout-project",
        import_key="stop-timeout-import",
    )
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="stop-timeout-job",
    )
    assert entered.wait(timeout=5)

    try:
        with pytest.raises(ServiceError) as raised:
            app.state.worker.stop(timeout=0.01)
        assert raised.value.code == "WORKER_STOP_TIMEOUT"
        worker_thread = app.state.worker._thread
        assert worker_thread is not None
        assert worker_thread.is_alive()
        # A timed-out stop must not close storage out from under the still-owned worker.
        assert app.state.jobs.get_job(job["jobId"])["state"] == "running"
    finally:
        release.set()
        app.state.worker.stop(timeout=5)
    assert app.state.worker._thread is None
    assert app.state.jobs.get_job(job["jobId"])["state"] == "interrupted"


def test_incompatible_checkpoint_remains_inspectable_and_cannot_resume(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "incompatible-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        first_app.state.worker.controls.after_checkpoint_gate.clear()
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="incompatible",
        )
        current = wait_for_job(first, headers, job["jobId"], {"running"})
        while not current["checkpointAvailable"]:
            current = wait_for_job(first, headers, job["jobId"], {"running"})

    second_app = create_app(
        ServiceSettings(data_dir=data_dir, bearer_token=TOKEN, worker_enabled=False)
    )
    with TestClient(second_app) as second:
        with second_app.state.database.session() as session:
            checkpoint = session.query(JobCheckpointRow).filter_by(job_id=job["jobId"]).one()
            checkpoint.producer_version = "incompatible-test-version"
        response = second.post(f"/api/v1/jobs/{job['jobId']}/resume", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHECKPOINT_INCOMPATIBLE"
        with second_app.state.database.session() as session:
            assert session.query(JobCheckpointRow).filter_by(job_id=job["jobId"]).count() == 1


def test_startup_reconciles_abandoned_running_attempt(tmp_path: Path) -> None:
    data_dir = tmp_path / "reconcile-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(
        ServiceSettings(data_dir=data_dir, bearer_token=TOKEN, worker_enabled=False)
    )
    with TestClient(first_app) as first:
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="abandoned",
        )
        with first_app.state.database.session() as session:
            row = session.get(JobRow, job["jobId"])
            assert row is not None
            row.state = "running"
            row.stage = "analyzing_story"

    second_app = create_app(
        ServiceSettings(data_dir=data_dir, bearer_token=TOKEN, worker_enabled=False)
    )
    with TestClient(second_app) as second:
        restored = second.get(f"/api/v1/jobs/{job['jobId']}", headers=headers).json()["job"]
        assert restored["state"] == "interrupted"
        events = second.get(f"/api/v1/jobs/{job['jobId']}/events", headers=headers).json()["events"]
        assert events[-1]["state"] == "interrupted"
