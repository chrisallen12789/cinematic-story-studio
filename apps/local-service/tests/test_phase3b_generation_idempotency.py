from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.models import (
    AuditionSessionRow,
    IdempotencyRow,
    JobEventRow,
    JobRow,
    SpeechProviderRequestRow,
)
from cinematic_story_service.util import request_fingerprint
from tests.conftest import wait_for_job
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _create_session_and_script,
    _establish_approved_cast,
    _generate,
    _workspace,
)


def _generation_snapshot(client: TestClient, job_id: str) -> dict[str, Any]:
    with client.app.state.database.session() as session:
        job = session.get(JobRow, job_id)
        assert job is not None
        audition_session = session.get(AuditionSessionRow, job.target_id)
        assert audition_session is not None
        requests = list(
            session.scalars(
                select(SpeechProviderRequestRow)
                .where(SpeechProviderRequestRow.job_id == job_id)
                .order_by(
                    SpeechProviderRequestRow.attempt,
                    SpeechProviderRequestRow.id,
                )
            )
        )
        return {
            "job": (
                job.id,
                job.state,
                job.current_attempt,
                job.stage,
                job.progress,
                job.updated_at,
                job.terminal_at,
            ),
            "session": (
                audition_session.id,
                audition_session.state,
                audition_session.revision,
                audition_session.published_at,
            ),
            "providerRequests": [
                (
                    row.id,
                    row.attempt,
                    row.idempotency_key,
                    row.request_fingerprint,
                    row.outcome,
                    row.finished_at,
                )
                for row in requests
            ],
            "eventCount": int(
                session.scalar(
                    select(func.count())
                    .select_from(JobEventRow)
                    .where(JobEventRow.job_id == job_id)
                )
                or 0
            ),
            "jobCount": int(session.scalar(select(func.count()).select_from(JobRow)) or 0),
            "providerRequestCount": int(
                session.scalar(select(func.count()).select_from(SpeechProviderRequestRow)) or 0
            ),
        }


def _post_generation(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    session_id: str,
    generation_request: dict[str, Any],
):
    return client.post(
        f"/api/v1/projects/{project_id}/audition-sessions/{session_id}/generate",
        headers=auth_headers,
        json={"preview": generation_request},
    )


