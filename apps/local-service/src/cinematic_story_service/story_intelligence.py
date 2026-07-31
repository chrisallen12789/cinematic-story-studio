from __future__ import annotations

import base64
import binascii
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .database import Database
from .errors import ServiceError, not_found
from .models import (
    AnalysisAgentExecutionRow,
    AnalysisCorrectionRow,
    AnalysisEntityRow,
    AnalysisEvidenceSpanRow,
    AnalysisExecutionRow,
    AnalysisReviewDecisionRow,
    AnalysisRunRow,
    AnalysisSnapshotRow,
    AnalysisStageCheckpointRow,
    CharacterRow,
    DocumentExtractionRow,
    ImportedStoryRow,
    ImportReviewRow,
    JobAttemptRow,
    JobRow,
    ProjectRow,
    SourceDocumentRow,
)
from .projects import ProjectRepository
from .util import (
    canonical_json,
    new_id,
    parse_json,
    provenance,
    request_fingerprint,
    sha256_text,
    stable_id,
    utc_now,
)
from .whole_book_analysis import (
    AGENT_REGISTRY,
    AGENT_REGISTRY_FINGERPRINT,
    ANALYSIS_CONTRACT_VERSION,
    CORRECTION_CATEGORIES,
    DEFAULT_ANALYSIS_PAGE_SIZE,
    ENTITY_COLLECTIONS,
    MAX_AGENT_ENVELOPE_BYTES,
    MAX_ANALYSIS_ENTITIES,
    MAX_ANALYSIS_PAGE_SIZE,
    MAX_EVIDENCE_EXCERPT,
    MAX_EVIDENCE_SPANS,
    MAX_EXACT_TEXT_CODE_POINTS,
    MAX_SNAPSHOT_STAGES,
    MAX_WARNINGS_PER_ENTITY,
    REVIEW_GATES,
    agent_envelopes,
    stage_snapshots,
)

MAX_CORRECTION_PATCH_BYTES = 16_384
MAX_EFFECTIVE_PROJECTION_TARGETS = 4_096
_BOUNDED_PROJECTION_CATEGORIES = frozenset(
    {"structure_boundary", "character_merge", "character_split"}
)


@dataclass(slots=True)
class _EffectiveStructureGraph:
    chapter_target_ids: tuple[str, ...]
    scene_target_ids: tuple[str, ...]
    removed_chapter_ids: frozenset[str]
    removed_scene_ids: frozenset[str]
    active_chapter_ids: frozenset[str]
    active_scene_ids: frozenset[str]
    effective_scene_parents: dict[str, str | None]
    chapter_items_by_source: dict[str, list[dict[str, Any]]]
    scene_items_by_source: dict[str, list[dict[str, Any]]]
    removed_entity_ids: dict[str, frozenset[str]]
    mention_items_by_character: dict[str, tuple[dict[str, Any], ...]]
    ambiguous_mention_items_by_character: dict[str, tuple[dict[str, Any], ...]]
    mention_index_loaded: bool


@dataclass(slots=True)
class _StructureBoundaryState:
    source_active: bool
    source_effects: tuple[tuple[AnalysisCorrectionRow, dict[str, Any]], ...]
    source_revision_count: int
    synthetic_root: AnalysisCorrectionRow | None
    synthetic_active: bool
    synthetic_effects: tuple[tuple[AnalysisCorrectionRow, dict[str, Any]], ...]
    synthetic_revision_count: int
    branch_by_correction_id: dict[str, str]

    @property
    def source_effect(self) -> tuple[AnalysisCorrectionRow, dict[str, Any]] | None:
        return self.source_effects[-1] if self.source_effects else None

    @property
    def synthetic_effect(self) -> tuple[AnalysisCorrectionRow, dict[str, Any]] | None:
        return self.synthetic_effects[-1] if self.synthetic_effects else None


@dataclass(frozen=True, slots=True)
class _RegistryReplacementEffect:
    latest_correction: AnalysisCorrectionRow
    corrections: tuple[AnalysisCorrectionRow, ...]


def _correction_order_key(value: AnalysisCorrectionRow) -> tuple[str, int, str]:
    return value.recorded_at, value.revision, value.id


def _correction_at_or_before_condition(value: AnalysisCorrectionRow) -> Any:
    return or_(
        AnalysisCorrectionRow.recorded_at < value.recorded_at,
        and_(
            AnalysisCorrectionRow.recorded_at == value.recorded_at,
            AnalysisCorrectionRow.revision < value.revision,
        ),
        and_(
            AnalysisCorrectionRow.recorded_at == value.recorded_at,
            AnalysisCorrectionRow.revision == value.revision,
            AnalysisCorrectionRow.id <= value.id,
        ),
    )


def _mention_item_order_key(value: Mapping[str, Any]) -> tuple[int, int, str]:
    evidence = value.get("evidence")
    first_evidence = (
        evidence[0]
        if isinstance(evidence, list)
        and evidence
        and isinstance(evidence[0], dict)
        else {}
    )
    return (
        int(first_evidence.get("startOffset", 0)),
        int(first_evidence.get("endOffset", 0)),
        str(value.get("entityId")),
    )


_GATE_COLLECTIONS: dict[str, frozenset[str]] = {
    "story_structure_review": frozenset(
        {"chapters", "scenes", "beats", "narration-spans", "pov-segments"}
    ),
    "character_registry_review": frozenset({"characters", "mentions", "relationships"}),
    "dialogue_attribution_review": frozenset({"dialogue-lines"}),
    "whole_book_analysis_review": frozenset(ENTITY_COLLECTIONS),
}
_CORRECTION_GATES: dict[str, tuple[str, ...]] = {
    "structure_boundary": ("story_structure_review", "whole_book_analysis_review"),
    "structure_label": ("story_structure_review", "whole_book_analysis_review"),
    "character_identity": ("character_registry_review", "whole_book_analysis_review"),
    "character_alias": ("character_registry_review", "whole_book_analysis_review"),
    "character_merge": ("character_registry_review", "whole_book_analysis_review"),
    "character_split": ("character_registry_review", "whole_book_analysis_review"),
    "mention_resolution": ("character_registry_review", "whole_book_analysis_review"),
    "dialogue_speaker": ("dialogue_attribution_review", "whole_book_analysis_review"),
    "point_of_view": ("story_structure_review", "whole_book_analysis_review"),
    "location_identity": ("whole_book_analysis_review",),
    "location_alias": ("whole_book_analysis_review",),
    "temporal_order": ("whole_book_analysis_review",),
    "relationship": ("character_registry_review", "whole_book_analysis_review"),
    "emotional_state": ("whole_book_analysis_review",),
    "dramatic_intent": ("whole_book_analysis_review",),
    "continuity_disposition": ("whole_book_analysis_review",),
}
_CONTINUITY_DISPOSITIONS = frozenset(
    {
        "confirmed_issue",
        "intentional",
        "false_positive",
        "deferred",
        "corrected",
        "unresolved",
    }
)

_COLLECTION_AGENT_ROLE = {
    collection: definition.role
    for definition in AGENT_REGISTRY
    for collection in definition.collections
}
_COLLECTION_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "chapters": (
        "chapterId",
        "title",
        "firstSceneId",
        "lastSceneId",
        "sceneCount",
        "effectiveBoundary",
    ),
    "scenes": (
        "sceneId",
        "chapterId",
        "heading",
        "boundaryKind",
        "firstBeatId",
        "lastBeatId",
        "beatCount",
        "effectiveBoundary",
    ),
    "beats": (
        "beatId",
        "chapterId",
        "sceneId",
        "kind",
        "summary",
        "dialogueLineId",
        "narration",
    ),
    "characters": (
        "characterId",
        "registryCharacterId",
        "projectId",
        "storyId",
        "registryScope",
        "stableAcrossCompatibleRuns",
        "canonicalName",
        "normalizedCanonicalName",
        "kind",
        "identityStatus",
        "aliases",
        "honorifics",
        "pronounEvidence",
        "firstMentionId",
        "lastMentionId",
        "namedMentionIds",
        "ambiguousMentionIds",
        "firstEvidence",
        "lastEvidence",
        "mentionCount",
        "effectiveRegistry",
    ),
    "mentions": (
        "mentionId",
        "chapterId",
        "sceneId",
        "mentionKind",
        "resolution",
        "effectiveCharacterId",
        "candidateCharacterIds",
    ),
    "dialogue-lines": (
        "dialogueLineId",
        "chapterId",
        "sceneId",
        "beatId",
        "distinction",
        "speakerState",
        "candidates",
        "effectiveAttribution",
    ),
    "narration-spans": (
        "narrationSpanId",
        "chapterId",
        "sceneId",
        "classification",
        "narratorCharacterId",
    ),
    "pov-segments": (
        "povSegmentId",
        "chapterId",
        "sceneId",
        "mode",
        "viewpointCharacterId",
        "narratorCharacterId",
        "shiftFromPovSegmentId",
        "shiftKind",
    ),
    "locations": (
        "locationId",
        "canonicalName",
        "normalizedCanonicalName",
        "aliases",
        "kind",
        "parentLocationId",
        "firstSceneId",
        "sceneIds",
        "sceneAssignments",
        "sceneCount",
    ),
    "timeline-events": (
        "timelineEventId",
        "chapterId",
        "sceneId",
        "kind",
        "label",
        "narrativeOrdinal",
        "chronologicalOrdinal",
        "locationId",
        "participantCharacterIds",
    ),
    "temporal-constraints": (
        "temporalConstraintId",
        "sourceEventId",
        "targetEventId",
        "relation",
        "approximate",
        "status",
    ),
    "relationships": (
        "relationshipId",
        "sourceCharacterId",
        "targetCharacterId",
        "sourceCandidateCharacterIds",
        "targetCandidateCharacterIds",
        "resolution",
        "sceneId",
        "chapterId",
        "scope",
        "validFromEventId",
        "validThroughEventId",
        "kind",
        "state",
        "change",
        "previousRelationshipId",
    ),
    "emotional-states": (
        "emotionalStateId",
        "subjectType",
        "characterId",
        "sceneId",
        "emotion",
        "customEmotion",
        "note",
        "valence",
        "arousal",
        "intensity",
        "progression",
        "previousEmotionalStateId",
    ),
    "dramatic-intents": (
        "dramaticIntentId",
        "subjectType",
        "characterId",
        "sceneId",
        "beatId",
        "dialogueLineId",
        "intent",
        "customIntent",
        "dramaticFunction",
        "customDramaticFunction",
        "note",
        "targetCharacterId",
        "status",
    ),
    "continuity-findings": (
        "continuityFindingId",
        "category",
        "severity",
        "machineStatus",
        "explanation",
        "suggestedReviewAction",
        "relatedEntityIds",
        "requiresHumanReview",
        "humanDisposition",
    ),
}
_RAW_ANALYSIS_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    collection: frozenset(fields) for collection, fields in _COLLECTION_PAYLOAD_FIELDS.items()
}
_RAW_ANALYSIS_PAYLOAD_FIELDS.update(
    {
        "dialogue-lines": _RAW_ANALYSIS_PAYLOAD_FIELDS["dialogue-lines"]
        | frozenset(
            {
                "proposedSpeakerId",
                "effectiveSpeakerId",
                "effectiveAuthority",
                "quoteStartOffset",
                "quoteEndOffset",
                "requiresHumanReview",
            }
        ),
        "narration-spans": _RAW_ANALYSIS_PAYLOAD_FIELDS["narration-spans"]
        | frozenset({"beatId", "subtype"}),
        "pov-segments": _RAW_ANALYSIS_PAYLOAD_FIELDS["pov-segments"]
        | frozenset({"povType", "focalCharacterId", "effectiveAuthority"}),
        "locations": _RAW_ANALYSIS_PAYLOAD_FIELDS["locations"]
        | frozenset({"displayName", "normalizedName"}),
        "timeline-events": _RAW_ANALYSIS_PAYLOAD_FIELDS["timeline-events"]
        | frozenset({"storyOrdinal", "orderingState", "timeExpressionState"}),
        "temporal-constraints": _RAW_ANALYSIS_PAYLOAD_FIELDS["temporal-constraints"]
        | frozenset({"fromEventId", "toEventId"}),
        "continuity-findings": _RAW_ANALYSIS_PAYLOAD_FIELDS["continuity-findings"]
        | frozenset(
            {
                "count",
                "currentValue",
                "disposition",
                "effectiveAuthority",
                "kind",
                "previousValue",
                "status",
                "subject",
                "suggestedAction",
            }
        ),
    }
)
_RAW_ANALYSIS_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "chapters": frozenset({"chapterId", "title", "firstSceneId", "lastSceneId", "sceneCount"}),
    "scenes": frozenset(
        {
            "sceneId",
            "chapterId",
            "heading",
            "boundaryKind",
            "firstBeatId",
            "lastBeatId",
            "beatCount",
        }
    ),
    "beats": frozenset(
        {
            "beatId",
            "chapterId",
            "sceneId",
            "kind",
            "summary",
            "dialogueLineId",
        }
    ),
    "characters": frozenset(
        {
            "characterId",
            "registryCharacterId",
            "projectId",
            "storyId",
            "registryScope",
            "stableAcrossCompatibleRuns",
            "canonicalName",
            "normalizedCanonicalName",
            "kind",
            "identityStatus",
            "aliases",
            "honorifics",
            "pronounEvidence",
            "firstMentionId",
            "lastMentionId",
            "namedMentionIds",
            "ambiguousMentionIds",
            "firstEvidence",
            "lastEvidence",
            "mentionCount",
        }
    ),
    "mentions": frozenset(
        {
            "mentionId",
            "chapterId",
            "sceneId",
            "mentionKind",
            "resolution",
            "effectiveCharacterId",
            "candidateCharacterIds",
        }
    ),
    "dialogue-lines": frozenset(
        {
            "dialogueLineId",
            "chapterId",
            "sceneId",
            "beatId",
            "distinction",
            "speakerState",
            "candidates",
            "effectiveAttribution",
            "proposedSpeakerId",
            "effectiveSpeakerId",
            "effectiveAuthority",
            "quoteStartOffset",
            "quoteEndOffset",
            "requiresHumanReview",
        }
    ),
    "narration-spans": frozenset(
        {
            "narrationSpanId",
            "chapterId",
            "sceneId",
            "classification",
            "subtype",
            "narratorCharacterId",
        }
    ),
    "pov-segments": frozenset(
        {
            "povSegmentId",
            "chapterId",
            "sceneId",
            "mode",
            "povType",
            "viewpointCharacterId",
            "focalCharacterId",
            "narratorCharacterId",
            "shiftFromPovSegmentId",
            "shiftKind",
            "effectiveAuthority",
        }
    ),
    "locations": frozenset(
        {
            "locationId",
            "canonicalName",
            "normalizedCanonicalName",
            "displayName",
            "normalizedName",
            "aliases",
            "kind",
            "parentLocationId",
            "firstSceneId",
            "sceneIds",
            "sceneAssignments",
            "sceneCount",
        }
    ),
    "timeline-events": frozenset(
        {
            "timelineEventId",
            "chapterId",
            "sceneId",
            "kind",
            "label",
            "narrativeOrdinal",
            "chronologicalOrdinal",
            "storyOrdinal",
            "orderingState",
            "timeExpressionState",
            "locationId",
            "participantCharacterIds",
        }
    ),
    "temporal-constraints": frozenset(
        {
            "temporalConstraintId",
            "sourceEventId",
            "targetEventId",
            "fromEventId",
            "toEventId",
            "relation",
            "approximate",
            "status",
        }
    ),
    "relationships": frozenset(
        {
            "relationshipId",
            "sourceCharacterId",
            "targetCharacterId",
            "sourceCandidateCharacterIds",
            "targetCandidateCharacterIds",
            "resolution",
            "sceneId",
            "chapterId",
            "scope",
            "validFromEventId",
            "validThroughEventId",
            "kind",
            "state",
            "change",
        }
    ),
    "emotional-states": frozenset(
        {
            "emotionalStateId",
            "subjectType",
            "sceneId",
            "emotion",
            "note",
            "valence",
            "arousal",
            "intensity",
            "progression",
        }
    ),
    "dramatic-intents": frozenset(
        {
            "dramaticIntentId",
            "subjectType",
            "sceneId",
            "dialogueLineId",
            "intent",
            "dramaticFunction",
            "note",
            "status",
        }
    ),
    "continuity-findings": frozenset(
        {
            "continuityFindingId",
            "category",
            "kind",
            "severity",
            "machineStatus",
            "status",
            "disposition",
            "explanation",
            "suggestedReviewAction",
            "suggestedAction",
            "relatedEntityIds",
            "requiresHumanReview",
            "effectiveAuthority",
        }
    ),
}
_RAW_ANALYSIS_OPTIONAL_FIELDS: dict[str, frozenset[str]] = {
    collection: frozenset() for collection in ENTITY_COLLECTIONS
}
_RAW_ANALYSIS_OPTIONAL_FIELDS.update(
    {
        "narration-spans": frozenset({"beatId"}),
        "relationships": frozenset({"previousRelationshipId"}),
        "emotional-states": frozenset({"characterId", "customEmotion", "previousEmotionalStateId"}),
        "continuity-findings": frozenset({"count", "currentValue", "previousValue", "subject"}),
    }
)
_PRIMARY_PAYLOAD_ID = {
    "chapters": "chapterId",
    "scenes": "sceneId",
    "beats": "beatId",
    "characters": "characterId",
    "mentions": "mentionId",
    "dialogue-lines": "dialogueLineId",
    "narration-spans": "narrationSpanId",
    "pov-segments": "povSegmentId",
    "locations": "locationId",
    "timeline-events": "timelineEventId",
    "temporal-constraints": "temporalConstraintId",
    "relationships": "relationshipId",
    "emotional-states": "emotionalStateId",
    "dramatic-intents": "dramaticIntentId",
    "continuity-findings": "continuityFindingId",
}
_PARENT_COLLECTION = {
    "scenes": "chapters",
    "beats": "scenes",
    "dialogue-lines": "scenes",
    "pov-segments": "scenes",
    "timeline-events": "scenes",
    "emotional-states": "scenes",
    "dramatic-intents": "dialogue-lines",
}
_SOURCE_SPAN_COLLECTIONS = frozenset({"chapters", "scenes", "beats", "pov-segments"})
_EXACT_TEXT_COLLECTIONS = frozenset({"mentions", "dialogue-lines", "narration-spans"})
_OPTIONAL_NON_NULL_ENTITY_FIELDS = frozenset(
    {
        "title",
        "heading",
        "dialogueLineId",
        "narration",
        "shiftFromPovSegmentId",
        "exactTimeExpression",
        "previousRelationshipId",
        "characterId",
        "customEmotion",
        "previousEmotionalStateId",
        "beatId",
        "customIntent",
        "customDramaticFunction",
        "targetCharacterId",
        "humanDisposition",
    }
)
_COUNT_KEY_BY_COLLECTION = {
    "chapters": "chapters",
    "scenes": "scenes",
    "beats": "beats",
    "characters": "characters",
    "mentions": "mentions",
    "dialogue-lines": "dialogueLines",
    "narration-spans": "narrationSpans",
    "pov-segments": "povSegments",
    "locations": "locations",
    "timeline-events": "timelineEvents",
    "temporal-constraints": "temporalConstraints",
    "relationships": "relationships",
    "emotional-states": "emotionalStates",
    "dramatic-intents": "dramaticIntents",
    "continuity-findings": "continuityFindings",
}
_CORRECTION_COLLECTION = {
    "structure_boundary": frozenset({"chapters", "scenes"}),
    "structure_label": frozenset({"chapters", "scenes"}),
    "character_identity": frozenset({"characters"}),
    "character_alias": frozenset({"characters"}),
    "character_merge": frozenset({"characters"}),
    "character_split": frozenset({"characters"}),
    "mention_resolution": frozenset({"mentions"}),
    "dialogue_speaker": frozenset({"dialogue-lines"}),
    "point_of_view": frozenset({"pov-segments"}),
    "location_identity": frozenset({"locations"}),
    "location_alias": frozenset({"locations"}),
    "temporal_order": frozenset({"temporal-constraints"}),
    "relationship": frozenset({"relationships"}),
    "emotional_state": frozenset({"emotional-states"}),
    "dramatic_intent": frozenset({"dramatic-intents"}),
    "continuity_disposition": frozenset({"continuity-findings"}),
}
_POV_MODES = frozenset(
    {
        "first_person",
        "second_person",
        "third_person_limited",
        "third_person_omniscient",
        "mixed",
        "experimental",
        "unknown",
    }
)
_LOCATION_KINDS = frozenset({"interior", "exterior", "vehicle", "region", "abstract", "unknown"})
_TEMPORAL_RELATIONS = frozenset(
    {"before", "after", "same_time", "overlaps", "during", "contains", "unknown"}
)
_TEMPORAL_STATUSES = frozenset({"consistent", "conflicting", "unresolved"})
_RELATIONSHIP_KINDS = frozenset(
    {
        "family",
        "friendship",
        "romantic",
        "professional",
        "adversarial",
        "authority",
        "dependency",
        "alliance",
        "unknown",
        "custom",
    }
)
_RELATIONSHIP_CHANGES = frozenset(
    {"established", "strengthened", "weakened", "reversed", "unchanged", "uncertain"}
)
_EMOTIONS = frozenset(
    {
        "fear",
        "anger",
        "sadness",
        "joy",
        "surprise",
        "calm",
        "disgust",
        "anticipation",
        "trust",
        "confusion",
        "hope",
        "guilt",
        "shame",
        "grief",
        "relief",
        "neutral",
        "mixed",
        "unknown",
        "custom",
    }
)
_EMOTION_PROGRESSIONS = frozenset(
    {"initial", "rising", "falling", "shifted", "stable", "uncertain"}
)
_DRAMATIC_INTENTS = frozenset(
    {
        "question",
        "direct",
        "persuade",
        "reassure",
        "reveal",
        "conceal",
        "deflect",
        "threaten",
        "comfort",
        "seek_information",
        "command",
        "negotiate",
        "connect",
        "withdraw",
        "deceive",
        "unknown",
        "custom",
    }
)
_DRAMATIC_FUNCTIONS = frozenset(
    {
        "setup",
        "inciting_action",
        "complication",
        "reversal",
        "revelation",
        "crisis",
        "climax",
        "resolution",
        "transition",
        "character_development",
        "relationship_change",
        "tension",
        "comic_relief",
        "exposition",
        "foreshadowing",
        "unknown",
        "custom",
    }
)
_INTENT_STATUSES = frozenset(
    {"pursued", "achieved", "blocked", "abandoned", "concealed", "uncertain"}
)


def _cursor_binding(value: dict[str, Any]) -> str:
    return request_fingerprint(value)[:24]


def _encode_cursor(binding: str, ordinal: int) -> str:
    raw = f"v2:{binding}:{ordinal}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(binding: str, cursor: str | None) -> int:
    if cursor is None:
        return -1
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        version, cursor_binding, raw_ordinal = (
            base64.urlsafe_b64decode(padded).decode().split(":", 2)
        )
        ordinal = int(raw_ordinal)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ServiceError(400, "INVALID_CURSOR", "The analysis cursor is invalid.") from exc
    if version != "v2" or cursor_binding != binding or ordinal < -1:
        raise ServiceError(400, "INVALID_CURSOR", "The analysis cursor is invalid.")
    return ordinal


def _encode_key_cursor(
    binding: str,
    *,
    recorded_at: str,
    entity_id: str,
) -> str:
    raw = canonical_json(
        {
            "binding": binding,
            "id": entity_id,
            "recordedAt": recorded_at,
            "version": 2,
        }
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_key_cursor(
    binding: str,
    cursor: str | None,
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        value = parse_json(base64.urlsafe_b64decode(padded).decode(), {})
    except (UnicodeDecodeError, binascii.Error) as exc:
        raise ServiceError(
            400,
            "INVALID_CURSOR",
            "The analysis cursor is invalid.",
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"binding", "id", "recordedAt", "version"}
        or value.get("version") != 2
        or value.get("binding") != binding
        or not isinstance(value.get("recordedAt"), str)
        or not isinstance(value.get("id"), str)
        or not value["recordedAt"]
        or not value["id"]
    ):
        raise ServiceError(400, "INVALID_CURSOR", "The analysis cursor is invalid.")
    return str(value["recordedAt"]), str(value["id"])


def _source_span(
    *,
    run: AnalysisRunRow,
    story: ImportedStoryRow,
    start: int,
    end: int,
) -> dict[str, Any]:
    safe_start = min(max(0, start), len(story.exact_text))
    safe_end = min(max(safe_start, end), len(story.exact_text))
    complete_text = story.exact_text[safe_start:safe_end]
    return {
        "sourceDocumentId": story.source_document_id,
        "extractionId": run.extraction_id,
        "extractionRevision": run.extraction_revision,
        "offsetUnit": "unicode-code-point",
        "startOffset": safe_start,
        "endOffset": safe_end,
        "textSha256": sha256_text(complete_text),
    }


def _exact_analysis_text(
    *,
    run: AnalysisRunRow,
    story: ImportedStoryRow,
    start: int,
    end: int,
) -> dict[str, Any]:
    span = _source_span(run=run, story=story, start=start, end=end)
    complete_text = story.exact_text[span["startOffset"] : span["endOffset"]]
    exact_text = complete_text[:MAX_EXACT_TEXT_CODE_POINTS]
    return {
        **span,
        "exactText": exact_text,
        "exactTextSha256": sha256_text(exact_text),
        "originalCodePointCount": len(complete_text),
        "exactTextTruncated": len(complete_text) > len(exact_text),
        "originalTextPreserved": True,
    }


def _bounded_excerpt(
    *,
    run: AnalysisRunRow,
    story: ImportedStoryRow,
    start: int,
    end: int,
) -> dict[str, Any]:
    span = _source_span(run=run, story=story, start=start, end=end)
    safe_start = int(span["startOffset"])
    safe_end = int(span["endOffset"])
    exact = story.exact_text[safe_start:safe_end]
    truncated = len(exact) > MAX_EVIDENCE_EXCERPT
    excerpt = exact[:MAX_EVIDENCE_EXCERPT]
    # Preserve common manuscript whitespace but render other controls visibly.
    excerpt = "".join(
        character
        if ord(character) >= 32 or character in {"\n", "\r", "\t"}
        else "\N{REPLACEMENT CHARACTER}"
        for character in excerpt
    )
    return {
        **span,
        "excerptStartOffset": safe_start,
        "excerptEndOffset": safe_start + len(excerpt),
        "excerptText": excerpt,
        "excerptSha256": sha256_text(excerpt),
        "excerptTruncated": truncated,
    }


def _bounded_wire_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_EXACT_TEXT_CODE_POINTS:
            return value
        return value[:MAX_EXACT_TEXT_CODE_POINTS]
    if isinstance(value, list):
        return [_bounded_wire_value(item) for item in value]
    if isinstance(value, dict):
        result = {key: _bounded_wire_value(item) for key, item in value.items()}
        for field in ("exactText", "verbatimText"):
            original = value.get(field)
            if isinstance(original, str) and len(original) > MAX_EXACT_TEXT_CODE_POINTS:
                result[f"{field}Truncated"] = True
                result[f"{field}CodePointCount"] = len(original)
        return result
    return value


_STABLE_REGISTRY_ID_FIELDS = frozenset({"registryCharacterId"})


def _replace_entity_ids(
    value: Any,
    entity_ids: Mapping[str, str],
    *,
    field_name: str | None = None,
) -> Any:
    if isinstance(value, str):
        if field_name in _STABLE_REGISTRY_ID_FIELDS:
            return value
        return entity_ids.get(value, value)
    if isinstance(value, list):
        return [_replace_entity_ids(item, entity_ids, field_name=field_name) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_entity_ids(item, entity_ids, field_name=key)
            for key, item in value.items()
        }
    return value


def _correction_patch_invalid() -> ServiceError:
    return ServiceError(
        422,
        "CORRECTION_PATCH_INVALID",
        "The correction patch does not match its category contract.",
    )


def _analysis_output_invalid() -> ServiceError:
    return ServiceError(
        500,
        "ANALYSIS_OUTPUT_INVALID",
        "The whole-book analysis output is invalid.",
        retryable=False,
    )


def _has_exact_keys(value: dict[str, Any], required: set[str]) -> bool:
    return set(value) == required


def _valid_id(value: Any, *, nullable: bool = False) -> bool:
    return (
        nullable
        and value is None
        or isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(ord(character) >= 32 for character in value)
    )


def _valid_string(
    value: Any,
    *,
    minimum: int = 1,
    maximum: int,
    nullable: bool = False,
) -> bool:
    return (
        nullable
        and value is None
        or isinstance(value, str)
        and minimum <= len(value) <= maximum
        and all(ord(character) >= 32 or character == "\t" for character in value)
    )


def _valid_number(value: Any, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= float(value) <= maximum
    )


def _valid_unique_ids(
    value: Any,
    *,
    minimum: int = 0,
    maximum: int,
) -> bool:
    return (
        isinstance(value, list)
        and minimum <= len(value) <= maximum
        and len(value) == len(set(value))
        and all(_valid_id(item) for item in value)
    )


def _valid_source_span(
    value: Any,
    *,
    run: AnalysisRunRow,
    story: ImportedStoryRow,
) -> bool:
    if not isinstance(value, dict) or not _has_exact_keys(
        value,
        {
            "sourceDocumentId",
            "extractionId",
            "extractionRevision",
            "offsetUnit",
            "startOffset",
            "endOffset",
            "textSha256",
        },
    ):
        return False
    start = value.get("startOffset")
    end = value.get("endOffset")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(story.exact_text)
    ):
        return False
    return (
        value.get("sourceDocumentId") == run.source_document_id
        and value.get("extractionId") == run.extraction_id
        and value.get("extractionRevision") == run.extraction_revision
        and value.get("offsetUnit") == "unicode-code-point"
        and value.get("textSha256") == sha256_text(story.exact_text[start:end])
    )


def _require_patch_shape(
    value: dict[str, Any],
    *,
    keys: set[str],
    predicates: dict[str, bool],
) -> None:
    if not _has_exact_keys(value, keys) or not all(predicates.values()):
        raise _correction_patch_invalid()


