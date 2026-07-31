from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.casting import CASTING_PROFILE_FINGERPRINT
from cinematic_story_service.models import JobRow
from tests.conftest import TOKEN, wait_for_job
from tests.test_phase2_api import create_phase2_run
from tests.test_phase3a_casting import (
    _append_phase2_label_correction,
    _approve_phase2,
    _catalog_variant,
)


def _prepare_phase2(
    client: TestClient,
    headers: dict[str, str],
    *,
    key: str,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    imported, created = create_phase2_run(
        client,
        headers,
        idempotency_key=key,
    )
    project_id = imported["project"]["projectId"]
    run, decisions = _approve_phase2(
        client,
        headers,
        project_id=project_id,
        run_id=created["run"]["runId"],
    )
    return project_id, run, decisions


def _queue_casting(
    client: TestClient,
    headers: dict[str, str],
    *,
    project_id: str,
    analysis_run: dict[str, Any],
    decisions: dict[str, str],
    key: str,
) -> dict[str, Any]:
    catalog_response = client.get(
        f"/api/v1/projects/{project_id}/casting/catalog",
        headers=headers,
    )
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    snapshot = analysis_run["currentSnapshot"]
    response = client.post(
        f"/api/v1/projects/{project_id}/casting-runs",
        headers=headers,
        json={
            "expectedAnalysisRunId": analysis_run["runId"],
            "expectedSnapshotId": snapshot["snapshotId"],
            "expectedSnapshotRevision": snapshot["revision"],
            "expectedSnapshotFingerprint": snapshot["snapshotFingerprint"],
            "expectedCorrectionSetFingerprint": snapshot["correctionSetFingerprint"],
            "expectedImportReviewDecisionId": analysis_run["importReviewDecisionId"],
            "expectedAnalysisGateDecisionIds": {
                "storyStructureReview": decisions["story_structure_review"],
                "characterRegistryReview": decisions["character_registry_review"],
                "dialogueAttributionReview": decisions["dialogue_attribution_review"],
                "wholeBookAnalysisReview": decisions["whole_book_analysis_review"],
            },
            "expectedCatalogRevisionId": catalog["catalogRevision"]["catalogRevisionId"],
            "expectedCatalogFingerprint": catalog["catalogRevision"]["catalogFingerprint"],
            "expectedCastingProfileFingerprint": CASTING_PROFILE_FINGERPRINT,
            "idempotencyKey": key,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_casting_job_cancellation_fails_closed(
    client: TestClient,
    app: FastAPI,
    auth_headers: dict[str, str],
) -> None:
    project_id, analysis_run, decisions = _prepare_phase2(
        client,
        auth_headers,
        key="phase3a-cancel-prerequisites",
    )
    app.state.worker.controls.execution_gate.clear()
    try:
        created = _queue_casting(
            client,
            auth_headers,
            project_id=project_id,
            analysis_run=analysis_run,
            decisions=decisions,
            key="phase3a-cancel",
        )
        job_id = created["job"]["jobId"]
        wait_for_job(
            client,
            auth_headers,
            job_id,
            {"running", "cancel_requested"},
        )
        cancelled = client.post(
            f"/api/v1/jobs/{job_id}/cancel",
            headers=auth_headers,
        )
        assert cancelled.status_code == 200, cancelled.text
    finally:
        app.state.worker.controls.execution_gate.set()

    terminal = wait_for_job(client, auth_headers, job_id, {"cancelled"})
    assert terminal["state"] == "cancelled"
    detail = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{created['run']['castingRunId']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    run = detail.json()["run"]
    assert run["status"] == "cancelled"
    assert run["approvedCastSnapshot"] is None
    assert run["summary"] is None


def test_casting_job_retry_reuses_verified_checkpoint_without_duplicate_publication(
    client: TestClient,
    app: FastAPI,
    auth_headers: dict[str, str],
) -> None:
    project_id, analysis_run, decisions = _prepare_phase2(
        client,
        auth_headers,
        key="phase3a-retry-prerequisites",
    )
    app.state.worker.fail_next_attempt()
    created = _queue_casting(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=analysis_run,
        decisions=decisions,
        key="phase3a-retry",
    )
    job_id = created["job"]["jobId"]
    failed = wait_for_job(client, auth_headers, job_id, {"failed"})
    assert failed["attempt"] == 1
    assert failed["checkpointAvailable"] is True
    assert failed["error"] == {
        "code": "CASTING_FAILED",
        "message": "Voice casting could not be completed safely.",
        "retryable": True,
    }
    failed_run = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{created['run']['castingRunId']}",
        headers=auth_headers,
    ).json()["run"]
    assert failed_run["approvedCastSnapshot"] is None
    assert failed_run["failure"]["redacted"] is True

    retry = client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers=auth_headers,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["job"]["attempt"] == 2
    succeeded = wait_for_job(client, auth_headers, job_id, {"succeeded"})
    assert succeeded["attempt"] == 2

    detail = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{created['run']['castingRunId']}",
        headers=auth_headers,
    ).json()["run"]
    assert detail["status"] == "succeeded"
    assert detail["approvedCastSnapshot"] is not None
    assert detail["summary"]["productionRoles"] > 0
    events = client.get(
        f"/api/v1/jobs/{job_id}/events",
        headers=auth_headers,
    ).json()["events"]
    assert {event["attempt"] for event in events} == {1, 2}
    assert any(event["type"] == "checkpoint" for event in events)
    assert "manuscript" not in str(events).casefold()


def test_casting_job_retry_fails_closed_after_three_attempts(
    client: TestClient,
    app: FastAPI,
    auth_headers: dict[str, str],
) -> None:
    project_id, analysis_run, decisions = _prepare_phase2(
        client,
        auth_headers,
        key="phase3a-retry-exhaustion-prerequisites",
    )
    app.state.worker.fail_next_attempt()
    created = _queue_casting(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=analysis_run,
        decisions=decisions,
        key="phase3a-retry-exhaustion",
    )
    job_id = created["job"]["jobId"]
    wait_for_job(client, auth_headers, job_id, {"failed"})
    with app.state.database.session() as session:
        job = session.get(JobRow, job_id)
        assert job is not None
        job.current_attempt = 3
        job.state = "failed"
        job.error_retryable = True

    detail = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{created['run']['castingRunId']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["run"]["retryClassification"] == "retry_exhausted"

    retry = client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers=auth_headers,
    )
    assert retry.status_code == 409, retry.text
    error = retry.json()["error"]
    assert error["code"] == "JOB_RETRY_EXHAUSTED"
    assert error["message"] == "This job has reached its maximum retry attempts."
    assert error["retryable"] is False
    assert error["details"] == {"attempt": 3, "maxAttempts": 3}
    assert error["correlationId"]


def test_failed_newer_casting_run_does_not_replace_effective_cast_snapshot(
    client: TestClient,
    app: FastAPI,
    auth_headers: dict[str, str],
) -> None:
    project_id, analysis_run, decisions = _prepare_phase2(
        client,
        auth_headers,
        key="phase3a-effective-run-prerequisites",
    )
    succeeded = _queue_casting(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=analysis_run,
        decisions=decisions,
        key="phase3a-effective-run-success",
    )
    wait_for_job(
        client,
        auth_headers,
        succeeded["job"]["jobId"],
        {"succeeded"},
    )
    effective_run_id = succeeded["run"]["castingRunId"]

    original_catalog = app.state.casting.catalog
    app.state.casting.catalog = _catalog_variant(original_catalog)
    try:
        app.state.worker.fail_next_attempt()
        failed = _queue_casting(
            client,
            auth_headers,
            project_id=project_id,
            analysis_run=analysis_run,
            decisions=decisions,
            key="phase3a-effective-run-failure",
        )
        failed_run_id = failed["run"]["castingRunId"]
        assert failed_run_id != effective_run_id
        wait_for_job(
            client,
            auth_headers,
            failed["job"]["jobId"],
            {"failed"},
        )

        project_detail = client.get(
            f"/api/v1/projects/{project_id}",
            headers=auth_headers,
        )
        assert project_detail.status_code == 200, project_detail.text
        current = project_detail.json()["voiceCasting"]["currentRun"]
        assert current["castingRunId"] == effective_run_id
        assert current["status"] == "succeeded"
        assert current["approvedCastSnapshot"] is not None

        listed = client.get(
            f"/api/v1/projects/{project_id}/casting-runs",
            headers=auth_headers,
            params={"limit": 20},
        )
        assert listed.status_code == 200, listed.text
        failed_run = next(
            value
            for value in listed.json()["items"]
            if value["castingRunId"] == failed_run_id
        )
        assert failed_run["status"] == "failed"
        assert failed_run["approvedCastSnapshot"] is None
    finally:
        app.state.casting.catalog = original_catalog


def test_casting_job_checkpoint_survives_restart_and_resumes(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "phase3a-resume-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    settings = ServiceSettings(
        data_dir=data_dir,
        bearer_token=TOKEN,
        ffmpeg_executable=str(tmp_path / "missing-ffmpeg.exe"),
    )
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        project_id, analysis_run, decisions = _prepare_phase2(
            first,
            headers,
            key="phase3a-resume-prerequisites",
        )
        first_app.state.worker.controls.after_checkpoint_gate.clear()
        created = _queue_casting(
            first,
            headers,
            project_id=project_id,
            analysis_run=analysis_run,
            decisions=decisions,
            key="phase3a-resume",
        )
        job_id = created["job"]["jobId"]
        assert first_app.state.worker.controls.after_checkpoint_reached.wait(timeout=20)
        checkpointed = first.get(
            f"/api/v1/jobs/{job_id}",
            headers=headers,
        ).json()["job"]
        assert checkpointed["checkpointAvailable"] is True
        first_app.state.worker.stop(timeout=20)
        interrupted = first.get(
            f"/api/v1/jobs/{job_id}",
            headers=headers,
        ).json()["job"]
        assert interrupted["state"] == "interrupted"

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        resume = second.post(
            f"/api/v1/jobs/{job_id}/resume",
            headers=headers,
        )
        assert resume.status_code == 200, resume.text
        assert resume.json()["job"]["attempt"] == 2
        terminal = wait_for_job(
            second,
            headers,
            job_id,
            {"succeeded"},
            timeout=20,
        )
        assert terminal["attempt"] == 2
        restored = second.get(
            f"/api/v1/projects/{project_id}/casting-runs/{created['run']['castingRunId']}",
            headers=headers,
        ).json()["run"]
        assert restored["status"] == "succeeded"
        assert restored["approvedCastSnapshot"] is not None
        assert restored["checkpoint"] is not None


def test_casting_publication_rechecks_phase2_evidence_atomically(
    client: TestClient,
    app: FastAPI,
    auth_headers: dict[str, str],
) -> None:
    project_id, analysis_run, decisions = _prepare_phase2(
        client,
        auth_headers,
        key="phase3a-publication-race-prerequisites",
    )
    app.state.worker.controls.before_publication_gate.clear()
    try:
        created = _queue_casting(
            client,
            auth_headers,
            project_id=project_id,
            analysis_run=analysis_run,
            decisions=decisions,
            key="phase3a-publication-race",
        )
        job_id = created["job"]["jobId"]
        deadline = time.monotonic() + 20
        current = created["job"]
        while time.monotonic() < deadline:
            current = client.get(
                f"/api/v1/jobs/{job_id}",
                headers=auth_headers,
            ).json()["job"]
            if current["stage"] == "publish_reviewable_cast_snapshot":
                break
            time.sleep(0.01)
        assert current["stage"] == "publish_reviewable_cast_snapshot"
        _append_phase2_label_correction(
            client,
            auth_headers,
            project_id=project_id,
            analysis_run_id=analysis_run["runId"],
            idempotency_key="phase3a-publication-race-correction",
        )
    finally:
        app.state.worker.controls.before_publication_gate.set()

    terminal = wait_for_job(
        client,
        auth_headers,
        job_id,
        {"failed"},
        timeout=20,
    )
    assert terminal["error"] == {
        "code": "CASTING_PHASE_2_EVIDENCE_STALE",
        "message": "Voice casting could not be completed safely.",
        "retryable": False,
    }
    detail = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{created['run']['castingRunId']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    run = detail.json()["run"]
    assert run["status"] == "failed"
    assert run["approvedCastSnapshot"] is None
    assert run["summary"] is None
