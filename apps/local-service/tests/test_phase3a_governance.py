from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import cinematic_story_service.casting_repository as casting_repository_module
from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.casting import CASTING_GATE_IDS
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.models import (
    ApprovedCastSnapshotRow,
    CastAssignmentRow,
    CastingCandidateRow,
    CastingConflictRow,
    CastingCorrectionRow,
    CastingRunRow,
    ProductionRoleRow,
    VoiceProfileRow,
)
from cinematic_story_service.schemas import AppendCastingCorrectionRequest
from cinematic_story_service.util import (
    canonical_json,
    new_id,
    parse_json,
    request_fingerprint,
    utc_now,
)
from tests.test_phase2_api import create_phase2_run
from tests.test_phase3a_casting import (
    _approve_phase2,
    _candidates,
    _create_casting_run,
    _evidence,
    _roles,
)


def test_casting_requirement_correction_preserves_an_undeclared_locale() -> None:
    request = AppendCastingCorrectionRequest.model_validate(
        {
            "operation": "change_casting_requirement",
            "targetRoleId": "role-narrator",
            "expectedRoleRevision": 1,
            "expectedRunFingerprint": "a" * 64,
            "expectedCatalogFingerprint": "b" * 64,
            "expectedSnapshotFingerprint": "c" * 64,
            "expectedCorrectionSetFingerprint": "d" * 64,
            "previousEffectiveFingerprint": "e" * 64,
            "voiceProfileId": None,
            "correctedValue": {
                "requirement": {
                    "language": "en",
                    "locales": [],
                    "agePresentationRange": None,
                    "vocalPresentations": [],
                    "preferredTextures": [],
                    "speakingRateRange": None,
                    "requiredExpressiveRange": [],
                    "longFormRequired": False,
                }
            },
            "reason": "Declare the required language without inventing a locale.",
            "supersedesCorrectionId": None,
            "idempotencyKey": "language-without-locale",
        }
    )

    assert request.corrected_value == {
        "requirement": {
            "language": "en",
            "locales": [],
            "agePresentationRange": None,
            "vocalPresentations": [],
            "preferredTextures": [],
            "speakingRateRange": None,
            "requiredExpressiveRange": [],
            "longFormRequired": False,
        }
    }


@pytest.mark.parametrize(
    ("operation", "voice_profile_id"),
    [
        ("clear_assignment", None),
        ("lock_assignment", None),
        ("unlock_assignment", None),
        ("acknowledge_restricted_rights", "synthetic-narrator-02"),
        ("reject_candidate", "synthetic-narrator-01"),
    ],
)
def test_stale_sensitive_corrections_require_operation_evidence(
    operation: str,
    voice_profile_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="requires correctedValue"):
        AppendCastingCorrectionRequest.model_validate(
            {
                "operation": operation,
                "targetRoleId": "role-narrator",
                "expectedRoleRevision": 1,
                "expectedRunFingerprint": "a" * 64,
                "expectedCatalogFingerprint": "b" * 64,
                "expectedSnapshotFingerprint": "c" * 64,
                "expectedCorrectionSetFingerprint": "d" * 64,
                "previousEffectiveFingerprint": "e" * 64,
                "voiceProfileId": voice_profile_id,
                "correctedValue": None,
                "reason": "Exercise the stale-sensitive discriminator.",
                "supersedesCorrectionId": None,
                "idempotencyKey": f"missing-{operation}-evidence",
            }
        )


def _prepare_casting(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    key: str,
) -> tuple[str, dict[str, Any]]:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key=key,
    )
    project_id = imported["project"]["projectId"]
    analysis_run, decisions = _approve_phase2(
        client,
        auth_headers,
        project_id=project_id,
        run_id=created["run"]["runId"],
    )
    run = _create_casting_run(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=analysis_run,
        decisions=decisions,
        idempotency_key=f"{key}-casting",
    )
    return project_id, run


def _role(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    role_id: str,
) -> dict[str, Any]:
    return next(
        value for value in _roles(client, auth_headers, run=run) if value["roleId"] == role_id
    )


def _candidate(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    role: dict[str, Any],
    voice_profile_id: str,
) -> dict[str, Any]:
    return next(
        value
        for value in _candidates(
            client,
            auth_headers,
            run=run,
            role=role,
        )
        if value["voiceProfileId"] == voice_profile_id
    )


def _correction_payload(
    *,
    run: dict[str, Any],
    role: dict[str, Any],
    operation: str,
    key: str,
    voice_profile_id: str | None = None,
    corrected_value: dict[str, Any] | None = None,
    reason: str | None = None,
    supersedes_correction_id: str | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "targetRoleId": role["roleId"],
        "expectedRoleRevision": role["revision"],
        "expectedRunFingerprint": run["outputFingerprint"] or run["inputFingerprint"],
        "expectedCatalogFingerprint": run["catalogFingerprint"],
        "expectedSnapshotFingerprint": run["prerequisites"]["analysisSnapshotFingerprint"],
        "expectedCorrectionSetFingerprint": run["effectiveCorrectionSetFingerprint"],
        "previousEffectiveFingerprint": role["effectiveFingerprint"],
        "voiceProfileId": voice_profile_id,
        "correctedValue": corrected_value,
        "reason": reason or f"Governed test correction: {operation}.",
        "supersedesCorrectionId": supersedes_correction_id,
        "idempotencyKey": key,
    }


def _post_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    payload: dict[str, Any],
) -> Any:
    return client.post(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/corrections",
        headers=auth_headers,
        json=payload,
    )


def _apply_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    role_id: str,
    operation: str,
    key: str,
    voice_profile_id: str | None = None,
    corrected_value: dict[str, Any] | None = None,
    reason: str | None = None,
    supersedes_correction_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    current_role = _role(
        client,
        auth_headers,
        run=run,
        role_id=role_id,
    )
    payload = _correction_payload(
        run=run,
        role=current_role,
        operation=operation,
        key=key,
        voice_profile_id=voice_profile_id,
        corrected_value=corrected_value,
        reason=reason,
        supersedes_correction_id=supersedes_correction_id,
    )
    response = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=payload,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["run"], body, payload


