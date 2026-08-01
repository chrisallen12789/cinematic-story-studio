from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.casting import CASTING_GATE_IDS
from cinematic_story_service.models import (
    AudioArtifactRow,
    AuditionCacheRecordRow,
    AuditionEvidenceInvalidationRow,
    AuditionReviewRecordRow,
    AuditionSessionRow,
    CastingGateDecisionRow,
    CastingGateReviewRow,
    IdempotencyRow,
    ProjectRow,
    VoiceReadinessSnapshotRow,
    VoiceRightsRecordRow,
)
from cinematic_story_service.util import (
    canonical_json,
    new_id,
    parse_json,
    request_fingerprint,
    utc_now,
)
from tests.test_phase3a_casting import _correct, _roles
from tests.test_phase3a_governance import _decision_payload, _post_decision, _reviews
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _clips,
    _create_session_and_script,
    _establish_approved_cast,
    _generate,
    _workspace,
)


def _approve_current_review(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    gate_id: str,
    key: str,
    role_id: str | None = None,
) -> dict[str, Any]:
    workspace = _workspace(client, auth_headers, project_id)
    review = next(
        value
        for value in workspace["reviews"]
        if value["gateId"] == gate_id and value["roleId"] == role_id
    )
    assert review["state"] == "pending", review
    evidence_fingerprint = review["evidence"].get("evidenceFingerprint")
    if evidence_fingerprint is None:
        snapshot = workspace["voiceReadinessSnapshot"]
        assert gate_id == "voice_readiness_review"
        assert snapshot is not None
        evidence_fingerprint = snapshot["snapshotFingerprint"]
    supersession_head = review["latestDecision"]
    response = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-reviews/{gate_id}/"
            f"{review['reviewId']}/decisions"
        ),
        headers=auth_headers,
        json={
            "expectedReviewRevision": review["revision"],
            "expectedEvidenceFingerprint": evidence_fingerprint,
            "decision": "approve",
            "rationale": f"Approve current repository-owned {gate_id} evidence.",
            "supersedesDecisionId": (
                supersession_head["decisionId"] if supersession_head is not None else None
            ),
            "idempotencyKey": key,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"]["decision"] == "approved"
    return response.json()


def _generate_role_replacements(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_ids: list[str],
    key: str,
) -> None:
    for index, role_id in enumerate(role_ids):
        session, _script, request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text=f"Repository-owned reconciliation audition {key} {index}.",
            key=f"{key}-role-{index}",
        )
        _queued, terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session["auditionSessionId"],
            generation_request=request,
        )
        assert terminal["state"] == "succeeded", terminal


def _approve_current_voice_evidence(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_ids: list[str],
    key: str,
) -> dict[str, Any]:
    for index, role_id in enumerate(role_ids):
        _approve_current_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="per_role_audition_review",
            role_id=role_id,
            key=f"{key}-per-role-{index}",
        )
    for gate_id in (
        "narrator_audition_review",
        "character_audition_review",
        "pronunciation_review",
    ):
        _approve_current_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id=gate_id,
            key=f"{key}-{gate_id}",
        )
    return _approve_current_review(
        client,
        auth_headers,
        project_id=project_id,
        gate_id="voice_readiness_review",
        key=f"{key}-voice-readiness",
    )


