from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

import pytest

from cinematic_story_service.errors import ServiceError
from cinematic_story_service.util import canonical_json, sha256_text
from cinematic_story_service.whole_book_analysis import (
    AGENT_REGISTRY,
    AGENT_REGISTRY_FINGERPRINT,
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_PRODUCER_VERSION,
    DEFAULT_ANALYSIS_PAGE_SIZE,
    DEFAULT_ANALYSIS_PROFILE,
    MAX_AGENT_ENVELOPE_BYTES,
    MAX_ANALYSIS_ENTITIES,
    MAX_ANALYSIS_PAGE_SIZE,
    MAX_ANALYSIS_WORDS,
    MAX_EVIDENCE_EXCERPT,
    MAX_EVIDENCE_SPANS,
    MAX_EXACT_TEXT_CODE_POINTS,
    MAX_SNAPSHOT_STAGES,
    MAX_SPEAKER_CANDIDATES,
    MAX_WARNINGS_PER_ENTITY,
    MAX_WHOLE_BOOK_CHECKPOINT_BYTES,
    AnalysisProfile,
    agent_envelopes,
    analyze_whole_book,
    decode_structure_resume_artifact,
)

_STORY_PATH = Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
_EXPECTED_COUNTS = {
    "chapters": 3,
    "scenes": 6,
    "beats": 25,
    "characters": 10,
    "mentions": 99,
    "dialogue-lines": 10,
    "narration-spans": 18,
    "pov-segments": 6,
    "locations": 6,
    "timeline-events": 6,
    "temporal-constraints": 5,
    "relationships": 11,
    "emotional-states": 5,
    "dramatic-intents": 10,
    "continuity-findings": 4,
}


def _analyze(*, registry_scope: str = "project-a") -> dict[str, object]:
    text = _STORY_PATH.read_text(encoding="utf-8")
    return analyze_whole_book(
        text=text,
        input_fingerprint=sha256_text(text),
        profile=DEFAULT_ANALYSIS_PROFILE,
        registry_scope=registry_scope,
        story_scope="canonical-story",
    )


def _analyze_text(text: str) -> dict[str, object]:
    return analyze_whole_book(
        text=text,
        input_fingerprint=sha256_text(text),
        profile=DEFAULT_ANALYSIS_PROFILE,
        registry_scope="semantic-test-project",
        story_scope="semantic-test-story",
    )


def test_canonical_fixture_is_exactly_deterministic_and_complete() -> None:
    first = _analyze()
    second = _analyze()

    assert first == second
    assert first["analysisContractVersion"] == ANALYSIS_CONTRACT_VERSION
    assert first["profileFingerprint"] == (
        "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec"
    )
    assert DEFAULT_ANALYSIS_PROFILE.to_wire()["limits"] == {
        "defaultPageSize": DEFAULT_ANALYSIS_PAGE_SIZE,
        "maximumAgentEnvelopeBytes": MAX_AGENT_ENVELOPE_BYTES,
        "maximumAnalysisEntities": MAX_ANALYSIS_ENTITIES,
        "maximumAnalysisWords": MAX_ANALYSIS_WORDS,
        "maximumAttributionCandidatesPerLine": MAX_SPEAKER_CANDIDATES,
        "maximumCheckpointBytes": MAX_WHOLE_BOOK_CHECKPOINT_BYTES,
        "maximumEvidenceExcerptCodePoints": MAX_EVIDENCE_EXCERPT,
        "maximumEvidenceSpansPerClaim": MAX_EVIDENCE_SPANS,
        "maximumExactTextCodePoints": MAX_EXACT_TEXT_CODE_POINTS,
        "maximumPageSize": MAX_ANALYSIS_PAGE_SIZE,
        "maximumSnapshotStages": MAX_SNAPSHOT_STAGES,
        "maximumWarningsPerEntity": MAX_WARNINGS_PER_ENTITY,
    }
    assert first["agentRegistryFingerprint"] == AGENT_REGISTRY_FINGERPRINT
    assert first["summary"]["collectionCounts"] == _EXPECTED_COUNTS  # type: ignore[index]
    assert len(AGENT_REGISTRY) == 11
    assert len(agent_envelopes(first)) == 11

    collections = first["collections"]  # type: ignore[assignment]
    assert all(
        relationship["payload"]["sourceCharacterId"] is not None
        and relationship["payload"]["targetCharacterId"] is not None
        and relationship["payload"]["resolution"] == "resolved"
        for relationship in collections["relationships"]
    )
    intent_kinds = {intent["payload"]["intent"] for intent in collections["dramatic-intents"]}
    assert {"command", "connect", "unknown"} <= intent_kinds