def test_terminal_generation_replay_is_exact_and_does_not_requeue(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-generation-idempotency",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-generation-idempotency",
        )
        role = next(
            value
            for value in _workspace(client, auth_headers, project_id)["roles"]["items"]
            if value["sessionEvidence"] is not None
        )

        succeeded_session, _script, succeeded_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role["roleId"],
            text="Repository-owned terminal replay success evidence.",
            key="phase3b-generation-idempotency-success",
        )
        succeeded_queue, succeeded_job = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=succeeded_session["auditionSessionId"],
            generation_request=succeeded_request,
        )
        assert succeeded_job["state"] == "succeeded"
        succeeded_before = _generation_snapshot(client, succeeded_queue["jobId"])

        succeeded_replay = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=succeeded_session["auditionSessionId"],
            generation_request=succeeded_request,
        )
        assert succeeded_replay.status_code == 202, succeeded_replay.text
        assert succeeded_replay.json()["jobId"] == succeeded_queue["jobId"]
        assert succeeded_replay.json()["session"]["state"] == "reviewable"
        assert succeeded_replay.json()["providerRequest"]["state"] == "succeeded"
        assert _generation_snapshot(client, succeeded_queue["jobId"]) == succeeded_before

        conflicting_request = dict(succeeded_request)
        conflicting_request["requestId"] = "phase3b-different-request"
        conflicting_material = dict(conflicting_request)
        conflicting_material.pop("requestFingerprint")
        conflicting_request["requestFingerprint"] = request_fingerprint(conflicting_material)
        conflict = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=succeeded_session["auditionSessionId"],
            generation_request=conflicting_request,
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert _generation_snapshot(client, succeeded_queue["jobId"]) == succeeded_before

        regeneration_request = dict(succeeded_request)
        regeneration_request["requestId"] = "phase3b-regenerated-request"
        regeneration_request["idempotencyKey"] = "phase3b-regenerated-idempotency"
        regeneration_material = dict(regeneration_request)
        regeneration_material.pop("requestFingerprint")
        regeneration_request["requestFingerprint"] = request_fingerprint(regeneration_material)
        regeneration_queue, regeneration_job = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=succeeded_session["auditionSessionId"],
            generation_request=regeneration_request,
        )
        assert regeneration_job["state"] == "succeeded"
        original_after_regeneration = _generation_snapshot(
            client,
            succeeded_queue["jobId"],
        )
        regeneration_before_replay = _generation_snapshot(
            client,
            regeneration_queue["jobId"],
        )

        historical_replay = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=succeeded_session["auditionSessionId"],
            generation_request=succeeded_request,
        )
        assert historical_replay.status_code == 202, historical_replay.text
        assert historical_replay.json()["jobId"] == succeeded_queue["jobId"]
        assert historical_replay.json()["session"]["jobId"] == succeeded_queue["jobId"]
        assert (
            historical_replay.json()["providerRequest"]["providerRequestId"]
            == original_after_regeneration["providerRequests"][-1][0]
        )
        assert _generation_snapshot(client, succeeded_queue["jobId"]) == original_after_regeneration
        assert (
            _generation_snapshot(client, regeneration_queue["jobId"]) == regeneration_before_replay
        )

        failed_session, _script, failed_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role["roleId"],
            text="Repository-owned terminal replay failure evidence.",
            key="phase3b-generation-idempotency-failure",
        )
        client.app.state.worker.fail_next_attempt()
        failed_queue, failed_job = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=failed_session["auditionSessionId"],
            generation_request=failed_request,
        )
        assert failed_job["state"] == "failed"
        failed_before = _generation_snapshot(client, failed_queue["jobId"])

        failed_replay = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=failed_session["auditionSessionId"],
            generation_request=failed_request,
        )
        assert failed_replay.status_code == 202, failed_replay.text
        assert failed_replay.json()["jobId"] == failed_queue["jobId"]
        assert failed_replay.json()["session"]["state"] == "failed"
        assert failed_replay.json()["providerRequest"]["state"] == "failed"
        assert _generation_snapshot(client, failed_queue["jobId"]) == failed_before

        retried_response = client.post(
            f"/api/v1/jobs/{failed_queue['jobId']}/retry",
            headers=auth_headers,
        )
        assert retried_response.status_code == 200, retried_response.text
        assert retried_response.json()["job"]["attempt"] == 2
        retried_job = wait_for_job(
            client,
            auth_headers,
            failed_queue["jobId"],
            {"succeeded", "failed"},
            timeout=30,
        )
        assert retried_job["state"] == "succeeded", retried_job
        retried_before = _generation_snapshot(client, failed_queue["jobId"])

        retried_replay = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=failed_session["auditionSessionId"],
            generation_request=failed_request,
        )
        assert retried_replay.status_code == 202, retried_replay.text
        assert retried_replay.json()["jobId"] == failed_queue["jobId"]
        assert retried_replay.json()["session"]["state"] == "reviewable"
        assert retried_replay.json()["providerRequest"]["state"] == "succeeded"
        assert (
            retried_replay.json()["providerRequest"]["providerRequestId"]
            == retried_before["providerRequests"][-1][0]
        )
        assert _generation_snapshot(client, failed_queue["jobId"]) == retried_before

        cancelled_session, _script, cancelled_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role["roleId"],
            text="Repository-owned terminal replay cancellation evidence.",
            key="phase3b-generation-idempotency-cancellation",
        )
        worker = client.app.state.worker
        worker.controls.execution_gate.clear()
        try:
            cancelled_queue, running_job = _generate(
                client,
                auth_headers,
                project_id=project_id,
                session_id=cancelled_session["auditionSessionId"],
                generation_request=cancelled_request,
                terminal_states={"running"},
            )
            assert running_job["state"] == "running"
            cancel = client.post(
                f"/api/v1/jobs/{cancelled_queue['jobId']}/cancel",
                headers=auth_headers,
            )
            assert cancel.status_code == 200, cancel.text
            cancelled_job = wait_for_job(
                client,
                auth_headers,
                cancelled_queue["jobId"],
                {"cancelled"},
                timeout=20,
            )
            assert cancelled_job["state"] == "cancelled"
        finally:
            worker.controls.execution_gate.set()
        cancelled_before = _generation_snapshot(client, cancelled_queue["jobId"])

        cancelled_replay = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=cancelled_session["auditionSessionId"],
            generation_request=cancelled_request,
        )
        assert cancelled_replay.status_code == 202, cancelled_replay.text
        assert cancelled_replay.json()["jobId"] == cancelled_queue["jobId"]
        assert cancelled_replay.json()["session"]["state"] == "cancelled"
        assert cancelled_replay.json()["providerRequest"]["state"] == "cancelled"
        assert _generation_snapshot(client, cancelled_queue["jobId"]) == cancelled_before

        idempotency_identity = {
            "scope": f"create_audition_job:{project_id}",
            "key": cancelled_request["idempotencyKey"],
        }
        with client.app.state.database.immediate_session() as database_session:
            idempotency = database_session.get(IdempotencyRow, idempotency_identity)
            assert idempotency is not None
            expected_hash = idempotency.request_hash
            expected_resource_id = idempotency.resource_id
            idempotency.request_hash = "0" * 64
        corrupted_hash = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=cancelled_session["auditionSessionId"],
            generation_request=cancelled_request,
        )
        assert corrupted_hash.status_code == 500, corrupted_hash.text
        assert corrupted_hash.json()["error"]["code"] == "IDEMPOTENCY_RECORD_INVALID"

        with client.app.state.database.immediate_session() as database_session:
            idempotency = database_session.get(IdempotencyRow, idempotency_identity)
            assert idempotency is not None
            idempotency.request_hash = expected_hash
            idempotency.resource_id = succeeded_queue["jobId"]
        corrupted_resource = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=cancelled_session["auditionSessionId"],
            generation_request=cancelled_request,
        )
        assert corrupted_resource.status_code == 500, corrupted_resource.text
        assert corrupted_resource.json()["error"]["code"] == "IDEMPOTENCY_RECORD_INVALID"

        with client.app.state.database.immediate_session() as database_session:
            idempotency = database_session.get(IdempotencyRow, idempotency_identity)
            assert idempotency is not None
            idempotency.resource_id = expected_resource_id
            database_session.delete(idempotency)
        missing_idempotency = _post_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=cancelled_session["auditionSessionId"],
            generation_request=cancelled_request,
        )
        assert missing_idempotency.status_code == 500, missing_idempotency.text
        assert missing_idempotency.json()["error"]["code"] == "IDEMPOTENCY_RECORD_INVALID"
        assert _generation_snapshot(client, cancelled_queue["jobId"]) == cancelled_before