def test_untrusted_forged_phase3a_approvals_do_not_grant_authority(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-untrusted-phase3a-forgery",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-untrusted-phase3a-forgery",
        )
        initial_workspace = _workspace(client, auth_headers, project_id)
        role = next(
            value
            for value in initial_workspace["roles"]["items"]
            if value["sessionEvidence"] is not None
        )
        stale_session_evidence = role["sessionEvidence"]
        snapshot = casting_run["approvedCastSnapshot"]
        assert snapshot is not None
        assert initial_workspace["approvedCastSnapshot"] == {
            "snapshotId": snapshot["snapshotId"],
            "revision": snapshot["revision"],
            "fingerprint": snapshot["snapshotFingerprint"],
        }

        forged_decision_ids: dict[str, str] = {}
        with client.app.state.database.immediate_session() as database_session:
            for gate_id in CASTING_GATE_IDS:
                prior_review = database_session.scalar(
                    select(CastingGateReviewRow)
                    .where(
                        CastingGateReviewRow.casting_run_id == casting_run["castingRunId"],
                        CastingGateReviewRow.gate_id == gate_id,
                    )
                    .order_by(
                        CastingGateReviewRow.revision.desc(),
                        CastingGateReviewRow.id.desc(),
                    )
                    .limit(1)
                )
                prior_decision = database_session.scalar(
                    select(CastingGateDecisionRow)
                    .where(
                        CastingGateDecisionRow.casting_run_id == casting_run["castingRunId"],
                        CastingGateDecisionRow.gate_id == gate_id,
                    )
                    .order_by(
                        CastingGateDecisionRow.revision.desc(),
                        CastingGateDecisionRow.id.desc(),
                    )
                    .limit(1)
                )
                assert prior_review is not None and prior_review.eligible
                assert prior_decision is not None and prior_decision.decision == "approved"

                now = utc_now()
                review_id = new_id()
                evidence_fingerprint = request_fingerprint(
                    {
                        "castingRunId": casting_run["castingRunId"],
                        "gateId": gate_id,
                        "snapshotId": snapshot["snapshotId"],
                        "snapshotRevision": snapshot["revision"],
                        "snapshotFingerprint": snapshot["snapshotFingerprint"],
                        "blockers": [],
                        "warnings": [],
                    }
                )
                database_session.add(
                    CastingGateReviewRow(
                        id=review_id,
                        project_id=project_id,
                        casting_run_id=casting_run["castingRunId"],
                        cast_snapshot_id=snapshot["snapshotId"],
                        gate_id=gate_id,
                        revision=prior_review.revision + 1,
                        eligible=True,
                        evidence_fingerprint=evidence_fingerprint,
                        required_gate_decision_ids_json=canonical_json(
                            list(CASTING_GATE_IDS[:2]) if gate_id == "complete_cast_review" else []
                        ),
                        warnings_json=canonical_json({"warningIds": [], "blockingReasonCodes": []}),
                        provenance_json=canonical_json(
                            {
                                "origin": "untrusted_import",
                                "producerId": "untrusted-test-producer",
                                "producerVersion": "0.0.0",
                                "recordedAt": now,
                                "inputFingerprint": snapshot["snapshotFingerprint"],
                            }
                        ),
                        created_at=now,
                    )
                )
                database_session.flush()

                decision_id = new_id()
                forged_decision_ids[gate_id] = decision_id
                rationale = "Untrusted raw rows must not manufacture Phase 3A authority."
                decision_request_fingerprint = request_fingerprint(
                    {
                        "projectId": project_id,
                        "castingRunId": casting_run["castingRunId"],
                        "gateId": gate_id,
                        "decision": "approved",
                        "expectedRevision": prior_review.revision + 1,
                        "expectedEvidenceFingerprint": evidence_fingerprint,
                        "expectedRunFingerprint": snapshot["snapshotFingerprint"],
                        "expectedApprovedCastSnapshotId": snapshot["snapshotId"],
                        "expectedApprovedCastSnapshotRevision": snapshot["revision"],
                        "warningAcknowledgementIds": [],
                        "rationale": rationale,
                        "supersedesDecisionId": None,
                    }
                )
                database_session.add(
                    CastingGateDecisionRow(
                        id=decision_id,
                        project_id=project_id,
                        casting_run_id=casting_run["castingRunId"],
                        cast_snapshot_id=snapshot["snapshotId"],
                        gate_review_id=review_id,
                        gate_id=gate_id,
                        revision=prior_decision.revision + 1,
                        decision="approved",
                        evidence_fingerprint=evidence_fingerprint,
                        actor_id="untrusted-actor",
                        warning_acknowledgements_json=canonical_json([]),
                        rationale=rationale,
                        provenance_json=canonical_json(
                            {
                                "origin": "untrusted_import",
                                "producerId": "untrusted-test-producer",
                                "producerVersion": "0.0.0",
                                "recordedAt": now,
                                "inputFingerprint": evidence_fingerprint,
                                "requestFingerprint": decision_request_fingerprint,
                            }
                        ),
                        supersedes_decision_id=prior_decision.id,
                        idempotency_key=f"phase3b-untrusted-forgery-{gate_id}",
                        decided_at=now,
                        created_at=now,
                    )
                )

        forged_workspace = _workspace(client, auth_headers, project_id)
        assert forged_workspace["approvedCastSnapshot"] is None
        for gate_id, decision_id in forged_decision_ids.items():
            prerequisite = next(
                value
                for value in forged_workspace["prerequisites"]
                if value["prerequisiteId"] == f"phase3a_{gate_id}"
            )
            assert prerequisite["current"] is False
            assert prerequisite["statusCode"] == "APPROVAL_REQUIRED"
            assert prerequisite["evidenceId"] == decision_id
        assert all(value["sessionEvidence"] is None for value in forged_workspace["roles"]["items"])

        rejected_session = client.post(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            json={
                "roleId": role["roleId"],
                "evidence": stale_session_evidence,
                "idempotencyKey": "phase3b-untrusted-phase3a-forged-session",
            },
        )
        assert rejected_session.status_code == 409, rejected_session.text
        assert rejected_session.json()["error"]["code"] == "AUDITION_PHASE3A_APPROVAL_REQUIRED"
        with client.app.state.database.session() as database_session:
            assert (
                database_session.scalar(
                    select(AuditionSessionRow).where(AuditionSessionRow.project_id == project_id)
                )
                is None
            )