def test_structure_stage_artifact_resumes_exactly_and_fails_closed() -> None:
    text = _STORY_PATH.read_text(encoding="utf-8")
    fingerprint = sha256_text(text)
    observed: list[tuple[str, dict[str, object]]] = []
    fresh = analyze_whole_book(
        text=text,
        input_fingerprint=fingerprint,
        profile=DEFAULT_ANALYSIS_PROFILE,
        registry_scope="resume-project",
        story_scope="resume-story",
        stage_observer=lambda role, payload: observed.append((role, payload)),
    )
    structure_payload = next(payload for role, payload in observed if role == "structure")
    artifact = structure_payload["resumeArtifact"]
    assert isinstance(artifact, dict)

    resumed_roles: list[str] = []
    resumed = analyze_whole_book(
        text=text,
        input_fingerprint=fingerprint,
        profile=DEFAULT_ANALYSIS_PROFILE,
        registry_scope="resume-project",
        story_scope="resume-story",
        structure_resume_artifact=artifact,
        stage_observer=lambda role, _payload: resumed_roles.append(role),
    )
    assert resumed["outputFingerprint"] == fresh["outputFingerprint"]
    assert "structure" not in resumed_roles

    incompatible_bindings = (
        {
            "input_fingerprint": "f" * 64,
            "profile_fingerprint": DEFAULT_ANALYSIS_PROFILE.fingerprint,
            "correction_set_fingerprint": "0" * 64,
        },
        {
            "input_fingerprint": fingerprint,
            "profile_fingerprint": AnalysisProfile(semantic_version="incompatible").fingerprint,
            "correction_set_fingerprint": "0" * 64,
        },
        {
            "input_fingerprint": fingerprint,
            "profile_fingerprint": DEFAULT_ANALYSIS_PROFILE.fingerprint,
            "correction_set_fingerprint": "1" * 64,
        },
    )
    for bindings in incompatible_bindings:
        with pytest.raises(ServiceError) as incompatible:
            decode_structure_resume_artifact(
                artifact,
                text_length=len(text),
                **bindings,
            )
        assert incompatible.value.code == "CHECKPOINT_INCOMPATIBLE"

    raw_payload = json.loads(zlib.decompress(base64.b64decode(artifact["data"])).decode())
    assert raw_payload["producerVersion"] == ANALYSIS_PRODUCER_VERSION
    raw_payload["producerVersion"] = "whole-book-analysis-orchestrator@0.0.0"
    changed_json = canonical_json(raw_payload)
    version_mismatch_artifact = {
        **artifact,
        "payloadSha256": sha256_text(changed_json),
        "data": base64.b64encode(zlib.compress(changed_json.encode(), level=9)).decode(),
    }
    with pytest.raises(ServiceError) as version_mismatch:
        decode_structure_resume_artifact(
            version_mismatch_artifact,
            text_length=len(text),
            input_fingerprint=fingerprint,
            profile_fingerprint=DEFAULT_ANALYSIS_PROFILE.fingerprint,
            correction_set_fingerprint="0" * 64,
        )
    assert version_mismatch.value.code == "CHECKPOINT_INCOMPATIBLE"


