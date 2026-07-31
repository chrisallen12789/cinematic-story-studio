from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from cinematic_story_service.util import sha256_text
from cinematic_story_service.whole_book_analysis import ENTITY_COLLECTIONS
from tests.test_phase2_api import create_phase2_run

_REPOSITORY_ROOT = Path(__file__).parents[3]
_SCENE_CHILD_COLLECTIONS = (
    "beats",
    "mentions",
    "dialogue-lines",
    "narration-spans",
    "pov-segments",
    "timeline-events",
    "relationships",
    "emotional-states",
    "dramatic-intents",
)
_CHAPTER_SCOPED_COLLECTIONS = (
    "scenes",
    "beats",
    "mentions",
    "dialogue-lines",
    "narration-spans",
    "pov-segments",
    "timeline-events",
    "relationships",
)
_GATE_ORDER = (
    "story_structure_review",
    "character_registry_review",
    "dialogue_attribution_review",
    "whole_book_analysis_review",
)


def _collection(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    collection: str,
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/{collection}",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _collections(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
) -> dict[str, list[dict[str, Any]]]:
    return {
        collection: _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection=collection,
        )
        for collection in ENTITY_COLLECTIONS
    }


def _assert_character_aggregate_integrity(
    *,
    characters: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
) -> None:
    ordered_mentions = sorted(
        mentions,
        key=lambda value: (
            value["evidence"][0]["startOffset"],
            value["evidence"][0]["endOffset"],
            value["entityId"],
        ),
    )
    for character in characters:
        resolved = [
            mention
            for mention in ordered_mentions
            if mention["effectiveCharacterId"] == character["entityId"]
        ]
        ambiguous = [
            mention
            for mention in ordered_mentions
            if mention["effectiveCharacterId"] is None
            and character["entityId"] in mention["candidateCharacterIds"]
        ]
        assert character["mentionCount"] == len(resolved)
        assert character["firstMentionId"] == (
            resolved[0]["entityId"] if resolved else None
        )
        assert character["lastMentionId"] == (
            resolved[-1]["entityId"] if resolved else None
        )
        assert character["namedMentionIds"] == [
            mention["entityId"]
            for mention in resolved
            if mention["mentionKind"] != "pronoun"
        ]
        assert character["ambiguousMentionIds"] == [
            mention["entityId"] for mention in ambiguous
        ]


