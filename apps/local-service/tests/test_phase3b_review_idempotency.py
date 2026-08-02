from __future__ import annotations

from typing import Any, Literal

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.models import (
    AuditionReviewDecisionRow,
    AuditionReviewRecordRow,
    VoiceReadinessDecisionRow,
    VoiceReadinessReviewRow,
)
from cinematic_story_service.util import new_id
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _approve_audition_review,
    _create_session_and_script,
    _establish_approved_cast,
    _generate,
    _workspace,
)


def _review(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    gate_id: str,
    role_id: str | None,
) -> dict[str, Any]:
    return next(
        value
        for value in _workspace(client, auth_headers, project_id)["reviews"]
        if value["gateId"] == gate_id and value["roleId"] == role_id
    )


def _decide(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    gate_id: str,
    review: dict[str, Any],
    decision: Literal["approve", "request_changes", "reject"],
    supersedes_decision_id: str | None,
    key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {
        "expectedReviewRevision": review["revision"],
        "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
        "decision": decision,
        "rationale": f"Exercise repository-owned {gate_id} idempotency evidence.",
        "supersedesDecisionId": supersedes_decision_id,
        "idempotencyKey": key,
    }
    response = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-reviews/"
            f"{gate_id}/{review['reviewId']}/decisions"
        ),
        headers=auth_headers,
        json=request,
    )
    assert response.status_code == 200, response.text
    value = response.json()
    value.pop("correlationId")
    return request, value


def _replay(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    gate_id: str,
    review_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        (f"/api/v1/projects/{project_id}/audition-reviews/{gate_id}/{review_id}/decisions"),
        headers=auth_headers,
        json=request,
    )
    assert response.status_code == 200, response.text
    value = response.json()
    value.pop("correlationId")
    return value