def test_registry_ids_use_frozen_evidence_anchors_not_display_names() -> None:
    first_text = """# Chapter One

Mara Vale entered.
Mara: "Hold the relay."
"""
    relabeled_text = """# Chapter One

Nora Vale entered.
Nora: "Hold the relay."
"""
    first = analyze_whole_book(
        text=first_text,
        input_fingerprint=sha256_text(first_text),
        registry_scope="anchor-project",
        story_scope="anchor-story",
    )
    relabeled = analyze_whole_book(
        text=relabeled_text,
        input_fingerprint=sha256_text(relabeled_text),
        registry_scope="anchor-project",
        story_scope="anchor-story",
    )
    first_character = next(
        value
        for value in first["collections"]["characters"]
        if value["payload"]["canonicalName"] == "Mara Vale"
    )
    relabeled_character = next(
        value
        for value in relabeled["collections"]["characters"]
        if value["payload"]["canonicalName"] == "Nora Vale"
    )
    assert first_character["entityId"] == relabeled_character["entityId"]
    assert (
        first_character["payload"]["registryCharacterId"]
        == relabeled_character["payload"]["registryCharacterId"]
    )

    duplicate_text = """# Chapter One

Alex Reed entered. Beside him waited another Alex Reed.
Alex: "Hold."
"""
    duplicates = analyze_whole_book(
        text=duplicate_text,
        input_fingerprint=sha256_text(duplicate_text),
        registry_scope="anchor-project",
        story_scope="duplicate-story",
    )
    alexes = [
        value
        for value in duplicates["collections"]["characters"]
        if value["payload"]["normalizedCanonicalName"] == "alex reed"
    ]
    assert len(alexes) == 2
    assert len({value["entityId"] for value in alexes}) == 2
    assert len({value["payload"]["registryCharacterId"] for value in alexes}) == 2
    assert all(value["payload"]["mentionCount"] == 0 for value in alexes)
    assert all(value["payload"]["firstMentionId"] is None for value in alexes)
    assert all(value["payload"]["lastMentionId"] is None for value in alexes)
    assert all(value["payload"]["firstEvidence"] == [] for value in alexes)
    assert all(value["payload"]["lastEvidence"] == [] for value in alexes)
    assert all(value["payload"]["ambiguousMentionIds"] for value in alexes)


def test_machine_output_is_private_and_registry_ids_are_scope_stable() -> None:
    first = _analyze(registry_scope="project-a")
    compatible_rerun = _analyze(registry_scope="project-a")
    other_project = _analyze(registry_scope="project-b")

    first_characters = first["collections"]["characters"]  # type: ignore[index]
    rerun_characters = compatible_rerun["collections"]["characters"]  # type: ignore[index]
    other_characters = other_project["collections"]["characters"]  # type: ignore[index]
    assert [value["payload"]["characterId"] for value in first_characters] == [
        value["payload"]["characterId"] for value in rerun_characters
    ]
    assert {value["payload"]["characterId"] for value in first_characters}.isdisjoint(
        {value["payload"]["characterId"] for value in other_characters}
    )

    persisted_result = canonical_json(first)
    assert '"exactText"' not in persisted_result
    assert '"verbatimText"' not in persisted_result
    assert (
        "Captain Mira Vale crossed the Relay Platform while rain traced silver "
        "paths down the roof." not in persisted_result
    )