def _reviews(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
) -> list[dict[str, Any]]:
    snapshot = run["approvedCastSnapshot"]
    assert snapshot is not None
    response = client.get(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/reviews",
        headers=auth_headers,
        params={
            **_evidence(run),
            "expectedApprovedCastSnapshotId": snapshot["snapshotId"],
            "expectedApprovedCastSnapshotRevision": snapshot["revision"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _decision_payload(
    *,
    run: dict[str, Any],
    review: dict[str, Any],
    key: str,
    decision: str = "approve",
    rationale: str | None = None,
    supersedes_decision_id: str | None = None,
) -> dict[str, Any]:
    snapshot = run["approvedCastSnapshot"]
    assert snapshot is not None
    return {
        "decision": decision,
        "expectedRevision": review["revision"],
        "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
        "expectedRunFingerprint": run["outputFingerprint"] or run["inputFingerprint"],
        "expectedApprovedCastSnapshotId": snapshot["snapshotId"],
        "expectedApprovedCastSnapshotRevision": snapshot["revision"],
        "warningAcknowledgementIds": review["openWarningIds"],
        "rationale": rationale or f"Governed test decision for {review['gateId']}.",
        "supersedesDecisionId": supersedes_decision_id,
        "idempotencyKey": key,
    }


def _post_decision(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    gate_id: str,
    payload: dict[str, Any],
) -> Any:
    return client.post(
        f"/api/v1/projects/{run['projectId']}/casting-runs/"
        f"{run['castingRunId']}/reviews/{gate_id}/decisions",
        headers=auth_headers,
        json=payload,
    )


def _blocking_reason_codes(response: Any) -> set[str]:
    details = response.json()["error"].get("details", {})
    return {value for value in details.get("blockingReasonCodes", "").split(",") if value}


def _collect_pages(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    url: str,
    params: dict[str, Any],
    limit: int,
    identity_key: str,
) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    expected_total: int | None = None
    while True:
        page_params = {**params, "limit": limit}
        if cursor is not None:
            page_params["cursor"] = cursor
        response = client.get(
            url,
            headers=auth_headers,
            params=page_params,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pageSize"] == len(body["items"])
        assert body["pageSize"] <= limit
        if expected_total is None:
            expected_total = body["total"]
        assert body["total"] == expected_total
        items.extend(body["items"])
        cursor = body.get("nextCursor")
        if cursor is None:
            break
    assert expected_total is not None
    assert len(items) == expected_total
    assert len({value[identity_key] for value in items}) == expected_total
    return items, expected_total


def test_rights_gates_complete_gate_idempotency_and_restart_persistence(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    decision_ids: dict[str, str] = {}
    correction_ids: set[str] = set()
    with TestClient(create_app(settings)) as client:
        project_id, run = _prepare_casting(
            client,
            auth_headers,
            key="governance-rights",
        )
        initial_roles = _roles(client, auth_headers, run=run)
        narrator = next(value for value in initial_roles if value["roleType"] == "primary_narrator")
        if narrator["performanceRequirements"]["language"] == "und":
            run, language_change, _ = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=narrator["roleId"],
                operation="change_casting_requirement",
                key="set-rights-narrator-language",
                corrected_value={
                    "requirement": {
                        **narrator["performanceRequirements"],
                        "language": "en",
                        "locales": ["en-US"],
                    }
                },
            )
            correction_ids.add(language_change["correction"]["correctionId"])
            narrator = _role(
                client,
                auth_headers,
                run=run,
                role_id=narrator["roleId"],
            )
        _candidate(
            client,
            auth_headers,
            run=run,
            role=narrator,
            voice_profile_id="synthetic-narrator-02",
        )
        run, restricted_selection, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=narrator["roleId"],
            operation="select_voice",
            key="select-restricted-narrator",
            voice_profile_id="synthetic-narrator-02",
        )
        correction_ids.add(restricted_selection["correction"]["correctionId"])

        narrator_review = next(
            value
            for value in _reviews(client, auth_headers, run=run)
            if value["gateId"] == "narrator_casting_review"
        )
        before_acknowledgement = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="narrator_casting_review",
            payload=_decision_payload(
                run=run,
                review=narrator_review,
                key="narrator-before-rights-ack",
            ),
        )
        assert before_acknowledgement.status_code == 409
        assert before_acknowledgement.json()["error"]["code"] == "CASTING_REVIEW_NOT_ELIGIBLE"
        assert any(
            value.startswith(f"restricted-rights:{narrator['roleId']}:")
            for value in _blocking_reason_codes(before_acknowledgement)
        )

        run, acknowledgement, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=narrator["roleId"],
            operation="acknowledge_restricted_rights",
            key="ack-restricted-narrator",
            voice_profile_id="synthetic-narrator-02",
            corrected_value={
                "rightsRecordId": restricted_selection["assignment"]["rightsRecordId"],
                "rightsRecordRevision": (
                    restricted_selection["assignment"]["rightsRecordRevision"]
                ),
            },
        )
        acknowledged_correction = acknowledgement["correction"]
        correction_ids.add(acknowledged_correction["correctionId"])
        assert acknowledged_correction["category"] == ("acknowledge_restricted_rights")
        assert acknowledged_correction["correctedValue"]["rightsRecordId"]
        assert acknowledged_correction["correctedValue"]["rightsRecordRevision"] >= 1

        character_voice_ids = (
            "synthetic-character-01",
            "synthetic-character-02",
            "synthetic-character-03",
            "synthetic-character-04",
        )
        named_index = 0
        for original_role in initial_roles:
            if original_role["roleId"] == narrator["roleId"]:
                continue
            if original_role["roleType"] == "secondary_narrator":
                current_role = _role(
                    client,
                    auth_headers,
                    run=run,
                    role_id=original_role["roleId"],
                )
                if current_role["performanceRequirements"]["language"] == "und":
                    run, language_change, _ = _apply_correction(
                        client,
                        auth_headers,
                        run=run,
                        role_id=current_role["roleId"],
                        operation="change_casting_requirement",
                        key=(f"set-secondary-narrator-language-{current_role['roleId']}"),
                        corrected_value={
                            "requirement": {
                                **current_role["performanceRequirements"],
                                "language": "en",
                                "locales": ["en-US"],
                            }
                        },
                    )
                    correction_ids.add(language_change["correction"]["correctionId"])
                    current_role = _role(
                        client,
                        auth_headers,
                        run=run,
                        role_id=original_role["roleId"],
                    )
                _candidate(
                    client,
                    auth_headers,
                    run=run,
                    role=current_role,
                    voice_profile_id="synthetic-narrator-01",
                )
                operation = "select_voice"
                voice_id = "synthetic-narrator-01"
            elif original_role["roleType"] == "named_character":
                current_role = _role(
                    client,
                    auth_headers,
                    run=run,
                    role_id=original_role["roleId"],
                )
                if current_role["performanceRequirements"]["language"] == "und":
                    run, language_change, _ = _apply_correction(
                        client,
                        auth_headers,
                        run=run,
                        role_id=current_role["roleId"],
                        operation="change_casting_requirement",
                        key=(f"set-character-language-{current_role['roleId']}"),
                        corrected_value={
                            "requirement": {
                                **current_role["performanceRequirements"],
                                "language": "en",
                                "locales": ["en-US"],
                            }
                        },
                    )
                    correction_ids.add(language_change["correction"]["correctionId"])
                    current_role = _role(
                        client,
                        auth_headers,
                        run=run,
                        role_id=original_role["roleId"],
                    )
                voice_id = character_voice_ids[named_index % len(character_voice_ids)]
                named_index += 1
                _candidate(
                    client,
                    auth_headers,
                    run=run,
                    role=current_role,
                    voice_profile_id=voice_id,
                )
                operation = "select_voice"
            else:
                operation = "mark_intentionally_uncast"
                voice_id = None
            run, correction, _ = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=original_role["roleId"],
                operation=operation,
                key=f"assign-role-{original_role['roleId']}",
                voice_profile_id=voice_id,
            )
            correction_ids.add(correction["correction"]["correctionId"])

        named_role = next(
            value for value in initial_roles if value["roleType"] == "named_character"
        )
        for rights_state, voice_id in (
            ("prohibited", "synthetic-character-07"),
            ("unknown", "synthetic-character-08"),
        ):
            current_role = _role(
                client,
                auth_headers,
                run=run,
                role_id=named_role["roleId"],
            )
            _candidate(
                client,
                auth_headers,
                run=run,
                role=current_role,
                voice_profile_id=voice_id,
            )
            run, selection, _ = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=named_role["roleId"],
                operation="select_voice",
                key=f"select-{rights_state}-rights",
                voice_profile_id=voice_id,
            )
            correction_ids.add(selection["correction"]["correctionId"])
            complete_review = next(
                value
                for value in _reviews(client, auth_headers, run=run)
                if value["gateId"] == "complete_cast_review"
            )
            rejected = _post_decision(
                client,
                auth_headers,
                run=run,
                gate_id="complete_cast_review",
                payload=_decision_payload(
                    run=run,
                    review=complete_review,
                    key=f"complete-with-{rights_state}-rights",
                ),
            )
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == "CASTING_REVIEW_NOT_ELIGIBLE"
            blockers = _blocking_reason_codes(rejected)
            assert f"ineligible-rights:{named_role['roleId']}" in blockers
            assert f"ineligible-candidate:{named_role['roleId']}:{voice_id}" in blockers

        run, verified_selection, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=named_role["roleId"],
            operation="select_voice",
            key="restore-verified-character",
            voice_profile_id="synthetic-character-01",
        )
        correction_ids.add(verified_selection["correction"]["correctionId"])

        complete_review = next(
            value
            for value in _reviews(client, auth_headers, run=run)
            if value["gateId"] == "complete_cast_review"
        )
        before_upstream = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="complete_cast_review",
            payload=_decision_payload(
                run=run,
                review=complete_review,
                key="complete-before-upstream",
            ),
        )
        assert before_upstream.status_code == 409
        assert before_upstream.json()["error"]["code"] == "CASTING_REVIEW_NOT_ELIGIBLE"
        upstream_blockers = _blocking_reason_codes(before_upstream)
        assert {
            "upstream-gate:narrator_casting_review",
            "upstream-gate:character_casting_review",
        } <= upstream_blockers

        for gate_id in CASTING_GATE_IDS[:2]:
            review = next(
                value
                for value in _reviews(client, auth_headers, run=run)
                if value["gateId"] == gate_id
            )
            response = _post_decision(
                client,
                auth_headers,
                run=run,
                gate_id=gate_id,
                payload=_decision_payload(
                    run=run,
                    review=review,
                    key=f"approve-{gate_id}",
                ),
            )
            assert response.status_code == 200, response.text
            assert response.json()["decision"]["decision"] == "approved"
            decision_ids[gate_id] = response.json()["decision"]["decisionId"]

        refreshed_run = client.get(
            f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}",
            headers=auth_headers,
        )
        assert refreshed_run.status_code == 200, refreshed_run.text
        run = refreshed_run.json()["run"]
        assert run["approvedCastSnapshot"]["reviewEligible"] is True

        complete_review = next(
            value
            for value in _reviews(client, auth_headers, run=run)
            if value["gateId"] == "complete_cast_review"
        )
        complete_payload = _decision_payload(
            run=run,
            review=complete_review,
            key="approve-complete-cast",
        )
        completed = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="complete_cast_review",
            payload=complete_payload,
        )
        assert completed.status_code == 200, completed.text
        complete_decision = completed.json()["decision"]
        assert complete_decision["decision"] == "approved"
        decision_ids["complete_cast_review"] = complete_decision["decisionId"]

        exact_replay = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="complete_cast_review",
            payload=complete_payload,
        )
        assert exact_replay.status_code == 200, exact_replay.text
        assert exact_replay.json()["decision"]["decisionId"] == complete_decision["decisionId"]
        conflicting_replay = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="complete_cast_review",
            payload={
                **complete_payload,
                "rationale": "A changed rationale must not reuse the key.",
            },
        )
        assert conflicting_replay.status_code == 409
        assert conflicting_replay.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        stale_run_replay = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="complete_cast_review",
            payload={
                **complete_payload,
                "expectedRunFingerprint": "f" * 64,
            },
        )
        assert stale_run_replay.status_code == 409
        assert stale_run_replay.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        narrator = _role(
            client,
            auth_headers,
            run=run,
            role_id=narrator["roleId"],
        )
        run, later_rationale, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=narrator["roleId"],
            operation="record_custom_rationale",
            key="later-snapshot-after-complete-decision",
            corrected_value={
                "rationale": "Record a later immutable snapshot for replay coverage.",
            },
        )
        correction_ids.add(later_rationale["correction"]["correctionId"])
        historical_replay = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="complete_cast_review",
            payload=complete_payload,
        )
        assert historical_replay.status_code == 200, historical_replay.text
        historical_body = historical_replay.json()
        assert historical_body["decision"]["decisionId"] == (
            complete_decision["decisionId"]
        )
        assert historical_body["review"]["state"] == "approved"
        assert historical_body["review"]["latestDecision"]["decisionId"] == (
            complete_decision["decisionId"]
        )
        assert historical_body["snapshot"]["snapshotId"] == (
            complete_payload["expectedApprovedCastSnapshotId"]
        )
        assert historical_body["run"]["outputFingerprint"] == (
            complete_payload["expectedRunFingerprint"]
        )

        for gate_id in CASTING_GATE_IDS[:2]:
            review = next(
                value
                for value in _reviews(client, auth_headers, run=run)
                if value["gateId"] == gate_id
            )
            assert review["state"] == "pending"
            assert review["latestDecision"] is None
            reapproval_payload = _decision_payload(
                run=run,
                review=review,
                key=f"reapprove-{gate_id}",
            )
            response = _post_decision(
                client,
                auth_headers,
                run=run,
                gate_id=gate_id,
                payload=reapproval_payload,
            )
            assert response.status_code == 200, response.text
            assert response.json()["decision"]["supersedesDecisionId"] == (
                decision_ids[gate_id]
            )
            exact_reapproval = _post_decision(
                client,
                auth_headers,
                run=run,
                gate_id=gate_id,
                payload=reapproval_payload,
            )
            assert exact_reapproval.status_code == 200, exact_reapproval.text
            assert exact_reapproval.json()["decision"]["decisionId"] == (
                response.json()["decision"]["decisionId"]
            )
            decision_ids[gate_id] = response.json()["decision"]["decisionId"]
        complete_review = next(
            value
            for value in _reviews(client, auth_headers, run=run)
            if value["gateId"] == "complete_cast_review"
        )
        recompleted = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id="complete_cast_review",
            payload=_decision_payload(
                run=run,
                review=complete_review,
                key="reapprove-complete-cast",
            ),
        )
        assert recompleted.status_code == 200, recompleted.text
        decision_ids["complete_cast_review"] = recompleted.json()["decision"][
            "decisionId"
        ]
        saved_snapshot_id = run["approvedCastSnapshot"]["snapshotId"]
        saved_run_id = run["castingRunId"]

    with TestClient(create_app(settings)) as restarted:
        detail = restarted.get(
            f"/api/v1/projects/{project_id}",
            headers=auth_headers,
        )
        assert detail.status_code == 200, detail.text
        casting = detail.json()["voiceCasting"]
        restored_run = casting["currentRun"]
        assert restored_run["castingRunId"] == saved_run_id
        assert restored_run["approvedCastSnapshot"]["snapshotId"] == saved_snapshot_id
        restored_decisions = {
            value["gateId"]: value["latestDecision"]["decisionId"]
            for value in casting["gateReviews"]
        }
        assert restored_decisions == decision_ids
        assert all(value["state"] == "approved" for value in casting["gateReviews"])

        corrections = restarted.get(
            f"/api/v1/projects/{project_id}/casting-runs/{saved_run_id}/corrections",
            headers=auth_headers,
            params={**_evidence(restored_run), "limit": 200},
        )
        assert corrections.status_code == 200, corrections.text
        restored_correction_ids = {value["correctionId"] for value in corrections.json()["items"]}
        assert correction_ids <= restored_correction_ids
        assert any(
            value["category"] == "acknowledge_restricted_rights"
            and value["correctedValue"]["rightsRecordId"]
            for value in corrections.json()["items"]
        )