def test_same_snapshot_upstream_reapproval_is_independent_of_intermediate_review_reads(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-upstream-read-path-independence",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-upstream-read-path-independence",
        )
        snapshot = casting_run["approvedCastSnapshot"]
        assert snapshot is not None
        initial_workspace = _workspace(client, auth_headers, project_id)
        assert initial_workspace["approvedCastSnapshot"] == {
            "snapshotId": snapshot["snapshotId"],
            "revision": snapshot["revision"],
            "fingerprint": snapshot["snapshotFingerprint"],
        }
        role_ids = [value["roleId"] for value in initial_workspace["roles"]["items"]]
        assert role_ids
        _generate_role_replacements(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="phase3b-upstream-read-path-initial",
        )
        _approve_current_voice_evidence(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="phase3b-upstream-read-path-initial",
        )
        ready_workspace = _workspace(client, auth_headers, project_id)
        assert ready_workspace["voiceReadinessSnapshot"] is not None

        def complete_identity() -> dict[str, Any]:
            with client.app.state.database.session() as database_session:
                review_rows = list(
                    database_session.scalars(
                        select(CastingGateReviewRow)
                        .where(
                            CastingGateReviewRow.casting_run_id == casting_run["castingRunId"],
                            CastingGateReviewRow.gate_id == "complete_cast_review",
                        )
                        .order_by(
                            CastingGateReviewRow.revision.desc(),
                            CastingGateReviewRow.id.desc(),
                        )
                    )
                )
                decision_rows = list(
                    database_session.scalars(
                        select(CastingGateDecisionRow)
                        .where(
                            CastingGateDecisionRow.casting_run_id == casting_run["castingRunId"],
                            CastingGateDecisionRow.gate_id == "complete_cast_review",
                        )
                        .order_by(
                            CastingGateDecisionRow.revision.desc(),
                            CastingGateDecisionRow.id.desc(),
                        )
                    )
                )
                assert review_rows
                assert decision_rows
                return {
                    "reviewCount": len(review_rows),
                    "reviewId": review_rows[0].id,
                    "reviewRevision": review_rows[0].revision,
                    "reviewEvidenceFingerprint": review_rows[0].evidence_fingerprint,
                    "requiredGateDecisionIds": parse_json(
                        review_rows[0].required_gate_decision_ids_json,
                        None,
                    ),
                    "decisionCount": len(decision_rows),
                    "decisionId": decision_rows[0].id,
                }

        baseline_complete = complete_identity()
        initial_casting_reviews = {
            value["gateId"]: value for value in _reviews(client, auth_headers, run=casting_run)
        }
        assert initial_casting_reviews["complete_cast_review"]["state"] == "approved"
        assert (
            initial_casting_reviews["complete_cast_review"]["latestDecision"]["decisionId"]
            == baseline_complete["decisionId"]
        )

        outcomes: dict[tuple[str, bool], dict[str, Any]] = {}
        cycles = (
            ("narrator_casting_review", False),
            ("narrator_casting_review", True),
            ("character_casting_review", False),
            ("character_casting_review", True),
        )
        for ordinal, (upstream_gate_id, intermediate_review_read) in enumerate(cycles):
            cycle_key = f"phase3b-upstream-cycle-{ordinal}"
            before_cycle = _workspace(client, auth_headers, project_id)
            assert before_cycle["approvedCastSnapshot"] == initial_workspace["approvedCastSnapshot"]
            current_session_ids = {
                value["latestSessionId"]
                for value in before_cycle["roles"]["items"]
                if value["latestSessionId"] is not None
            }
            assert current_session_ids
            pronunciation_before = next(
                value
                for value in before_cycle["reviews"]
                if value["gateId"] == "pronunciation_review"
            )
            assert pronunciation_before["state"] == "approved"
            prior_pronunciation_decision_id = pronunciation_before["latestDecision"]["decisionId"]

            current_reviews = {
                value["gateId"]: value for value in _reviews(client, auth_headers, run=casting_run)
            }
            upstream_review = current_reviews[upstream_gate_id]
            prior_upstream_decision_id = upstream_review["latestDecision"]["decisionId"]
            changes_response = _post_decision(
                client,
                auth_headers,
                run=casting_run,
                gate_id=upstream_gate_id,
                payload=_decision_payload(
                    run=casting_run,
                    review=upstream_review,
                    key=f"{cycle_key}-changes-requested",
                    decision="request_changes",
                    supersedes_decision_id=prior_upstream_decision_id,
                ),
            )
            assert changes_response.status_code == 200, changes_response.text
            changes = changes_response.json()
            changes_decision_id = changes["decision"]["decisionId"]
            assert changes["decision"]["decision"] == "changes_requested"
            assert changes["decision"]["supersedesDecisionId"] == prior_upstream_decision_id
            assert changes["snapshot"]["snapshotId"] == snapshot["snapshotId"]
            assert changes["snapshot"]["snapshotFingerprint"] == snapshot["snapshotFingerprint"]

            reapproval_review = upstream_review
            if intermediate_review_read:
                intermediate_reviews = {
                    value["gateId"]: value
                    for value in _reviews(client, auth_headers, run=casting_run)
                }
                reapproval_review = intermediate_reviews[upstream_gate_id]
                intermediate_complete = intermediate_reviews["complete_cast_review"]
                assert intermediate_complete["state"] == "invalidated"
                assert intermediate_complete["latestDecision"] is None
                assert complete_identity() == baseline_complete

            blocked_workspace = _workspace(client, auth_headers, project_id)
            assert blocked_workspace["approvedCastSnapshot"] is None
            assert all(
                value["sessionEvidence"] is None for value in blocked_workspace["roles"]["items"]
            )
            assert not any(
                value["gateId"] == "pronunciation_review" for value in blocked_workspace["reviews"]
            )
            assert blocked_workspace["voiceReadinessSnapshot"] is None
            assert complete_identity() == baseline_complete
            with client.app.state.database.session() as database_session:
                invalidated_sessions = list(
                    database_session.scalars(
                        select(AuditionSessionRow).where(
                            AuditionSessionRow.id.in_(current_session_ids)
                        )
                    )
                )
                assert len(invalidated_sessions) == len(current_session_ids)
                assert {value.state for value in invalidated_sessions} == {"invalidated"}

            reapproval_response = _post_decision(
                client,
                auth_headers,
                run=casting_run,
                gate_id=upstream_gate_id,
                payload=_decision_payload(
                    run=casting_run,
                    review=reapproval_review,
                    key=f"{cycle_key}-reapproved",
                    supersedes_decision_id=changes_decision_id,
                ),
            )
            assert reapproval_response.status_code == 200, reapproval_response.text
            reapproval = reapproval_response.json()
            replacement_upstream_decision_id = reapproval["decision"]["decisionId"]
            assert reapproval["decision"]["decision"] == "approved"
            assert reapproval["decision"]["supersedesDecisionId"] == changes_decision_id
            assert replacement_upstream_decision_id != prior_upstream_decision_id
            assert reapproval["snapshot"]["snapshotId"] == snapshot["snapshotId"]
            assert reapproval["snapshot"]["snapshotFingerprint"] == snapshot["snapshotFingerprint"]

            final_reviews = {
                value["gateId"]: value for value in _reviews(client, auth_headers, run=casting_run)
            }
            final_complete = final_reviews["complete_cast_review"]
            assert final_complete["state"] == "approved"
            assert final_complete["latestDecision"]["decisionId"] == baseline_complete["decisionId"]
            assert complete_identity() == baseline_complete
            restored_workspace = _workspace(client, auth_headers, project_id)
            assert (
                restored_workspace["approvedCastSnapshot"]
                == initial_workspace["approvedCastSnapshot"]
            )
            assert all(
                value["current"]
                for value in restored_workspace["prerequisites"]
                if value["prerequisiteId"].startswith("phase3a_")
            )
            assert all(
                value["sessionEvidence"] is not None
                for value in restored_workspace["roles"]["items"]
            )
            assert not any(
                value["gateId"] == "pronunciation_review" for value in restored_workspace["reviews"]
            )
            assert restored_workspace["voiceReadinessSnapshot"] is None

            replacement_session, _script, _request = _create_session_and_script(
                client,
                auth_headers,
                project_id=project_id,
                role_id=role_ids[0],
                text=f"Repository-owned upstream authority cycle {ordinal}.",
                key=f"{cycle_key}-replacement",
            )
            exact_phase3a_ids = [
                final_reviews[gate_id]["latestDecision"]["decisionId"]
                for gate_id in CASTING_GATE_IDS
            ]
            with client.app.state.database.session() as database_session:
                replacement_session_row = database_session.get(
                    AuditionSessionRow,
                    replacement_session["auditionSessionId"],
                )
                assert replacement_session_row is not None
                assert (
                    parse_json(
                        replacement_session_row.phase3a_gate_decision_ids_json,
                        None,
                    )
                    == exact_phase3a_ids
                )
            assert exact_phase3a_ids[CASTING_GATE_IDS.index(upstream_gate_id)] == (
                replacement_upstream_decision_id
            )
            assert (
                exact_phase3a_ids[CASTING_GATE_IDS.index("complete_cast_review")]
                == (baseline_complete["decisionId"])
            )

            replacement_workspace = _workspace(client, auth_headers, project_id)
            replacement_pronunciation = next(
                value
                for value in replacement_workspace["reviews"]
                if value["gateId"] == "pronunciation_review"
            )
            assert replacement_pronunciation["state"] == "pending"
            replacement_pronunciation_approval = _approve_current_review(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="pronunciation_review",
                key=f"{cycle_key}-pronunciation-reapproved",
            )
            assert (
                replacement_pronunciation_approval["decision"]["decisionId"]
                != prior_pronunciation_decision_id
            )
            after_downstream_reapproval = _workspace(client, auth_headers, project_id)
            assert after_downstream_reapproval["voiceReadinessSnapshot"] is None

            outcomes[(upstream_gate_id, intermediate_review_read)] = {
                "completeIdentityUnchanged": complete_identity() == baseline_complete,
                "completeDecisionReused": (
                    final_complete["latestDecision"]["decisionId"]
                    == baseline_complete["decisionId"]
                ),
                "castAuthorityRestored": (
                    restored_workspace["approvedCastSnapshot"]
                    == initial_workspace["approvedCastSnapshot"]
                ),
                "replacementUpstreamBound": (
                    exact_phase3a_ids[CASTING_GATE_IDS.index(upstream_gate_id)]
                    == replacement_upstream_decision_id
                ),
                "completeDecisionBound": (
                    exact_phase3a_ids[CASTING_GATE_IDS.index("complete_cast_review")]
                    == baseline_complete["decisionId"]
                ),
                "priorSessionsInvalidated": True,
                "pronunciationReapprovalRequired": True,
                "readinessReapprovalRequired": (
                    after_downstream_reapproval["voiceReadinessSnapshot"] is None
                ),
            }

        for upstream_gate_id in CASTING_GATE_IDS[:2]:
            assert outcomes[(upstream_gate_id, False)] == outcomes[(upstream_gate_id, True)]