def _run(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["run"]


def _append_structure_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run: dict[str, Any],
    target: dict[str, Any],
    operation: str,
    parent_entity_id: str,
    ordinal: int,
    idempotency_key: str,
    target_entity_id: str | None = None,
    expected_target_revision: int | None = None,
    previous_value_fingerprint: str | None = None,
    supersedes_correction_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": "structure_boundary",
        "targetCollection": "scenes",
        "targetEntityId": target_entity_id or target["entityId"],
        "expectedTargetRevision": (
            target["effectiveRevision"]
            if expected_target_revision is None
            else expected_target_revision
        ),
        "expectedRunFingerprint": run["runFingerprint"],
        "previousValueFingerprint": (
            target["effectiveValueFingerprint"]
            if previous_value_fingerprint is None
            else previous_value_fingerprint
        ),
        "patch": {
            "operation": operation,
            "parentEntityId": parent_entity_id,
            "ordinal": ordinal,
            "sourceSpan": {
                key: value
                for key, value in target["sourceSpan"].items()
                if key != "textSha256"
            },
            "boundaryKind": target["boundaryKind"],
        },
        "reason": f"Exercise the {operation} structure-boundary branch.",
        "idempotencyKey": idempotency_key,
    }
    if supersedes_correction_id is not None:
        payload["supersedesCorrectionId"] = supersedes_correction_id
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run['runId']}/corrections",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _append_scene_label_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run: dict[str, Any],
    target: dict[str, Any],
    heading: str,
    idempotency_key: str,
    supersedes_correction_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": "structure_label",
        "targetCollection": "scenes",
        "targetEntityId": target["entityId"],
        "expectedTargetRevision": target["effectiveRevision"],
        "expectedRunFingerprint": run["runFingerprint"],
        "previousValueFingerprint": target["effectiveValueFingerprint"],
        "patch": {"heading": heading},
        "reason": "Exercise branch-local structure-label semantics.",
        "idempotencyKey": idempotency_key,
    }
    if supersedes_correction_id is not None:
        payload["supersedesCorrectionId"] = supersedes_correction_id
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run['runId']}/corrections",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _append_registry_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run: dict[str, Any],
    target: dict[str, Any],
    category: str,
    patch: dict[str, Any],
    idempotency_key: str,
    supersedes_correction_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": category,
        "targetCollection": "characters",
        "targetEntityId": target["entityId"],
        "expectedTargetRevision": target["effectiveRevision"],
        "expectedRunFingerprint": run["runFingerprint"],
        "previousValueFingerprint": target["effectiveValueFingerprint"],
        "patch": patch,
        "reason": "Exercise registry-only aggregate closure.",
        "idempotencyKey": idempotency_key,
    }
    if supersedes_correction_id is not None:
        payload["supersedesCorrectionId"] = supersedes_correction_id
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run['runId']}/corrections",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _reviews(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
) -> dict[str, dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return {value["gateId"]: value for value in response.json()["items"]}


def _approve_gate(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    gate_id: str,
    idempotency_key: str,
) -> str:
    gate = _reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )[gate_id]
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews/"
        f"{gate_id}/decisions",
        headers=auth_headers,
        json={
            "decision": "approve",
            "expectedRevision": gate["revision"],
            "expectedArtifactFingerprint": gate["artifactFingerprint"],
            "expectedEvidenceFingerprint": gate["evidenceFingerprint"],
            "acknowledgedWarningIds": gate["openWarningIds"],
            "rationale": "Approve the currently projected governed evidence.",
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["decision"]["decisionId"]


def _nested_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {
            nested
            for item in value
            for nested in _nested_strings(item)
        }
    if isinstance(value, dict):
        return {
            nested
            for item in value.values()
            for nested in _nested_strings(item)
        }
    return set()


def _assert_structure_integrity(
    collections: dict[str, list[dict[str, Any]]],
) -> None:
    chapters = collections["chapters"]
    scenes = collections["scenes"]
    beats = collections["beats"]
    chapter_ids = {value["entityId"] for value in chapters}
    scene_ids = {value["entityId"] for value in scenes}
    beat_ids = {value["entityId"] for value in beats}

    assert len(chapter_ids) == len(chapters)
    assert len(scene_ids) == len(scenes)
    assert all(scene["chapterId"] in chapter_ids for scene in scenes)
    assert all(beat["sceneId"] in scene_ids for beat in beats)
    assert all(
        beat["chapterId"]
        == next(scene["chapterId"] for scene in scenes if scene["entityId"] == beat["sceneId"])
        for beat in beats
    )

    scenes_by_chapter = {
        chapter_id: sorted(
            (scene for scene in scenes if scene["chapterId"] == chapter_id),
            key=lambda value: (value["ordinal"], value["entityId"]),
        )
        for chapter_id in chapter_ids
    }
    for chapter in chapters:
        children = scenes_by_chapter[chapter["entityId"]]
        assert chapter["sceneCount"] == len(children)
        assert chapter["firstSceneId"] == (
            children[0]["entityId"] if children else None
        )
        assert chapter["lastSceneId"] == (
            children[-1]["entityId"] if children else None
        )

    for collection in _SCENE_CHILD_COLLECTIONS:
        assert all(
            value["sceneId"] in scene_ids
            for value in collections[collection]
        )
    dialogue_ids = {
        value["entityId"] for value in collections["dialogue-lines"]
    }
    timeline_event_ids = {
        value["entityId"] for value in collections["timeline-events"]
    }
    location_ids = {value["entityId"] for value in collections["locations"]}
    all_entity_ids = {
        value["entityId"]
        for values in collections.values()
        for value in values
    }
    assert all(
        value["beatId"] in beat_ids
        for value in collections["dialogue-lines"]
    )
    assert all(
        value.get("beatId") is None or value["beatId"] in beat_ids
        for value in collections["narration-spans"]
    )
    assert all(
        value["dialogueLineId"] in dialogue_ids
        for value in collections["dramatic-intents"]
    )
    for value in collections["temporal-constraints"]:
        assert {
            event_id
            for field in (
                "sourceEventId",
                "targetEventId",
                "fromEventId",
                "toEventId",
            )
            if (event_id := value.get(field)) is not None
        } <= timeline_event_ids
    for value in collections["timeline-events"]:
        assert value["locationId"] is None or value["locationId"] in location_ids
    for value in collections["relationships"]:
        assert value["scope"]["firstSceneId"] in scene_ids
        assert value["scope"]["lastSceneId"] in scene_ids
        assert (
            value["validFromEventId"] is None
            or value["validFromEventId"] in timeline_event_ids
        )
        assert (
            value["validThroughEventId"] is None
            or value["validThroughEventId"] in timeline_event_ids
        )
    assert all(
        set(value["relatedEntityIds"]) <= all_entity_ids
        for value in collections["continuity-findings"]
    )


def test_registry_merge_only_rebuilds_character_mention_aggregates(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-registry-merge-aggregate-closure",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    baseline_characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    baseline_mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    merge_source = next(
        character for character in baseline_characters if character["mentionCount"] > 0
    )
    merge_target = next(
        character
        for character in baseline_characters
        if character["entityId"] != merge_source["entityId"]
    )
    source_mention_ids = {
        mention["entityId"]
        for mention in baseline_mentions
        if mention["effectiveCharacterId"] == merge_source["entityId"]
    }
    source_named_mention_ids = set(merge_source["namedMentionIds"])
    assert source_mention_ids
    merge_response = _append_registry_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=merge_source,
        category="character_merge",
        patch={"mergeIntoCharacterId": merge_target["entityId"]},
        idempotency_key="phase2-registry-merge-aggregate-correction",
    )

    characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    _assert_character_aggregate_integrity(
        characters=characters,
        mentions=mentions,
    )
    effective_source = next(
        value for value in characters if value["entityId"] == merge_source["entityId"]
    )
    effective_target = next(
        value for value in characters if value["entityId"] == merge_target["entityId"]
    )
    assert effective_source["mentionCount"] == 0
    assert merge_response["correction"]["correctedValueFingerprint"] == (
        effective_source["effectiveValueFingerprint"]
    )
    assert source_named_mention_ids <= set(effective_target["namedMentionIds"])
    assert all(
        mention["effectiveCharacterId"] == merge_target["entityId"]
        for mention in mentions
        if mention["entityId"] in source_mention_ids
    )

    identity_response = _append_registry_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=effective_source,
        category="character_identity",
        patch={
            "canonicalName": "Merged Source Identity",
            "normalizedCanonicalName": "merged source identity",
            "identityStatus": "resolved",
        },
        idempotency_key="phase2-registry-merge-then-identity",
        supersedes_correction_id=merge_response["correction"]["correctionId"],
    )
    refreshed_source = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="characters",
        )
        if value["entityId"] == merge_source["entityId"]
    )
    assert identity_response["correction"]["correctedValueFingerprint"] == (
        refreshed_source["effectiveValueFingerprint"]
    )
    corrections_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
    )
    assert corrections_response.status_code == 200, corrections_response.text
    listed_identity = next(
        value
        for value in corrections_response.json()["items"]
        if value["correctionId"]
        == identity_response["correction"]["correctionId"]
    )
    assert listed_identity["correctedValueFingerprint"] == (
        refreshed_source["effectiveValueFingerprint"]
    )


def test_registry_split_only_rebuilds_source_and_synthetic_aggregates(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-registry-split-aggregate-closure",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    baseline_characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    split_source = next(
        character for character in baseline_characters if character["namedMentionIds"]
    )
    split_mention_id = split_source["namedMentionIds"][0]
    split_registry_id = "registry-only-split-character"
    split_response = _append_registry_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=split_source,
        category="character_split",
        patch={
            "newRegistryCharacterId": split_registry_id,
            "canonicalName": "Registry Only Split",
            "normalizedCanonicalName": "registry only split",
            "mentionIds": [split_mention_id],
        },
        idempotency_key="phase2-registry-split-aggregate-correction",
    )

    characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    _assert_character_aggregate_integrity(
        characters=characters,
        mentions=mentions,
    )
    effective_source = next(
        value for value in characters if value["entityId"] == split_source["entityId"]
    )
    synthetic = next(
        value for value in characters if value["entityId"] == split_registry_id
    )
    split_mention = next(
        value for value in mentions if value["entityId"] == split_mention_id
    )
    assert split_mention["effectiveCharacterId"] == split_registry_id
    assert split_response["correction"]["correctedValueFingerprint"] == (
        effective_source["effectiveValueFingerprint"]
    )
    assert split_mention_id not in effective_source["namedMentionIds"]
    assert synthetic["namedMentionIds"] == [split_mention_id]
    assert synthetic["mentionCount"] == 1


