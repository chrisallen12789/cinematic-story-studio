from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import replace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from cinematic_story_service.casting import (
    CASTING_CONTRACT_VERSION,
    CASTING_GATE_IDS,
    MAX_CASTING_WARNINGS_PER_ENTITY,
    validate_catalog,
)
from cinematic_story_service.models import (
    CastAssignmentInvalidationRow,
    CastingGateDecisionRow,
)
from cinematic_story_service.util import request_fingerprint
from tests.conftest import collect_concurrent_database_results
from tests.test_phase2_api import create_phase2_run
from tests.test_phase3a_casting import (
    _approve_phase2,
    _candidates,
    _correct,
    _create_casting_run,
    _evidence,
    _roles,
)

_NARRATOR_GATE = "narrator_casting_review"
_CHARACTER_GATE = "character_casting_review"
_COMPLETE_GATE = "complete_cast_review"


def _catalog_variant(
    catalog: Any,
    *,
    revision_tag: str,
    providers: tuple[dict[str, Any], ...] | None = None,
    models: tuple[dict[str, Any], ...] | None = None,
    voices: tuple[dict[str, Any], ...] | None = None,
    rights: tuple[dict[str, Any], ...] | None = None,
) -> Any:
    next_providers = providers or catalog.providers
    next_models = models or catalog.models
    proposed_voices = voices or catalog.voices
    proposed_rights = rights or catalog.rights
    revision_id = f"synthetic-voice-catalog-v1@{revision_tag}"
    proposed_rights_by_voice = {str(value["voiceProfileId"]): value for value in proposed_rights}
    next_voices_list: list[dict[str, Any]] = []
    next_rights_list: list[dict[str, Any]] = []
    for proposed_voice in proposed_voices:
        voice_profile_id = str(proposed_voice["voiceProfileId"])
        proposed_rights_record = proposed_rights_by_voice[voice_profile_id]
        next_rights_record = {
            **proposed_rights_record,
            "providerId": proposed_voice["providerId"],
        }
        next_voice = {
            **proposed_voice,
            "catalogRevisionId": revision_id,
            "rightsRecordId": next_rights_record["rightsRecordId"],
            "rightsState": next_rights_record["state"],
            "commercialUse": next_rights_record["commercialUsePermission"],
            "attributionRequired": (next_rights_record["attributionRequirement"] == "required"),
            "consentStatus": next_rights_record["consentStatus"],
        }
        next_voices_list.append(next_voice)
        next_rights_list.append(next_rights_record)
    next_voices = tuple(next_voices_list)
    next_rights = tuple(next_rights_list)
    revision = {
        **catalog.revision,
        "catalogRevisionId": revision_id,
        "revision": 2,
        "semanticVersion": revision_tag,
    }
    fingerprint = request_fingerprint(
        {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "catalogRevision": {
                key: value for key, value in revision.items() if key != "catalogFingerprint"
            },
            "providers": next_providers,
            "models": next_models,
            "voices": next_voices,
            "rights": next_rights,
        }
    )
    revision["catalogFingerprint"] = fingerprint
    return validate_catalog(
        replace(
            catalog,
            revision=revision,
            providers=next_providers,
            models=next_models,
            voices=next_voices,
            rights=next_rights,
            fingerprint=fingerprint,
        ).to_wire()
    )


def _list_reviews(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
) -> list[dict[str, Any]]:
    snapshot = run["approvedCastSnapshot"]
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