def test_temporal_rights_predicate_invalidates_phase3b_session_authority(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-temporally-expired-rights",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-temporally-expired-rights",
        )
        initial_workspace = _workspace(client, auth_headers, project_id)
        assert initial_workspace["approvedCastSnapshot"] is not None
        role = next(
            value
            for value in initial_workspace["roles"]["items"]
            if value["sessionEvidence"] is not None
        )
        stale_session_evidence = role["sessionEvidence"]
        created = client.post(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            json={
                "roleId": role["roleId"],
                "evidence": stale_session_evidence,
                "idempotencyKey": "phase3b-temporally-expired-rights-existing",
            },
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["session"]["auditionSessionId"]

        with client.app.state.database.session() as database_session:
            audition_session = database_session.get(AuditionSessionRow, session_id)
            assert audition_session is not None
            rights = database_session.get(
                VoiceRightsRecordRow,
                audition_session.rights_record_id,
            )
            assert rights is not None
            assert rights.rights_record_id == stale_session_evidence["rightsRecordId"]
            assert rights.revision == stale_session_evidence["rightsRecordRevision"]
            assert rights.rights_fingerprint == stale_session_evidence["rightsRecordFingerprint"]
            rights_row_id = rights.id
            original_rights_fingerprint = rights.rights_fingerprint
            original_expiration_date = rights.expiration_date
            limitations = parse_json(rights.distribution_limitations_json, {})
            assert isinstance(limitations, dict)
            selected_rights_material = {
                "state": rights.rights_state,
                "licenseIdentifier": rights.license_identifier,
                "rightsBasis": rights.rights_basis,
                "commercialUsePermission": rights.commercial_use_status,
                "attributionRequirement": ("required" if rights.attribution_required else "none"),
                "geographicLimitations": limitations.get("geographic", []),
                "distributionLimitations": limitations.get("distribution", []),
                "voiceCloningStatus": rights.voice_cloning_status,
                "consentStatus": rights.consent_status,
                "effectiveDate": rights.effective_date,
                "expiresAt": rights.expiration_date,
                "evidenceReference": rights.evidence_reference,
                "humanVerificationStatus": rights.human_verification_status,
            }

        catalog_rights = next(
            value
            for value in client.app.state.casting.catalog.rights
            if value["rightsRecordId"] == stale_session_evidence["rightsRecordId"]
        )
        original_catalog_rights = dict(catalog_rights)
        original_catalog_fingerprint = client.app.state.casting.catalog.fingerprint
        assert request_fingerprint(catalog_rights) == original_rights_fingerprint
        assert catalog_rights["effectiveDate"] == selected_rights_material["effectiveDate"]
        assert catalog_rights["expiresAt"] == selected_rights_material["expiresAt"]

        temporal_checks: list[tuple[dict[str, Any], Any]] = []

        def rights_are_temporally_expired(
            rights_material: dict[str, Any],
            *,
            reference_time: Any = None,
        ) -> bool:
            temporal_checks.append((dict(rights_material), reference_time))
            return False

        monkeypatch.setattr(
            "cinematic_story_service.casting_repository.rights_record_is_current",
            rights_are_temporally_expired,
        )

        invalidated_workspace = _workspace(client, auth_headers, project_id)
        assert temporal_checks
        assert any(
            material == selected_rights_material for material, _reference_time in temporal_checks
        )
        assert all(reference_time is None for _material, reference_time in temporal_checks)
        assert invalidated_workspace["approvedCastSnapshot"] is None
        rights_prerequisite = next(
            value
            for value in invalidated_workspace["prerequisites"]
            if value["prerequisiteId"] == "voice_rights"
        )
        assert rights_prerequisite["current"] is False
        assert rights_prerequisite["statusCode"] == "RIGHTS_REQUIRED"
        assert all(
            not value["current"]
            for value in invalidated_workspace["prerequisites"]
            if value["prerequisiteId"].startswith("phase3a_")
        )
        assert all(
            value["sessionEvidence"] is None for value in invalidated_workspace["roles"]["items"]
        )
        invalidated_role = next(
            value
            for value in invalidated_workspace["roles"]["items"]
            if value["roleId"] == role["roleId"]
        )
        assert invalidated_role["latestSessionId"] is None

        rejected = client.post(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            json={
                "roleId": role["roleId"],
                "evidence": stale_session_evidence,
                "idempotencyKey": "phase3b-temporally-expired-rights-rejected",
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "AUDITION_PHASE3A_APPROVAL_REQUIRED"

        with client.app.state.database.session() as database_session:
            rights = database_session.get(
                VoiceRightsRecordRow,
                rights_row_id,
            )
            assert rights is not None
            assert rights.expiration_date == original_expiration_date
            assert rights.rights_fingerprint == original_rights_fingerprint
            sessions = list(
                database_session.scalars(
                    select(AuditionSessionRow).where(AuditionSessionRow.project_id == project_id)
                )
            )
            assert [(value.id, value.state) for value in sessions] == [(session_id, "invalidated")]
        current_catalog_rights = next(
            value
            for value in client.app.state.casting.catalog.rights
            if value["rightsRecordId"] == stale_session_evidence["rightsRecordId"]
        )
        assert current_catalog_rights == original_catalog_rights
        assert client.app.state.casting.catalog.fingerprint == original_catalog_fingerprint


def test_reapproved_replacement_evidence_survives_repeated_reconciliation(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-reconciliation-reapproval",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-reconciliation-reapproval",
        )
        role_ids = [
            value["roleId"]
            for value in _workspace(client, auth_headers, project_id)["roles"]["items"]
        ]
        assert role_ids

        _generate_role_replacements(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="initial",
        )
        initial = _approve_current_voice_evidence(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="initial",
        )
        initial_readiness_id = initial["decision"]["decisionId"]
        readiness_evidence = initial["review"]["evidence"]
        assert set(readiness_evidence) == {
            "approvedCastSnapshotFingerprint",
            "audioQualityFingerprint",
            "auditionClipId",
            "auditionClipRevision",
            "auditionSessionId",
            "castAssignmentFingerprint",
            "evidenceFingerprint",
            "gateId",
            "modelVerificationFingerprint",
            "projectId",
            "pronunciationDependencyFingerprint",
            "pronunciationDictionaryFingerprint",
            "rightsRecordFingerprint",
            "roleId",
            "runtimeProfileFingerprint",
        }
        assert readiness_evidence["projectId"] == project_id
        assert readiness_evidence["gateId"] == "voice_readiness_review"
        assert readiness_evidence["roleId"] is None
        assert readiness_evidence["auditionSessionId"] is None
        assert readiness_evidence["auditionClipId"] is None
        assert readiness_evidence["auditionClipRevision"] is None
        assert readiness_evidence["castAssignmentFingerprint"] is None
        assert (
            readiness_evidence["evidenceFingerprint"]
            == initial["voiceReadinessSnapshot"]["snapshotFingerprint"]
        )
        assert all(
            isinstance(readiness_evidence[key], str) and len(readiness_evidence[key]) == 64
            for key in (
                "approvedCastSnapshotFingerprint",
                "audioQualityFingerprint",
                "evidenceFingerprint",
                "modelVerificationFingerprint",
                "pronunciationDependencyFingerprint",
                "pronunciationDictionaryFingerprint",
                "rightsRecordFingerprint",
                "runtimeProfileFingerprint",
            )
        )

        packages = client.get(
            f"/api/v1/projects/{project_id}/speech/model-packages",
            headers=auth_headers,
            params={"limit": 200},
        )
        assert packages.status_code == 200, packages.text
        fixture = next(
            value
            for value in packages.json()["items"]
            if value["manifest"]["providerId"] == "deterministic-pcm-wav-fixture"
        )
        manifest = fixture["manifest"]
        installation = fixture["installation"]
        assert installation is not None
        reverified = client.post(
            (
                f"/api/v1/projects/{project_id}/speech/model-packages/"
                f"{manifest['modelPackageId']}/actions"
            ),
            headers=auth_headers,
            json={
                "modelPackageId": manifest["modelPackageId"],
                "expectedManifestFingerprint": manifest["manifestFingerprint"],
                "expectedInstallationRevision": installation["installationRevision"],
                "action": "verify",
                "reason": "Rotate exact fixture verification evidence for reconciliation.",
                "idempotencyKey": "phase3b-reconciliation-reverify",
            },
        )
        assert reverified.status_code == 200, reverified.text
        assert reverified.json()["installation"]["status"] == "active"

        invalidated = _workspace(client, auth_headers, project_id)
        assert invalidated["voiceReadinessSnapshot"] is None
        assert not any(
            value["gateId"] == "voice_readiness_review" for value in invalidated["reviews"]
        )
        readiness_history_response = client.get(
            f"/api/v1/projects/{project_id}/audition-review-decisions",
            headers=auth_headers,
            params={"gateId": "voice_readiness_review", "limit": 10},
        )
        assert readiness_history_response.status_code == 200, readiness_history_response.text
        readiness_history = readiness_history_response.json()["items"]
        assert readiness_history[0]["decision"] == "invalidated"
        assert readiness_history[0]["supersedesDecisionId"] == initial_readiness_id

        _generate_role_replacements(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="replacement",
        )
        replacement = _approve_current_voice_evidence(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="replacement",
        )
        replacement_readiness_id = replacement["decision"]["decisionId"]
        assert replacement_readiness_id != initial_readiness_id

        for _ in range(3):
            refreshed = _workspace(client, auth_headers, project_id)
            readiness_review = next(
                value
                for value in refreshed["reviews"]
                if value["gateId"] == "voice_readiness_review"
            )
            assert readiness_review["state"] == "approved"
            assert readiness_review["latestDecision"] is not None
            assert readiness_review["latestDecision"]["decisionId"] == replacement_readiness_id
            assert refreshed["voiceReadinessSnapshot"]["approvedRoleCount"] == len(role_ids)


def test_same_snapshot_phase3a_supersession_requires_new_pronunciation_approval(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-same-snapshot-phase3a",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-same-snapshot-phase3a",
        )
        role_id = _workspace(client, auth_headers, project_id)["roles"]["items"][0]["roleId"]
        initial_session, _script, _request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned initial pronunciation authority signal.",
            key="phase3b-same-snapshot-phase3a-initial",
        )
        initial_approval = _approve_current_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="pronunciation_review",
            key="phase3b-same-snapshot-phase3a-pronunciation",
        )
        initial_review = initial_approval["review"]
        initial_pronunciation_decision_id = initial_approval["decision"]["decisionId"]
        initial_snapshot = casting_run["approvedCastSnapshot"]
        assert initial_snapshot is not None

        gate_id = "narrator_casting_review"
        gate_review = next(
            value
            for value in _reviews(client, auth_headers, run=casting_run)
            if value["gateId"] == gate_id
        )
        prior_gate_decision = gate_review["latestDecision"]
        assert prior_gate_decision is not None
        reapproved = _post_decision(
            client,
            auth_headers,
            run=casting_run,
            gate_id=gate_id,
            payload=_decision_payload(
                run=casting_run,
                review=gate_review,
                key="phase3b-same-snapshot-phase3a-reapproval",
                supersedes_decision_id=prior_gate_decision["decisionId"],
            ),
        )
        assert reapproved.status_code == 200, reapproved.text
        reapproved_body = reapproved.json()
        replacement_gate_decision_id = reapproved_body["decision"]["decisionId"]
        assert replacement_gate_decision_id != prior_gate_decision["decisionId"]
        assert reapproved_body["snapshot"]["snapshotId"] == initial_snapshot["snapshotId"]
        assert (
            reapproved_body["snapshot"]["snapshotFingerprint"]
            == initial_snapshot["snapshotFingerprint"]
        )

        invalidated = _workspace(client, auth_headers, project_id)
        assert not any(
            value["gateId"] == "pronunciation_review" for value in invalidated["reviews"]
        )
        assert all(
            value["latestSessionId"] != initial_session["auditionSessionId"]
            for value in invalidated["roles"]["items"]
        )

        _replacement_session, _replacement_script, _replacement_request = (
            _create_session_and_script(
                client,
                auth_headers,
                project_id=project_id,
                role_id=role_id,
                text="Repository-owned replacement pronunciation authority signal.",
                key="phase3b-same-snapshot-phase3a-replacement",
            )
        )
        refreshed = _workspace(client, auth_headers, project_id)
        replacement_review = next(
            value for value in refreshed["reviews"] if value["gateId"] == "pronunciation_review"
        )
        assert replacement_review["state"] == "pending"
        assert replacement_review["reviewId"] != initial_review["reviewId"]
        assert (
            replacement_review["evidence"]["approvedCastSnapshotFingerprint"]
            == initial_review["evidence"]["approvedCastSnapshotFingerprint"]
        )
        assert (
            replacement_review["evidence"]["pronunciationDictionaryFingerprint"]
            == initial_review["evidence"]["pronunciationDictionaryFingerprint"]
        )
        assert (
            replacement_review["evidence"]["pronunciationDependencyFingerprint"]
            != initial_review["evidence"]["pronunciationDependencyFingerprint"]
        )
        assert (
            replacement_review["evidence"]["evidenceFingerprint"]
            != initial_review["evidence"]["evidenceFingerprint"]
        )
        assert replacement_review["latestDecision"] is not None
        assert (
            replacement_review["latestDecision"]["decisionId"] == initial_pronunciation_decision_id
        )
        assert replacement_review["latestDecision"]["reviewId"] == initial_review["reviewId"]

        with client.app.state.database.session() as database_session:
            initial_row = database_session.get(
                AuditionReviewRecordRow,
                initial_review["reviewId"],
            )
            replacement_row = database_session.get(
                AuditionReviewRecordRow,
                replacement_review["reviewId"],
            )
            assert initial_row is not None
            assert replacement_row is not None
            initial_required_ids = parse_json(initial_row.required_decision_ids_json, None)
            replacement_required_ids = parse_json(
                replacement_row.required_decision_ids_json,
                None,
            )
        assert isinstance(initial_required_ids, list)
        assert isinstance(replacement_required_ids, list)
        assert len(initial_required_ids) == len(CASTING_GATE_IDS)
        gate_index = CASTING_GATE_IDS.index(gate_id)
        assert initial_required_ids[gate_index] == prior_gate_decision["decisionId"]
        assert replacement_required_ids == [
            replacement_gate_decision_id if index == gate_index else decision_id
            for index, decision_id in enumerate(initial_required_ids)
        ]


def test_new_cast_snapshot_cannot_mix_historical_role_approvals(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-cross-snapshot-authority",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-cross-snapshot-authority",
        )
        initial_workspace = _workspace(client, auth_headers, project_id)
        initial_roles = initial_workspace["roles"]["items"]
        role_ids = [value["roleId"] for value in initial_roles]
        character_ids = [
            value["roleId"] for value in initial_roles if value["roleType"] == "character"
        ]
        narrator_ids = [
            value["roleId"] for value in initial_roles if value["roleType"] == "narrator"
        ]
        assert len(character_ids) >= 2
        assert narrator_ids

        _generate_role_replacements(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="cross-snapshot-s1",
        )
        _approve_current_voice_evidence(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="cross-snapshot-s1",
        )
        s1_workspace = _workspace(client, auth_headers, project_id)
        s1_snapshot = casting_run["approvedCastSnapshot"]
        assert s1_workspace["voiceReadinessSnapshot"] is not None
        s1_role_state = {
            value["roleId"]: {
                "clipId": value["latestClipId"],
                "sessionId": value["latestSessionId"],
            }
            for value in s1_workspace["roles"]["items"]
        }
        s1_review_ids = {
            value["reviewId"]
            for value in s1_workspace["reviews"]
            if value["gateId"] == "per_role_audition_review"
        }
        assert len(s1_review_ids) == len(role_ids)

        changed_role_id = character_ids[0]
        casting_role = next(
            value
            for value in _roles(client, auth_headers, run=casting_run)
            if value["roleId"] == changed_role_id
        )
        s2_run = _correct(
            client,
            auth_headers,
            run=casting_run,
            role=casting_role,
            operation="record_custom_rationale",
            key="phase3b-cross-snapshot-s2-rationale",
            corrected_value={
                "rationale": "Publish a new exact cast snapshot without assignment drift."
            },
        )
        s2_snapshot = s2_run["approvedCastSnapshot"]
        assert s2_snapshot["snapshotId"] != s1_snapshot["snapshotId"]
        assert s2_snapshot["snapshotFingerprint"] != s1_snapshot["snapshotFingerprint"]
        assert s2_snapshot["assignmentIds"] == s1_snapshot["assignmentIds"]

        before_s2_reapproval = _workspace(client, auth_headers, project_id)
        assert before_s2_reapproval["approvedCastSnapshot"] is None
        phase3a_prerequisites = [
            value
            for value in before_s2_reapproval["prerequisites"]
            if value["prerequisiteId"].startswith("phase3a_")
        ]
        assert len(phase3a_prerequisites) == len(CASTING_GATE_IDS)
        assert not any(value["current"] for value in phase3a_prerequisites)
        assert before_s2_reapproval["voiceReadinessSnapshot"] is None
        assert before_s2_reapproval["reviews"] == []
        assert all(
            value["latestSessionId"] is None
            and value["latestClipId"] is None
            and value["reviewState"] == "pending"
            for value in before_s2_reapproval["roles"]["items"]
        )

        for gate_id in CASTING_GATE_IDS:
            review = next(
                value
                for value in _reviews(client, auth_headers, run=s2_run)
                if value["gateId"] == gate_id
            )
            decided = _post_decision(
                client,
                auth_headers,
                run=s2_run,
                gate_id=gate_id,
                payload=_decision_payload(
                    run=s2_run,
                    review=review,
                    key=f"phase3b-cross-snapshot-s2-{gate_id}",
                ),
            )
            assert decided.status_code == 200, decided.text
            assert decided.json()["decision"]["decision"] == "approved"

        s2_session, _script, s2_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=changed_role_id,
            text="Repository-owned exact snapshot authority signal.",
            key="phase3b-cross-snapshot-s2-role",
        )
        _queued, terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=s2_session["auditionSessionId"],
            generation_request=s2_request,
        )
        assert terminal["state"] == "succeeded", terminal
        s2_clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=s2_session["auditionSessionId"],
        )[0]
        s2_approval = _approve_current_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="per_role_audition_review",
            role_id=changed_role_id,
            key="phase3b-cross-snapshot-s2-role-approval",
        )
        assert (
            s2_approval["review"]["evidence"]["approvedCastSnapshotFingerprint"]
            == s2_snapshot["snapshotFingerprint"]
        )

        workspace = _workspace(client, auth_headers, project_id)
        roles_by_id = {value["roleId"]: value for value in workspace["roles"]["items"]}
        changed = roles_by_id[changed_role_id]
        assert changed["latestSessionId"] == s2_session["auditionSessionId"]
        assert changed["latestClipId"] == s2_clip["auditionClipId"]
        assert changed["reviewState"] == "approved"

        untouched_ids = set(role_ids) - {changed_role_id}
        for role_id in untouched_ids:
            untouched = roles_by_id[role_id]
            assert untouched["latestSessionId"] is None
            assert untouched["latestClipId"] is None
            assert untouched["generationRequest"] is None
            assert untouched["reviewState"] == "pending"
        current_role_reviews = {
            review["reviewId"]
            for review in workspace["reviews"]
            if review["gateId"] == "per_role_audition_review"
        }
        assert current_role_reviews == {s2_approval["review"]["reviewId"]}
        assert current_role_reviews.isdisjoint(s1_review_ids)

        character_aggregate = next(
            review
            for review in workspace["reviews"]
            if review["gateId"] == "character_audition_review"
        )
        narrator_aggregate = next(
            review
            for review in workspace["reviews"]
            if review["gateId"] == "narrator_audition_review"
        )
        assert character_aggregate["state"] == "blocked"
        assert set(character_aggregate["blockerCodes"]) == {
            f"ROLE_AUDITION_APPROVAL_REQUIRED:{role_id}"
            for role_id in set(character_ids) - {changed_role_id}
        }
        assert (
            character_aggregate["evidence"]["approvedCastSnapshotFingerprint"]
            == s2_snapshot["snapshotFingerprint"]
        )
        assert narrator_aggregate["state"] == "blocked"
        assert set(narrator_aggregate["blockerCodes"]) == {
            f"ROLE_AUDITION_APPROVAL_REQUIRED:{role_id}" for role_id in narrator_ids
        }
        blocked_response = client.post(
            (
                f"/api/v1/projects/{project_id}/audition-reviews/"
                f"character_audition_review/{character_aggregate['reviewId']}/decisions"
            ),
            headers=auth_headers,
            json={
                "expectedReviewRevision": character_aggregate["revision"],
                "expectedEvidenceFingerprint": character_aggregate["evidence"][
                    "evidenceFingerprint"
                ],
                "decision": "approve",
                "rationale": "This blocked mixed-snapshot aggregate must fail closed.",
                "supersedesDecisionId": (
                    character_aggregate["latestDecision"]["decisionId"]
                    if character_aggregate["latestDecision"] is not None
                    else None
                ),
                "idempotencyKey": "phase3b-cross-snapshot-blocked-aggregate",
            },
        )
        assert blocked_response.status_code == 409, blocked_response.text
        assert blocked_response.json()["error"]["code"] == "AUDITION_REVIEW_BLOCKED"

        assert workspace["voiceReadinessSnapshot"] is None
        assert not any(
            review["gateId"] == "voice_readiness_review" for review in workspace["reviews"]
        )
        with client.app.state.database.immediate_session() as database_session:
            assert (
                client.app.state.auditions._ensure_voice_readiness(
                    database_session,
                    project_id,
                )
                is None
            )
            assert (
                list(
                    database_session.scalars(
                        select(VoiceReadinessSnapshotRow).where(
                            VoiceReadinessSnapshotRow.project_id == project_id,
                            VoiceReadinessSnapshotRow.cast_snapshot_id == s2_snapshot["snapshotId"],
                        )
                    )
                )
                == []
            )
            old_sessions = list(
                database_session.scalars(
                    select(AuditionSessionRow).where(
                        AuditionSessionRow.id.in_(
                            value["sessionId"] for value in s1_role_state.values()
                        )
                    )
                )
            )
            assert len(old_sessions) == len(role_ids)
            assert {value.state for value in old_sessions} == {"invalidated"}
            old_clip_ids = {value["clipId"] for value in s1_role_state.values()}
            invalidated_clip_ids = set(
                database_session.scalars(
                    select(AuditionEvidenceInvalidationRow.clip_id).where(
                        AuditionEvidenceInvalidationRow.project_id == project_id,
                        AuditionEvidenceInvalidationRow.source_kind == "cast_snapshot",
                        AuditionEvidenceInvalidationRow.source_record_id
                        == s1_snapshot["snapshotId"],
                    )
                )
            )
            assert old_clip_ids <= invalidated_clip_ids


