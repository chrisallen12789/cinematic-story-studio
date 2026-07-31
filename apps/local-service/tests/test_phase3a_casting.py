from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import cinematic_story_service.casting as casting_module
from cinematic_story_service.casting import (
    CASTING_CONFLICT_CATEGORIES,
    CASTING_GATE_IDS,
    CASTING_JOB_STAGES,
    CASTING_PROFILE_FINGERPRINT,
    CASTING_PROFILE_ID,
    HARD_CONSTRAINT_IDS,
    MAX_CASTING_CONFLICTS,
    MAX_CASTING_WARNINGS_PER_ENTITY,
    MAX_FINAL_CANDIDATES,
    MAX_PRODUCTION_ROLES,
    SOFT_PREFERENCE_IDS,
    casting_profile,
    compatibility_assessment,
    generate_candidates,
    load_synthetic_catalog,
    production_roles,
    validate_casting_result,
    validate_catalog,
)
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.util import request_fingerprint
from tests.conftest import wait_for_job
from tests.test_phase2_api import create_phase2_run


def _approve_phase2(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    idempotency_prefix: str = "approve",
) -> tuple[dict[str, Any], dict[str, str]]:
    for gate_id in (
        "story_structure_review",
        "character_registry_review",
        "dialogue_attribution_review",
        "whole_book_analysis_review",
    ):
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews",
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        review = next(value for value in response.json()["items"] if value["gateId"] == gate_id)
        if review["state"] == "approved" and review["latestDecision"] is not None:
            continue
        decided = client.post(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews/{gate_id}/decisions",
            headers=auth_headers,
            json={
                "decision": "approve",
                "expectedRevision": review["revision"],
                "expectedArtifactFingerprint": review["artifactFingerprint"],
                "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
                "acknowledgedWarningIds": review["openWarningIds"],
                "rationale": f"Approve synthetic {gate_id} evidence.",
                "idempotencyKey": f"{idempotency_prefix}-{gate_id}",
            },
        )
        assert decided.status_code == 200, f"{gate_id}: {decided.text}; review={review}"
    reviews = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews",
        headers=auth_headers,
    )
    assert reviews.status_code == 200, reviews.text
    decisions = {
        value["gateId"]: value["latestDecision"]["decisionId"] for value in reviews.json()["items"]
    }
    run_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    )
    assert run_response.status_code == 200, run_response.text
    return run_response.json()["run"], decisions


def _create_casting_run(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    analysis_run: dict[str, Any],
    decisions: dict[str, str],
    idempotency_key: str = "create-governed-casting-run",
) -> dict[str, Any]:
    catalog_response = client.get(
        f"/api/v1/projects/{project_id}/casting/catalog",
        headers=auth_headers,
    )
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    snapshot = analysis_run["currentSnapshot"]
    response = client.post(
        f"/api/v1/projects/{project_id}/casting-runs",
        headers=auth_headers,
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
            "expectedCastingProfileFingerprint": (CASTING_PROFILE_FINGERPRINT),
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    terminal = wait_for_job(
        client,
        auth_headers,
        body["job"]["jobId"],
        {"succeeded", "failed", "cancelled", "interrupted"},
        timeout=20,
    )
    assert terminal["state"] == "succeeded", terminal
    refreshed = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{body['run']['castingRunId']}",
        headers=auth_headers,
    )
    assert refreshed.status_code == 200, refreshed.text
    return refreshed.json()["run"]


def _evidence(run: dict[str, Any]) -> dict[str, Any]:
    prerequisites = run["prerequisites"]
    return {
        "expectedRunFingerprint": run["outputFingerprint"] or run["inputFingerprint"],
        "expectedCatalogRevisionId": run["catalogRevisionId"],
        "expectedCatalogFingerprint": run["catalogFingerprint"],
        "expectedSnapshotId": prerequisites["analysisSnapshotId"],
        "expectedSnapshotRevision": prerequisites["analysisSnapshotRevision"],
        "expectedSnapshotFingerprint": prerequisites["analysisSnapshotFingerprint"],
    }