def _apply_correction_patch(
    payload: dict[str, Any],
    correction: AnalysisCorrectionRow | None,
    *,
    effective_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if correction is None:
        return dict(payload)
    patch = (
        effective_patch if effective_patch is not None else parse_json(correction.patch_json, {})
    )
    if not isinstance(patch, dict):
        return dict(payload)
    result = dict(payload)
    if correction.category == "character_alias":
        aliases = [dict(value) for value in result.get("aliases", []) if isinstance(value, dict)]
        operation = patch.get("operation")
        if operation == "remove":
            aliases = [value for value in aliases if value.get("aliasId") != patch.get("aliasId")]
        elif operation in {"add", "replace"} and isinstance(
            patch.get("alias"),
            dict,
        ):
            corrected_alias = dict(patch["alias"])
            if operation == "replace":
                aliases = [
                    value
                    for value in aliases
                    if value.get("aliasId") != corrected_alias.get("aliasId")
                ]
            aliases.append(corrected_alias)
        result["aliases"] = aliases
    elif correction.category == "location_alias":
        location_aliases = [
            str(value) for value in result.get("aliases", []) if isinstance(value, str)
        ]
        alias = patch.get("alias")
        if isinstance(alias, str):
            if patch.get("operation") == "add":
                location_aliases.append(alias)
            elif patch.get("operation") == "remove":
                location_aliases = [value for value in location_aliases if value != alias]
        result["aliases"] = list(dict.fromkeys(location_aliases))
    elif correction.category == "dialogue_speaker":
        attribution = result.get("effectiveAttribution")
        if not isinstance(attribution, dict):
            attribution = {}
        result["effectiveAttribution"] = {
            **attribution,
            **{
                key: patch[key]
                for key in (
                    "speakerCharacterId",
                    "selectedCandidateId",
                    "requiresHumanReview",
                )
                if key in patch
            },
            "authority": "human_correction",
            "correctionId": correction.id,
        }
        result["speakerState"] = "corrected"
        result["effectiveSpeakerId"] = patch.get("speakerCharacterId")
        result["effectiveAuthority"] = "human"
    elif correction.category == "structure_boundary":
        operation = patch.get("operation")
        result["effectiveBoundary"] = {
            "operation": operation,
            "included": operation != "remove",
            "parentEntityId": patch.get("parentEntityId"),
            "ordinal": patch.get("ordinal"),
            "sourceSpan": patch.get("sourceSpan"),
            "authority": "human",
            "correctionId": correction.id,
        }
        result["_effectiveSourceSpan"] = patch.get("sourceSpan")
        result["_effectiveOrdinal"] = patch.get("ordinal")
        if correction.target_key.startswith("scenes:"):
            result["chapterId"] = patch.get("parentEntityId")
            result["boundaryKind"] = patch.get("boundaryKind")
    elif correction.category == "character_merge":
        result["effectiveRegistry"] = {
            "operation": "merge",
            "authority": "human",
            "correctionId": correction.id,
            "mergeIntoCharacterId": patch.get("mergeIntoCharacterId"),
        }
    elif correction.category == "character_split":
        result["effectiveRegistry"] = {
            "operation": "split",
            "authority": "human",
            "correctionId": correction.id,
            "splitIdentity": {
                "registryCharacterId": patch.get("newRegistryCharacterId"),
                "canonicalName": patch.get("canonicalName"),
                "normalizedCanonicalName": patch.get("normalizedCanonicalName"),
                "mentionIds": patch.get("mentionIds"),
            },
        }
    elif correction.category == "continuity_disposition":
        pass
    else:
        result.update(patch)
    return result


def _wire_confidence(value: Any) -> dict[str, Any]:
    candidate = value if isinstance(value, dict) else {}
    score = float(candidate.get("score", 0))
    if score <= 0:
        score = 0
        classification = "unknown"
    elif score < 0.75:
        classification = "low"
    elif score < 0.85:
        classification = "medium"
    else:
        classification = "high"
    return {
        "score": score,
        "classification": classification,
        "basis": str(candidate.get("basis") or "insufficient_evidence"),
        "calibrationId": str(candidate.get("calibrationId") or "governed-local-rules-v1"),
    }


def _wire_raw_evidence(
    value: Any,
    *,
    run: AnalysisRunRow,
    story: ImportedStoryRow,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for candidate in value[:MAX_EVIDENCE_SPANS]:
        if not isinstance(candidate, dict):
            continue
        start = candidate.get("startOffset")
        end = candidate.get("endOffset")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or end <= start
        ):
            continue
        result.append(
            _bounded_excerpt(
                run=run,
                story=story,
                start=start,
                end=end,
            )
        )
    return result


def _wire_entity_payload(
    *,
    collection: str,
    payload: dict[str, Any],
    run: AnalysisRunRow,
    story: ImportedStoryRow,
    start: int | None,
    end: int | None,
    correction: AnalysisCorrectionRow | None,
) -> dict[str, Any]:
    allowed_fields = _COLLECTION_PAYLOAD_FIELDS[collection]
    result = {
        field: payload[field]
        for field in allowed_fields
        if field in payload
        and not (
            payload[field] is None
            and field in _OPTIONAL_NON_NULL_ENTITY_FIELDS
            and not (
                collection == "relationships"
                and field == "targetCharacterId"
            )
        )
    }
    if (
        collection in _SOURCE_SPAN_COLLECTIONS
        and start is not None
        and end is not None
        and end > start
    ):
        result["sourceSpan"] = _source_span(
            run=run,
            story=story,
            start=start,
            end=end,
        )
    if collection in {"chapters", "scenes"}:
        effective_span = payload.get("_effectiveSourceSpan")
        if isinstance(effective_span, dict):
            effective_start = effective_span.get("startOffset")
            effective_end = effective_span.get("endOffset")
            if isinstance(effective_start, int) and isinstance(effective_end, int):
                result["sourceSpan"] = _source_span(
                    run=run,
                    story=story,
                    start=effective_start,
                    end=effective_end,
                )
        effective_boundary = result.get("effectiveBoundary")
        if isinstance(effective_boundary, dict):
            raw_boundary_span = effective_boundary.get("sourceSpan")
            if isinstance(raw_boundary_span, dict):
                boundary_start = raw_boundary_span.get("startOffset")
                boundary_end = raw_boundary_span.get("endOffset")
                if isinstance(boundary_start, int) and isinstance(
                    boundary_end,
                    int,
                ):
                    result["effectiveBoundary"] = {
                        key: effective_boundary[key]
                        for key in (
                            "operation",
                            "included",
                            "parentEntityId",
                            "ordinal",
                            "authority",
                            "correctionId",
                        )
                        if key in effective_boundary
                    } | {
                        "sourceSpan": _source_span(
                            run=run,
                            story=story,
                            start=boundary_start,
                            end=boundary_end,
                        )
                    }
    if (
        collection in _EXACT_TEXT_COLLECTIONS
        and start is not None
        and end is not None
        and end > start
    ):
        result["exactText"] = _exact_analysis_text(
            run=run,
            story=story,
            start=start,
            end=end,
        )

    if collection == "characters":
        aliases = []
        for alias in result.get("aliases", []):
            if not isinstance(alias, dict):
                continue
            effective_range = alias.get("effectiveRange")
            if not isinstance(effective_range, dict):
                continue
            raw_range = effective_range.get("sourceRange")
            if not isinstance(raw_range, dict):
                continue
            range_start = raw_range.get("startOffset")
            range_end = raw_range.get("endOffset")
            if not isinstance(range_start, int) or not isinstance(range_end, int):
                continue
            aliases.append(
                {
                    key: alias[key]
                    for key in (
                        "aliasId",
                        "characterId",
                        "alias",
                        "normalizedAlias",
                        "kind",
                        "ambiguous",
                        "change",
                        "previousAliasId",
                    )
                    if key in alias
                }
                | {
                    "effectiveRange": {
                        "sourceRange": _source_span(
                            run=run,
                            story=story,
                            start=range_start,
                            end=range_end,
                        ),
                        "validFromEventId": effective_range.get("validFromEventId"),
                        "validThroughEventId": effective_range.get("validThroughEventId"),
                    },
                    "confidence": _wire_confidence(alias.get("confidence")),
                    "evidence": _wire_raw_evidence(
                        alias.get("evidence"),
                        run=run,
                        story=story,
                    ),
                }
            )
        result["aliases"] = aliases
        for field, scalar_fields in (
            (
                "honorifics",
                ("honorific", "normalizedHonorific"),
            ),
            (
                "pronounEvidence",
                ("pronoun", "normalizedPronoun", "resolution"),
            ),
        ):
            result[field] = [
                {key: candidate[key] for key in scalar_fields if key in candidate}
                | {
                    "confidence": _wire_confidence(candidate.get("confidence")),
                    "evidence": _wire_raw_evidence(
                        candidate.get("evidence"),
                        run=run,
                        story=story,
                    ),
                }
                for candidate in result.get(field, [])
                if isinstance(candidate, dict)
            ]
        for field in ("firstEvidence", "lastEvidence"):
            result[field] = _wire_raw_evidence(
                result.get(field),
                run=run,
                story=story,
            )

    if collection == "dialogue-lines":
        result["candidates"] = [
            {
                key: candidate[key]
                for key in (
                    "candidateId",
                    "characterId",
                    "rank",
                    "rationale",
                )
                if key in candidate
            }
            | {
                "confidence": _wire_confidence(candidate.get("confidence")),
                "evidence": _wire_raw_evidence(
                    candidate.get("evidence"),
                    run=run,
                    story=story,
                ),
            }
            for candidate in result.get("candidates", [])
            if isinstance(candidate, dict)
        ][:8]
        attribution = result.get("effectiveAttribution")
        if not isinstance(attribution, dict):
            attribution = {}
        attribution = {
            key: attribution.get(key)
            for key in (
                "speakerCharacterId",
                "selectedCandidateId",
                "authority",
                "correctionId",
                "requiresHumanReview",
            )
            if key in attribution
        } | {"confidence": _wire_confidence(attribution.get("confidence"))}
        if correction is not None and correction.category == "dialogue_speaker":
            attribution["authority"] = "human_correction"
            attribution["correctionId"] = correction.id
        result["effectiveAttribution"] = attribution

    if collection == "locations":
        result["sceneAssignments"] = [
            {
                key: assignment[key]
                for key in ("assignmentId", "locationId", "sceneId", "role")
                if key in assignment
            }
            | {
                "confidence": _wire_confidence(assignment.get("confidence")),
                "evidence": _wire_raw_evidence(
                    assignment.get("evidence"),
                    run=run,
                    story=story,
                ),
            }
            for assignment in result.get("sceneAssignments", [])
            if isinstance(assignment, dict)
        ]

    if collection == "relationships":
        scope = result.get("scope")
        if isinstance(scope, dict):
            raw_range = scope.get("sourceRange")
            if isinstance(raw_range, dict):
                range_start = raw_range.get("startOffset")
                range_end = raw_range.get("endOffset")
                if isinstance(range_start, int) and isinstance(range_end, int):
                    result["scope"] = {
                        key: scope[key]
                        for key in ("kind", "firstSceneId", "lastSceneId")
                        if key in scope
                    } | {
                        "sourceRange": _source_span(
                            run=run,
                            story=story,
                            start=range_start,
                            end=range_end,
                        )
                    }

    if collection == "continuity-findings" and correction is not None:
        patch = parse_json(correction.patch_json, {})
        if isinstance(patch, dict) and "disposition" in patch:
            result["humanDisposition"] = {
                "disposition": patch["disposition"],
                "explanation": str(
                    patch.get("explanation")
                    or correction.reason
                    or "Human continuity disposition recorded."
                ),
                "actorId": correction.actor_id,
                "recordedAt": correction.recorded_at,
                "provenance": {
                    "origin": "human_correction",
                    "recordedAt": correction.recorded_at,
                    "inputFingerprint": run.input_fingerprint,
                    "correctionId": correction.id,
                    "deterministic": False,
                },
                "correctionId": correction.id,
            }

    return result


class StoryIntelligenceRepository:
    def __init__(self, database: Database, projects: ProjectRepository) -> None:
        self.database = database
        self.projects = projects

    @staticmethod
    def require_run(
        session: Session,
        *,
        project_id: str,
        run_id: str,
    ) -> AnalysisRunRow:
        run = session.get(AnalysisRunRow, run_id)
        if run is None or run.project_id != project_id:
            raise not_found("analysis run")
        return run

    @staticmethod
    def correction_set_fingerprint(
        session: Session,
        *,
        project_id: str,
        recorded_through: str | None = None,
    ) -> str:
        statement = select(AnalysisCorrectionRow).where(
            AnalysisCorrectionRow.project_id == project_id
        )
        if recorded_through is not None:
            statement = statement.where(AnalysisCorrectionRow.recorded_at <= recorded_through)
        rows = list(
            session.scalars(
                statement.order_by(
                    AnalysisCorrectionRow.recorded_at,
                    AnalysisCorrectionRow.id,
                )
            )
        )
        return request_fingerprint(
            [
                {
                    "category": row.category,
                    "targetKey": row.target_key,
                    "revision": row.revision,
                    "patch": parse_json(row.patch_json, {}),
                    "correctionFingerprint": row.correction_fingerprint,
                    "supersedesCorrectionId": row.supersedes_correction_id,
                    "legacyCorrectionId": row.legacy_correction_id,
                }
                for row in rows
            ]
        )

    @staticmethod
    def current_approved_input(
        session: Session,
        *,
        project_id: str,
    ) -> tuple[
        ProjectRow,
        SourceDocumentRow,
        DocumentExtractionRow,
        ImportedStoryRow,
        ImportReviewRow,
    ]:
        project = session.get(ProjectRow, project_id)
        if project is None:
            raise not_found("project")
        if project.story_id is None:
            raise ServiceError(
                409,
                "IMPORT_APPROVAL_REQUIRED",
                "Approve the current extraction before whole-book analysis.",
            )
        story = session.get(ImportedStoryRow, project.story_id)
        if story is None:
            raise ServiceError(500, "STORY_UNAVAILABLE", "The imported story is unavailable.")
        source = session.get(SourceDocumentRow, story.source_document_id)
        extraction = session.get(DocumentExtractionRow, story.extraction_id)
        review = session.scalar(
            select(ImportReviewRow)
            .where(ImportReviewRow.extraction_id == story.extraction_id)
            .order_by(ImportReviewRow.revision.desc(), ImportReviewRow.id.desc())
            .limit(1)
        )
        latest_source_id = session.scalar(
            select(SourceDocumentRow.id)
            .where(SourceDocumentRow.project_id == project_id)
            .order_by(
                SourceDocumentRow.source_revision.desc(),
                SourceDocumentRow.imported_at.desc(),
                SourceDocumentRow.id.desc(),
            )
            .limit(1)
        )
        latest_extraction_id = (
            session.scalar(
                select(DocumentExtractionRow.id)
                .where(DocumentExtractionRow.source_document_id == source.id)
                .order_by(
                    DocumentExtractionRow.revision.desc(),
                    DocumentExtractionRow.created_at.desc(),
                    DocumentExtractionRow.id.desc(),
                )
                .limit(1)
            )
            if source is not None
            else None
        )
        if (
            source is None
            or extraction is None
            or review is None
            or source.id != latest_source_id
            or extraction.id != latest_extraction_id
            or extraction.project_id != project_id
            or extraction.source_document_id != source.id
            or extraction.revision != story.extraction_revision
            or extraction.text_sha256 != story.content_fingerprint
            or extraction.status not in {"complete", "partial"}
            or review.project_id != project_id
            or review.state != "approved"
            or review.candidate_story_id != story.id
            or review.evidence_fingerprint != extraction.evidence_fingerprint
        ):
            raise ServiceError(
                409,
                "APPROVED_EXTRACTION_STALE",
                "The current extraction no longer has an exact effective approval.",
            )
        return project, source, extraction, story, review

    @staticmethod
    def _applicable_correction_set_fingerprint(
        session: Session,
        *,
        run: AnalysisRunRow,
    ) -> str:
        rows = list(
            session.scalars(
                select(AnalysisCorrectionRow)
                .where(
                    AnalysisCorrectionRow.project_id == run.project_id,
                    or_(
                        AnalysisCorrectionRow.recorded_at <= run.created_at,
                        and_(
                            AnalysisCorrectionRow.run_id.is_(None),
                            AnalysisCorrectionRow.category == "dialogue_speaker",
                            AnalysisCorrectionRow.expected_run_fingerprint == run.input_fingerprint,
                        ),
                    ),
                )
                .order_by(
                    AnalysisCorrectionRow.recorded_at,
                    AnalysisCorrectionRow.id,
                )
            )
        )
        return request_fingerprint(
            [
                {
                    "category": row.category,
                    "targetKey": row.target_key,
                    "revision": row.revision,
                    "patch": parse_json(row.patch_json, {}),
                    "correctionFingerprint": row.correction_fingerprint,
                    "supersedesCorrectionId": row.supersedes_correction_id,
                    "legacyCorrectionId": row.legacy_correction_id,
                }
                for row in rows
            ]
        )

    def _assert_run_approval_current(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        check_correction_set: bool = True,
    ) -> ImportedStoryRow:
        try:
            _project, source, extraction, story, review = self.current_approved_input(
                session,
                project_id=run.project_id,
            )
        except ServiceError as exc:
            raise ServiceError(
                409,
                "ANALYSIS_RUN_APPROVAL_STALE",
                "The approved analysis input changed before this operation.",
                retryable=False,
            ) from exc
        if (
            story.id != run.story_id
            or story.revision != run.story_revision
            or story.content_fingerprint != run.input_fingerprint
            or source.id != run.source_document_id
            or source.source_revision != run.source_revision
            or extraction.id != run.extraction_id
            or extraction.revision != run.extraction_revision
            or extraction.text_sha256 != run.extracted_text_sha256
            or review.id != run.import_review_record_id
            or review.review_id != run.review_id
            or review.revision != run.review_revision
            or review.decision_id != run.review_decision_id
            or review.evidence_fingerprint != run.approval_evidence_fingerprint
            or check_correction_set
            and self._applicable_correction_set_fingerprint(session, run=run)
            != run.correction_set_fingerprint
        ):
            raise ServiceError(
                409,
                "ANALYSIS_RUN_APPROVAL_STALE",
                "The approved analysis input changed before this operation.",
                retryable=False,
            )
        return story

    @staticmethod
    def _valid_analysis_confidence(value: Any) -> bool:
        if not isinstance(value, dict) or set(value) != {
            "score",
            "classification",
            "basis",
            "calibrationId",
        }:
            return False
        score = value.get("score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 1
            or not _valid_string(value.get("basis"), maximum=160)
            or value.get("calibrationId") != "governed-local-rules-v1"
        ):
            return False
        expected_class = (
            "unknown"
            if float(score) <= 0
            else "low"
            if float(score) < 0.75
            else "medium"
            if float(score) < 0.85
            else "high"
        )
        return value.get("classification") == expected_class

    def _validate_analysis_result(
        self,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        result: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        expected_root_keys = {
            "analysisContractVersion",
            "profile",
            "profileFingerprint",
            "agentRegistryFingerprint",
            "inputFingerprint",
            "correctionSetFingerprint",
            "collections",
            "collectionFingerprints",
            "gateFingerprints",
            "summary",
            "outputFingerprint",
        }
        if (
            set(result) != expected_root_keys
            or result.get("analysisContractVersion") != ANALYSIS_CONTRACT_VERSION
            or result.get("profile") != parse_json(run.profile_json, {})
            or result.get("profileFingerprint") != run.profile_fingerprint
            or result.get("agentRegistryFingerprint") != AGENT_REGISTRY_FINGERPRINT
            or result.get("inputFingerprint") != run.input_fingerprint
            or result.get("correctionSetFingerprint") != run.correction_set_fingerprint
        ):
            raise _analysis_output_invalid()
        raw_collections = result.get("collections")
        if (
            not isinstance(raw_collections, dict)
            or set(raw_collections) != set(ENTITY_COLLECTIONS)
            or any(
                not isinstance(raw_collections[collection], list)
                for collection in ENTITY_COLLECTIONS
            )
        ):
            raise _analysis_output_invalid()
        collections: dict[str, list[dict[str, Any]]] = {}
        total_entities = 0
        ids_by_collection: dict[str, set[str]] = {}
        all_entity_ids: set[str] = set()
        common_keys = {
            "entityId",
            "collection",
            "ordinal",
            "identityKey",
            "parentEntityId",
            "startOffset",
            "endOffset",
            "payload",
            "confidence",
            "warnings",
            "evidence",
            "fingerprint",
        }
        text_length = len(story.exact_text)

        def nonnegative_integer(value: Any) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and value >= 0

        def bounded_number(
            value: Any,
            minimum: float,
            maximum: float,
        ) -> bool:
            return (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and minimum <= float(value) <= maximum
            )

        def valid_dialogue_payload(payload: dict[str, Any]) -> bool:
            candidates = payload.get("candidates")
            if not isinstance(candidates, list) or len(candidates) > 8:
                return False
            candidate_ids: set[str] = set()
            candidate_characters: dict[str, str | None] = {}
            for expected_rank, candidate in enumerate(candidates, start=1):
                if not isinstance(candidate, dict):
                    return False
                required = {
                    "candidateId",
                    "characterId",
                    "rank",
                    "confidence",
                    "evidence",
                    "rationale",
                }
                optional = {"score", "basis"}
                if not required.issubset(candidate) or not set(candidate).issubset(
                    required | optional
                ):
                    return False
                candidate_id = candidate.get("candidateId")
                character_id = candidate.get("characterId")
                evidence_values = candidate.get("evidence")
                if (
                    not _valid_id(candidate_id)
                    or candidate_id in candidate_ids
                    or not _valid_id(character_id, nullable=True)
                    or isinstance(character_id, str)
                    and candidate_id
                    != stable_id(
                        run.input_fingerprint,
                        "dialogue-candidate",
                        payload.get("quoteStartOffset"),
                        character_id,
                    )
                    or candidate.get("rank") != expected_rank
                    or isinstance(candidate.get("rank"), bool)
                    or not self._valid_analysis_confidence(candidate.get("confidence"))
                    or not isinstance(evidence_values, list)
                    or len(evidence_values) > MAX_EVIDENCE_SPANS
                    or not _valid_string(candidate.get("rationale"), maximum=1000)
                ):
                    return False
                confidence_value = candidate["confidence"]
                if (
                    "score" in candidate
                    and (
                        not bounded_number(candidate["score"], 0, 1)
                        or float(candidate["score"])
                        != float(confidence_value["score"])
                    )
                    or "basis" in candidate
                    and (
                        not _valid_string(candidate["basis"], maximum=160)
                        or candidate["basis"] != confidence_value["basis"]
                    )
                ):
                    return False
                for evidence in evidence_values:
                    if not isinstance(evidence, dict) or set(evidence) != {
                        "startOffset",
                        "endOffset",
                    }:
                        return False
                    evidence_start = evidence.get("startOffset")
                    evidence_end = evidence.get("endOffset")
                    if (
                        not isinstance(evidence_start, int)
                        or isinstance(evidence_start, bool)
                        or not isinstance(evidence_end, int)
                        or isinstance(evidence_end, bool)
                        or evidence_start < 0
                        or evidence_end <= evidence_start
                        or evidence_end > text_length
                    ):
                        return False
                candidate_ids.add(str(candidate_id))
                candidate_characters[str(candidate_id)] = (
                    str(character_id) if isinstance(character_id, str) else None
                )

            attribution = payload.get("effectiveAttribution")
            if not isinstance(attribution, dict) or set(attribution) != {
                "speakerCharacterId",
                "selectedCandidateId",
                "authority",
                "confidence",
                "requiresHumanReview",
            }:
                return False
            speaker_id = attribution.get("speakerCharacterId")
            selected_id = attribution.get("selectedCandidateId")
            authority = attribution.get("authority")
            requires_review = attribution.get("requiresHumanReview")
            if (
                not _valid_id(speaker_id, nullable=True)
                or not _valid_id(selected_id, nullable=True)
                or authority not in {"runtime_agent", "unresolved"}
                or not self._valid_analysis_confidence(attribution.get("confidence"))
                or not isinstance(requires_review, bool)
                or requires_review is not payload.get("requiresHumanReview")
                or attribution.get("confidence") != payload.get("_entityConfidence")
            ):
                return False
            if speaker_id is None:
                return (
                    selected_id is None
                    and authority == "unresolved"
                    and requires_review is True
                    and payload.get("proposedSpeakerId") is None
                    and payload.get("effectiveSpeakerId") is None
                    and payload.get("effectiveAuthority") == "unresolved"
                    and payload.get("speakerState") in {"unknown", "ambiguous"}
                )
            return (
                isinstance(selected_id, str)
                and selected_id in candidate_ids
                and candidate_characters[selected_id] == speaker_id
                and authority == "runtime_agent"
                and payload.get("proposedSpeakerId") == speaker_id
                and payload.get("effectiveSpeakerId") == speaker_id
                and payload.get("effectiveAuthority") == "runtime_agent"
                and payload.get("speakerState") == "proposed"
            )

        for collection in ENTITY_COLLECTIONS:
            raw_values = raw_collections[collection]
            values: list[dict[str, Any]] = []
            ids: set[str] = set()
            for expected_ordinal, raw_value in enumerate(raw_values):
                if not isinstance(raw_value, dict) or set(raw_value) != common_keys:
                    raise _analysis_output_invalid()
                value = raw_value
                entity_id = value.get("entityId")
                identity_key = value.get("identityKey")
                ordinal = value.get("ordinal")
                start = value.get("startOffset")
                end = value.get("endOffset")
                parent_id = value.get("parentEntityId")
                payload = value.get("payload")
                warnings = value.get("warnings")
                evidence_values = value.get("evidence")
                if (
                    value.get("collection") != collection
                    or not isinstance(entity_id, str)
                    or not isinstance(identity_key, str)
                    or not 1 <= len(identity_key) <= 160
                    or ordinal != expected_ordinal
                    or isinstance(ordinal, bool)
                    or entity_id in ids
                    or entity_id in all_entity_ids
                    or parent_id is not None
                    and not isinstance(parent_id, str)
                    or not isinstance(payload, dict)
                    or not _RAW_ANALYSIS_REQUIRED_FIELDS[collection].issubset(payload)
                    or not set(payload).issubset(
                        _RAW_ANALYSIS_REQUIRED_FIELDS[collection]
                        | _RAW_ANALYSIS_OPTIONAL_FIELDS[collection]
                    )
                    or payload.get(_PRIMARY_PAYLOAD_ID[collection]) != entity_id
                    or not self._valid_analysis_confidence(value.get("confidence"))
                    or not isinstance(warnings, list)
                    or len(warnings) > MAX_WARNINGS_PER_ENTITY
                    or not isinstance(evidence_values, list)
                    or len(evidence_values) > MAX_EVIDENCE_SPANS
                ):
                    raise _analysis_output_invalid()
                for count_field in (
                    "sceneCount",
                    "beatCount",
                    "mentionCount",
                    "narrativeOrdinal",
                    "chronologicalOrdinal",
                    "storyOrdinal",
                    "count",
                ):
                    if (
                        count_field in payload
                        and not (
                            count_field in {"chronologicalOrdinal", "storyOrdinal"}
                            and payload[count_field] is None
                        )
                        and not nonnegative_integer(payload[count_field])
                    ):
                        raise _analysis_output_invalid()
                for list_field in (
                    "aliases",
                    "honorifics",
                    "pronounEvidence",
                    "firstEvidence",
                    "lastEvidence",
                    "namedMentionIds",
                    "ambiguousMentionIds",
                    "candidateCharacterIds",
                    "candidates",
                    "sceneIds",
                    "sceneAssignments",
                    "participantCharacterIds",
                    "sourceCandidateCharacterIds",
                    "targetCandidateCharacterIds",
                    "relatedEntityIds",
                ):
                    if list_field in payload and not isinstance(
                        payload[list_field],
                        list,
                    ):
                        raise _analysis_output_invalid()
                if (
                    collection == "chapters"
                    and not _valid_string(
                        payload.get("title"),
                        maximum=500,
                        nullable=True,
                    )
                    or collection == "scenes"
                    and not _valid_string(
                        payload.get("heading"),
                        maximum=500,
                        nullable=True,
                    )
                    or collection == "beats"
                    and (
                        not _valid_string(payload.get("kind"), maximum=80)
                        or not _valid_string(
                            payload.get("summary"),
                            maximum=1000,
                            nullable=True,
                        )
                    )
                    or collection == "characters"
                    and (
                        not _valid_string(
                            payload.get("canonicalName"),
                            maximum=240,
                        )
                        or payload.get("normalizedCanonicalName")
                        != " ".join(str(payload.get("canonicalName", "")).split()).casefold()
                        or payload.get("registryScope") != "project_story"
                        or payload.get("stableAcrossCompatibleRuns") is not True
                        or payload.get("kind") != "person"
                        or payload.get("identityStatus") not in {"resolved", "ambiguous"}
                    )
                    or collection == "mentions"
                    and payload.get("mentionKind")
                    not in {"proper_name", "honorific", "alias", "pronoun"}
                    or collection == "dialogue-lines"
                    and (
                        payload.get("distinction") != "spoken_dialogue"
                        or not isinstance(
                            payload.get("requiresHumanReview"),
                            bool,
                        )
                        or not nonnegative_integer(payload.get("quoteStartOffset"))
                        or not nonnegative_integer(payload.get("quoteEndOffset"))
                        or int(payload["quoteEndOffset"]) <= int(payload["quoteStartOffset"])
                        or int(payload["quoteEndOffset"]) > text_length
                        or not valid_dialogue_payload(
                            {
                                **payload,
                                "_entityConfidence": value.get("confidence"),
                            }
                        )
                    )
                    or collection == "narration-spans"
                    and (
                        payload.get("classification")
                        not in {
                            "direct_narration",
                            "epigraph_or_document",
                            "quoted_material",
                            "internal_thought",
                        }
                        or payload.get("subtype") != payload.get("classification")
                    )
                    or collection == "pov-segments"
                    and payload.get("povType") != payload.get("mode")
                    or collection == "locations"
                    and (
                        not _valid_string(
                            payload.get("canonicalName"),
                            maximum=500,
                        )
                        or payload.get("displayName") != payload.get("canonicalName")
                        or payload.get("normalizedName") != payload.get("normalizedCanonicalName")
                    )
                    or collection == "timeline-events"
                    and (
                        not _valid_string(payload.get("kind"), maximum=80)
                        or not _valid_string(payload.get("label"), maximum=500)
                        or payload.get("storyOrdinal") != payload.get("chronologicalOrdinal")
                    )
                    or collection == "temporal-constraints"
                    and not isinstance(payload.get("approximate"), bool)
                    or collection == "relationships"
                    and (
                        payload.get("resolution") not in {"resolved", "ambiguous", "unresolved"}
                        or not _valid_string(payload.get("state"), maximum=500)
                        or not isinstance(payload.get("scope"), dict)
                    )
                    or collection == "emotional-states"
                    and (
                        payload.get("subjectType") not in {"character", "scene"}
                        or not _valid_string(payload.get("note"), maximum=1000)
                        or not bounded_number(payload.get("valence"), -1, 1)
                        or not bounded_number(payload.get("arousal"), 0, 1)
                        or not bounded_number(payload.get("intensity"), 0, 1)
                    )
                    or collection == "dramatic-intents"
                    and (
                        payload.get("subjectType") != "dialogue"
                        or not _valid_string(payload.get("note"), maximum=1000)
                    )
                    or collection == "continuity-findings"
                    and (
                        payload.get("severity") not in {"info", "warning", "error"}
                        or payload.get("machineStatus") != "open"
                        or payload.get("status") != "open"
                        or payload.get("disposition") != "unresolved"
                        or not _valid_string(
                            payload.get("explanation"),
                            maximum=4000,
                        )
                        or not _valid_string(
                            payload.get("suggestedReviewAction"),
                            maximum=2000,
                        )
                        or payload.get("suggestedAction") != payload.get("suggestedReviewAction")
                        or payload.get("requiresHumanReview") is not True
                        or payload.get("effectiveAuthority") != "runtime_agent"
                    )
                ):
                    raise _analysis_output_invalid()
                if (start is None) != (end is None):
                    raise _analysis_output_invalid()
                if start is not None and (
                    not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 0
                    or end <= start
                    or end > text_length
                ):
                    raise _analysis_output_invalid()
                for warning_value in warnings:
                    if (
                        not isinstance(warning_value, dict)
                        or set(warning_value)
                        != {
                            "code",
                            "severity",
                            "message",
                            "requiresHumanReview",
                        }
                        or not _valid_string(
                            warning_value.get("code"),
                            maximum=120,
                        )
                        or warning_value.get("severity") not in {"info", "warning", "error"}
                        or not _valid_string(
                            warning_value.get("message"),
                            maximum=2000,
                        )
                        or not isinstance(
                            warning_value.get("requiresHumanReview"),
                            bool,
                        )
                    ):
                        raise _analysis_output_invalid()
                for evidence in evidence_values:
                    if not isinstance(evidence, dict) or set(evidence) != {
                        "startOffset",
                        "endOffset",
                        "basis",
                        "confidence",
                    }:
                        raise _analysis_output_invalid()
                    evidence_start = evidence.get("startOffset")
                    evidence_end = evidence.get("endOffset")
                    if (
                        not isinstance(evidence_start, int)
                        or isinstance(evidence_start, bool)
                        or not isinstance(evidence_end, int)
                        or isinstance(evidence_end, bool)
                        or evidence_start < 0
                        or evidence_end <= evidence_start
                        or evidence_end > text_length
                        or not _valid_string(
                            evidence.get("basis"),
                            maximum=160,
                        )
                        or not self._valid_analysis_confidence(evidence.get("confidence"))
                    ):
                        raise _analysis_output_invalid()
                expected_id = stable_id(
                    run.input_fingerprint,
                    collection,
                    identity_key,
                )
                if collection == "characters":
                    if not evidence_values:
                        raise _analysis_output_invalid()
                    anchor = min(int(evidence["startOffset"]) for evidence in evidence_values)
                    base_identity, separator, duplicate = identity_key.rpartition("#")
                    if separator:
                        if not duplicate.isdigit() or int(duplicate) < 2 or not base_identity:
                            raise _analysis_output_invalid()
                        duplicate_index = int(duplicate) - 1
                    else:
                        duplicate_index = 0
                    expected_id = stable_id(
                        "whole-book-character-registry-v2",
                        run.project_id,
                        run.story_id,
                        anchor,
                        duplicate_index,
                    )
                    if (
                        payload.get("registryCharacterId")
                        != stable_id(
                            "whole-book-character-registry-identity-v2",
                            run.project_id,
                            run.story_id,
                            anchor,
                            duplicate_index,
                        )
                        or payload.get("projectId") != run.project_id
                        or payload.get("storyId") != run.story_id
                    ):
                        raise _analysis_output_invalid()
                if entity_id != expected_id or value.get("fingerprint") != request_fingerprint(
                    {key: item for key, item in value.items() if key != "fingerprint"}
                ):
                    raise _analysis_output_invalid()
                ids.add(entity_id)
                all_entity_ids.add(entity_id)
                values.append(value)
            collections[collection] = values
            ids_by_collection[collection] = ids
            total_entities += len(values)
        if total_entities > MAX_ANALYSIS_ENTITIES:
            raise ServiceError(
                422,
                "ANALYSIS_ENTITY_LIMIT_EXCEEDED",
                "The whole-book analysis produced too many claims.",
                retryable=False,
            )

        def require_reference(
            payload: dict[str, Any],
            field: str,
            target_collection: str,
            *,
            many: bool = False,
        ) -> None:
            if field not in payload or payload[field] is None:
                return
            raw_reference = payload[field]
            references = raw_reference if many else [raw_reference]
            if not isinstance(references, list) or any(
                not isinstance(reference, str)
                or reference not in ids_by_collection[target_collection]
                for reference in references
            ):
                raise _analysis_output_invalid()

        scalar_references = {
            "chapterId": "chapters",
            "firstSceneId": "scenes",
            "lastSceneId": "scenes",
            "sceneId": "scenes",
            "firstBeatId": "beats",
            "lastBeatId": "beats",
            "beatId": "beats",
            "dialogueLineId": "dialogue-lines",
            "characterId": "characters",
            "effectiveCharacterId": "characters",
            "sourceCharacterId": "characters",
            "targetCharacterId": "characters",
            "viewpointCharacterId": "characters",
            "focalCharacterId": "characters",
            "narratorCharacterId": "characters",
            "locationId": "locations",
            "parentLocationId": "locations",
            "sourceEventId": "timeline-events",
            "targetEventId": "timeline-events",
            "fromEventId": "timeline-events",
            "toEventId": "timeline-events",
            "validFromEventId": "timeline-events",
            "validThroughEventId": "timeline-events",
            "previousRelationshipId": "relationships",
            "previousEmotionalStateId": "emotional-states",
            "shiftFromPovSegmentId": "pov-segments",
            "firstMentionId": "mentions",
            "lastMentionId": "mentions",
        }
        list_references = {
            "sceneIds": "scenes",
            "participantCharacterIds": "characters",
            "candidateCharacterIds": "characters",
            "sourceCandidateCharacterIds": "characters",
            "targetCandidateCharacterIds": "characters",
            "namedMentionIds": "mentions",
            "ambiguousMentionIds": "mentions",
        }
        for collection, values in collections.items():
            expected_parent_collection = _PARENT_COLLECTION.get(collection)
            for value in values:
                parent_id = value["parentEntityId"]
                if collection == "narration-spans":
                    expected_parent_collection = (
                        "beats" if value["payload"].get("beatId") is not None else "scenes"
                    )
                if (
                    expected_parent_collection is None
                    and parent_id is not None
                    or expected_parent_collection is not None
                    and (
                        not isinstance(parent_id, str)
                        or parent_id not in ids_by_collection[expected_parent_collection]
                    )
                ):
                    raise _analysis_output_invalid()
                payload = value["payload"]
                for field, target_collection in scalar_references.items():
                    require_reference(payload, field, target_collection)
                for field, target_collection in list_references.items():
                    require_reference(
                        payload,
                        field,
                        target_collection,
                        many=True,
                    )
                if "relatedEntityIds" in payload:
                    related = payload["relatedEntityIds"]
                    if not isinstance(related, list) or any(
                        not isinstance(reference, str) or reference not in all_entity_ids
                        for reference in related
                    ):
                        raise _analysis_output_invalid()
                for candidate in payload.get("candidates", []):
                    if not isinstance(candidate, dict):
                        raise _analysis_output_invalid()
                    require_reference(candidate, "characterId", "characters")
                attribution = payload.get("effectiveAttribution")
                if isinstance(attribution, dict):
                    require_reference(
                        attribution,
                        "speakerCharacterId",
                        "characters",
                    )
                for alias in payload.get("aliases", []):
                    if isinstance(alias, dict):
                        require_reference(alias, "characterId", "characters")
                        effective_range = alias.get("effectiveRange")
                        if isinstance(effective_range, dict):
                            require_reference(
                                effective_range,
                                "validFromEventId",
                                "timeline-events",
                            )
                            require_reference(
                                effective_range,
                                "validThroughEventId",
                                "timeline-events",
                            )
                for assignment in payload.get("sceneAssignments", []):
                    if not isinstance(assignment, dict):
                        raise _analysis_output_invalid()
                    require_reference(assignment, "locationId", "locations")
                    require_reference(assignment, "sceneId", "scenes")
                scope = payload.get("scope")
                if isinstance(scope, dict):
                    require_reference(scope, "firstSceneId", "scenes")
                    require_reference(scope, "lastSceneId", "scenes")

                if (
                    collection == "scenes"
                    and payload.get("boundaryKind")
                    not in {
                        "chapter_start",
                        "explicit_scene_break",
                        "heading",
                        "inferred",
                    }
                    or collection == "mentions"
                    and payload.get("resolution") not in {"resolved", "ambiguous", "unresolved"}
                    or collection == "dialogue-lines"
                    and payload.get("speakerState") not in {"unknown", "ambiguous", "proposed"}
                    or collection == "pov-segments"
                    and payload.get("mode") not in _POV_MODES
                    or collection == "locations"
                    and payload.get("kind") not in _LOCATION_KINDS
                    or collection == "temporal-constraints"
                    and (
                        payload.get("relation") not in _TEMPORAL_RELATIONS
                        or payload.get("status") not in _TEMPORAL_STATUSES
                    )
                    or collection == "relationships"
                    and (
                        payload.get("kind") not in _RELATIONSHIP_KINDS
                        or payload.get("change") not in _RELATIONSHIP_CHANGES
                    )
                    or collection == "emotional-states"
                    and (
                        payload.get("emotion") not in _EMOTIONS
                        or payload.get("progression") not in _EMOTION_PROGRESSIONS
                    )
                    or collection == "dramatic-intents"
                    and (
                        payload.get("intent") not in _DRAMATIC_INTENTS
                        or payload.get("dramaticFunction") not in _DRAMATIC_FUNCTIONS
                        or payload.get("status") not in _INTENT_STATUSES
                    )
                ):
                    raise _analysis_output_invalid()

        entities_by_id = {
            str(value["entityId"]): value for values in collections.values() for value in values
        }
        scenes_by_chapter: dict[str, list[dict[str, Any]]] = {
            chapter_id: [] for chapter_id in ids_by_collection["chapters"]
        }
        for scene in collections["scenes"]:
            chapter_id = str(scene["payload"]["chapterId"])
            if scene["parentEntityId"] != chapter_id:
                raise _analysis_output_invalid()
            scenes_by_chapter[chapter_id].append(scene)
        for chapter in collections["chapters"]:
            child_scenes = sorted(
                scenes_by_chapter[str(chapter["entityId"])],
                key=lambda value: (int(value["ordinal"]), str(value["entityId"])),
            )
            payload = chapter["payload"]
            if (
                payload["sceneCount"] != len(child_scenes)
                or payload["firstSceneId"]
                != (child_scenes[0]["entityId"] if child_scenes else None)
                or payload["lastSceneId"]
                != (child_scenes[-1]["entityId"] if child_scenes else None)
            ):
                raise _analysis_output_invalid()

        beats_by_scene: dict[str, list[dict[str, Any]]] = {
            scene_id: [] for scene_id in ids_by_collection["scenes"]
        }
        for beat in collections["beats"]:
            scene_id = str(beat["payload"]["sceneId"])
            scene = entities_by_id[scene_id]
            if (
                beat["parentEntityId"] != scene_id
                or beat["payload"]["chapterId"] != scene["payload"]["chapterId"]
            ):
                raise _analysis_output_invalid()
            beats_by_scene[scene_id].append(beat)
        for scene in collections["scenes"]:
            child_beats = sorted(
                beats_by_scene[str(scene["entityId"])],
                key=lambda value: (int(value["ordinal"]), str(value["entityId"])),
            )
            payload = scene["payload"]
            if (
                payload["beatCount"] != len(child_beats)
                or payload["firstBeatId"] != (child_beats[0]["entityId"] if child_beats else None)
                or payload["lastBeatId"] != (child_beats[-1]["entityId"] if child_beats else None)
            ):
                raise _analysis_output_invalid()

        for collection, values in collections.items():
            for value in values:
                payload = value["payload"]
                scene_id = payload.get("sceneId")
                if isinstance(scene_id, str):
                    scene = entities_by_id[scene_id]
                    if (
                        "chapterId" in payload
                        and payload["chapterId"] != scene["payload"]["chapterId"]
                    ):
                        raise _analysis_output_invalid()
                beat_id = payload.get("beatId")
                if isinstance(beat_id, str):
                    beat = entities_by_id[beat_id]
                    if isinstance(scene_id, str) and beat["payload"]["sceneId"] != scene_id:
                        raise _analysis_output_invalid()
                    if collection == "narration-spans" and value["parentEntityId"] != beat_id:
                        raise _analysis_output_invalid()
                elif collection == "narration-spans" and value["parentEntityId"] != scene_id:
                    raise _analysis_output_invalid()
                dialogue_line_id = payload.get("dialogueLineId")
                if isinstance(dialogue_line_id, str):
                    dialogue_line = entities_by_id[dialogue_line_id]
                    if (
                        isinstance(scene_id, str)
                        and dialogue_line["payload"]["sceneId"] != scene_id
                    ):
                        raise _analysis_output_invalid()
                    if (
                        collection == "dramatic-intents"
                        and value["parentEntityId"] != dialogue_line_id
                    ):
                        raise _analysis_output_invalid()

        for dialogue in collections["dialogue-lines"]:
            beat = entities_by_id[str(dialogue["payload"]["beatId"])]
            if (
                beat["payload"]["dialogueLineId"] != dialogue["entityId"]
                or dialogue["parentEntityId"] != dialogue["payload"]["sceneId"]
            ):
                raise _analysis_output_invalid()

        for location in collections["locations"]:
            payload = location["payload"]
            scene_ids = payload["sceneIds"]
            assignments = payload["sceneAssignments"]
            if (
                payload["sceneCount"] != len(scene_ids)
                or len(scene_ids) != len(set(scene_ids))
                or payload["firstSceneId"] != (scene_ids[0] if scene_ids else None)
                or any(
                    not isinstance(assignment, dict)
                    or assignment.get("locationId") != location["entityId"]
                    or assignment.get("sceneId") not in scene_ids
                    for assignment in assignments
                )
                or {
                    assignment["sceneId"]
                    for assignment in assignments
                    if isinstance(assignment, dict)
                }
                != set(scene_ids)
            ):
                raise _analysis_output_invalid()

        collection_fingerprints = {
            collection: request_fingerprint(collections[collection])
            for collection in ENTITY_COLLECTIONS
        }
        expected_gate_fingerprints = {
            "story_structure_review": request_fingerprint(
                {
                    name: collection_fingerprints[name]
                    for name in (
                        "chapters",
                        "scenes",
                        "beats",
                        "pov-segments",
                    )
                }
            ),
            "character_registry_review": request_fingerprint(
                {
                    name: collection_fingerprints[name]
                    for name in ("characters", "mentions", "relationships")
                }
            ),
            "dialogue_attribution_review": collection_fingerprints["dialogue-lines"],
            "whole_book_analysis_review": request_fingerprint(collection_fingerprints),
        }
        expected_summary = {
            "collectionCounts": {
                collection: len(collections[collection]) for collection in ENTITY_COLLECTIONS
            },
            "warningCount": sum(
                len(value["warnings"]) for values in collections.values() for value in values
            ),
            "requiresHumanReview": any(
                bool(value["payload"].get("requiresHumanReview"))
                for value in collections["dialogue-lines"]
            ),
        }
        if (
            result.get("collectionFingerprints") != collection_fingerprints
            or result.get("gateFingerprints") != expected_gate_fingerprints
            or result.get("summary") != expected_summary
            or result.get("outputFingerprint")
            != request_fingerprint(
                {key: value for key, value in result.items() if key != "outputFingerprint"}
            )
        ):
            raise _analysis_output_invalid()
        return collections

    def validate_run_preconditions(
        self,
        session: Session,
        *,
        project_id: str,
        expected_extraction_id: str,
        expected_extraction_revision: int,
        expected_review_id: str,
        expected_review_revision: int,
        expected_evidence_fingerprint: str,
    ) -> tuple[
        ProjectRow,
        SourceDocumentRow,
        DocumentExtractionRow,
        ImportedStoryRow,
        ImportReviewRow,
    ]:
        values = self.current_approved_input(session, project_id=project_id)
        _project, _source, extraction, _story, review = values
        if (
            extraction.id != expected_extraction_id
            or extraction.revision != expected_extraction_revision
            or review.review_id != expected_review_id
            or review.revision != expected_review_revision
            or review.evidence_fingerprint != expected_evidence_fingerprint
            or extraction.evidence_fingerprint != expected_evidence_fingerprint
        ):
            raise ServiceError(
                409,
                "ANALYSIS_PRECONDITION_CONFLICT",
                "The approved extraction or review changed; refresh before analysis.",
                details={
                    "currentExtractionId": extraction.id,
                    "currentExtractionRevision": extraction.revision,
                    "currentReviewId": review.review_id,
                    "currentReviewRevision": review.revision,
                    "currentEvidenceFingerprint": review.evidence_fingerprint,
                },
            )
        return values

    @staticmethod
    def _latest_execution(
        session: Session,
        run_id: str,
    ) -> AnalysisExecutionRow | None:
        return session.scalar(
            select(AnalysisExecutionRow)
            .where(AnalysisExecutionRow.run_id == run_id)
            .order_by(AnalysisExecutionRow.attempt.desc(), AnalysisExecutionRow.id.desc())
            .limit(1)
        )

    @staticmethod
    def _latest_snapshot(
        session: Session,
        run_id: str,
    ) -> AnalysisSnapshotRow | None:
        return session.scalar(
            select(AnalysisSnapshotRow)
            .where(AnalysisSnapshotRow.run_id == run_id)
            .order_by(
                AnalysisSnapshotRow.created_at.desc(),
                AnalysisSnapshotRow.ordinal.desc(),
                AnalysisSnapshotRow.id.desc(),
            )
            .limit(1)
        )

    def run_dict(self, session: Session, run: AnalysisRunRow) -> dict[str, Any]:
        job = session.get(JobRow, run.job_id)
        snapshot = self._latest_snapshot(session, run.id)
        profile = parse_json(run.profile_json, {})
        source = session.get(SourceDocumentRow, run.source_document_id)
        snapshot_count = int(
            session.scalar(
                select(func.count())
                .select_from(AnalysisSnapshotRow)
                .where(AnalysisSnapshotRow.run_id == run.id)
            )
            or 0
        )
        latest_checkpoint = session.scalar(
            select(AnalysisStageCheckpointRow)
            .where(AnalysisStageCheckpointRow.run_id == run.id)
            .order_by(
                AnalysisStageCheckpointRow.attempt.desc(),
                AnalysisStageCheckpointRow.ordinal.desc(),
                AnalysisStageCheckpointRow.id.desc(),
            )
            .limit(1)
        )
        status = job.state if job is not None else "interrupted"
        if status == "cancel_requested":
            status = "running"
        if status not in {
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
        }:
            status = "interrupted"
        current_stage = (
            "complete"
            if status == "succeeded"
            else job.stage
            if job is not None
            and job.stage
            in {
                "queued",
                "validate_approved_input",
                "initialize_run",
                "analyze_structure",
                "analyze_beats",
                "analyze_character_identity",
                "analyze_dialogue_attribution",
                "analyze_point_of_view",
                "analyze_locations",
                "analyze_timeline",
                "analyze_relationships",
                "analyze_emotion_intent",
                "analyze_continuity",
                "synthesize_analysis",
                "publish_analysis",
            }
            else latest_checkpoint.stage
            if latest_checkpoint is not None
            else "queued"
        )
        current_snapshot = (
            self._snapshot_dict(session, run=run, snapshot=snapshot)
            if snapshot is not None
            else None
        )
        summary = (
            current_snapshot["counts"]
            if current_snapshot is not None
            else self._empty_count_summary()
        )
        gate_states = [
            latest.state
            for gate_id in REVIEW_GATES
            if (
                latest := self._latest_gate(
                    session,
                    run_id=run.id,
                    gate_id=gate_id,
                )
            )
            is not None
        ]
        review_eligibility = (
            "not_ready"
            if current_snapshot is None
            else "invalidated"
            if "invalidated" in gate_states
            else "blocked_by_warnings"
            if bool(
                session.scalar(
                    select(func.count())
                    .select_from(AnalysisEntityRow)
                    .where(
                        AnalysisEntityRow.run_id == run.id,
                        AnalysisEntityRow.warnings_json != "[]",
                    )
                )
            )
            else "ready"
        )
        result: dict[str, Any] = {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "runId": run.id,
            "projectId": run.project_id,
            "storyId": run.story_id,
            "storyRevision": run.story_revision,
            "storyFingerprint": run.input_fingerprint,
            "sourceDocumentId": run.source_document_id,
            "sourceRevision": run.source_revision,
            "sourceSha256": (
                source.content_sha256 if source is not None else run.input_fingerprint
            ),
            "extractionId": run.extraction_id,
            "extractionRevision": run.extraction_revision,
            "extractedTextSha256": run.extracted_text_sha256,
            "importReviewId": run.review_id,
            "importReviewRevision": run.review_revision,
            "importReviewDecisionId": run.review_decision_id,
            "approvedEvidenceFingerprint": run.approval_evidence_fingerprint,
            "inputFingerprint": run.input_fingerprint,
            "runFingerprint": run.run_fingerprint,
            "producer": {
                "producerId": run.producer_id,
                "producerVersion": run.producer_version,
            },
            "profile": {
                "profileId": profile.get("profileId", "whole-book-intelligence-v1"),
                "semanticVersion": profile.get("semanticVersion", "1.0.0"),
                "fingerprint": run.profile_fingerprint,
            },
            "agentVersions": profile.get("agentVersions", []),
            "jobId": run.job_id,
            "status": status,
            "currentStage": current_stage,
            "progress": (job.progress / 1_000_000) if job is not None else 0,
            "createdAt": run.created_at,
            "updatedAt": job.updated_at if job is not None else run.created_at,
            "warnings": [],
            "snapshotCount": snapshot_count,
            "currentSnapshot": current_snapshot,
            "reviewEligibility": review_eligibility,
        }
        latest_execution = self._latest_execution(session, run.id)
        if latest_execution is not None:
            agent_rows = list(
                session.scalars(
                    select(AnalysisAgentExecutionRow)
                    .where(AnalysisAgentExecutionRow.execution_id == latest_execution.id)
                    .order_by(
                        AnalysisAgentExecutionRow.ordinal,
                        AnalysisAgentExecutionRow.id,
                    )
                )
            )
            latest_agent = None
            for candidate in agent_rows:
                envelope = parse_json(candidate.envelope_json, {})
                if isinstance(envelope, dict) and envelope.get("status") in {
                    "running",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    latest_agent = candidate
                    break
                if candidate.outcome == "succeeded":
                    latest_agent = candidate
            if latest_agent is None and agent_rows:
                latest_agent = agent_rows[0]
            if latest_agent is not None:
                result["latestExecution"] = self._agent_execution_dict(
                    session,
                    run=run,
                    row=latest_agent,
                    snapshot=snapshot,
                )
        if status == "succeeded":
            result["summary"] = summary
            result["completedAt"] = (
                job.terminal_at
                if job is not None and job.terminal_at is not None
                else result["updatedAt"]
            )
        return result

    @staticmethod
    def _empty_count_summary() -> dict[str, int]:
        return {
            "agentExecutions": 0,
            **{count_key: 0 for count_key in _COUNT_KEY_BY_COLLECTION.values()},
            "corrections": 0,
        }

    def _snapshot_dict(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        snapshot: AnalysisSnapshotRow,
    ) -> dict[str, Any]:
        manifest = parse_json(snapshot.manifest_json, {})
        raw_collections = manifest.get("collections", {})
        if not isinstance(raw_collections, dict):
            raw_collections = {}
        agent_rows = list(
            session.scalars(
                select(AnalysisAgentExecutionRow)
                .where(
                    AnalysisAgentExecutionRow.run_id == run.id,
                    AnalysisAgentExecutionRow.execution_id == snapshot.execution_id,
                )
                .order_by(
                    AnalysisAgentExecutionRow.ordinal,
                    AnalysisAgentExecutionRow.id,
                )
            )
        )
        collections = [
            {
                "collection": "agent-executions",
                "itemCount": len(agent_rows),
                "fingerprint": request_fingerprint([row.output_fingerprint for row in agent_rows]),
            }
        ]
        counts = self._empty_count_summary()
        for collection in ENTITY_COLLECTIONS:
            raw_summary = raw_collections.get(collection, {})
            if not isinstance(raw_summary, dict):
                raw_summary = {}
            item_count = int(raw_summary.get("count", 0))
            fingerprint = raw_summary.get("fingerprint")
            if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                fingerprint = request_fingerprint([])
            collections.append(
                {
                    "collection": collection,
                    "itemCount": item_count,
                    "fingerprint": fingerprint,
                }
            )
            counts[_COUNT_KEY_BY_COLLECTION[collection]] = item_count
        counts["agentExecutions"] = len(agent_rows)
        snapshot_target_keys = select(
            AnalysisEntityRow.collection + ":" + AnalysisEntityRow.identity_key
        ).where(AnalysisEntityRow.run_id == run.id)
        snapshot_compatible_run_ids = select(AnalysisRunRow.id).where(
            AnalysisRunRow.project_id == run.project_id,
            AnalysisRunRow.id != run.id,
            AnalysisRunRow.story_id == run.story_id,
            AnalysisRunRow.story_revision == run.story_revision,
            AnalysisRunRow.input_fingerprint == run.input_fingerprint,
            AnalysisRunRow.extracted_text_sha256 == run.extracted_text_sha256,
            AnalysisRunRow.profile_fingerprint == run.profile_fingerprint,
            AnalysisRunRow.source_document_id == run.source_document_id,
            AnalysisRunRow.source_revision == run.source_revision,
            AnalysisRunRow.extraction_id == run.extraction_id,
            AnalysisRunRow.extraction_revision == run.extraction_revision,
            AnalysisRunRow.approval_evidence_fingerprint == run.approval_evidence_fingerprint,
        )
        counts["corrections"] = int(
            session.scalar(
                select(func.count())
                .select_from(AnalysisCorrectionRow)
                .where(
                    AnalysisCorrectionRow.project_id == run.project_id,
                    or_(
                        AnalysisCorrectionRow.run_id == run.id,
                        and_(
                            AnalysisCorrectionRow.run_id.in_(snapshot_compatible_run_ids),
                            AnalysisCorrectionRow.target_key.in_(snapshot_target_keys),
                        ),
                        and_(
                            AnalysisCorrectionRow.run_id.is_(None),
                            AnalysisCorrectionRow.target_key.in_(snapshot_target_keys),
                        ),
                    ),
                    AnalysisCorrectionRow.recorded_at <= snapshot.created_at,
                )
            )
            or 0
        )
        execution = session.get(AnalysisExecutionRow, snapshot.execution_id)
        return {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "snapshotId": snapshot.id,
            "runId": run.id,
            "revision": execution.attempt if execution is not None else 1,
            "inputFingerprint": run.input_fingerprint,
            "snapshotFingerprint": snapshot.fingerprint,
            "correctionSetFingerprint": run.correction_set_fingerprint,
            "counts": counts,
            "collections": collections,
            "createdAt": snapshot.created_at,
            "immutable": True,
        }

    def get_run(self, *, project_id: str, run_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            run = self.require_run(session, project_id=project_id, run_id=run_id)
            return self.run_dict(session, run)

    def list_runs(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        if not 1 <= limit <= MAX_ANALYSIS_PAGE_SIZE:
            raise ServiceError(422, "INVALID_PAGE_SIZE", "The page size is invalid.")
        with self.database.session() as session:
            self.projects.require_project(session, project_id)
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(AnalysisRunRow)
                    .where(AnalysisRunRow.project_id == project_id)
                )
                or 0
            )
            latest = session.scalar(
                select(AnalysisRunRow)
                .where(AnalysisRunRow.project_id == project_id)
                .order_by(
                    AnalysisRunRow.created_at.desc(),
                    AnalysisRunRow.id.desc(),
                )
                .limit(1)
            )
            binding = _cursor_binding(
                {
                    "projectId": project_id,
                    "collection": "runs",
                    "order": "created_at,id",
                    "total": total,
                    "latestCreatedAt": (latest.created_at if latest is not None else None),
                    "latestId": latest.id if latest is not None else None,
                }
            )
            after = _decode_key_cursor(binding, cursor)
            conditions = [AnalysisRunRow.project_id == project_id]
            if after is not None:
                after_created_at, after_id = after
                conditions.append(
                    or_(
                        AnalysisRunRow.created_at > after_created_at,
                        and_(
                            AnalysisRunRow.created_at == after_created_at,
                            AnalysisRunRow.id > after_id,
                        ),
                    )
                )
            rows = list(
                session.scalars(
                    select(AnalysisRunRow)
                    .where(*conditions)
                    .order_by(AnalysisRunRow.created_at, AnalysisRunRow.id)
                    .limit(limit + 1)
                )
            )
            page, extra = rows[:limit], len(rows) > limit
            next_cursor = (
                _encode_key_cursor(
                    binding,
                    recorded_at=page[-1].created_at,
                    entity_id=page[-1].id,
                )
                if page and extra
                else None
            )
            return (
                [self.run_dict(session, row) for row in page],
                next_cursor,
                total,
            )

    def project_summary(self, project_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            latest = session.scalar(
                select(AnalysisRunRow)
                .where(AnalysisRunRow.project_id == project_id)
                .order_by(AnalysisRunRow.created_at.desc(), AnalysisRunRow.id.desc())
                .limit(1)
            )
            return {
                "currentRun": self.run_dict(session, latest) if latest is not None else None,
                "profileId": "whole-book-intelligence-v1",
                "analysisContractVersion": ANALYSIS_CONTRACT_VERSION,
            }

    def load_run_input(
        self,
        *,
        run_id: str,
        job_id: str,
    ) -> tuple[AnalysisRunRow, ImportedStoryRow]:
        with self.database.session() as session:
            run = session.get(AnalysisRunRow, run_id)
            if run is None or run.job_id != job_id:
                raise ServiceError(
                    409,
                    "ANALYSIS_RUN_INPUT_INVALID",
                    "The frozen whole-book analysis run is unavailable.",
                )
            _project, _source, extraction, story, review = self.current_approved_input(
                session,
                project_id=run.project_id,
            )
            if (
                story.id != run.story_id
                or story.revision != run.story_revision
                or story.content_fingerprint != run.input_fingerprint
                or extraction.id != run.extraction_id
                or extraction.revision != run.extraction_revision
                or review.id != run.import_review_record_id
                or review.review_id != run.review_id
                or review.revision != run.review_revision
            ):
                raise ServiceError(
                    409,
                    "ANALYSIS_RUN_APPROVAL_STALE",
                    "The approved extraction changed before analysis publication.",
                )
            session.expunge(run)
            session.expunge(story)
            return run, story

    def stage_checkpoints(
        self,
        *,
        job_id: str,
        attempt: int,
    ) -> dict[str, dict[str, Any]]:
        with self.database.session() as session:
            job = session.get(JobRow, job_id)
            if job is None or job.target_id is None or job.type != "analyze_whole_book":
                return {}
            run = session.get(AnalysisRunRow, job.target_id)
            if run is None or run.job_id != job.id:
                raise ServiceError(
                    409,
                    "CHECKPOINT_INCOMPATIBLE",
                    "The saved analysis-stage checkpoint is incompatible.",
                )
            rows = list(
                session.scalars(
                    select(AnalysisStageCheckpointRow)
                    .where(
                        AnalysisStageCheckpointRow.job_id == job_id,
                        AnalysisStageCheckpointRow.attempt <= attempt,
                    )
                    .order_by(
                        AnalysisStageCheckpointRow.ordinal,
                        AnalysisStageCheckpointRow.attempt.desc(),
                        AnalysisStageCheckpointRow.id,
                    )
                )
            )
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                try:
                    payload = parse_json(row.payload_json, {})
                except (TypeError, ValueError) as exc:
                    raise ServiceError(
                        409,
                        "CHECKPOINT_INCOMPATIBLE",
                        "The saved analysis-stage checkpoint failed verification.",
                    ) from exc
                if (
                    row.run_id != run.id
                    or row.project_id != run.project_id
                    or row.input_fingerprint != run.input_fingerprint
                    or row.profile_fingerprint != run.profile_fingerprint
                    or sha256_text(row.payload_json) != row.payload_fingerprint
                    or not isinstance(payload, dict)
                ):
                    raise ServiceError(
                        409,
                        "CHECKPOINT_INCOMPATIBLE",
                        "The saved analysis-stage checkpoint failed verification.",
                    )
                result.setdefault(
                    row.stage,
                    {
                        "ordinal": row.ordinal,
                        "payloadFingerprint": row.payload_fingerprint,
                        "payload": payload,
                    },
                )
            return result

    @staticmethod
    def _agent_stage(role: str) -> str:
        return {
            "structure": "analyze_structure",
            "beats": "analyze_beats",
            "character_identity": "analyze_character_identity",
            "dialogue_attribution": "analyze_dialogue_attribution",
            "point_of_view": "analyze_point_of_view",
            "setting": "analyze_locations",
            "timeline": "analyze_timeline",
            "relationships": "analyze_relationships",
            "emotion_intent": "analyze_emotion_intent",
            "continuity": "analyze_continuity",
            "synthesis": "synthesize_analysis",
        }[role]

    @classmethod
    def _agent_lifecycle_envelope(
        cls,
        *,
        role: str,
        status: str,
        progress: float,
        failure: dict[str, Any] | None = None,
    ) -> str:
        return canonical_json(
            {
                "status": status,
                "progress": progress,
                "currentStage": cls._agent_stage(role),
                "failure": failure,
            }
        )

    def initialize_agent_lifecycle(self, *, job_id: str) -> bool:
        """Persist all governed agent states before analysis work begins."""

        with self.database.session() as session:
            job = session.get(JobRow, job_id)
            if (
                job is None
                or job.type != "analyze_whole_book"
                or job.target_id is None
                or job.state != "running"
            ):
                return False
            run = session.get(AnalysisRunRow, job.target_id)
            if run is None:
                return False
            execution = session.scalar(
                select(AnalysisExecutionRow).where(
                    AnalysisExecutionRow.job_id == job.id,
                    AnalysisExecutionRow.attempt == job.current_attempt,
                )
            )
            now = utc_now()
            attempt = session.get(
                JobAttemptRow,
                {"job_id": job.id, "number": job.current_attempt},
            )
            if execution is None:
                execution = AnalysisExecutionRow(
                    id=new_id(),
                    project_id=run.project_id,
                    run_id=run.id,
                    job_id=job.id,
                    attempt=job.current_attempt,
                    outcome="interrupted",
                    input_fingerprint=run.input_fingerprint,
                    profile_fingerprint=run.profile_fingerprint,
                    agent_registry_fingerprint=AGENT_REGISTRY_FINGERPRINT,
                    output_fingerprint=None,
                    warnings_json="[]",
                    error_code="ANALYSIS_INTERRUPTED",
                    error_message="Whole-book analysis was interrupted before publication.",
                    error_retryable=True,
                    started_at=(attempt.started_at if attempt is not None else None) or now,
                    finished_at=now,
                )
                session.add(execution)
                session.flush()
            existing_roles = set(
                session.scalars(
                    select(AnalysisAgentExecutionRow.role).where(
                        AnalysisAgentExecutionRow.execution_id == execution.id
                    )
                )
            )
            for ordinal, definition in enumerate(AGENT_REGISTRY):
                if definition.role in existing_roles:
                    continue
                status = "running" if ordinal == 0 else "queued"
                session.add(
                    AnalysisAgentExecutionRow(
                        id=new_id(),
                        project_id=run.project_id,
                        run_id=run.id,
                        execution_id=execution.id,
                        ordinal=ordinal,
                        role=definition.role,
                        agent_id=definition.agent_id,
                        agent_version=definition.version,
                        outcome="skipped",
                        input_fingerprint=run.input_fingerprint,
                        output_fingerprint=request_fingerprint(
                            {
                                "role": definition.role,
                                "status": status,
                                "attempt": job.current_attempt,
                            }
                        ),
                        confidence_json=canonical_json(
                            {
                                "score": 0,
                                "classification": "unknown",
                                "basis": "execution_not_completed",
                            }
                        ),
                        warnings_json="[]",
                        provenance_json=canonical_json(
                            {
                                "origin": "runtime_agent",
                                "deterministic": True,
                            }
                        ),
                        envelope_json=self._agent_lifecycle_envelope(
                            role=definition.role,
                            status=status,
                            progress=0,
                        ),
                        started_at=execution.started_at,
                        finished_at=execution.started_at,
                    )
                )
            return True

    def complete_agent_boundary(
        self,
        *,
        job_id: str,
        role: str,
        payload: dict[str, Any],
    ) -> bool:
        """Mark one agent successful and durably start its successor."""

        with self.database.session() as session:
            job = session.get(JobRow, job_id)
            if job is None or job.target_id is None or job.state != "running":
                return False
            execution = session.scalar(
                select(AnalysisExecutionRow).where(
                    AnalysisExecutionRow.job_id == job.id,
                    AnalysisExecutionRow.attempt == job.current_attempt,
                )
            )
            if execution is None:
                return False
            row = session.scalar(
                select(AnalysisAgentExecutionRow).where(
                    AnalysisAgentExecutionRow.execution_id == execution.id,
                    AnalysisAgentExecutionRow.role == role,
                )
            )
            if row is None:
                return False
            now = utc_now()
            row.outcome = "succeeded"
            row.output_fingerprint = request_fingerprint(payload)
            row.confidence_json = canonical_json(
                {
                    "score": 1,
                    "classification": "high",
                    "basis": "deterministic_bounded_rules",
                }
            )
            row.envelope_json = self._agent_lifecycle_envelope(
                role=row.role,
                status="succeeded",
                progress=1,
            )
            row.finished_at = now
            next_row = session.scalar(
                select(AnalysisAgentExecutionRow)
                .where(
                    AnalysisAgentExecutionRow.execution_id == execution.id,
                    AnalysisAgentExecutionRow.ordinal == row.ordinal + 1,
                )
                .limit(1)
            )
            if next_row is not None:
                next_row.envelope_json = self._agent_lifecycle_envelope(
                    role=next_row.role,
                    status="running",
                    progress=0,
                )
                next_row.started_at = now
                next_row.finished_at = now
            return True

    def save_stage_checkpoint(
        self,
        *,
        job_id: str,
        ordinal: int,
        stage: str,
        payload: dict[str, Any],
    ) -> bool:
        payload_json = canonical_json(payload)
        if len(payload_json.encode()) > MAX_AGENT_ENVELOPE_BYTES:
            raise ServiceError(
                422,
                "STAGE_CHECKPOINT_LIMIT_EXCEEDED",
                "The bounded analysis stage checkpoint exceeded its limit.",
            )
        payload_fingerprint = sha256_text(payload_json)
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            job = session.get(JobRow, job_id)
            if (
                job is None
                or job.target_id is None
                or job.type != "analyze_whole_book"
                or job.state != "running"
                or job.cancellation_requested
            ):
                return False
            run = session.get(AnalysisRunRow, job.target_id)
            if run is None:
                return False
            existing = session.scalar(
                select(AnalysisStageCheckpointRow).where(
                    AnalysisStageCheckpointRow.job_id == job.id,
                    AnalysisStageCheckpointRow.attempt == job.current_attempt,
                    AnalysisStageCheckpointRow.ordinal == ordinal,
                )
            )
            if existing is not None:
                if (
                    existing.stage != stage
                    or existing.payload_fingerprint != payload_fingerprint
                    or existing.input_fingerprint != run.input_fingerprint
                    or existing.profile_fingerprint != run.profile_fingerprint
                ):
                    raise ServiceError(
                        409,
                        "STAGE_CHECKPOINT_CONFLICT",
                        "The durable analysis stage checkpoint is incompatible.",
                    )
                return True
            session.add(
                AnalysisStageCheckpointRow(
                    id=new_id(),
                    project_id=run.project_id,
                    run_id=run.id,
                    job_id=job.id,
                    attempt=job.current_attempt,
                    ordinal=ordinal,
                    stage=stage,
                    input_fingerprint=run.input_fingerprint,
                    profile_fingerprint=run.profile_fingerprint,
                    payload_fingerprint=payload_fingerprint,
                    payload_json=payload_json,
                    created_at=utc_now(),
                )
            )
            if isinstance(payload.get("resumeArtifact"), dict):
                job.checkpoint_available = True
            return True

    def publish_result(
        self,
        *,
        session: Session,
        job: JobRow,
        result: dict[str, Any],
    ) -> AnalysisExecutionRow:
        if job.target_type != "analysis_run" or job.target_id is None:
            raise ServiceError(
                409,
                "ANALYSIS_RUN_INPUT_INVALID",
                "The whole-book analysis target is invalid.",
            )
        run = session.get(AnalysisRunRow, job.target_id)
        if (
            run is None
            or run.job_id != job.id
            or run.project_id != job.project_id
            or run.input_fingerprint != job.input_fingerprint
        ):
            raise ServiceError(
                409,
                "ANALYSIS_RUN_INPUT_CHANGED",
                "The immutable run no longer matches the analysis result.",
            )
        story = self._assert_run_approval_current(
            session,
            run=run,
        )
        collections = self._validate_analysis_result(
            run=run,
            story=story,
            result=result,
        )
        now = utc_now()
        attempt = session.get(
            JobAttemptRow,
            {"job_id": job.id, "number": job.current_attempt},
        )
        execution = session.scalar(
            select(AnalysisExecutionRow).where(
                AnalysisExecutionRow.job_id == job.id,
                AnalysisExecutionRow.attempt == job.current_attempt,
            )
        )
        if execution is None:
            execution = AnalysisExecutionRow(
                id=new_id(),
                project_id=run.project_id,
                run_id=run.id,
                job_id=job.id,
                attempt=job.current_attempt,
                outcome="succeeded",
                input_fingerprint=run.input_fingerprint,
                profile_fingerprint=run.profile_fingerprint,
                agent_registry_fingerprint=AGENT_REGISTRY_FINGERPRINT,
                output_fingerprint=str(result["outputFingerprint"]),
                warnings_json="[]",
                started_at=(attempt.started_at if attempt is not None else None) or now,
                finished_at=now,
            )
            session.add(execution)
        else:
            execution.outcome = "succeeded"
            execution.output_fingerprint = str(result["outputFingerprint"])
            execution.warnings_json = "[]"
            execution.error_code = None
            execution.error_message = None
            execution.error_retryable = None
            execution.finished_at = now
        session.flush()

        snapshots = stage_snapshots(result)
        if len(snapshots) > MAX_SNAPSHOT_STAGES:
            raise ServiceError(500, "ANALYSIS_OUTPUT_INVALID", "Too many stages were produced.")
        snapshot_rows: list[AnalysisSnapshotRow] = []
        for value in snapshots:
            manifest = dict(value["manifest"])
            if value["stage"] == "synthesis":
                manifest["summary"] = result["summary"]
                manifest["outputFingerprint"] = result["outputFingerprint"]
                manifest["correctionSetFingerprint"] = run.correction_set_fingerprint
                manifest["gateFingerprints"] = result["gateFingerprints"]
            manifest_fingerprint = request_fingerprint(manifest)
            row = AnalysisSnapshotRow(
                id=new_id(),
                project_id=run.project_id,
                run_id=run.id,
                execution_id=execution.id,
                ordinal=int(value["ordinal"]),
                stage=str(value["stage"]),
                fingerprint=manifest_fingerprint,
                entity_count=int(value["entityCount"]),
                manifest_json=canonical_json(manifest),
                created_at=now,
            )
            snapshot_rows.append(row)
            session.add(row)
        session.flush()
        final_snapshot = snapshot_rows[-1]

        envelopes = agent_envelopes(result)
        for envelope in envelopes:
            envelope_json = canonical_json(envelope)
            if len(envelope_json.encode()) > MAX_AGENT_ENVELOPE_BYTES:
                raise ServiceError(
                    500,
                    "AGENT_ENVELOPE_LIMIT_EXCEEDED",
                    "A bounded runtime-agent envelope exceeded its limit.",
                )
            agent_row = session.scalar(
                select(AnalysisAgentExecutionRow).where(
                    AnalysisAgentExecutionRow.execution_id == execution.id,
                    AnalysisAgentExecutionRow.ordinal == int(envelope["ordinal"]),
                )
            )
            if agent_row is None:
                agent_row = AnalysisAgentExecutionRow(
                    id=new_id(),
                    project_id=run.project_id,
                    run_id=run.id,
                    execution_id=execution.id,
                    ordinal=int(envelope["ordinal"]),
                    role=str(envelope["role"]),
                    agent_id=str(envelope["agentId"]),
                    agent_version=str(envelope["agentVersion"]),
                    outcome=str(envelope["outcome"]),
                    input_fingerprint=run.input_fingerprint,
                    output_fingerprint=str(envelope["outputFingerprint"]),
                    confidence_json=canonical_json(envelope["confidence"]),
                    warnings_json=canonical_json(
                        list(envelope["warnings"])[:MAX_WARNINGS_PER_ENTITY]
                    ),
                    provenance_json=canonical_json(envelope["provenance"]),
                    envelope_json=envelope_json,
                    started_at=execution.started_at,
                    finished_at=now,
                )
                session.add(agent_row)
            else:
                agent_row.role = str(envelope["role"])
                agent_row.agent_id = str(envelope["agentId"])
                agent_row.agent_version = str(envelope["agentVersion"])
                agent_row.outcome = str(envelope["outcome"])
                agent_row.input_fingerprint = run.input_fingerprint
                agent_row.output_fingerprint = str(envelope["outputFingerprint"])
                agent_row.confidence_json = canonical_json(envelope["confidence"])
                agent_row.warnings_json = canonical_json(
                    list(envelope["warnings"])[:MAX_WARNINGS_PER_ENTITY]
                )
                agent_row.provenance_json = canonical_json(envelope["provenance"])
                agent_row.envelope_json = envelope_json
                agent_row.finished_at = now

        all_entities = [
            value for collection in ENTITY_COLLECTIONS for value in collections[collection]
        ]
        entity_ids = {
            str(value["entityId"]): stable_id(run.id, value["entityId"]) for value in all_entities
        }
        for value in all_entities:
            local_id = str(value["entityId"])
            entity_id = entity_ids[local_id]
            payload = _replace_entity_ids(value["payload"], entity_ids)
            parent_id = value.get("parentEntityId")
            score = float(value["confidence"]["score"])
            start_offset = value.get("startOffset")
            end_offset = value.get("endOffset")
            machine_entity_fingerprint = request_fingerprint(
                _wire_entity_payload(
                    collection=str(value["collection"]),
                    payload=payload,
                    run=run,
                    story=story,
                    start=(int(start_offset) if isinstance(start_offset, int) else None),
                    end=(int(end_offset) if isinstance(end_offset, int) else None),
                    correction=None,
                )
            )
            session.add(
                AnalysisEntityRow(
                    id=entity_id,
                    project_id=run.project_id,
                    run_id=run.id,
                    snapshot_id=final_snapshot.id,
                    collection=str(value["collection"]),
                    ordinal=int(value["ordinal"]),
                    parent_entity_id=(
                        entity_ids.get(str(parent_id)) if parent_id is not None else None
                    ),
                    identity_key=str(value["identityKey"]),
                    start_offset=start_offset,
                    end_offset=end_offset,
                    revision=1,
                    payload_json=canonical_json(payload),
                    fingerprint=machine_entity_fingerprint,
                    confidence_score=round(score * 1_000_000),
                    confidence_class=str(value["confidence"]["classification"]),
                    confidence_basis=str(value["confidence"]["basis"]),
                    warnings_json=canonical_json(list(value["warnings"])[:MAX_WARNINGS_PER_ENTITY]),
                    provenance_json=canonical_json(
                        provenance(
                            origin="runtime_agent",
                            actor_id="whole-book-analysis@1.0.0",
                            recorded_at=now,
                            input_fingerprint=run.input_fingerprint,
                            source_references=[
                                {
                                    "entityType": "AnalysisRun",
                                    "entityId": run.id,
                                    "revision": 1,
                                }
                            ],
                            notes="Deterministic local whole-book claim.",
                        )
                    ),
                )
            )
            for evidence_ordinal, evidence in enumerate(
                list(value["evidence"])[:MAX_EVIDENCE_SPANS]
            ):
                session.add(
                    AnalysisEvidenceSpanRow(
                        id=stable_id(run.id, local_id, "evidence", evidence_ordinal),
                        project_id=run.project_id,
                        run_id=run.id,
                        entity_id=entity_id,
                        ordinal=evidence_ordinal,
                        start_offset=int(evidence["startOffset"]),
                        end_offset=int(evidence["endOffset"]),
                        text_sha256=sha256_text(
                            story.exact_text[
                                int(evidence["startOffset"]) : int(evidence["endOffset"])
                            ]
                        ),
                        basis=str(evidence["basis"]),
                        confidence_score=round(float(evidence["confidence"]["score"]) * 1_000_000),
                        provenance_json=canonical_json(
                            {"origin": "runtime_agent", "runId": run.id}
                        ),
                    )
                )
        for gate_id in REVIEW_GATES:
            session.add(
                AnalysisReviewDecisionRow(
                    id=new_id(),
                    project_id=run.project_id,
                    run_id=run.id,
                    snapshot_id=final_snapshot.id,
                    gate_id=gate_id,
                    revision=1,
                    state="pending",
                    artifact_fingerprint=str(result["gateFingerprints"][gate_id]),
                    evidence_fingerprint=str(result["gateFingerprints"][gate_id]),
                    eligible=gate_id != "whole_book_analysis_review",
                    rationale="Awaiting explicit human review.",
                    warning_acknowledgements_json="[]",
                    provenance_json=canonical_json(
                        {
                            "origin": "runtime_agent",
                            "producerId": run.producer_id,
                            "producerVersion": run.producer_version,
                            "runFingerprint": run.run_fingerprint,
                        }
                    ),
                    actor_id=None,
                    idempotency_key=None,
                    supersedes_decision_id=None,
                    decided_at=None,
                    created_at=now,
                )
            )
        return execution

    def record_terminal_execution(
        self,
        *,
        session: Session,
        job: JobRow,
        outcome: str,
        error_code: str | None,
        error_message: str | None,
        error_retryable: bool | None,
        finished_at: str,
    ) -> None:
        if job.type != "analyze_whole_book" or job.target_id is None:
            return
        execution = session.scalar(
            select(AnalysisExecutionRow).where(
                AnalysisExecutionRow.job_id == job.id,
                AnalysisExecutionRow.attempt == job.current_attempt,
            )
        )
        run = session.get(AnalysisRunRow, job.target_id)
        if run is None:
            return
        if execution is None:
            execution = AnalysisExecutionRow(
                id=new_id(),
                project_id=run.project_id,
                run_id=run.id,
                job_id=job.id,
                attempt=job.current_attempt,
                outcome=outcome,
                input_fingerprint=run.input_fingerprint,
                profile_fingerprint=run.profile_fingerprint,
                agent_registry_fingerprint=AGENT_REGISTRY_FINGERPRINT,
                output_fingerprint=None,
                warnings_json="[]",
                error_code=error_code,
                error_message=error_message,
                error_retryable=error_retryable,
                started_at=finished_at,
                finished_at=finished_at,
            )
            session.add(execution)
            session.flush()
        else:
            execution.outcome = outcome
            execution.output_fingerprint = None
            execution.error_code = error_code
            execution.error_message = error_message
            execution.error_retryable = error_retryable
            execution.finished_at = finished_at
        active_agent = None
        for candidate in session.scalars(
            select(AnalysisAgentExecutionRow)
            .where(AnalysisAgentExecutionRow.execution_id == execution.id)
            .order_by(
                AnalysisAgentExecutionRow.ordinal,
                AnalysisAgentExecutionRow.id,
            )
        ):
            envelope = parse_json(candidate.envelope_json, {})
            if isinstance(envelope, dict) and envelope.get("status") == "running":
                active_agent = candidate
                break
        if active_agent is None:
            return
        terminal_status = outcome if outcome in {"failed", "cancelled", "interrupted"} else "failed"
        retryable = bool(error_retryable)
        failure = {
            "code": error_code or "ANALYSIS_FAILED",
            "classification": (
                "cancelled"
                if terminal_status == "cancelled"
                else "interrupted"
                if terminal_status == "interrupted"
                else "transient"
                if retryable
                else "permanent"
            ),
            "retryable": retryable,
            "message": error_message or "Whole-book analysis did not complete.",
            "redacted": True,
        }
        active_agent.outcome = terminal_status
        active_agent.envelope_json = self._agent_lifecycle_envelope(
            role=active_agent.role,
            status=terminal_status,
            progress=0,
            failure=failure,
        )
        active_agent.finished_at = finished_at

    @staticmethod
    def _correction_scope_condition(run: AnalysisRunRow) -> Any:
        compatible_prior_run_ids = select(AnalysisRunRow.id).where(
            AnalysisRunRow.project_id == run.project_id,
            AnalysisRunRow.id != run.id,
            AnalysisRunRow.story_id == run.story_id,
            AnalysisRunRow.story_revision == run.story_revision,
            AnalysisRunRow.input_fingerprint == run.input_fingerprint,
            AnalysisRunRow.extracted_text_sha256 == run.extracted_text_sha256,
            AnalysisRunRow.profile_fingerprint == run.profile_fingerprint,
            AnalysisRunRow.source_document_id == run.source_document_id,
            AnalysisRunRow.source_revision == run.source_revision,
            AnalysisRunRow.extraction_id == run.extraction_id,
            AnalysisRunRow.extraction_revision == run.extraction_revision,
            AnalysisRunRow.approval_evidence_fingerprint
            == run.approval_evidence_fingerprint,
        )
        return or_(
            AnalysisCorrectionRow.run_id == run.id,
            and_(
                AnalysisCorrectionRow.run_id.in_(compatible_prior_run_ids),
                AnalysisCorrectionRow.recorded_at <= run.created_at,
            ),
            and_(
                AnalysisCorrectionRow.run_id.is_(None),
                AnalysisCorrectionRow.category == "dialogue_speaker",
                AnalysisCorrectionRow.expected_run_fingerprint == run.input_fingerprint,
            ),
        )

    def _effective_correction(
        self,
        session: Session,
        *,
        project_id: str,
        target_key: str,
        run: AnalysisRunRow | None = None,
    ) -> AnalysisCorrectionRow | None:
        statement = select(AnalysisCorrectionRow).where(
            AnalysisCorrectionRow.project_id == project_id,
            AnalysisCorrectionRow.target_key == target_key,
        )
        if run is not None:
            statement = statement.where(self._correction_scope_condition(run))
        ordered = statement.order_by(
            AnalysisCorrectionRow.recorded_at.desc(),
            AnalysisCorrectionRow.revision.desc(),
            AnalysisCorrectionRow.id.desc(),
        )
        if run is None:
            return session.scalar(ordered.limit(1))
        for correction in session.scalars(ordered):
            if (
                self._correction_patch_for_run(
                    session,
                    correction=correction,
                    run=run,
                )
                is not None
            ):
                return correction
        return None

    def _effective_corrections(
        self,
        session: Session,
        *,
        project_id: str,
        target_key: str,
        run: AnalysisRunRow,
        through_revision: int | None = None,
    ) -> list[AnalysisCorrectionRow]:
        statement = select(AnalysisCorrectionRow).where(
            AnalysisCorrectionRow.project_id == project_id,
            AnalysisCorrectionRow.target_key == target_key,
            self._correction_scope_condition(run),
        )
        if through_revision is not None:
            statement = statement.where(AnalysisCorrectionRow.revision <= through_revision)
        return list(
            session.scalars(
                statement.order_by(
                    AnalysisCorrectionRow.revision,
                    AnalysisCorrectionRow.recorded_at,
                    AnalysisCorrectionRow.id,
                )
            )
        )

    @staticmethod
    def _patch_string_values(value: Any) -> set[str]:
        if isinstance(value, str):
            return {value}
        if isinstance(value, list):
            return {
                item
                for child in value
                for item in StoryIntelligenceRepository._patch_string_values(child)
            }
        if isinstance(value, dict):
            return {
                item
                for child in value.values()
                for item in StoryIntelligenceRepository._patch_string_values(child)
            }
        return set()

    @staticmethod
    def _replace_patch_ids(value: Any, replacements: Mapping[str, str]) -> Any:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [
                StoryIntelligenceRepository._replace_patch_ids(
                    item,
                    replacements,
                )
                for item in value
            ]
        if isinstance(value, dict):
            return {
                key: StoryIntelligenceRepository._replace_patch_ids(
                    item,
                    replacements,
                )
                for key, item in value.items()
            }
        return value

    def _correction_patch_for_run(
        self,
        session: Session,
        *,
        correction: AnalysisCorrectionRow,
        run: AnalysisRunRow,
    ) -> dict[str, Any] | None:
        patch = parse_json(correction.patch_json, {})
        if not isinstance(patch, dict):
            return None
        if correction.run_id is None:
            if correction.expected_run_fingerprint != run.input_fingerprint:
                return None
        elif correction.run_id != run.id:
            prior_run = session.get(AnalysisRunRow, correction.run_id)
            if (
                prior_run is None
                or prior_run.project_id != run.project_id
                or prior_run.story_id != run.story_id
                or prior_run.story_revision != run.story_revision
                or prior_run.input_fingerprint != run.input_fingerprint
                or prior_run.extracted_text_sha256 != run.extracted_text_sha256
                or prior_run.profile_fingerprint != run.profile_fingerprint
                or prior_run.source_document_id != run.source_document_id
                or prior_run.source_revision != run.source_revision
                or prior_run.extraction_id != run.extraction_id
                or prior_run.extraction_revision != run.extraction_revision
                or prior_run.approval_evidence_fingerprint != run.approval_evidence_fingerprint
            ):
                return None
        if (
            correction.category == "dialogue_speaker"
            and patch.get("legacyPhase") == 0
            and patch.get("kind") == "dialogue_speaker"
        ):
            legacy_character_id = patch.get("characterId")
            if legacy_character_id is None:
                return {
                    "speakerCharacterId": None,
                    "selectedCandidateId": None,
                    "requiresHumanReview": True,
                }
            if not isinstance(legacy_character_id, str):
                return None
            legacy_character = session.get(CharacterRow, legacy_character_id)
            if (
                legacy_character is None
                or legacy_character.project_id != run.project_id
                or legacy_character.story_id != run.story_id
            ):
                return None
            matches: list[AnalysisEntityRow] = []
            for candidate in session.scalars(
                select(AnalysisEntityRow).where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == "characters",
                )
            ):
                candidate_payload = parse_json(candidate.payload_json, {})
                aliases = (
                    candidate_payload.get("aliases", [])
                    if isinstance(candidate_payload, dict)
                    else []
                )
                if (
                    isinstance(candidate_payload, dict)
                    and (
                        candidate_payload.get("normalizedCanonicalName")
                        == legacy_character.normalized_name
                        or (
                            isinstance(aliases, list)
                            and any(
                                isinstance(alias, dict)
                                and alias.get("normalizedAlias")
                                == legacy_character.normalized_name
                                for alias in aliases
                            )
                        )
                    )
                ):
                    matches.append(candidate)
            if len(matches) != 1:
                return None
            return {
                "speakerCharacterId": matches[0].id,
                "selectedCandidateId": None,
                "requiresHumanReview": False,
            }
        if correction.run_id in {None, run.id}:
            return patch

        candidate_ids = self._patch_string_values(patch)
        prior_entities: list[AnalysisEntityRow] = []
        ordered_candidates = sorted(candidate_ids)
        for offset in range(0, len(ordered_candidates), 400):
            prior_entities.extend(
                session.scalars(
                    select(AnalysisEntityRow).where(
                        AnalysisEntityRow.run_id == correction.run_id,
                        AnalysisEntityRow.id.in_(ordered_candidates[offset : offset + 400]),
                    )
                )
            )
        if not prior_entities:
            return patch

        current_by_semantic_key: dict[tuple[str, str], AnalysisEntityRow] = {}
        keys_by_collection: dict[str, set[str]] = {}
        for entity in prior_entities:
            keys_by_collection.setdefault(entity.collection, set()).add(entity.identity_key)
        for collection, identity_keys in keys_by_collection.items():
            ordered_keys = sorted(identity_keys)
            for offset in range(0, len(ordered_keys), 400):
                for entity in session.scalars(
                    select(AnalysisEntityRow).where(
                        AnalysisEntityRow.run_id == run.id,
                        AnalysisEntityRow.collection == collection,
                        AnalysisEntityRow.identity_key.in_(ordered_keys[offset : offset + 400]),
                    )
                ):
                    current_by_semantic_key[(entity.collection, entity.identity_key)] = entity

        replacements: dict[str, str] = {}
        for prior_entity in prior_entities:
            current_entity = current_by_semantic_key.get(
                (prior_entity.collection, prior_entity.identity_key)
            )
            if current_entity is None:
                # The target still resolves, but one of the correction's
                # referenced claims does not. Fail closed instead of applying a
                # stale run-scoped entity ID to a different analysis.
                return None
            replacements[prior_entity.id] = current_entity.id
        remapped = self._replace_patch_ids(patch, replacements)
        return remapped if isinstance(remapped, dict) else None

    def _applicable_corrections(
        self,
        session: Session,
        *,
        project_id: str,
        target_key: str,
        run: AnalysisRunRow,
        through_revision: int | None = None,
    ) -> list[tuple[AnalysisCorrectionRow, dict[str, Any]]]:
        result: list[tuple[AnalysisCorrectionRow, dict[str, Any]]] = []
        for correction in self._effective_corrections(
            session,
            project_id=project_id,
            target_key=target_key,
            run=run,
            through_revision=through_revision,
        ):
            patch = self._correction_patch_for_run(
                session,
                correction=correction,
                run=run,
            )
            if patch is not None:
                result.append((correction, patch))
        return result

    def _registry_effect_index(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> tuple[
        dict[str, str],
        dict[str, _RegistryReplacementEffect],
        dict[str, tuple[str, AnalysisCorrectionRow]],
    ]:
        candidate_statement = select(AnalysisCorrectionRow.target_key).where(
            AnalysisCorrectionRow.project_id == run.project_id,
            AnalysisCorrectionRow.category.in_({"character_merge", "character_split"}),
            self._correction_scope_condition(run),
        )
        if through_correction is not None:
            candidate_statement = candidate_statement.where(
                _correction_at_or_before_condition(through_correction)
            )
        candidate_target_key_rows = list(
            session.scalars(
                candidate_statement
                .distinct()
                .limit(MAX_EFFECTIVE_PROJECTION_TARGETS + 1)
            )
        )
        if len(candidate_target_key_rows) > MAX_EFFECTIVE_PROJECTION_TARGETS:
            raise ServiceError(
                422,
                "ANALYSIS_PROJECTION_LIMIT_EXCEEDED",
                "The bounded corrected registry projection limit was exceeded.",
            )
        candidate_target_keys = set(candidate_target_key_rows)
        if not candidate_target_keys:
            return {}, {}, {}
        identity_keys = {
            target_key.removeprefix("characters:")
            for target_key in candidate_target_keys
            if target_key.startswith("characters:")
        }
        character_rows: list[AnalysisEntityRow] = []
        ordered_identity_keys = sorted(identity_keys)
        for offset in range(0, len(ordered_identity_keys), 400):
            character_rows.extend(
                session.scalars(
                    select(AnalysisEntityRow).where(
                        AnalysisEntityRow.run_id == run.id,
                        AnalysisEntityRow.collection == "characters",
                        AnalysisEntityRow.identity_key.in_(
                            ordered_identity_keys[offset : offset + 400]
                        ),
                    )
                )
            )
        merge_targets: dict[str, tuple[str, AnalysisCorrectionRow]] = {}
        split_mentions: dict[str, tuple[str, AnalysisCorrectionRow]] = {}
        for character in character_rows:
            target_key = f"characters:{character.identity_key}"
            operations = [
                (correction, patch)
                for correction, patch in self._applicable_corrections(
                    session,
                    project_id=run.project_id,
                    target_key=target_key,
                    run=run,
                )
                if correction.category in {"character_merge", "character_split"}
                and (
                    through_correction is None
                    or _correction_order_key(correction)
                    <= _correction_order_key(through_correction)
                )
            ]
            if not operations:
                continue
            correction, patch = max(
                operations,
                key=lambda value: _correction_order_key(value[0]),
            )
            if correction.category == "character_merge":
                merge_target = patch.get("mergeIntoCharacterId")
                if isinstance(merge_target, str):
                    merge_targets[character.id] = (
                        merge_target,
                        correction,
                    )
                continue
            registry_id = patch.get("newRegistryCharacterId")
            mention_ids = patch.get("mentionIds")
            if isinstance(registry_id, str) and isinstance(
                mention_ids,
                list,
            ):
                for mention_id in mention_ids:
                    if not isinstance(mention_id, str):
                        continue
                    prior_split = split_mentions.get(mention_id)
                    if prior_split is None or _correction_order_key(
                        correction
                    ) > _correction_order_key(prior_split[1]):
                        split_mentions[mention_id] = (
                            registry_id,
                            correction,
                        )

        replacement_ids: dict[str, str] = {}
        replacement_effects: dict[str, _RegistryReplacementEffect] = {}
        for source_id, (initial_target, correction) in merge_targets.items():
            target = initial_target
            visited = {source_id}
            chain_corrections = [correction]
            while target in merge_targets and target not in visited:
                visited.add(target)
                target, next_correction = merge_targets[target]
                chain_corrections.append(next_correction)
            if target not in visited:
                replacement_ids[source_id] = target
                replacement_effects[source_id] = _RegistryReplacementEffect(
                    latest_correction=max(
                        chain_corrections,
                        key=_correction_order_key,
                    ),
                    corrections=tuple(
                        sorted(
                            {
                                chain_correction.id: chain_correction
                                for chain_correction in chain_corrections
                            }.values(),
                            key=_correction_order_key,
                        )
                    ),
                )
        return replacement_ids, replacement_effects, split_mentions

    def _apply_registry_effects(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        entity: AnalysisEntityRow,
        payload: dict[str, Any],
        direct_correction: AnalysisCorrectionRow | None = None,
        registry_effects: tuple[
            dict[str, str],
            dict[str, _RegistryReplacementEffect],
            dict[str, tuple[str, AnalysisCorrectionRow]],
        ]
        | None = None,
    ) -> tuple[
        dict[str, Any],
        AnalysisCorrectionRow | None,
        frozenset[str],
    ]:
        if entity.collection == "characters":
            return payload, None, frozenset()
        (
            replacement_ids,
            replacement_effects,
            split_mentions,
        ) = registry_effects or self._registry_effect_index(
            session,
            run=run,
        )

        effective_payload = self._replace_patch_ids(payload, replacement_ids)
        if not isinstance(effective_payload, dict):
            effective_payload = dict(payload)
        for field in (
            "candidateCharacterIds",
            "participantCharacterIds",
            "sourceCandidateCharacterIds",
            "targetCandidateCharacterIds",
            "relatedEntityIds",
        ):
            character_ids = effective_payload.get(field)
            if isinstance(character_ids, list):
                effective_payload[field] = list(dict.fromkeys(character_ids))
        if (
            entity.collection == "relationships"
            and isinstance(
                (collapsed_character_id := effective_payload.get("sourceCharacterId")),
                str,
            )
            and effective_payload.get("targetCharacterId")
            == collapsed_character_id
        ):
            effective_payload["sourceCharacterId"] = None
            effective_payload["targetCharacterId"] = None
            effective_payload["sourceCandidateCharacterIds"] = [
                collapsed_character_id
            ]
            effective_payload["targetCandidateCharacterIds"] = [
                collapsed_character_id
            ]
            effective_payload["resolution"] = "unresolved"
        if (
            entity.collection == "mentions"
            and isinstance(
                (original_candidates := payload.get("candidateCharacterIds")),
                list,
            )
            and len(set(original_candidates)) > 1
            and effective_payload.get("resolution") == "ambiguous"
            and effective_payload.get("effectiveCharacterId") is None
            and isinstance(
                (collapsed_candidates := effective_payload.get(
                    "candidateCharacterIds"
                )),
                list,
            )
            and len(collapsed_candidates) == 1
            and isinstance(collapsed_candidates[0], str)
        ):
            effective_payload["resolution"] = "resolved"
            effective_payload["effectiveCharacterId"] = collapsed_candidates[0]
        applied_by_id: dict[str, AnalysisCorrectionRow] = {
            correction.id: correction
            for source_id in self._patch_string_values(payload)
            if source_id in replacement_effects
            for correction in replacement_effects[source_id].corrections
        }

        if (
            entity.collection == "mentions"
            and entity.id in split_mentions
            and (
                direct_correction is None
                or _correction_order_key(split_mentions[entity.id][1])
                > _correction_order_key(direct_correction)
            )
        ):
            registry_id, correction = split_mentions[entity.id]
            effective_payload["resolution"] = "resolved"
            effective_payload["effectiveCharacterId"] = registry_id
            effective_payload["candidateCharacterIds"] = [registry_id]
            applied_by_id[correction.id] = correction

        if not applied_by_id:
            return effective_payload, None, frozenset()
        applied = tuple(applied_by_id.values())
        return (
            effective_payload,
            max(applied, key=_correction_order_key),
            frozenset(applied_by_id),
        )

    def _entity_dict(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        entity: AnalysisEntityRow,
        story: ImportedStoryRow,
        registry_effects: tuple[
            dict[str, str],
            dict[str, _RegistryReplacementEffect],
            dict[str, tuple[str, AnalysisCorrectionRow]],
        ]
        | None = None,
        excluded_correction_categories: frozenset[str] = frozenset(),
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> dict[str, Any]:
        target_key = f"{entity.collection}:{entity.identity_key}"
        correction_chain = [
            value
            for value in self._applicable_corrections(
                session,
                project_id=run.project_id,
                target_key=target_key,
                run=run,
            )
            if value[0].category not in excluded_correction_categories
            and (
                through_correction is None
                or _correction_order_key(value[0])
                <= _correction_order_key(through_correction)
            )
        ]
        correction = correction_chain[-1][0] if correction_chain else None
        payload = parse_json(entity.payload_json, {})
        direct_correction_ids = {
            correction_value.id
            for correction_value, _effective_patch in correction_chain
        }
        for correction_value, effective_patch in correction_chain:
            payload = _apply_correction_patch(
                payload,
                correction_value,
                effective_patch=effective_patch,
            )
        effective_registry_effects = registry_effects
        if effective_registry_effects is None and entity.collection != "characters":
            effective_registry_effects = self._registry_effect_index(
                session,
                run=run,
                through_correction=through_correction,
            )
        (
            payload,
            registry_correction,
            registry_correction_ids,
        ) = self._apply_registry_effects(
            session,
            run=run,
            entity=entity,
            payload=payload,
            direct_correction=correction,
            registry_effects=effective_registry_effects,
        )
        if registry_correction is not None and (
            correction is None
            or (
                registry_correction.recorded_at,
                registry_correction.revision,
                registry_correction.id,
            )
            > (
                correction.recorded_at,
                correction.revision,
                correction.id,
            )
        ):
            correction = registry_correction
        revision = entity.revision + len(
            direct_correction_ids | set(registry_correction_ids)
        )
        evidence_rows = list(
            session.scalars(
                select(AnalysisEvidenceSpanRow)
                .where(AnalysisEvidenceSpanRow.entity_id == entity.id)
                .order_by(
                    AnalysisEvidenceSpanRow.ordinal,
                    AnalysisEvidenceSpanRow.id,
                )
                .limit(MAX_EVIDENCE_SPANS)
            )
        )
        evidence = []
        for span in evidence_rows:
            if span.end_offset <= span.start_offset:
                continue
            evidence.append(
                _bounded_excerpt(
                    run=run,
                    story=story,
                    start=span.start_offset,
                    end=span.end_offset,
                )
            )
        wire_payload = _wire_entity_payload(
            collection=entity.collection,
            payload=payload,
            run=run,
            story=story,
            start=entity.start_offset,
            end=entity.end_offset,
            correction=correction,
        )
        effective_value_fingerprint = request_fingerprint(wire_payload)
        agent_role = _COLLECTION_AGENT_ROLE[entity.collection]
        agent_definition = next(
            definition for definition in AGENT_REGISTRY if definition.role == agent_role
        )
        agent_execution = session.scalar(
            select(AnalysisAgentExecutionRow)
            .where(
                AnalysisAgentExecutionRow.run_id == run.id,
                AnalysisAgentExecutionRow.role == agent_role,
            )
            .order_by(
                AnalysisAgentExecutionRow.finished_at.desc(),
                AnalysisAgentExecutionRow.id.desc(),
            )
            .limit(1)
        )
        provenance_value: dict[str, Any]
        if correction is not None:
            provenance_value = {
                "origin": "human_correction",
                "recordedAt": correction.recorded_at,
                "inputFingerprint": run.input_fingerprint,
                "correctionId": correction.id,
                "deterministic": False,
            }
        else:
            provenance_value = {
                "origin": "runtime_agent",
                "recordedAt": (
                    agent_execution.finished_at if agent_execution is not None else run.created_at
                ),
                "inputFingerprint": run.input_fingerprint,
                "agentExecutionId": (
                    agent_execution.id
                    if agent_execution is not None
                    else stable_id(run.id, "agent-execution", agent_role)
                ),
                "agentId": agent_definition.agent_id,
                "agentVersion": agent_definition.version,
                "deterministic": True,
            }
        warnings = []
        for warning_value in parse_json(
            entity.warnings_json,
            [],
        )[:MAX_WARNINGS_PER_ENTITY]:
            if not isinstance(warning_value, dict):
                continue
            warnings.append(
                {
                    "code": str(warning_value.get("code") or "ANALYSIS_WARNING"),
                    "severity": str(warning_value.get("severity") or "warning"),
                    "message": str(
                        warning_value.get("message") or "The analysis claim requires review."
                    ),
                    "requiresHumanReview": bool(warning_value.get("requiresHumanReview", True)),
                    "relatedEntityIds": list(
                        dict.fromkeys(
                            str(value)
                            for value in warning_value.get(
                                "relatedEntityIds",
                                [],
                            )
                            if isinstance(value, str)
                        )
                    )[:16],
                    "evidence": evidence,
                }
            )
        result: dict[str, Any] = {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "entityId": entity.id,
            "stableSemanticId": stable_id(
                "analysis-semantic-identity-v1",
                run.project_id,
                run.story_id,
                entity.collection,
                entity.identity_key,
            ),
            "runId": entity.run_id,
            "snapshotId": entity.snapshot_id,
            "ordinal": (
                int(payload["_effectiveOrdinal"])
                if isinstance(payload.get("_effectiveOrdinal"), int)
                else entity.ordinal
            ),
            "revision": entity.revision,
            "effectiveRevision": revision,
            "effectiveAuthority": ("human" if correction is not None else "runtime_agent"),
            "machineEntityFingerprint": entity.fingerprint,
            "effectiveValueFingerprint": effective_value_fingerprint,
            "confidence": {
                "score": entity.confidence_score / 1_000_000,
                "classification": entity.confidence_class,
                "basis": entity.confidence_basis,
                "calibrationId": "governed-local-rules-v1",
            },
            "warnings": warnings,
            "provenance": provenance_value,
            "evidence": evidence,
            **wire_payload,
        }
        return result

    @staticmethod
    def _wire_payload_from_entity_result(
        collection: str,
        item: Mapping[str, Any],
    ) -> dict[str, Any]:
        fields = set(_COLLECTION_PAYLOAD_FIELDS[collection])
        fields.update({"sourceSpan", "exactText"})
        return {field: item[field] for field in fields if field in item}

    def _synthetic_structure_add(
        self,
        *,
        run: AnalysisRunRow,
        collection: str,
        original: dict[str, Any],
        corrected: dict[str, Any],
        identity_correction_id: str | None = None,
    ) -> dict[str, Any]:
        boundary = corrected.get("effectiveBoundary")
        if not isinstance(boundary, dict):
            return corrected
        correction_id = boundary.get("correctionId")
        if not isinstance(correction_id, str):
            return corrected
        identity_correction_id = identity_correction_id or correction_id
        entity_id = stable_id(
            run.id,
            "effective-structure-add",
            collection,
            identity_correction_id,
        )
        synthetic = dict(corrected)
        synthetic["entityId"] = entity_id
        synthetic["stableSemanticId"] = stable_id(
            "analysis-effective-structure-add-v1",
            run.project_id,
            run.story_id,
            collection,
            identity_correction_id,
        )
        if collection == "chapters":
            synthetic["chapterId"] = entity_id
            synthetic["firstSceneId"] = None
            synthetic["lastSceneId"] = None
            synthetic["sceneCount"] = 0
        else:
            synthetic["sceneId"] = entity_id
            synthetic["firstBeatId"] = None
            synthetic["lastBeatId"] = None
            synthetic["beatCount"] = 0
        synthetic["machineEntityFingerprint"] = original["machineEntityFingerprint"]
        synthetic["effectiveValueFingerprint"] = request_fingerprint(
            self._wire_payload_from_entity_result(collection, synthetic)
        )
        return synthetic

    def _synthetic_split_character(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        source: dict[str, Any],
        ordinal: int,
        registry_effects: tuple[
            dict[str, str],
            dict[str, _RegistryReplacementEffect],
            dict[str, tuple[str, AnalysisCorrectionRow]],
        ],
    ) -> dict[str, Any] | None:
        registry = source.get("effectiveRegistry")
        if not isinstance(registry, dict) or registry.get("operation") != "split":
            return None
        split_identity = registry.get("splitIdentity")
        correction_id = registry.get("correctionId")
        if not isinstance(split_identity, dict) or not isinstance(
            correction_id,
            str,
        ):
            return None
        registry_character_id = split_identity.get("registryCharacterId")
        canonical_name = split_identity.get("canonicalName")
        normalized_name = split_identity.get("normalizedCanonicalName")
        mention_ids = split_identity.get("mentionIds")
        if (
            not isinstance(registry_character_id, str)
            or not isinstance(canonical_name, str)
            or not isinstance(normalized_name, str)
            or not isinstance(mention_ids, list)
            or not all(isinstance(value, str) for value in mention_ids)
        ):
            return None
        mention_rows = {
            row.id: row
            for row in session.scalars(
                select(AnalysisEntityRow).where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == "mentions",
                    AnalysisEntityRow.id.in_(mention_ids),
                )
            )
        }
        if set(mention_rows) != set(mention_ids):
            return None
        ordered_mentions = [
            self._entity_dict(
                session,
                run=run,
                entity=mention_row,
                story=story,
                registry_effects=registry_effects,
            )
            for mention_row in sorted(
                mention_rows.values(),
                key=lambda value: (
                    value.start_offset if value.start_offset is not None else 0,
                    value.end_offset if value.end_offset is not None else 0,
                    value.id,
                ),
            )
        ]
        first_mention = ordered_mentions[0]
        last_mention = ordered_mentions[-1]
        evidence = [
            evidence_value
            for mention in ordered_mentions
            for evidence_value in mention.get("evidence", [])
            if isinstance(evidence_value, dict)
        ][:MAX_EVIDENCE_SPANS]
        named_mention_ids = [
            str(mention["entityId"])
            for mention in ordered_mentions
            if mention.get("mentionKind") != "pronoun"
        ]
        wire_payload: dict[str, Any] = {
            "characterId": registry_character_id,
            "registryCharacterId": registry_character_id,
            "projectId": run.project_id,
            "storyId": run.story_id,
            "registryScope": "project_story",
            "stableAcrossCompatibleRuns": True,
            "canonicalName": canonical_name,
            "normalizedCanonicalName": normalized_name,
            "kind": source.get("kind", "person"),
            "identityStatus": "resolved",
            "aliases": [],
            "honorifics": [],
            "pronounEvidence": [],
            "firstMentionId": first_mention["entityId"],
            "lastMentionId": last_mention["entityId"],
            "namedMentionIds": named_mention_ids,
            "ambiguousMentionIds": [],
            "firstEvidence": first_mention.get("evidence", [])[:1],
            "lastEvidence": last_mention.get("evidence", [])[:1],
            "mentionCount": len(ordered_mentions),
        }
        recorded_at = str(
            source.get("provenance", {}).get("recordedAt")
            if isinstance(source.get("provenance"), dict)
            else run.created_at
        )
        return {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "entityId": registry_character_id,
            "stableSemanticId": stable_id(
                "analysis-human-split-character-v1",
                run.project_id,
                run.story_id,
                registry_character_id,
            ),
            "runId": run.id,
            "snapshotId": source["snapshotId"],
            "ordinal": ordinal,
            "revision": 1,
            "effectiveRevision": max(
                1,
                int(source.get("effectiveRevision", 1)),
            ),
            "effectiveAuthority": "human",
            "machineEntityFingerprint": request_fingerprint(
                {
                    "correctionId": correction_id,
                    "registryCharacterId": registry_character_id,
                    "sourceMachineEntityFingerprint": source["machineEntityFingerprint"],
                }
            ),
            "effectiveValueFingerprint": request_fingerprint(wire_payload),
            "confidence": {
                "score": 1.0,
                "classification": "high",
                "basis": "durable_human_correction",
                "calibrationId": "human-authority",
            },
            "warnings": [],
            "provenance": {
                "origin": "human_correction",
                "recordedAt": recorded_at,
                "inputFingerprint": run.input_fingerprint,
                "correctionId": correction_id,
                "deterministic": False,
            },
            "evidence": evidence,
            **wire_payload,
        }

    def _bounded_projection_rows(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        collection: str,
        category: str,
        base_conditions: list[Any],
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> tuple[list[AnalysisEntityRow], list[str]]:
        target_statement = select(AnalysisCorrectionRow.target_key).where(
            AnalysisCorrectionRow.project_id == run.project_id,
            AnalysisCorrectionRow.category == category,
            AnalysisCorrectionRow.target_key.like(f"{collection}:%"),
            self._correction_scope_condition(run),
        )
        if through_correction is not None:
            target_statement = target_statement.where(
                _correction_at_or_before_condition(through_correction)
            )
        target_keys = list(
            session.scalars(
                target_statement
                .distinct()
                .limit(MAX_EFFECTIVE_PROJECTION_TARGETS + 1)
            )
        )
        if len(target_keys) > MAX_EFFECTIVE_PROJECTION_TARGETS:
            raise ServiceError(
                422,
                "ANALYSIS_PROJECTION_LIMIT_EXCEEDED",
                "The bounded corrected entity projection limit was exceeded.",
            )
        identity_keys = sorted(
            {
                target_key.removeprefix(f"{collection}:")
                for target_key in target_keys
                if target_key.startswith(f"{collection}:")
            }
        )
        rows: list[AnalysisEntityRow] = []
        for offset in range(0, len(identity_keys), 400):
            rows.extend(
                session.scalars(
                    select(AnalysisEntityRow)
                    .where(
                        *base_conditions,
                        AnalysisEntityRow.identity_key.in_(identity_keys[offset : offset + 400]),
                    )
                    .order_by(
                        AnalysisEntityRow.ordinal,
                        AnalysisEntityRow.id,
                    )
                )
            )
        return rows, [row.id for row in rows]

    def _structure_operations(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        entity: AnalysisEntityRow,
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> list[tuple[AnalysisCorrectionRow, dict[str, Any]]]:
        operations = [
            (correction, patch)
            for correction, patch in self._applicable_corrections(
                session,
                project_id=run.project_id,
                target_key=f"{entity.collection}:{entity.identity_key}",
                run=run,
            )
            if correction.category in {"structure_boundary", "structure_label"}
        ]
        if through_correction is not None:
            cutoff = _correction_order_key(through_correction)
            operations = [
                value
                for value in operations
                if _correction_order_key(value[0]) <= cutoff
            ]
        return sorted(
            operations,
            key=lambda value: _correction_order_key(value[0]),
        )

    def _structure_item_from_effects(
        self,
        *,
        run: AnalysisRunRow,
        collection: str,
        original: dict[str, Any],
        effects: tuple[tuple[AnalysisCorrectionRow, dict[str, Any]], ...],
    ) -> dict[str, Any]:
        result = dict(original)
        for correction, patch in effects:
            if correction.category == "structure_label":
                field = "title" if collection == "chapters" else "heading"
                result[field] = patch.get(field)
                continue
            operation = patch.get("operation")
            result["effectiveBoundary"] = {
                "operation": operation,
                "included": operation != "remove",
                "parentEntityId": patch.get("parentEntityId"),
                "ordinal": patch.get("ordinal"),
                "sourceSpan": patch.get("sourceSpan"),
                "authority": "human",
                "correctionId": correction.id,
            }
            result["sourceSpan"] = patch.get("sourceSpan")
            result["ordinal"] = patch.get("ordinal")
            if collection == "scenes":
                result["chapterId"] = patch.get("parentEntityId")
                result["boundaryKind"] = patch.get("boundaryKind")
        correction = effects[-1][0]
        result["effectiveRevision"] = int(original["effectiveRevision"]) + len(effects)
        result["effectiveAuthority"] = "human"
        result["provenance"] = {
            "origin": "human_correction",
            "recordedAt": correction.recorded_at,
            "inputFingerprint": run.input_fingerprint,
            "correctionId": correction.id,
            "deterministic": False,
        }
        return self._refresh_structural_fingerprint(
            collection=collection,
            item=result,
        )

    def _structure_boundary_state(
        self,
        session: Session,
        *,
        row: AnalysisEntityRow,
        operations: list[tuple[AnalysisCorrectionRow, dict[str, Any]]],
    ) -> _StructureBoundaryState:
        source_active = True
        source_effects: list[tuple[AnalysisCorrectionRow, dict[str, Any]]] = []
        synthetic_root: AnalysisCorrectionRow | None = None
        synthetic_active = False
        synthetic_effects: list[tuple[AnalysisCorrectionRow, dict[str, Any]]] = []
        branch_by_correction_id: dict[str, str] = {}

        for correction, patch in operations:
            superseded_branch = (
                branch_by_correction_id.get(correction.supersedes_correction_id)
                if correction.supersedes_correction_id is not None
                else None
            )
            stored_target = (
                session.get(AnalysisEntityRow, correction.target_entity_id)
                if correction.target_entity_id is not None
                else None
            )
            stored_target_is_source = correction.target_entity_id == row.id or (
                stored_target is not None
                and stored_target.collection == row.collection
                and stored_target.identity_key == row.identity_key
            )
            targets_synthetic = superseded_branch == "synthetic" or (
                superseded_branch is None
                and synthetic_root is not None
                and not stored_target_is_source
            )

            if correction.category == "structure_label":
                branch = "synthetic" if targets_synthetic else "source"
                branch_by_correction_id[correction.id] = branch
                if branch == "synthetic" and synthetic_root is not None:
                    synthetic_effects.append((correction, patch))
                else:
                    source_effects.append((correction, patch))
                continue

            operation = patch.get("operation")
            if operation == "add":
                if targets_synthetic and synthetic_root is not None:
                    synthetic_active = True
                    synthetic_effects.append((correction, patch))
                    branch_by_correction_id[correction.id] = "synthetic"
                elif not source_active:
                    source_active = True
                    source_effects.append((correction, patch))
                    branch_by_correction_id[correction.id] = "source"
                elif synthetic_root is None:
                    synthetic_root = correction
                    synthetic_active = True
                    synthetic_effects.append((correction, patch))
                    branch_by_correction_id[correction.id] = "synthetic"
                else:
                    source_active = True
                    source_effects.append((correction, patch))
                    branch_by_correction_id[correction.id] = "source"
                continue

            branch = "synthetic" if targets_synthetic else "source"
            branch_by_correction_id[correction.id] = branch
            if branch == "synthetic" and synthetic_root is not None:
                synthetic_active = operation != "remove"
                synthetic_effects.append((correction, patch))
            else:
                source_active = operation != "remove"
                source_effects.append((correction, patch))

        return _StructureBoundaryState(
            source_active=source_active,
            source_effects=tuple(source_effects),
            source_revision_count=len(source_effects),
            synthetic_root=synthetic_root,
            synthetic_active=synthetic_active,
            synthetic_effects=tuple(synthetic_effects),
            synthetic_revision_count=len(synthetic_effects),
            branch_by_correction_id=branch_by_correction_id,
        )

    def _project_structure_row(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        row: AnalysisEntityRow,
        registry_effects: tuple[
            dict[str, str],
            dict[str, _RegistryReplacementEffect],
            dict[str, tuple[str, AnalysisCorrectionRow]],
        ],
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> tuple[list[dict[str, Any]], frozenset[str]]:
        original = self._entity_dict(
            session,
            run=run,
            entity=row,
            story=story,
            registry_effects=registry_effects,
            excluded_correction_categories=frozenset(
                {"structure_boundary", "structure_label"}
            ),
            through_correction=through_correction,
        )
        operations = self._structure_operations(
            session,
            run=run,
            entity=row,
            through_correction=through_correction,
        )
        if not operations:
            return [original], frozenset({row.id})

        state = self._structure_boundary_state(
            session,
            row=row,
            operations=operations,
        )

        items: list[dict[str, Any]] = []
        known_ids = {row.id}
        if state.source_active:
            if not state.source_effects:
                items.append(original)
            else:
                items.append(
                    self._structure_item_from_effects(
                        run=run,
                        collection=row.collection,
                        original=original,
                        effects=state.source_effects,
                    )
                )
        if state.synthetic_root is not None:
            synthetic_id = stable_id(
                run.id,
                "effective-structure-add",
                row.collection,
                state.synthetic_root.id,
            )
            known_ids.add(synthetic_id)
            if state.synthetic_active and state.synthetic_effects:
                corrected = self._structure_item_from_effects(
                    run=run,
                    collection=row.collection,
                    original=original,
                    effects=state.synthetic_effects,
                )
                items.append(
                    self._synthetic_structure_add(
                        run=run,
                        collection=row.collection,
                        original=original,
                        corrected=corrected,
                        identity_correction_id=state.synthetic_root.id,
                    )
                )
        return (
            sorted(
                items,
                key=lambda value: (int(value["ordinal"]), str(value["entityId"])),
            ),
            frozenset(known_ids),
        )

    def _effective_structure_graph(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> _EffectiveStructureGraph:
        registry_effects = self._registry_effect_index(
            session,
            run=run,
            through_correction=through_correction,
        )
        rows_by_collection: dict[str, list[AnalysisEntityRow]] = {}
        for collection in ("chapters", "scenes"):
            rows, _target_ids = self._bounded_projection_rows(
                session,
                run=run,
                collection=collection,
                category="structure_boundary",
                base_conditions=[
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == collection,
                ],
                through_correction=through_correction,
            )
            rows_by_collection[collection] = rows

        if not rows_by_collection["chapters"] and not rows_by_collection["scenes"]:
            return _EffectiveStructureGraph(
                chapter_target_ids=(),
                scene_target_ids=(),
                removed_chapter_ids=frozenset(),
                removed_scene_ids=frozenset(),
                active_chapter_ids=frozenset(),
                active_scene_ids=frozenset(),
                effective_scene_parents={},
                chapter_items_by_source={},
                scene_items_by_source={},
                removed_entity_ids={},
                mention_items_by_character={},
                ambiguous_mention_items_by_character={},
                mention_index_loaded=False,
            )

        chapter_items_by_source: dict[str, list[dict[str, Any]]] = {}
        known_chapter_ids: set[str] = set()
        for row in rows_by_collection["chapters"]:
            projected, known_ids = self._project_structure_row(
                session,
                run=run,
                story=story,
                row=row,
                registry_effects=registry_effects,
                through_correction=through_correction,
            )
            chapter_items_by_source[row.id] = projected
            known_chapter_ids.update(known_ids)

        chapter_target_ids = {row.id for row in rows_by_collection["chapters"]}
        all_chapter_ids = set(
            session.scalars(
                select(AnalysisEntityRow.id).where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == "chapters",
                )
            )
        )
        active_chapter_ids = all_chapter_ids - chapter_target_ids
        active_chapter_ids.update(
            str(item["entityId"])
            for projected in chapter_items_by_source.values()
            for item in projected
        )
        removed_chapter_ids = known_chapter_ids - active_chapter_ids

        scene_items_by_source: dict[str, list[dict[str, Any]]] = {}
        effective_scene_parents: dict[str, str | None] = {}
        known_scene_ids: set[str] = set()
        for row in rows_by_collection["scenes"]:
            projected, known_ids = self._project_structure_row(
                session,
                run=run,
                story=story,
                row=row,
                registry_effects=registry_effects,
                through_correction=through_correction,
            )
            projected = [
                item
                for item in projected
                if item.get("chapterId") in active_chapter_ids
            ]
            scene_items_by_source[row.id] = projected
            known_scene_ids.update(known_ids)
            for item in projected:
                effective_scene_parents[str(item["entityId"])] = str(item["chapterId"])

        scene_target_ids = {row.id for row in rows_by_collection["scenes"]}
        all_scene_rows = list(
            session.execute(
                select(
                    AnalysisEntityRow.id,
                    AnalysisEntityRow.parent_entity_id,
                ).where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == "scenes",
                )
            )
        )
        active_scene_ids: set[str] = set()
        for scene_id, parent_id in all_scene_rows:
            if scene_id in scene_target_ids:
                continue
            if parent_id in active_chapter_ids:
                active_scene_ids.add(str(scene_id))
                effective_scene_parents[str(scene_id)] = str(parent_id)
        active_scene_ids.update(effective_scene_parents)
        all_source_scene_ids = {str(scene_id) for scene_id, _parent_id in all_scene_rows}
        removed_scene_ids = (
            (all_source_scene_ids | known_scene_ids) - active_scene_ids
        )

        removed_entity_ids: dict[str, set[str]] = {
            "chapters": set(removed_chapter_ids),
            "scenes": set(removed_scene_ids),
        }
        other_rows = list(
            session.execute(
                select(
                    AnalysisEntityRow.id,
                    AnalysisEntityRow.collection,
                    AnalysisEntityRow.parent_entity_id,
                    AnalysisEntityRow.payload_json,
                ).where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection.not_in({"chapters", "scenes"}),
                )
            )
        )
        payload_by_id: dict[str, dict[str, Any]] = {}
        collection_by_id: dict[str, str] = {}
        parent_by_id: dict[str, str | None] = {}
        for entity_id, collection, parent_id, payload_json in other_rows:
            entity_id = str(entity_id)
            collection = str(collection)
            payload = parse_json(str(payload_json), {})
            payload_by_id[entity_id] = payload if isinstance(payload, dict) else {}
            collection_by_id[entity_id] = collection
            parent_by_id[entity_id] = str(parent_id) if parent_id is not None else None

        direct_scene_collections = {
            "beats",
            "mentions",
            "dialogue-lines",
            "narration-spans",
            "pov-segments",
            "timeline-events",
            "relationships",
            "emotional-states",
            "dramatic-intents",
        }
        for entity_id, collection in collection_by_id.items():
            payload = payload_by_id[entity_id]
            if (
                collection in direct_scene_collections
                and payload.get("sceneId") in removed_scene_ids
            ):
                removed_entity_ids.setdefault(collection, set()).add(entity_id)

        removed_dialogue_ids = removed_entity_ids.get("dialogue-lines", set())
        removed_beat_ids = removed_entity_ids.get("beats", set())
        for entity_id, collection in collection_by_id.items():
            parent_id = parent_by_id[entity_id]
            payload = payload_by_id[entity_id]
            if (
                collection == "narration-spans"
                and parent_id in removed_beat_ids
                or collection == "dramatic-intents"
                and (
                    parent_id in removed_dialogue_ids
                    or payload.get("dialogueLineId") in removed_dialogue_ids
                    or payload.get("beatId") in removed_beat_ids
                )
            ):
                removed_entity_ids.setdefault(collection, set()).add(entity_id)

        removed_event_ids = removed_entity_ids.get("timeline-events", set())
        for entity_id, collection in collection_by_id.items():
            if collection != "temporal-constraints":
                continue
            payload = payload_by_id[entity_id]
            if (
                payload.get("sourceEventId") in removed_event_ids
                or payload.get("targetEventId") in removed_event_ids
            ):
                removed_entity_ids.setdefault(collection, set()).add(entity_id)

        for entity_id, collection in collection_by_id.items():
            if collection != "locations":
                continue
            scene_ids = payload_by_id[entity_id].get("sceneIds")
            if isinstance(scene_ids, list) and not any(
                scene_id in active_scene_ids for scene_id in scene_ids
            ):
                removed_entity_ids.setdefault(collection, set()).add(entity_id)

        removed_ids = {
            entity_id
            for values in removed_entity_ids.values()
            for entity_id in values
        }
        for entity_id, collection in collection_by_id.items():
            if collection != "continuity-findings":
                continue
            related_ids = payload_by_id[entity_id].get("relatedEntityIds")
            if isinstance(related_ids, list) and related_ids and all(
                related_id in removed_ids for related_id in related_ids
            ):
                removed_entity_ids.setdefault(collection, set()).add(entity_id)

        return _EffectiveStructureGraph(
            chapter_target_ids=tuple(sorted(chapter_target_ids)),
            scene_target_ids=tuple(sorted(scene_target_ids)),
            removed_chapter_ids=frozenset(removed_chapter_ids),
            removed_scene_ids=frozenset(removed_scene_ids),
            active_chapter_ids=frozenset(active_chapter_ids),
            active_scene_ids=frozenset(active_scene_ids),
            effective_scene_parents=effective_scene_parents,
            chapter_items_by_source=chapter_items_by_source,
            scene_items_by_source=scene_items_by_source,
            removed_entity_ids={
                collection: frozenset(entity_ids)
                for collection, entity_ids in removed_entity_ids.items()
            },
            mention_items_by_character={},
            ambiguous_mention_items_by_character={},
            mention_index_loaded=False,
        )

    def _refresh_structural_fingerprint(
        self,
        *,
        collection: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(item)
        result["effectiveValueFingerprint"] = request_fingerprint(
            self._wire_payload_from_entity_result(collection, result)
        )
        return result

    def _apply_scene_beat_aggregates(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        scene_id = str(item["entityId"])
        beat_conditions = (
            AnalysisEntityRow.run_id == run.id,
            AnalysisEntityRow.collection == "beats",
            AnalysisEntityRow.parent_entity_id == scene_id,
        )
        beat_count = int(
            session.scalar(
                select(func.count()).select_from(AnalysisEntityRow).where(*beat_conditions)
            )
            or 0
        )
        first_beat = session.scalar(
            select(AnalysisEntityRow)
            .where(*beat_conditions)
            .order_by(AnalysisEntityRow.ordinal, AnalysisEntityRow.id)
            .limit(1)
        )
        last_beat = session.scalar(
            select(AnalysisEntityRow)
            .where(*beat_conditions)
            .order_by(
                AnalysisEntityRow.ordinal.desc(),
                AnalysisEntityRow.id.desc(),
            )
            .limit(1)
        )
        result = dict(item)
        result["firstBeatId"] = first_beat.id if first_beat is not None else None
        result["lastBeatId"] = last_beat.id if last_beat is not None else None
        result["beatCount"] = beat_count
        return self._refresh_structural_fingerprint(
            collection="scenes",
            item=result,
        )

    def _apply_chapter_scene_aggregates(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        graph: _EffectiveStructureGraph,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        chapter_id = str(item["entityId"])
        normal_conditions: list[Any] = [
            AnalysisEntityRow.run_id == run.id,
            AnalysisEntityRow.collection == "scenes",
            AnalysisEntityRow.parent_entity_id == chapter_id,
        ]
        for offset in range(0, len(graph.scene_target_ids), 400):
            normal_conditions.append(
                ~AnalysisEntityRow.id.in_(graph.scene_target_ids[offset : offset + 400])
            )
        normal_count = int(
            session.scalar(
                select(func.count()).select_from(AnalysisEntityRow).where(*normal_conditions)
            )
            or 0
        )
        first_normal = session.scalar(
            select(AnalysisEntityRow)
            .where(*normal_conditions)
            .order_by(AnalysisEntityRow.ordinal, AnalysisEntityRow.id)
            .limit(1)
        )
        last_normal = session.scalar(
            select(AnalysisEntityRow)
            .where(*normal_conditions)
            .order_by(
                AnalysisEntityRow.ordinal.desc(),
                AnalysisEntityRow.id.desc(),
            )
            .limit(1)
        )
        special_scenes = [
            scene
            for projected in graph.scene_items_by_source.values()
            for scene in projected
            if scene.get("chapterId") == chapter_id
        ]
        ordered_candidates = sorted(
            [
                *(
                    [
                        (
                            first_normal.ordinal,
                            first_normal.id,
                        )
                    ]
                    if first_normal is not None
                    else []
                ),
                *(
                    [
                        (
                            last_normal.ordinal,
                            last_normal.id,
                        )
                    ]
                    if last_normal is not None
                    and (first_normal is None or last_normal.id != first_normal.id)
                    else []
                ),
                *[(int(scene["ordinal"]), str(scene["entityId"])) for scene in special_scenes],
            ],
            key=lambda value: (value[0], value[1]),
        )
        result = dict(item)
        result["sceneCount"] = normal_count + len(special_scenes)
        result["firstSceneId"] = ordered_candidates[0][1] if ordered_candidates else None
        result["lastSceneId"] = ordered_candidates[-1][1] if ordered_candidates else None
        return self._refresh_structural_fingerprint(
            collection="chapters",
            item=result,
        )

    def _effective_structural_entity(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        entity: AnalysisEntityRow,
        effective_entity_id: str | None = None,
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> dict[str, Any]:
        graph = self._effective_structure_graph(
            session,
            run=run,
            story=story,
            through_correction=through_correction,
        )
        registry_effects = self._registry_effect_index(
            session,
            run=run,
            through_correction=through_correction,
        )
        original = self._entity_dict(
            session,
            run=run,
            entity=entity,
            story=story,
            registry_effects=registry_effects,
            excluded_correction_categories=frozenset(
                {"structure_boundary", "structure_label"}
            ),
        )
        item = original
        if entity.collection in {"chapters", "scenes"}:
            operations = self._structure_operations(
                session,
                run=run,
                entity=entity,
                through_correction=through_correction,
            )
            state = self._structure_boundary_state(
                session,
                row=entity,
                operations=operations,
            )
            targets_source = effective_entity_id in {None, entity.id}
            effects = (
                state.source_effects
                if targets_source
                else state.synthetic_effects
            )
            if effects:
                item = self._structure_item_from_effects(
                    run=run,
                    collection=entity.collection,
                    original=original,
                    effects=effects,
                )
                if not targets_source and state.synthetic_root is not None:
                    item = self._synthetic_structure_add(
                        run=run,
                        collection=entity.collection,
                        original=original,
                        corrected=item,
                        identity_correction_id=state.synthetic_root.id,
                    )
        if entity.collection == "chapters":
            return self._apply_chapter_scene_aggregates(
                session,
                run=run,
                graph=graph,
                item=item,
            )
        if entity.collection == "scenes":
            return self._apply_scene_beat_aggregates(
                session,
                run=run,
                item=item,
            )
        if entity.collection != "beats":
            return item
        effective_chapter_id = graph.effective_scene_parents.get(str(entity.parent_entity_id))
        if effective_chapter_id is None:
            return item
        result = dict(item)
        result["chapterId"] = effective_chapter_id
        narration = result.get("narration")
        if isinstance(narration, dict):
            result["narration"] = {
                **narration,
                "chapterId": effective_chapter_id,
            }
        return self._refresh_structural_fingerprint(
            collection="beats",
            item=result,
        )

    def _effective_mention_index(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        removed_mention_ids: frozenset[str] = frozenset(),
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> tuple[
        dict[str, tuple[dict[str, Any], ...]],
        dict[str, tuple[dict[str, Any], ...]],
    ]:
        registry_effects = self._registry_effect_index(
            session,
            run=run,
            through_correction=through_correction,
        )
        correction_statement = select(AnalysisCorrectionRow).where(
            AnalysisCorrectionRow.project_id == run.project_id,
            AnalysisCorrectionRow.category == "mention_resolution",
            AnalysisCorrectionRow.target_key.like("mentions:%"),
            self._correction_scope_condition(run),
        )
        if through_correction is not None:
            correction_statement = correction_statement.where(
                _correction_at_or_before_condition(through_correction)
            )
        correction_chains: dict[
            str,
            list[tuple[AnalysisCorrectionRow, dict[str, Any]]],
        ] = {}
        for correction in session.scalars(correction_statement):
            patch = self._correction_patch_for_run(
                session,
                correction=correction,
                run=run,
            )
            if patch is None:
                continue
            correction_chains.setdefault(
                correction.target_key,
                [],
            ).append((correction, patch))
        for values in correction_chains.values():
            values.sort(
                key=lambda value: (
                    value[0].revision,
                    value[0].recorded_at,
                    value[0].id,
                )
            )

        mention_rows = list(
            session.scalars(
                select(AnalysisEntityRow)
                .where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == "mentions",
                )
                .order_by(
                    AnalysisEntityRow.start_offset,
                    AnalysisEntityRow.end_offset,
                    AnalysisEntityRow.id,
                )
            )
        )
        evidence_by_entity: dict[str, list[dict[str, Any]]] = {}
        for span in session.scalars(
            select(AnalysisEvidenceSpanRow)
            .join(
                AnalysisEntityRow,
                AnalysisEvidenceSpanRow.entity_id == AnalysisEntityRow.id,
            )
            .where(
                AnalysisEntityRow.run_id == run.id,
                AnalysisEntityRow.collection == "mentions",
            )
            .order_by(
                AnalysisEvidenceSpanRow.entity_id,
                AnalysisEvidenceSpanRow.ordinal,
                AnalysisEvidenceSpanRow.id,
            )
        ):
            if span.end_offset <= span.start_offset:
                continue
            evidence_values = evidence_by_entity.setdefault(span.entity_id, [])
            if len(evidence_values) >= MAX_EVIDENCE_SPANS:
                continue
            evidence_values.append(
                _bounded_excerpt(
                    run=run,
                    story=story,
                    start=span.start_offset,
                    end=span.end_offset,
                )
            )

        resolved: dict[str, list[dict[str, Any]]] = {}
        ambiguous: dict[str, list[dict[str, Any]]] = {}
        for mention_row in mention_rows:
            if mention_row.id in removed_mention_ids:
                continue
            payload = parse_json(mention_row.payload_json, {})
            direct_correction: AnalysisCorrectionRow | None = None
            for correction, patch in correction_chains.get(
                f"mentions:{mention_row.identity_key}",
                (),
            ):
                payload = _apply_correction_patch(
                    payload,
                    correction,
                    effective_patch=patch,
                )
                direct_correction = correction
            (
                effective_payload,
                _registry_correction,
                _registry_correction_ids,
            ) = self._apply_registry_effects(
                session,
                run=run,
                entity=mention_row,
                payload=payload,
                direct_correction=direct_correction,
                registry_effects=registry_effects,
            )
            mention_item = {
                "entityId": mention_row.id,
                "mentionKind": effective_payload.get("mentionKind"),
                "effectiveCharacterId": effective_payload.get(
                    "effectiveCharacterId"
                ),
                "candidateCharacterIds": effective_payload.get(
                    "candidateCharacterIds",
                    [],
                ),
                "evidence": evidence_by_entity.get(mention_row.id, []),
            }
            effective_character_id = mention_item.get("effectiveCharacterId")
            if isinstance(effective_character_id, str):
                resolved.setdefault(effective_character_id, []).append(mention_item)
                continue
            for candidate_character_id in mention_item.get(
                "candidateCharacterIds",
                [],
            ):
                if isinstance(candidate_character_id, str):
                    ambiguous.setdefault(
                        candidate_character_id,
                        [],
                    ).append(mention_item)
        return (
            {
                character_id: tuple(items)
                for character_id, items in resolved.items()
            },
            {
                character_id: tuple(items)
                for character_id, items in ambiguous.items()
            },
        )

    def _ensure_structure_mention_index(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        graph: _EffectiveStructureGraph,
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> None:
        if graph.mention_index_loaded:
            return
        (
            graph.mention_items_by_character,
            graph.ambiguous_mention_items_by_character,
        ) = self._effective_mention_index(
            session,
            run=run,
            story=story,
            removed_mention_ids=graph.removed_entity_ids.get(
                "mentions",
                frozenset(),
            ),
            through_correction=through_correction,
        )
        graph.mention_index_loaded = True

    def _apply_character_mention_aggregates(
        self,
        *,
        item: dict[str, Any],
        mention_items_by_character: Mapping[
            str,
            tuple[dict[str, Any], ...],
        ],
        ambiguous_mention_items_by_character: Mapping[
            str,
            tuple[dict[str, Any], ...],
        ],
    ) -> dict[str, Any]:
        result = dict(item)
        character_ids = {
            character_id
            for field in ("entityId", "characterId", "registryCharacterId")
            if isinstance((character_id := result.get(field)), str)
        }
        ordered_mentions: list[dict[str, Any]] = []
        seen_mention_ids: set[str] = set()
        for character_id in character_ids:
            for mention in mention_items_by_character.get(character_id, ()):
                mention_id = mention.get("entityId")
                if not isinstance(mention_id, str) or mention_id in seen_mention_ids:
                    continue
                seen_mention_ids.add(mention_id)
                ordered_mentions.append(mention)
        ordered_mentions.sort(key=_mention_item_order_key)
        ambiguous_mentions: list[dict[str, Any]] = []
        seen_ambiguous_mention_ids: set[str] = set()
        for character_id in character_ids:
            for mention in ambiguous_mention_items_by_character.get(
                character_id,
                (),
            ):
                mention_id = mention.get("entityId")
                if (
                    not isinstance(mention_id, str)
                    or mention_id in seen_ambiguous_mention_ids
                ):
                    continue
                seen_ambiguous_mention_ids.add(mention_id)
                ambiguous_mentions.append(mention)
        ambiguous_mentions.sort(key=_mention_item_order_key)
        result["namedMentionIds"] = [
            str(mention["entityId"])
            for mention in ordered_mentions
            if mention.get("mentionKind") != "pronoun"
        ]
        result["ambiguousMentionIds"] = [
            str(mention["entityId"]) for mention in ambiguous_mentions
        ]
        result["mentionCount"] = len(ordered_mentions)
        first_item = ordered_mentions[0] if ordered_mentions else None
        last_item = ordered_mentions[-1] if ordered_mentions else None
        result["firstMentionId"] = (
            first_item.get("entityId") if first_item is not None else None
        )
        result["lastMentionId"] = (
            last_item.get("entityId") if last_item is not None else None
        )
        result["firstEvidence"] = (
            list(first_item.get("evidence", []))[:1]
            if first_item is not None
            else []
        )
        result["lastEvidence"] = (
            list(last_item.get("evidence", []))[:1]
            if last_item is not None
            else []
        )
        return self._refresh_structural_fingerprint(
            collection="characters",
            item=result,
        )

    def _apply_structure_graph_to_item(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        collection: str,
        item: dict[str, Any],
        graph: _EffectiveStructureGraph,
        through_correction: AnalysisCorrectionRow | None = None,
    ) -> dict[str, Any]:
        result = dict(item)
        scene_id = result.get("sceneId")
        if isinstance(scene_id, str):
            effective_chapter_id = graph.effective_scene_parents.get(scene_id)
            if effective_chapter_id is not None and "chapterId" in result:
                result["chapterId"] = effective_chapter_id
                narration = result.get("narration")
                if isinstance(narration, dict):
                    result["narration"] = {
                        **narration,
                        "chapterId": effective_chapter_id,
                    }

        removed_ids = {
            entity_id
            for values in graph.removed_entity_ids.values()
            for entity_id in values
        }
        if collection == "characters":
            self._ensure_structure_mention_index(
                session,
                run=run,
                story=story,
                graph=graph,
                through_correction=through_correction,
            )
            result = self._apply_character_mention_aggregates(
                item=result,
                mention_items_by_character=graph.mention_items_by_character,
                ambiguous_mention_items_by_character=(
                    graph.ambiguous_mention_items_by_character
                ),
            )
            aliases = []
            for alias in result.get("aliases", []):
                if not isinstance(alias, dict):
                    continue
                effective_range = alias.get("effectiveRange")
                if isinstance(effective_range, dict):
                    effective_range = {
                        key: (
                            None
                            if key in {"validFromEventId", "validThroughEventId"}
                            and isinstance(value, str)
                            and value in graph.removed_entity_ids.get(
                                "timeline-events",
                                frozenset(),
                            )
                            else value
                        )
                        for key, value in effective_range.items()
                    }
                    alias = {**alias, "effectiveRange": effective_range}
                aliases.append(alias)
            result["aliases"] = aliases
        elif collection == "locations":
            active_scene_ids = [
                scene_id
                for scene_id in result.get("sceneIds", [])
                if scene_id in graph.active_scene_ids
            ]
            result["sceneIds"] = active_scene_ids
            result["sceneAssignments"] = [
                assignment
                for assignment in result.get("sceneAssignments", [])
                if isinstance(assignment, dict)
                and assignment.get("sceneId") in graph.active_scene_ids
            ]
            result["sceneCount"] = len(active_scene_ids)
            result["firstSceneId"] = active_scene_ids[0] if active_scene_ids else None
            if result.get("parentLocationId") in graph.removed_entity_ids.get(
                "locations",
                frozenset(),
            ):
                result["parentLocationId"] = None
        elif collection == "timeline-events":
            if result.get("locationId") in graph.removed_entity_ids.get(
                "locations",
                frozenset(),
            ):
                result["locationId"] = None
        elif collection == "relationships":
            if result.get("previousRelationshipId") in graph.removed_entity_ids.get(
                "relationships",
                frozenset(),
            ):
                result["previousRelationshipId"] = None
            scope = result.get("scope")
            if isinstance(scope, dict) and isinstance(scene_id, str):
                removed_scene_ids = graph.removed_entity_ids.get(
                    "scenes",
                    frozenset(),
                )
                result["scope"] = {
                    **scope,
                    "firstSceneId": (
                        scene_id
                        if scope.get("firstSceneId") in removed_scene_ids
                        else scope.get("firstSceneId")
                    ),
                    "lastSceneId": (
                        scene_id
                        if scope.get("lastSceneId") in removed_scene_ids
                        else scope.get("lastSceneId")
                    ),
                }
            removed_event_ids = graph.removed_entity_ids.get(
                "timeline-events",
                frozenset(),
            )
            if result.get("validFromEventId") in removed_event_ids:
                result["validFromEventId"] = None
            if result.get("validThroughEventId") in removed_event_ids:
                result["validThroughEventId"] = None
        elif collection == "emotional-states":
            if result.get("previousEmotionalStateId") in graph.removed_entity_ids.get(
                "emotional-states",
                frozenset(),
            ):
                result["previousEmotionalStateId"] = None
        elif collection == "pov-segments":
            if result.get("shiftFromPovSegmentId") in graph.removed_entity_ids.get(
                "pov-segments",
                frozenset(),
            ):
                result["shiftFromPovSegmentId"] = None
        elif collection == "continuity-findings":
            related_ids = [
                entity_id
                for entity_id in result.get("relatedEntityIds", [])
                if entity_id not in removed_ids
            ]
            result["relatedEntityIds"] = related_ids
            if "count" in result:
                result["count"] = len(related_ids)

        return self._refresh_structural_fingerprint(
            collection=collection,
            item=result,
        )

    def _effective_collection_projection(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        collection: str,
        rows: list[AnalysisEntityRow],
        synthetic_start_ordinal: int | None = None,
    ) -> list[dict[str, Any]]:
        registry_effects = self._registry_effect_index(session, run=run)
        result: list[dict[str, Any]] = []
        split_sources: list[dict[str, Any]] = []
        next_ordinal = (
            synthetic_start_ordinal
            if synthetic_start_ordinal is not None
            else max((row.ordinal for row in rows), default=-1) + 1
        )
        for row in rows:
            item = self._entity_dict(
                session,
                run=run,
                entity=row,
                story=story,
                registry_effects=registry_effects,
            )
            if collection in {"chapters", "scenes"}:
                boundary = item.get("effectiveBoundary")
                operation = boundary.get("operation") if isinstance(boundary, dict) else None
                if operation == "remove":
                    continue
                if operation == "add":
                    original = self._entity_dict(
                        session,
                        run=run,
                        entity=row,
                        story=story,
                        registry_effects=registry_effects,
                        excluded_correction_categories=frozenset({"structure_boundary"}),
                    )
                    result.append(original)
                    result.append(
                        self._synthetic_structure_add(
                            run=run,
                            collection=collection,
                            original=original,
                            corrected=item,
                        )
                    )
                    continue
            result.append(item)
            if (
                collection == "characters"
                and isinstance(item.get("effectiveRegistry"), dict)
                and item["effectiveRegistry"].get("operation") == "split"
            ):
                split_sources.append(item)
        for source in split_sources:
            synthetic = self._synthetic_split_character(
                session,
                run=run,
                story=story,
                source=source,
                ordinal=next_ordinal,
                registry_effects=registry_effects,
            )
            if synthetic is not None:
                result.append(synthetic)
                next_ordinal += 1
        return sorted(
            result,
            key=lambda value: (int(value["ordinal"]), str(value["entityId"])),
        )

    def _agent_execution_dict(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        row: AnalysisAgentExecutionRow,
        snapshot: AnalysisSnapshotRow | None,
    ) -> dict[str, Any]:
        definition = next(value for value in AGENT_REGISTRY if value.role == row.role)
        stage_by_role = {
            "structure": "analyze_structure",
            "beats": "analyze_beats",
            "character_identity": "analyze_character_identity",
            "dialogue_attribution": "analyze_dialogue_attribution",
            "point_of_view": "analyze_point_of_view",
            "setting": "analyze_locations",
            "timeline": "analyze_timeline",
            "relationships": "analyze_relationships",
            "emotion_intent": "analyze_emotion_intent",
            "continuity": "analyze_continuity",
            "synthesis": "synthesize_analysis",
        }
        current_stage = stage_by_role[row.role]
        execution = session.get(AnalysisExecutionRow, row.execution_id)
        attempt = execution.attempt if execution is not None else 1
        checkpoint = session.scalar(
            select(AnalysisStageCheckpointRow)
            .where(
                AnalysisStageCheckpointRow.run_id == run.id,
                AnalysisStageCheckpointRow.attempt == attempt,
                AnalysisStageCheckpointRow.stage == current_stage,
            )
            .order_by(AnalysisStageCheckpointRow.id.desc())
            .limit(1)
        )
        warnings = [
            {
                "code": str(value.get("code") or "ANALYSIS_WARNING"),
                "severity": str(value.get("severity") or "warning"),
                "message": str(value.get("message") or "The analysis agent requires review."),
                "requiresHumanReview": bool(value.get("requiresHumanReview", True)),
                "relatedEntityIds": [],
                "evidence": [],
            }
            for value in parse_json(row.warnings_json, [])[:MAX_WARNINGS_PER_ENTITY]
            if isinstance(value, dict)
        ]
        lifecycle = parse_json(row.envelope_json, {})
        lifecycle_status = lifecycle.get("status") if isinstance(lifecycle, dict) else None
        status = (
            str(lifecycle_status)
            if lifecycle_status
            in {
                "queued",
                "running",
                "succeeded",
                "partial",
                "failed",
                "cancelled",
                "interrupted",
            }
            else row.outcome
            if row.outcome in {"succeeded", "failed", "cancelled", "interrupted"}
            else "partial"
        )
        progress = float(lifecycle.get("progress", 0)) if isinstance(lifecycle, dict) else 0
        failure = lifecycle.get("failure") if isinstance(lifecycle, dict) else None
        result: dict[str, Any] = {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "executionId": row.id,
            "runId": row.run_id,
            "ordinal": row.ordinal,
            "agentId": row.agent_id,
            "agentVersion": row.agent_version,
            "status": status,
            "attempt": attempt,
            "progress": 1 if status == "succeeded" else progress,
            "currentStage": current_stage,
            "checkpoint": (
                {
                    "checkpointId": checkpoint.id,
                    "checkpointFingerprint": checkpoint.payload_fingerprint,
                    "stage": checkpoint.stage,
                    "schemaVersion": ANALYSIS_CONTRACT_VERSION,
                    "recordedAt": checkpoint.created_at,
                }
                if checkpoint is not None
                else None
            ),
            "retryClassification": (
                "not_retryable" if status in {"succeeded", "cancelled"} else "retryable"
            ),
            "retryPolicy": {
                "maxAttempts": 3,
                "retryableFailureCodes": ["ANALYSIS_FAILED"],
            },
            "failurePolicy": "fail_closed_without_partial_publication",
            "inputFingerprint": row.input_fingerprint,
            "outputCollections": list(definition.collections),
            "confidence": _wire_confidence(parse_json(row.confidence_json, {})),
            "warnings": warnings,
            "provenance": {
                "origin": "runtime_agent",
                "recordedAt": (
                    row.finished_at
                    if status
                    in {
                        "succeeded",
                        "partial",
                        "failed",
                        "cancelled",
                        "interrupted",
                    }
                    else row.started_at
                ),
                "inputFingerprint": row.input_fingerprint,
                "agentExecutionId": row.id,
                "agentId": row.agent_id,
                "agentVersion": row.agent_version,
                "deterministic": True,
            },
            "startedAt": row.started_at,
            "failure": failure,
        }
        if snapshot is not None:
            result["snapshotId"] = snapshot.id
        if status == "succeeded":
            result["outputFingerprint"] = row.output_fingerprint
        if status in {
            "succeeded",
            "partial",
            "failed",
            "cancelled",
            "interrupted",
        }:
            result["finishedAt"] = row.finished_at
        return result

    def list_entities(
        self,
        *,
        project_id: str,
        run_id: str,
        collection: str,
        cursor: str | None,
        limit: int = DEFAULT_ANALYSIS_PAGE_SIZE,
        confidence_max: float | None = None,
        requires_review: bool | None = None,
        speaker_state: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        if collection not in {*ENTITY_COLLECTIONS, "agent-executions"}:
            raise not_found("analysis collection")
        if not 1 <= limit <= MAX_ANALYSIS_PAGE_SIZE:
            raise ServiceError(422, "INVALID_PAGE_SIZE", "The page size is invalid.")
        if confidence_max is not None and not 0 <= confidence_max <= 1:
            raise ServiceError(
                422,
                "ANALYSIS_FILTER_INVALID",
                "The confidence filter is invalid.",
            )
        if speaker_state is not None and (
            collection != "dialogue-lines"
            or speaker_state not in {"unknown", "ambiguous", "proposed", "corrected"}
        ):
            raise ServiceError(
                422,
                "ANALYSIS_FILTER_INVALID",
                "The speaker-state filter is only valid for dialogue lines.",
            )
        if collection == "agent-executions" and (
            confidence_max is not None or requires_review is not None
        ):
            raise ServiceError(
                422,
                "ANALYSIS_FILTER_INVALID",
                "Entity filters are not valid for agent executions.",
            )
        with self.database.session() as session:
            run = self.require_run(session, project_id=project_id, run_id=run_id)
            story = session.get(ImportedStoryRow, run.story_id)
            if story is None:
                raise ServiceError(500, "STORY_UNAVAILABLE", "The imported story is unavailable.")
            snapshot = self._latest_snapshot(session, run.id)
            snapshot_binding = (
                {
                    "snapshotId": snapshot.id,
                    "snapshotFingerprint": snapshot.fingerprint,
                }
                if snapshot is not None
                else {"snapshotId": None, "snapshotFingerprint": None}
            )
            binding = _cursor_binding(
                {
                    "projectId": project_id,
                    "runId": run_id,
                    **snapshot_binding,
                    "collection": collection,
                    "confidenceMax": confidence_max,
                    "requiresReview": requires_review,
                    "speakerState": speaker_state,
                    "order": "ordinal,id",
                    "correctionSetFingerprint": self.correction_set_fingerprint(
                        session,
                        project_id=project_id,
                    ),
                }
            )
            if collection == "agent-executions":
                after_ordinal = _decode_cursor(binding, cursor)
                latest = self._latest_execution(session, run.id)
                if latest is None:
                    return [], None, 0
                statement = (
                    select(AnalysisAgentExecutionRow)
                    .where(
                        AnalysisAgentExecutionRow.run_id == run.id,
                        AnalysisAgentExecutionRow.execution_id == latest.id,
                        AnalysisAgentExecutionRow.ordinal > after_ordinal,
                    )
                    .order_by(
                        AnalysisAgentExecutionRow.ordinal,
                        AnalysisAgentExecutionRow.id,
                    )
                    .limit(limit + 1)
                )
                agent_rows = list(session.scalars(statement))
                total = int(
                    session.scalar(
                        select(func.count())
                        .select_from(AnalysisAgentExecutionRow)
                        .where(
                            AnalysisAgentExecutionRow.run_id == run.id,
                            AnalysisAgentExecutionRow.execution_id == latest.id,
                        )
                    )
                    or 0
                )
                agent_page, extra = agent_rows[:limit], len(agent_rows) > limit
                items = [
                    self._agent_execution_dict(
                        session,
                        run=run,
                        row=row,
                        snapshot=snapshot,
                    )
                    for row in agent_page
                ]
                next_cursor = (
                    _encode_cursor(binding, agent_page[-1].ordinal)
                    if agent_page and extra
                    else None
                )
                return items, next_cursor, total

            after = _decode_key_cursor(binding, cursor)
            after_ordinal = -1
            after_entity_id = ""
            if after is not None:
                raw_after_ordinal, after_entity_id = after
                try:
                    after_ordinal = int(raw_after_ordinal)
                except ValueError as exc:
                    raise ServiceError(
                        400,
                        "INVALID_CURSOR",
                        "The analysis cursor is invalid.",
                    ) from exc
                if after_ordinal < 0:
                    raise ServiceError(
                        400,
                        "INVALID_CURSOR",
                        "The analysis cursor is invalid.",
                    )

            base_conditions = [
                AnalysisEntityRow.run_id == run.id,
                AnalysisEntityRow.collection == collection,
            ]
            if confidence_max is not None:
                base_conditions.append(
                    AnalysisEntityRow.confidence_score <= round(confidence_max * 1_000_000)
                )
            if requires_review is not None:
                base_conditions.append(
                    or_(
                        AnalysisEntityRow.payload_json.contains('"requiresHumanReview":true'),
                        AnalysisEntityRow.warnings_json != "[]",
                    )
                    if requires_review
                    else (
                        ~AnalysisEntityRow.payload_json.contains('"requiresHumanReview":true')
                        & (AnalysisEntityRow.warnings_json == "[]")
                    )
                )
            if speaker_state is not None:
                compatible_prior_run_ids = select(AnalysisRunRow.id).where(
                    AnalysisRunRow.project_id == run.project_id,
                    AnalysisRunRow.story_id == run.story_id,
                    AnalysisRunRow.story_revision == run.story_revision,
                    AnalysisRunRow.input_fingerprint == run.input_fingerprint,
                    AnalysisRunRow.extracted_text_sha256 == run.extracted_text_sha256,
                    AnalysisRunRow.profile_fingerprint == run.profile_fingerprint,
                    AnalysisRunRow.source_document_id == run.source_document_id,
                    AnalysisRunRow.source_revision == run.source_revision,
                    AnalysisRunRow.extraction_id == run.extraction_id,
                    AnalysisRunRow.extraction_revision == run.extraction_revision,
                    AnalysisRunRow.approval_evidence_fingerprint
                    == run.approval_evidence_fingerprint,
                )
                corrected_speaker_exists = (
                    select(AnalysisCorrectionRow.id)
                    .where(
                        AnalysisCorrectionRow.project_id == project_id,
                        AnalysisCorrectionRow.category == "dialogue_speaker",
                        AnalysisCorrectionRow.target_key
                        == ("dialogue-lines:" + AnalysisEntityRow.identity_key),
                        or_(
                            AnalysisCorrectionRow.run_id == run.id,
                            and_(
                                AnalysisCorrectionRow.run_id.in_(
                                    compatible_prior_run_ids
                                ),
                                AnalysisCorrectionRow.recorded_at <= run.created_at,
                            ),
                            and_(
                                AnalysisCorrectionRow.run_id.is_(None),
                                AnalysisCorrectionRow.expected_run_fingerprint
                                == run.input_fingerprint,
                            ),
                        ),
                    )
                    .exists()
                )
                base_conditions.append(
                    corrected_speaker_exists
                    if speaker_state == "corrected"
                    else (
                        AnalysisEntityRow.payload_json.contains(f'"speakerState":"{speaker_state}"')
                        & ~corrected_speaker_exists
                    )
                )
            candidate_structure_graph = self._effective_structure_graph(
                session,
                run=run,
                story=story,
            )
            structure_graph: _EffectiveStructureGraph | None = (
                candidate_structure_graph
                if (
                    candidate_structure_graph.chapter_target_ids
                    or candidate_structure_graph.scene_target_ids
                )
                else None
            )
            standalone_resolved_mentions: (
                dict[str, tuple[dict[str, Any], ...]] | None
            ) = None
            standalone_ambiguous_mentions: (
                dict[str, tuple[dict[str, Any], ...]] | None
            ) = None
            if collection == "characters" and structure_graph is None:
                registry_aggregate_correction_exists = session.scalar(
                    select(AnalysisCorrectionRow.id)
                    .where(
                        AnalysisCorrectionRow.project_id == run.project_id,
                        AnalysisCorrectionRow.category.in_(
                            {
                                "character_merge",
                                "character_split",
                                "mention_resolution",
                            }
                        ),
                        self._correction_scope_condition(run),
                    )
                    .limit(1)
                )
                if registry_aggregate_correction_exists is not None:
                    (
                        standalone_resolved_mentions,
                        standalone_ambiguous_mentions,
                    ) = self._effective_mention_index(
                        session,
                        run=run,
                        story=story,
                    )
            if (
                structure_graph is not None
                and collection not in {"chapters", "scenes"}
            ):
                removed_collection_ids = structure_graph.removed_entity_ids.get(
                    collection,
                    frozenset(),
                )
                for offset in range(0, len(removed_collection_ids), 400):
                    removed_page = sorted(removed_collection_ids)[offset : offset + 400]
                    base_conditions.append(
                        ~AnalysisEntityRow.id.in_(removed_page)
                    )
            if collection == "beats" and structure_graph is not None:
                if structure_graph.removed_scene_ids:
                    base_conditions.append(
                        ~AnalysisEntityRow.parent_entity_id.in_(structure_graph.removed_scene_ids)
                    )
                if structure_graph.removed_chapter_ids:
                    moved_out_scene_ids = [
                        scene_id
                        for scene_id, parent_id in (structure_graph.effective_scene_parents.items())
                        if parent_id is not None
                        and parent_id not in structure_graph.removed_chapter_ids
                    ]
                    removed_chapter_scenes = select(AnalysisEntityRow.id).where(
                        AnalysisEntityRow.run_id == run.id,
                        AnalysisEntityRow.collection == "scenes",
                        AnalysisEntityRow.parent_entity_id.in_(structure_graph.removed_chapter_ids),
                    )
                    if moved_out_scene_ids:
                        removed_chapter_scenes = removed_chapter_scenes.where(
                            ~AnalysisEntityRow.id.in_(moved_out_scene_ids)
                        )
                    base_conditions.append(
                        ~AnalysisEntityRow.parent_entity_id.in_(removed_chapter_scenes)
                    )
            projection_category = (
                "structure_boundary"
                if collection in {"chapters", "scenes"}
                else "character_split"
                if collection == "characters"
                else None
            )
            if projection_category is not None:
                projection_rows, projection_target_ids = self._bounded_projection_rows(
                    session,
                    run=run,
                    collection=collection,
                    category=projection_category,
                    base_conditions=base_conditions,
                )
                if structure_graph is not None and collection in {"chapters", "scenes"}:
                    filtered_source_ids = {row.id for row in projection_rows}
                    projection_target_ids = list(
                        structure_graph.chapter_target_ids
                        if collection == "chapters"
                        else structure_graph.scene_target_ids
                    )
                    items_by_source = (
                        structure_graph.chapter_items_by_source
                        if collection == "chapters"
                        else structure_graph.scene_items_by_source
                    )
                    projected_items = [
                        item
                        for source_id, source_items in (items_by_source.items())
                        if source_id in filtered_source_ids
                        for item in source_items
                    ]
                if projection_target_ids:
                    if not (structure_graph is not None and collection in {"chapters", "scenes"}):
                        maximum_ordinal = session.scalar(
                            select(func.max(AnalysisEntityRow.ordinal)).where(
                                AnalysisEntityRow.run_id == run.id,
                                AnalysisEntityRow.collection == collection,
                            )
                        )
                        next_synthetic_ordinal = (
                            int(maximum_ordinal) if maximum_ordinal is not None else -1
                        ) + 1
                        projected_items = self._effective_collection_projection(
                            session,
                            run=run,
                            story=story,
                            collection=collection,
                            rows=projection_rows,
                            synthetic_start_ordinal=(next_synthetic_ordinal),
                        )
                    projected_after = [
                        item
                        for item in projected_items
                        if (
                            int(item["ordinal"]) > after_ordinal
                            or (
                                int(item["ordinal"]) == after_ordinal
                                and str(item["entityId"]) > after_entity_id
                            )
                        )
                    ]
                    normal_base_conditions = list(base_conditions)
                    for offset in range(
                        0,
                        len(projection_target_ids),
                        400,
                    ):
                        normal_base_conditions.append(
                            ~AnalysisEntityRow.id.in_(projection_target_ids[offset : offset + 400])
                        )
                    if (
                        collection == "scenes"
                        and structure_graph is not None
                        and structure_graph.removed_chapter_ids
                    ):
                        normal_base_conditions.append(
                            ~AnalysisEntityRow.parent_entity_id.in_(
                                structure_graph.removed_chapter_ids
                            )
                        )
                    normal_conditions = list(normal_base_conditions)
                    if after is not None:
                        normal_conditions.append(
                            or_(
                                AnalysisEntityRow.ordinal > after_ordinal,
                                and_(
                                    AnalysisEntityRow.ordinal == after_ordinal,
                                    AnalysisEntityRow.id > after_entity_id,
                                ),
                            )
                        )
                    normal_rows = list(
                        session.scalars(
                            select(AnalysisEntityRow)
                            .where(*normal_conditions)
                            .order_by(
                                AnalysisEntityRow.ordinal,
                                AnalysisEntityRow.id,
                            )
                            .limit(limit + 1)
                        )
                    )
                    total = int(
                        session.scalar(
                            select(func.count())
                            .select_from(AnalysisEntityRow)
                            .where(*normal_base_conditions)
                        )
                        or 0
                    ) + len(projected_items)
                    registry_effects = (
                        self._registry_effect_index(session, run=run)
                        if collection != "characters"
                        else None
                    )
                    normal_items = [
                        self._entity_dict(
                            session,
                            run=run,
                            entity=row,
                            story=story,
                            registry_effects=registry_effects,
                        )
                        for row in normal_rows
                    ]
                    candidates = sorted(
                        [*projected_after, *normal_items],
                        key=lambda value: (
                            int(value["ordinal"]),
                            str(value["entityId"]),
                        ),
                    )
                    page_items = candidates[:limit]
                    if structure_graph is not None:
                        if collection == "chapters":
                            page_items = [
                                self._apply_chapter_scene_aggregates(
                                    session,
                                    run=run,
                                    graph=structure_graph,
                                    item=item,
                                )
                                for item in page_items
                            ]
                        elif collection == "scenes":
                            page_items = [
                                self._apply_scene_beat_aggregates(
                                    session,
                                    run=run,
                                    item=item,
                                )
                                for item in page_items
                            ]
                        else:
                            page_items = [
                                self._apply_structure_graph_to_item(
                                    session,
                                    run=run,
                                    story=story,
                                    collection=collection,
                                    item=item,
                                    graph=structure_graph,
                                )
                                for item in page_items
                            ]
                    elif (
                        collection == "characters"
                        and standalone_resolved_mentions is not None
                        and standalone_ambiguous_mentions is not None
                    ):
                        page_items = [
                            self._apply_character_mention_aggregates(
                                item=item,
                                mention_items_by_character=(
                                    standalone_resolved_mentions
                                ),
                                ambiguous_mention_items_by_character=(
                                    standalone_ambiguous_mentions
                                ),
                            )
                            for item in page_items
                        ]
                    extra = len(candidates) > limit
                    next_cursor = (
                        _encode_key_cursor(
                            binding,
                            recorded_at=str(page_items[-1]["ordinal"]),
                            entity_id=str(page_items[-1]["entityId"]),
                        )
                        if page_items and extra
                        else None
                    )
                    return page_items, next_cursor, total

            if (
                collection == "scenes"
                and structure_graph is not None
                and structure_graph.removed_chapter_ids
                and not structure_graph.scene_target_ids
            ):
                base_conditions.append(
                    ~AnalysisEntityRow.parent_entity_id.in_(structure_graph.removed_chapter_ids)
                )
            conditions = list(base_conditions)
            if after is not None:
                conditions.append(
                    or_(
                        AnalysisEntityRow.ordinal > after_ordinal,
                        and_(
                            AnalysisEntityRow.ordinal == after_ordinal,
                            AnalysisEntityRow.id > after_entity_id,
                        ),
                    )
                )
            rows = list(
                session.scalars(
                    select(AnalysisEntityRow)
                    .where(*conditions)
                    .order_by(AnalysisEntityRow.ordinal, AnalysisEntityRow.id)
                    .limit(limit + 1)
                )
            )
            total = int(
                session.scalar(
                    select(func.count()).select_from(AnalysisEntityRow).where(*base_conditions)
                )
                or 0
            )
            page, extra = rows[:limit], len(rows) > limit
            registry_effects = (
                self._registry_effect_index(session, run=run)
                if collection != "characters"
                else None
            )
            items = [
                self._entity_dict(
                    session,
                    run=run,
                    entity=row,
                    story=story,
                    registry_effects=registry_effects,
                )
                for row in page
            ]
            if (
                collection == "characters"
                and standalone_resolved_mentions is not None
                and standalone_ambiguous_mentions is not None
            ):
                items = [
                    self._apply_character_mention_aggregates(
                        item=item,
                        mention_items_by_character=standalone_resolved_mentions,
                        ambiguous_mention_items_by_character=(
                            standalone_ambiguous_mentions
                        ),
                    )
                    for item in items
                ]
            if (
                structure_graph is not None
                and collection not in {"chapters", "scenes", "beats"}
            ):
                items = [
                    self._apply_structure_graph_to_item(
                        session,
                        run=run,
                        story=story,
                        collection=collection,
                        item=item,
                        graph=structure_graph,
                    )
                    for item in items
                ]
            if (
                collection == "chapters"
                and structure_graph is not None
                and (structure_graph.chapter_target_ids or structure_graph.scene_target_ids)
            ):
                items = [
                    self._apply_chapter_scene_aggregates(
                        session,
                        run=run,
                        graph=structure_graph,
                        item=item,
                    )
                    for item in items
                ]
            if collection == "beats" and structure_graph is not None:
                items = [
                    self._apply_structure_graph_to_item(
                        session,
                        run=run,
                        story=story,
                        collection=collection,
                        item=item,
                        graph=structure_graph,
                    )
                    for item in items
                ]
            next_cursor = (
                _encode_key_cursor(
                    binding,
                    recorded_at=str(page[-1].ordinal),
                    entity_id=page[-1].id,
                )
                if page and extra
                else None
            )
            return items, next_cursor, total

    def _correction_dict(
        self,
        session: Session,
        row: AnalysisCorrectionRow,
        *,
        projection_run: AnalysisRunRow | None = None,
    ) -> dict[str, Any]:
        run = projection_run or (
            session.get(AnalysisRunRow, row.run_id) if row.run_id is not None else None
        )
        entity = (
            session.get(AnalysisEntityRow, row.target_entity_id)
            if row.target_entity_id is not None
            else None
        )
        if run is not None and (entity is None or entity.run_id != run.id):
            collection, separator, identity_key = row.target_key.partition(":")
            if separator:
                entity = session.scalar(
                    select(AnalysisEntityRow)
                    .where(
                        AnalysisEntityRow.run_id == run.id,
                        AnalysisEntityRow.collection == collection,
                        AnalysisEntityRow.identity_key == identity_key,
                    )
                    .limit(1)
                )
        if run is None or entity is None or entity.run_id != run.id or row.reason is None:
            raise ServiceError(
                500,
                "CORRECTION_PROJECTION_INVALID",
                "The protected correction target is unavailable.",
            )
        story = session.get(ImportedStoryRow, run.story_id)
        if story is None:
            raise ServiceError(
                500,
                "STORY_UNAVAILABLE",
                "The imported story is unavailable.",
            )
        payload = parse_json(entity.payload_json, {})
        patch = self._correction_patch_for_run(
            session,
            correction=row,
            run=run,
        )
        if patch is None:
            raise ServiceError(
                500,
                "CORRECTION_REMAP_REQUIRED",
                "The protected correction requires an explicit remap.",
            )
        correction_chain = self._applicable_corrections(
            session,
            project_id=row.project_id,
            target_key=row.target_key,
            run=run,
            through_revision=row.revision,
        )
        for correction_value, effective_patch in correction_chain:
            payload = _apply_correction_patch(
                payload,
                correction_value,
                effective_patch=effective_patch,
            )
        wire_payload = _wire_entity_payload(
            collection=entity.collection,
            payload=payload,
            run=run,
            story=story,
            start=entity.start_offset,
            end=entity.end_offset,
            correction=row,
        )
        projected_target_entity_id = (
            row.target_entity_id
            if row.run_id == run.id and row.target_entity_id is not None
            else entity.id
        )
        corrected_value_fingerprint = request_fingerprint(wire_payload)
        if (
            row.category in {"structure_boundary", "structure_label"}
            and entity.collection in {"chapters", "scenes"}
        ):
            boundary_operations = self._structure_operations(
                session,
                run=run,
                entity=entity,
                through_correction=row,
            )
            boundary_state = self._structure_boundary_state(
                session,
                row=entity,
                operations=boundary_operations,
            )
            if (
                boundary_state.branch_by_correction_id.get(row.id) == "synthetic"
                and boundary_state.synthetic_root is not None
            ):
                projected_target_entity_id = stable_id(
                    run.id,
                    "effective-structure-add",
                    entity.collection,
                    boundary_state.synthetic_root.id,
                )
            corrected_value_fingerprint = self._effective_structural_entity(
                session,
                run=run,
                story=story,
                entity=entity,
                effective_entity_id=projected_target_entity_id,
                through_correction=row,
            )["effectiveValueFingerprint"]
        else:
            corrected_item = self._entity_dict(
                session,
                run=run,
                entity=entity,
                story=story,
                through_correction=row,
            )
            structure_graph = self._effective_structure_graph(
                session,
                run=run,
                story=story,
                through_correction=row,
            )
            if (
                structure_graph.chapter_target_ids
                or structure_graph.scene_target_ids
            ):
                corrected_item = self._apply_structure_graph_to_item(
                    session,
                    run=run,
                    story=story,
                    collection=entity.collection,
                    item=corrected_item,
                    graph=structure_graph,
                    through_correction=row,
                )
            elif entity.collection == "characters":
                (
                    resolved_mentions,
                    ambiguous_mentions,
                ) = self._effective_mention_index(
                    session,
                    run=run,
                    story=story,
                    through_correction=row,
                )
                corrected_item = self._apply_character_mention_aggregates(
                    item=corrected_item,
                    mention_items_by_character=resolved_mentions,
                    ambiguous_mention_items_by_character=ambiguous_mentions,
                )
            corrected_value_fingerprint = corrected_item[
                "effectiveValueFingerprint"
            ]
        return {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "correctionId": row.id,
            "projectId": row.project_id,
            "runId": run.id,
            "snapshotId": entity.snapshot_id,
            "category": row.category,
            "targetCollection": entity.collection,
            "targetEntityId": projected_target_entity_id,
            "expectedTargetRevision": row.expected_target_revision,
            "expectedRunFingerprint": row.expected_run_fingerprint,
            "previousValueFingerprint": row.previous_value_fingerprint,
            "correctedValueFingerprint": corrected_value_fingerprint,
            "patch": patch,
            "actor": {
                "classification": "human",
                "actorId": row.actor_id,
            },
            "recordedAt": row.recorded_at,
            "immutable": True,
            "lockedAgainstAutomation": True,
            "idempotencyFingerprint": request_fingerprint(
                {
                    "projectId": row.project_id,
                    "runId": run.id,
                    "idempotencyKey": row.idempotency_key,
                    "category": row.category,
                    "targetCollection": entity.collection,
                    "targetEntityId": projected_target_entity_id,
                    "expectedTargetRevision": row.expected_target_revision,
                    "expectedRunFingerprint": row.expected_run_fingerprint,
                    "previousValueFingerprint": row.previous_value_fingerprint,
                    "patch": patch,
                    "reason": row.reason,
                    "supersedesCorrectionId": row.supersedes_correction_id,
                }
            ),
            "reason": row.reason,
            **(
                {"supersedesCorrectionId": row.supersedes_correction_id}
                if row.supersedes_correction_id is not None
                else {}
            ),
        }

    @staticmethod
    def _correction_invalidated_gate_ids(
        session: Session,
        *,
        correction: AnalysisCorrectionRow,
    ) -> list[str]:
        invalidated: set[str] = set()
        for row in session.scalars(
            select(AnalysisReviewDecisionRow).where(
                AnalysisReviewDecisionRow.run_id == correction.run_id,
                AnalysisReviewDecisionRow.state == "invalidated",
                AnalysisReviewDecisionRow.provenance_json.contains(correction.id),
            )
        ):
            provenance_value = parse_json(row.provenance_json, {})
            if (
                isinstance(provenance_value, dict)
                and provenance_value.get("correctionId") == correction.id
            ):
                invalidated.add(row.gate_id)
        return [gate_id for gate_id in REVIEW_GATES if gate_id in invalidated]

    def list_corrections(
        self,
        *,
        project_id: str,
        run_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        if not 1 <= limit <= MAX_ANALYSIS_PAGE_SIZE:
            raise ServiceError(422, "INVALID_PAGE_SIZE", "The page size is invalid.")
        with self.database.session() as session:
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            snapshot = self._latest_snapshot(session, run_id)
            current_target_keys = select(
                AnalysisEntityRow.collection + ":" + AnalysisEntityRow.identity_key
            ).where(AnalysisEntityRow.run_id == run.id)
            compatible_prior_run_ids = select(AnalysisRunRow.id).where(
                AnalysisRunRow.project_id == run.project_id,
                AnalysisRunRow.id != run.id,
                AnalysisRunRow.story_id == run.story_id,
                AnalysisRunRow.story_revision == run.story_revision,
                AnalysisRunRow.input_fingerprint == run.input_fingerprint,
                AnalysisRunRow.extracted_text_sha256 == run.extracted_text_sha256,
                AnalysisRunRow.profile_fingerprint == run.profile_fingerprint,
                AnalysisRunRow.source_document_id == run.source_document_id,
                AnalysisRunRow.source_revision == run.source_revision,
                AnalysisRunRow.extraction_id == run.extraction_id,
                AnalysisRunRow.extraction_revision == run.extraction_revision,
                AnalysisRunRow.approval_evidence_fingerprint == run.approval_evidence_fingerprint,
            )
            scope_condition = or_(
                AnalysisCorrectionRow.run_id == run.id,
                and_(
                    AnalysisCorrectionRow.run_id.in_(compatible_prior_run_ids),
                    AnalysisCorrectionRow.recorded_at <= run.created_at,
                    AnalysisCorrectionRow.target_key.in_(current_target_keys),
                ),
                and_(
                    AnalysisCorrectionRow.run_id.is_(None),
                    AnalysisCorrectionRow.category == "dialogue_speaker",
                    AnalysisCorrectionRow.expected_run_fingerprint == run.input_fingerprint,
                    AnalysisCorrectionRow.target_key.in_(current_target_keys),
                ),
            )
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(AnalysisCorrectionRow)
                    .where(
                        AnalysisCorrectionRow.project_id == project_id,
                        scope_condition,
                    )
                )
                or 0
            )
            latest = session.scalar(
                select(AnalysisCorrectionRow)
                .where(
                    AnalysisCorrectionRow.project_id == project_id,
                    scope_condition,
                )
                .order_by(
                    AnalysisCorrectionRow.recorded_at.desc(),
                    AnalysisCorrectionRow.id.desc(),
                )
                .limit(1)
            )
            binding = _cursor_binding(
                {
                    "projectId": project_id,
                    "runId": run_id,
                    "snapshotId": snapshot.id if snapshot is not None else None,
                    "snapshotFingerprint": (snapshot.fingerprint if snapshot is not None else None),
                    "collection": "corrections",
                    "order": "recorded_at,id",
                    "total": total,
                    "latestRecordedAt": (latest.recorded_at if latest is not None else None),
                    "latestId": latest.id if latest is not None else None,
                    "correctionSetFingerprint": self.correction_set_fingerprint(
                        session,
                        project_id=project_id,
                    ),
                }
            )
            after = _decode_key_cursor(binding, cursor)
            conditions = [
                AnalysisCorrectionRow.project_id == project_id,
                scope_condition,
            ]
            if after is not None:
                after_recorded_at, after_id = after
                conditions.append(
                    or_(
                        AnalysisCorrectionRow.recorded_at > after_recorded_at,
                        and_(
                            AnalysisCorrectionRow.recorded_at == after_recorded_at,
                            AnalysisCorrectionRow.id > after_id,
                        ),
                    )
                )
            rows = list(
                session.scalars(
                    select(AnalysisCorrectionRow)
                    .where(*conditions)
                    .order_by(
                        AnalysisCorrectionRow.recorded_at,
                        AnalysisCorrectionRow.id,
                    )
                    .limit(limit + 1)
                )
            )
            page, extra = rows[:limit], len(rows) > limit
            next_cursor = (
                _encode_key_cursor(
                    binding,
                    recorded_at=page[-1].recorded_at,
                    entity_id=page[-1].id,
                )
                if page and extra
                else None
            )
            return (
                [
                    self._correction_dict(
                        session,
                        row,
                        projection_run=run,
                    )
                    for row in page
                ],
                next_cursor,
                total,
            )

    def _require_same_run_ids(
        self,
        session: Session,
        *,
        run_id: str,
        collection: str,
        entity_ids: list[str],
    ) -> None:
        if not entity_ids:
            return
        found = set(
            session.scalars(
                select(AnalysisEntityRow.id).where(
                    AnalysisEntityRow.run_id == run_id,
                    AnalysisEntityRow.collection == collection,
                    AnalysisEntityRow.id.in_(entity_ids),
                )
            )
        )
        if collection in {"chapters", "scenes", "characters"}:
            run = session.get(AnalysisRunRow, run_id)
            if run is None:
                raise _correction_patch_invalid()
            entities = list(
                session.scalars(
                    select(AnalysisEntityRow).where(
                        AnalysisEntityRow.run_id == run.id,
                        AnalysisEntityRow.collection == collection,
                    )
                )
            )
            for entity in entities:
                target_key = f"{collection}:{entity.identity_key}"
                applicable = self._applicable_corrections(
                    session,
                    project_id=run.project_id,
                    target_key=target_key,
                    run=run,
                )
                if collection in {"chapters", "scenes"}:
                    boundary = next(
                        (
                            (correction, patch)
                            for correction, patch in reversed(applicable)
                            if correction.category == "structure_boundary"
                        ),
                        None,
                    )
                    if boundary is None:
                        continue
                    correction, patch = boundary
                    if patch.get("operation") == "remove":
                        found.discard(entity.id)
                    elif patch.get("operation") == "add":
                        found.add(
                            stable_id(
                                run.id,
                                "effective-structure-add",
                                collection,
                                correction.id,
                            )
                        )
                else:
                    for correction, patch in applicable:
                        if correction.category == "character_split" and isinstance(
                            patch.get("newRegistryCharacterId"),
                            str,
                        ):
                            found.add(str(patch["newRegistryCharacterId"]))
        if found != set(entity_ids):
            raise _correction_patch_invalid()

    def _validate_correction_patch(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        entity: AnalysisEntityRow,
        category: str,
        target_collection: str,
        patch: dict[str, Any],
    ) -> None:
        allowed_collections = _CORRECTION_COLLECTION.get(category)
        if allowed_collections is None or target_collection not in allowed_collections:
            raise _correction_patch_invalid()
        if category == "structure_boundary":
            keys = {"operation", "parentEntityId", "ordinal", "sourceSpan"}
            if target_collection == "scenes":
                keys.add("boundaryKind")
            _require_patch_shape(
                patch,
                keys=keys,
                predicates={
                    "operation": patch.get("operation") in {"add", "remove", "move"},
                    "parent": _valid_id(patch.get("parentEntityId")),
                    "ordinal": isinstance(patch.get("ordinal"), int)
                    and not isinstance(patch.get("ordinal"), bool)
                    and 0 <= int(patch["ordinal"]) <= 10_000_000,
                    "span": _valid_source_span(
                        patch.get("sourceSpan"),
                        run=run,
                        story=story,
                    ),
                    "boundaryKind": (
                        target_collection != "scenes"
                        or patch.get("boundaryKind")
                        in {
                            "chapter_start",
                            "explicit_scene_break",
                            "heading",
                            "inferred",
                        }
                    ),
                },
            )
            parent_collection = "chapters" if target_collection == "scenes" else ""
            if parent_collection:
                self._require_same_run_ids(
                    session,
                    run_id=run.id,
                    collection=parent_collection,
                    entity_ids=[str(patch["parentEntityId"])],
                )
            elif patch["parentEntityId"] != run.story_id:
                raise _correction_patch_invalid()
        elif category == "structure_label":
            field = "title" if target_collection == "chapters" else "heading"
            _require_patch_shape(
                patch,
                keys={field},
                predicates={
                    field: _valid_string(
                        patch.get(field),
                        maximum=500,
                        nullable=True,
                    )
                },
            )
        elif category == "character_identity":
            _require_patch_shape(
                patch,
                keys={
                    "canonicalName",
                    "normalizedCanonicalName",
                    "identityStatus",
                },
                predicates={
                    "canonicalName": _valid_string(
                        patch.get("canonicalName"),
                        maximum=240,
                    ),
                    "normalizedCanonicalName": _valid_string(
                        patch.get("normalizedCanonicalName"),
                        maximum=240,
                    )
                    and patch.get("normalizedCanonicalName")
                    == " ".join(str(patch.get("canonicalName", "")).split()).casefold(),
                    "identityStatus": patch.get("identityStatus")
                    in {"resolved", "ambiguous", "unresolved", "unknown"},
                },
            )
        elif category == "character_alias":
            operation = patch.get("operation")
            if operation == "remove":
                _require_patch_shape(
                    patch,
                    keys={"operation", "aliasId"},
                    predicates={
                        "operation": True,
                        "aliasId": _valid_id(patch.get("aliasId")),
                    },
                )
            elif operation in {"add", "replace"}:
                alias = patch.get("alias")
                if not isinstance(alias, dict):
                    raise _correction_patch_invalid()
                _require_patch_shape(
                    patch,
                    keys={"operation", "alias"},
                    predicates={"operation": True, "alias": True},
                )
                required_alias_keys = {
                    "aliasId",
                    "characterId",
                    "alias",
                    "normalizedAlias",
                    "kind",
                    "ambiguous",
                    "effectiveRange",
                    "change",
                    "confidence",
                    "evidence",
                }
                if (
                    set(alias) - {"previousAliasId"} != required_alias_keys
                    or alias.get("characterId") != entity.id
                    or not _valid_id(alias.get("aliasId"))
                    or not _valid_string(alias.get("alias"), maximum=240)
                    or alias.get("normalizedAlias")
                    != " ".join(str(alias.get("alias", "")).split()).casefold()
                    or alias.get("kind")
                    not in {
                        "full_name",
                        "given_name",
                        "family_name",
                        "nickname",
                        "honorific",
                        "title",
                        "description",
                        "other",
                    }
                    or not isinstance(alias.get("ambiguous"), bool)
                    or alias.get("change")
                    not in {"introduced", "continued", "retired", "uncertain"}
                    or not isinstance(alias.get("effectiveRange"), dict)
                    or not isinstance(alias.get("confidence"), dict)
                    or not isinstance(alias.get("evidence"), list)
                ):
                    raise _correction_patch_invalid()
                effective_range = alias["effectiveRange"]
                if (
                    not _has_exact_keys(
                        effective_range,
                        {
                            "sourceRange",
                            "validFromEventId",
                            "validThroughEventId",
                        },
                    )
                    or not _valid_source_span(
                        effective_range.get("sourceRange"),
                        run=run,
                        story=story,
                    )
                    or not _valid_id(
                        effective_range.get("validFromEventId"),
                        nullable=True,
                    )
                    or not _valid_id(
                        effective_range.get("validThroughEventId"),
                        nullable=True,
                    )
                ):
                    raise _correction_patch_invalid()
            else:
                raise _correction_patch_invalid()
        elif category == "character_merge":
            _require_patch_shape(
                patch,
                keys={"mergeIntoCharacterId"},
                predicates={
                    "mergeIntoCharacterId": _valid_id(patch.get("mergeIntoCharacterId"))
                    and patch.get("mergeIntoCharacterId") != entity.id
                },
            )
            self._require_same_run_ids(
                session,
                run_id=run.id,
                collection="characters",
                entity_ids=[str(patch["mergeIntoCharacterId"])],
            )
            prior_registry_operations = [
                correction
                for correction, _effective_patch in self._applicable_corrections(
                    session,
                    project_id=run.project_id,
                    target_key=f"characters:{entity.identity_key}",
                    run=run,
                )
                if correction.category in {"character_merge", "character_split"}
            ]
            if (
                prior_registry_operations
                and max(prior_registry_operations, key=_correction_order_key).category
                == "character_split"
            ):
                raise _correction_patch_invalid()
            replacement_ids, _merge_corrections, _split_mentions = self._registry_effect_index(
                session,
                run=run,
            )
            proposed_target = str(patch["mergeIntoCharacterId"])
            visited: set[str] = set()
            while proposed_target not in visited:
                if proposed_target == entity.id:
                    raise _correction_patch_invalid()
                visited.add(proposed_target)
                next_target = replacement_ids.get(proposed_target)
                if next_target is None:
                    break
                proposed_target = next_target
        elif category == "character_split":
            _require_patch_shape(
                patch,
                keys={
                    "newRegistryCharacterId",
                    "canonicalName",
                    "normalizedCanonicalName",
                    "mentionIds",
                },
                predicates={
                    "newRegistryCharacterId": _valid_id(patch.get("newRegistryCharacterId")),
                    "canonicalName": _valid_string(
                        patch.get("canonicalName"),
                        maximum=240,
                    ),
                    "normalizedCanonicalName": patch.get("normalizedCanonicalName")
                    == " ".join(str(patch.get("canonicalName", "")).split()).casefold(),
                    "mentionIds": _valid_unique_ids(
                        patch.get("mentionIds"),
                        minimum=1,
                        maximum=100_000,
                    ),
                },
            )
            self._require_same_run_ids(
                session,
                run_id=run.id,
                collection="mentions",
                entity_ids=list(patch["mentionIds"]),
            )
            prior_registry_operations = [
                correction
                for correction, _effective_patch in self._applicable_corrections(
                    session,
                    project_id=run.project_id,
                    target_key=f"characters:{entity.identity_key}",
                    run=run,
                )
                if correction.category in {"character_merge", "character_split"}
            ]
            if (
                prior_registry_operations
                and max(prior_registry_operations, key=_correction_order_key).category
                == "character_merge"
            ):
                raise _correction_patch_invalid()
            new_registry_id = str(patch["newRegistryCharacterId"])
            machine_registry_ids: set[str] = set()
            for character in session.scalars(
                select(AnalysisEntityRow).where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == "characters",
                )
            ):
                machine_registry_ids.add(character.id)
                character_payload = parse_json(character.payload_json, {})
                if isinstance(character_payload, dict):
                    for field in ("characterId", "registryCharacterId"):
                        value = character_payload.get(field)
                        if isinstance(value, str):
                            machine_registry_ids.add(value)
            if new_registry_id in machine_registry_ids:
                raise _correction_patch_invalid()
            for prior_split in session.scalars(
                select(AnalysisCorrectionRow).where(
                    AnalysisCorrectionRow.project_id == run.project_id,
                    AnalysisCorrectionRow.category == "character_split",
                )
            ):
                prior_patch = parse_json(prior_split.patch_json, {})
                if (
                    isinstance(prior_patch, dict)
                    and prior_patch.get("newRegistryCharacterId") == new_registry_id
                ):
                    raise _correction_patch_invalid()

            mention_rows = list(
                session.scalars(
                    select(AnalysisEntityRow).where(
                        AnalysisEntityRow.run_id == run.id,
                        AnalysisEntityRow.collection == "mentions",
                        AnalysisEntityRow.id.in_(list(patch["mentionIds"])),
                    )
                )
            )
            for mention in mention_rows:
                mention_payload = parse_json(mention.payload_json, {})
                if not isinstance(mention_payload, dict):
                    raise _correction_patch_invalid()
                for correction, effective_patch in self._applicable_corrections(
                    session,
                    project_id=run.project_id,
                    target_key=f"mentions:{mention.identity_key}",
                    run=run,
                ):
                    if correction.category == "mention_resolution":
                        raise _correction_patch_invalid()
                    mention_payload = _apply_correction_patch(
                        mention_payload,
                        correction,
                        effective_patch=effective_patch,
                    )
                if mention_payload.get(
                    "effectiveCharacterId"
                ) != entity.id and entity.id not in mention_payload.get(
                    "candidateCharacterIds", []
                ):
                    raise _correction_patch_invalid()
            _replacement_ids, _merge_corrections, split_mentions = self._registry_effect_index(
                session,
                run=run,
            )
            target_key = f"characters:{entity.identity_key}"
            for mention_id in patch["mentionIds"]:
                prior_owner = split_mentions.get(str(mention_id))
                if prior_owner is not None and prior_owner[1].target_key != target_key:
                    raise _correction_patch_invalid()
        elif category == "mention_resolution":
            _require_patch_shape(
                patch,
                keys={
                    "resolution",
                    "effectiveCharacterId",
                    "candidateCharacterIds",
                },
                predicates={
                    "resolution": patch.get("resolution")
                    in {"resolved", "ambiguous", "unresolved"},
                    "effectiveCharacterId": _valid_id(
                        patch.get("effectiveCharacterId"),
                        nullable=True,
                    ),
                    "candidateCharacterIds": _valid_unique_ids(
                        patch.get("candidateCharacterIds"),
                        maximum=8,
                    ),
                    "consistency": (
                        patch.get("resolution") == "resolved"
                        and patch.get("effectiveCharacterId") is not None
                        or patch.get("resolution") != "resolved"
                        and patch.get("effectiveCharacterId") is None
                    ),
                },
            )
            referenced = list(patch["candidateCharacterIds"])
            if patch["effectiveCharacterId"] is not None:
                referenced.append(str(patch["effectiveCharacterId"]))
            self._require_same_run_ids(
                session,
                run_id=run.id,
                collection="characters",
                entity_ids=list(dict.fromkeys(referenced)),
            )
            _replacement_ids, _merge_corrections, split_mentions = (
                self._registry_effect_index(
                    session,
                    run=run,
                )
            )
            if entity.id in split_mentions:
                raise _correction_patch_invalid()
        elif category == "dialogue_speaker":
            _require_patch_shape(
                patch,
                keys={
                    "speakerCharacterId",
                    "selectedCandidateId",
                    "requiresHumanReview",
                },
                predicates={
                    "speakerCharacterId": _valid_id(
                        patch.get("speakerCharacterId"),
                        nullable=True,
                    ),
                    "selectedCandidateId": _valid_id(
                        patch.get("selectedCandidateId"),
                        nullable=True,
                    ),
                    "requiresHumanReview": isinstance(
                        patch.get("requiresHumanReview"),
                        bool,
                    ),
                    "consistency": (
                        patch.get("speakerCharacterId") is None
                        and patch.get("selectedCandidateId") is None
                        and patch.get("requiresHumanReview") is True
                        or patch.get("speakerCharacterId") is not None
                    ),
                },
            )
            if patch["speakerCharacterId"] is not None:
                self._require_same_run_ids(
                    session,
                    run_id=run.id,
                    collection="characters",
                    entity_ids=[str(patch["speakerCharacterId"])],
                )
            if patch["selectedCandidateId"] is not None:
                payload = parse_json(entity.payload_json, {})
                candidates = payload.get("candidates", [])
                match = next(
                    (
                        candidate
                        for candidate in candidates
                        if isinstance(candidate, dict)
                        and candidate.get("candidateId") == patch["selectedCandidateId"]
                    ),
                    None,
                )
                if match is None or match.get("characterId") != patch["speakerCharacterId"]:
                    raise _correction_patch_invalid()
        elif category == "point_of_view":
            _require_patch_shape(
                patch,
                keys={
                    "mode",
                    "viewpointCharacterId",
                    "narratorCharacterId",
                },
                predicates={
                    "mode": patch.get("mode") in _POV_MODES,
                    "viewpointCharacterId": _valid_id(
                        patch.get("viewpointCharacterId"),
                        nullable=True,
                    ),
                    "narratorCharacterId": _valid_id(
                        patch.get("narratorCharacterId"),
                        nullable=True,
                    ),
                },
            )
            self._require_same_run_ids(
                session,
                run_id=run.id,
                collection="characters",
                entity_ids=[
                    str(value)
                    for value in (
                        patch["viewpointCharacterId"],
                        patch["narratorCharacterId"],
                    )
                    if value is not None
                ],
            )
        elif category == "location_identity":
            _require_patch_shape(
                patch,
                keys={
                    "canonicalName",
                    "normalizedCanonicalName",
                    "kind",
                    "parentLocationId",
                },
                predicates={
                    "canonicalName": _valid_string(
                        patch.get("canonicalName"),
                        maximum=500,
                    ),
                    "normalizedCanonicalName": patch.get("normalizedCanonicalName")
                    == " ".join(str(patch.get("canonicalName", "")).split()).casefold(),
                    "kind": patch.get("kind") in _LOCATION_KINDS,
                    "parentLocationId": _valid_id(
                        patch.get("parentLocationId"),
                        nullable=True,
                    )
                    and patch.get("parentLocationId") != entity.id,
                },
            )
            if patch["parentLocationId"] is not None:
                self._require_same_run_ids(
                    session,
                    run_id=run.id,
                    collection="locations",
                    entity_ids=[str(patch["parentLocationId"])],
                )
        elif category == "location_alias":
            _require_patch_shape(
                patch,
                keys={"operation", "alias"},
                predicates={
                    "operation": patch.get("operation") in {"add", "remove"},
                    "alias": _valid_string(patch.get("alias"), maximum=500),
                },
            )
        elif category == "temporal_order":
            _require_patch_shape(
                patch,
                keys={"relation", "approximate", "status"},
                predicates={
                    "relation": patch.get("relation") in _TEMPORAL_RELATIONS,
                    "approximate": isinstance(patch.get("approximate"), bool),
                    "status": patch.get("status") in _TEMPORAL_STATUSES,
                },
            )
        elif category == "relationship":
            _require_patch_shape(
                patch,
                keys={
                    "sourceCharacterId",
                    "targetCharacterId",
                    "kind",
                    "state",
                    "change",
                    "scope",
                    "validFromEventId",
                    "validThroughEventId",
                },
                predicates={
                    "sourceCharacterId": _valid_id(patch.get("sourceCharacterId")),
                    "targetCharacterId": _valid_id(patch.get("targetCharacterId"))
                    and patch.get("sourceCharacterId") != patch.get("targetCharacterId"),
                    "kind": patch.get("kind") in _RELATIONSHIP_KINDS,
                    "state": _valid_string(patch.get("state"), maximum=1000),
                    "change": patch.get("change") in _RELATIONSHIP_CHANGES,
                    "scope": isinstance(patch.get("scope"), dict),
                    "validFromEventId": _valid_id(
                        patch.get("validFromEventId"),
                        nullable=True,
                    ),
                    "validThroughEventId": _valid_id(
                        patch.get("validThroughEventId"),
                        nullable=True,
                    ),
                },
            )
            scope = patch["scope"]
            if (
                not _has_exact_keys(
                    scope,
                    {"kind", "firstSceneId", "lastSceneId", "sourceRange"},
                )
                or scope.get("kind") not in {"scene", "chapter", "scene_range"}
                or not _valid_id(scope.get("firstSceneId"))
                or not _valid_id(scope.get("lastSceneId"))
                or not _valid_source_span(
                    scope.get("sourceRange"),
                    run=run,
                    story=story,
                )
            ):
                raise _correction_patch_invalid()
            self._require_same_run_ids(
                session,
                run_id=run.id,
                collection="characters",
                entity_ids=[
                    str(patch["sourceCharacterId"]),
                    str(patch["targetCharacterId"]),
                ],
            )
            self._require_same_run_ids(
                session,
                run_id=run.id,
                collection="scenes",
                entity_ids=[
                    str(scope["firstSceneId"]),
                    str(scope["lastSceneId"]),
                ],
            )
            self._require_same_run_ids(
                session,
                run_id=run.id,
                collection="timeline-events",
                entity_ids=[
                    str(value)
                    for value in (
                        patch["validFromEventId"],
                        patch["validThroughEventId"],
                    )
                    if value is not None
                ],
            )
        elif category == "emotional_state":
            _require_patch_shape(
                patch,
                keys={
                    "emotion",
                    "customEmotion",
                    "note",
                    "valence",
                    "arousal",
                    "intensity",
                    "progression",
                },
                predicates={
                    "emotion": patch.get("emotion") in _EMOTIONS,
                    "customEmotion": _valid_string(
                        patch.get("customEmotion"),
                        maximum=160,
                        nullable=True,
                    )
                    and (
                        patch.get("emotion") == "custom"
                        and patch.get("customEmotion") is not None
                        or patch.get("emotion") != "custom"
                        and patch.get("customEmotion") is None
                    ),
                    "note": _valid_string(
                        patch.get("note"),
                        minimum=0,
                        maximum=1000,
                    ),
                    "valence": _valid_number(patch.get("valence"), -1, 1),
                    "arousal": _valid_number(patch.get("arousal"), 0, 1),
                    "intensity": _valid_number(patch.get("intensity"), 0, 1),
                    "progression": patch.get("progression") in _EMOTION_PROGRESSIONS,
                },
            )
        elif category == "dramatic_intent":
            _require_patch_shape(
                patch,
                keys={
                    "intent",
                    "customIntent",
                    "dramaticFunction",
                    "customDramaticFunction",
                    "note",
                    "targetCharacterId",
                    "status",
                },
                predicates={
                    "intent": patch.get("intent") in _DRAMATIC_INTENTS,
                    "customIntent": _valid_string(
                        patch.get("customIntent"),
                        maximum=160,
                        nullable=True,
                    )
                    and (
                        patch.get("intent") == "custom"
                        and patch.get("customIntent") is not None
                        or patch.get("intent") != "custom"
                        and patch.get("customIntent") is None
                    ),
                    "dramaticFunction": patch.get("dramaticFunction") in _DRAMATIC_FUNCTIONS,
                    "customDramaticFunction": _valid_string(
                        patch.get("customDramaticFunction"),
                        maximum=160,
                        nullable=True,
                    )
                    and (
                        patch.get("dramaticFunction") == "custom"
                        and patch.get("customDramaticFunction") is not None
                        or patch.get("dramaticFunction") != "custom"
                        and patch.get("customDramaticFunction") is None
                    ),
                    "note": _valid_string(
                        patch.get("note"),
                        minimum=0,
                        maximum=1000,
                    ),
                    "targetCharacterId": _valid_id(
                        patch.get("targetCharacterId"),
                        nullable=True,
                    ),
                    "status": patch.get("status") in _INTENT_STATUSES,
                },
            )
            if patch["targetCharacterId"] is not None:
                self._require_same_run_ids(
                    session,
                    run_id=run.id,
                    collection="characters",
                    entity_ids=[str(patch["targetCharacterId"])],
                )
        elif category == "continuity_disposition":
            _require_patch_shape(
                patch,
                keys={"disposition", "explanation"},
                predicates={
                    "disposition": patch.get("disposition") in _CONTINUITY_DISPOSITIONS,
                    "explanation": _valid_string(
                        patch.get("explanation"),
                        maximum=2000,
                    ),
                },
            )
        else:
            raise _correction_patch_invalid()

    def _derive_structure_boundary_patch(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        requested_span = patch.get("sourceSpan")
        if not isinstance(requested_span, dict) or not _has_exact_keys(
            requested_span,
            {
                "sourceDocumentId",
                "extractionId",
                "extractionRevision",
                "offsetUnit",
                "startOffset",
                "endOffset",
            },
        ):
            raise _correction_patch_invalid()
        start = requested_span.get("startOffset")
        end = requested_span.get("endOffset")
        if (
            requested_span.get("sourceDocumentId") != run.source_document_id
            or requested_span.get("extractionId") != run.extraction_id
            or requested_span.get("extractionRevision")
            != run.extraction_revision
            or requested_span.get("offsetUnit") != "unicode-code-point"
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
        ):
            raise _correction_patch_invalid()

        extraction = session.get(DocumentExtractionRow, run.extraction_id)
        extracted_text = (
            extraction.exact_text
            if extraction is not None
            and isinstance(extraction.exact_text, str)
            else None
        )
        extracted_text_sha256 = (
            sha256_text(extracted_text)
            if extracted_text is not None
            else None
        )
        if (
            extraction is None
            or extraction.project_id != run.project_id
            or extraction.source_document_id != run.source_document_id
            or extraction.revision != run.extraction_revision
            or extraction.status not in {"complete", "partial"}
            or extracted_text is None
            or extraction.text_sha256 != extracted_text_sha256
            or story.project_id != run.project_id
            or story.source_document_id != run.source_document_id
            or story.extraction_id != run.extraction_id
            or story.extraction_revision != run.extraction_revision
            or story.exact_text != extracted_text
            or story.content_fingerprint != extracted_text_sha256
            or run.extracted_text_sha256 != extracted_text_sha256
        ):
            raise ServiceError(
                409,
                "ANALYSIS_SOURCE_CONFLICT",
                "The frozen approved extraction no longer matches the analysis run.",
            )
        if end > len(extracted_text):
            raise _correction_patch_invalid()
        return {
            **patch,
            "sourceSpan": _source_span(
                run=run,
                story=story,
                start=start,
                end=end,
            ),
        }

    def _resolve_correction_target_entity(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        target_collection: str,
        target_entity_id: str | None,
    ) -> AnalysisEntityRow | None:
        if target_entity_id is None:
            return None
        entity = session.get(AnalysisEntityRow, target_entity_id)
        if (
            entity is not None
            and entity.run_id == run.id
            and entity.collection == target_collection
        ):
            return entity
        if target_collection not in {"chapters", "scenes"}:
            return None

        target_keys = list(
            session.scalars(
                select(AnalysisCorrectionRow.target_key)
                .where(
                    AnalysisCorrectionRow.project_id == run.project_id,
                    AnalysisCorrectionRow.category == "structure_boundary",
                    AnalysisCorrectionRow.target_key.like(f"{target_collection}:%"),
                    self._correction_scope_condition(run),
                )
                .distinct()
                .limit(MAX_EFFECTIVE_PROJECTION_TARGETS + 1)
            )
        )
        if len(target_keys) > MAX_EFFECTIVE_PROJECTION_TARGETS:
            raise ServiceError(
                422,
                "ANALYSIS_PROJECTION_LIMIT_EXCEEDED",
                "The bounded corrected entity projection limit was exceeded.",
            )
        registry_effects = self._registry_effect_index(session, run=run)
        for target_key in target_keys:
            identity_key = target_key.removeprefix(f"{target_collection}:")
            candidate = session.scalar(
                select(AnalysisEntityRow)
                .where(
                    AnalysisEntityRow.run_id == run.id,
                    AnalysisEntityRow.collection == target_collection,
                    AnalysisEntityRow.identity_key == identity_key,
                )
                .limit(1)
            )
            if candidate is None:
                continue
            _projected, known_ids = self._project_structure_row(
                session,
                run=run,
                story=story,
                row=candidate,
                registry_effects=registry_effects,
            )
            if target_entity_id in known_ids:
                return candidate
        return None

    def _structure_branch_current(
        self,
        session: Session,
        *,
        run: AnalysisRunRow,
        story: ImportedStoryRow,
        entity: AnalysisEntityRow,
        target_entity_id: str | None,
    ) -> tuple[AnalysisCorrectionRow | None, int]:
        original = self._entity_dict(
            session,
            run=run,
            entity=entity,
            story=story,
            registry_effects=self._registry_effect_index(session, run=run),
            excluded_correction_categories=frozenset(
                {"structure_boundary", "structure_label"}
            ),
        )
        operations = self._structure_operations(
            session,
            run=run,
            entity=entity,
        )
        state = self._structure_boundary_state(
            session,
            row=entity,
            operations=operations,
        )
        if target_entity_id == entity.id:
            effect = state.source_effect
            revision_count = state.source_revision_count
        else:
            effect = state.synthetic_effect
            revision_count = state.synthetic_revision_count
        return (
            effect[0] if effect is not None else None,
            int(original["effectiveRevision"]) + revision_count,
        )

    def append_correction(
        self,
        *,
        project_id: str,
        run_id: str,
        category: str,
        target_collection: str,
        target_entity_id: str | None,
        expected_target_revision: int,
        expected_run_fingerprint: str,
        previous_value_fingerprint: str,
        patch: dict[str, Any],
        reason: str,
        supersedes_correction_id: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], list[str]]:
        if category not in CORRECTION_CATEGORIES:
            raise ServiceError(422, "CORRECTION_CATEGORY_INVALID", "The correction is invalid.")
        if target_collection not in _CORRECTION_COLLECTION.get(category, frozenset()):
            raise _correction_patch_invalid()
        reason = reason.strip() if isinstance(reason, str) else ""
        if (
            not reason
            or len(reason) > 1000
            or any(ord(character) < 32 and character != "\t" for character in reason)
        ):
            raise ServiceError(
                422,
                "CORRECTION_REASON_INVALID",
                "A nonblank correction reason is required.",
            )
        request_patch_json = canonical_json(patch)
        if not patch or len(request_patch_json.encode()) > MAX_CORRECTION_PATCH_BYTES:
            raise ServiceError(422, "CORRECTION_PATCH_INVALID", "The correction patch is invalid.")
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = self.require_run(session, project_id=project_id, run_id=run_id)
            snapshot = self._latest_snapshot(session, run.id)
            if snapshot is None or snapshot.stage != "synthesis":
                raise ServiceError(
                    409,
                    "ANALYSIS_SNAPSHOT_REQUIRED",
                    "A completed analysis snapshot is required before correction.",
                )
            if run.run_fingerprint != expected_run_fingerprint:
                raise ServiceError(
                    409,
                    "ANALYSIS_RUN_CONFLICT",
                    "The immutable analysis run does not match the correction.",
                )
            story = session.get(ImportedStoryRow, run.story_id)
            if story is None:
                raise ServiceError(
                    500,
                    "STORY_UNAVAILABLE",
                    "The imported story is unavailable.",
                )
            if category == "structure_boundary":
                patch = self._derive_structure_boundary_patch(
                    session,
                    run=run,
                    story=story,
                    patch=patch,
                )
            patch_json = canonical_json(patch)
            if len(patch_json.encode()) > MAX_CORRECTION_PATCH_BYTES:
                raise ServiceError(
                    422,
                    "CORRECTION_PATCH_INVALID",
                    "The correction patch is invalid.",
                )
            idempotent = session.scalar(
                select(AnalysisCorrectionRow).where(
                    AnalysisCorrectionRow.run_id == run.id,
                    AnalysisCorrectionRow.idempotency_key == idempotency_key,
                )
            )
            if idempotent is not None:
                idempotent_entity = self._resolve_correction_target_entity(
                    session,
                    run=run,
                    story=story,
                    target_collection=target_collection,
                    target_entity_id=idempotent.target_entity_id,
                )
                if (
                    idempotent.category != category
                    or idempotent_entity is None
                    or idempotent_entity.collection != target_collection
                    or idempotent.target_entity_id != target_entity_id
                    or idempotent.expected_target_revision != expected_target_revision
                    or idempotent.expected_run_fingerprint != expected_run_fingerprint
                    or idempotent.previous_value_fingerprint != previous_value_fingerprint
                    or idempotent.patch_json != patch_json
                    or idempotent.reason != reason
                    or idempotent.supersedes_correction_id != supersedes_correction_id
                ):
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was used for another correction.",
                    )
                return (
                    self._correction_dict(session, idempotent),
                    self._correction_invalidated_gate_ids(
                        session,
                        correction=idempotent,
                    ),
                )
            entity = self._resolve_correction_target_entity(
                session,
                run=run,
                story=story,
                target_collection=target_collection,
                target_entity_id=target_entity_id,
            )
            if entity is None:
                raise not_found("analysis entity")
            if entity.collection != target_collection:
                raise ServiceError(
                    409,
                    "CORRECTION_TARGET_CONFLICT",
                    "The correction target collection does not match the entity.",
                )
            self._validate_correction_patch(
                session,
                run=run,
                story=story,
                entity=entity,
                category=category,
                target_collection=target_collection,
                patch=patch,
            )
            target_key = f"{entity.collection}:{entity.identity_key}"
            if category in _BOUNDED_PROJECTION_CATEGORIES:
                existing_projection_target = session.scalar(
                    select(AnalysisCorrectionRow.id)
                    .where(
                        AnalysisCorrectionRow.project_id == project_id,
                        AnalysisCorrectionRow.category == category,
                        AnalysisCorrectionRow.target_key == target_key,
                        self._correction_scope_condition(run),
                    )
                    .limit(1)
                )
                if existing_projection_target is None:
                    projection_targets = (
                        select(AnalysisCorrectionRow.target_key)
                        .where(
                            AnalysisCorrectionRow.project_id == project_id,
                            AnalysisCorrectionRow.category.in_(_BOUNDED_PROJECTION_CATEGORIES),
                            self._correction_scope_condition(run),
                        )
                        .distinct()
                        .subquery()
                    )
                    projection_target_count = int(
                        session.scalar(select(func.count()).select_from(projection_targets)) or 0
                    )
                    if projection_target_count >= MAX_EFFECTIVE_PROJECTION_TARGETS:
                        raise ServiceError(
                            422,
                            "ANALYSIS_PROJECTION_LIMIT_EXCEEDED",
                            "The bounded corrected entity projection limit was exceeded.",
                        )
            applicable_corrections = self._applicable_corrections(
                session,
                project_id=project_id,
                target_key=target_key,
                run=run,
            )
            if category in {"structure_boundary", "structure_label"}:
                current, current_revision = self._structure_branch_current(
                    session,
                    run=run,
                    story=story,
                    entity=entity,
                    target_entity_id=target_entity_id,
                )
                effective_current_item: dict[str, Any] | None = None
            else:
                current = self._effective_correction(
                    session,
                    project_id=project_id,
                    target_key=target_key,
                    run=run,
                )
                effective_current_item = self._entity_dict(
                    session,
                    run=run,
                    entity=entity,
                    story=story,
                )
                current_structure_graph = self._effective_structure_graph(
                    session,
                    run=run,
                    story=story,
                )
                if (
                    current_structure_graph.chapter_target_ids
                    or current_structure_graph.scene_target_ids
                ):
                    effective_current_item = self._apply_structure_graph_to_item(
                        session,
                        run=run,
                        story=story,
                        collection=entity.collection,
                        item=effective_current_item,
                        graph=current_structure_graph,
                    )
                elif entity.collection == "characters":
                    (
                        resolved_mentions,
                        ambiguous_mentions,
                    ) = self._effective_mention_index(
                        session,
                        run=run,
                        story=story,
                    )
                    effective_current_item = (
                        self._apply_character_mention_aggregates(
                            item=effective_current_item,
                            mention_items_by_character=resolved_mentions,
                            ambiguous_mention_items_by_character=(
                                ambiguous_mentions
                            ),
                        )
                    )
                current_revision = int(
                    effective_current_item["effectiveRevision"]
                )
            if current_revision != expected_target_revision:
                raise ServiceError(
                    409,
                    "CORRECTION_REVISION_CONFLICT",
                    "The corrected claim changed; refresh before saving.",
                    details={"currentRevision": current_revision},
                )
            if (current is None and supersedes_correction_id is not None) or (
                current is not None and supersedes_correction_id != current.id
            ):
                raise ServiceError(
                    409,
                    "CORRECTION_SUPERSESSION_CONFLICT",
                    "The correction supersession chain changed.",
                )
            payload = parse_json(entity.payload_json, {})
            for correction_value, effective_patch in applicable_corrections:
                payload = _apply_correction_patch(
                    payload,
                    correction_value,
                    effective_patch=effective_patch,
                )
            actual_previous_fingerprint = request_fingerprint(
                _wire_entity_payload(
                    collection=entity.collection,
                    payload=payload,
                    run=run,
                    story=story,
                    start=entity.start_offset,
                    end=entity.end_offset,
                    correction=current,
                )
            )
            if entity.collection in {"chapters", "scenes", "beats"}:
                actual_previous_fingerprint = self._effective_structural_entity(
                    session,
                    run=run,
                    story=story,
                    entity=entity,
                    effective_entity_id=target_entity_id,
                )["effectiveValueFingerprint"]
            elif effective_current_item is not None:
                actual_previous_fingerprint = effective_current_item[
                    "effectiveValueFingerprint"
                ]
            if actual_previous_fingerprint != previous_value_fingerprint:
                raise ServiceError(
                    409,
                    "CORRECTION_VALUE_CONFLICT",
                    "The correction target value changed; refresh before saving.",
                )
            prior_structure_graph = (
                self._effective_structure_graph(
                    session,
                    run=run,
                    story=story,
                )
                if category == "structure_boundary"
                and entity.collection == "chapters"
                else None
            )
            prior_registry_replacements = (
                self._registry_effect_index(
                    session,
                    run=run,
                )[0]
                if category
                in {
                    "character_identity",
                    "character_alias",
                    "character_merge",
                    "character_split",
                }
                else {}
            )
            now = utc_now()
            latest_correction_recorded_at = session.scalar(
                select(func.max(AnalysisCorrectionRow.recorded_at)).where(
                    AnalysisCorrectionRow.project_id == project_id,
                )
            )
            if (
                latest_correction_recorded_at is not None
                and now <= latest_correction_recorded_at
            ):
                latest_instant = datetime.fromisoformat(
                    latest_correction_recorded_at.replace("Z", "+00:00")
                )
                now = (
                    (latest_instant.astimezone(UTC) + timedelta(milliseconds=1))
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
            revision = (
                max(
                    (
                        correction.revision
                        for correction, _effective_patch in applicable_corrections
                        if correction.category == category
                    ),
                    default=0,
                )
                + 1
                if category in {"structure_boundary", "structure_label"}
                else (current.revision + 1)
                if current is not None
                else 1
            )
            fingerprint = request_fingerprint(
                {
                    "projectId": project_id,
                    "runId": run.id,
                    "category": category,
                    "targetKey": target_key,
                    "targetCollection": target_collection,
                    "targetEntityId": target_entity_id,
                    "revision": revision,
                    "expectedTargetRevision": expected_target_revision,
                    "expectedRunFingerprint": expected_run_fingerprint,
                    "previousValueFingerprint": actual_previous_fingerprint,
                    "patch": patch,
                    "reason": reason,
                    "supersedesCorrectionId": supersedes_correction_id,
                    "idempotencyKey": idempotency_key,
                }
            )
            row = AnalysisCorrectionRow(
                id=new_id(),
                project_id=project_id,
                run_id=run.id,
                category=category,
                target_entity_id=target_entity_id,
                target_key=target_key,
                revision=revision,
                expected_target_revision=expected_target_revision,
                expected_run_fingerprint=expected_run_fingerprint,
                previous_value_fingerprint=actual_previous_fingerprint,
                patch_json=patch_json,
                correction_fingerprint=fingerprint,
                reason=reason,
                actor_id="local_user",
                supersedes_correction_id=current.id if current is not None else None,
                legacy_correction_id=None,
                idempotency_key=idempotency_key,
                recorded_at=now,
            )
            session.add(row)
            session.flush()
            invalidated: list[str] = []
            correction_gate_ids = list(_CORRECTION_GATES[category])
            if category == "structure_boundary":
                affected_scene_ids: set[str] = set()
                current_operation = patch.get("operation")
                targets_synthetic_branch = target_entity_id != entity.id
                creates_empty_branch = current is None and current_operation == "add"
                if not creates_empty_branch:
                    if entity.collection == "scenes" and not targets_synthetic_branch:
                        affected_scene_ids.add(entity.id)
                    elif entity.collection == "chapters":
                        if not targets_synthetic_branch:
                            affected_scene_ids.update(
                                str(scene_id)
                                for scene_id in session.scalars(
                                    select(AnalysisEntityRow.id).where(
                                        AnalysisEntityRow.run_id == run.id,
                                        AnalysisEntityRow.collection == "scenes",
                                        AnalysisEntityRow.parent_entity_id == entity.id,
                                    )
                                )
                            )
                        current_structure_graph = self._effective_structure_graph(
                            session,
                            run=run,
                            story=story,
                        )
                        for graph in (
                            prior_structure_graph,
                            current_structure_graph,
                        ):
                            if graph is None:
                                continue
                            affected_scene_ids.update(
                                scene_id
                                for scene_id, parent_id in (
                                    graph.effective_scene_parents.items()
                                )
                                if parent_id == target_entity_id
                            )
                if affected_scene_ids:
                    character_evidence_affected = bool(
                        session.scalar(
                            select(func.count())
                            .select_from(AnalysisEntityRow)
                            .where(
                                AnalysisEntityRow.run_id == run.id,
                                AnalysisEntityRow.collection.in_(
                                    {"mentions", "relationships"}
                                ),
                                func.json_extract(
                                    AnalysisEntityRow.payload_json,
                                    "$.sceneId",
                                ).in_(affected_scene_ids),
                            )
                        )
                    )
                    dialogue_evidence_affected = bool(
                        session.scalar(
                            select(func.count())
                            .select_from(AnalysisEntityRow)
                            .where(
                                AnalysisEntityRow.run_id == run.id,
                                AnalysisEntityRow.collection == "dialogue-lines",
                                func.json_extract(
                                    AnalysisEntityRow.payload_json,
                                    "$.sceneId",
                                ).in_(affected_scene_ids),
                            )
                        )
                    )
                    if (
                        character_evidence_affected
                        and "character_registry_review" not in correction_gate_ids
                    ):
                        correction_gate_ids.append("character_registry_review")
                    if (
                        dialogue_evidence_affected
                        and "dialogue_attribution_review" not in correction_gate_ids
                    ):
                        correction_gate_ids.append("dialogue_attribution_review")
            if category in {
                "character_identity",
                "character_alias",
                "character_merge",
                "character_split",
            }:
                affected_character_ids = {
                    entity.id,
                    *(
                        source_id
                        for source_id, effective_id in (
                            prior_registry_replacements.items()
                        )
                        if effective_id == entity.id
                    ),
                }
                dialogue_affected = False
                ordered_affected_character_ids = sorted(affected_character_ids)
                for offset in range(
                    0,
                    len(ordered_affected_character_ids),
                    400,
                ):
                    dialogue_affected = bool(
                        session.scalar(
                            select(func.count())
                            .select_from(AnalysisEntityRow)
                            .where(
                                AnalysisEntityRow.run_id == run.id,
                                AnalysisEntityRow.collection == "dialogue-lines",
                                or_(
                                    *(
                                        AnalysisEntityRow.payload_json.contains(
                                            character_id
                                        )
                                        for character_id in (
                                            ordered_affected_character_ids[
                                                offset : offset + 400
                                            ]
                                        )
                                    )
                                ),
                            )
                        )
                    )
                    if dialogue_affected:
                        break
            elif category == "mention_resolution":
                entity_payload = parse_json(entity.payload_json, {})
                scene_id = entity_payload.get("sceneId")
                dialogue_affected = isinstance(scene_id, str) and bool(
                    session.scalar(
                        select(func.count())
                        .select_from(AnalysisEntityRow)
                        .where(
                            AnalysisEntityRow.run_id == run.id,
                            AnalysisEntityRow.collection == "dialogue-lines",
                            AnalysisEntityRow.payload_json.contains(scene_id),
                        )
                    )
                )
            else:
                dialogue_affected = False
            if dialogue_affected and "dialogue_attribution_review" not in correction_gate_ids:
                correction_gate_ids.append("dialogue_attribution_review")
            for gate_id in correction_gate_ids:
                gate = self._latest_gate(session, run_id=run.id, gate_id=gate_id)
                if gate is None:
                    continue
                new_state = "invalidated" if gate.state == "approved" else "pending"
                if gate.state == "approved":
                    invalidated.append(gate_id)
                session.add(
                    AnalysisReviewDecisionRow(
                        id=new_id(),
                        project_id=project_id,
                        run_id=run.id,
                        snapshot_id=gate.snapshot_id,
                        gate_id=gate_id,
                        revision=gate.revision + 1,
                        state=new_state,
                        artifact_fingerprint=request_fingerprint(
                            {
                                "prior": gate.artifact_fingerprint,
                                "correction": fingerprint,
                            }
                        ),
                        evidence_fingerprint=request_fingerprint(
                            {
                                "prior": gate.evidence_fingerprint,
                                "correction": fingerprint,
                            }
                        ),
                        eligible=gate_id != "whole_book_analysis_review",
                        rationale="A human correction changed governed evidence.",
                        warning_acknowledgements_json="[]",
                        provenance_json=canonical_json(
                            {
                                "origin": "human",
                                "actorId": "local_user",
                                "correctionId": row.id,
                            }
                        ),
                        actor_id="local_user",
                        idempotency_key=None,
                        supersedes_decision_id=gate.id,
                        decided_at=now if new_state == "invalidated" else None,
                        created_at=now,
                    )
                )
            return self._correction_dict(session, row), [
                gate_id for gate_id in REVIEW_GATES if gate_id in invalidated
            ]

    @staticmethod
    def _latest_gate(
        session: Session,
        *,
        run_id: str,
        gate_id: str,
    ) -> AnalysisReviewDecisionRow | None:
        return session.scalar(
            select(AnalysisReviewDecisionRow)
            .where(
                AnalysisReviewDecisionRow.run_id == run_id,
                AnalysisReviewDecisionRow.gate_id == gate_id,
            )
            .order_by(
                AnalysisReviewDecisionRow.revision.desc(),
                AnalysisReviewDecisionRow.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _gate_warning_ids(
        session: Session,
        *,
        run_id: str,
        gate_id: str,
    ) -> list[str]:
        rows = list(
            session.execute(
                select(
                    AnalysisEntityRow.id,
                    AnalysisEntityRow.warnings_json,
                ).where(
                    AnalysisEntityRow.run_id == run_id,
                    AnalysisEntityRow.collection.in_(_GATE_COLLECTIONS[gate_id]),
                    AnalysisEntityRow.warnings_json != "[]",
                )
            )
        )
        values: list[str] = []
        for entity_id, warnings_json in rows:
            for ordinal, warning_value in enumerate(
                parse_json(warnings_json, [])[:MAX_WARNINGS_PER_ENTITY]
            ):
                if isinstance(warning_value, dict):
                    values.append(
                        stable_id(
                            str(entity_id),
                            "warning",
                            ordinal,
                            warning_value.get("code", "warning"),
                        )
                    )
        return list(dict.fromkeys(values))[:32]

    @staticmethod
    def _review_id(*, run_id: str, gate_id: str) -> str:
        return stable_id("analysis-gate-review-v1", run_id, gate_id)

    @staticmethod
    def _gate_evidence(
        session: Session,
        *,
        gate: AnalysisReviewDecisionRow,
    ) -> dict[str, Any]:
        run = session.get(AnalysisRunRow, gate.run_id)
        snapshot = session.get(AnalysisSnapshotRow, gate.snapshot_id)
        if run is None or snapshot is None:
            raise ServiceError(
                500,
                "ANALYSIS_REVIEW_EVIDENCE_UNAVAILABLE",
                "The governed review evidence is unavailable.",
            )
        execution = session.get(AnalysisExecutionRow, snapshot.execution_id)
        return {
            "projectId": gate.project_id,
            "sourceDocumentId": run.source_document_id,
            "extractionId": run.extraction_id,
            "extractionRevision": run.extraction_revision,
            "storyId": run.story_id,
            "profileId": "whole-book-intelligence-v1",
            "profileFingerprint": run.profile_fingerprint,
            "runId": run.id,
            "runFingerprint": run.run_fingerprint,
            "snapshotId": snapshot.id,
            "snapshotRevision": execution.attempt if execution is not None else 1,
            "snapshotFingerprint": snapshot.fingerprint,
            "artifactFingerprint": gate.artifact_fingerprint,
            "evidenceFingerprint": gate.evidence_fingerprint,
        }

    @staticmethod
    def _latest_gate_decision(
        session: Session,
        *,
        run_id: str,
        gate_id: str,
        before_revision: int | None = None,
    ) -> AnalysisReviewDecisionRow | None:
        statement = select(AnalysisReviewDecisionRow).where(
            AnalysisReviewDecisionRow.run_id == run_id,
            AnalysisReviewDecisionRow.gate_id == gate_id,
            AnalysisReviewDecisionRow.state.in_(("approved", "changes_requested", "rejected")),
        )
        if before_revision is not None:
            statement = statement.where(AnalysisReviewDecisionRow.revision < before_revision)
        return session.scalar(
            statement.order_by(
                AnalysisReviewDecisionRow.revision.desc(),
                AnalysisReviewDecisionRow.id.desc(),
            ).limit(1)
        )

    def _gate_provenance(
        self,
        session: Session,
        *,
        gate: AnalysisReviewDecisionRow,
    ) -> dict[str, Any]:
        run = session.get(AnalysisRunRow, gate.run_id)
        if run is None:
            raise ServiceError(
                500,
                "ANALYSIS_REVIEW_EVIDENCE_UNAVAILABLE",
                "The governed review evidence is unavailable.",
            )
        stored = parse_json(gate.provenance_json, {})
        if gate.state in {"approved", "changes_requested", "rejected"}:
            return {
                "origin": "human_review",
                "recordedAt": gate.decided_at or gate.created_at,
                "inputFingerprint": gate.evidence_fingerprint,
                "deterministic": False,
            }
        correction_id = stored.get("correctionId") if isinstance(stored, dict) else None
        if isinstance(correction_id, str):
            return {
                "origin": "human_correction",
                "recordedAt": gate.created_at,
                "inputFingerprint": gate.evidence_fingerprint,
                "correctionId": correction_id,
                "deterministic": False,
            }
        snapshot = session.get(AnalysisSnapshotRow, gate.snapshot_id)
        agent = None
        if snapshot is not None:
            agent = session.scalar(
                select(AnalysisAgentExecutionRow)
                .where(
                    AnalysisAgentExecutionRow.execution_id == snapshot.execution_id,
                    AnalysisAgentExecutionRow.agent_id == "analysis-synthesis",
                )
                .limit(1)
            )
        if agent is None:
            return {
                "origin": "migration",
                "recordedAt": gate.created_at,
                "inputFingerprint": run.input_fingerprint,
                "deterministic": True,
            }
        return {
            "origin": "analysis_synthesis",
            "recordedAt": gate.created_at,
            "inputFingerprint": run.input_fingerprint,
            "agentExecutionId": agent.id,
            "agentId": agent.agent_id,
            "agentVersion": agent.agent_version,
            "producerId": run.producer_id,
            "producerVersion": run.producer_version,
            "deterministic": True,
        }

    def _gate_eligible(
        self,
        session: Session,
        *,
        gate: AnalysisReviewDecisionRow,
    ) -> bool:
        other_states = {
            gate_id: (
                latest.state
                if (
                    latest := self._latest_gate(
                        session,
                        run_id=gate.run_id,
                        gate_id=gate_id,
                    )
                )
                else None
            )
            for gate_id in REVIEW_GATES[:3]
        }
        return gate.eligible or (
            gate.gate_id == "whole_book_analysis_review"
            and all(value == "approved" for value in other_states.values())
        )

    def _gate_review_dict(
        self,
        session: Session,
        *,
        gate: AnalysisReviewDecisionRow,
    ) -> dict[str, Any]:
        warning_ids = self._gate_warning_ids(
            session,
            run_id=gate.run_id,
            gate_id=gate.gate_id,
        )
        acknowledged_warning_ids = parse_json(
            gate.warning_acknowledgements_json,
            [],
        )
        if not isinstance(acknowledged_warning_ids, list):
            acknowledged_warning_ids = []
        acknowledged_warning_ids = [
            value for value in acknowledged_warning_ids if isinstance(value, str)
        ][:32]
        acknowledged = set(acknowledged_warning_ids)
        latest_decision = self._latest_gate_decision(
            session,
            run_id=gate.run_id,
            gate_id=gate.gate_id,
        )
        result: dict[str, Any] = {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "reviewId": self._review_id(
                run_id=gate.run_id,
                gate_id=gate.gate_id,
            ),
            "projectId": gate.project_id,
            "gateId": gate.gate_id,
            "runId": gate.run_id,
            "snapshotId": gate.snapshot_id,
            "state": gate.state,
            "revision": gate.revision,
            "artifactFingerprint": gate.artifact_fingerprint,
            "evidenceFingerprint": gate.evidence_fingerprint,
            "evidence": self._gate_evidence(session, gate=gate),
            "openWarningIds": [value for value in warning_ids if value not in acknowledged],
            "acknowledgedWarningIds": acknowledged_warning_ids,
            "latestDecision": (
                self._gate_decision_dict(
                    session,
                    gate=latest_decision,
                )
                if latest_decision is not None
                else None
            ),
            "provenance": self._gate_provenance(session, gate=gate),
            "updatedAt": gate.decided_at or gate.created_at,
        }
        if latest_decision is not None:
            result["latestDecisionId"] = latest_decision.id
        return result

    def _gate_decision_dict(
        self,
        session: Session,
        *,
        gate: AnalysisReviewDecisionRow,
    ) -> dict[str, Any]:
        if gate.state not in {"approved", "changes_requested", "rejected"}:
            raise ServiceError(
                500,
                "ANALYSIS_REVIEW_DECISION_UNAVAILABLE",
                "The immutable review decision is unavailable.",
            )
        if not gate.rationale.strip():
            raise ServiceError(
                500,
                "ANALYSIS_REVIEW_DECISION_UNAVAILABLE",
                "The immutable review decision rationale is unavailable.",
            )
        acknowledged_warning_ids = parse_json(
            gate.warning_acknowledgements_json,
            [],
        )
        if not isinstance(acknowledged_warning_ids, list):
            acknowledged_warning_ids = []
        previous_decision = self._latest_gate_decision(
            session,
            run_id=gate.run_id,
            gate_id=gate.gate_id,
            before_revision=gate.revision,
        )
        result: dict[str, Any] = {
            "contractVersion": ANALYSIS_CONTRACT_VERSION,
            "decisionId": gate.id,
            "reviewId": self._review_id(
                run_id=gate.run_id,
                gate_id=gate.gate_id,
            ),
            "projectId": gate.project_id,
            "gateId": gate.gate_id,
            "runId": gate.run_id,
            "snapshotId": gate.snapshot_id,
            "decision": gate.state,
            "rationale": gate.rationale,
            "artifactFingerprint": gate.artifact_fingerprint,
            "evidenceFingerprint": gate.evidence_fingerprint,
            "evidence": self._gate_evidence(session, gate=gate),
            "actor": {
                "classification": "human",
                "actorId": gate.actor_id or "local_user",
            },
            "acknowledgedWarningIds": [
                value for value in acknowledged_warning_ids if isinstance(value, str)
            ][:32],
            "provenance": {
                "origin": "human_review",
                "recordedAt": gate.decided_at or gate.created_at,
                "inputFingerprint": gate.evidence_fingerprint,
                "deterministic": False,
            },
            "decidedAt": gate.decided_at or gate.created_at,
            "immutable": True,
        }
        if previous_decision is not None:
            result["supersedesDecisionId"] = previous_decision.id
        return result

    def list_reviews(
        self,
        *,
        project_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            self.require_run(session, project_id=project_id, run_id=run_id)
            values = []
            for gate_id in REVIEW_GATES:
                gate = self._latest_gate(session, run_id=run_id, gate_id=gate_id)
                if gate is not None:
                    values.append(self._gate_review_dict(session, gate=gate))
            return values

    def decide_review(
        self,
        *,
        project_id: str,
        run_id: str,
        gate_id: str,
        decision: str,
        expected_revision: int,
        expected_artifact_fingerprint: str,
        expected_evidence_fingerprint: str,
        acknowledged_warning_ids: list[str],
        rationale: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if gate_id not in REVIEW_GATES:
            raise not_found("analysis review gate")
        states = {
            "approve": "approved",
            "reject": "rejected",
            "request_changes": "changes_requested",
        }
        state = states.get(decision)
        if state is None:
            raise ServiceError(422, "REVIEW_DECISION_INVALID", "The decision is invalid.")
        rationale = rationale.strip() if isinstance(rationale, str) else ""
        if (
            not rationale
            or len(rationale) > 4000
            or any(ord(character) < 32 and character != "\t" for character in rationale)
        ):
            raise ServiceError(
                422,
                "REVIEW_RATIONALE_REQUIRED",
                "A rationale is required for this review decision.",
            )
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            self._assert_run_approval_current(
                session,
                run=run,
                check_correction_set=False,
            )
            idempotent = session.scalar(
                select(AnalysisReviewDecisionRow).where(
                    AnalysisReviewDecisionRow.run_id == run_id,
                    AnalysisReviewDecisionRow.gate_id == gate_id,
                    AnalysisReviewDecisionRow.idempotency_key == idempotency_key,
                )
            )
            if idempotent is not None:
                if (
                    idempotent.state != state
                    or idempotent.revision != expected_revision + 1
                    or idempotent.artifact_fingerprint != expected_artifact_fingerprint
                    or idempotent.evidence_fingerprint != expected_evidence_fingerprint
                    or idempotent.rationale != rationale
                    or parse_json(idempotent.warning_acknowledgements_json, [])
                    != acknowledged_warning_ids
                ):
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was used for another gate decision.",
                    )
                current = self._latest_gate(
                    session,
                    run_id=run_id,
                    gate_id=gate_id,
                )
                if current is None:
                    raise not_found("analysis review gate")
                return (
                    self._gate_review_dict(session, gate=current),
                    self._gate_decision_dict(session, gate=idempotent),
                )
            current = self._latest_gate(session, run_id=run_id, gate_id=gate_id)
            if current is None:
                raise not_found("analysis review gate")
            if (
                current.revision != expected_revision
                or current.artifact_fingerprint != expected_artifact_fingerprint
                or current.evidence_fingerprint != expected_evidence_fingerprint
            ):
                raise ServiceError(
                    409,
                    "REVIEW_EVIDENCE_CONFLICT",
                    "The governed review evidence changed; refresh before deciding.",
                    details={"currentRevision": current.revision},
                )
            if state == "approved" and not self._gate_eligible(
                session,
                gate=current,
            ):
                raise ServiceError(
                    409,
                    "REVIEW_GATE_NOT_ELIGIBLE",
                    "Required upstream review gates are not approved.",
                )
            known_warning_ids = set(
                self._gate_warning_ids(
                    session,
                    run_id=run_id,
                    gate_id=gate_id,
                )
            )
            supplied_warning_ids = set(acknowledged_warning_ids)
            if not supplied_warning_ids.issubset(known_warning_ids):
                raise ServiceError(
                    409,
                    "REVIEW_WARNING_CONFLICT",
                    "A warning acknowledgement no longer matches this candidate.",
                )
            if state == "approved" and known_warning_ids - supplied_warning_ids:
                raise ServiceError(
                    409,
                    "REVIEW_WARNINGS_UNACKNOWLEDGED",
                    "Acknowledge the candidate warnings before approval.",
                )
            now = utc_now()
            row = AnalysisReviewDecisionRow(
                id=new_id(),
                project_id=project_id,
                run_id=run_id,
                snapshot_id=current.snapshot_id,
                gate_id=gate_id,
                revision=current.revision + 1,
                state=state,
                artifact_fingerprint=current.artifact_fingerprint,
                evidence_fingerprint=current.evidence_fingerprint,
                eligible=self._gate_eligible(session, gate=current),
                rationale=rationale,
                warning_acknowledgements_json=canonical_json(acknowledged_warning_ids),
                provenance_json=canonical_json(
                    {
                        "origin": "human",
                        "actorId": "local_user",
                        "inputArtifactFingerprint": current.artifact_fingerprint,
                        "inputEvidenceFingerprint": current.evidence_fingerprint,
                    }
                ),
                actor_id="local_user",
                idempotency_key=idempotency_key,
                supersedes_decision_id=current.id,
                decided_at=now,
                created_at=now,
            )
            session.add(row)
            session.flush()
            if gate_id != "whole_book_analysis_review" and state != "approved":
                downstream = self._latest_gate(
                    session,
                    run_id=run_id,
                    gate_id="whole_book_analysis_review",
                )
                if downstream is not None and downstream.state == "approved":
                    downstream_artifact_fingerprint = request_fingerprint(
                        {
                            "priorArtifactFingerprint": (downstream.artifact_fingerprint),
                            "supersedingDecisionId": row.id,
                            "supersedingDecisionState": state,
                        }
                    )
                    downstream_evidence_fingerprint = request_fingerprint(
                        {
                            "priorEvidenceFingerprint": (downstream.evidence_fingerprint),
                            "supersedingDecisionId": row.id,
                            "supersedingDecisionState": state,
                        }
                    )
                    session.add(
                        AnalysisReviewDecisionRow(
                            id=new_id(),
                            project_id=project_id,
                            run_id=run_id,
                            snapshot_id=downstream.snapshot_id,
                            gate_id="whole_book_analysis_review",
                            revision=downstream.revision + 1,
                            state="invalidated",
                            artifact_fingerprint=(downstream_artifact_fingerprint),
                            evidence_fingerprint=(downstream_evidence_fingerprint),
                            eligible=False,
                            rationale=(
                                "An upstream human review decision no longer "
                                "approves this snapshot."
                            ),
                            warning_acknowledgements_json="[]",
                            provenance_json=canonical_json(
                                {
                                    "origin": "human",
                                    "actorId": "local_user",
                                    "reviewDecisionId": row.id,
                                }
                            ),
                            actor_id="local_user",
                            idempotency_key=None,
                            supersedes_decision_id=downstream.id,
                            decided_at=now,
                            created_at=now,
                        )
                    )
            return (
                self._gate_review_dict(session, gate=row),
                self._gate_decision_dict(session, gate=row),
            )