def test_vs206_through_vs229_semantic_domains_are_evidence_backed() -> None:
    text = _STORY_PATH.read_text(encoding="utf-8")
    result = _analyze()
    collections = result["collections"]  # type: ignore[assignment]

    chapters = collections["chapters"]
    scenes = collections["scenes"]
    assert [value["payload"]["sceneCount"] for value in chapters] == [2, 2, 2]
    assert all(
        value["startOffset"] < value["endOffset"] and value["evidence"]
        for value in [*chapters, *scenes]
    )
    assert all(
        value["payload"]["firstBeatId"] and value["payload"]["lastBeatId"] for value in scenes
    )

    characters = collections["characters"]
    aliases = [alias for character in characters for alias in character["payload"]["aliases"]]
    assert len(characters) >= 6
    assert any(alias["kind"] == "honorific" for alias in aliases)
    assert all(
        alias["evidence"]
        and alias["confidence"]["basis"]
        and alias["effectiveRange"]["sourceRange"]["endOffset"]
        > alias["effectiveRange"]["sourceRange"]["startOffset"]
        for alias in aliases
    )

    mentions = collections["mentions"]
    assert {"resolved", "ambiguous"} <= {value["payload"]["resolution"] for value in mentions}
    assert all(len(value["payload"]["candidateCharacterIds"]) <= 8 for value in mentions)
    assert all(text[value["startOffset"] : value["endOffset"]] for value in mentions)

    dialogue = collections["dialogue-lines"]
    narration = collections["narration-spans"]
    assert all(
        text[value["payload"]["quoteStartOffset"] : value["payload"]["quoteEndOffset"]]
        for value in dialogue
    )
    assert all(text[value["startOffset"] : value["endOffset"]] for value in narration)
    assert all(
        candidate["evidence"] for value in dialogue for candidate in value["payload"]["candidates"]
    )
    assert any(
        value["payload"]["candidates"][0]["basis"] == "explicit_name_speech_verb"
        and value["payload"]["candidates"][0]["score"] >= 0.9
        for value in dialogue
        if value["payload"]["candidates"]
    )
    assert any(
        value["payload"]["requiresHumanReview"]
        and value["payload"]["effectiveSpeakerId"] is None
        and len(value["payload"]["candidates"]) <= 8
        for value in dialogue
    )

    pov = collections["pov-segments"]
    assert "mixed" in {value["payload"]["mode"] for value in pov}
    assert any(value["payload"]["shiftKind"] == "uncertain" for value in pov)
    assert all(value["evidence"] for value in pov)

    locations = collections["locations"]
    assert len({value["payload"]["locationId"] for value in locations}) == len(locations)
    assert all(
        assignment["evidence"]
        for value in locations
        for assignment in value["payload"]["sceneAssignments"]
    )

    events = collections["timeline-events"]
    constraints = collections["temporal-constraints"]
    assert [value["payload"]["narrativeOrdinal"] for value in events] == list(range(len(events)))
    assert any(value["payload"]["orderingState"] == "unknown" for value in events)
    assert {"before", "unknown"} <= {value["payload"]["relation"] for value in constraints}
    for value in constraints:
        assert 1 <= len(value["evidence"]) <= MAX_EVIDENCE_SPANS
        for evidence in value["evidence"]:
            start = evidence["startOffset"]
            end = evidence["endOffset"]
            assert 0 <= start < end <= len(text)
            assert text[start:end]
    assert all("exactTimeExpression" not in value["payload"] for value in events)

    relationships = collections["relationships"]
    assert all(
        value["payload"]["sourceCharacterId"]
        and value["payload"]["targetCharacterId"]
        and value["payload"]["kind"]
        and value["evidence"]
        and value["confidence"]["basis"]
        for value in relationships
    )
    assert any("previousRelationshipId" in value["payload"] for value in relationships)

    assert all(value["evidence"] for value in collections["emotional-states"])
    assert all(value["evidence"] for value in collections["dramatic-intents"])
    continuity = collections["continuity-findings"]
    anomaly = next(
        value
        for value in continuity
        if value["payload"]["category"] == "unexplained_object_state_change"
    )
    assert anomaly["payload"]["previousValue"] == "shattered"
    assert anomaly["payload"]["currentValue"] == "unbroken"
    for value in continuity:
        assert 1 <= len(value["evidence"]) <= MAX_EVIDENCE_SPANS
        for evidence in value["evidence"]:
            start = evidence["startOffset"]
            end = evidence["endOffset"]
            assert 0 <= start < end <= len(text)
            assert text[start:end]