def test_cache_clear_commit_failure_restores_exact_audio_and_metadata(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-cache-clear-compensation",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-cache-clear-compensation",
        )
        role_id = _workspace(client, auth_headers, project_id)["roles"]["items"][0]["roleId"]
        audition_session, _script, request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned cache rollback signal.",
            key="phase3b-cache-clear-compensation",
        )
        _queued, terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=request,
        )
        assert terminal["state"] == "succeeded", terminal
        clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )[0]
        artifact = clip["audioArtifact"]
        artifact_id = artifact["audioArtifactId"]
        audio_path = (
            settings.data_dir
            / "projects"
            / project_id
            / "auditions"
            / "audio"
            / f"{artifact_id}.wav"
        )
        original_audio = audio_path.read_bytes()
        assert hashlib.sha256(original_audio).hexdigest() == artifact["sha256"]
        project_response = client.get(
            f"/api/v1/projects/{project_id}",
            headers=auth_headers,
        )
        assert project_response.status_code == 200, project_response.text
        original_project_revision = project_response.json()["project"]["revision"]

        original_commit = Session.commit
        commit_failure_injected = False
        observed_tombstones: list[tuple[bool, int, bytes]] = []

        def fail_cache_clear_commit(database_session: Session) -> None:
            nonlocal commit_failure_injected
            pending_artifact = next(
                (
                    value
                    for value in database_session.dirty
                    if isinstance(value, AudioArtifactRow)
                    and value.id == artifact_id
                    and value.availability == "purged"
                ),
                None,
            )
            pending_cache = next(
                (
                    value
                    for value in database_session.dirty
                    if isinstance(value, AuditionCacheRecordRow)
                    and value.artifact_id == artifact_id
                    and value.state == "cleared"
                ),
                None,
            )
            if (
                not commit_failure_injected
                and pending_artifact is not None
                and pending_cache is not None
            ):
                tombstones = list(audio_path.parent.glob(f".{audio_path.name}.purge.*.tmp"))
                observed_tombstones.append(
                    (
                        audio_path.exists(),
                        len(tombstones),
                        tombstones[0].read_bytes() if len(tombstones) == 1 else b"",
                    )
                )
                commit_failure_injected = True
                raise RuntimeError("injected cache-clear commit failure")
            original_commit(database_session)

        monkeypatch.setattr(Session, "commit", fail_cache_clear_commit)
        with pytest.raises(RuntimeError, match="injected cache-clear commit failure"):
            client.post(
                f"/api/v1/projects/{project_id}/audition-cache/clear",
                headers=auth_headers,
                json={
                    "expectedProjectRevision": original_project_revision,
                    "reason": "Exercise rollback-safe private cache clearing.",
                    "idempotencyKey": "phase3b-cache-clear-commit-failure",
                },
            )

        assert commit_failure_injected
        assert observed_tombstones == [(False, 1, original_audio)]
        assert audio_path.read_bytes() == original_audio
        assert list(audio_path.parent.glob(f".{audio_path.name}.purge.*.tmp")) == []
        with client.app.state.database.session() as database_session:
            persisted_artifact = database_session.get(AudioArtifactRow, artifact_id)
            persisted_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            persisted_project = database_session.get(ProjectRow, project_id)
            persisted_idempotency = database_session.get(
                IdempotencyRow,
                {
                    "scope": f"phase3b-cache-clear:{project_id}",
                    "key": "phase3b-cache-clear-commit-failure",
                },
            )
            assert persisted_artifact is not None
            assert persisted_artifact.availability == "present"
            assert persisted_artifact.content_sha256 == artifact["sha256"]
            assert persisted_cache is not None
            assert persisted_cache.state == "verified"
            assert persisted_project is not None
            assert persisted_project.revision == original_project_revision
            assert persisted_idempotency is None

        rollback_crash_tombstone = audio_path.parent / f".{audio_path.name}.purge.{'a' * 32}.tmp"
        audio_path.rename(rollback_crash_tombstone)
        client.app.state.auditions._reconcile_audio_storage()
        assert audio_path.read_bytes() == original_audio
        assert not rollback_crash_tombstone.exists()
        with client.app.state.database.session() as database_session:
            restored_artifact = database_session.get(AudioArtifactRow, artifact_id)
            restored_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            assert restored_artifact is not None
            assert restored_artifact.availability == "present"
            assert restored_cache is not None
            assert restored_cache.state == "verified"

        committed_crash_tombstone = audio_path.parent / f".{audio_path.name}.purge.{'b' * 32}.tmp"
        audio_path.rename(committed_crash_tombstone)
        with client.app.state.database.immediate_session() as database_session:
            committed_artifact = database_session.get(AudioArtifactRow, artifact_id)
            committed_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            assert committed_artifact is not None
            assert committed_cache is not None
            committed_artifact.availability = "purged"
            committed_artifact.purged_at = utc_now()
            committed_cache.state = "cleared"
            committed_cache.purged_at = utc_now()
        client.app.state.auditions._reconcile_audio_storage()
        assert not audio_path.exists()
        assert not committed_crash_tombstone.exists()
        with client.app.state.database.session() as database_session:
            purged_artifact = database_session.get(AudioArtifactRow, artifact_id)
            cleared_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            assert purged_artifact is not None
            assert purged_artifact.availability == "purged"
            assert cleared_cache is not None
            assert cleared_cache.state == "cleared"