def _roles(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/roles",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _candidates(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    role: dict[str, Any],
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{run['projectId']}/casting-runs/"
        f"{run['castingRunId']}/roles/{role['roleId']}/candidates",
        headers=auth_headers,
        params={
            **_evidence(run),
            "expectedRoleRevision": role["revision"],
            "limit": 50,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _correct(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    role: dict[str, Any],
    operation: str,
    key: str,
    voice_profile_id: str | None = None,
    corrected_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _append_correction(
        client,
        auth_headers,
        run=run,
        role=role,
        operation=operation,
        key=key,
        voice_profile_id=voice_profile_id,
        corrected_value=corrected_value,
    )["run"]


def _append_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    run: dict[str, Any],
    role: dict[str, Any],
    operation: str,
    key: str,
    voice_profile_id: str | None = None,
    corrected_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{run['projectId']}/casting-runs/{run['castingRunId']}/corrections",
        headers=auth_headers,
        json={
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
            "reason": f"Synthetic governed correction: {operation}.",
            "supersedesCorrectionId": None,
            "idempotencyKey": key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _catalog_variant(
    catalog: Any,
    *,
    voices: tuple[dict[str, Any], ...] | None = None,
    rights: tuple[dict[str, Any], ...] | None = None,
) -> Any:
    next_voices = voices or catalog.voices
    next_rights = rights or catalog.rights
    revision = {
        **catalog.revision,
        "catalogRevisionId": "synthetic-voice-catalog-v1@1.0.1-test",
        "revision": 2,
        "semanticVersion": "1.0.1-test",
    }
    fingerprint = request_fingerprint(
        {
            "revision": {
                key: value for key, value in revision.items() if key != "catalogFingerprint"
            },
            "providers": catalog.providers,
            "models": catalog.models,
            "voices": next_voices,
            "rights": next_rights,
        }
    )
    revision["catalogFingerprint"] = fingerprint
    return replace(
        catalog,
        revision=revision,
        voices=next_voices,
        rights=next_rights,
        fingerprint=fingerprint,
    )


def _append_phase2_label_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    analysis_run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    run_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{analysis_run_id}",
        headers=auth_headers,
    )
    assert run_response.status_code == 200, run_response.text
    analysis_run = run_response.json()["run"]
    chapters_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{analysis_run_id}/entities/chapters",
        headers=auth_headers,
        params={"limit": 1},
    )
    assert chapters_response.status_code == 200, chapters_response.text
    chapter = chapters_response.json()["items"][0]
    corrected = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{analysis_run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "structure_label",
            "targetCollection": "chapters",
            "targetEntityId": chapter["entityId"],
            "expectedTargetRevision": chapter["effectiveRevision"],
            "expectedRunFingerprint": analysis_run["runFingerprint"],
            "previousValueFingerprint": chapter["effectiveValueFingerprint"],
            "patch": {"title": "Synthetic corrected chapter label"},
            "reason": "Exercise stale downstream evidence safely.",
            "idempotencyKey": idempotency_key,
        },
    )
    assert corrected.status_code == 200, corrected.text
    return corrected.json()


def test_casting_profile_catalog_and_rule_sets_are_deterministic() -> None:
    profile = casting_profile()
    catalog = load_synthetic_catalog()

    assert profile["fingerprint"] == CASTING_PROFILE_FINGERPRINT
    assert profile["values"]["profileId"] == CASTING_PROFILE_ID
    assert profile == casting_profile()
    assert catalog.fingerprint == (
        "68d116d1f66e4ea4bcceabfd0520fd889cf9da3074ee1b9186c43c285575c25f"
    )
    assert len(catalog.providers) == 2
    assert len(catalog.models) == 5
    assert len(catalog.voices) == len(catalog.rights) == 14
    assert {value["state"] for value in catalog.rights} == {
        "verified",
        "restricted",
        "unknown",
        "prohibited",
    }
    assert {
        "active",
        "unavailable",
        "deprecated",
    } <= {value["state"] for value in catalog.voices}
    assert len(HARD_CONSTRAINT_IDS) == 9
    assert len(SOFT_PREFERENCE_IDS) == 10
    assert len(CASTING_CONFLICT_CATEGORIES) == 11
    assert len(CASTING_JOB_STAGES) == 9
    assert len(CASTING_GATE_IDS) == 3
    for provider in catalog.providers:
        assert {
            "providerId",
            "providerVersion",
            "providerType",
            "runtimeAvailability",
            "catalogAvailability",
            "synthesisImplemented",
            "networkUseRequired",
            "credentialsRequired",
            "supportedOperatingSystems",
            "supportedLanguages",
            "outputCapability",
            "rightsMetadataCapabilities",
            "healthStatus",
            "provenance",
        } <= set(provider)
        assert not any("credentialValue" in key for key in provider)
    for model in catalog.models:
        assert {
            "modelId",
            "providerId",
            "modelName",
            "modelVersion",
            "capability",
            "executionLocation",
            "licenseClassification",
            "availability",
            "deprecated",
            "provenance",
        } <= set(model)
        assert {
            "supportedLanguages",
            "supportedLocales",
            "expressiveControls",
            "speakingRateRange",
            "pitchControl",
            "styleControl",
            "outputCapability",
        } <= set(model["capability"])
    assert all("celebrity" not in str(value).casefold() for value in catalog.voices)
    assert all("manuscript" not in str(value).casefold() for value in catalog.to_wire().values())


def test_catalog_validation_rejects_structural_drift_with_valid_fingerprints() -> None:
    catalog_payload = copy.deepcopy(load_synthetic_catalog().to_wire())

    def refingerprint(value: dict[str, Any]) -> None:
        fingerprint_input = copy.deepcopy(value)
        fingerprint_input.pop("fingerprint", None)
        fingerprint_input["catalogRevision"].pop("catalogFingerprint", None)
        fingerprint = request_fingerprint(fingerprint_input)
        value["fingerprint"] = fingerprint
        value["catalogRevision"]["catalogFingerprint"] = fingerprint

    def assert_invalid(value: dict[str, Any]) -> None:
        with pytest.raises(ServiceError) as error:
            validate_catalog(value)
        assert error.value.code == "VOICE_CATALOG_INVALID"

    assert validate_catalog(catalog_payload).fingerprint == catalog_payload["fingerprint"]

    missing_required_field = copy.deepcopy(catalog_payload)
    missing_required_field["voices"][0].pop("consentStatus")
    refingerprint(missing_required_field)
    assert_invalid(missing_required_field)

    duplicate_provider = copy.deepcopy(catalog_payload)
    duplicate_provider["providers"][1]["providerId"] = duplicate_provider["providers"][0][
        "providerId"
    ]
    refingerprint(duplicate_provider)
    assert_invalid(duplicate_provider)

    unexpected_nested_field = copy.deepcopy(catalog_payload)
    unexpected_nested_field["models"][0]["capability"]["undocumentedControl"] = True
    refingerprint(unexpected_nested_field)
    assert_invalid(unexpected_nested_field)

    malformed_effective_date = copy.deepcopy(catalog_payload)
    malformed_effective_date["rights"][0]["effectiveDate"] = "not-a-timestamp"
    refingerprint(malformed_effective_date)
    assert_invalid(malformed_effective_date)

    malformed_catalog_timestamp = copy.deepcopy(catalog_payload)
    malformed_catalog_timestamp["catalogRevision"]["createdAt"] = "not-a-timestamp"
    refingerprint(malformed_catalog_timestamp)
    assert_invalid(malformed_catalog_timestamp)

    reversed_rights_window = copy.deepcopy(catalog_payload)
    reversed_rights_window["rights"][0]["expiresAt"] = "2025-12-31T00:00:00Z"
    refingerprint(reversed_rights_window)
    assert_invalid(reversed_rights_window)


def test_production_roles_require_explicit_approved_analysis_evidence() -> None:
    entities = [
        {
            "id": "chapter-entity",
            "collection": "chapters",
            "ordinal": 0,
            "payload": {"chapterId": "chapter-1"},
        },
        {
            "id": "scene-entity",
            "collection": "scenes",
            "ordinal": 0,
            "payload": {"sceneId": "scene-1", "chapterId": "chapter-1"},
        },
        {
            "id": "character-mara",
            "collection": "characters",
            "ordinal": 0,
            "payload": {
                "characterId": "phase2-character-mara",
                "registryCharacterId": "mara",
                "canonicalName": "Mara",
                "kind": "person",
                "roleImportance": "major",
            },
        },
        {
            "id": "character-crowd",
            "collection": "characters",
            "ordinal": 1,
            "payload": {
                "characterId": "phase2-character-crowd",
                "registryCharacterId": "crowd",
                "canonicalName": "Observatory crowd",
                "kind": "group",
            },
        },
        {
            "id": "character-silent",
            "collection": "characters",
            "ordinal": 2,
            "payload": {
                "characterId": "phase2-character-silent",
                "registryCharacterId": "silent",
                "canonicalName": "Silent observer",
                "kind": "person",
                "roleImportance": "major",
            },
        },
        {
            "id": "dialogue-mara",
            "collection": "dialogue-lines",
            "ordinal": 0,
            "payload": {
                "chapterId": "chapter-1",
                "sceneId": "scene-1",
                "effectiveSpeakerId": "phase2-character-mara",
                "speakerState": "corrected",
                "distinction": "spoken_dialogue",
                "language": "es",
                "locale": "es-MX",
                "sourceSpan": {"startOffset": 0, "endOffset": 60},
            },
        },
        {
            "id": "dialogue-unresolved",
            "collection": "dialogue-lines",
            "ordinal": 1,
            "payload": {
                "chapterId": "chapter-1",
                "sceneId": "scene-1",
                "speakerState": "unknown",
                "distinction": "unresolved_speech",
                "sourceSpan": {"startOffset": 60, "endOffset": 120},
            },
        },
        {
            "id": "dialogue-group",
            "collection": "dialogue-lines",
            "ordinal": 2,
            "payload": {
                "chapterId": "chapter-1",
                "sceneId": "scene-1",
                "effectiveSpeakerId": "phase2-character-crowd",
                "speakerState": "proposed",
                "distinction": "spoken_dialogue",
                "language": None,
                "locale": None,
                "sourceSpan": {"startOffset": 120, "endOffset": 180},
            },
        },
        {
            "id": "narration",
            "collection": "narration-spans",
            "ordinal": 0,
            "payload": {
                "chapterId": "chapter-1",
                "sceneId": "scene-1",
                "classification": "direct_narration",
                "narratorCharacterId": None,
                "sourceSpan": {"startOffset": 180, "endOffset": 240},
            },
        },
        {
            "id": "secondary-narration",
            "collection": "narration-spans",
            "ordinal": 1,
            "payload": {
                "chapterId": "chapter-1",
                "sceneId": "scene-1",
                "classification": "direct_narration",
                "narratorCharacterId": "phase2-character-mara",
                "sourceSpan": {"startOffset": 240, "endOffset": 300},
            },
        },
        {
            "id": "quoted",
            "collection": "narration-spans",
            "ordinal": 2,
            "payload": {
                "chapterId": "chapter-1",
                "sceneId": "scene-1",
                "classification": "epigraph_or_document",
                "narratorCharacterId": None,
                "sourceSpan": {"startOffset": 300, "endOffset": 360},
            },
        },
        {
            "id": "thought",
            "collection": "narration-spans",
            "ordinal": 3,
            "payload": {
                "chapterId": "chapter-1",
                "sceneId": "scene-1",
                "classification": "internal_thought",
                "narratorCharacterId": "mara",
                "sourceSpan": {"startOffset": 360, "endOffset": 420},
            },
        },
    ]
    roles = production_roles(
        project_id="project-synthetic",
        casting_evidence_fingerprint="1" * 64,
        analysis_run_id="analysis-run-synthetic",
        snapshot_id="snapshot-synthetic",
        snapshot_fingerprint="1" * 64,
        entities=entities,
    )
    by_type = {value["roleType"]: value for value in roles}
    assert set(by_type) == {
        "primary_narrator",
        "secondary_narrator",
        "named_character",
        "unresolved_speaker",
        "group_or_crowd",
        "quoted_document_or_announcement",
        "internal_thought",
    }
    assert by_type["named_character"]["dialogueLineCount"] == 1
    assert by_type["primary_narrator"]["narrationSpanCount"] == 1
    assert by_type["secondary_narrator"]["narrationSpanCount"] == 1
    assert by_type["named_character"]["chapterRange"] == {
        "firstOrdinal": 1,
        "lastOrdinal": 1,
    }
    assert by_type["unresolved_speaker"]["warnings"][0]["code"] == ("UNRESOLVED_SPEAKER_ROLE")
    assert by_type["named_character"]["characterId"] == "mara"
    assert by_type["named_character"]["analysisEntityId"] == "character-mara"
    assert (
        by_type["named_character"]["characterId"] != by_type["named_character"]["analysisEntityId"]
    )
    assert by_type["named_character"]["roleImportance"] == "major"
    assert by_type["group_or_crowd"]["characterId"] == "crowd"
    assert by_type["group_or_crowd"]["roleImportance"] == "supporting"
    assert by_type["unresolved_speaker"]["roleImportance"] == "unresolved"
    assert not any(value["analysisEntityId"] == "character-silent" for value in roles)
    assert all(
        value["dialogueLineCount"] > 0 for value in roles if value["roleType"] == "named_character"
    )
    assert by_type["named_character"]["languageRequirements"] == ["es"]
    assert by_type["named_character"]["performanceRequirements"]["language"] == "es"
    assert by_type["named_character"]["performanceRequirements"]["locales"] == ["es-MX"]
    assert by_type["primary_narrator"]["languageRequirements"] == ["es"]
    assert by_type["group_or_crowd"]["languageRequirements"] == ["und"]
    assert by_type["group_or_crowd"]["performanceRequirements"]["language"] == "und"
    assert by_type["group_or_crowd"]["performanceRequirements"]["locales"] == []
    assert all(value["projectId"] == "project-synthetic" for value in roles)
    assert roles == production_roles(
        project_id="project-synthetic",
        casting_evidence_fingerprint="1" * 64,
        analysis_run_id="analysis-run-synthetic",
        snapshot_id="snapshot-synthetic",
        snapshot_fingerprint="1" * 64,
        entities=entities,
    )


def test_production_role_limit_counts_emitted_roles_not_raw_evidence() -> None:
    repeated_narration = [
        {
            "id": f"narration-{ordinal}",
            "collection": "narration-spans",
            "ordinal": ordinal,
            "payload": {
                "classification": "direct_narration",
                "narratorCharacterId": None,
                "sourceSpan": {
                    "startOffset": ordinal * 6,
                    "endOffset": ordinal * 6 + 6,
                },
            },
        }
        for ordinal in range(MAX_PRODUCTION_ROLES + 1)
    ]
    roles = production_roles(
        project_id="project-many-narration-spans",
        casting_evidence_fingerprint="2" * 64,
        analysis_run_id="analysis-run-many-narration-spans",
        snapshot_id="snapshot-many-narration-spans",
        snapshot_fingerprint="2" * 64,
        entities=[
            {
                "id": "silent-character",
                "collection": "characters",
                "ordinal": 0,
                "payload": {
                    "registryCharacterId": "silent",
                    "canonicalName": "Silent",
                },
            },
            *repeated_narration,
        ],
    )
    assert len(roles) == 1
    assert roles[0]["narrationSpanCount"] == MAX_PRODUCTION_ROLES + 1

    speaking_entities: list[dict[str, Any]] = []
    for ordinal in range(MAX_PRODUCTION_ROLES):
        character_id = f"speaker-{ordinal:03d}"
        speaking_entities.extend(
            [
                {
                    "id": f"character-{ordinal:03d}",
                    "collection": "characters",
                    "ordinal": ordinal,
                    "payload": {
                        "registryCharacterId": character_id,
                        "canonicalName": f"Speaker {ordinal}",
                    },
                },
                {
                    "id": f"dialogue-{ordinal:03d}",
                    "collection": "dialogue-lines",
                    "ordinal": ordinal,
                    "payload": {
                        "effectiveSpeakerId": character_id,
                        "speakerState": "proposed",
                        "distinction": "spoken_dialogue",
                        "sourceSpan": {
                            "startOffset": ordinal * 6,
                            "endOffset": ordinal * 6 + 6,
                        },
                    },
                },
            ]
        )
    with pytest.raises(ServiceError) as error:
        production_roles(
            project_id="project-too-many-emitted-roles",
            casting_evidence_fingerprint="3" * 64,
            analysis_run_id="analysis-run-too-many-emitted-roles",
            snapshot_id="snapshot-too-many-emitted-roles",
            snapshot_fingerprint="3" * 64,
            entities=speaking_entities,
        )
    assert error.value.code == "CASTING_ROLE_LIMIT_EXCEEDED"


def test_candidate_generation_is_bounded_explainable_and_deterministic() -> None:
    catalog = load_synthetic_catalog()
    role = {
        "roleId": "synthetic-role",
        "roleType": "primary_narrator",
        "languageRequirements": ["en"],
        "locale": "en-US",
        "performanceRequirements": {"longFormRequired": True},
    }
    first_candidates, first_conflicts = generate_candidates(
        roles=[role],
        catalog=catalog,
        input_fingerprint="a" * 64,
    )
    second_candidates, second_conflicts = generate_candidates(
        roles=[role],
        catalog=catalog,
        input_fingerprint="a" * 64,
    )

    assert first_candidates == second_candidates
    assert first_conflicts == second_conflicts
    assert len(first_candidates) <= MAX_FINAL_CANDIDATES
    assert {value["rightsEligibility"] for value in first_candidates} >= {
        "verified",
        "restricted",
        "unknown",
        "prohibited",
    }
    assert any(value["languageEligibility"] == "ineligible" for value in first_candidates)
    assert any(value["providerAvailability"] is False for value in first_candidates)
    assert any(value["modelAvailability"] is False for value in first_candidates)
    assert any(value["longFormSuitability"] is False for value in first_candidates)
    assert any(value["voiceProfileId"] == "synthetic-character-06" for value in first_candidates)
    assert any(
        value["voiceProfileId"] == "synthetic-character-07"
        and value["compatibilityStatus"] == "ineligible"
        for value in first_candidates
    )
    assert any(
        value["voiceProfileId"] == "synthetic-character-08"
        and value["compatibilityStatus"] in {"ineligible", "unknown"}
        for value in first_candidates
    )
    for candidate in first_candidates:
        assert {value["ruleId"] for value in candidate["hardConstraintResults"]} == set(
            HARD_CONSTRAINT_IDS
        )
        assert {value["ruleId"] for value in candidate["softPreferenceResults"]} == set(
            SOFT_PREFERENCE_IDS
        )
        assert "artistic" in candidate["explanation"]
        assert "acoustic" in candidate["explanation"]


def test_candidate_rights_eligibility_is_frozen_to_catalog_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_synthetic_catalog()
    rights = tuple(copy.deepcopy(value) for value in catalog.rights)
    target = next(
        value for value in rights if value["voiceProfileId"] == "synthetic-narrator-01"
    )
    target["effectiveDate"] = "2025-01-01T00:00:00Z"
    target["expiresAt"] = "2027-01-01T00:00:00Z"
    bounded_catalog = replace(catalog, rights=rights)
    role = {
        "roleId": "temporal-rights-role",
        "roleType": "primary_narrator",
        "languageRequirements": ["en"],
        "locale": "en-US",
        "performanceRequirements": {"longFormRequired": True},
    }

    class BeforeExpiry(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2026, 6, 1, tzinfo=UTC)

    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2028, 6, 1, tzinfo=UTC)

    monkeypatch.setattr(casting_module, "datetime", BeforeExpiry)
    before = generate_candidates(
        roles=[role],
        catalog=bounded_catalog,
        input_fingerprint="e" * 64,
    )
    monkeypatch.setattr(casting_module, "datetime", AfterExpiry)
    after = generate_candidates(
        roles=[role],
        catalog=bounded_catalog,
        input_fingerprint="e" * 64,
    )

    assert before == after
    selected = next(
        value for value in before[0] if value["voiceProfileId"] == "synthetic-narrator-01"
    )
    assert selected["rightsEligibility"] == "verified"