def test_mention_resolution_only_rebuilds_character_aggregates(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-mention-resolution-aggregate-closure",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    mention = next(
        value for value in mentions if value["effectiveCharacterId"] is not None
    )
    source_character_id = mention["effectiveCharacterId"]
    target_character = next(
        value for value in characters if value["entityId"] != source_character_id
    )
    source_before = next(
        value for value in characters if value["entityId"] == source_character_id
    )
    target_before = target_character
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "mention_resolution",
            "targetCollection": "mentions",
            "targetEntityId": mention["entityId"],
            "expectedTargetRevision": mention["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": mention["effectiveValueFingerprint"],
            "patch": {
                "resolution": "resolved",
                "effectiveCharacterId": target_character["entityId"],
                "candidateCharacterIds": [target_character["entityId"]],
            },
            "reason": "Exercise mention-only aggregate closure.",
            "idempotencyKey": "phase2-mention-resolution-aggregate-correction",
        },
    )
    assert response.status_code == 200, response.text

    effective_characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    effective_mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    _assert_character_aggregate_integrity(
        characters=effective_characters,
        mentions=effective_mentions,
    )
    source_after = next(
        value
        for value in effective_characters
        if value["entityId"] == source_character_id
    )
    target_after = next(
        value
        for value in effective_characters
        if value["entityId"] == target_character["entityId"]
    )
    assert source_after["mentionCount"] == source_before["mentionCount"] - 1
    assert target_after["mentionCount"] == target_before["mentionCount"] + 1
    assert next(
        value
        for value in effective_mentions
        if value["entityId"] == mention["entityId"]
    )["effectiveCharacterId"] == target_character["entityId"]


def test_registry_merge_deduplicates_and_resolves_collapsed_candidates(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-registry-candidate-collapse",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    ambiguous_mention = next(
        value
        for value in mentions
        if value["resolution"] == "ambiguous"
        and len(value["candidateCharacterIds"]) >= 2
    )
    merge_source_id, merge_target_id = ambiguous_mention[
        "candidateCharacterIds"
    ][:2]
    merge_source = next(
        value for value in characters if value["entityId"] == merge_source_id
    )
    merge_target = next(
        value for value in characters if value["entityId"] == merge_target_id
    )
    _append_registry_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=merge_source,
        category="character_merge",
        patch={"mergeIntoCharacterId": merge_target["entityId"]},
        idempotency_key="phase2-registry-candidate-collapse-merge",
    )

    effective_mention = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="mentions",
        )
        if value["entityId"] == ambiguous_mention["entityId"]
    )
    expected_candidates = list(
        dict.fromkeys(
            merge_target_id if value == merge_source_id else value
            for value in ambiguous_mention["candidateCharacterIds"]
        )
    )
    assert effective_mention["candidateCharacterIds"] == expected_candidates
    assert len(effective_mention["candidateCharacterIds"]) == len(
        set(effective_mention["candidateCharacterIds"])
    )
    if expected_candidates == [merge_target_id]:
        assert effective_mention["resolution"] == "resolved"
        assert effective_mention["effectiveCharacterId"] == merge_target_id

    mention_correction = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "mention_resolution",
            "targetCollection": "mentions",
            "targetEntityId": effective_mention["entityId"],
            "expectedTargetRevision": effective_mention["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": effective_mention[
                "effectiveValueFingerprint"
            ],
            "patch": {
                "resolution": "resolved",
                "effectiveCharacterId": merge_target_id,
                "candidateCharacterIds": [merge_target_id],
            },
            "reason": "Exercise CAS after a registry-derived revision.",
            "idempotencyKey": "phase2-registry-derived-mention-cas",
        },
    )
    assert mention_correction.status_code == 200, mention_correction.text
    corrected_mention = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="mentions",
        )
        if value["entityId"] == effective_mention["entityId"]
    )
    assert mention_correction.json()["correction"][
        "correctedValueFingerprint"
    ] == corrected_mention["effectiveValueFingerprint"]
    ambiguous_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "mention_resolution",
            "targetCollection": "mentions",
            "targetEntityId": corrected_mention["entityId"],
            "expectedTargetRevision": corrected_mention["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": corrected_mention[
                "effectiveValueFingerprint"
            ],
            "patch": {
                "resolution": "ambiguous",
                "effectiveCharacterId": None,
                "candidateCharacterIds": [merge_target_id],
            },
            "reason": "Preserve an explicit one-candidate human ambiguity.",
            "supersedesCorrectionId": mention_correction.json()["correction"][
                "correctionId"
            ],
            "idempotencyKey": "phase2-registry-human-ambiguous-one",
        },
    )
    assert ambiguous_response.status_code == 200, ambiguous_response.text
    explicit_ambiguous = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="mentions",
        )
        if value["entityId"] == effective_mention["entityId"]
    )
    assert explicit_ambiguous["resolution"] == "ambiguous"
    assert explicit_ambiguous["effectiveCharacterId"] is None
    assert explicit_ambiguous["candidateCharacterIds"] == [merge_target_id]

    for collection, fields in (
        ("timeline-events", ("participantCharacterIds",)),
        (
            "relationships",
            (
                "sourceCandidateCharacterIds",
                "targetCandidateCharacterIds",
            ),
        ),
        ("continuity-findings", ("relatedEntityIds",)),
    ):
        for item in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection=collection,
        ):
            for field in fields:
                assert len(item[field]) == len(set(item[field]))
            if collection == "relationships":
                assert (
                    item["sourceCharacterId"] is None
                    or item["sourceCharacterId"]
                    != item["targetCharacterId"]
                )