def _list_assignments(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/assignments",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _decision_records(
    app: FastAPI,
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    with app.state.database.session() as session:
        rows = list(
            session.scalars(
                select(CastingGateDecisionRow)
                .where(CastingGateDecisionRow.casting_run_id == run_id)
                .order_by(
                    CastingGateDecisionRow.gate_id,
                    CastingGateDecisionRow.revision,
                    CastingGateDecisionRow.id,
                )
            )
        )
        return [
            {
                "id": row.id,
                "gateId": row.gate_id,
                "revision": row.revision,
                "decision": row.decision,
                "actorId": row.actor_id,
                "provenance": row.provenance_json,
                "supersedesDecisionId": row.supersedes_decision_id,
            }
            for row in rows
        ]


def _invalidation_records(
    app: FastAPI,
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    with app.state.database.session() as session:
        rows = list(
            session.scalars(
                select(CastAssignmentInvalidationRow)
                .where(CastAssignmentInvalidationRow.casting_run_id == run_id)
                .order_by(
                    CastAssignmentInvalidationRow.created_at,
                    CastAssignmentInvalidationRow.id,
                )
            )
        )
        return [
            {
                "id": row.id,
                "roleId": row.role_id,
                "assignmentId": row.assignment_id,
                "reasonCodesJson": row.reason_codes_json,
                "evidenceFingerprint": row.evidence_fingerprint,
                "provenanceJson": row.provenance_json,
            }
            for row in rows
        ]


def _approve_current_gates(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    key_prefix: str,
) -> None:
    for gate_id in CASTING_GATE_IDS:
        review = next(
            value
            for value in _list_reviews(client, auth_headers, run=run)
            if value["gateId"] == gate_id
        )
        if review["state"] == "approved" and review["latestDecision"] is not None:
            continue
        snapshot = run["approvedCastSnapshot"]
        response = client.post(
            f"/api/v1/projects/{run['projectId']}/casting-runs/"
            f"{run['castingRunId']}/reviews/{gate_id}/decisions",
            headers=auth_headers,
            json={
                "decision": "approve",
                "expectedRevision": review["revision"],
                "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
                "expectedRunFingerprint": (run["outputFingerprint"] or run["inputFingerprint"]),
                "expectedApprovedCastSnapshotId": snapshot["snapshotId"],
                "expectedApprovedCastSnapshotRevision": snapshot["revision"],
                "warningAcknowledgementIds": review["openWarningIds"],
                "rationale": f"Approve synthetic {gate_id} evidence.",
                "supersedesDecisionId": (
                    review["latestDecision"]["decisionId"]
                    if review["latestDecision"] is not None
                    else None
                ),
                "idempotencyKey": f"{key_prefix}-{gate_id}",
            },
        )
        assert response.status_code == 200, f"{gate_id}: {response.text}; review={review}"
        decision = response.json()["decision"]
        assert decision["decision"] == "approved"
        assert decision["actor"]["classification"] == "human"


def _select_voice(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    role: dict[str, Any],
    voice_profile_id: str,
    idempotency_key: str,
    supersedes_correction_id: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/corrections",
        headers=auth_headers,
        json={
            "operation": "select_voice",
            "targetRoleId": role["roleId"],
            "expectedRoleRevision": role["revision"],
            "expectedRunFingerprint": (run["outputFingerprint"] or run["inputFingerprint"]),
            "expectedCatalogFingerprint": run["catalogFingerprint"],
            "expectedSnapshotFingerprint": run["prerequisites"]["analysisSnapshotFingerprint"],
            "expectedCorrectionSetFingerprint": run["effectiveCorrectionSetFingerprint"],
            "previousEffectiveFingerprint": role["effectiveFingerprint"],
            "voiceProfileId": voice_profile_id,
            "correctedValue": {"voiceProfileId": voice_profile_id},
            "reason": "Select governed synthetic voice evidence.",
            "supersedesCorrectionId": supersedes_correction_id,
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _prepare_approved_cast(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="assign-drift",
    )
    project_id = imported["project"]["projectId"]
    analysis_run, phase2_decisions = _approve_phase2(
        client,
        auth_headers,
        project_id=project_id,
        run_id=created["run"]["runId"],
        idempotency_prefix="phase3a-invalidation-phase2",
    )
    run = _create_casting_run(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=analysis_run,
        decisions=phase2_decisions,
        idempotency_key="assign-drift-cast",
    )
    roles = _roles(client, auth_headers, run=run)
    narrator = next(value for value in roles if value["roleType"] == "primary_narrator")
    characters = [value for value in roles if value["roleType"] == "named_character"]
    assert characters
    character_voice_ids = (
        "synthetic-character-01",
        "synthetic-character-02",
        "synthetic-character-03",
        "synthetic-character-04",
    )
    selections = [(narrator, "synthetic-narrator-01")] + [
        (role, character_voice_ids[index % len(character_voice_ids)])
        for index, role in enumerate(characters)
    ]
    selected: dict[str, dict[str, Any]] = {}
    correction_ids: dict[str, str] = {}
    for index, (original_role, voice_profile_id) in enumerate(selections):
        role = next(
            value
            for value in _roles(client, auth_headers, run=run)
            if value["roleId"] == original_role["roleId"]
        )
        if role["performanceRequirements"]["language"] == "und":
            run = _correct(
                client,
                auth_headers,
                run=run,
                role=role,
                operation="change_casting_requirement",
                key=f"phase3a-invalidation-language-{index}",
                corrected_value={
                    "requirement": {
                        **role["performanceRequirements"],
                        "language": "en",
                        "locales": ["en-US"],
                    }
                },
            )
            role = next(
                value
                for value in _roles(client, auth_headers, run=run)
                if value["roleId"] == original_role["roleId"]
            )
        assert voice_profile_id in {
            value["voiceProfileId"]
            for value in _candidates(
                client,
                auth_headers,
                run=run,
                role=role,
            )
        }
        selected_response = _select_voice(
            client,
            auth_headers,
            run=run,
            role=role,
            voice_profile_id=voice_profile_id,
            idempotency_key=f"phase3a-invalidation-select-{index}",
        )
        run = selected_response["run"]
        assignment = selected_response["assignment"]
        assert assignment is not None
        assert assignment["effective"] is True
        selected[role["roleId"]] = assignment
        correction_ids[role["roleId"]] = selected_response["correction"]["correctionId"]

    selected_role_ids = set(selected)
    for index, original_role in enumerate(
        value for value in roles if value["roleId"] not in selected_role_ids
    ):
        role = next(
            value
            for value in _roles(client, auth_headers, run=run)
            if value["roleId"] == original_role["roleId"]
        )
        run = _correct(
            client,
            auth_headers,
            run=run,
            role=role,
            operation="mark_intentionally_uncast",
            key=f"phase3a-invalidation-uncast-{index}",
        )

    first_review = _list_reviews(client, auth_headers, run=run)[0]
    snapshot = run["approvedCastSnapshot"]
    base_decision = {
        "expectedRevision": first_review["revision"],
        "expectedEvidenceFingerprint": first_review["evidence"]["evidenceFingerprint"],
        "expectedRunFingerprint": (run["outputFingerprint"] or run["inputFingerprint"]),
        "expectedApprovedCastSnapshotId": snapshot["snapshotId"],
        "expectedApprovedCastSnapshotRevision": snapshot["revision"],
        "warningAcknowledgementIds": first_review["openWarningIds"],
        "rationale": "No system-authored approval is accepted from a client.",
        "supersedesDecisionId": None,
    }
    human_invalidated = client.post(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/"
        f"reviews/{first_review['gateId']}/decisions",
        headers=auth_headers,
        json={
            **base_decision,
            "decision": "invalidated",
            "idempotencyKey": "human-cannot-invalidate",
        },
    )
    assert human_invalidated.status_code == 422
    forged_system_approval = client.post(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/"
        f"reviews/{first_review['gateId']}/decisions",
        headers=auth_headers,
        json={
            **base_decision,
            "decision": "approve",
            "actor": {
                "classification": "system",
                "actorId": "casting-evidence-monitor",
            },
            "idempotencyKey": "client-cannot-forge-system-approval",
        },
    )
    assert forged_system_approval.status_code == 422

    _approve_current_gates(
        app,
        client,
        auth_headers,
        run=run,
        key_prefix="gate-initial",
    )
    assert {
        value["gateId"]: value["state"] for value in _list_reviews(client, auth_headers, run=run)
    } == {gate_id: "approved" for gate_id in CASTING_GATE_IDS}
    return run, selected, correction_ids


def _project_casting(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    casting = response.json()["voiceCasting"]
    assert casting is not None
    return casting


def _assert_gate_states(
    casting: dict[str, Any],
    expected: dict[str, str],
) -> None:
    assert {value["gateId"]: value["state"] for value in casting["gateReviews"]} == expected


def _assert_bounded_invalidation_warning(
    assignment: dict[str, Any],
) -> None:
    warnings = assignment["warnings"]
    assert 1 <= len(warnings) <= MAX_CASTING_WARNINGS_PER_ENTITY
    invalidation_warnings = [
        value for value in warnings if "INVALIDAT" in str(value.get("code", "")).upper()
    ]
    assert invalidation_warnings
    for warning in invalidation_warnings:
        assert warning["requiresHumanReview"] is True
        assert warning["message"]
        assert len(warning["message"]) <= 1000
        assert warning["evidence"] == []
        assert assignment["roleId"] in warning["relatedEntityIds"]


def _assert_assignment_evidence_is_frozen(
    original: dict[str, Any],
    current: dict[str, Any],
) -> None:
    for field in (
        "voiceProfileId",
        "voiceProfileVersion",
        "voiceEvidenceFingerprint",
        "rightsRecordId",
        "rightsRecordRevision",
        "rightsEvidenceFingerprint",
        "rightsState",
        "catalogRevisionId",
    ):
        assert current[field] == original[field]


def _assert_system_invalidation_decisions(
    app: FastAPI,
    casting: dict[str, Any],
    *,
    run_id: str,
    invalidated_gate_ids: set[str],
    approved_gate_ids: set[str],
) -> None:
    by_gate = {value["gateId"]: value for value in casting["gateReviews"]}
    for gate_id in invalidated_gate_ids:
        decision = by_gate[gate_id]["latestDecision"]
        assert decision is not None
        assert decision["decision"] == "invalidated"
        assert decision["actor"]["classification"] == "system"
        assert decision["provenance"]["origin"] == "system"
        assert decision["supersedesDecisionId"] is not None
    for gate_id in approved_gate_ids:
        decision = by_gate[gate_id]["latestDecision"]
        assert decision is not None
        assert decision["decision"] == "approved"
        assert decision["actor"]["classification"] == "human"

    records = _decision_records(app, run_id=run_id)
    latest = {
        gate_id: max(
            (value for value in records if value["gateId"] == gate_id),
            key=lambda value: (value["revision"], value["id"]),
        )
        for gate_id in CASTING_GATE_IDS
    }
    for gate_id in invalidated_gate_ids:
        assert latest[gate_id]["decision"] == "invalidated"
        assert latest[gate_id]["actorId"] not in {None, "local_user"}
        assert '"origin":"system"' in latest[gate_id]["provenance"]
        assert latest[gate_id]["supersedesDecisionId"] is not None
    for gate_id in approved_gate_ids:
        assert latest[gate_id]["decision"] == "approved"
        assert latest[gate_id]["actorId"] == "local_user"


def test_selected_catalog_and_rights_drift_are_durably_invalidated(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    run, selected, selection_correction_ids = _prepare_approved_cast(
        app,
        client,
        auth_headers,
    )
    project_id = run["projectId"]
    run_id = run["castingRunId"]
    narrator_assignment = next(
        value for value in selected.values() if value["voiceProfileId"] == "synthetic-narrator-01"
    )
    character_assignment = next(
        value
        for value in selected.values()
        if value["voiceProfileId"].startswith("synthetic-character-")
    )
    original_catalog = app.state.casting.catalog
    try:
        unrelated_voice_id = "synthetic-character-12"
        assert unrelated_voice_id not in {value["voiceProfileId"] for value in selected.values()}
        app.state.casting.catalog = _catalog_variant(
            original_catalog,
            revision_tag="1.0.1-unrelated-test",
            voices=tuple(
                (
                    {
                        **value,
                        "displayLabel": (f"{value['displayLabel']} (unrelated metadata revision)"),
                    }
                    if value["voiceProfileId"] == unrelated_voice_id
                    else value
                )
                for value in original_catalog.voices
            ),
        )
        unrelated = _project_casting(
            client,
            auth_headers,
            project_id=project_id,
        )
        _assert_gate_states(
            unrelated,
            {gate_id: "approved" for gate_id in CASTING_GATE_IDS},
        )
        unrelated_assignments = {
            value["assignmentId"]: value
            for value in _list_assignments(client, auth_headers, run=run)
        }
        assert all(
            unrelated_assignments[value["assignmentId"]]["effective"] is True
            for value in selected.values()
        )
        assert _invalidation_records(app, run_id=run_id) == []

        app.state.casting.catalog = _catalog_variant(
            original_catalog,
            revision_tag="1.0.2-selected-narrator-drift-test",
            voices=tuple(
                (
                    {
                        **value,
                        "providerId": "synthetic-disabled-cloud",
                        "modelId": "synthetic-disabled-cloud-model",
                        "displayLabel": (
                            f"{value['displayLabel']} (selected voice/provider/model drift)"
                        ),
                    }
                    if value["voiceProfileId"] == narrator_assignment["voiceProfileId"]
                    else value
                )
                for value in original_catalog.voices
            ),
        )
        narrator_drift = _project_casting(
            client,
            auth_headers,
            project_id=project_id,
        )
        _assert_gate_states(
            narrator_drift,
            {
                _NARRATOR_GATE: "invalidated",
                _CHARACTER_GATE: "approved",
                _COMPLETE_GATE: "invalidated",
            },
        )
        drift_assignments = {
            value["assignmentId"]: value
            for value in _list_assignments(client, auth_headers, run=run)
        }
        invalidated_narrator = drift_assignments[narrator_assignment["assignmentId"]]
        assert invalidated_narrator["effective"] is False
        _assert_assignment_evidence_is_frozen(
            narrator_assignment,
            invalidated_narrator,
        )
        _assert_bounded_invalidation_warning(invalidated_narrator)
        for assignment in selected.values():
            if assignment["assignmentId"] != narrator_assignment["assignmentId"]:
                assert drift_assignments[assignment["assignmentId"]]["effective"] is True
        invalidations = _invalidation_records(app, run_id=run_id)
        assert len(invalidations) == 1
        assert invalidations[0]["assignmentId"] == narrator_assignment["assignmentId"]
        assert invalidations[0]["roleId"] == narrator_assignment["roleId"]
        assert len(invalidations[0]["evidenceFingerprint"]) == 64
        assert any(
            marker in invalidations[0]["reasonCodesJson"].casefold()
            for marker in ("catalog", "voice", "provider", "model")
        )
        assert '"origin":"system"' in invalidations[0]["provenanceJson"]
        _assert_system_invalidation_decisions(
            app,
            narrator_drift,
            run_id=run_id,
            invalidated_gate_ids={_NARRATOR_GATE, _COMPLETE_GATE},
            approved_gate_ids={_CHARACTER_GATE},
        )

        app.state.casting.catalog = original_catalog
        reverted_narrator = _project_casting(
            client,
            auth_headers,
            project_id=project_id,
        )
        _assert_gate_states(
            reverted_narrator,
            {
                _NARRATOR_GATE: "invalidated",
                _CHARACTER_GATE: "approved",
                _COMPLETE_GATE: "invalidated",
            },
        )
        reverted_assignments = {
            value["assignmentId"]: value
            for value in _list_assignments(client, auth_headers, run=run)
        }
        assert reverted_assignments[narrator_assignment["assignmentId"]]["effective"] is False
        assert len(_invalidation_records(app, run_id=run_id)) == 1

        narrator_role = next(
            value
            for value in _roles(client, auth_headers, run=run)
            if value["roleId"] == narrator_assignment["roleId"]
        )
        narrator_reselection = _select_voice(
            client,
            auth_headers,
            run=run,
            role=narrator_role,
            voice_profile_id=narrator_assignment["voiceProfileId"],
            idempotency_key="phase3a-invalidation-reselect-narrator",
            supersedes_correction_id=selection_correction_ids[narrator_assignment["roleId"]],
        )
        run = narrator_reselection["run"]
        new_narrator_assignment = narrator_reselection["assignment"]
        assert new_narrator_assignment is not None
        assert new_narrator_assignment["assignmentId"] != narrator_assignment["assignmentId"]
        assert (
            new_narrator_assignment["supersedesAssignmentId"]
            == (narrator_assignment["assignmentId"])
        )
        assert new_narrator_assignment["effective"] is True
        _approve_current_gates(
            app,
            client,
            auth_headers,
            run=run,
            key_prefix="gate-narrator",
        )
        assert {
            value["gateId"]: value["state"]
            for value in _list_reviews(client, auth_headers, run=run)
        } == {gate_id: "approved" for gate_id in CASTING_GATE_IDS}

        app.state.casting.catalog = _catalog_variant(
            original_catalog,
            revision_tag="1.0.3-selected-character-rights-test",
            rights=tuple(
                (
                    {
                        **value,
                        "revision": int(value["revision"]) + 1,
                        "state": "prohibited",
                        "commercialUsePermission": "prohibited",
                        "attributionRequirement": "prohibited",
                        "voiceCloningStatus": "prohibited",
                        "consentStatus": "prohibited",
                    }
                    if value["voiceProfileId"] == character_assignment["voiceProfileId"]
                    else value
                )
                for value in original_catalog.rights
            ),
        )
        rights_drift = _project_casting(
            client,
            auth_headers,
            project_id=project_id,
        )
        _assert_gate_states(
            rights_drift,
            {
                _NARRATOR_GATE: "approved",
                _CHARACTER_GATE: "invalidated",
                _COMPLETE_GATE: "invalidated",
            },
        )
        rights_assignments = {
            value["assignmentId"]: value
            for value in _list_assignments(client, auth_headers, run=run)
        }
        invalidated_character = rights_assignments[character_assignment["assignmentId"]]
        assert invalidated_character["effective"] is False
        _assert_assignment_evidence_is_frozen(
            character_assignment,
            invalidated_character,
        )
        current_character_rights = next(
            value
            for value in app.state.casting.catalog.rights
            if value["voiceProfileId"] == character_assignment["voiceProfileId"]
        )
        assert current_character_rights["state"] == "prohibited"
        _assert_bounded_invalidation_warning(invalidated_character)
        assert rights_assignments[new_narrator_assignment["assignmentId"]]["effective"] is True
        invalidations = _invalidation_records(app, run_id=run_id)
        assert len(invalidations) == 2
        rights_invalidation = next(
            value
            for value in invalidations
            if value["assignmentId"] == character_assignment["assignmentId"]
        )
        assert rights_invalidation["roleId"] == character_assignment["roleId"]
        assert "rights" in rights_invalidation["reasonCodesJson"].casefold()
        assert '"origin":"system"' in rights_invalidation["provenanceJson"]
        _assert_system_invalidation_decisions(
            app,
            rights_drift,
            run_id=run_id,
            invalidated_gate_ids={_CHARACTER_GATE, _COMPLETE_GATE},
            approved_gate_ids={_NARRATOR_GATE},
        )

        app.state.casting.catalog = original_catalog
        reverted_rights = _project_casting(
            client,
            auth_headers,
            project_id=project_id,
        )
        _assert_gate_states(
            reverted_rights,
            {
                _NARRATOR_GATE: "approved",
                _CHARACTER_GATE: "invalidated",
                _COMPLETE_GATE: "invalidated",
            },
        )
        reverted_rights_assignments = {
            value["assignmentId"]: value
            for value in _list_assignments(client, auth_headers, run=run)
        }
        assert (
            reverted_rights_assignments[character_assignment["assignmentId"]]["effective"] is False
        )
        assert len(_invalidation_records(app, run_id=run_id)) == 2

        character_role = next(
            value
            for value in _roles(client, auth_headers, run=run)
            if value["roleId"] == character_assignment["roleId"]
        )
        character_reselection = _select_voice(
            client,
            auth_headers,
            run=run,
            role=character_role,
            voice_profile_id=character_assignment["voiceProfileId"],
            idempotency_key="phase3a-invalidation-reselect-character",
            supersedes_correction_id=selection_correction_ids[character_assignment["roleId"]],
        )
        run = character_reselection["run"]
        new_character_assignment = character_reselection["assignment"]
        assert new_character_assignment is not None
        assert new_character_assignment["assignmentId"] != (character_assignment["assignmentId"])
        assert (
            new_character_assignment["supersedesAssignmentId"]
            == (character_assignment["assignmentId"])
        )
        assert new_character_assignment["effective"] is True
        assert new_character_assignment["rightsState"] == "verified"
        _approve_current_gates(
            app,
            client,
            auth_headers,
            run=run,
            key_prefix="gate-character",
        )
        assert {
            value["gateId"]: value["state"]
            for value in _list_reviews(client, auth_headers, run=run)
        } == {gate_id: "approved" for gate_id in CASTING_GATE_IDS}
        assert len(_invalidation_records(app, run_id=run_id)) == 2
    finally:
        app.state.casting.catalog = original_catalog


def test_parallel_reads_latch_one_assignment_and_gate_invalidation(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    run, selected, _selection_correction_ids = _prepare_approved_cast(
        app,
        client,
        auth_headers,
    )
    narrator_assignment = next(
        value for value in selected.values() if value["voiceProfileId"] == "synthetic-narrator-01"
    )
    original_catalog = app.state.casting.catalog
    try:
        app.state.casting.catalog = _catalog_variant(
            original_catalog,
            revision_tag="1.0.4-concurrent-narrator-drift-test",
            voices=tuple(
                (
                    {
                        **value,
                        "displayLabel": (f"{value['displayLabel']} (concurrent selected drift)"),
                    }
                    if value["voiceProfileId"] == narrator_assignment["voiceProfileId"]
                    else value
                )
                for value in original_catalog.voices
            ),
        )
        start = threading.Barrier(3)

        def read_project() -> tuple[int, dict[str, Any]]:
            start.wait()
            response = client.get(
                f"/api/v1/projects/{run['projectId']}",
                headers=auth_headers,
            )
            return response.status_code, response.json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(read_project)
            second = pool.submit(read_project)
            start.wait()
            outcomes = collect_concurrent_database_results([first, second])

        assert all(status == 200 for status, _body in outcomes)
        for _status, body in outcomes:
            _assert_gate_states(
                body["voiceCasting"],
                {
                    _NARRATOR_GATE: "invalidated",
                    _CHARACTER_GATE: "approved",
                    _COMPLETE_GATE: "invalidated",
                },
            )
        invalidations = _invalidation_records(
            app,
            run_id=run["castingRunId"],
        )
        assert [value["assignmentId"] for value in invalidations] == [
            narrator_assignment["assignmentId"]
        ]
        system_decisions = [
            value
            for value in _decision_records(
                app,
                run_id=run["castingRunId"],
            )
            if value["decision"] == "invalidated"
        ]
        assert [value["gateId"] for value in system_decisions] == [
            _COMPLETE_GATE,
            _NARRATOR_GATE,
        ]
    finally:
        app.state.casting.catalog = original_catalog