def test_metadata_similarity_conflict_binds_published_role_voice_pairs() -> None:
    catalog = load_synthetic_catalog()
    roles = [
        {
            "roleId": "metadata-primary-role",
            "roleType": "primary_narrator",
            "languageRequirements": ["en"],
            "approximateWordCount": 100,
            "performanceRequirements": {"longFormRequired": False},
        },
        {
            "roleId": "metadata-character-role",
            "roleType": "named_character",
            "roleImportance": "supporting",
            "languageRequirements": ["en"],
            "approximateWordCount": 100,
            "performanceRequirements": {"longFormRequired": False},
        },
    ]
    candidates, conflicts = generate_candidates(
        roles=roles,
        catalog=catalog,
        input_fingerprint="b" * 64,
    )
    conflict = next(value for value in conflicts if value["category"] == "metadata_similarity_risk")
    referenced_role_ids = [
        conflict["primaryRoleId"],
        *conflict["relatedRoleIds"],
    ]
    published_by_role = {
        role_id: {value["voiceProfileId"] for value in candidates if value["roleId"] == role_id}
        for role_id in referenced_role_ids
    }
    assert len(referenced_role_ids) == 2
    assert len(conflict["voiceProfileIds"]) == 2
    assert conflict["voiceProfileIds"][0] in published_by_role[referenced_role_ids[0]]
    assert conflict["voiceProfileIds"][1] in published_by_role[referenced_role_ids[1]]
    assert conflict["voiceProfileIds"][0] != conflict["voiceProfileIds"][1]
    assert conflict["metadataBased"] is True
    assert conflict["acousticSimilarityClaimed"] is False