def test_transitive_merge_updates_revision_provenance_and_dialogue_gate(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-transitive-registry-merge",
        story_bytes=story,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    characters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    dialogue_lines = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="dialogue-lines",
    )
    dialogue_character_ids = {
        candidate["characterId"]
        for line in dialogue_lines
        for candidate in line["candidates"]
        if candidate["characterId"] is not None
    } | {
        attribution["speakerCharacterId"]
        for line in dialogue_lines
        if (
            attribution := line["effectiveAttribution"]
        )["speakerCharacterId"]
        is not None
    }
    source = next(
        character
        for character in characters
        if character["entityId"] in dialogue_character_ids
        and any(
            mention["effectiveCharacterId"] == character["entityId"]
            for mention in mentions
        )
    )
    bridge = next(
        character
        for character in characters
        if character["entityId"] not in dialogue_character_ids
        and character["entityId"] != source["entityId"]
    )
    final = next(
        character
        for character in characters
        if character["entityId"]
        not in {source["entityId"], bridge["entityId"]}
    )
    source_mention = next(
        mention
        for mention in mentions
        if mention["effectiveCharacterId"] == source["entityId"]
    )
    first_merge = _append_registry_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=source,
        category="character_merge",
        patch={"mergeIntoCharacterId": bridge["entityId"]},
        idempotency_key="phase2-transitive-registry-first-merge",
    )
    mention_after_first = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="mentions",
        )
        if value["entityId"] == source_mention["entityId"]
    )
    assert mention_after_first["effectiveCharacterId"] == bridge["entityId"]

    for gate_id in _GATE_ORDER:
        _approve_gate(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            gate_id=gate_id,
            idempotency_key=f"phase2-transitive-registry-approve-{gate_id}",
        )

    refreshed_bridge = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="characters",
        )
        if value["entityId"] == bridge["entityId"]
    )
    second_merge = _append_registry_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=refreshed_bridge,
        category="character_merge",
        patch={"mergeIntoCharacterId": final["entityId"]},
        idempotency_key="phase2-transitive-registry-second-merge",
    )
    assert "dialogue_attribution_review" in second_merge["invalidatedGateIds"]
    mention_after_second = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="mentions",
        )
        if value["entityId"] == source_mention["entityId"]
    )
    assert mention_after_second["effectiveCharacterId"] == final["entityId"]
    assert mention_after_second["effectiveRevision"] > (
        mention_after_first["effectiveRevision"]
    )
    assert mention_after_second["provenance"]["correctionId"] == (
        second_merge["correction"]["correctionId"]
    )
    assert second_merge["correction"]["recordedAt"] > (
        first_merge["correction"]["recordedAt"]
    )


def test_scene_move_and_remove_project_across_the_complete_entity_graph(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-complete-structure-graph",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    original = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    moved_scene = original["scenes"][2]
    removed_scene = original["scenes"][1]
    destination_chapter = original["chapters"][-1]
    assert moved_scene["chapterId"] != destination_chapter["entityId"]

    removed_entity_ids = {removed_scene["entityId"]}
    for collection in _SCENE_CHILD_COLLECTIONS:
        removed_entity_ids.update(
            value["entityId"]
            for value in original[collection]
            if value["sceneId"] == removed_scene["entityId"]
        )
    removed_location_ids = {
        value["entityId"]
        for value in original["locations"]
        if set(value["sceneIds"]) <= {removed_scene["entityId"]}
    }
    removed_entity_ids.update(removed_location_ids)
    removed_event_ids = {
        value["entityId"]
        for value in original["timeline-events"]
        if value["sceneId"] == removed_scene["entityId"]
    }
    removed_constraint_ids = {
        value["entityId"]
        for value in original["temporal-constraints"]
        if value["sourceEventId"] in removed_event_ids
        or value["targetEventId"] in removed_event_ids
    }
    removed_entity_ids.update(removed_constraint_ids)
    original_continuity = {
        value["stableSemanticId"]: value
        for value in original["continuity-findings"]
    }
    expected_continuity_refs = {
        semantic_id: [
            entity_id
            for entity_id in value["relatedEntityIds"]
            if entity_id not in removed_entity_ids
        ]
        for semantic_id, value in original_continuity.items()
    }
    assert any(
        expected_continuity_refs[semantic_id] != value["relatedEntityIds"]
        for semantic_id, value in original_continuity.items()
    )

    _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=moved_scene,
        operation="move",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=moved_scene["ordinal"] + 100,
        idempotency_key="phase2-complete-graph-move",
    )
    _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=removed_scene,
        operation="remove",
        parent_entity_id=removed_scene["chapterId"],
        ordinal=removed_scene["ordinal"],
        idempotency_key="phase2-complete-graph-remove",
    )

    effective = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    _assert_structure_integrity(effective)

    for collection in _CHAPTER_SCOPED_COLLECTIONS:
        moved_items = [
            value
            for value in effective[collection]
            if value["sceneId"] == moved_scene["entityId"]
        ]
        assert moved_items, collection
        assert {
            value["chapterId"] for value in moved_items
        } == {destination_chapter["entityId"]}
        original_by_semantic_id = {
            value["stableSemanticId"]: value for value in original[collection]
        }
        assert all(
            value["effectiveValueFingerprint"]
            != original_by_semantic_id[value["stableSemanticId"]][
                "effectiveValueFingerprint"
            ]
            for value in moved_items
        )

    effective_entity_ids = {
        value["entityId"]
        for values in effective.values()
        for value in values
    }
    assert removed_entity_ids.isdisjoint(effective_entity_ids)
    effective_strings = {
        nested
        for values in effective.values()
        for value in values
        for nested in _nested_strings(value)
    }
    assert removed_entity_ids.isdisjoint(effective_strings)

    scene_ids = {value["entityId"] for value in effective["scenes"]}
    for location in effective["locations"]:
        assert location["sceneIds"]
        assert set(location["sceneIds"]) <= scene_ids
        assert location["sceneCount"] == len(location["sceneIds"])
        assert location["firstSceneId"] == location["sceneIds"][0]
        assert {
            assignment["sceneId"] for assignment in location["sceneAssignments"]
        } == set(location["sceneIds"])

    ordered_mentions = sorted(
        effective["mentions"],
        key=lambda value: (
            value["evidence"][0]["startOffset"],
            value["evidence"][0]["endOffset"],
            value["entityId"],
        ),
    )
    for character in effective["characters"]:
        resolved_mentions = [
            value
            for value in ordered_mentions
            if value["effectiveCharacterId"] == character["entityId"]
        ]
        ambiguous_mentions = [
            value
            for value in ordered_mentions
            if value["effectiveCharacterId"] is None
            and character["entityId"] in value["candidateCharacterIds"]
        ]
        assert character["mentionCount"] == len(resolved_mentions)
        if resolved_mentions:
            assert character["firstMentionId"] == resolved_mentions[0]["entityId"]
            assert character["lastMentionId"] == resolved_mentions[-1]["entityId"]
            assert character["firstEvidence"]
            assert character["lastEvidence"]
        else:
            assert character["firstMentionId"] is None
            assert character["lastMentionId"] is None
            assert character["firstEvidence"] == []
            assert character["lastEvidence"] == []
        assert character["namedMentionIds"] == [
            value["entityId"]
            for value in resolved_mentions
            if value["mentionKind"] != "pronoun"
        ]
        assert character["ambiguousMentionIds"] == [
            value["entityId"] for value in ambiguous_mentions
        ]

    effective_continuity = {
        value["stableSemanticId"]: value
        for value in effective["continuity-findings"]
    }
    for semantic_id, expected_refs in expected_continuity_refs.items():
        if not expected_refs:
            assert semantic_id not in effective_continuity
            continue
        value = effective_continuity[semantic_id]
        assert value["relatedEntityIds"] == expected_refs
        if expected_refs != original_continuity[semantic_id]["relatedEntityIds"]:
            assert value["effectiveValueFingerprint"] != (
                original_continuity[semantic_id]["effectiveValueFingerprint"]
            )

    moved_pov = next(
        value
        for value in effective["pov-segments"]
        if value["sceneId"] == moved_scene["entityId"]
    )
    pov_character_id = effective["characters"][0]["entityId"]
    pov_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "point_of_view",
            "targetCollection": "pov-segments",
            "targetEntityId": moved_pov["entityId"],
            "expectedTargetRevision": moved_pov["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": moved_pov["effectiveValueFingerprint"],
            "patch": {
                "mode": "third_person_limited",
                "viewpointCharacterId": pov_character_id,
                "narratorCharacterId": pov_character_id,
            },
            "reason": "Preserve graph-derived chapter scope in the response.",
            "idempotencyKey": "phase2-moved-pov-fingerprint-closure",
        },
    )
    assert pov_response.status_code == 200, pov_response.text
    refreshed_pov = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="pov-segments",
        )
        if value["entityId"] == moved_pov["entityId"]
    )
    assert pov_response.json()["correction"]["correctedValueFingerprint"] == (
        refreshed_pov["effectiveValueFingerprint"]
    )

    merge_source = next(
        character
        for character in effective["characters"]
        if character["mentionCount"] > 0
    )
    merge_target = next(
        character
        for character in effective["characters"]
        if character["entityId"] != merge_source["entityId"]
    )
    merge_response = _append_registry_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=merge_source,
        category="character_merge",
        patch={"mergeIntoCharacterId": merge_target["entityId"]},
        idempotency_key="phase2-structure-remove-then-merge",
    )
    refreshed_merge_source = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="characters",
        )
        if value["entityId"] == merge_source["entityId"]
    )
    assert merge_response["correction"]["correctedValueFingerprint"] == (
        refreshed_merge_source["effectiveValueFingerprint"]
    )