def test_beats_classify_bounded_purpose_without_inventing_unknowns() -> None:
    text = """# Chapter One

## Scene One: Signal Hall

At dawn, Mira waited by the gate.
"Ready?" Mira asked.
Mira crossed the hall.
"Go," Mira said.
The chamber was silent.
"Wait," Mira said.
Mira admitted the map was false.
"I understand," Mira said.
Blue silence.
"""
    first = _analyze_text(text)
    second = _analyze_text(text)
    assert first == second

    beats = first["collections"]["beats"]  # type: ignore[index]
    bases = {value["confidence"]["basis"] for value in beats}
    assert {
        "explicit_temporal_transition",
        "explicit_action_verb",
        "explicit_descriptive_state",
        "explicit_revelation_lexeme",
        "insufficient_beat_purpose_evidence",
    } <= bases
    assert {
        value["payload"]["kind"]
        for value in beats
        if value["confidence"]["basis"] != "explicit_revelation_lexeme"
    } >= {"action", "description", "transition"}
    revelation = next(
        value for value in beats if value["confidence"]["basis"] == "explicit_revelation_lexeme"
    )
    assert revelation["payload"]["kind"] == "narration"
    assert revelation["payload"]["summary"].startswith("Revelation narration beat;")
    unknown = next(
        value
        for value in beats
        if value["confidence"]["basis"] == "insufficient_beat_purpose_evidence"
    )
    assert unknown["confidence"]["classification"] == "unknown"
    assert unknown["payload"]["summary"].endswith("scene purpose remains unknown.")
    assert {value["code"] for value in unknown["warnings"]} == {"BEAT_PURPOSE_UNKNOWN"}
    for value in beats:
        assert value["evidence"]
        for evidence in value["evidence"]:
            assert text[evidence["startOffset"] : evidence["endOffset"]]
        if value["confidence"]["classification"] != "unknown":
            assert "bounded scene-purpose signal" in value["payload"]["summary"]


def test_same_named_locations_remain_scene_scoped_without_coreference() -> None:
    text = """# Chapter One

## Scene One: East Harbor

At Harbor, Mira waited.

## Scene Two: West Harbor

At Harbor, Tovin waited.
"""
    result = _analyze_text(text)
    locations = [
        value
        for value in result["collections"]["locations"]  # type: ignore[index]
        if value["payload"]["normalizedCanonicalName"] == "harbor"
    ]

    assert len(locations) == 2
    assert len({value["entityId"] for value in locations}) == 2
    assert all(value["payload"]["sceneCount"] == 1 for value in locations)
    assert all(
        {warning_value["code"] for warning_value in value["warnings"]}
        == {"LOCATION_IDENTITY_AMBIGUOUS"}
        for value in locations
    )
    assert all(
        value["confidence"]["basis"] == "scene_scoped_ambiguous_location_label"
        for value in locations
    )
    location_finding = next(
        value
        for value in result["collections"]["continuity-findings"]  # type: ignore[index]
        if value["payload"]["category"] == "location_conflict"
    )
    assert set(location_finding["payload"]["relatedEntityIds"]) == {
        value["entityId"] for value in locations
    }
    assert all(
        text[evidence["startOffset"] : evidence["endOffset"]]
        for evidence in location_finding["evidence"]
    )


def test_timeline_covers_exact_time_flashforward_relations_and_conflicts() -> None:
    text = """# Chapter One

## Scene One: Present

Mira waited.

## Scene Two: Conflicting Clock

Yesterday, Mira returned; tomorrow, she would leave.

## Scene Three: Appointment

At 09:30, Mira opened the gate.

## Scene Four: Forward

Tomorrow, Mira will cross the bridge.

## Scene Five: Backward

Years earlier, Mira crossed the bridge.

## Scene Six: Concurrent

Meanwhile, Tovin waited.
"""
    result = _analyze_text(text)
    events = result["collections"]["timeline-events"]  # type: ignore[index]
    constraints = result["collections"]["temporal-constraints"]  # type: ignore[index]

    assert {"flashback", "flashforward", "relative_time", "unknown"} <= {
        value["payload"]["kind"] for value in events
    }
    exact_time_event = next(
        value
        for value in events
        if value["confidence"]["basis"] == "explicit_date_or_time_expression"
    )
    assert exact_time_event["payload"]["timeExpressionState"] == "explicit"
    assert "exactTimeExpression" not in exact_time_event["payload"]
    assert any(
        text[evidence["startOffset"] : evidence["endOffset"]] == "At 09:30"
        for evidence in exact_time_event["evidence"]
    )
    assert {"before", "after", "overlaps", "unknown"} <= {
        value["payload"]["relation"] for value in constraints
    }
    conflicting = next(
        value for value in constraints if value["payload"]["status"] == "conflicting"
    )
    assert conflicting["payload"]["relation"] == "unknown"
    assert {value["code"] for value in conflicting["warnings"]} == {"TEMPORAL_SIGNALS_CONFLICT"}
    continuity_categories = {
        value["payload"]["category"]
        for value in result["collections"]["continuity-findings"]  # type: ignore[index]
    }
    assert continuity_categories >= {"chronology_conflict"}
    for value in [*events, *constraints]:
        assert value["evidence"]
        assert all(
            text[evidence["startOffset"] : evidence["endOffset"]] for evidence in value["evidence"]
        )