def test_review_decisions_replay_exact_historical_response_without_mutation(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-review-idempotency",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-review-idempotency",
        )
        roles = _workspace(client, auth_headers, project_id)["roles"]["items"]
        assert roles

        for index, role in enumerate(roles):
            audition_session, _script, generation_request = _create_session_and_script(
                client,
                auth_headers,
                project_id=project_id,
                role_id=role["roleId"],
                text=f"Repository-owned review idempotency signal {index}.",
                key=f"phase3b-review-idempotency-role-{index}",
            )
            _queued, terminal = _generate(
                client,
                auth_headers,
                project_id=project_id,
                session_id=audition_session["auditionSessionId"],
                generation_request=generation_request,
            )
            assert terminal["state"] == "succeeded", terminal.get("error")
            if index != 0:
                _approve_audition_review(
                    client,
                    auth_headers,
                    project_id=project_id,
                    gate_id="per_role_audition_review",
                    role_id=role["roleId"],
                    key=f"phase3b-review-idempotency-role-{index}-approve",
                )
                continue

            per_role_review = _review(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="per_role_audition_review",
                role_id=role["roleId"],
            )
            request_a, response_a = _decide(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="per_role_audition_review",
                review=per_role_review,
                decision="approve",
                supersedes_decision_id=None,
                key="phase3b-review-idempotency-per-role-a",
            )
            decision_a_id = response_a["decision"]["decisionId"]
            _request_b, response_b = _decide(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="per_role_audition_review",
                review=per_role_review,
                decision="request_changes",
                supersedes_decision_id=decision_a_id,
                key="phase3b-review-idempotency-per-role-b",
            )
            assert response_b["review"]["state"] == "changes_requested"
            with client.app.state.database.session() as database_session:
                before_replay = int(
                    database_session.scalar(
                        select(func.count())
                        .select_from(AuditionReviewDecisionRow)
                        .where(
                            AuditionReviewDecisionRow.project_id == project_id,
                            AuditionReviewDecisionRow.gate_id == "per_role_audition_review",
                            AuditionReviewDecisionRow.scope_key == role["roleId"],
                        )
                    )
                    or 0
                )
            assert (
                _replay(
                    client,
                    auth_headers,
                    project_id=project_id,
                    gate_id="per_role_audition_review",
                    review_id=per_role_review["reviewId"],
                    request=request_a,
                )
                == response_a
            )
            with client.app.state.database.session() as database_session:
                after_replay = int(
                    database_session.scalar(
                        select(func.count())
                        .select_from(AuditionReviewDecisionRow)
                        .where(
                            AuditionReviewDecisionRow.project_id == project_id,
                            AuditionReviewDecisionRow.gate_id == "per_role_audition_review",
                            AuditionReviewDecisionRow.scope_key == role["roleId"],
                        )
                    )
                    or 0
                )
            assert after_replay == before_replay == 2
            current_per_role = _review(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="per_role_audition_review",
                role_id=role["roleId"],
            )
            assert (
                current_per_role["latestDecision"]["decisionId"]
                == response_b["decision"]["decisionId"]
            )
            _decide(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="per_role_audition_review",
                review=per_role_review,
                decision="approve",
                supersedes_decision_id=response_b["decision"]["decisionId"],
                key="phase3b-review-idempotency-per-role-c",
            )

        _approve_audition_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="narrator_audition_review",
            role_id=None,
            key="phase3b-review-idempotency-narrator",
        )
        _approve_audition_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="character_audition_review",
            role_id=None,
            key="phase3b-review-idempotency-character",
        )

        pronunciation_review = _review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="pronunciation_review",
            role_id=None,
        )
        ordinary_request_a, ordinary_response_a = _decide(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="pronunciation_review",
            review=pronunciation_review,
            decision="approve",
            supersedes_decision_id=None,
            key="phase3b-review-idempotency-ordinary-a",
        )
        _ordinary_request_b, ordinary_response_b = _decide(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="pronunciation_review",
            review=pronunciation_review,
            decision="request_changes",
            supersedes_decision_id=ordinary_response_a["decision"]["decisionId"],
            key="phase3b-review-idempotency-ordinary-b",
        )
        with client.app.state.database.session() as database_session:
            ordinary_count = int(
                database_session.scalar(
                    select(func.count())
                    .select_from(AuditionReviewDecisionRow)
                    .where(
                        AuditionReviewDecisionRow.project_id == project_id,
                        AuditionReviewDecisionRow.gate_id == "pronunciation_review",
                    )
                )
                or 0
            )
        assert (
            _replay(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="pronunciation_review",
                review_id=pronunciation_review["reviewId"],
                request=ordinary_request_a,
            )
            == ordinary_response_a
        )
        with client.app.state.database.session() as database_session:
            assert (
                int(
                    database_session.scalar(
                        select(func.count())
                        .select_from(AuditionReviewDecisionRow)
                        .where(
                            AuditionReviewDecisionRow.project_id == project_id,
                            AuditionReviewDecisionRow.gate_id == "pronunciation_review",
                        )
                    )
                    or 0
                )
                == ordinary_count
                == 2
            )
        current_ordinary = _review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="pronunciation_review",
            role_id=None,
        )
        assert (
            current_ordinary["latestDecision"]["decisionId"]
            == ordinary_response_b["decision"]["decisionId"]
        )
        _decide(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="pronunciation_review",
            review=pronunciation_review,
            decision="approve",
            supersedes_decision_id=ordinary_response_b["decision"]["decisionId"],
            key="phase3b-review-idempotency-ordinary-c",
        )

        readiness_review = _review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="voice_readiness_review",
            role_id=None,
        )
        readiness_request_a, readiness_response_a = _decide(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="voice_readiness_review",
            review=readiness_review,
            decision="approve",
            supersedes_decision_id=None,
            key="phase3b-review-idempotency-readiness-a",
        )
        _readiness_request_b, readiness_response_b = _decide(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="voice_readiness_review",
            review=readiness_review,
            decision="request_changes",
            supersedes_decision_id=readiness_response_a["decision"]["decisionId"],
            key="phase3b-review-idempotency-readiness-b",
        )
        with client.app.state.database.session() as database_session:
            readiness_count = int(
                database_session.scalar(
                    select(func.count())
                    .select_from(VoiceReadinessDecisionRow)
                    .where(VoiceReadinessDecisionRow.project_id == project_id)
                )
                or 0
            )
        assert (
            _replay(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="voice_readiness_review",
                review_id=readiness_review["reviewId"],
                request=readiness_request_a,
            )
            == readiness_response_a
        )
        with client.app.state.database.session() as database_session:
            assert (
                int(
                    database_session.scalar(
                        select(func.count())
                        .select_from(VoiceReadinessDecisionRow)
                        .where(VoiceReadinessDecisionRow.project_id == project_id)
                    )
                    or 0
                )
                == readiness_count
                == 2
            )
        current_readiness = _review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="voice_readiness_review",
            role_id=None,
        )
        assert (
            current_readiness["latestDecision"]["decisionId"]
            == readiness_response_b["decision"]["decisionId"]
        )

        with client.app.state.database.immediate_session() as database_session:
            ordinary_source = database_session.get(
                AuditionReviewRecordRow,
                pronunciation_review["reviewId"],
            )
            readiness_source = database_session.get(
                VoiceReadinessReviewRow,
                readiness_review["reviewId"],
            )
            assert ordinary_source is not None
            assert readiness_source is not None
            ordinary_other_id = new_id()
            readiness_other_id = new_id()
            database_session.add(
                AuditionReviewRecordRow(
                    id=ordinary_other_id,
                    project_id=ordinary_source.project_id,
                    gate_id=ordinary_source.gate_id,
                    scope_key=ordinary_source.scope_key,
                    subject_type=ordinary_source.subject_type,
                    revision=ordinary_source.revision + 1,
                    session_id=ordinary_source.session_id,
                    clip_id=ordinary_source.clip_id,
                    role_id=ordinary_source.role_id,
                    pronunciation_dictionary_record_id=(
                        ordinary_source.pronunciation_dictionary_record_id
                    ),
                    eligible=ordinary_source.eligible,
                    evidence_json=ordinary_source.evidence_json,
                    evidence_fingerprint=ordinary_source.evidence_fingerprint,
                    required_decision_ids_json=(ordinary_source.required_decision_ids_json),
                    blockers_json=ordinary_source.blockers_json,
                    warnings_json=ordinary_source.warnings_json,
                    provenance_json=ordinary_source.provenance_json,
                    created_at=ordinary_source.created_at,
                )
            )
            database_session.add(
                VoiceReadinessReviewRow(
                    id=readiness_other_id,
                    project_id=readiness_source.project_id,
                    snapshot_id=readiness_source.snapshot_id,
                    gate_id=readiness_source.gate_id,
                    revision=readiness_source.revision + 1,
                    eligible=readiness_source.eligible,
                    evidence_fingerprint=readiness_source.evidence_fingerprint,
                    required_decision_ids_json=(readiness_source.required_decision_ids_json),
                    blockers_json=readiness_source.blockers_json,
                    warnings_json=readiness_source.warnings_json,
                    provenance_json=readiness_source.provenance_json,
                    created_at=readiness_source.created_at,
                )
            )

        for gate_id, other_review_id, original_request in (
            (
                "pronunciation_review",
                ordinary_other_id,
                ordinary_request_a,
            ),
            (
                "voice_readiness_review",
                readiness_other_id,
                readiness_request_a,
            ),
        ):
            conflict = client.post(
                (
                    f"/api/v1/projects/{project_id}/audition-reviews/"
                    f"{gate_id}/{other_review_id}/decisions"
                ),
                headers=auth_headers,
                json=original_request,
            )
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