def test_stale_sensitive_correction_discriminators_fail_closed(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-stale-discriminators",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    rejected_candidate = _candidate(
        client,
        auth_headers,
        run=run,
        role=narrator,
        voice_profile_id="synthetic-narrator-01",
    )
    restricted_candidate = _candidate(
        client,
        auth_headers,
        run=run,
        role=narrator,
        voice_profile_id="synthetic-narrator-02",
    )

    wrong_rejection = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=_correction_payload(
            run=run,
            role=narrator,
            operation="reject_candidate",
            key="reject-with-wrong-candidate-id",
            voice_profile_id="synthetic-narrator-01",
            corrected_value={"candidateId": restricted_candidate["candidateId"]},
        ),
    )
    assert wrong_rejection.status_code == 409
    assert wrong_rejection.json()["error"]["code"] == "CASTING_CANDIDATE_STALE"

    run, rejection, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="reject_candidate",
        key="reject-with-exact-candidate-id",
        voice_profile_id="synthetic-narrator-01",
        corrected_value={"candidateId": rejected_candidate["candidateId"]},
    )
    assert rejection["correction"]["correctedValue"] == {
        "candidateId": rejected_candidate["candidateId"]
    }

    narrator = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    run, selection, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="select_voice",
        key="select-restricted-for-stale-discriminators",
        voice_profile_id="synthetic-narrator-02",
    )
    selected_assignment = selection["assignment"]
    rights_value = {
        "rightsRecordId": selected_assignment["rightsRecordId"],
        "rightsRecordRevision": selected_assignment["rightsRecordRevision"],
    }
    narrator = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    for key, wrong_rights in (
        (
            "ack-with-wrong-rights-id",
            {
                **rights_value,
                "rightsRecordId": "rights-not-the-selected-voice",
            },
        ),
        (
            "ack-with-wrong-rights-revision",
            {
                **rights_value,
                "rightsRecordRevision": rights_value["rightsRecordRevision"] + 1,
            },
        ),
    ):
        wrong_acknowledgement = _post_correction(
            client,
            auth_headers,
            run=run,
            payload=_correction_payload(
                run=run,
                role=narrator,
                operation="acknowledge_restricted_rights",
                key=key,
                voice_profile_id="synthetic-narrator-02",
                corrected_value=wrong_rights,
            ),
        )
        assert wrong_acknowledgement.status_code == 409
        assert wrong_acknowledgement.json()["error"]["code"] == "VOICE_RIGHTS_STALE"

    run, acknowledgement, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="acknowledge_restricted_rights",
        key="ack-with-exact-rights-evidence",
        voice_profile_id="synthetic-narrator-02",
        corrected_value=rights_value,
    )
    assert acknowledgement["correction"]["correctedValue"] == rights_value

    narrator = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    wrong_lock = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=_correction_payload(
            run=run,
            role=narrator,
            operation="lock_assignment",
            key="lock-with-wrong-assignment-id",
            corrected_value={"assignmentId": "assignment-not-current"},
        ),
    )
    assert wrong_lock.status_code == 409
    assert wrong_lock.json()["error"]["code"] == "CASTING_ASSIGNMENT_STALE"

    run, locked, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="lock_assignment",
        key="lock-with-exact-assignment-id",
        corrected_value={
            "assignmentId": selected_assignment["assignmentId"],
        },
    )
    locked_assignment = locked["assignment"]
    narrator = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    wrong_unlock = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=_correction_payload(
            run=run,
            role=narrator,
            operation="unlock_assignment",
            key="unlock-with-wrong-assignment-id",
            corrected_value={"lockedAssignmentId": "assignment-not-locked"},
        ),
    )
    assert wrong_unlock.status_code == 409
    assert wrong_unlock.json()["error"]["code"] == "CASTING_ASSIGNMENT_STALE"

    run, unlocked, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="unlock_assignment",
        key="unlock-with-exact-assignment-id",
        corrected_value={
            "lockedAssignmentId": locked_assignment["assignmentId"],
        },
    )
    unlocked_assignment = unlocked["assignment"]
    narrator = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    wrong_clear = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=_correction_payload(
            run=run,
            role=narrator,
            operation="clear_assignment",
            key="clear-with-wrong-assignment-id",
            corrected_value={"expectedAssignmentId": "assignment-not-current"},
        ),
    )
    assert wrong_clear.status_code == 409
    assert wrong_clear.json()["error"]["code"] == "CASTING_ASSIGNMENT_STALE"

    run, cleared, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="clear_assignment",
        key="clear-with-exact-assignment-id",
        corrected_value={
            "expectedAssignmentId": unlocked_assignment["assignmentId"],
        },
    )
    assert cleared["assignment"] is None
    assert cleared["correction"]["correctedValue"] == {
        "expectedAssignmentId": unlocked_assignment["assignmentId"]
    }
    assert run["approvedCastSnapshot"]["counts"]["assignments"] == 0