def test_emotion_and_intent_expose_urgency_restraint_and_subtext_as_warnings() -> None:
    text = """# Chapter One

## Scene One: Gate

Mira was afraid but kept her voice even; she had to leave now.
"We must go now," Mira said quietly.
"""
    result = _analyze_text(text)
    emotional_state = next(
        value
        for value in result["collections"]["emotional-states"]  # type: ignore[index]
        if value["payload"]["emotion"] == "fear"
    )
    emotion_warning_codes = {value["code"] for value in emotional_state["warnings"]}
    assert {
        "EMOTIONAL_URGENCY_CUE",
        "EMOTIONAL_RESTRAINT_CUE",
        "EMOTIONAL_SUBTEXT_REQUIRES_REVIEW",
    } <= emotion_warning_codes

    intent = result["collections"]["dramatic-intents"][0]  # type: ignore[index]
    intent_warning_codes = {value["code"] for value in intent["warnings"]}
    assert {
        "DRAMATIC_URGENCY_CUE",
        "DRAMATIC_RESTRAINT_CUE",
        "DRAMATIC_SUBTEXT_REQUIRES_REVIEW",
    } <= intent_warning_codes
    assert all(
        text[evidence["startOffset"] : evidence["endOffset"]]
        for evidence in [*emotional_state["evidence"], *intent["evidence"]]
    )


def test_curly_unicode_punctuation_preserves_urgency_and_imperative_cues() -> None:
    text = """# Chapter One

## Scene One: Gate

Mira Vale was afraid because she can’t wait.
Mira: “Go now.”
"""
    result = _analyze_text(text)
    fear = next(
        value
        for value in result["collections"]["emotional-states"]  # type: ignore[index]
        if value["payload"]["emotion"] == "fear"
    )
    assert "EMOTIONAL_URGENCY_CUE" in {value["code"] for value in fear["warnings"]}
    imperative = next(
        value
        for value in result["collections"]["beats"]  # type: ignore[index]
        if value["confidence"]["basis"] == "explicit_imperative_form"
    )
    assert text[imperative["startOffset"] : imperative["endOffset"]] == "“Go now.”"
    assert imperative["payload"]["summary"].endswith("issues a direct command.")


def test_continuity_uncertainty_categories_are_exactly_evidence_backed() -> None:
    identity_text = """# Chapter One

    Alex Reed entered the room. Beside him waited another Alex Reed. "Here," Alex said.
    The mark was \ufffd.
"This quotation never closes.
"""
    location_text = """# Chapter One

## Scene One: East Harbor

At Harbor, Alex waited.

## Scene Two: West Harbor

At Harbor, Tovin waited.
"""
    timeline_text = """# Chapter One

## Scene One: Present

Mira waited.

## Scene Two: Conflict

Yesterday, Mira returned; tomorrow, she would leave.
"""
    analyses = [
        (identity_text, _analyze_text(identity_text)),
        (location_text, _analyze_text(location_text)),
        (timeline_text, _analyze_text(timeline_text)),
    ]
    findings = [
        (source_text, finding)
        for source_text, result in analyses
        for finding in result["collections"]["continuity-findings"]  # type: ignore[index]
    ]
    categories = {value["payload"]["category"] for _text, value in findings}
    assert {
        "possible_duplicate_character",
        "chronology_conflict",
        "location_conflict",
        "pov_discontinuity",
        "scene_boundary_uncertainty",
        "extraction_uncertainty",
    } <= categories
    for source_text, value in findings:
        assert value["payload"]["relatedEntityIds"]
        assert 1 <= len(value["evidence"]) <= MAX_EVIDENCE_SPANS
        assert all(
            source_text[evidence["startOffset"] : evidence["endOffset"]]
            for evidence in value["evidence"]
        )
