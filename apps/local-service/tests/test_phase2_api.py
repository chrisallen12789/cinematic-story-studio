from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from cinematic_story_service.util import sha256_text
from cinematic_story_service.whole_book_analysis import (
    ANALYSIS_CONTRACT_VERSION,
    DEFAULT_ANALYSIS_PROFILE,
    ENTITY_COLLECTIONS,
    MAX_EVIDENCE_SPANS,
)
from tests.conftest import create_imported_project, wait_for_job

_REPOSITORY_ROOT = Path(__file__).parents[3]
_DEFINITIONS = json.loads(
    (_REPOSITORY_ROOT / "schemas" / "v2" / "definitions.schema.json").read_text(encoding="utf-8")
)["$defs"]
_ENTITY_SCHEMA_BY_COLLECTION = {
    "agent-executions": "AnalysisAgentExecution",
    "chapters": "AnalysisChapter",
    "scenes": "AnalysisScene",
    "beats": "AnalysisBeat",
    "characters": "CharacterIdentity",
    "mentions": "CharacterMention",
    "dialogue-lines": "AnalysisDialogueLine",
    "narration-spans": "NarrationSpan",
    "pov-segments": "PovSegment",
    "locations": "StoryLocation",
    "timeline-events": "TimelineEvent",
    "temporal-constraints": "TemporalConstraint",
    "relationships": "CharacterRelationship",
    "emotional-states": "EmotionalState",
    "dramatic-intents": "DramaticIntent",
    "continuity-findings": "ContinuityFinding",
}
_EXACT_TEXT_COLLECTIONS = {"mentions", "dialogue-lines", "narration-spans"}


def _schema_object_shape(schema: dict[str, Any]) -> tuple[set[str], set[str]]:
    if "$ref" in schema:
        return _schema_object_shape(_DEFINITIONS[schema["$ref"].rsplit("/", 1)[-1]])
    allowed = set(schema.get("properties", {}))
    required = set(schema.get("required", []))
    for branch in schema.get("allOf", []):
        if "if" in branch:
            continue
        branch_allowed, branch_required = _schema_object_shape(branch)
        allowed.update(branch_allowed)
        required.update(branch_required)
    return allowed, required


def _assert_schema_object(value: dict[str, Any], definition: str) -> None:
    allowed, required = _schema_object_shape(_DEFINITIONS[definition])
    assert required <= set(value), (definition, "missing", sorted(required - set(value)))
    assert set(value) <= allowed, (definition, "extra", sorted(set(value) - allowed))


def _exact_text_key_count(value: Any) -> int:
    if isinstance(value, dict):
        return int("exactText" in value) + sum(
            _exact_text_key_count(item) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_exact_text_key_count(item) for item in value)
    return 0