def test_compatibility_preserves_unknown_and_exact_hard_failures() -> None:
    catalog = load_synthetic_catalog()
    role = {
        "roleId": "synthetic-supporting-role",
        "roleType": "named_character",
        "roleImportance": "supporting",
        "languageRequirements": ["en"],
        "approximateWordCount": 500,
        "performanceRequirements": {
            "language": "en",
            "locales": ["en-US"],
            "agePresentationRange": None,
            "vocalPresentations": [],
            "preferredTextures": [],
            "speakingRateRange": None,
            "requiredExpressiveRange": [],
            "longFormRequired": False,
        },
    }
    providers = {value["providerId"]: value for value in catalog.providers}
    models = {value["modelId"]: value for value in catalog.models}
    voices = {value["voiceProfileId"]: value for value in catalog.voices}
    rights = {value["voiceProfileId"]: value for value in catalog.rights}

    def assess(voice_id: str) -> dict[str, Any]:
        voice = voices[voice_id]
        return compatibility_assessment(
            role=role,
            voice=voice,
            rights=rights[voice_id],
            provider=providers[voice["providerId"]],
            model=models[voice["modelId"]],
            input_fingerprint="f" * 64,
        )

    unknown = assess("synthetic-character-08")
    assert unknown["compatibilityStatus"] == "unknown"
    assert (
        next(
            value for value in unknown["hardConstraintResults"] if value["ruleId"] == "rights_known"
        )["result"]
        == "unknown"
    )
    assert (
        next(
            value
            for value in unknown["hardConstraintResults"]
            if value["ruleId"] == "required_consent"
        )["result"]
        == "unknown"
    )

    unresolved_language_role = copy.deepcopy(role)
    unresolved_language_role["languageRequirements"] = ["und"]
    unresolved_language_role["performanceRequirements"]["language"] = "und"
    voice = voices["synthetic-character-01"]
    unresolved_language = compatibility_assessment(
        role=unresolved_language_role,
        voice=voice,
        rights=rights["synthetic-character-01"],
        provider=providers[voice["providerId"]],
        model=models[voice["modelId"]],
        input_fingerprint="f" * 64,
    )
    assert unresolved_language["compatibilityStatus"] == "unknown"
    assert unresolved_language["languageEligibility"] == "unknown"
    assert (
        next(
            value
            for value in unresolved_language["hardConstraintResults"]
            if value["ruleId"] == "language_support"
        )["result"]
        == "unknown"
    )
    corrected_language = assess("synthetic-character-01")
    assert corrected_language["compatibilityStatus"] == "eligible"
    assert corrected_language["languageEligibility"] == "eligible"

    exact_failures = {
        "synthetic-character-03": "language_support",
        "synthetic-character-05": "model_available",
        "synthetic-character-07": "rights_not_prohibited",
        "synthetic-character-09": "provider_available",
        "synthetic-character-12": "voice_not_blocked",
    }
    for voice_id, rule_id in exact_failures.items():
        assessment = assess(voice_id)
        assert assessment["compatibilityStatus"] == "ineligible"
        assert (
            next(
                value for value in assessment["hardConstraintResults"] if value["ruleId"] == rule_id
            )["result"]
            == "fail"
        )

    synthetic_fixture = assess("synthetic-character-01")
    assert (
        next(
            value
            for value in synthetic_fixture["hardConstraintResults"]
            if value["ruleId"] == "required_consent"
        )["result"]
        == "pass"
    )
    prohibited = assess("synthetic-character-07")
    assert (
        next(
            value
            for value in prohibited["hardConstraintResults"]
            if value["ruleId"] == "required_consent"
        )["result"]
        == "fail"
    )

    voice = voices["synthetic-character-01"]
    expired_rights = copy.deepcopy(rights["synthetic-character-01"])
    expired_rights["effectiveDate"] = None
    expired_rights["expiresAt"] = "2000-01-01T00:00:00Z"
    expired = compatibility_assessment(
        role=role,
        voice=voice,
        rights=expired_rights,
        provider=providers[voice["providerId"]],
        model=models[voice["modelId"]],
        input_fingerprint="f" * 64,
    )
    assert expired["compatibilityStatus"] == "ineligible"
    assert (
        next(
            value for value in expired["hardConstraintResults"] if value["ruleId"] == "rights_known"
        )["result"]
        == "fail"
    )