def test_structure_corrections_invalidate_only_evidence_dependent_gates(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story = b"""# Chapter One

## Scene One: Empty

Rain fell over the stones.

## Scene Two: Character Evidence

Mara Vale crossed the silent hall. Mara waited beside the door.

## Scene Three: Anonymous Dialogue

"Who waits beyond the wall?"

## Scene Four: Registry Seed

Mara Vale: "Hold the line."
"""
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-structure-selective-gates",
        story_bytes=story,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    scenes = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="scenes",
    )
    scene_by_heading = {value["heading"]: value for value in scenes}
    empty_scene = scene_by_heading["Scene One: Empty"]
    character_scene = scene_by_heading["Scene Two: Character Evidence"]
    dialogue_scene = scene_by_heading["Scene Three: Anonymous Dialogue"]
    mentions = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    relationships = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="relationships",
    )
    dialogue_lines = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="dialogue-lines",
    )
    assert not any(
        value["sceneId"] == empty_scene["entityId"]
        for value in [*mentions, *relationships, *dialogue_lines]
    )
    assert any(
        value["sceneId"] == character_scene["entityId"]
        for value in [*mentions, *relationships]
    )
    assert not any(
        value["sceneId"] == character_scene["entityId"]
        for value in dialogue_lines
    )
    assert any(
        value["sceneId"] == dialogue_scene["entityId"]
        for value in dialogue_lines
    )
    assert not any(
        value["sceneId"] == dialogue_scene["entityId"]
        for value in [*mentions, *relationships]
    )

    decision_ids = {
        gate_id: _approve_gate(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            gate_id=gate_id,
            idempotency_key=f"phase2-selective-gates-initial-{gate_id}",
        )
        for gate_id in _GATE_ORDER
    }

    cases = (
        (
            empty_scene,
            {
                "story_structure_review",
                "whole_book_analysis_review",
            },
        ),
        (
            character_scene,
            {
                "story_structure_review",
                "character_registry_review",
                "whole_book_analysis_review",
            },
        ),
        (
            dialogue_scene,
            {
                "story_structure_review",
                "dialogue_attribution_review",
                "whole_book_analysis_review",
            },
        ),
    )
    for index, (target, expected_invalidated) in enumerate(cases):
        response = _append_structure_correction(
            client,
            auth_headers,
            project_id=project_id,
            run=run,
            target=target,
            operation="remove",
            parent_entity_id=target["chapterId"],
            ordinal=target["ordinal"],
            idempotency_key=f"phase2-selective-gates-remove-{index}",
        )
        assert set(response["invalidatedGateIds"]) == expected_invalidated
        reviews = _reviews(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
        )
        for gate_id in _GATE_ORDER:
            assert reviews[gate_id]["latestDecisionId"] == decision_ids[gate_id]
            assert reviews[gate_id]["state"] == (
                "invalidated"
                if gate_id in expected_invalidated
                else "approved"
            )
        for gate_id in _GATE_ORDER:
            if gate_id not in expected_invalidated:
                continue
            decision_ids[gate_id] = _approve_gate(
                client,
                auth_headers,
                project_id=project_id,
                run_id=run_id,
                gate_id=gate_id,
                idempotency_key=f"phase2-selective-gates-reapprove-{index}-{gate_id}",
            )