def test_candidate_rejection_assignment_lifecycle_and_correction_idempotency(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-lifecycle",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    candidate = _candidate(
        client,
        auth_headers,
        run=run,
        role=narrator,
        voice_profile_id="synthetic-narrator-01",
    )
    run, rejection, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="reject_candidate",
        key="reject-narrator-candidate",
        voice_profile_id="synthetic-narrator-01",
        corrected_value={"candidateId": candidate["candidateId"]},
    )
    rejection_id = rejection["correction"]["correctionId"]
    assert rejection["correction"]["correctedValue"] == {"candidateId": candidate["candidateId"]}
    refreshed_role = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    rejected_candidate = _candidate(
        client,
        auth_headers,
        run=run,
        role=refreshed_role,
        voice_profile_id="synthetic-narrator-01",
    )
    assert rejected_candidate["rejectedByCorrectionId"] == rejection_id
    assert (
        rejected_candidate["baseEvidenceFingerprint"]
        == candidate["baseEvidenceFingerprint"]
    )
    assert rejected_candidate["outputFingerprint"] != candidate["outputFingerprint"]
    rejected_projection = dict(rejected_candidate)
    rejected_output_fingerprint = rejected_projection.pop("outputFingerprint")
    assert rejected_output_fingerprint == request_fingerprint(rejected_projection)

    cross_domain_supersession = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=_correction_payload(
            run=run,
            role=refreshed_role,
            operation="change_role_label",
            key="cross-domain-rejection-supersession",
            corrected_value={"effectiveDisplayLabel": "Governed narrator"},
            supersedes_correction_id=rejection_id,
        ),
    )
    assert cross_domain_supersession.status_code == 409
    assert cross_domain_supersession.json()["error"]["code"] == ("CASTING_SUPERSESSION_INVALID")

    run, label_change, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="change_role_label",
        key="independent-label-correction",
        corrected_value={"effectiveDisplayLabel": "Governed narrator"},
    )
    label_correction_id = label_change["correction"]["correctionId"]
    assert label_change["correction"]["supersedesCorrectionId"] is None
    refreshed_role = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    still_rejected = _candidate(
        client,
        auth_headers,
        run=run,
        role=refreshed_role,
        voice_profile_id="synthetic-narrator-01",
    )
    assert still_rejected["rejectedByCorrectionId"] == rejection_id
    assert (
        still_rejected["baseEvidenceFingerprint"]
        == rejected_candidate["baseEvidenceFingerprint"]
    )

    blocked_payload = _correction_payload(
        run=run,
        role=refreshed_role,
        operation="select_voice",
        key="select-rejected-without-supersession",
        voice_profile_id="synthetic-narrator-01",
    )
    blocked = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=blocked_payload,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CASTING_CANDIDATE_REJECTED"

    selection_payload = {
        **blocked_payload,
        "idempotencyKey": "supersede-rejected-candidate",
        "supersedesCorrectionId": rejection_id,
    }
    selected = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=selection_payload,
    )
    assert selected.status_code == 200, selected.text
    selection = selected.json()
    assert selection["correction"]["supersedesCorrectionId"] == rejection_id
    selection_id = selection["correction"]["correctionId"]
    selection_assignment_id = selection["assignment"]["assignmentId"]
    assert selection["run"]["summary"]["assignments"] == 1
    assert selection["run"]["approvedCastSnapshot"]["counts"]["assignments"] == 1

    exact_replay = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=selection_payload,
    )
    assert exact_replay.status_code == 200, exact_replay.text
    assert exact_replay.json()["correction"]["correctionId"] == selection_id
    assert exact_replay.json()["assignment"]["assignmentId"] == selection_assignment_id
    conflicting_replay = _post_correction(
        client,
        auth_headers,
        run=run,
        payload={
            **selection_payload,
            "reason": "A changed selection reason must conflict.",
        },
    )
    assert conflicting_replay.status_code == 409
    assert conflicting_replay.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    stale_precondition_replay = _post_correction(
        client,
        auth_headers,
        run=run,
        payload={
            **selection_payload,
            "expectedRoleRevision": selection_payload["expectedRoleRevision"] + 1,
        },
    )
    assert stale_precondition_replay.status_code == 409
    assert stale_precondition_replay.json()["error"]["code"] == ("IDEMPOTENCY_CONFLICT")

    run = selection["run"]
    run, locked, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="lock_assignment",
        key="lock-selected-narrator",
        corrected_value={"assignmentId": selection_assignment_id},
    )
    lock_assignment = locked["assignment"]
    lock_correction_id = locked["correction"]["correctionId"]
    assert locked["correction"]["supersedesCorrectionId"] == selection_id
    assert lock_assignment["authority"] == "human_locked"
    assert lock_assignment["supersedesAssignmentId"] == selection_assignment_id
    assert run["summary"]["assignments"] == 1
    assert run["approvedCastSnapshot"]["counts"]["assignments"] == 1

    locked_role = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    wrong_unlock = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=_correction_payload(
            run=run,
            role=locked_role,
            operation="unlock_assignment",
            key="unlock-with-wrong-semantic-leaf",
            corrected_value={
                "lockedAssignmentId": lock_assignment["assignmentId"],
            },
            supersedes_correction_id=label_correction_id,
        ),
    )
    assert wrong_unlock.status_code == 409
    assert wrong_unlock.json()["error"]["code"] == "CASTING_SUPERSESSION_INVALID"

    run, unlocked, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="unlock_assignment",
        key="unlock-selected-narrator",
        corrected_value={
            "lockedAssignmentId": lock_assignment["assignmentId"],
        },
    )
    unlock_assignment = unlocked["assignment"]
    unlock_correction_id = unlocked["correction"]["correctionId"]
    assert unlocked["correction"]["supersedesCorrectionId"] == (lock_correction_id)
    assert unlock_assignment["authority"] == "human_selection"
    assert unlock_assignment["supersedesAssignmentId"] == lock_assignment["assignmentId"]
    assert run["summary"]["assignments"] == 1
    assert run["approvedCastSnapshot"]["counts"]["assignments"] == 1

    run, cleared, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="clear_assignment",
        key="clear-selected-narrator",
        corrected_value={
            "expectedAssignmentId": unlock_assignment["assignmentId"],
        },
    )
    assert cleared["assignment"] is None
    assert cleared["correction"]["correctedValue"] == {
        "expectedAssignmentId": unlock_assignment["assignmentId"]
    }
    assert cleared["correction"]["supersedesCorrectionId"] == (unlock_correction_id)
    snapshot = run["approvedCastSnapshot"]
    assert unlock_assignment["assignmentId"] not in snapshot["assignmentIds"]
    assert snapshot["counts"]["assignments"] == 0
    assert run["summary"]["assignments"] == 0

    assignments = client.get(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/assignments",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert assignments.status_code == 200, assignments.text
    role_assignments = [
        value for value in assignments.json()["items"] if value["roleId"] == narrator["roleId"]
    ]
    assert len(role_assignments) == 3
    assert all(
        value["authority"] in {"human_selection", "human_locked"} for value in role_assignments
    )
    assert [value["assignmentId"] for value in role_assignments] == [
        selection_assignment_id,
        lock_assignment["assignmentId"],
        unlock_assignment["assignmentId"],
    ]
    assert [value["revision"] for value in role_assignments] == [1, 2, 3]
    assert all(value["effective"] is False for value in role_assignments)

    corrections = client.get(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/corrections",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert corrections.status_code == 200, corrections.text
    lifecycle = [
        value
        for value in corrections.json()["items"]
        if value["targetRoleId"] == narrator["roleId"]
    ]
    assert [value["category"] for value in lifecycle] == [
        "reject_candidate",
        "change_role_label",
        "select_voice",
        "lock_assignment",
        "unlock_assignment",
        "clear_assignment",
    ]
    assert lifecycle[1]["correctionId"] == label_correction_id
    assert all(
        value["immutable"] is True and value["lockedAgainstAutomation"] is True
        for value in lifecycle
    )


def test_assignment_replay_uses_durable_correction_link_when_timestamps_collide(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-assignment-correction-link",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    fixed_timestamp = "2026-02-03T04:05:06.789Z"
    monkeypatch.setattr(
        casting_repository_module,
        "utc_now",
        lambda: fixed_timestamp,
    )

    first_payload = _correction_payload(
        run=run,
        role=narrator,
        operation="select_voice",
        key="same-time-first-selection",
        voice_profile_id="synthetic-narrator-01",
    )
    first_response = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=first_payload,
    )
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    current_run = first["run"]
    current_role = _role(
        client,
        auth_headers,
        run=current_run,
        role_id=narrator["roleId"],
    )
    second_payload = _correction_payload(
        run=current_run,
        role=current_role,
        operation="select_voice",
        key="same-time-second-selection",
        voice_profile_id="synthetic-narrator-02",
    )
    second_response = _post_correction(
        client,
        auth_headers,
        run=current_run,
        payload=second_payload,
    )
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert first["assignment"]["assignmentId"] != second["assignment"]["assignmentId"]

    replay = _post_correction(
        client,
        auth_headers,
        run=second["run"],
        payload=first_payload,
    )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["correction"]["correctionId"] == (first["correction"]["correctionId"])
    assert replay_body["assignment"]["assignmentId"] == (first["assignment"]["assignmentId"])

    repository = client.app.state.casting
    with repository.database.session() as session:
        assignments = list(
            session.scalars(
                select(CastAssignmentRow)
                .where(
                    CastAssignmentRow.casting_run_id == run["castingRunId"],
                    CastAssignmentRow.role_id == narrator["roleId"],
                    CastAssignmentRow.authority.in_(("human_selection", "human_locked")),
                )
                .order_by(CastAssignmentRow.revision, CastAssignmentRow.id)
            )
        )
        assert [assignment.correction_id for assignment in assignments] == [
            first["correction"]["correctionId"],
            second["correction"]["correctionId"],
        ]
        assert {assignment.created_at for assignment in assignments} == {fixed_timestamp}


def test_casting_correction_limit_is_fail_closed_but_allows_exact_replay(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        casting_repository_module,
        "MAX_CASTING_CORRECTIONS_PER_RUN",
        1,
    )
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-correction-limit",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    first_payload = _correction_payload(
        run=run,
        role=narrator,
        operation="change_role_label",
        key="correction-limit-first",
        corrected_value={"effectiveDisplayLabel": "Bounded narrator"},
    )
    first = _post_correction(
        client,
        auth_headers,
        run=run,
        payload=first_payload,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()

    exact_replay = _post_correction(
        client,
        auth_headers,
        run=first_body["run"],
        payload=first_payload,
    )
    assert exact_replay.status_code == 200, exact_replay.text
    assert (
        exact_replay.json()["correction"]["correctionId"]
        == (first_body["correction"]["correctionId"])
    )

    current_run = first_body["run"]
    current_role = _role(
        client,
        auth_headers,
        run=current_run,
        role_id=narrator["roleId"],
    )
    over_limit = _post_correction(
        client,
        auth_headers,
        run=current_run,
        payload=_correction_payload(
            run=current_run,
            role=current_role,
            operation="change_role_label",
            key="correction-limit-second",
            corrected_value={"effectiveDisplayLabel": "Another narrator label"},
            supersedes_correction_id=first_body["correction"]["correctionId"],
        ),
    )
    assert over_limit.status_code == 409
    assert over_limit.json()["error"]["code"] == ("CASTING_CORRECTION_LIMIT_EXCEEDED")


def test_correction_material_tamper_fails_closed(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-correction-tamper",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    run, selection, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="select_voice",
        key="select-before-correction-tamper",
        voice_profile_id="synthetic-narrator-01",
    )
    correction_id = selection["correction"]["correctionId"]
    repository = client.app.state.casting
    with repository.database.session() as session:
        row = session.get(CastingCorrectionRow, correction_id)
        assert row is not None
        row.corrected_value_json = canonical_json(
            {"voiceProfileId": "synthetic-narrator-02"}
        )

    response = client.get(
        (
            f"/api/v1/projects/{project_id}/casting-runs/"
            f"{run['castingRunId']}/corrections"
        ),
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == (
        "CASTING_CORRECTION_EVIDENCE_INVALID"
    )


def test_snapshot_manifest_fingerprint_and_role_tamper_fail_closed(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-snapshot-tamper",
    )
    repository = client.app.state.casting
    with repository.database.session() as session:
        snapshot = session.get(
            ApprovedCastSnapshotRow,
            run["approvedCastSnapshot"]["snapshotId"],
        )
        assert snapshot is not None
        original_manifest_json = snapshot.manifest_json
        original_fingerprint = snapshot.snapshot_fingerprint
        manifest = copy.deepcopy(parse_json(snapshot.manifest_json, {}))
        manifest["counts"]["productionRoles"] += 1
        snapshot.manifest_json = canonical_json(manifest)
        snapshot.snapshot_fingerprint = repository._snapshot_fingerprint(
            snapshot,
            manifest,
        )
        with pytest.raises(ServiceError) as manifest_error:
            repository._snapshot_wire(session, snapshot)
        assert manifest_error.value.code == "CASTING_SNAPSHOT_MANIFEST_INVALID"

        snapshot.manifest_json = original_manifest_json
        snapshot.snapshot_fingerprint = "0" * 64
        with pytest.raises(ServiceError) as fingerprint_error:
            repository._snapshot_wire(session, snapshot)
        assert fingerprint_error.value.code == "CASTING_SNAPSHOT_MANIFEST_INVALID"

        snapshot.snapshot_fingerprint = original_fingerprint
        role = session.get(
            ProductionRoleRow,
            next(iter(parse_json(original_manifest_json, {})["productionRoleEvidence"]))[
                "roleId"
            ],
        )
        assert role is not None
        original_label = role.effective_display_label
        role.effective_display_label = f"{original_label} tampered"
        with pytest.raises(ServiceError) as role_error:
            repository._snapshot_wire(session, snapshot)
        assert role_error.value.code == "CASTING_ROLE_EVIDENCE_INVALID"
        role.effective_display_label = original_label


def test_superseded_restricted_rights_acknowledgement_does_not_authorize(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-superseded-rights",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    _candidate(
        client,
        auth_headers,
        run=run,
        role=narrator,
        voice_profile_id="synthetic-narrator-02",
    )
    run, selection, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="select_voice",
        key="select-rights-leaf-voice",
        voice_profile_id="synthetic-narrator-02",
    )
    run, acknowledgement, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="acknowledge_restricted_rights",
        key="ack-rights-leaf-voice",
        voice_profile_id="synthetic-narrator-02",
        corrected_value={
            "rightsRecordId": selection["assignment"]["rightsRecordId"],
            "rightsRecordRevision": selection["assignment"]["rightsRecordRevision"],
        },
    )
    acknowledgement_id = acknowledgement["correction"]["correctionId"]
    restricted_marker = f"restricted-rights:{narrator['roleId']}:"

    repository = client.app.state.casting
    with repository.database.session() as session:
        run_row = session.get(CastingRunRow, run["castingRunId"])
        acknowledgement_row = session.get(
            CastingCorrectionRow,
            acknowledgement_id,
        )
        assert run_row is not None
        assert acknowledgement_row is not None
        blockers_before, _ = repository._review_blockers(
            session,
            run=run_row,
            gate_id="narrator_casting_review",
        )
        assert not any(value.startswith(restricted_marker) for value in blockers_before)

        now = utc_now()
        wrong_value = {
            "rightsRecordId": "rights-synthetic-character-10",
            "rightsRecordRevision": 1,
        }
        successor = CastingCorrectionRow(
            id=new_id(),
            project_id=run_row.project_id,
            casting_run_id=run_row.id,
            role_id=narrator["roleId"],
            kind="acknowledge_restricted_rights",
            revision=acknowledgement_row.revision + 1,
            prior_effective_fingerprint=(acknowledgement_row.prior_effective_fingerprint),
            corrected_value_json=canonical_json(wrong_value),
            correction_fingerprint="",
            actor_id="local_user",
            reason=("Represent a later acknowledgement for different exact rights evidence."),
            provenance_json=canonical_json(
                {
                    "origin": "human",
                    "producerId": "local_user",
                    "producerVersion": "1.0.0",
                    "recordedAt": now,
                    "inputFingerprint": (acknowledgement_row.prior_effective_fingerprint),
                }
            ),
            supersedes_correction_id=acknowledgement_row.id,
            idempotency_key="superseding-rights-leaf",
            recorded_at=now,
        )
        successor.correction_fingerprint = request_fingerprint(
            repository._correction_material(successor)
        )
        session.add(successor)
        session.flush()
        blockers_after, _ = repository._review_blockers(
            session,
            run=run_row,
            gate_id="narrator_casting_review",
        )
        assert any(value.startswith(restricted_marker) for value in blockers_after)


def test_human_assignments_create_and_supersede_metadata_reuse_conflicts(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-human-reuse",
    )
    roles = _roles(client, auth_headers, run=run)
    narrator = next(value for value in roles if value["roleType"] == "primary_narrator")
    character = next(value for value in roles if value["roleType"] == "named_character")
    for role in (narrator, character):
        _candidate(
            client,
            auth_headers,
            run=run,
            role=role,
            voice_profile_id="synthetic-narrator-01",
        )
        run, _selection, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=role["roleId"],
            operation="select_voice",
            key=f"reuse-{role['roleId']}",
            voice_profile_id="synthetic-narrator-01",
            corrected_value={"voiceProfileId": "synthetic-narrator-01"},
        )

    conflicts_url = (
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/conflicts"
    )
    conflicts = client.get(
        conflicts_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert conflicts.status_code == 200, conflicts.text
    dynamic = next(
        value
        for value in conflicts.json()["items"]
        if value["category"] == "narrator_major_character_reuse"
        and set(value["roleIds"]) == {narrator["roleId"], character["roleId"]}
        and value["voiceProfileIds"] == ["synthetic-narrator-01"]
    )
    assert dynamic["resolutionState"] == "open"
    assert dynamic["metadataOnly"] is True
    assert dynamic["acousticSimilarityClaimed"] is False

    current_character = _role(
        client,
        auth_headers,
        run=run,
        role_id=character["roleId"],
    )
    _candidate(
        client,
        auth_headers,
        run=run,
        role=current_character,
        voice_profile_id="synthetic-character-01",
    )
    run, _replacement, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=character["roleId"],
        operation="select_voice",
        key="replace-reused-character-voice",
        voice_profile_id="synthetic-character-01",
        corrected_value={"voiceProfileId": "synthetic-character-01"},
    )
    refreshed = client.get(
        conflicts_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert refreshed.status_code == 200, refreshed.text
    resolved = next(
        value for value in refreshed.json()["items"] if value["conflictId"] == dynamic["conflictId"]
    )
    assert resolved["resolutionState"] == "superseded"


def test_assignment_conflict_refresh_is_append_only_and_preserves_dispositions(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-immutable-assignment-conflicts",
    )
    roles = _roles(client, auth_headers, run=run)
    narrator = next(value for value in roles if value["roleType"] == "primary_narrator")
    character = next(value for value in roles if value["roleType"] == "named_character")
    selections: dict[str, dict[str, Any]] = {}
    for role in (narrator, character):
        _candidate(
            client,
            auth_headers,
            run=run,
            role=role,
            voice_profile_id="synthetic-narrator-01",
        )
        run, selection, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=role["roleId"],
            operation="select_voice",
            key=f"immutable-conflict-select-{role['roleId']}",
            voice_profile_id="synthetic-narrator-01",
            corrected_value={"voiceProfileId": "synthetic-narrator-01"},
        )
        selections[role["roleId"]] = selection

    conflicts_url = (
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/conflicts"
    )
    initial_response = client.get(
        conflicts_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert initial_response.status_code == 200, initial_response.text
    initial = next(
        value
        for value in initial_response.json()["items"]
        if value["category"] == "narrator_major_character_reuse"
        and set(value["roleIds"]) == {narrator["roleId"], character["roleId"]}
        and value["voiceProfileIds"] == ["synthetic-narrator-01"]
        and value["resolutionState"] == "open"
    )

    repository = client.app.state.casting
    with repository.database.session() as session:
        initial_row = session.get(CastingConflictRow, initial["conflictId"])
        assert initial_row is not None
        immutable_values = {
            "detailsJson": initial_row.details_json,
            "evidenceFingerprint": initial_row.evidence_fingerprint,
            "provenanceJson": initial_row.provenance_json,
            "createdAt": initial_row.created_at,
        }

    run, approval, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="approve_voice_reuse",
        key="approve-immutable-assignment-conflict",
        corrected_value={
            "conflictId": initial["conflictId"],
            "approvedRoleIds": initial["roleIds"],
        },
    )
    approval_id = approval["correction"]["correctionId"]
    run, _rationale, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="record_custom_rationale",
        key="unrelated-rationale-preserves-assignment-conflict",
        corrected_value={
            "rationale": "Keep this casting rationale separate from reuse evidence.",
        },
    )
    after_unrelated_response = client.get(
        conflicts_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert after_unrelated_response.status_code == 200, after_unrelated_response.text
    after_unrelated = next(
        value
        for value in after_unrelated_response.json()["items"]
        if value["conflictId"] == initial["conflictId"]
    )
    assert after_unrelated["resolutionState"] == "approved_reuse"
    assert after_unrelated["dispositionCorrectionId"] == approval_id
    assert (
        len(
            [
                value
                for value in after_unrelated_response.json()["items"]
                if value["category"] == "narrator_major_character_reuse"
                and set(value["roleIds"]) == {narrator["roleId"], character["roleId"]}
                and value["voiceProfileIds"] == ["synthetic-narrator-01"]
            ]
        )
        == 1
    )

    narrator_assignment_id = selections[narrator["roleId"]]["assignment"]["assignmentId"]
    run, _lock, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="lock_assignment",
        key="new-assignment-generation-appends-conflict",
        corrected_value={"assignmentId": narrator_assignment_id},
    )
    regenerated_response = client.get(
        conflicts_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert regenerated_response.status_code == 200, regenerated_response.text
    regenerated_items = regenerated_response.json()["items"]
    prior = next(
        value for value in regenerated_items if value["conflictId"] == initial["conflictId"]
    )
    assert prior["resolutionState"] == "superseded"
    assert prior["dispositionCorrectionId"] == approval_id
    current = next(
        value
        for value in regenerated_items
        if value["category"] == "narrator_major_character_reuse"
        and set(value["roleIds"]) == {narrator["roleId"], character["roleId"]}
        and value["voiceProfileIds"] == ["synthetic-narrator-01"]
        and value["resolutionState"] == "open"
    )
    assert current["conflictId"] != initial["conflictId"]
    assert current["inputFingerprint"] != initial["inputFingerprint"]

    with repository.database.session() as session:
        prior_row = session.get(CastingConflictRow, initial["conflictId"])
        current_row = session.get(CastingConflictRow, current["conflictId"])
        approval_row = session.get(CastingCorrectionRow, approval_id)
        assert prior_row is not None
        assert current_row is not None
        assert approval_row is not None
        assert prior_row.status == "superseded"
        assert prior_row.details_json == immutable_values["detailsJson"]
        assert prior_row.evidence_fingerprint == immutable_values["evidenceFingerprint"]
        assert prior_row.provenance_json == immutable_values["provenanceJson"]
        assert prior_row.created_at == immutable_values["createdAt"]
        assert parse_json(approval_row.corrected_value_json, {})["conflictId"] == prior_row.id


def test_actual_selected_voices_in_declared_similarity_group_create_conflict(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-selected-similarity",
    )
    characters = [
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "named_character"
    ]
    assert len(characters) >= 2
    selected = (
        (characters[0], "synthetic-character-01"),
        (characters[1], "synthetic-character-02"),
    )
    for role, voice_id in selected:
        current_role = _role(
            client,
            auth_headers,
            run=run,
            role_id=role["roleId"],
        )
        _candidate(
            client,
            auth_headers,
            run=run,
            role=current_role,
            voice_profile_id=voice_id,
        )
        run, _selection, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=role["roleId"],
            operation="select_voice",
            key=f"similarity-{role['roleId']}",
            voice_profile_id=voice_id,
        )

    conflicts = client.get(
        (f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/conflicts"),
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert conflicts.status_code == 200, conflicts.text
    expected_role_ids = {role["roleId"] for role, _voice_id in selected}
    dynamic = next(
        value
        for value in conflicts.json()["items"]
        if value["category"] == "metadata_similarity_risk"
        and set(value["roleIds"]) == expected_role_ids
        and set(value["voiceProfileIds"])
        == {
            "synthetic-character-01",
            "synthetic-character-02",
        }
        and value["explanation"].startswith("Current human selections")
    )
    assert dynamic["metadataOnly"] is True
    assert dynamic["acousticSimilarityClaimed"] is False


def test_assignment_conflicts_are_aggregated_bounded_and_fail_closed(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-bounded-assignment-conflicts",
    )
    roles = _roles(client, auth_headers, run=run)
    selected_roles = [
        next(value for value in roles if value["roleType"] == "primary_narrator"),
        *[value for value in roles if value["roleType"] == "named_character"][:2],
    ]
    assert len(selected_roles) == 3
    conflicts_url = (
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/conflicts"
    )
    initial = client.get(
        conflicts_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert initial.status_code == 200, initial.text
    static_count = initial.json()["total"]
    monkeypatch.setattr(
        casting_repository_module,
        "MAX_CASTING_CONFLICTS",
        static_count + 1,
    )

    for role in selected_roles:
        current_role = _role(
            client,
            auth_headers,
            run=run,
            role_id=role["roleId"],
        )
        _candidate(
            client,
            auth_headers,
            run=run,
            role=current_role,
            voice_profile_id="synthetic-narrator-01",
        )
        run, _selection, _ = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=role["roleId"],
            operation="select_voice",
            key=f"bounded-reuse-{role['roleId']}",
            voice_profile_id="synthetic-narrator-01",
        )

    bounded = client.get(
        conflicts_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert bounded.status_code == 200, bounded.text
    assert bounded.json()["total"] <= static_count + 1
    current_dynamic = [
        value
        for value in bounded.json()["items"]
        if value["resolutionState"] == "open"
        and value["explanation"].startswith("The current human assignments")
    ]
    assert current_dynamic == []
    historical_dynamic = [
        value
        for value in bounded.json()["items"]
        if value["resolutionState"] == "superseded"
        and value["explanation"].startswith("The current human assignments")
    ]
    assert len(historical_dynamic) == 1
    assert set(historical_dynamic[0]["roleIds"]) < {value["roleId"] for value in selected_roles}
    assert historical_dynamic[0]["voiceProfileIds"] == ["synthetic-narrator-01"]
    assert historical_dynamic[0]["acousticSimilarityClaimed"] is False

    narrator_review = next(
        value
        for value in _reviews(client, auth_headers, run=run)
        if value["gateId"] == "narrator_casting_review"
    )
    rejected = _post_decision(
        client,
        auth_headers,
        run=run,
        gate_id="narrator_casting_review",
        payload=_decision_payload(
            run=run,
            review=narrator_review,
            key="bounded-overflow-must-block",
        ),
    )
    assert rejected.status_code == 409
    assert "casting-assignment-conflict-evidence-overflow" in _blocking_reason_codes(rejected)


def test_requirement_correction_replaces_current_candidate_evidence_atomically(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-requirement-refresh",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    before_candidates = _candidates(
        client,
        auth_headers,
        run=run,
        role=narrator,
    )
    selected = next(
        value for value in before_candidates if value["voiceProfileId"] == "synthetic-narrator-01"
    )
    run, _selection, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="select_voice",
        key="select-before-requirement-change",
        voice_profile_id=selected["voiceProfileId"],
    )
    before_role = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    before_candidates = _candidates(
        client,
        auth_headers,
        run=run,
        role=before_role,
    )
    before_snapshot = run["approvedCastSnapshot"]
    repository = client.app.state.casting
    with repository.database.session() as session:
        before_conflict_ids = {
            value.id
            for value in session.scalars(
                select(CastingConflictRow).where(
                    CastingConflictRow.casting_run_id == run["castingRunId"],
                    (
                        (CastingConflictRow.primary_role_id == narrator["roleId"])
                        | (CastingConflictRow.secondary_role_id == narrator["roleId"])
                    ),
                    CastingConflictRow.status == "open",
                )
            )
        }
    assert before_conflict_ids

    changed_requirement = {
        "language": "es",
        "locales": ["es-MX"],
        "agePresentationRange": None,
        "vocalPresentations": [],
        "preferredTextures": [],
        "speakingRateRange": None,
        "requiredExpressiveRange": [],
        "longFormRequired": False,
    }
    run, _change, _ = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="change_casting_requirement",
        key="change-narrator-language",
        corrected_value={"requirement": changed_requirement},
    )
    after_role = _role(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
    )
    assert after_role["languageRequirements"] == ["es"]
    assert after_role["performanceRequirements"] == (changed_requirement)
    after_candidates = _candidates(
        client,
        auth_headers,
        run=run,
        role=after_role,
    )
    before_ids = {value["candidateId"] for value in before_candidates}
    after_ids = {value["candidateId"] for value in after_candidates}
    assert before_ids.isdisjoint(after_ids)
    assert {value["outputFingerprint"] for value in before_candidates}.isdisjoint(
        {value["outputFingerprint"] for value in after_candidates}
    )
    assert selected["candidateId"] not in after_ids

    stale_candidates = client.get(
        (
            f"/api/v1/projects/{run['projectId']}/casting-runs/"
            f"{run['castingRunId']}/roles/{narrator['roleId']}/"
            "candidates"
        ),
        headers=auth_headers,
        params={
            **_evidence(run),
            "expectedRoleRevision": before_role["revision"],
            "limit": 12,
        },
    )
    assert stale_candidates.status_code == 409
    assert stale_candidates.json()["error"]["code"] == ("CASTING_ROLE_STALE")

    narrator_review = next(
        value
        for value in _reviews(client, auth_headers, run=run)
        if value["gateId"] == "narrator_casting_review"
    )
    rejected_approval = _post_decision(
        client,
        auth_headers,
        run=run,
        gate_id="narrator_casting_review",
        payload=_decision_payload(
            run=run,
            review=narrator_review,
            key="reject-stale-selected-candidate",
        ),
    )
    assert rejected_approval.status_code == 409
    assert rejected_approval.json()["error"]["code"] == ("CASTING_REVIEW_NOT_ELIGIBLE")
    assert any(
        value.startswith(f"ineligible-candidate:{narrator['roleId']}:")
        for value in _blocking_reason_codes(rejected_approval)
    )

    with repository.database.session() as session:
        historical_candidates = [
            session.get(CastingCandidateRow, candidate_id) for candidate_id in before_ids
        ]
        assert all(value is not None for value in historical_candidates)
        assert {value.role_revision for value in historical_candidates if value is not None} == {1}
        assert {
            session.get(CastingCandidateRow, candidate_id).role_revision
            for candidate_id in after_ids
            if session.get(CastingCandidateRow, candidate_id) is not None
        } == {after_role["revision"]}
        superseded_conflicts = {
            value.id
            for value in session.scalars(
                select(CastingConflictRow).where(
                    CastingConflictRow.id.in_(before_conflict_ids),
                    CastingConflictRow.status == "superseded",
                )
            )
        }
        assert superseded_conflicts == before_conflict_ids
        current_conflicts = list(
            session.scalars(
                select(CastingConflictRow).where(
                    CastingConflictRow.casting_run_id == run["castingRunId"],
                    (
                        (CastingConflictRow.primary_role_id == narrator["roleId"])
                        | (CastingConflictRow.secondary_role_id == narrator["roleId"])
                    ),
                    CastingConflictRow.status == "open",
                )
            )
        )
        assert current_conflicts
        assert not before_conflict_ids.intersection(value.id for value in current_conflicts)
        conflict_row = current_conflicts[0]
        stored_conflict_details = conflict_row.details_json
        tampered_conflict_details = parse_json(stored_conflict_details, {})
        tampered_conflict_details["explanation"] = "Tampered conflict explanation."
        conflict_row.details_json = canonical_json(tampered_conflict_details)
        with pytest.raises(ServiceError) as conflict_error:
            repository._conflict_wire(session, conflict_row)
        assert conflict_error.value.code == "CASTING_CONFLICT_INVALID"
        conflict_row.details_json = stored_conflict_details
        stored_conflict_provenance = conflict_row.provenance_json
        tampered_conflict_provenance = parse_json(
            stored_conflict_provenance,
            {},
        )
        tampered_conflict_provenance["producerId"] = "untrusted-producer"
        conflict_row.provenance_json = canonical_json(
            tampered_conflict_provenance,
        )
        with pytest.raises(ServiceError) as provenance_error:
            repository._conflict_wire(session, conflict_row)
        assert provenance_error.value.code == "CASTING_CONFLICT_INVALID"
        conflict_row.provenance_json = stored_conflict_provenance
        unknown_language_row = session.get(
            CastingCandidateRow,
            after_candidates[0]["candidateId"],
        )
        assert unknown_language_row is not None
        stored_language_eligibility = unknown_language_row.language_eligibility
        unknown_language_row.language_eligibility = "unknown"
        with pytest.raises(ServiceError) as candidate_error:
            repository._candidate_wire(
                session,
                unknown_language_row,
            )["assessment"]["languageEligibility"]
        assert candidate_error.value.code == "CASTING_CANDIDATE_INVALID"
        unknown_language_row.language_eligibility = stored_language_eligibility
        prior_row = session.get(
            ApprovedCastSnapshotRow,
            before_snapshot["snapshotId"],
        )
        current_row = session.get(
            ApprovedCastSnapshotRow,
            run["approvedCastSnapshot"]["snapshotId"],
        )
        assert prior_row is not None
        assert current_row is not None
        prior_manifest = parse_json(prior_row.manifest_json, {})
        current_manifest = parse_json(
            current_row.manifest_json,
            {},
        )
        prior_role_candidate_ids = {
            value["candidateId"]
            for value in prior_manifest["candidateEvidence"]
            if value["roleId"] == narrator["roleId"]
        }
        current_role_candidate_ids = {
            value["candidateId"]
            for value in current_manifest["candidateEvidence"]
            if value["roleId"] == narrator["roleId"]
        }
        assert prior_role_candidate_ids == before_ids
        assert current_role_candidate_ids == after_ids
        assert run["summary"]["finalCandidates"] == (current_manifest["counts"]["finalCandidates"])
        assert run["summary"]["conflicts"] == current_manifest["counts"]["conflicts"]
        assert run["summary"]["assignments"] == current_manifest["counts"]["assignments"]
        historical_candidate_count = len(
            list(
                session.scalars(
                    select(CastingCandidateRow.id).where(
                        CastingCandidateRow.casting_run_id == run["castingRunId"]
                    )
                )
            )
        )
        historical_conflict_count = len(
            list(
                session.scalars(
                    select(CastingConflictRow.id).where(
                        CastingConflictRow.casting_run_id == run["castingRunId"]
                    )
                )
            )
        )
        assert historical_candidate_count > run["summary"]["finalCandidates"]
        assert historical_conflict_count > run["summary"]["conflicts"]


def test_voice_assignment_preserves_exact_catalog_profile_version(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-profile-semver",
    )
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    assert any(
        value["voiceProfileId"] == "synthetic-character-06"
        for value in _candidates(
            client,
            auth_headers,
            run=run,
            role=narrator,
        )
    )
    _run, selection, _payload = _apply_correction(
        client,
        auth_headers,
        run=run,
        role_id=narrator["roleId"],
        operation="select_voice",
        key="select-exact-semver-profile",
        voice_profile_id="synthetic-character-06",
    )
    assert selection["assignment"]["voiceProfileVersion"] == "0.9.0"
    repository = client.app.state.casting
    with repository.database.session() as session:
        voice = session.scalar(
            select(VoiceProfileRow).where(VoiceProfileRow.profile_id == "synthetic-character-06")
        )
        assert voice is not None
        assert voice.revision == 1
        assert voice.profile_version == "0.9.0"


def test_catalog_roles_candidates_pagination_and_mutation_payload_limits(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="governance-pagination",
    )
    catalog_url = f"/api/v1/projects/{project_id}/casting/catalog"
    catalog_items, catalog_total = _collect_pages(
        client,
        auth_headers,
        url=catalog_url,
        params={},
        limit=5,
        identity_key="voiceProfileId",
    )
    assert catalog_total == 14
    assert len(catalog_items) == 14

    roles_url = f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/roles"
    role_items, role_total = _collect_pages(
        client,
        auth_headers,
        url=roles_url,
        params=_evidence(run),
        limit=1,
        identity_key="roleId",
    )
    assert role_total == run["summary"]["productionRoles"]

    first_role = role_items[0]
    candidates_url = (
        f"/api/v1/projects/{project_id}/casting-runs/"
        f"{run['castingRunId']}/roles/{first_role['roleId']}/candidates"
    )
    candidate_items, candidate_total = _collect_pages(
        client,
        auth_headers,
        url=candidates_url,
        params={
            **_evidence(run),
            "expectedRoleRevision": first_role["revision"],
        },
        limit=3,
        identity_key="candidateId",
    )
    assert candidate_total == len(candidate_items)
    assert 1 <= candidate_total <= 12

    first_catalog_page = client.get(
        catalog_url,
        headers=auth_headers,
        params={"limit": 1},
    )
    assert first_catalog_page.status_code == 200, first_catalog_page.text
    catalog_cursor = first_catalog_page.json()["nextCursor"]
    cross_collection_cursor = client.get(
        roles_url,
        headers=auth_headers,
        params={
            **_evidence(run),
            "limit": 1,
            "cursor": catalog_cursor,
        },
    )
    assert cross_collection_cursor.status_code == 400
    assert cross_collection_cursor.json()["error"]["code"] == "INVALID_CURSOR"

    oversized_page = client.get(
        roles_url,
        headers=auth_headers,
        params={**_evidence(run), "limit": 201},
    )
    assert oversized_page.status_code == 422
    assert oversized_page.json()["error"]["code"] == "INVALID_REQUEST"

    current_role = _role(
        client,
        auth_headers,
        run=run,
        role_id=first_role["roleId"],
    )
    base_payload = _correction_payload(
        run=run,
        role=current_role,
        operation="record_custom_rationale",
        key="bounded-correction-payload",
        corrected_value={"rationale": "A bounded rationale."},
    )
    too_many_fields = _post_correction(
        client,
        auth_headers,
        run=run,
        payload={
            **base_payload,
            "correctedValue": {f"field-{index}": "value" for index in range(33)},
        },
    )
    assert too_many_fields.status_code == 422
    assert too_many_fields.json()["error"]["code"] == "INVALID_REQUEST"

    oversized_body = client.post(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/corrections",
        headers={**auth_headers, "Content-Type": "application/json"},
        content=json.dumps(
            {
                **base_payload,
                "reason": "x" * (65 * 1024),
            }
        ),
    )
    assert oversized_body.status_code == 413
    assert oversized_body.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


def test_all_casting_read_and_write_routes_require_authentication(
    client: TestClient,
) -> None:
    project_id = "project-auth"
    run_id = "casting-run-auth"
    role_id = "casting-role-auth"
    routes = (
        ("GET", f"/api/v1/projects/{project_id}/casting/catalog"),
        ("GET", f"/api/v1/projects/{project_id}/casting-runs"),
        ("POST", f"/api/v1/projects/{project_id}/casting-runs"),
        ("GET", f"/api/v1/projects/{project_id}/casting-runs/{run_id}"),
        (
            "GET",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/roles",
        ),
        (
            "POST",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/roles",
        ),
        (
            "GET",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/roles/{role_id}/candidates",
        ),
        (
            "GET",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/conflicts",
        ),
        (
            "GET",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/assignments",
        ),
        (
            "GET",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/corrections",
        ),
        (
            "POST",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/corrections",
        ),
        (
            "GET",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/reviews",
        ),
        (
            "POST",
            f"/api/v1/projects/{project_id}/casting-runs/{run_id}/reviews/"
            "complete_cast_review/decisions",
        ),
    )
    for method, url in routes:
        for headers in ({}, {"Authorization": "Bearer wrong-token"}):
            response = client.request(
                method,
                url,
                headers=headers,
            )
            assert response.status_code == 401, (method, url, response.text)
            assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