def test_casting_checkpoint_validation_rejects_forged_structure_and_bounds() -> None:
    input_fingerprint = "c" * 64
    catalog = load_synthetic_catalog()
    roles = production_roles(
        project_id="project-checkpoint-validation",
        casting_evidence_fingerprint=input_fingerprint,
        analysis_run_id="analysis-run-checkpoint-validation",
        snapshot_id="snapshot-checkpoint-validation",
        snapshot_fingerprint="d" * 64,
        entities=[
            {
                "id": "checkpoint-character-entity",
                "collection": "characters",
                "ordinal": 0,
                "payload": {
                    "registryCharacterId": "checkpoint-character",
                    "canonicalName": "Checkpoint character",
                    "language": "en",
                    "roleImportance": "supporting",
                },
            },
            {
                "id": "checkpoint-dialogue",
                "collection": "dialogue-lines",
                "ordinal": 0,
                "payload": {
                    "effectiveSpeakerId": "checkpoint-character",
                    "speakerState": "proposed",
                    "distinction": "spoken_dialogue",
                    "language": "en",
                    "locale": "en-US",
                    "sourceSpan": {"startOffset": 0, "endOffset": 60},
                },
            },
            {
                "id": "checkpoint-narration",
                "collection": "narration-spans",
                "ordinal": 0,
                "payload": {
                    "classification": "direct_narration",
                    "narratorCharacterId": None,
                    "language": "en",
                    "locale": "en-US",
                    "sourceSpan": {"startOffset": 60, "endOffset": 120},
                },
            },
        ],
    )
    candidates, conflicts = generate_candidates(
        roles=roles,
        catalog=catalog,
        input_fingerprint=input_fingerprint,
    )
    result_values = {
        "contractVersion": "3.0.0",
        "castingRunId": "checkpoint-validation-run",
        "inputFingerprint": input_fingerprint,
        "catalogRevisionId": catalog.revision_id,
        "catalogFingerprint": catalog.fingerprint,
        "castingProfileFingerprint": CASTING_PROFILE_FINGERPRINT,
        "stages": list(CASTING_JOB_STAGES),
        "roles": roles,
        "candidates": candidates,
        "conflicts": conflicts,
    }
    valid_result = result_values | {"outputFingerprint": request_fingerprint(result_values)}

    def validate(value: dict[str, Any]) -> None:
        validate_casting_result(
            value,
            expected_input_fingerprint=input_fingerprint,
            expected_catalog_fingerprint=catalog.fingerprint,
            expected_profile_fingerprint=CASTING_PROFILE_FINGERPRINT,
        )

    def refingerprint_result(value: dict[str, Any]) -> None:
        fingerprint_values = dict(value)
        fingerprint_values.pop("outputFingerprint", None)
        value["outputFingerprint"] = request_fingerprint(fingerprint_values)

    def refingerprint_record(value: dict[str, Any], field: str) -> None:
        fingerprint_values = dict(value)
        fingerprint_values.pop(field, None)
        value[field] = request_fingerprint(fingerprint_values)

    def refingerprint_candidate(value: dict[str, Any]) -> None:
        assessment_fields = (
            "compatibilityStatus",
            "compatibilityScore",
            "confidenceClassification",
            "hardConstraintResults",
            "softPreferenceResults",
            "rightsEligibility",
            "languageEligibility",
            "providerAvailability",
            "modelAvailability",
            "longFormSuitability",
            "explanation",
            "inputFingerprint",
        )
        value["assessmentFingerprint"] = request_fingerprint(
            {field: value[field] for field in assessment_fields}
        )
        refingerprint_record(value, "outputFingerprint")

    def assert_invalid(value: dict[str, Any]) -> None:
        with pytest.raises(ServiceError) as error:
            validate(value)
        assert error.value.code == "CASTING_OUTPUT_INVALID"

    validate(valid_result)

    bad_top_fingerprint = copy.deepcopy(valid_result)
    bad_top_fingerprint["outputFingerprint"] = "0" * 64
    assert_invalid(bad_top_fingerprint)

    empty_named_workload = copy.deepcopy(valid_result)
    named_role = next(
        value for value in empty_named_workload["roles"] if value["roleType"] == "named_character"
    )
    named_role["dialogueLineCount"] = 0
    refingerprint_record(named_role, "roleFingerprint")
    refingerprint_result(empty_named_workload)
    assert_invalid(empty_named_workload)

    duplicate_candidate = copy.deepcopy(valid_result)
    duplicate_candidate["candidates"].append(copy.deepcopy(duplicate_candidate["candidates"][0]))
    refingerprint_result(duplicate_candidate)
    assert_invalid(duplicate_candidate)

    unknown_role_reference = copy.deepcopy(valid_result)
    unknown_role_reference["candidates"][0]["roleId"] = "unknown-role"
    refingerprint_candidate(unknown_role_reference["candidates"][0])
    refingerprint_result(unknown_role_reference)
    assert_invalid(unknown_role_reference)

    incomplete_rule_array = copy.deepcopy(valid_result)
    incomplete_rule_array["candidates"][0]["hardConstraintResults"].pop()
    refingerprint_candidate(incomplete_rule_array["candidates"][0])
    refingerprint_result(incomplete_rule_array)
    assert_invalid(incomplete_rule_array)

    assert valid_result["conflicts"]
    unpublished_conflict_voice = copy.deepcopy(valid_result)
    unpublished_conflict_voice["conflicts"][0]["voiceProfileIds"] = ["not-a-published-candidate"]
    refingerprint_record(
        unpublished_conflict_voice["conflicts"][0],
        "conflictFingerprint",
    )
    refingerprint_result(unpublished_conflict_voice)
    assert_invalid(unpublished_conflict_voice)

    too_many_conflicts = copy.deepcopy(valid_result)
    too_many_conflicts["conflicts"] = [
        copy.deepcopy(valid_result["conflicts"][0]) for _ in range(MAX_CASTING_CONFLICTS + 1)
    ]
    refingerprint_result(too_many_conflicts)
    assert_invalid(too_many_conflicts)