def test_chapter_remove_invalidates_gates_for_moved_in_scene_evidence(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story = b"""# Chapter One

## Scene One: Evidence

Mara Vale: "Hold the line." Mara Vale waited beside the door.

# Chapter Two

## Scene Two: Empty

Rain crossed the empty stones.
"""
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-chapter-moved-evidence-gates",
        story_bytes=story,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    chapters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="chapters",
    )
    scenes = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="scenes",
    )
    evidence_scene = next(
        value for value in scenes if value["heading"] == "Scene One: Evidence"
    )
    destination_chapter = next(
        value for value in chapters if value["title"] == "Chapter Two"
    )
    _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=evidence_scene,
        operation="move",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=evidence_scene["ordinal"],
        idempotency_key="phase2-chapter-moved-evidence-scene-move",
    )
    for gate_id in _GATE_ORDER:
        _approve_gate(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            gate_id=gate_id,
            idempotency_key=f"phase2-chapter-moved-evidence-approve-{gate_id}",
        )

    refreshed_destination = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="chapters",
        )
        if value["entityId"] == destination_chapter["entityId"]
    )
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "structure_boundary",
            "targetCollection": "chapters",
            "targetEntityId": refreshed_destination["entityId"],
            "expectedTargetRevision": refreshed_destination["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": refreshed_destination[
                "effectiveValueFingerprint"
            ],
            "patch": {
                "operation": "remove",
                "parentEntityId": run["storyId"],
                "ordinal": refreshed_destination["ordinal"],
                "sourceSpan": {
                    key: value
                    for key, value in refreshed_destination[
                        "sourceSpan"
                    ].items()
                    if key != "textSha256"
                },
            },
            "reason": "Remove a chapter containing moved-in governed evidence.",
            "idempotencyKey": "phase2-chapter-moved-evidence-remove",
        },
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["invalidatedGateIds"]) == set(_GATE_ORDER)


def test_structure_boundary_span_hash_is_derived_from_frozen_extraction(
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
        idempotency_key="phase2-derived-boundary-span",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    scenes = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="scenes",
    )
    chapters = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="chapters",
    )
    target = scenes[1]
    destination = next(
        chapter
        for chapter in chapters
        if chapter["entityId"] != target["chapterId"]
    )
    original_span = target["sourceSpan"]
    start = original_span["startOffset"] + 1
    end = original_span["endOffset"] - 1
    assert end > start
    requested_span = {
        key: value
        for key, value in original_span.items()
        if key != "textSha256"
    } | {
        "startOffset": start,
        "endOffset": end,
    }
    payload = {
        "category": "structure_boundary",
        "targetCollection": "scenes",
        "targetEntityId": target["entityId"],
        "expectedTargetRevision": target["effectiveRevision"],
        "expectedRunFingerprint": run["runFingerprint"],
        "previousValueFingerprint": target["effectiveValueFingerprint"],
        "patch": {
            "operation": "move",
            "parentEntityId": destination["entityId"],
            "ordinal": target["ordinal"] + 10,
            "sourceSpan": requested_span,
            "boundaryKind": target["boundaryKind"],
        },
        "reason": "Derive the changed selection digest from frozen text.",
        "idempotencyKey": "phase2-derived-boundary-span-correction",
    }
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    correction = response.json()["correction"]
    expected_span = {
        **requested_span,
        "textSha256": sha256_text(story_text[start:end]),
    }
    assert correction["patch"]["sourceSpan"] == expected_span

    replay = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json=payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["correction"] == correction
    refreshed_target = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="scenes",
        )
        if value["entityId"] == target["entityId"]
    )
    assert refreshed_target["sourceSpan"] == expected_span

    invalid_spans = (
        requested_span | {"sourceDocumentId": "tampered-source-document"},
        requested_span | {"extractionId": "tampered-extraction"},
        requested_span
        | {"extractionRevision": requested_span["extractionRevision"] + 1},
        requested_span | {"offsetUnit": "utf-16-code-unit"},
        requested_span | {"endOffset": len(story_text) + 1},
        requested_span | {"textSha256": "0" * 64},
    )
    for index, invalid_span in enumerate(invalid_spans):
        invalid_response = client.post(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
            headers=auth_headers,
            json={
                **payload,
                "expectedTargetRevision": refreshed_target[
                    "effectiveRevision"
                ],
                "previousValueFingerprint": refreshed_target[
                    "effectiveValueFingerprint"
                ],
                "patch": {
                    **payload["patch"],
                    "sourceSpan": invalid_span,
                },
                "supersedesCorrectionId": correction["correctionId"],
                "idempotencyKey": (
                    f"phase2-derived-boundary-span-invalid-{index}"
                ),
            },
        )
        assert invalid_response.status_code == 422, invalid_response.text
        assert invalid_response.json()["error"]["code"] == (
            "CORRECTION_PATCH_INVALID"
        )
    corrections = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
    )
    assert corrections.status_code == 200, corrections.text
    assert corrections.json()["items"] == [correction]