def _collection(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    collection: str,
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/{collection}",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()


def queue_phase2_run(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    imported: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    project_id = imported["project"]["projectId"]
    extraction = imported["extraction"]
    review = imported["review"]
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs",
        headers=auth_headers,
        json={
            "expectedExtractionId": extraction["extractionId"],
            "expectedExtractionRevision": extraction["revision"],
            "expectedReviewId": review["reviewId"],
            "expectedReviewRevision": review["revision"],
            "expectedEvidenceFingerprint": review["evidenceFingerprint"],
            "expectedProfileFingerprint": DEFAULT_ANALYSIS_PROFILE.fingerprint,
            "profile": {
                "profileId": DEFAULT_ANALYSIS_PROFILE.profile_id,
                "semanticVersion": DEFAULT_ANALYSIS_PROFILE.semantic_version,
                "fingerprint": DEFAULT_ANALYSIS_PROFILE.fingerprint,
            },
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def create_phase2_run(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    idempotency_key: str = "phase2-run",
    story_bytes: bytes | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    imported = create_imported_project(
        client,
        auth_headers,
        **({"story_bytes": story_bytes} if story_bytes is not None else {}),
        create_key=f"{idempotency_key}-project",
        import_key=f"{idempotency_key}-import",
    )
    created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key=idempotency_key,
    )
    terminal = wait_for_job(
        client,
        auth_headers,
        created["job"]["jobId"],
        {"succeeded", "failed"},
        timeout=20,
    )
    assert terminal["state"] == "succeeded", terminal
    return imported, created


def test_phase2_analysis_route_requires_exact_bearer_token(
    client: TestClient,
) -> None:
    path = "/api/v1/projects/project-1/analysis-runs"
    missing = client.get(path)
    wrong = client.get(
        path,
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert wrong.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_phase2_run_and_all_entity_collections_are_published(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported, created = create_phase2_run(client, auth_headers)
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    detail = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["run"]["contractVersion"] == ANALYSIS_CONTRACT_VERSION
    for collection in ("agent-executions", *ENTITY_COLLECTIONS):
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/{collection}",
            headers=auth_headers,
            params={"limit": 200},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["collection"] == collection
        assert body["runId"] == run_id
        assert body["pageSize"] == len(body["items"])


def test_dialogue_speaker_state_projection_filters_and_human_overlay(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story = b"""# Chapter One

## Scene One
\"Who goes there?\"

## Scene Two
Mara: \"Hold.\"

## Scene Three
Doctor Alex Reed entered. Another Doctor Alex Reed waited.
Alex: \"Ready.\"
"""
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-speaker-state",
        story_bytes=story,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]

    by_state: dict[str, list[dict[str, Any]]] = {}
    for state in ("unknown", "ambiguous", "proposed"):
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/dialogue-lines",
            headers=auth_headers,
            params={"speakerState": state},
        )
        assert response.status_code == 200, response.text
        by_state[state] = response.json()["items"]
        assert by_state[state]
        assert {item["speakerState"] for item in by_state[state]} == {state}

    proposed = by_state["proposed"][0]
    corrected = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "dialogue_speaker",
            "targetCollection": "dialogue-lines",
            "targetEntityId": proposed["entityId"],
            "expectedTargetRevision": proposed["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": proposed["effectiveValueFingerprint"],
            "patch": {
                "speakerCharacterId": proposed["effectiveAttribution"]["speakerCharacterId"],
                "selectedCandidateId": proposed["effectiveAttribution"]["selectedCandidateId"],
                "requiresHumanReview": False,
            },
            "reason": "Confirm the explicit speaker attribution.",
            "idempotencyKey": "phase2-speaker-state-corrected",
        },
    )
    assert corrected.status_code == 200, corrected.text

    corrected_page = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/dialogue-lines",
        headers=auth_headers,
        params={"speakerState": "corrected"},
    )
    assert corrected_page.status_code == 200, corrected_page.text
    corrected_items = corrected_page.json()["items"]
    assert len(corrected_items) == 1
    assert corrected_items[0]["entityId"] == proposed["entityId"]
    assert corrected_items[0]["speakerState"] == "corrected"
    assert corrected_items[0]["effectiveAuthority"] == "human"

    proposed_page = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/dialogue-lines",
        headers=auth_headers,
        params={"speakerState": "proposed"},
    )
    assert proposed_page.status_code == 200, proposed_page.text
    assert proposed_page.json()["items"] == []


def test_canonical_fixture_matches_strict_phase2_response_shapes(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    story_text = story_bytes.decode("utf-8")
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-contract-shapes",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]

    detail_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert set(detail) == {"correlationId", "run"}
    _assert_schema_object(detail["run"], "StoryAnalysisRun")
    _assert_schema_object(detail["run"]["currentSnapshot"], "AnalysisSnapshot")

    list_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs",
        headers=auth_headers,
    )
    assert list_response.status_code == 200, list_response.text
    run_page = list_response.json()
    assert set(run_page) == {"correlationId", "pageSize", "total", "runs"}
    assert run_page["pageSize"] == run_page["total"] == 1
    _assert_schema_object(run_page["runs"][0], "StoryAnalysisRun")

    for collection, definition in _ENTITY_SCHEMA_BY_COLLECTION.items():
        page = _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection=collection,
        )
        assert set(page) == {
            "correlationId",
            "pageSize",
            "total",
            "collection",
            "runId",
            "snapshotId",
            "items",
        }
        assert page["items"], collection
        for item in page["items"]:
            _assert_schema_object(item, definition)
            if collection != "agent-executions":
                assert item["machineEntityFingerprint"] == item["effectiveValueFingerprint"], (
                    collection
                )
            if collection in {"temporal-constraints", "continuity-findings"}:
                assert 1 <= len(item["evidence"]) <= MAX_EVIDENCE_SPANS
                for evidence in item["evidence"]:
                    start = evidence["startOffset"]
                    end = evidence["endOffset"]
                    excerpt_start = evidence["excerptStartOffset"]
                    excerpt_end = evidence["excerptEndOffset"]
                    assert 0 <= start < end <= len(story_text)
                    assert start == excerpt_start < excerpt_end <= end
                    assert evidence["textSha256"] == sha256_text(story_text[start:end])
                    assert evidence["excerptText"] == story_text[excerpt_start:excerpt_end]
                    assert evidence["excerptSha256"] == sha256_text(evidence["excerptText"])
            exact_text_count = _exact_text_key_count(item)
            if collection in _EXACT_TEXT_COLLECTIONS:
                assert exact_text_count == 2, collection
                _assert_schema_object(item["exactText"], "ExactAnalysisText")
            else:
                assert exact_text_count == 0, collection

    reviews_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews",
        headers=auth_headers,
    )
    assert reviews_response.status_code == 200, reviews_response.text
    reviews_page = reviews_response.json()
    assert set(reviews_page) == {"correlationId", "runId", "items"}
    assert len(reviews_page["items"]) == 4
    for review in reviews_page["items"]:
        _assert_schema_object(review, "AnalysisGateReview")
        _assert_schema_object(review["evidence"], "AnalysisGateEvidence")