def test_phase3a_api_persists_assignments_conflicts_and_three_gates(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase3a-full",
    )
    project_id = imported["project"]["projectId"]
    analysis_run, phase2_decisions = _approve_phase2(
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
        decisions=phase2_decisions,
    )
    assert run["status"] == "succeeded"
    assert run["profile"]["fingerprint"] == CASTING_PROFILE_FINGERPRINT
    assert run["summary"]["productionRoles"] >= 3
    assert run["summary"]["finalCandidates"] <= (
        run["summary"]["productionRoles"] * MAX_FINAL_CANDIDATES
    )
    initial_conflicts = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/conflicts",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert initial_conflicts.status_code == 200, initial_conflicts.text
    for conflict in initial_conflicts.json()["items"]:
        projection = dict(conflict)
        output_fingerprint = projection.pop("outputFingerprint")
        assert output_fingerprint == request_fingerprint(projection)
        assert len(conflict["baseEvidenceFingerprint"]) == 64
    candidate_only_warning_ids = {
        f"casting-conflict:{value['conflictId']}"
        for value in initial_conflicts.json()["items"]
        if value["voiceProfileIds"]
    }
    initial_snapshot = run["approvedCastSnapshot"]
    initial_reviews = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/reviews",
        headers=auth_headers,
        params={
            **_evidence(run),
            "expectedApprovedCastSnapshotId": initial_snapshot["snapshotId"],
            "expectedApprovedCastSnapshotRevision": initial_snapshot["revision"],
        },
    )
    assert initial_reviews.status_code == 200, initial_reviews.text
    for review in initial_reviews.json()["items"]:
        assert len(review["openWarningIds"]) <= MAX_CASTING_WARNINGS_PER_ENTITY
        assert candidate_only_warning_ids.isdisjoint(review["openWarningIds"])

    roles = _roles(client, auth_headers, run=run)
    initial_candidates = [
        candidate
        for role in roles
        for candidate in _candidates(
            client,
            auth_headers,
            run=run,
            role=role,
        )
    ]
    warned_candidates = [value for value in initial_candidates if value["conflictIds"]]
    assert warned_candidates
    for candidate in initial_candidates:
        candidate_projection = dict(candidate)
        candidate_output_fingerprint = candidate_projection.pop("outputFingerprint")
        assert candidate_output_fingerprint == request_fingerprint(candidate_projection)
        assert len(candidate["baseEvidenceFingerprint"]) == 64
        assessment_projection = dict(candidate["assessment"])
        assessment_output_fingerprint = assessment_projection.pop("outputFingerprint")
        assert assessment_output_fingerprint == request_fingerprint(assessment_projection)
        assert len(candidate["assessment"]["baseEvidenceFingerprint"]) == 64
    for candidate in warned_candidates:
        assert candidate["conflictWarnings"]
        assert len(candidate["conflictWarnings"]) <= (MAX_CASTING_WARNINGS_PER_ENTITY)
        for warning in candidate["conflictWarnings"]:
            assert warning["code"].startswith("CASTING_CONFLICT_")
            assert warning["requiresHumanReview"] is True
            assert warning["relatedEntityIds"]
            assert warning["evidence"] == []
    narrator = next(value for value in roles if value["roleType"] == "primary_narrator")
    characters = [value for value in roles if value["roleType"] == "named_character"]
    assert len(characters) >= 2
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
    for index, (original_role, voice_id) in enumerate(selections):
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
                key=f"set-supported-language-{index}",
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
                for value in _roles(
                    client,
                    auth_headers,
                    run=run,
                )
                if value["roleId"] == original_role["roleId"]
            )
        candidate_ids = {
            value["voiceProfileId"]
            for value in _candidates(
                client,
                auth_headers,
                run=run,
                role=role,
            )
        }
        assert voice_id in candidate_ids
        selection = _append_correction(
            client,
            auth_headers,
            run=run,
            role=role,
            operation="select_voice",
            key=f"select-{index}",
            voice_profile_id=voice_id,
            corrected_value={
                "voiceProfileId": voice_id,
            },
        )
        run = selection["run"]
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
            operation="lock_assignment",
            key=f"lock-{index}",
            corrected_value={
                "assignmentId": selection["assignment"]["assignmentId"],
            },
        )
    selected_role_ids = {role["roleId"] for role, _ in selections}
    for index, original_role in enumerate(
        role for role in roles if role["roleId"] not in selected_role_ids
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
            key=f"intentionally-uncast-{index}",
        )

    conflicts_response = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/conflicts",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert conflicts_response.status_code == 200, conflicts_response.text
    conflicts = conflicts_response.json()["items"]
    assert any(value["category"] == "metadata_similarity_risk" for value in conflicts)
    narrator = next(
        value
        for value in _roles(client, auth_headers, run=run)
        if value["roleType"] == "primary_narrator"
    )
    first_conflict = next(
        value
        for value in conflicts
        if value["category"] == "metadata_similarity_risk" and value["resolutionState"] == "open"
    )
    run = _correct(
        client,
        auth_headers,
        run=run,
        role=narrator,
        operation="approve_voice_reuse",
        key="approve-metadata-conflict",
        corrected_value={
            "conflictId": first_conflict["conflictId"],
            "approvedRoleIds": first_conflict["roleIds"],
        },
    )
    resolved_conflicts_response = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/conflicts",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert resolved_conflicts_response.status_code == 200
    resolved_conflict = next(
        value
        for value in resolved_conflicts_response.json()["items"]
        if value["conflictId"] == first_conflict["conflictId"]
    )
    assert resolved_conflict["resolutionState"] == "approved_reuse"
    assert (
        resolved_conflict["baseEvidenceFingerprint"]
        == first_conflict["baseEvidenceFingerprint"]
    )
    assert resolved_conflict["outputFingerprint"] != first_conflict["outputFingerprint"]
    resolved_projection = dict(resolved_conflict)
    resolved_output_fingerprint = resolved_projection.pop("outputFingerprint")
    assert resolved_output_fingerprint == request_fingerprint(resolved_projection)

    reviews_url = f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/reviews"
    for gate_id in CASTING_GATE_IDS:
        snapshot = run["approvedCastSnapshot"]
        listed = client.get(
            reviews_url,
            headers=auth_headers,
            params={
                **_evidence(run),
                "expectedApprovedCastSnapshotId": snapshot["snapshotId"],
                "expectedApprovedCastSnapshotRevision": snapshot["revision"],
            },
        )
        assert listed.status_code == 200, listed.text
        review = next(value for value in listed.json()["items"] if value["gateId"] == gate_id)
        decided = client.post(
            f"{reviews_url}/{gate_id}/decisions",
            headers=auth_headers,
            json={
                "decision": "approve",
                "expectedRevision": review["revision"],
                "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
                "expectedRunFingerprint": run["outputFingerprint"] or run["inputFingerprint"],
                "expectedApprovedCastSnapshotId": snapshot["snapshotId"],
                "expectedApprovedCastSnapshotRevision": snapshot["revision"],
                "warningAcknowledgementIds": review["openWarningIds"],
                "rationale": f"Approve synthetic {gate_id}.",
                "supersedesDecisionId": None,
                "idempotencyKey": f"casting-approve-{gate_id}",
            },
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["decision"]["decision"] == "approved"

    detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    restored = detail.json()["voiceCasting"]
    assert restored["currentRun"]["castingRunId"] == run["castingRunId"]
    assert all(value["state"] == "approved" for value in restored["gateReviews"])
    assert restored["currentRun"]["summary"]["assignments"] >= 3
    rerun = _create_casting_run(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=analysis_run,
        decisions=phase2_decisions,
        idempotency_key="rerun-preserves-governed-casting",
    )
    assert rerun["castingRunId"] == run["castingRunId"]
    assert rerun["effectiveCorrectionSetFingerprint"] == run["effectiveCorrectionSetFingerprint"]
    assert rerun["approvedCastSnapshot"] == run["approvedCastSnapshot"]

    original_catalog = app.state.casting.catalog
    assigned_voice_ids = {voice_id for _, voice_id in selections}
    unrelated_voice = next(
        value
        for value in original_catalog.voices
        if value["voiceProfileId"] not in assigned_voice_ids
    )
    app.state.casting.catalog = _catalog_variant(
        original_catalog,
        voices=tuple(
            (
                {**value, "displayLabel": f"{value['displayLabel']} (unrelated revision)"}
                if value["voiceProfileId"] == unrelated_voice["voiceProfileId"]
                else value
            )
            for value in original_catalog.voices
        ),
    )
    unrelated_change = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    )
    assert unrelated_change.status_code == 200, unrelated_change.text
    assert all(
        value["state"] == "approved"
        for value in unrelated_change.json()["voiceCasting"]["gateReviews"]
    )

    app.state.casting.catalog = _catalog_variant(
        original_catalog,
        voices=tuple(
            (
                {**value, "displayLabel": f"{value['displayLabel']} (selected revision)"}
                if value["voiceProfileId"] == "synthetic-narrator-01"
                else value
            )
            for value in original_catalog.voices
        ),
    )
    selected_voice_change = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    )
    assert selected_voice_change.status_code == 200, selected_voice_change.text
    voice_change_states = {
        value["gateId"]: value["state"]
        for value in selected_voice_change.json()["voiceCasting"]["gateReviews"]
    }
    assert voice_change_states == {
        "narrator_casting_review": "invalidated",
        "character_casting_review": "approved",
        "complete_cast_review": "invalidated",
    }

    selected_character_voice_id = selections[1][1]
    app.state.casting.catalog = _catalog_variant(
        original_catalog,
        rights=tuple(
            (
                {**value, "state": "prohibited"}
                if value["voiceProfileId"] == selected_character_voice_id
                else value
            )
            for value in original_catalog.rights
        ),
    )
    rights_change = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    )
    assert rights_change.status_code == 200, rights_change.text
    rights_change_states = {
        value["gateId"]: value["state"]
        for value in rights_change.json()["voiceCasting"]["gateReviews"]
    }
    assert rights_change_states == {
        "narrator_casting_review": "invalidated",
        "character_casting_review": "invalidated",
        "complete_cast_review": "invalidated",
    }
    app.state.casting.catalog = original_catalog
    _append_phase2_label_correction(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run_id=analysis_run["runId"],
        idempotency_key="invalidate-casting-after-phase2-change",
    )
    stale_casting = client.get(
        f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/roles",
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert stale_casting.status_code == 409


def test_casting_freezes_current_approved_phase2_correction_overlays(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-overlay",
    )
    project_id = imported["project"]["projectId"]
    analysis_run, _decisions = _approve_phase2(
        client,
        auth_headers,
        project_id=project_id,
        run_id=created["run"]["runId"],
    )
    original_correction_fingerprint = analysis_run["currentSnapshot"]["correctionSetFingerprint"]
    _append_phase2_label_correction(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run_id=analysis_run["runId"],
        idempotency_key="phase2-label",
    )
    corrected_run, corrected_decisions = _approve_phase2(
        client,
        auth_headers,
        project_id=project_id,
        run_id=analysis_run["runId"],
        idempotency_prefix="reapprove-corrected",
    )
    corrected_fingerprint = corrected_run["currentSnapshot"]["correctionSetFingerprint"]
    assert corrected_fingerprint != original_correction_fingerprint

    casting_run = _create_casting_run(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=corrected_run,
        decisions=corrected_decisions,
        idempotency_key="cast-overlay",
    )

    assert casting_run["prerequisites"]["analysisCorrectionSetFingerprint"] == corrected_fingerprint


def test_phase3a_routes_require_authentication_and_reject_stale_evidence(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    missing = client.get("/api/v1/projects/project-1/casting/catalog")
    assert missing.status_code == 401

    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase3a-stale",
    )
    project_id = imported["project"]["projectId"]
    analysis_run, decisions = _approve_phase2(
        client,
        auth_headers,
        project_id=project_id,
        run_id=created["run"]["runId"],
    )
    catalog = client.get(
        f"/api/v1/projects/{project_id}/casting/catalog",
        headers=auth_headers,
    ).json()
    snapshot = analysis_run["currentSnapshot"]
    stale = client.post(
        f"/api/v1/projects/{project_id}/casting-runs",
        headers=auth_headers,
        json={
            "expectedAnalysisRunId": analysis_run["runId"],
            "expectedSnapshotId": snapshot["snapshotId"],
            "expectedSnapshotRevision": snapshot["revision"],
            "expectedSnapshotFingerprint": "0" * 64,
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
            "expectedCastingProfileFingerprint": (CASTING_PROFILE_FINGERPRINT),
            "idempotencyKey": "stale-casting-run",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CASTING_PHASE_2_EVIDENCE_STALE"