def test_structure_add_move_remove_and_remove_restore_are_append_only(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        _REPOSITORY_ROOT / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-structure-supersession",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = _run(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    baseline = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    baseline_scenes = baseline["scenes"]
    add_source = baseline_scenes[0]
    restore_source = baseline_scenes[1]
    destination_chapter = baseline["chapters"][-1]

    add_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=add_source,
        operation="add",
        parent_entity_id=add_source["chapterId"],
        ordinal=add_source["ordinal"] + 100,
        idempotency_key="phase2-supersession-add-synthetic",
    )
    add_correction = add_response["correction"]
    after_add = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    _assert_structure_integrity(after_add)
    assert len(after_add["scenes"]) == len(baseline_scenes) + 1
    original_after_add = next(
        value
        for value in after_add["scenes"]
        if value["entityId"] == add_source["entityId"]
    )
    synthetic_after_add = next(
        value
        for value in after_add["scenes"]
        if value.get("effectiveBoundary", {}).get("correctionId")
        == add_correction["correctionId"]
    )
    assert original_after_add["effectiveRevision"] == add_source["effectiveRevision"]
    assert "effectiveBoundary" not in original_after_add
    assert synthetic_after_add["entityId"] != add_source["entityId"]
    assert synthetic_after_add["stableSemanticId"] != add_source["stableSemanticId"]
    assert synthetic_after_add["effectiveRevision"] == add_source["effectiveRevision"] + 1
    assert synthetic_after_add["provenance"]["correctionId"] == (
        add_correction["correctionId"]
    )
    synthetic_entity_id = synthetic_after_add["entityId"]
    synthetic_semantic_id = synthetic_after_add["stableSemanticId"]

    source_label_response = _append_scene_label_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=original_after_add,
        heading="Source branch label",
        idempotency_key="phase2-supersession-label-visible-source",
    )
    source_label = source_label_response["correction"]
    synthetic_label_response = _append_scene_label_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=synthetic_after_add,
        heading="Synthetic branch label",
        supersedes_correction_id=add_correction["correctionId"],
        idempotency_key="phase2-supersession-label-synthetic",
    )
    synthetic_label = synthetic_label_response["correction"]
    after_labels = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    labeled_source = next(
        value
        for value in after_labels["scenes"]
        if value["entityId"] == add_source["entityId"]
    )
    labeled_synthetic = next(
        value
        for value in after_labels["scenes"]
        if value["entityId"] == synthetic_entity_id
    )
    assert labeled_source["heading"] == "Source branch label"
    assert labeled_source["effectiveRevision"] == add_source["effectiveRevision"] + 1
    assert labeled_source["provenance"]["correctionId"] == source_label["correctionId"]
    assert labeled_synthetic["heading"] == "Synthetic branch label"
    assert labeled_synthetic["effectiveRevision"] == (
        synthetic_after_add["effectiveRevision"] + 1
    )
    assert labeled_synthetic["provenance"]["correctionId"] == (
        synthetic_label["correctionId"]
    )
    assert labeled_synthetic["effectiveBoundary"]["correctionId"] == (
        add_correction["correctionId"]
    )
    second_source_label_response = _append_scene_label_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=labeled_source,
        heading="Source branch relabel",
        supersedes_correction_id=source_label["correctionId"],
        idempotency_key="phase2-supersession-relabel-visible-source",
    )
    second_source_label = second_source_label_response["correction"]
    after_source_relabel = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    relabeled_source = next(
        value
        for value in after_source_relabel["scenes"]
        if value["entityId"] == add_source["entityId"]
    )
    unchanged_labeled_synthetic = next(
        value
        for value in after_source_relabel["scenes"]
        if value["entityId"] == synthetic_entity_id
    )
    assert relabeled_source["heading"] == "Source branch relabel"
    assert relabeled_source["effectiveRevision"] == (
        labeled_source["effectiveRevision"] + 1
    )
    assert unchanged_labeled_synthetic["heading"] == "Synthetic branch label"
    assert unchanged_labeled_synthetic["effectiveRevision"] == (
        labeled_synthetic["effectiveRevision"]
    )

    move_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=labeled_synthetic,
        target_entity_id=synthetic_entity_id,
        operation="move",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=labeled_synthetic["ordinal"] + 1,
        supersedes_correction_id=synthetic_label["correctionId"],
        idempotency_key="phase2-supersession-move-synthetic",
    )
    move_correction = move_response["correction"]
    assert move_correction["supersedesCorrectionId"] == (
        synthetic_label["correctionId"]
    )
    after_move = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    _assert_structure_integrity(after_move)
    assert len(after_move["scenes"]) == len(baseline_scenes) + 1
    synthetic_after_move = next(
        value
        for value in after_move["scenes"]
        if value["entityId"] == synthetic_entity_id
    )
    assert synthetic_after_move["stableSemanticId"] == synthetic_semantic_id
    assert synthetic_after_move["chapterId"] == destination_chapter["entityId"]
    assert synthetic_after_move["effectiveBoundary"]["operation"] == "move"
    assert synthetic_after_move["effectiveBoundary"]["correctionId"] == (
        move_correction["correctionId"]
    )
    assert synthetic_after_move["effectiveRevision"] == (
        labeled_synthetic["effectiveRevision"] + 1
    )
    assert synthetic_after_move["provenance"]["correctionId"] == (
        move_correction["correctionId"]
    )
    unchanged_original = next(
        value
        for value in after_move["scenes"]
        if value["entityId"] == add_source["entityId"]
    )
    assert unchanged_original["effectiveRevision"] == relabeled_source["effectiveRevision"]
    assert unchanged_original["heading"] == "Source branch relabel"
    assert "effectiveBoundary" not in unchanged_original

    remove_synthetic_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=synthetic_after_move,
        target_entity_id=synthetic_entity_id,
        operation="remove",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=synthetic_after_move["ordinal"],
        supersedes_correction_id=move_correction["correctionId"],
        idempotency_key="phase2-supersession-remove-synthetic",
    )
    remove_synthetic = remove_synthetic_response["correction"]
    assert remove_synthetic["supersedesCorrectionId"] == move_correction["correctionId"]
    after_synthetic_remove = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    _assert_structure_integrity(after_synthetic_remove)
    assert len(after_synthetic_remove["scenes"]) == len(baseline_scenes)
    assert synthetic_entity_id not in {
        value["entityId"] for value in after_synthetic_remove["scenes"]
    }
    assert synthetic_semantic_id not in {
        value["stableSemanticId"] for value in after_synthetic_remove["scenes"]
    }
    assert sum(
        value["entityId"] == add_source["entityId"]
        for value in after_synthetic_remove["scenes"]
    ) == 1

    visible_source = next(
        value
        for value in after_synthetic_remove["scenes"]
        if value["entityId"] == add_source["entityId"]
    )
    move_visible_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=visible_source,
        operation="move",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=visible_source["ordinal"] + 2,
        supersedes_correction_id=second_source_label["correctionId"],
        idempotency_key="phase2-supersession-move-visible-source",
    )
    move_visible = move_visible_response["correction"]
    assert move_visible["supersedesCorrectionId"] == (
        second_source_label["correctionId"]
    )
    after_visible_move = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    moved_visible_source = next(
        value
        for value in after_visible_move["scenes"]
        if value["entityId"] == add_source["entityId"]
    )
    assert moved_visible_source["chapterId"] == destination_chapter["entityId"]
    assert moved_visible_source["effectiveRevision"] == (
        visible_source["effectiveRevision"] + 1
    )
    assert synthetic_entity_id not in {
        value["entityId"] for value in after_visible_move["scenes"]
    }

    remove_visible_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=moved_visible_source,
        operation="remove",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=moved_visible_source["ordinal"],
        supersedes_correction_id=move_visible["correctionId"],
        idempotency_key="phase2-supersession-remove-visible-source",
    )
    remove_visible = remove_visible_response["correction"]
    after_visible_remove = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    assert {
        add_source["entityId"],
        synthetic_entity_id,
    }.isdisjoint(
        {value["entityId"] for value in after_visible_remove["scenes"]}
    )

    restore_visible_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=moved_visible_source,
        operation="add",
        parent_entity_id=add_source["chapterId"],
        ordinal=add_source["ordinal"],
        expected_target_revision=moved_visible_source["effectiveRevision"] + 1,
        previous_value_fingerprint=remove_visible["correctedValueFingerprint"],
        supersedes_correction_id=remove_visible["correctionId"],
        idempotency_key="phase2-supersession-restore-visible-source",
    )
    restore_visible = restore_visible_response["correction"]
    after_visible_restore = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    restored_visible_source = next(
        value
        for value in after_visible_restore["scenes"]
        if value["entityId"] == add_source["entityId"]
    )
    assert restored_visible_source["chapterId"] == add_source["chapterId"]
    assert restored_visible_source["effectiveRevision"] == (
        add_source["effectiveRevision"] + 5
    )
    assert synthetic_entity_id not in {
        value["entityId"] for value in after_visible_restore["scenes"]
    }

    reactivate_synthetic_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=synthetic_after_move,
        target_entity_id=synthetic_entity_id,
        operation="add",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=synthetic_after_move["ordinal"],
        expected_target_revision=synthetic_after_move["effectiveRevision"] + 1,
        previous_value_fingerprint=remove_synthetic["correctedValueFingerprint"],
        supersedes_correction_id=remove_synthetic["correctionId"],
        idempotency_key="phase2-supersession-reactivate-synthetic",
    )
    reactivate_synthetic = reactivate_synthetic_response["correction"]
    after_synthetic_reactivation = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    reactivated_synthetic = next(
        value
        for value in after_synthetic_reactivation["scenes"]
        if value["entityId"] == synthetic_entity_id
    )
    assert reactivated_synthetic["stableSemanticId"] == synthetic_semantic_id
    assert reactivated_synthetic["effectiveRevision"] == (
        synthetic_after_add["effectiveRevision"] + 4
    )
    assert next(
        value
        for value in after_synthetic_reactivation["scenes"]
        if value["entityId"] == add_source["entityId"]
    )["effectiveRevision"] == restored_visible_source["effectiveRevision"]

    final_remove_synthetic_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=reactivated_synthetic,
        target_entity_id=synthetic_entity_id,
        operation="remove",
        parent_entity_id=destination_chapter["entityId"],
        ordinal=reactivated_synthetic["ordinal"],
        supersedes_correction_id=reactivate_synthetic["correctionId"],
        idempotency_key="phase2-supersession-remove-reactivated-synthetic",
    )
    final_remove_synthetic = final_remove_synthetic_response["correction"]
    after_final_synthetic_remove = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    assert synthetic_entity_id not in {
        value["entityId"] for value in after_final_synthetic_remove["scenes"]
    }
    assert next(
        value
        for value in after_final_synthetic_remove["scenes"]
        if value["entityId"] == add_source["entityId"]
    )["effectiveRevision"] == restored_visible_source["effectiveRevision"]

    restore_target = next(
        value
        for value in after_final_synthetic_remove["scenes"]
        if value["entityId"] == restore_source["entityId"]
    )
    removed_beat_ids = {
        value["entityId"]
        for value in after_final_synthetic_remove["beats"]
        if value["sceneId"] == restore_target["entityId"]
    }
    remove_source_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=restore_target,
        operation="remove",
        parent_entity_id=restore_target["chapterId"],
        ordinal=restore_target["ordinal"],
        idempotency_key="phase2-supersession-remove-source",
    )
    remove_source = remove_source_response["correction"]
    after_source_remove = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    _assert_structure_integrity(after_source_remove)
    assert restore_target["entityId"] not in {
        value["entityId"] for value in after_source_remove["scenes"]
    }
    assert removed_beat_ids.isdisjoint(
        {value["entityId"] for value in after_source_remove["beats"]}
    )

    restore_response = _append_structure_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        target=restore_target,
        operation="add",
        parent_entity_id=restore_target["chapterId"],
        ordinal=restore_target["ordinal"],
        expected_target_revision=restore_target["effectiveRevision"] + 1,
        previous_value_fingerprint=remove_source["correctedValueFingerprint"],
        supersedes_correction_id=remove_source["correctionId"],
        idempotency_key="phase2-supersession-restore-source",
    )
    restore_correction = restore_response["correction"]
    assert restore_correction["supersedesCorrectionId"] == remove_source["correctionId"]
    restored = _collections(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    _assert_structure_integrity(restored)
    assert len(restored["scenes"]) == len(baseline_scenes)
    restored_source = next(
        value
        for value in restored["scenes"]
        if value["entityId"] == restore_source["entityId"]
    )
    assert restored_source["stableSemanticId"] == restore_source["stableSemanticId"]
    assert restored_source["chapterId"] == restore_source["chapterId"]
    assert restored_source["effectiveBoundary"]["operation"] == "add"
    assert restored_source["effectiveBoundary"]["correctionId"] == (
        restore_correction["correctionId"]
    )
    assert restored_source["effectiveRevision"] == restore_source["effectiveRevision"] + 2
    assert restored_source["provenance"]["correctionId"] == (
        restore_correction["correctionId"]
    )
    assert sum(
        value["stableSemanticId"] == restore_source["stableSemanticId"]
        for value in restored["scenes"]
    ) == 1
    assert removed_beat_ids <= {
        value["entityId"] for value in restored["beats"]
    }

    corrections_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert corrections_response.status_code == 200, corrections_response.text
    corrections = corrections_response.json()["items"]
    correction_ids = {
        add_correction["correctionId"],
        source_label["correctionId"],
        synthetic_label["correctionId"],
        second_source_label["correctionId"],
        move_correction["correctionId"],
        remove_synthetic["correctionId"],
        move_visible["correctionId"],
        remove_visible["correctionId"],
        restore_visible["correctionId"],
        reactivate_synthetic["correctionId"],
        final_remove_synthetic["correctionId"],
        remove_source["correctionId"],
        restore_correction["correctionId"],
    }
    assert correction_ids <= {
        value["correctionId"] for value in corrections
    }
    assert len(correction_ids) == 13
    assert all(
        value["immutable"] and value["lockedAgainstAutomation"]
        for value in corrections
        if value["correctionId"] in correction_ids
    )