def test_correction_and_review_decision_have_canonical_fingerprints_and_shapes(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-correction-contract",
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    )
    assert run_response.status_code == 200, run_response.text
    run = run_response.json()["run"]
    chapters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="chapters",
    )
    target = chapters["items"][0]
    correction_payload = {
        "category": "structure_label",
        "targetCollection": "chapters",
        "targetEntityId": target["entityId"],
        "expectedTargetRevision": target["effectiveRevision"],
        "expectedRunFingerprint": run["runFingerprint"],
        "previousValueFingerprint": target["effectiveValueFingerprint"],
        "patch": {"title": "Chapter One: Corrected Signal"},
        "reason": "Canonically correct the chapter label.",
        "idempotencyKey": "phase2-correction-contract-append",
    }
    missing_reason = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={key: value for key, value in correction_payload.items() if key != "reason"},
    )
    assert missing_reason.status_code == 422
    blank_reason = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json=correction_payload
        | {
            "reason": " \t ",
            "idempotencyKey": "phase2-blank-correction-reason",
        },
    )
    assert blank_reason.status_code == 422
    correction_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json=correction_payload,
    )
    assert correction_response.status_code == 200, correction_response.text
    correction_envelope = correction_response.json()
    assert set(correction_envelope) == {
        "correlationId",
        "correction",
        "invalidatedGateIds",
        "run",
        "reviews",
    }
    correction = correction_envelope["correction"]
    _assert_schema_object(correction, "AnalysisCorrection")
    assert correction["previousValueFingerprint"] == target["effectiveValueFingerprint"]

    corrected = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="chapters",
    )["items"][0]
    assert corrected["title"] == "Chapter One: Corrected Signal"
    assert correction["correctedValueFingerprint"] == corrected["effectiveValueFingerprint"]
    assert corrected["effectiveAuthority"] == "human"
    assert corrected["effectiveRevision"] == target["effectiveRevision"] + 1
    assert corrected["machineEntityFingerprint"] == target["machineEntityFingerprint"]
    assert corrected["effectiveValueFingerprint"] != corrected["machineEntityFingerprint"]

    corrections_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
    )
    assert corrections_response.status_code == 200, corrections_response.text
    corrections_page = corrections_response.json()
    assert set(corrections_page) == {
        "correlationId",
        "pageSize",
        "total",
        "runId",
        "items",
    }
    assert corrections_page["items"] == [correction]

    reviews_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews",
        headers=auth_headers,
    )
    reviews = reviews_response.json()["items"]
    review = next(item for item in reviews if item["gateId"] == "story_structure_review")
    assert review["latestDecision"] is None
    missing_rationale = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}"
        "/reviews/story_structure_review/decisions",
        headers=auth_headers,
        json={
            "decision": "approve",
            "expectedRevision": review["revision"],
            "expectedArtifactFingerprint": review["artifactFingerprint"],
            "expectedEvidenceFingerprint": review["evidenceFingerprint"],
            "acknowledgedWarningIds": review["openWarningIds"],
            "idempotencyKey": "phase2-structure-review-missing-rationale",
        },
    )
    assert missing_rationale.status_code == 422
    decision_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}"
        "/reviews/story_structure_review/decisions",
        headers=auth_headers,
        json={
            "decision": "approve",
            "expectedRevision": review["revision"],
            "expectedArtifactFingerprint": review["artifactFingerprint"],
            "expectedEvidenceFingerprint": review["evidenceFingerprint"],
            "acknowledgedWarningIds": review["openWarningIds"],
            "rationale": "Approve the governed story structure evidence.",
            "idempotencyKey": "phase2-structure-review-approval",
        },
    )
    assert decision_response.status_code == 200, decision_response.text
    decision_envelope = decision_response.json()
    assert set(decision_envelope) == {
        "correlationId",
        "review",
        "decision",
        "run",
    }
    _assert_schema_object(decision_envelope["review"], "AnalysisGateReview")
    _assert_schema_object(decision_envelope["decision"], "AnalysisGateDecision")
    assert decision_envelope["decision"]["rationale"] == (
        "Approve the governed story structure evidence."
    )
    assert (
        decision_envelope["review"]["latestDecisionId"]
        == (decision_envelope["decision"]["decisionId"])
    )
    assert decision_envelope["review"]["latestDecision"] == (decision_envelope["decision"])
