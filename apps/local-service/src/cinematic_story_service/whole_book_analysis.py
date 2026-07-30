from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

from .errors import ServiceError
from .util import canonical_json, request_fingerprint, sha256_text, stable_id

ANALYSIS_CONTRACT_VERSION: Final = "2.0.0"
ANALYSIS_PROFILE_ID: Final = "whole-book-intelligence-v1"
ANALYSIS_PROFILE_VERSION: Final = "1.0.0"
ANALYSIS_PRODUCER_ID: Final = "whole-book-analysis-orchestrator"
ANALYSIS_PRODUCER_SEMANTIC_VERSION: Final = "1.0.0"
ANALYSIS_PRODUCER_VERSION: Final = f"{ANALYSIS_PRODUCER_ID}@{ANALYSIS_PRODUCER_SEMANTIC_VERSION}"
MAX_ANALYSIS_PAGE_SIZE: Final = 200
DEFAULT_ANALYSIS_PAGE_SIZE: Final = 50
MAX_EVIDENCE_SPANS: Final = 16
MAX_SPEAKER_CANDIDATES: Final = 8
MAX_EVIDENCE_EXCERPT: Final = 512
MAX_EXACT_TEXT_CODE_POINTS: Final = 16_384
MAX_WARNINGS_PER_ENTITY: Final = 32
MAX_ANALYSIS_WORDS: Final = 150_000
MAX_ANALYSIS_ENTITIES: Final = 250_000
MAX_AGENT_ENVELOPE_BYTES: Final = 32_768
MAX_WHOLE_BOOK_CHECKPOINT_BYTES: Final = 64 * 1024 * 1024
MAX_SNAPSHOT_STAGES: Final = 5
STRUCTURE_RESUME_SCHEMA_VERSION: Final = 1
MAX_STRUCTURE_RESUME_BYTES: Final = 4 * 1024 * 1024

ENTITY_COLLECTIONS: Final = (
    "chapters",
    "scenes",
    "beats",
    "characters",
    "mentions",
    "dialogue-lines",
    "narration-spans",
    "pov-segments",
    "locations",
    "timeline-events",
    "temporal-constraints",
    "relationships",
    "emotional-states",
    "dramatic-intents",
    "continuity-findings",
)

REVIEW_GATES: Final = (
    "story_structure_review",
    "character_registry_review",
    "dialogue_attribution_review",
    "whole_book_analysis_review",
)

CORRECTION_CATEGORIES: Final = (
    "structure_boundary",
    "structure_label",
    "character_identity",
    "character_alias",
    "character_merge",
    "character_split",
    "mention_resolution",
    "dialogue_speaker",
    "point_of_view",
    "location_identity",
    "location_alias",
    "temporal_order",
    "relationship",
    "emotional_state",
    "dramatic_intent",
    "continuity_disposition",
)


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    role: str
    agent_id: str
    version: str
    collections: tuple[str, ...]


AGENT_REGISTRY: Final = (
    AgentDefinition("structure", "story-structure", "1.0.0", ("chapters", "scenes")),
    AgentDefinition(
        "beats",
        "story-beats",
        "1.0.0",
        ("beats", "narration-spans"),
    ),
    AgentDefinition(
        "character_identity",
        "character-identity",
        "1.0.0",
        ("characters", "mentions"),
    ),
    AgentDefinition(
        "dialogue_attribution",
        "dialogue-attribution",
        "1.0.0",
        ("dialogue-lines",),
    ),
    AgentDefinition("point_of_view", "point-of-view", "1.0.0", ("pov-segments",)),
    AgentDefinition("setting", "story-setting", "1.0.0", ("locations",)),
    AgentDefinition(
        "timeline",
        "story-timeline",
        "1.0.0",
        ("timeline-events", "temporal-constraints"),
    ),
    AgentDefinition(
        "relationships",
        "character-relationships",
        "1.0.0",
        ("relationships",),
    ),
    AgentDefinition(
        "emotion_intent",
        "emotion-dramatic-intent",
        "1.0.0",
        ("emotional-states", "dramatic-intents"),
    ),
    AgentDefinition(
        "continuity",
        "story-continuity",
        "1.0.0",
        ("continuity-findings",),
    ),
    AgentDefinition("synthesis", "analysis-synthesis", "1.0.0", ()),
)
AGENT_REGISTRY_FINGERPRINT: Final = request_fingerprint(
    [
        {
            "role": value.role,
            "agentId": value.agent_id,
            "version": value.version,
            "collections": list(value.collections),
        }
        for value in AGENT_REGISTRY
    ]
)


@dataclass(frozen=True, slots=True)
class AnalysisProfile:
    profile_id: str = ANALYSIS_PROFILE_ID
    semantic_version: str = ANALYSIS_PROFILE_VERSION

    def to_wire(self) -> dict[str, Any]:
        return {
            "agentVersions": [
                {"agentId": value.agent_id, "version": value.version} for value in AGENT_REGISTRY
            ],
            "analysisContractVersion": ANALYSIS_CONTRACT_VERSION,
            "confidenceClassification": {
                "high": {"maximumInclusive": 1, "minimumInclusive": 0.85},
                "low": {"maximumExclusive": 0.75, "minimumExclusive": 0},
                "medium": {"maximumExclusive": 0.85, "minimumInclusive": 0.75},
                "unknown": {"score": 0},
            },
            "deterministic": True,
            "limits": {
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
            },
            "offsetUnit": "unicode-code-point",
            "producer": {
                "producerId": ANALYSIS_PRODUCER_ID,
                "producerVersion": ANALYSIS_PRODUCER_SEMANTIC_VERSION,
            },
            "profileId": self.profile_id,
            "semanticVersion": self.semantic_version,
        }

    @property
    def fingerprint(self) -> str:
        return request_fingerprint(self.to_wire())


DEFAULT_ANALYSIS_PROFILE: Final = AnalysisProfile()


class AnalysisCancelled(RuntimeError):
    """Raised when a cooperative whole-book cancellation is observed."""


_CHAPTER = re.compile(
    r"(?im)^[ \t]*(#{1,6}[ \t]+)?(chapter[ \t]+(?:[0-9ivxlcdm]+|[^\r\n:]+)"
    r"(?:[ \t]*:[ \t]*[^\r\n]+)?)[ \t]*$"
)
_SCENE = re.compile(
    r"(?im)^[ \t]*(?:(?:#{1,6})[ \t]+)?"
    r"((?:scene[ \t]+[^\r\n]+)|(?:(?:int|ext|int/ext|ext/int)\.[^\r\n]+))[ \t]*$"
)
_SEPARATOR = re.compile(r"(?m)^[ \t]*(?:\*{3,}|-{3,})[ \t]*(?:\r?\n|$)")
_QUOTE = re.compile(r'"([^"]+)"|“([^”]+)”')
_PREFIX_SPEAKER = re.compile(
    r"([A-Z][A-Za-z0-9'’-]*(?:[ \t]+[A-Z][A-Za-z0-9'’-]*){0,3})"
    r"[ \t]*:[ \t]*$"
)
_SUFFIX_NAME_VERB = re.compile(
    r"^[ \t]*[,;.!?—-]*[ \t]*"
    r"([A-Z][A-Za-z'’-]*(?:[ \t]+[A-Z][A-Za-z'’-]*){0,3})[ \t]+"
    r"(?i:said|asked|replied|answered|whispered|murmured|called|shouted|cried|added|told)\b"
)
_SUFFIX_VERB_NAME = re.compile(
    r"^[ \t]*[,;.!?—-]*[ \t]*"
    r"(?i:said|asked|replied|answered|whispered|murmured|called|shouted|cried|added|told)"
    r"[ \t]+([A-Z][A-Za-z'’-]*(?:[ \t]+[A-Z][A-Za-z'’-]*){0,3})\b"
)
_SCREENPLAY_LABEL = re.compile(
    r"(?m)^[ \t]*([A-Z][A-Z0-9'’-]*(?:[ \t]+[A-Z][A-Z0-9'’-]*){0,3})[ \t]*\r?\n"
    r"[ \t]*$"
)
_ALIAS = re.compile(
    r"\b([A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’-]+){1,3})"
    r"(?:,[ \t]*)?(?:known as|called|nicknamed)[ \t]+[\"“]?([A-Z][A-Za-z'’-]+)[\"”]?"
)
_CHARACTER_TITLE = (
    r"(?:Captain|Engineer|Inspector|Keeper|Archivist|Professor|Doctor|Dr\.|Mr\.|Ms\.|Mrs\.)"
)
_TITLED_CHARACTER = re.compile(
    rf"\b(?P<title>{_CHARACTER_TITLE})[ \t\r\n]+"
    r"(?P<first>[A-Z][A-Za-z'’-]+)[ \t\r\n]+(?P<last>[A-Z][A-Za-z'’-]+)\b"
)
_TWO_PART_NAME = re.compile(
    r"\b(?P<first>[A-Z][A-Za-z'’-]+)[ \t\r\n]+"
    r"(?P<last>[A-Z][A-Za-z'’-]+)\b"
)
_KNOWN_AS = re.compile(
    rf"\b(?:(?P<title>{_CHARACTER_TITLE})[ \t\r\n]+)?"
    r"(?P<first>[A-Z][A-Za-z'’-]+)[ \t\r\n]+(?P<last>[A-Z][A-Za-z'’-]+)"
    r"(?:,[ \t\r\n]*)?(?:also[ \t]+known[ \t]+as|known[ \t]+as|called|nicknamed)"
    rf"[ \t\r\n]+(?:(?P<alias_title>{_CHARACTER_TITLE})[ \t\r\n]+)?"
    r"(?P<alias>[A-Z][A-Za-z'’-]+)\b"
)
_LOCATION_ALIAS = re.compile(
    r"\b([A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’-]+){0,3}),[ \t]+"
    r"(?:also called|known locally as)[ \t]+[\"“]?([A-Z][A-Za-z'’-]+)[\"”]?"
)
_LOCATION_NARRATIVE = re.compile(
    r"\b(?:At|Inside|Within|Into|Toward|Towards|at|inside|within|into|toward|towards|"
    r"crossed|climbed|entered|in)[ \t\r\n]+(?:the[ \t\r\n]+)?"
    r"([A-Z][A-Za-z'’-]+(?:[ \t\r\n]+[A-Z][A-Za-z'’-]+){0,3})\b"
)
_TEMPORAL_SIGNAL = re.compile(
    r"(?i)\b(?:years?|months?|weeks?|days?|hours?|minutes?)[ \t]+"
    r"(?:earlier|before|later|after)\b|"
    r"\b(?:flashback|flashforward|previously|meanwhile|at the same time|"
    r"the next day|tomorrow|yesterday|back in the present)\b"
)
_EXPLICIT_DATE_TIME = re.compile(
    r"(?i)\b(?:"
    r"(?:at[ \t]+)?(?:[01]?\d|2[0-3])(?::[0-5]\d)(?:[ \t]*(?:a\.?m\.?|p\.?m\.?))?|"
    r"(?:at[ \t]+)?(?:midnight|noon|dawn|dusk)|"
    r"(?:[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"[ \t\r\n]+(?:minutes?|hours?)[ \t\r\n]+(?:before|after)[ \t\r\n]+"
    r"(?:midnight|noon|dawn|dusk)|"
    r"(?:on[ \t]+)?(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)[ \t]+\d{1,2}(?:,[ \t]+\d{4})?|"
    r"\d{4}-\d{2}-\d{2}"
    r")\b"
)
_BACKWARD_TIME = re.compile(
    r"(?i)\b(?:years?|months?|weeks?|days?|hours?|minutes?)[ \t]+earlier\b|"
    r"\b(?:flashback|previously|yesterday)\b"
)
_FORWARD_TIME = re.compile(
    r"(?i)\b(?:years?|months?|weeks?|days?|hours?|minutes?)[ \t]+later\b|"
    r"\b(?:flashforward|the next day|tomorrow|back in the present)\b"
)
_SIMULTANEOUS_TIME = re.compile(r"(?i)\b(?:meanwhile|at the same time)\b")
_RELATIONSHIP = re.compile(
    r"(?i)\b([A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’-]+){0,2})[ \t]+"
    r"(loved|hated|trusted|distrusted|betrayed|protected|feared|admired|followed|left)"
    r"[ \t]+([A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’-]+){0,2})\b"
)
_KINSHIP = re.compile(
    r"(?i)\b([A-Z][A-Za-z'’-]+)'s[ \t]+"
    r"(mother|father|sister|brother|daughter|son|partner|friend|enemy)\b"
)
_PROPERTY = re.compile(
    r"(?i)\b([A-Z][A-Za-z'’-]+)[ \t]+"
    r"(?:wore|carried|held)[ \t]+(?:a|an|the)?[ \t]*"
    r"(red|blue|green|black|white|silver|golden)[ \t]+([A-Za-z'’-]+)\b"
)
_SENTENCE = re.compile(r"[^.!?\r\n]+[.!?]?")
_OBJECT_DAMAGE = re.compile(
    r"(?is)\b(?P<object>lantern|door|window|glass|mirror|blade|weapon|vehicle|bridge)\b"
    r"(?P<context>.{0,180}?\b(?:shattered|broken|destroyed|torn|smashed|cracked)\b)"
)
_OBJECT_RESTORED = re.compile(
    r"(?is)\b(?:(?:same|the)[ \t\r\n]+)?"
    r"(?P<object>lantern|door|window|glass|mirror|blade|weapon|vehicle|bridge)\b"
    r"(?P<context>.{0,180}?\b(?:unbroken|whole|intact|restored|repaired)\b)"
)
_PRONOUN = re.compile(r"(?i)\b(?:he|him|his|she|her|hers|they|them|their|theirs)\b")

_NON_CHARACTER_NAME_TERMS: Final = frozenset(
    {
        "archive vault",
        "back in",
        "borrowed hours",
        "chapter one",
        "chapter three",
        "chapter two",
        "clock room",
        "dawn switchyard",
        "flooded concourse",
        "north signal",
        "north signal tower",
        "platform glass",
        "relay platform",
        "scene five",
        "scene four",
        "scene one",
        "scene six",
        "scene three",
        "scene two",
        "the clock",
        "the quiet",
    }
)
_NON_LOCATION_TERMS: Final = frozenset(
    {
        "back",
        "dawn",
        "everyone",
        "her",
        "him",
        "midnight",
        "no one",
        "present",
        "the present",
        "them",
    }
)

_EMOTIONS: Final[dict[str, tuple[str, ...]]] = {
    "fear": ("afraid", "fear", "frightened", "terrified", "dread"),
    "anger": ("angry", "furious", "rage", "irritated"),
    "sadness": ("sad", "grief", "wept", "sorrow", "mourned"),
    "joy": ("happy", "joy", "smiled", "delighted", "laughed"),
    "surprise": ("surprised", "startled", "astonished", "shocked"),
    "calm": ("calm", "relieved", "peaceful", "settled"),
}
_BEAT_REVELATION = re.compile(
    r"(?i)\b(?:admit(?:ted|s)?|discover(?:ed|s)?|learn(?:ed|s)?|"
    r"realiz(?:ed|es)|reveal(?:ed|s)?|the truth|the secret|turned out)\b"
)
_BEAT_TRANSITION = re.compile(
    r"(?i)\b(?:meanwhile|later|earlier|previously|the next day|"
    r"tomorrow|yesterday|back in the present|at dawn|at dusk|at midnight)\b"
)
_BEAT_ACTION = re.compile(
    r"(?i)\b(?:arrived|caught|climbed|closed|crossed|entered|fled|"
    r"gathered|grabbed|left|locked|lowered|opened|raised|reached|reset|"
    r"ran|set|slipped|stood|turned|walked|waited)\b"
)
_BEAT_DESCRIPTION = re.compile(
    r"(?i)\b(?:appeared|became|felt|hung|lay|looked|remained|seemed|"
    r"was|were)\b"
)
_URGENCY_CUE = re.compile(
    r"(?i)\b(?:at once|before it(?:'|’)?s too late|cannot wait|"
    r"can(?:'|’)?t wait|hurry|immediately|now|quickly|urgent(?:ly)?)\b"
)
_RESTRAINT_CUE = re.compile(
    r"(?i)\b(?:calmly|controlled|kept (?:her|his|their) voice even|"
    r"lowered (?:her|his|their) voice|quietly|restrained|said nothing|"
    r"stayed silent|withheld)\b"
)
_SUBTEXT_CUE = re.compile(
    r"(?i)\b(?:although|but|despite|hesitated|instead|looked away|"
    r"paused|pretended|said nothing)\b"
)


@dataclass(frozen=True, slots=True)
class _Span:
    start: int
    end: int
    label: str | None = None
    parent: int | None = None


@dataclass(frozen=True, slots=True)
class _DialogueClaim:
    start: int
    end: int
    quote_start: int
    quote_end: int
    speaker_name: str | None
    speaker_start: int | None
    score: float
    basis: str
    scene_index: int


@dataclass(slots=True)
class _CharacterSeed:
    key: str
    display: str
    normalized: str
    duplicate_index: int
    spans: list[tuple[int, int]]
    aliases: dict[str, tuple[str, str]]


def _structure_resume_error() -> ServiceError:
    return ServiceError(
        409,
        "CHECKPOINT_INCOMPATIBLE",
        "The saved structure-stage checkpoint is incompatible.",
        retryable=False,
    )


def _encode_structure_resume_artifact(
    *,
    chapters: list[_Span],
    scenes: list[_Span],
    input_fingerprint: str,
    profile_fingerprint: str,
    correction_set_fingerprint: str,
) -> dict[str, Any] | None:
    payload_json = canonical_json(
        {
            "schemaVersion": STRUCTURE_RESUME_SCHEMA_VERSION,
            "producerVersion": ANALYSIS_PRODUCER_VERSION,
            "inputFingerprint": input_fingerprint,
            "profileFingerprint": profile_fingerprint,
            "correctionSetFingerprint": correction_set_fingerprint,
            "chapters": [[span.start, span.end, span.label, span.parent] for span in chapters],
            "scenes": [[span.start, span.end, span.label, span.parent] for span in scenes],
        }
    )
    raw = payload_json.encode("utf-8")
    if len(raw) > MAX_STRUCTURE_RESUME_BYTES:
        return None
    compressed = zlib.compress(raw, level=9)
    artifact = {
        "encoding": "zlib-base64",
        "payloadSha256": sha256_text(payload_json),
        "data": base64.b64encode(compressed).decode("ascii"),
    }
    if len(canonical_json(artifact).encode("utf-8")) > MAX_AGENT_ENVELOPE_BYTES // 2:
        return None
    return artifact


def decode_structure_resume_artifact(
    artifact: dict[str, Any],
    *,
    text_length: int,
    input_fingerprint: str,
    profile_fingerprint: str,
    correction_set_fingerprint: str,
) -> tuple[list[_Span], list[_Span]]:
    """Validate and decode the bounded, frozen structure-stage result."""

    try:
        if (
            artifact.get("encoding") != "zlib-base64"
            or not isinstance(artifact.get("payloadSha256"), str)
            or not isinstance(artifact.get("data"), str)
        ):
            raise _structure_resume_error()
        compressed = base64.b64decode(
            str(artifact["data"]).encode("ascii"),
            validate=True,
        )
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(
            compressed,
            MAX_STRUCTURE_RESUME_BYTES + 1,
        )
        if (
            len(raw) > MAX_STRUCTURE_RESUME_BYTES
            or decompressor.unconsumed_tail
            or decompressor.unused_data
        ):
            raise _structure_resume_error()
        raw += decompressor.flush()
        if len(raw) > MAX_STRUCTURE_RESUME_BYTES:
            raise _structure_resume_error()
        payload_json = raw.decode("utf-8")
        if sha256_text(payload_json) != artifact["payloadSha256"]:
            raise _structure_resume_error()
        value = json.loads(payload_json)
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        zlib.error,
    ) as exc:
        raise _structure_resume_error() from exc
    if not isinstance(value, dict) or (
        value.get("schemaVersion") != STRUCTURE_RESUME_SCHEMA_VERSION
        or value.get("producerVersion") != ANALYSIS_PRODUCER_VERSION
        or value.get("inputFingerprint") != input_fingerprint
        or value.get("profileFingerprint") != profile_fingerprint
        or value.get("correctionSetFingerprint") != correction_set_fingerprint
    ):
        raise _structure_resume_error()

    def decode_spans(raw_spans: object, *, scenes: bool) -> list[_Span]:
        if not isinstance(raw_spans, list) or not raw_spans:
            raise _structure_resume_error()
        result: list[_Span] = []
        prior_start = -1
        for raw_span in raw_spans:
            if (
                not isinstance(raw_span, list)
                or len(raw_span) != 4
                or not isinstance(raw_span[0], int)
                or isinstance(raw_span[0], bool)
                or not isinstance(raw_span[1], int)
                or isinstance(raw_span[1], bool)
                or raw_span[2] is not None
                and not isinstance(raw_span[2], str)
                or raw_span[3] is not None
                and (not isinstance(raw_span[3], int) or isinstance(raw_span[3], bool))
            ):
                raise _structure_resume_error()
            start, end, label, parent = raw_span
            if (
                start < 0
                or start < prior_start
                or end <= start
                or end > text_length
                or (scenes and (parent is None or parent < 0))
                or (not scenes and parent is not None)
            ):
                raise _structure_resume_error()
            prior_start = start
            result.append(_Span(start, end, label, parent))
        return result

    chapters = decode_spans(value.get("chapters"), scenes=False)
    scenes = decode_spans(value.get("scenes"), scenes=True)
    if any(scene.parent is None or scene.parent >= len(chapters) for scene in scenes):
        raise _structure_resume_error()
    if any(
        not any(scene.parent == chapter_index for scene in scenes)
        for chapter_index in range(len(chapters))
    ):
        raise _structure_resume_error()
    return chapters, scenes


def confidence_class(score: float) -> Literal["unknown", "low", "medium", "high"]:
    if score <= 0:
        return "unknown"
    if score < 0.75:
        return "low"
    if score < 0.85:
        return "medium"
    return "high"


def confidence(score: float, basis: str) -> dict[str, Any]:
    bounded = min(1.0, max(0.0, score))
    return {
        "score": bounded,
        "classification": confidence_class(bounded),
        "basis": basis,
        "calibrationId": "governed-local-rules-v1",
    }


def warning(
    code: str,
    message: str,
    *,
    requires_human_review: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "warning",
        "message": message,
        "requiresHumanReview": requires_human_review,
    }


def _classify_beat(
    source_text: str,
    component_kind: str,
) -> tuple[str, str, float, str, list[dict[str, Any]]]:
    if component_kind == "dialogue":
        stripped = source_text.strip()
        if stripped.endswith("?"):
            return (
                "dialogue",
                "Dialogue beat; bounded scene-purpose signal: asks a question.",
                0.92,
                "explicit_question_form",
                [],
            )
        if _BEAT_REVELATION.search(stripped):
            return (
                "dialogue",
                "Revelation dialogue beat; bounded scene-purpose signal: "
                "states newly disclosed information.",
                0.88,
                "explicit_revelation_lexeme",
                [],
            )
        if re.match(
            r'(?i)^["“]?(?:close|give|go|hide|hold|leave|listen|look|'
            r"open|run|stop|take|tell|wait)\b",
            stripped,
        ):
            return (
                "dialogue",
                "Dialogue beat; bounded scene-purpose signal: issues a direct command.",
                0.9,
                "explicit_imperative_form",
                [],
            )
        return (
            "dialogue",
            "Dialogue beat; scene purpose remains unknown from bounded form evidence.",
            0.0,
            "insufficient_dialogue_purpose_evidence",
            [],
        )

    if _BEAT_TRANSITION.search(source_text):
        return (
            "transition",
            "Transition beat; bounded scene-purpose signal: reorients explicit story time.",
            0.92,
            "explicit_temporal_transition",
            [],
        )
    if _BEAT_REVELATION.search(source_text):
        return (
            "narration",
            "Revelation narration beat; bounded scene-purpose signal: "
            "exposes explicitly stated information.",
            0.88,
            "explicit_revelation_lexeme",
            [],
        )
    if _BEAT_ACTION.search(source_text):
        return (
            "action",
            "Action beat; bounded scene-purpose signal: advances an explicit physical action.",
            0.86,
            "explicit_action_verb",
            [],
        )
    if _BEAT_DESCRIPTION.search(source_text):
        return (
            "description",
            "Description beat; bounded scene-purpose signal: establishes an explicit state.",
            0.8,
            "explicit_descriptive_state",
            [],
        )
    return (
        "narration",
        "Unclassified narration beat; scene purpose remains unknown.",
        0.0,
        "insufficient_beat_purpose_evidence",
        [
            warning(
                "BEAT_PURPOSE_UNKNOWN",
                "Narration does not contain a bounded action, description, "
                "revelation, or transition cue.",
            )
        ],
    )


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _structural_spans(text: str) -> tuple[list[_Span], list[_Span]]:
    chapter_matches = list(_CHAPTER.finditer(text))
    chapters: list[_Span] = []
    if not chapter_matches:
        chapters.append(_Span(0, len(text), None))
    else:
        if text[: chapter_matches[0].start()].strip():
            chapters.append(_Span(0, chapter_matches[0].start(), None))
        for index, match in enumerate(chapter_matches):
            end = (
                chapter_matches[index + 1].start()
                if index + 1 < len(chapter_matches)
                else len(text)
            )
            chapters.append(_Span(match.start(), end, match.group(2).strip()))

    scenes: list[_Span] = []
    for chapter_index, chapter in enumerate(chapters):
        scene_matches = list(_SCENE.finditer(text, chapter.start, chapter.end))
        starts: list[tuple[int, str | None]] = [
            (match.start(), match.group(1).strip()) for match in scene_matches
        ]
        if scene_matches:
            preface_start, preface_end = _trim(
                text,
                chapter.start,
                scene_matches[0].start(),
            )
            chapter_heading = _CHAPTER.match(text, preface_start, preface_end)
            if (
                preface_start < preface_end
                and chapter_heading is None
                and text[preface_start:preface_end].strip()
            ):
                starts.insert(0, (chapter.start, None))
        else:
            starts.append((chapter.start, None))
            for separator in _SEPARATOR.finditer(text, chapter.start, chapter.end):
                starts.append((separator.end(), None))
        starts.sort(key=lambda value: value[0])
        deduplicated: list[tuple[int, str | None]] = []
        for start, label in starts:
            if deduplicated and deduplicated[-1][0] == start:
                if label is not None:
                    deduplicated[-1] = (start, label)
            else:
                deduplicated.append((start, label))
        for index, (start, label) in enumerate(deduplicated):
            end = deduplicated[index + 1][0] if index + 1 < len(deduplicated) else chapter.end
            content_start, content_end = _trim(text, start, end)
            if content_start < content_end:
                scenes.append(_Span(start, end, label, chapter_index))
        if not any(value.parent == chapter_index for value in scenes):
            scenes.append(_Span(chapter.start, chapter.end, None, chapter_index))
    return chapters, scenes


def _speaker_for_quote(
    text: str,
    *,
    scene_start: int,
    scene_end: int,
    quote_start: int,
    quote_end: int,
) -> tuple[str | None, int | None, float, str]:
    line_start = max(
        text.rfind("\n", scene_start, quote_start),
        text.rfind("\r", scene_start, quote_start),
    )
    line_start = scene_start if line_start < 0 else line_start + 1
    line_ends = [
        value
        for value in (
            text.find("\n", quote_end, scene_end),
            text.find("\r", quote_end, scene_end),
        )
        if value >= 0
    ]
    line_end = min(line_ends) if line_ends else scene_end
    prefix = text[line_start:quote_start]
    suffix = text[quote_end:line_end]
    prefix_match = _PREFIX_SPEAKER.search(prefix)
    if prefix_match is not None:
        return (
            " ".join(prefix_match.group(1).split()),
            line_start + prefix_match.start(1),
            0.99,
            "explicit_speaker_tag",
        )
    preceding = text[max(scene_start, line_start - 120) : quote_start]
    screenplay = list(_SCREENPLAY_LABEL.finditer(preceding))
    if screenplay:
        value = screenplay[-1]
        return (
            " ".join(part.title() for part in value.group(1).split()),
            max(scene_start, line_start - 120) + value.start(1),
            0.96,
            "screenplay_speaker_tag",
        )
    for pattern, basis in (
        (_SUFFIX_NAME_VERB, "explicit_name_speech_verb"),
        (_SUFFIX_VERB_NAME, "speech_verb_explicit_name"),
    ):
        match = pattern.search(suffix)
        if match is not None:
            return (
                " ".join(match.group(1).split()),
                quote_end + match.start(1),
                0.94,
                basis,
            )
    preceding_sentence = re.search(
        r"(?:^|[.!?][ \t\r\n]+)"
        r"([A-Z][A-Za-z'’-]*(?:[ \t]+[A-Z][A-Za-z'’-]*){0,2})"
        r"[ \t]+([^.!?]{0,120})[.!?][ \t\r\n]*$",
        preceding,
    )
    if preceding_sentence is not None:
        action = preceding_sentence.group(2)
        if re.search(
            r"(?i)\b(?:set|lowered|turned|looked|nodded|reached|steadied)\b",
            action,
        ) and not re.search(
            r"\b[A-Z][A-Za-z'’-]+[ \t]+and[ \t]+[A-Z][A-Za-z'’-]+\b",
            preceding_sentence.group(0),
        ):
            return (
                " ".join(preceding_sentence.group(1).split()),
                max(scene_start, line_start - 120) + preceding_sentence.start(1),
                0.86,
                "adjacent_named_speech_action",
            )
    return None, None, 0.0, "unknown_speaker"


def _dialogues(text: str, scenes: list[_Span]) -> list[_DialogueClaim]:
    result: list[_DialogueClaim] = []
    for scene_index, scene in enumerate(scenes):
        for match in _QUOTE.finditer(text, scene.start, scene.end):
            preceding = text[max(scene.start, match.start() - 96) : match.start()].casefold()
            following = text[match.end() : min(scene.end, match.end() + 96)].casefold()
            if re.search(
                r"(?:plaque|sign|notice|letter|page|screen|inscription)[^.!?\r\n]{0,48}"
                r"(?:read|said|showed|displayed)[ \t]*$",
                preceding,
            ) or re.search(
                r"^[^.!?\r\n]{0,64}\b(?:words?|instruction|inscription|quotation)\b",
                following,
            ):
                continue
            if re.search(
                r"\b(?:called|named|nicknamed|dubbed|labeled|labelled|referred[ \t]+to[ \t]+as)"
                r"[^.!?\r\n]{0,48}$",
                preceding,
            ):
                continue
            group = 1 if match.group(1) is not None else 2
            start, end = match.start(group), match.end(group)
            speaker_name, speaker_start, score, basis = _speaker_for_quote(
                text,
                scene_start=scene.start,
                scene_end=scene.end,
                quote_start=match.start(),
                quote_end=match.end(),
            )
            result.append(
                _DialogueClaim(
                    start,
                    end,
                    match.start(),
                    match.end(),
                    speaker_name,
                    speaker_start,
                    score,
                    basis,
                    scene_index,
                )
            )
    return result


def _entity(
    *,
    input_fingerprint: str,
    collection: str,
    ordinal: int,
    identity_key: str,
    payload: dict[str, Any],
    score: float,
    basis: str,
    evidence: list[dict[str, Any]],
    start: int | None = None,
    end: int | None = None,
    parent_id: str | None = None,
    warnings: list[dict[str, Any]] | None = None,
    entity_id: str | None = None,
) -> dict[str, Any]:
    semantic_id = entity_id or stable_id(input_fingerprint, collection, identity_key)
    semantic = {
        "entityId": semantic_id,
        "collection": collection,
        "ordinal": ordinal,
        "identityKey": identity_key,
        "parentEntityId": parent_id,
        "startOffset": start,
        "endOffset": end,
        "payload": payload,
        "confidence": confidence(score, basis),
        "warnings": warnings or [],
        "evidence": evidence[:MAX_EVIDENCE_SPANS],
    }
    semantic["fingerprint"] = request_fingerprint(semantic)
    return semantic


def _evidence(start: int, end: int, basis: str, score: float) -> dict[str, Any]:
    return {
        "startOffset": start,
        "endOffset": end,
        "basis": basis,
        "confidence": confidence(score, basis),
    }


def analyze_whole_book(
    *,
    text: str,
    input_fingerprint: str,
    correction_set_fingerprint: str = "0" * 64,
    profile: AnalysisProfile = DEFAULT_ANALYSIS_PROFILE,
    stage_observer: Callable[[str, dict[str, Any]], None] | None = None,
    result_checkpoint_observer: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    maximum_entities: int = MAX_ANALYSIS_ENTITIES,
    registry_scope: str = "default",
    story_scope: str = "default",
    structure_resume_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce deterministic local whole-book claims without mutable or network state."""

    if sha256_text(text) != input_fingerprint:
        raise ValueError("The approved analysis input fingerprint does not match the exact text.")
    if not 1 <= maximum_entities <= MAX_ANALYSIS_ENTITIES:
        raise ValueError("The analysis entity budget is invalid.")
    collections: dict[str, list[dict[str, Any]]] = {name: [] for name in ENTITY_COLLECTIONS}
    guard_calls = 0

    def guard(*, force: bool = False) -> None:
        nonlocal guard_calls
        guard_calls += 1
        if not force and guard_calls % 128:
            return
        if should_cancel is not None and should_cancel():
            raise AnalysisCancelled
        entity_count = sum(len(values) for values in collections.values())
        if entity_count > maximum_entities:
            raise ServiceError(
                422,
                "ANALYSIS_ENTITY_LIMIT_EXCEEDED",
                "The whole-book analysis produced too many claims.",
                retryable=False,
            )

    word_count = 0
    for word_count, _match in enumerate(
        re.finditer(r"\S+", text),
        start=1,
    ):
        if word_count % 4096 == 0:
            guard(force=True)
        if word_count > MAX_ANALYSIS_WORDS:
            raise ServiceError(
                422,
                "ANALYSIS_WORD_LIMIT_EXCEEDED",
                "The manuscript exceeds the whole-book analysis word limit.",
                retryable=False,
            )

    def observe(
        role: str,
        names: tuple[str, ...],
        *,
        resume_artifact: dict[str, Any] | None = None,
    ) -> None:
        guard(force=True)
        if stage_observer is None:
            return
        payload: dict[str, Any] = {
            "role": role,
            "collections": {
                name: {
                    "count": len(collections[name]),
                    "fingerprint": request_fingerprint(collections[name]),
                }
                for name in names
            },
        }
        if resume_artifact is not None:
            payload["resumeArtifact"] = resume_artifact
        stage_observer(role, payload)

    resumed_structure = structure_resume_artifact is not None
    if structure_resume_artifact is None:
        chapters, scenes = _structural_spans(text)
    else:
        chapters, scenes = decode_structure_resume_artifact(
            structure_resume_artifact,
            text_length=len(text),
            input_fingerprint=input_fingerprint,
            profile_fingerprint=profile.fingerprint,
            correction_set_fingerprint=correction_set_fingerprint,
        )
    dialogues = _dialogues(text, scenes)

    chapter_ids = [
        stable_id(input_fingerprint, "chapters", f"{chapter.start}:{chapter.end}")
        for chapter in chapters
    ]
    scene_ids = [
        stable_id(input_fingerprint, "scenes", f"{scene.start}:{scene.end}") for scene in scenes
    ]
    for ordinal, value in enumerate(chapters):
        guard()
        child_scene_ids = [
            scene_ids[scene_index]
            for scene_index, scene in enumerate(scenes)
            if scene.parent == ordinal
        ]
        item = _entity(
            input_fingerprint=input_fingerprint,
            collection="chapters",
            ordinal=ordinal,
            identity_key=f"{value.start}:{value.end}",
            payload={
                "chapterId": chapter_ids[ordinal],
                "title": value.label,
                "firstSceneId": child_scene_ids[0],
                "lastSceneId": child_scene_ids[-1],
                "sceneCount": len(child_scene_ids),
            },
            score=0.98 if value.label else 0.72,
            basis="explicit_chapter_heading" if value.label else "whole_book_fallback",
            evidence=[_evidence(value.start, min(value.end, value.start + 160), "boundary", 0.98)],
            start=value.start,
            end=value.end,
        )
        collections["chapters"].append(item)

    for ordinal, value in enumerate(scenes):
        guard()
        parent_id = chapter_ids[value.parent or 0]
        item = _entity(
            input_fingerprint=input_fingerprint,
            collection="scenes",
            ordinal=ordinal,
            identity_key=f"{value.start}:{value.end}",
            payload={
                "sceneId": scene_ids[ordinal],
                "heading": value.label,
                "chapterId": parent_id,
                "boundaryKind": ("heading" if value.label else "inferred"),
                "firstBeatId": None,
                "lastBeatId": None,
                "beatCount": 0,
            },
            score=0.96 if value.label else 0.72,
            basis="explicit_scene_heading" if value.label else "structural_segment",
            evidence=[_evidence(value.start, min(value.end, value.start + 160), "boundary", 0.96)],
            start=value.start,
            end=value.end,
            parent_id=parent_id,
        )
        collections["scenes"].append(item)
    if not resumed_structure:
        observe(
            "structure",
            ("chapters", "scenes"),
            resume_artifact=_encode_structure_resume_artifact(
                chapters=chapters,
                scenes=scenes,
                input_fingerprint=input_fingerprint,
                profile_fingerprint=profile.fingerprint,
                correction_set_fingerprint=correction_set_fingerprint,
            ),
        )

    speaker_surfaces = [
        (
            dialogue_claim.speaker_name,
            dialogue_claim.speaker_start,
            dialogue_claim.speaker_start + len(dialogue_claim.speaker_name),
        )
        for dialogue_claim in dialogues
        if dialogue_claim.speaker_name is not None and dialogue_claim.speaker_start is not None
    ]
    speaker_tokens = {
        token.casefold()
        for speaker_surface, _speaker_start, _speaker_end in speaker_surfaces
        for token in speaker_surface.split()
    }
    character_seeds: dict[str, _CharacterSeed] = {}
    duplicate_counts: dict[str, int] = {}

    def add_character_seed(
        display: str,
        *,
        start: int,
        end: int,
        title: str | None = None,
        force_duplicate: bool = False,
    ) -> _CharacterSeed:
        normalized_display = " ".join(display.split())
        normalized_name = normalized_display.casefold()
        duplicate_index = duplicate_counts.get(normalized_name, 0)
        existing_key = (
            normalized_name if duplicate_index == 0 else f"{normalized_name}#{duplicate_index + 1}"
        )
        if not force_duplicate and normalized_name in character_seeds:
            existing = character_seeds[normalized_name]
            if (start, end) not in existing.spans:
                existing.spans.append((start, end))
            return existing
        if force_duplicate or existing_key in character_seeds:
            duplicate_index += 1
        duplicate_counts[normalized_name] = duplicate_index
        key = (
            normalized_name if duplicate_index == 0 else f"{normalized_name}#{duplicate_index + 1}"
        )
        parts = normalized_display.split()
        aliases: dict[str, tuple[str, str]] = {
            normalized_name: (normalized_display, "full_name"),
            parts[0].casefold(): (parts[0], "given_name"),
        }
        if len(parts) > 1:
            aliases[parts[-1].casefold()] = (parts[-1], "family_name")
        if title is not None:
            full_title = f"{title} {normalized_display}"
            surname_title = f"{title} {parts[-1]}"
            aliases[full_title.casefold()] = (full_title, "honorific")
            aliases[surname_title.casefold()] = (surname_title, "honorific")
            aliases[title.casefold().rstrip(".")] = (title.rstrip("."), "title")
        seed = _CharacterSeed(
            key=key,
            display=normalized_display,
            normalized=normalized_name,
            duplicate_index=duplicate_index,
            spans=[(start, end)],
            aliases=aliases,
        )
        character_seeds[key] = seed
        return seed

    titled_ranges: list[tuple[int, int]] = []
    for titled_match in _TITLED_CHARACTER.finditer(text):
        guard()
        canonical_display = f"{titled_match.group('first')} {titled_match.group('last')}"
        prefix = text[max(0, titled_match.start() - 16) : titled_match.start()].casefold()
        force_duplicate = bool(re.search(r"\b(?:another|second|other)[ \t]+$", prefix))
        add_character_seed(
            canonical_display,
            start=titled_match.start(),
            end=titled_match.end(),
            title=titled_match.group("title"),
            force_duplicate=force_duplicate,
        )
        titled_ranges.append((titled_match.start(), titled_match.end()))

    for name_match in _TWO_PART_NAME.finditer(text):
        guard()
        display_name = f"{name_match.group('first')} {name_match.group('last')}"
        normalized_name = display_name.casefold()
        if (
            normalized_name in _NON_CHARACTER_NAME_TERMS
            or name_match.group("first").casefold()
            in {
                "a",
                "an",
                "captain",
                "doctor",
                "dr",
                "engineer",
                "inspector",
                "keeper",
                "mr",
                "mrs",
                "ms",
                "professor",
                "the",
            }
            or not {
                name_match.group("first").casefold(),
                name_match.group("last").casefold(),
            }
            & speaker_tokens
        ):
            continue
        if any(
            titled_start <= name_match.start() and name_match.end() <= titled_end
            for titled_start, titled_end in titled_ranges
        ):
            continue
        prefix = text[max(0, name_match.start() - 16) : name_match.start()].casefold()
        add_character_seed(
            display_name,
            start=name_match.start(),
            end=name_match.end(),
            force_duplicate=bool(re.search(r"\b(?:another|second|other)[ \t]+$", prefix)),
        )

    for known_as_match in _KNOWN_AS.finditer(text):
        guard()
        canonical_display = f"{known_as_match.group('first')} {known_as_match.group('last')}"
        seed = add_character_seed(
            canonical_display,
            start=known_as_match.start(),
            end=known_as_match.start("last") + len(known_as_match.group("last")),
            title=known_as_match.group("title"),
        )
        alias_title = known_as_match.group("alias_title")
        alias_name = known_as_match.group("alias")
        alias_display = f"{alias_title} {alias_name}" if alias_title else alias_name
        seed.aliases[alias_display.casefold()] = (
            alias_display,
            "honorific" if alias_title else "nickname",
        )

    # Resolve explicit one-token speech tags to the introduced full identity. This avoids
    # creating a second identity for "Mira" after "Captain Mira Vale".
    for speaker_surface, speaker_start, speaker_end in speaker_surfaces:
        guard()
        normalized_surface = speaker_surface.casefold()
        matching_seeds = [
            seed
            for seed in character_seeds.values()
            if normalized_surface in seed.aliases
            or normalized_surface in {part.casefold() for part in seed.display.split()}
        ]
        if len(matching_seeds) == 1:
            matching_seed = matching_seeds[0]
            matching_seed.aliases[normalized_surface] = (speaker_surface, "given_name")
            if (speaker_start, speaker_end) not in matching_seed.spans:
                matching_seed.spans.append((speaker_start, speaker_end))
        elif (
            not matching_seeds
            and speaker_surface[:1].isupper()
            and not speaker_surface.casefold().startswith(("the ", "a ", "an "))
            and speaker_surface.casefold() not in _NON_CHARACTER_NAME_TERMS
        ):
            add_character_seed(
                speaker_surface,
                start=speaker_start,
                end=speaker_end,
            )

    # Legacy alias phrasing and honorific/surname references remain explicit evidence.
    for alias_match in _ALIAS.finditer(text):
        guard()
        full_name = " ".join(alias_match.group(1).split())
        matching_seeds = [
            seed for seed in character_seeds.values() if seed.normalized == full_name.casefold()
        ]
        if len(matching_seeds) == 1:
            matching_seeds[0].aliases[alias_match.group(2).casefold()] = (
                alias_match.group(2),
                "nickname",
            )
    honorific_aliases_by_surname: dict[str, list[str]] = {}
    for honorific_match in re.finditer(
        rf"(?i)\b({_CHARACTER_TITLE})[ \t\r\n]+(?P<surname>[A-Za-z'’-]+)\b",
        text,
    ):
        alias_display = " ".join(honorific_match.group(0).split())
        honorific_aliases_by_surname.setdefault(
            honorific_match.group("surname").casefold(),
            [],
        ).append(alias_display)
    for seed in character_seeds.values():
        surname = seed.display.split()[-1]
        for alias_display in honorific_aliases_by_surname.get(
            surname.casefold(),
            (),
        ):
            seed.aliases[alias_display.casefold()] = (alias_display, "honorific")

    role_members: dict[str, list[_CharacterSeed]] = {}
    for seed in character_seeds.values():
        for alias_key, (_alias_display, alias_kind) in seed.aliases.items():
            if alias_kind == "title":
                normalized_role = "doctor" if alias_key in {"dr", "doctor"} else alias_key
                role_members.setdefault(normalized_role, []).append(seed)
    for role, members in role_members.items():
        if re.search(rf"(?i)\b(?:the|both|neither)[ \t]+{re.escape(role)}s?\b", text):
            for member in members:
                member.aliases[role] = (role, "title")

    ordered_seeds = sorted(
        character_seeds.values(),
        key=lambda seed: (min(start for start, _end in seed.spans), seed.key),
    )
    character_anchors = {
        seed.key: min(start for start, _end in seed.spans) for seed in ordered_seeds
    }
    character_ids: dict[str, str] = {
        seed.key: stable_id(
            "whole-book-character-registry-v2",
            registry_scope,
            story_scope,
            character_anchors[seed.key],
            seed.duplicate_index,
        )
        for seed in ordered_seeds
    }
    registry_character_ids: dict[str, str] = {
        seed.key: stable_id(
            "whole-book-character-registry-identity-v2",
            registry_scope,
            story_scope,
            character_anchors[seed.key],
            seed.duplicate_index,
        )
        for seed in ordered_seeds
    }
    surface_to_characters: dict[str, list[str]] = {}
    surface_kinds: dict[str, str] = {}
    for seed in ordered_seeds:
        character_id = character_ids[seed.key]
        for alias_key, (_alias_display, alias_kind) in seed.aliases.items():
            surface_to_characters.setdefault(alias_key, []).append(character_id)
            surface_kinds.setdefault(alias_key, alias_kind)

    mention_records: list[dict[str, Any]] = []
    mention_pattern = (
        re.compile(
            r"(?i)\b("
            + "|".join(
                re.escape(surface)
                for surface in sorted(
                    surface_to_characters,
                    key=lambda surface: (-len(surface), surface),
                )
            )
            + r"|he|him|his|she|her|hers|they|them|their|theirs)\b"
        )
        if surface_to_characters
        else None
    )
    latest_resolved: tuple[int, str] | None = None
    if mention_pattern is not None:
        for mention_match in mention_pattern.finditer(text):
            guard()
            mention_surface = mention_match.group(1)
            normalized_surface = mention_surface.casefold()
            pronoun = _PRONOUN.fullmatch(mention_surface) is not None
            if pronoun:
                mention_candidates = (
                    [latest_resolved[1]]
                    if latest_resolved is not None
                    and mention_match.start() - latest_resolved[0] <= 160
                    else list(character_ids.values())[:MAX_SPEAKER_CANDIDATES]
                )
                mention_kind = "pronoun"
            else:
                mention_candidates = list(
                    dict.fromkeys(surface_to_characters.get(normalized_surface, []))
                )[:MAX_SPEAKER_CANDIDATES]
                alias_kind = surface_kinds.get(normalized_surface, "other")
                mention_kind = (
                    "proper_name"
                    if alias_kind in {"full_name", "given_name", "family_name"}
                    else "honorific"
                    if alias_kind in {"honorific", "title"}
                    else "alias"
                )
            mention_resolved = mention_candidates[0] if len(mention_candidates) == 1 else None
            if mention_resolved is not None and not pronoun:
                latest_resolved = (mention_match.end(), mention_resolved)
            mention_records.append(
                {
                    "start": mention_match.start(),
                    "end": mention_match.end(),
                    "surface": text[mention_match.start() : mention_match.end()],
                    "kind": mention_kind,
                    "candidates": mention_candidates,
                    "resolved": mention_resolved,
                }
            )

    mentions_by_character: dict[str, list[dict[str, Any]]] = {
        character_id: [] for character_id in character_ids.values()
    }
    ambiguous_mentions_by_character: dict[str, list[dict[str, Any]]] = {
        character_id: [] for character_id in character_ids.values()
    }
    for mention_record in mention_records:
        resolved_character_id = mention_record["resolved"]
        if isinstance(resolved_character_id, str):
            mentions_by_character[resolved_character_id].append(mention_record)
        else:
            for candidate_character_id in mention_record["candidates"]:
                ambiguous_mentions_by_character[candidate_character_id].append(mention_record)

    duplicate_name_sizes = Counter(seed.normalized for seed in ordered_seeds)
    for character_ordinal, character_seed in enumerate(ordered_seeds):
        guard()
        character_id = character_ids[character_seed.key]
        resolved_mentions = mentions_by_character[character_id]
        seed_start = min(start for start, _end in character_seed.spans)
        seed_end = max(end for _start, end in character_seed.spans)
        first_position = (
            min(int(mention["start"]) for mention in resolved_mentions)
            if resolved_mentions
            else seed_start
        )
        last_position = (
            max(int(mention["end"]) for mention in resolved_mentions)
            if resolved_mentions
            else seed_end
        )
        first_mention_end = next(
            (
                int(mention["end"])
                for mention in resolved_mentions
                if int(mention["start"]) == first_position
            ),
            last_position,
        )
        first_mention_id = (
            stable_id(
                input_fingerprint,
                "mentions",
                f"{first_position}:{first_mention_end}",
            )
            if resolved_mentions
            else None
        )
        last_mention_start = next(
            (
                int(mention["start"])
                for mention in reversed(resolved_mentions)
                if int(mention["end"]) == last_position
            ),
            first_position,
        )
        last_mention_id = (
            stable_id(
                input_fingerprint,
                "mentions",
                f"{last_mention_start}:{last_position}",
            )
            if resolved_mentions
            else None
        )
        named_mention_ids = [
            stable_id(
                input_fingerprint,
                "mentions",
                f"{mention_record['start']}:{mention_record['end']}",
            )
            for mention_record in resolved_mentions
            if mention_record["kind"] != "pronoun"
        ]
        ambiguous_mention_ids = [
            stable_id(
                input_fingerprint,
                "mentions",
                f"{mention_record['start']}:{mention_record['end']}",
            )
            for mention_record in ambiguous_mentions_by_character[character_id]
        ]
        alias_values = [
            {
                "aliasId": stable_id(character_id, "alias", alias_key),
                "characterId": character_id,
                "alias": alias_display,
                "normalizedAlias": alias_key,
                "kind": alias_kind,
                "ambiguous": len(surface_to_characters.get(alias_key, [])) > 1,
                "effectiveRange": {
                    "sourceRange": {
                        "startOffset": seed_start,
                        "endOffset": seed_end,
                    },
                    "validFromEventId": None,
                    "validThroughEventId": None,
                },
                "change": "introduced",
                "confidence": confidence(0.92, "explicit_name_or_title"),
                "evidence": [
                    _evidence(
                        seed_start,
                        seed_end,
                        "alias_identity_evidence",
                        0.92,
                    )
                ],
            }
            for alias_key, (alias_display, alias_kind) in sorted(character_seed.aliases.items())
        ]
        character_item = _entity(
            input_fingerprint=input_fingerprint,
            collection="characters",
            ordinal=character_ordinal,
            identity_key=character_seed.key,
            entity_id=character_id,
            payload={
                "characterId": character_id,
                "registryCharacterId": registry_character_ids[character_seed.key],
                "canonicalName": character_seed.display,
                "normalizedCanonicalName": character_seed.normalized,
                "projectId": registry_scope,
                "storyId": story_scope,
                "registryScope": "project_story",
                "stableAcrossCompatibleRuns": True,
                "kind": "person",
                "identityStatus": (
                    "ambiguous"
                    if duplicate_name_sizes[character_seed.normalized] > 1
                    else "resolved"
                ),
                "aliases": alias_values,
                "honorifics": [
                    {
                        "honorific": alias_display,
                        "normalizedHonorific": alias_key,
                        "confidence": confidence(
                            0.92,
                            "explicit_honorific_evidence",
                        ),
                        "evidence": [
                            _evidence(
                                seed_start,
                                seed_end,
                                "honorific_evidence",
                                0.92,
                            )
                        ],
                    }
                    for alias_key, (alias_display, alias_kind) in sorted(
                        character_seed.aliases.items()
                    )
                    if alias_kind in {"honorific", "title"}
                ],
                "pronounEvidence": [
                    {
                        "pronoun": str(mention_record["surface"]),
                        "normalizedPronoun": str(mention_record["surface"]).casefold(),
                        "resolution": "resolved",
                        "confidence": confidence(
                            0.55,
                            "bounded_local_pronoun_antecedent",
                        ),
                        "evidence": [
                            _evidence(
                                int(mention_record["start"]),
                                int(mention_record["end"]),
                                "pronoun_evidence",
                                0.55,
                            )
                        ],
                    }
                    for mention_record in resolved_mentions[:64]
                    if mention_record["kind"] == "pronoun"
                ],
                "firstMentionId": first_mention_id,
                "lastMentionId": last_mention_id,
                "namedMentionIds": list(dict.fromkeys(named_mention_ids)),
                "ambiguousMentionIds": list(dict.fromkeys(ambiguous_mention_ids)),
                "firstEvidence": (
                    [
                        _evidence(
                            first_position,
                            first_mention_end,
                            "first_character_evidence",
                            0.94,
                        )
                    ]
                    if resolved_mentions
                    else []
                ),
                "lastEvidence": (
                    [
                        _evidence(
                            last_mention_start,
                            last_position,
                            "last_character_evidence",
                            0.94,
                        )
                    ]
                    if resolved_mentions
                    else []
                ),
                "mentionCount": len(resolved_mentions),
            },
            score=0.94,
            basis="explicit_introduction_or_dialogue_identity",
            evidence=[
                _evidence(span_start, span_end, "character_introduction", 0.94)
                for span_start, span_end in sorted(set(character_seed.spans))
            ],
            start=first_position,
            end=last_position,
        )
        collections["characters"].append(character_item)

    for mention_ordinal, mention_record in enumerate(mention_records):
        guard()
        mention_candidates = list(mention_record["candidates"])
        mention_resolved = mention_record["resolved"]
        mention_ambiguous = mention_resolved is None
        mention_scene_index = next(
            (
                scene_index
                for scene_index, scene in enumerate(scenes)
                if scene.start <= int(mention_record["start"]) < scene.end
            ),
            0,
        )
        mention_identity_key = f"{mention_record['start']}:{mention_record['end']}"
        collections["mentions"].append(
            _entity(
                input_fingerprint=input_fingerprint,
                collection="mentions",
                ordinal=mention_ordinal,
                identity_key=mention_identity_key,
                payload={
                    "mentionId": stable_id(
                        input_fingerprint,
                        "mentions",
                        mention_identity_key,
                    ),
                    "chapterId": chapter_ids[scenes[mention_scene_index].parent or 0],
                    "sceneId": scene_ids[mention_scene_index],
                    "mentionKind": mention_record["kind"],
                    "effectiveCharacterId": mention_resolved,
                    "candidateCharacterIds": mention_candidates,
                    "resolution": (
                        "ambiguous"
                        if mention_ambiguous and mention_candidates
                        else "unresolved"
                        if mention_ambiguous
                        else "resolved"
                    ),
                },
                score=0.55
                if mention_record["kind"] == "pronoun"
                else 0.0
                if mention_ambiguous
                else 0.9,
                basis="bounded_local_pronoun_antecedent"
                if mention_record["kind"] == "pronoun"
                else "duplicate_name_ambiguous"
                if mention_ambiguous
                else "exact_name_or_alias",
                evidence=[
                    _evidence(
                        int(mention_record["start"]),
                        int(mention_record["end"]),
                        "character_mention",
                        0.9,
                    )
                ],
                start=int(mention_record["start"]),
                end=int(mention_record["end"]),
                warnings=(
                    [
                        warning(
                            "CHARACTER_MENTION_AMBIGUOUS",
                            "The mention is ambiguous or unresolved.",
                        )
                    ]
                    if mention_ambiguous
                    else []
                ),
            )
        )

    dialogue_ids: list[str] = []
    for dialogue_ordinal, dialogue_claim in enumerate(dialogues):
        guard()
        speaker_candidates: list[dict[str, Any]] = []
        explicit_candidate_evidence = [
            {
                "startOffset": (
                    dialogue_claim.speaker_start
                    if dialogue_claim.speaker_start is not None
                    else dialogue_claim.quote_start
                ),
                "endOffset": (
                    dialogue_claim.speaker_start + len(dialogue_claim.speaker_name)
                    if dialogue_claim.speaker_start is not None
                    and dialogue_claim.speaker_name is not None
                    else dialogue_claim.quote_end
                ),
            }
        ]
        proposed: str | None = None
        dialogue_score = dialogue_claim.score
        dialogue_basis = dialogue_claim.basis
        if dialogue_claim.speaker_name is not None:
            normalized_speaker_surface = dialogue_claim.speaker_name.casefold()
            if normalized_speaker_surface.startswith("the "):
                normalized_speaker_surface = normalized_speaker_surface[4:]
            if normalized_speaker_surface.endswith("s"):
                singular_surface = normalized_speaker_surface[:-1]
            else:
                singular_surface = normalized_speaker_surface
            surface_candidates = list(
                dict.fromkeys(
                    surface_to_characters.get(
                        normalized_speaker_surface,
                        surface_to_characters.get(singular_surface, []),
                    )
                )
            )
            if len(surface_candidates) == 1:
                proposed = surface_candidates[0]
                speaker_candidates.append(
                    {
                        "candidateId": stable_id(
                            input_fingerprint,
                            "dialogue-candidate",
                            dialogue_claim.quote_start,
                            proposed,
                        ),
                        "characterId": proposed,
                        "rank": 1,
                        "score": dialogue_claim.score,
                        "basis": dialogue_claim.basis,
                        "confidence": confidence(
                            dialogue_claim.score,
                            dialogue_claim.basis,
                        ),
                        "evidence": explicit_candidate_evidence,
                        "rationale": dialogue_claim.basis,
                    }
                )
            elif surface_candidates:
                dialogue_score = 0.0
                dialogue_basis = "duplicate_name_ambiguous"
                speaker_candidates.extend(
                    {
                        "candidateId": stable_id(
                            input_fingerprint,
                            "dialogue-candidate",
                            dialogue_claim.quote_start,
                            character_id,
                        ),
                        "characterId": character_id,
                        "rank": candidate_rank,
                        "score": 0.5,
                        "basis": "duplicate_name_candidate",
                        "confidence": confidence(0.5, "duplicate_name_candidate"),
                        "evidence": explicit_candidate_evidence,
                        "rationale": "duplicate_name_candidate",
                    }
                    for candidate_rank, character_id in enumerate(
                        surface_candidates[:MAX_SPEAKER_CANDIDATES],
                        start=1,
                    )
                )
        if proposed is None and not speaker_candidates:
            context_start = max(
                scenes[dialogue_claim.scene_index].start,
                dialogue_claim.quote_start - 180,
            )
            context_end = min(
                scenes[dialogue_claim.scene_index].end,
                dialogue_claim.quote_end + 180,
            )
            context_text = text[context_start:context_end]
            role_cue = re.search(
                r"(?i)\b(?:both|neither)[ \t]+([a-z]+?)(?:s)?\b",
                context_text,
            )
            contextual_ids: list[str]
            if role_cue is not None:
                role = role_cue.group(1).casefold()
                if role in {"doctor", "doctors"}:
                    role = "doctor"
                contextual_ids = list(dict.fromkeys(surface_to_characters.get(role, [])))
            else:
                contextual_ids = list(
                    dict.fromkeys(
                        str(mention_record["resolved"])
                        for mention_record in mention_records
                        if mention_record["kind"] != "pronoun"
                        and mention_record["resolved"] is not None
                        and int(mention_record["start"]) >= context_start
                        and int(mention_record["end"]) <= context_end
                    )
                )
            # Preserve ambiguity: contextual turn-taking produces bounded candidates but
            # never silently invents an effective speaker.
            if len(contextual_ids) > 4 and role_cue is None:
                nearest = sorted(
                    (
                        (
                            min(
                                abs(int(mention_record["start"]) - dialogue_claim.quote_start),
                                abs(int(mention_record["end"]) - dialogue_claim.quote_end),
                            ),
                            str(mention_record["resolved"]),
                        )
                        for mention_record in mention_records
                        if mention_record["kind"] != "pronoun"
                        and mention_record["resolved"] is not None
                        and int(mention_record["start"]) >= context_start
                        and int(mention_record["end"]) <= context_end
                    ),
                    key=lambda candidate: (candidate[0], candidate[1]),
                )
                contextual_ids = list(
                    dict.fromkeys(character_id for _distance, character_id in nearest)
                )[:2]
            speaker_candidates.extend(
                {
                    "candidateId": stable_id(
                        input_fingerprint,
                        "dialogue-candidate",
                        dialogue_claim.quote_start,
                        character_id,
                    ),
                    "characterId": character_id,
                    "rank": candidate_rank,
                    "score": 0.45,
                    "basis": "bounded_context_candidate",
                    "confidence": confidence(0.45, "bounded_context_candidate"),
                    "evidence": [
                        {
                            "startOffset": context_start,
                            "endOffset": context_end,
                        }
                    ],
                    "rationale": "bounded_context_candidate",
                }
                for candidate_rank, character_id in enumerate(
                    contextual_ids[:MAX_SPEAKER_CANDIDATES],
                    start=1,
                )
            )
            dialogue_score = 0.0
            dialogue_basis = (
                "bounded_context_ambiguous" if speaker_candidates else "unknown_speaker"
            )
        dialogue_evidence = [
            _evidence(
                dialogue_claim.quote_start,
                dialogue_claim.quote_end,
                "dialogue_text",
                1.0,
            )
        ]
        if dialogue_claim.speaker_start is not None and dialogue_claim.speaker_name is not None:
            dialogue_evidence.append(
                _evidence(
                    dialogue_claim.speaker_start,
                    dialogue_claim.speaker_start + len(dialogue_claim.speaker_name),
                    dialogue_claim.basis,
                    dialogue_claim.score,
                )
            )
        scene_id = scene_ids[dialogue_claim.scene_index]
        chapter_id = chapter_ids[scenes[dialogue_claim.scene_index].parent or 0]
        beat_id = stable_id(
            input_fingerprint,
            "beats",
            f"{dialogue_claim.quote_start}:{dialogue_claim.quote_end}:dialogue",
        )
        dialogue_item = _entity(
            input_fingerprint=input_fingerprint,
            collection="dialogue-lines",
            ordinal=dialogue_ordinal,
            identity_key=f"{dialogue_claim.quote_start}:{dialogue_claim.quote_end}",
            payload={
                "dialogueLineId": stable_id(
                    input_fingerprint,
                    "dialogue-lines",
                    f"{dialogue_claim.quote_start}:{dialogue_claim.quote_end}",
                ),
                "chapterId": chapter_id,
                "sceneId": scene_id,
                "beatId": beat_id,
                "distinction": "spoken_dialogue",
                "quoteStartOffset": dialogue_claim.quote_start,
                "quoteEndOffset": dialogue_claim.quote_end,
                "proposedSpeakerId": proposed,
                "effectiveSpeakerId": proposed,
                "effectiveAuthority": ("runtime_agent" if proposed is not None else "unresolved"),
                "speakerState": (
                    "unknown"
                    if not speaker_candidates
                    else "ambiguous"
                    if proposed is None
                    else "proposed"
                ),
                "candidates": speaker_candidates[:MAX_SPEAKER_CANDIDATES],
                "effectiveAttribution": {
                    "speakerCharacterId": proposed,
                    "selectedCandidateId": (
                        speaker_candidates[0]["candidateId"]
                        if proposed is not None and speaker_candidates
                        else None
                    ),
                    "authority": ("runtime_agent" if proposed is not None else "unresolved"),
                    "confidence": confidence(dialogue_score, dialogue_basis),
                    "requiresHumanReview": (proposed is None or dialogue_score < 0.75),
                },
                "requiresHumanReview": proposed is None or dialogue_score < 0.75,
            },
            score=dialogue_score,
            basis=dialogue_basis,
            evidence=dialogue_evidence,
            start=dialogue_claim.quote_start,
            end=dialogue_claim.quote_end,
            parent_id=scene_id,
            warnings=(
                [
                    warning(
                        "DIALOGUE_SPEAKER_UNCERTAIN",
                        "Speaker evidence is unknown or ambiguous; no identity was invented.",
                    )
                ]
                if proposed is None or dialogue_score < 0.75
                else []
            ),
        )
        dialogue_ids.append(str(dialogue_item["entityId"]))
        collections["dialogue-lines"].append(dialogue_item)

    dialogues_by_scene: dict[int, list[_DialogueClaim]] = {
        scene_index: [] for scene_index in range(len(scenes))
    }
    dialogue_by_start = {
        dialogue_claim.quote_start: dialogue_index
        for dialogue_index, dialogue_claim in enumerate(dialogues)
    }
    for dialogue_claim in dialogues:
        dialogues_by_scene[dialogue_claim.scene_index].append(dialogue_claim)
    for scene_index, scene in enumerate(scenes):
        scene_dialogues = dialogues_by_scene[scene_index]
        narration_cursor = scene.start
        components: list[tuple[int, int, str, str | None]] = []
        for scene_dialogue in scene_dialogues:
            component_start, component_end = _trim(
                text,
                narration_cursor,
                scene_dialogue.quote_start,
            )
            if component_start < component_end:
                components.append((component_start, component_end, "narration", None))
            dialogue_index = dialogue_by_start[scene_dialogue.quote_start]
            components.append(
                (
                    scene_dialogue.quote_start,
                    scene_dialogue.quote_end,
                    "dialogue",
                    dialogue_ids[dialogue_index],
                )
            )
            narration_cursor = scene_dialogue.quote_end
        component_start, component_end = _trim(text, narration_cursor, scene.end)
        if component_start < component_end:
            components.append((component_start, component_end, "narration", None))
        for component_start, component_end, component_kind, dialogue_id in components:
            (
                beat_kind,
                beat_summary,
                beat_score,
                beat_basis,
                beat_warnings,
            ) = _classify_beat(
                text[component_start:component_end],
                component_kind,
            )
            beat_item = _entity(
                input_fingerprint=input_fingerprint,
                collection="beats",
                ordinal=len(collections["beats"]),
                identity_key=f"{component_start}:{component_end}:{component_kind}",
                payload={
                    "beatId": stable_id(
                        input_fingerprint,
                        "beats",
                        f"{component_start}:{component_end}:{component_kind}",
                    ),
                    "chapterId": chapter_ids[scene.parent or 0],
                    "sceneId": scene_ids[scene_index],
                    "kind": beat_kind,
                    "summary": beat_summary,
                    "dialogueLineId": dialogue_id,
                },
                score=beat_score,
                basis=beat_basis,
                evidence=[
                    _evidence(
                        component_start,
                        component_end,
                        beat_basis,
                        beat_score,
                    )
                ],
                start=component_start,
                end=component_end,
                parent_id=scene_ids[scene_index],
                warnings=beat_warnings,
            )
            collections["beats"].append(beat_item)
            if component_kind == "narration":
                exact_narration = text[component_start:component_end]
                stripped = exact_narration.strip()
                narration_classification = (
                    "epigraph_or_document"
                    if scene_index == 0
                    and len(stripped) <= 400
                    and stripped.startswith((">", "Dear ", "From "))
                    else "direct_narration"
                )
                collections["narration-spans"].append(
                    _entity(
                        input_fingerprint=input_fingerprint,
                        collection="narration-spans",
                        ordinal=len(collections["narration-spans"]),
                        identity_key=f"{component_start}:{component_end}:direct",
                        payload={
                            "narrationSpanId": stable_id(
                                input_fingerprint,
                                "narration-spans",
                                f"{component_start}:{component_end}:direct",
                            ),
                            "chapterId": chapter_ids[scene.parent or 0],
                            "sceneId": scene_ids[scene_index],
                            "beatId": beat_item["entityId"],
                            "classification": narration_classification,
                            "subtype": narration_classification,
                            "narratorCharacterId": None,
                        },
                        score=(0.94 if narration_classification == "direct_narration" else 0.78),
                        basis="canonical_non_dialogue_segmentation",
                        evidence=[
                            _evidence(
                                component_start,
                                component_end,
                                "narration_text",
                                1.0,
                            )
                        ],
                        start=component_start,
                        end=component_end,
                        parent_id=str(beat_item["entityId"]),
                    )
                )

        dialogue_quote_starts = {dialogue_claim.quote_start for dialogue_claim in scene_dialogues}
        for quoted_match in _QUOTE.finditer(text, scene.start, scene.end):
            if quoted_match.start() in dialogue_quote_starts:
                continue
            collections["narration-spans"].append(
                _entity(
                    input_fingerprint=input_fingerprint,
                    collection="narration-spans",
                    ordinal=len(collections["narration-spans"]),
                    identity_key=f"{quoted_match.start()}:{quoted_match.end()}:quoted",
                    payload={
                        "narrationSpanId": stable_id(
                            input_fingerprint,
                            "narration-spans",
                            f"{quoted_match.start()}:{quoted_match.end()}:quoted",
                        ),
                        "chapterId": chapter_ids[scene.parent or 0],
                        "sceneId": scene_ids[scene_index],
                        "classification": "quoted_material",
                        "subtype": "quoted_material",
                        "narratorCharacterId": None,
                    },
                    score=0.96,
                    basis="explicit_non_speech_quotation_context",
                    evidence=[
                        _evidence(
                            quoted_match.start(),
                            quoted_match.end(),
                            "quoted_material",
                            0.96,
                        )
                    ],
                    start=quoted_match.start(),
                    end=quoted_match.end(),
                    parent_id=scene_ids[scene_index],
                )
            )
        for thought_match in re.finditer(
            r"(?im)(?:^|(?<=[.!?]))[ \t\r\n]*"
            r"([^.!?\r\n]*(?:thought|wondered)[^.!?\r\n]*[.!?])",
            text[scene.start : scene.end],
        ):
            thought_start = scene.start + thought_match.start(1)
            thought_end = scene.start + thought_match.end(1)
            thought_start, thought_end = _trim(text, thought_start, thought_end)
            if thought_start >= thought_end:
                continue
            collections["narration-spans"].append(
                _entity(
                    input_fingerprint=input_fingerprint,
                    collection="narration-spans",
                    ordinal=len(collections["narration-spans"]),
                    identity_key=f"{thought_start}:{thought_end}:thought",
                    payload={
                        "narrationSpanId": stable_id(
                            input_fingerprint,
                            "narration-spans",
                            f"{thought_start}:{thought_end}:thought",
                        ),
                        "chapterId": chapter_ids[scene.parent or 0],
                        "sceneId": scene_ids[scene_index],
                        "classification": "internal_thought",
                        "subtype": "internal_thought",
                        "narratorCharacterId": None,
                    },
                    score=0.82,
                    basis="explicit_thought_verb",
                    evidence=[
                        _evidence(
                            thought_start,
                            thought_end,
                            "internal_thought",
                            0.82,
                        )
                    ],
                    start=thought_start,
                    end=thought_end,
                    parent_id=scene_ids[scene_index],
                )
            )
    beats_by_scene_id: dict[str, list[dict[str, Any]]] = {scene_id: [] for scene_id in scene_ids}
    for beat_item in collections["beats"]:
        beats_by_scene_id[str(beat_item["payload"]["sceneId"])].append(beat_item)
    for scene_index, scene_item in enumerate(collections["scenes"]):
        child_beats = beats_by_scene_id[scene_ids[scene_index]]
        scene_item["payload"]["firstBeatId"] = child_beats[0]["entityId"] if child_beats else None
        scene_item["payload"]["lastBeatId"] = child_beats[-1]["entityId"] if child_beats else None
        scene_item["payload"]["beatCount"] = len(child_beats)
    observe("beats", ("beats", "narration-spans"))
    observe("character_identity", ("characters", "mentions"))
    observe("dialogue_attribution", ("dialogue-lines",))

    previous_viewpoint_id: str | None = None
    previous_pov_segment_id: str | None = None
    for scene_index, scene in enumerate(scenes):
        segment = text[scene.start : scene.end]
        first = len(re.findall(r"(?i)\b(?:i|me|my|mine|we|us|our)\b", segment))
        second = len(re.findall(r"(?i)\b(?:you|your|yours)\b", segment))
        third = len(re.findall(r"(?i)\b(?:he|she|they|him|her|them|his|their)\b", segment))
        if first > 0 and third > 0 and min(first, third) / max(first, third) >= 0.6:
            pov_type, pov_score, pov_basis = (
                "mixed",
                0.76,
                "mixed_pronoun_prevalence",
            )
        elif second > max(first, third) and second >= 3:
            pov_type, pov_score, pov_basis = (
                "second_person",
                0.82,
                "second_person_pronoun_prevalence",
            )
        elif first > third and first > 0:
            pov_type, pov_score, pov_basis = (
                "first_person",
                0.9,
                "first_person_pronoun_prevalence",
            )
        elif third > 0:
            pov_type, pov_score, pov_basis = (
                "third_person_limited",
                0.8,
                "third_person_pronoun_prevalence",
            )
        else:
            pov_type, pov_score, pov_basis = (
                "experimental"
                if re.search(r"(?i)\b(?:second-person chorus|fragmented viewpoint)\b", segment)
                else "unknown",
                0.65
                if re.search(
                    r"(?i)\b(?:second-person chorus|fragmented viewpoint)\b",
                    segment,
                )
                else 0.0,
                "explicit_experimental_viewpoint_signal"
                if re.search(
                    r"(?i)\b(?:second-person chorus|fragmented viewpoint)\b",
                    segment,
                )
                else "insufficient_pov_evidence",
            )
        focal_candidates: list[str] = []
        for focal_match in re.finditer(
            r"(?i)\b([A-Z][A-Za-z'’-]+)[ \t]+(?:wished|thought|wondered)\b",
            segment,
        ):
            focal_candidates.extend(surface_to_characters.get(focal_match.group(1).casefold(), []))
        if re.search(r"(?i)\bonly[ \t]+(?:he|she|they)[ \t]+noticed\b", segment):
            scene_mentions = [
                mention_record
                for mention_record in mention_records
                if mention_record["resolved"] is not None
                and mention_record["kind"] != "pronoun"
                and int(mention_record["start"]) >= scene.start
                and int(mention_record["end"]) <= scene.end
            ]
            if scene_mentions:
                focal_candidates.append(str(scene_mentions[0]["resolved"]))
        unique_focal_candidates = list(dict.fromkeys(focal_candidates))
        viewpoint_id = unique_focal_candidates[0] if len(unique_focal_candidates) == 1 else None
        shift_kind = (
            "initial"
            if previous_pov_segment_id is None
            else "scene_boundary"
            if viewpoint_id is not None
            and previous_viewpoint_id is not None
            and viewpoint_id != previous_viewpoint_id
            else "uncertain"
            if viewpoint_id is None
            else "scene_boundary"
        )
        pov_segment = _entity(
            input_fingerprint=input_fingerprint,
            collection="pov-segments",
            ordinal=scene_index,
            identity_key=f"{scene.start}:{scene.end}",
            payload={
                "povSegmentId": stable_id(
                    input_fingerprint,
                    "pov-segments",
                    f"{scene.start}:{scene.end}",
                ),
                "chapterId": chapter_ids[scene.parent or 0],
                "sceneId": scene_ids[scene_index],
                "mode": pov_type,
                "povType": pov_type,
                "viewpointCharacterId": viewpoint_id,
                "focalCharacterId": viewpoint_id,
                "narratorCharacterId": None,
                "shiftFromPovSegmentId": previous_pov_segment_id,
                "shiftKind": shift_kind,
                "effectiveAuthority": "runtime_agent",
            },
            score=pov_score,
            basis=pov_basis,
            evidence=[
                _evidence(
                    scene.start,
                    scene.end,
                    pov_basis,
                    pov_score,
                )
            ],
            start=scene.start,
            end=scene.end,
            parent_id=scene_ids[scene_index],
            warnings=(
                [warning("POINT_OF_VIEW_UNKNOWN", "Point of view could not be determined.")]
                if pov_type == "unknown"
                else []
            ),
        )
        collections["pov-segments"].append(pov_segment)
        previous_pov_segment_id = str(pov_segment["entityId"])
        if viewpoint_id is not None:
            previous_viewpoint_id = viewpoint_id
    observe("point_of_view", ("pov-segments",))

    def location_scene_index_at(offset: int) -> int | None:
        return next(
            (
                candidate_index
                for candidate_index, candidate_scene in enumerate(scenes)
                if candidate_scene.start <= offset < candidate_scene.end
            ),
            None,
        )

    # A repeated surface label is not sufficient identity evidence across scenes.
    # Records remain scene-scoped unless explicit bounded evidence establishes
    # co-reference.
    location_records: dict[tuple[str, int], dict[str, Any]] = {}
    for match in _LOCATION_NARRATIVE.finditer(text):
        guard()
        location_name = " ".join(match.group(1).split())
        normalized_location = location_name.casefold()
        if normalized_location in _NON_LOCATION_TERMS:
            continue
        containing_scene_index = location_scene_index_at(match.start(1))
        if containing_scene_index is None:
            continue
        location_record = location_records.setdefault(
            (normalized_location, containing_scene_index),
            {
                "display": location_name,
                "spans": [],
                "sceneIds": [],
                "aliases": [],
            },
        )
        location_record["spans"].append((match.start(1), match.end(1)))
        location_record["sceneIds"].append(scene_ids[containing_scene_index])
    for match in _LOCATION_ALIAS.finditer(text):
        guard()
        location_name = " ".join(match.group(1).split())
        location_alias = match.group(2)
        containing_scene_index = location_scene_index_at(match.start())
        if containing_scene_index is None:
            continue
        location_record = location_records.setdefault(
            (location_name.casefold(), containing_scene_index),
            {
                "display": location_name,
                "spans": [],
                "sceneIds": [],
                "aliases": [],
            },
        )
        location_record["aliases"].append(location_alias)
        location_record["spans"].append((match.start(), match.end()))
        location_record["sceneIds"].append(scene_ids[containing_scene_index])
    for scene_index, scene in enumerate(scenes):
        if any(
            scene_ids[scene_index] in list(location_record["sceneIds"])
            for location_record in location_records.values()
        ):
            continue
        if scene.label is None:
            continue
        if re.match(r"(?i)^(?:int|ext|int/ext|ext/int)\.", scene.label):
            heading_location = scene.label.split("-", 1)[0].strip()
        elif ":" in scene.label:
            heading_location = scene.label.split(":", 1)[1].strip()
            if "," in heading_location and re.search(
                r"(?i)\b(?:earlier|later|present|years?|months?|days?|hours?)\b",
                heading_location.split(",", 1)[0],
            ):
                heading_location = heading_location.rsplit(",", 1)[1].strip()
        else:
            continue
        normalized_heading_location = heading_location.casefold()
        if normalized_heading_location in _NON_LOCATION_TERMS:
            continue
        location_record = location_records.setdefault(
            (normalized_heading_location, scene_index),
            {
                "display": heading_location,
                "spans": [],
                "sceneIds": [],
                "aliases": [],
            },
        )
        location_record["spans"].append(
            (scene.start, min(scene.end, scene.start + len(scene.label)))
        )
        location_record["sceneIds"].append(scene_ids[scene_index])
    location_surface_counts = {
        normalized_location: sum(
            1
            for candidate_normalized, _candidate_scene_index in location_records
            if candidate_normalized == normalized_location
        )
        for normalized_location, _scene_index in location_records
    }
    for (normalized_location, scoped_scene_index), location_record in sorted(
        location_records.items(),
        key=lambda record_item: (
            record_item[1]["spans"][0][0] if record_item[1]["spans"] else len(text),
            record_item[0],
        ),
    ):
        location_spans: list[tuple[int, int]] = list(location_record["spans"])
        assigned_scene_ids = list(dict.fromkeys(location_record["sceneIds"]))
        if not assigned_scene_ids:
            continue
        location_identity_key = (
            f"{normalized_location}:scene:"
            f"{scenes[scoped_scene_index].start}:{scenes[scoped_scene_index].end}"
        )
        same_name_is_ambiguous = location_surface_counts[normalized_location] > 1
        location_id = stable_id(
            input_fingerprint,
            "locations",
            location_identity_key,
        )
        scene_assignments = []
        for assignment_ordinal, assigned_scene_id in enumerate(assigned_scene_ids):
            assigned_scene_index = scene_ids.index(assigned_scene_id)
            assignment_spans = [
                (span_start, span_end)
                for span_start, span_end in location_spans
                if scenes[assigned_scene_index].start
                <= span_start
                < scenes[assigned_scene_index].end
            ]
            if not assignment_spans:
                assignment_spans = [
                    (
                        scenes[assigned_scene_index].start,
                        min(
                            scenes[assigned_scene_index].end,
                            scenes[assigned_scene_index].start + 1,
                        ),
                    )
                ]
            assignment_start, assignment_end = assignment_spans[0]
            scene_assignments.append(
                {
                    "assignmentId": stable_id(
                        location_id,
                        "scene-assignment",
                        assigned_scene_id,
                    ),
                    "locationId": location_id,
                    "sceneId": assigned_scene_id,
                    "role": "primary" if assignment_ordinal == 0 else "secondary",
                    "confidence": confidence(
                        0.68 if same_name_is_ambiguous else 0.9,
                        (
                            "scene_scoped_ambiguous_location_label"
                            if same_name_is_ambiguous
                            else "narrative_or_scene_location"
                        ),
                    ),
                    "evidence": [
                        _evidence(
                            assignment_start,
                            assignment_end,
                            "location_scene_assignment",
                            0.9,
                        )
                    ],
                }
            )
        collections["locations"].append(
            _entity(
                input_fingerprint=input_fingerprint,
                collection="locations",
                ordinal=len(collections["locations"]),
                identity_key=location_identity_key,
                payload={
                    "locationId": location_id,
                    "canonicalName": location_record["display"],
                    "normalizedCanonicalName": normalized_location,
                    "displayName": location_record["display"],
                    "normalizedName": normalized_location,
                    "aliases": sorted(
                        set(location_record["aliases"]),
                        key=str.casefold,
                    ),
                    "kind": "unknown",
                    "parentLocationId": None,
                    "firstSceneId": assigned_scene_ids[0],
                    "sceneCount": len(assigned_scene_ids),
                    "sceneIds": assigned_scene_ids,
                    "sceneAssignments": scene_assignments,
                },
                score=0.68 if same_name_is_ambiguous else 0.9,
                basis=(
                    "scene_scoped_ambiguous_location_label"
                    if same_name_is_ambiguous
                    else "narrative_or_scene_location"
                ),
                evidence=[
                    _evidence(
                        span_start,
                        span_end,
                        "location_mention",
                        0.85,
                    )
                    for span_start, span_end in location_spans
                ],
                start=location_spans[0][0] if location_spans else None,
                end=location_spans[0][1] if location_spans else None,
                warnings=(
                    [
                        warning(
                            "LOCATION_IDENTITY_AMBIGUOUS",
                            "The same normalized location label occurs in another "
                            "scene without explicit co-reference evidence; identities "
                            "remain separate.",
                        )
                    ]
                    if same_name_is_ambiguous
                    else []
                ),
            )
        )
    observe("setting", ("locations",))

    previous_event_id: str | None = None
    for scene_index, scene in enumerate(scenes):
        scene_text = text[scene.start : scene.end]
        backward_signal = _BACKWARD_TIME.search(scene_text)
        forward_signal = _FORWARD_TIME.search(scene_text)
        simultaneous_signal = _SIMULTANEOUS_TIME.search(scene_text)
        explicit_time_signal = _EXPLICIT_DATE_TIME.search(scene_text)
        temporal_signals = list(_TEMPORAL_SIGNAL.finditer(scene_text))
        temporal_conflict = backward_signal is not None and forward_signal is not None
        ordering = "unknown"
        story_ordinal: int | None = None
        if temporal_conflict:
            ordering = "conflicting"
        elif backward_signal is not None:
            ordering, story_ordinal = "before_previous", max(0, scene_index - 1)
        elif forward_signal is not None:
            ordering, story_ordinal = "after_previous", scene_index
        elif simultaneous_signal is not None:
            ordering = "overlaps_previous"
        elif explicit_time_signal is not None:
            ordering = "absolute_time_unknown"
        event_kind = (
            "unknown"
            if temporal_conflict
            else "flashback"
            if backward_signal is not None
            else "flashforward"
            if forward_signal is not None
            else "relative_time"
            if simultaneous_signal is not None or explicit_time_signal is not None
            else "present_action"
        )
        temporal_evidence_values: list[tuple[int, int, str, float]] = [
            (
                scene.start + temporal_signal.start(),
                scene.start + temporal_signal.end(),
                "explicit_temporal_signal",
                0.9,
            )
            for temporal_signal in temporal_signals
        ]
        if explicit_time_signal is not None:
            temporal_evidence_values.append(
                (
                    scene.start + explicit_time_signal.start(),
                    scene.start + explicit_time_signal.end(),
                    "explicit_date_or_time_expression",
                    0.94,
                )
            )
        temporal_evidence = [
            _evidence(start, end, basis, score)
            for start, end, basis, score in dict.fromkeys(temporal_evidence_values)
        ][:MAX_EVIDENCE_SPANS]
        event_basis = (
            "conflicting_explicit_temporal_signals"
            if temporal_conflict
            else "explicit_backward_time_signal"
            if backward_signal is not None
            else "explicit_forward_time_signal"
            if forward_signal is not None
            else "explicit_simultaneous_time_signal"
            if simultaneous_signal is not None
            else "explicit_date_or_time_expression"
            if explicit_time_signal is not None
            else "story_order_unknown"
        )
        event_score = (
            0.95
            if temporal_conflict
            else 0.94
            if explicit_time_signal is not None
            else 0.9
            if temporal_signals
            else 0.0
        )
        event_location_id = next(
            (
                str(location_item["entityId"])
                for location_item in collections["locations"]
                if scene_ids[scene_index] in location_item["payload"]["sceneIds"]
            ),
            None,
        )
        event_participants = list(
            dict.fromkeys(
                str(mention_record["resolved"])
                for mention_record in mention_records
                if mention_record["resolved"] is not None
                and int(mention_record["start"]) >= scene.start
                and int(mention_record["end"]) <= scene.end
            )
        )
        event = _entity(
            input_fingerprint=input_fingerprint,
            collection="timeline-events",
            ordinal=scene_index,
            identity_key=f"scene:{scene.start}:{scene.end}",
            payload={
                "timelineEventId": stable_id(
                    input_fingerprint,
                    "timeline-events",
                    f"scene:{scene.start}:{scene.end}",
                ),
                "chapterId": chapter_ids[scene.parent or 0],
                "sceneId": scene_ids[scene_index],
                "kind": event_kind,
                "label": f"Timeline event {scene_index + 1}",
                "narrativeOrdinal": scene_index,
                "chronologicalOrdinal": story_ordinal,
                "storyOrdinal": story_ordinal,
                "orderingState": ordering,
                "timeExpressionState": (
                    "explicit" if explicit_time_signal is not None else "unknown"
                ),
                "locationId": event_location_id,
                "participantCharacterIds": event_participants,
            },
            score=event_score,
            basis=event_basis,
            evidence=(
                temporal_evidence
                if temporal_evidence
                else [
                    _evidence(
                        scene.start,
                        min(scene.end, scene.start + 80),
                        "scene_order",
                        0.0,
                    )
                ]
            ),
            start=scene.start,
            end=scene.end,
            parent_id=scene_ids[scene_index],
            warnings=(
                [
                    warning(
                        "TEMPORAL_SIGNALS_CONFLICT",
                        "Explicit backward and forward signals conflict inside "
                        "the same scene; no chronology was invented.",
                    )
                ]
                if temporal_conflict
                else [
                    warning(
                        "STORY_CHRONOLOGY_UNKNOWN",
                        "Story-time order is not explicit.",
                    )
                ]
                if not temporal_signals and explicit_time_signal is None
                else []
            ),
        )
        event_id = str(event["entityId"])
        collections["timeline-events"].append(event)
        if previous_event_id is not None:
            constraint_kind = (
                "before"
                if ordering == "before_previous"
                else "after"
                if ordering == "after_previous"
                else "overlaps"
                if ordering == "overlaps_previous"
                else "unknown"
            )
            constraint_score = (
                0.95 if temporal_conflict else 0.9 if constraint_kind != "unknown" else 0.0
            )
            collections["temporal-constraints"].append(
                _entity(
                    input_fingerprint=input_fingerprint,
                    collection="temporal-constraints",
                    ordinal=len(collections["temporal-constraints"]),
                    identity_key=f"{previous_event_id}:{event_id}",
                    payload={
                        "temporalConstraintId": stable_id(
                            input_fingerprint,
                            "temporal-constraints",
                            f"{previous_event_id}:{event_id}",
                        ),
                        "sourceEventId": previous_event_id,
                        "targetEventId": event_id,
                        "fromEventId": previous_event_id,
                        "toEventId": event_id,
                        "relation": constraint_kind,
                        "approximate": (not temporal_signals and explicit_time_signal is None),
                        "status": (
                            "conflicting"
                            if temporal_conflict
                            else "consistent"
                            if constraint_kind != "unknown"
                            else "unresolved"
                        ),
                    },
                    score=constraint_score,
                    basis=(
                        "conflicting_explicit_temporal_signals"
                        if temporal_conflict
                        else event_basis
                        if constraint_kind != "unknown"
                        else "unknown_ordering"
                    ),
                    evidence=(
                        temporal_evidence
                        if temporal_evidence
                        else [
                            _evidence(
                                scene.start,
                                min(scene.end, scene.start + 80),
                                "narrative_adjacency_context",
                                0.0,
                            )
                        ]
                    ),
                    warnings=(
                        [
                            warning(
                                "TEMPORAL_SIGNALS_CONFLICT",
                                "Explicit temporal signals conflict; relation remains unknown.",
                            )
                        ]
                        if temporal_conflict
                        else [
                            warning(
                                "TEMPORAL_ORDER_UNKNOWN",
                                "No story-time ordering was inferred.",
                            )
                        ]
                        if constraint_kind == "unknown"
                        else []
                    ),
                )
            )
        previous_event_id = event_id
    observe("timeline", ("timeline-events", "temporal-constraints"))

    relationship_terms = re.compile(
        r"(?i)\b(?:accused|alliance|ally|betrayed|distrust|enemy|feared|followed|"
        r"friend|hated|help|loved|offered|partnership|protected|rivalry|trusted|trust)\b"
    )
    last_pair_by_scene: dict[int, tuple[str, str]] = {}
    previous_relationship_by_pair: dict[tuple[str, str], str] = {}
    for relationship_sentence in _SENTENCE.finditer(text):
        guard()
        relationship_term = relationship_terms.search(relationship_sentence.group(0))
        if relationship_term is None:
            continue
        relationship_scene_index = next(
            (
                scene_index
                for scene_index, scene in enumerate(scenes)
                if scene.start <= relationship_sentence.start() < scene.end
            ),
            None,
        )
        if relationship_scene_index is None:
            continue
        sentence_character_ids = list(
            dict.fromkeys(
                str(mention_record["resolved"])
                for mention_record in mention_records
                if mention_record["resolved"] is not None
                and int(mention_record["start"]) >= relationship_sentence.start()
                and int(mention_record["end"]) <= relationship_sentence.end()
            )
        )
        if len(sentence_character_ids) >= 2:
            relationship_pair = (
                sentence_character_ids[0],
                sentence_character_ids[1],
            )
            last_pair_by_scene[relationship_scene_index] = relationship_pair
        elif len(sentence_character_ids) == 1:
            prior_pair = last_pair_by_scene.get(relationship_scene_index)
            if prior_pair is None:
                continue
            counterpart = next(
                (
                    character_id
                    for character_id in prior_pair
                    if character_id != sentence_character_ids[0]
                ),
                None,
            )
            if counterpart is None:
                continue
            relationship_pair = (sentence_character_ids[0], counterpart)
            last_pair_by_scene[relationship_scene_index] = relationship_pair
        else:
            prior_pair = last_pair_by_scene.get(relationship_scene_index)
            if prior_pair is None:
                continue
            relationship_pair = prior_pair
        source_character_id, target_character_id = relationship_pair
        if source_character_id == target_character_id:
            continue
        normalized_term = relationship_term.group(0).casefold()
        relationship_kind = (
            "adversarial"
            if normalized_term in {"accused", "betrayed", "distrust", "enemy", "hated", "rivalry"}
            else "alliance"
            if normalized_term
            in {"alliance", "ally", "friend", "help", "offered", "protected", "trust", "trusted"}
            else "romantic"
            if normalized_term == "loved"
            else "authority"
            if normalized_term == "followed"
            else "professional"
        )
        sentence_text = relationship_sentence.group(0).casefold()
        relationship_change = (
            "reversed"
            if any(term in sentence_text for term in ("eased", "ended", "abandoned"))
            else "strengthened"
            if any(term in sentence_text for term in ("alliance", "trust", "help"))
            else "weakened"
            if any(term in sentence_text for term in ("distrust", "betrayed", "rivalry"))
            else "established"
        )
        relationship_scene_id = scene_ids[relationship_scene_index]
        relationship_chapter_id = chapter_ids[scenes[relationship_scene_index].parent or 0]
        relationship_event_id = str(
            collections["timeline-events"][relationship_scene_index]["entityId"]
        )
        relationship_identity = (
            f"{relationship_sentence.start()}:{relationship_sentence.end()}:"
            f"{source_character_id}:{target_character_id}"
        )
        relationship_id = stable_id(
            input_fingerprint,
            "relationships",
            relationship_identity,
        )
        pair_key: tuple[str, str] = (
            min(source_character_id, target_character_id),
            max(source_character_id, target_character_id),
        )
        previous_relationship_id = previous_relationship_by_pair.get(pair_key)
        relationship_payload: dict[str, Any] = {
            "relationshipId": relationship_id,
            "sourceCharacterId": source_character_id,
            "targetCharacterId": target_character_id,
            "sourceCandidateCharacterIds": [source_character_id],
            "targetCandidateCharacterIds": [target_character_id],
            "resolution": "resolved",
            "sceneId": relationship_scene_id,
            "chapterId": relationship_chapter_id,
            "scope": {
                "kind": "scene",
                "firstSceneId": relationship_scene_id,
                "lastSceneId": relationship_scene_id,
                "sourceRange": {
                    "startOffset": relationship_sentence.start(),
                    "endOffset": relationship_sentence.end(),
                },
            },
            "validFromEventId": relationship_event_id,
            "validThroughEventId": relationship_event_id,
            "kind": relationship_kind,
            "state": normalized_term,
            "change": relationship_change,
        }
        if previous_relationship_id is not None:
            relationship_payload["previousRelationshipId"] = previous_relationship_id
        collections["relationships"].append(
            _entity(
                input_fingerprint=input_fingerprint,
                collection="relationships",
                ordinal=len(collections["relationships"]),
                identity_key=relationship_identity,
                payload=relationship_payload,
                score=0.86,
                basis="explicit_relationship_language_with_resolved_pair",
                evidence=[
                    _evidence(
                        relationship_sentence.start(),
                        relationship_sentence.end(),
                        "relationship_statement",
                        0.86,
                    )
                ],
                start=relationship_sentence.start(),
                end=relationship_sentence.end(),
            )
        )
        previous_relationship_by_pair[pair_key] = relationship_id
    observe("relationships", ("relationships",))

    previous_emotion_by_subject: dict[str, str] = {}
    emotion_measurements = {
        "fear": (-0.7, 0.85),
        "anger": (-0.65, 0.9),
        "sadness": (-0.75, 0.45),
        "joy": (0.8, 0.65),
        "surprise": (0.0, 0.9),
        "calm": (0.55, 0.2),
    }
    for sentence in _SENTENCE.finditer(text):
        guard()
        lowered = sentence.group(0).casefold()
        for emotion, terms in _EMOTIONS.items():
            if not any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms):
                continue
            emotion_scene_index = next(
                (
                    scene_index
                    for scene_index, scene in enumerate(scenes)
                    if scene.start <= sentence.start() < scene.end
                ),
                0,
            )
            emotion_character_ids = list(
                dict.fromkeys(
                    str(mention_record["resolved"])
                    for mention_record in mention_records
                    if mention_record["resolved"] is not None
                    and sentence.start() <= int(mention_record["start"])
                    and int(mention_record["end"]) <= sentence.end()
                )
            )
            subject_type = "character" if len(emotion_character_ids) == 1 else "scene"
            subject_id = (
                emotion_character_ids[0]
                if subject_type == "character"
                else scene_ids[emotion_scene_index]
            )
            emotion_identity = f"{sentence.start()}:{emotion}:{subject_id}"
            emotional_state_id = stable_id(
                input_fingerprint,
                "emotional-states",
                emotion_identity,
            )
            emotion_payload: dict[str, Any] = {
                "emotionalStateId": emotional_state_id,
                "subjectType": subject_type,
                "sceneId": scene_ids[emotion_scene_index],
                "emotion": emotion,
                "note": "Explicit emotional language appears in this source span.",
                "valence": emotion_measurements[emotion][0],
                "arousal": emotion_measurements[emotion][1],
                "intensity": 0.78,
                "progression": (
                    "shifted" if subject_id in previous_emotion_by_subject else "initial"
                ),
            }
            if subject_type == "character":
                emotion_payload["characterId"] = subject_id
            previous_emotional_state_id = previous_emotion_by_subject.get(subject_id)
            if previous_emotional_state_id is not None:
                emotion_payload["previousEmotionalStateId"] = previous_emotional_state_id
            emotion_warnings: list[dict[str, Any]] = []
            emotion_evidence = [
                _evidence(
                    sentence.start(),
                    sentence.end(),
                    "emotion_statement",
                    0.78,
                )
            ]
            for cue_pattern, cue_basis, cue_code, cue_message in (
                (
                    _URGENCY_CUE,
                    "explicit_urgency_cue",
                    "EMOTIONAL_URGENCY_CUE",
                    "Urgency language is explicit; its emotional cause is not inferred.",
                ),
                (
                    _RESTRAINT_CUE,
                    "explicit_restraint_cue",
                    "EMOTIONAL_RESTRAINT_CUE",
                    "Restraint language is explicit; concealed emotion is not inferred.",
                ),
                (
                    _SUBTEXT_CUE,
                    "explicit_subtext_contrast_cue",
                    "EMOTIONAL_SUBTEXT_REQUIRES_REVIEW",
                    "A contrast cue may indicate subtext; interpretation requires review.",
                ),
            ):
                cue_match = cue_pattern.search(sentence.group(0))
                if cue_match is None:
                    continue
                emotion_evidence.append(
                    _evidence(
                        sentence.start() + cue_match.start(),
                        sentence.start() + cue_match.end(),
                        cue_basis,
                        0.75,
                    )
                )
                emotion_warnings.append(warning(cue_code, cue_message))
            collections["emotional-states"].append(
                _entity(
                    input_fingerprint=input_fingerprint,
                    collection="emotional-states",
                    ordinal=len(collections["emotional-states"]),
                    identity_key=emotion_identity,
                    payload=emotion_payload,
                    score=0.78,
                    basis="explicit_emotion_lexeme",
                    evidence=emotion_evidence,
                    start=sentence.start(),
                    end=sentence.end(),
                    parent_id=scene_ids[emotion_scene_index],
                    warnings=emotion_warnings,
                )
            )
            previous_emotion_by_subject[subject_id] = emotional_state_id

    for ordinal, dialogue in enumerate(dialogues):
        guard()
        lowered = re.sub(
            r"(?s)^[^a-z0-9]+|[^a-z0-9?!]+$",
            "",
            text[dialogue.quote_start : dialogue.quote_end].casefold(),
        )
        if lowered.endswith("?"):
            intent, intent_score, intent_basis = "question", 0.92, "question_mark"
        elif re.match(r"(?i)^(?:please|listen|look|go|stop|tell|give|leave)\b", lowered):
            intent, intent_score, intent_basis = "direct", 0.82, "imperative_form"
        elif re.match(r"(?i)^(?:hide|close|open|hold|take|bring|wait|run)\b", lowered):
            intent, intent_score, intent_basis = "command", 0.9, "imperative_command"
        elif any(
            value in lowered
            for value in (
                "i was wrong",
                "i am sorry",
                "i'm sorry",
                "forgive me",
                "you were right",
            )
        ):
            intent, intent_score, intent_basis = (
                "connect",
                0.88,
                "relationship_repair_phrase",
            )
        elif any(value in lowered for value in ("trust me", "believe me", "i promise")):
            intent, intent_score, intent_basis = "persuade", 0.8, "persuasive_phrase"
        elif any(value in lowered for value in ("don't worry", "it is okay", "you're safe")):
            intent, intent_score, intent_basis = "reassure", 0.8, "reassurance_phrase"
        else:
            intent, intent_score, intent_basis = (
                "unknown",
                0.0,
                "insufficient_intent_evidence",
            )
        dialogue_context_start = max(
            scenes[dialogue.scene_index].start,
            dialogue.start - 160,
        )
        dialogue_context_end = min(
            scenes[dialogue.scene_index].end,
            dialogue.end + 160,
        )
        dialogue_context = text[dialogue_context_start:dialogue_context_end]
        intent_warnings: list[dict[str, Any]] = []
        intent_evidence = [
            _evidence(
                dialogue.start,
                dialogue.end,
                "dialogue_intent",
                intent_score,
            )
        ]
        for cue_pattern, cue_basis, cue_code, cue_message in (
            (
                _URGENCY_CUE,
                "bounded_urgency_context",
                "DRAMATIC_URGENCY_CUE",
                "Urgency language is present near this dialogue; intent is not "
                "expanded beyond the bounded rule.",
            ),
            (
                _RESTRAINT_CUE,
                "bounded_restraint_context",
                "DRAMATIC_RESTRAINT_CUE",
                "Restraint language is present near this dialogue; concealed "
                "intent is not inferred.",
            ),
            (
                _SUBTEXT_CUE,
                "bounded_subtext_context",
                "DRAMATIC_SUBTEXT_REQUIRES_REVIEW",
                "A contrast cue may indicate subtext; interpretation requires review.",
            ),
        ):
            cue_match = cue_pattern.search(dialogue_context)
            if cue_match is None:
                continue
            intent_evidence.append(
                _evidence(
                    dialogue_context_start + cue_match.start(),
                    dialogue_context_start + cue_match.end(),
                    cue_basis,
                    0.75,
                )
            )
            intent_warnings.append(warning(cue_code, cue_message))
        if intent == "unknown":
            intent_warnings.insert(
                0,
                warning(
                    "DRAMATIC_INTENT_UNKNOWN",
                    "Dramatic intent remains unknown.",
                ),
            )
        collections["dramatic-intents"].append(
            _entity(
                input_fingerprint=input_fingerprint,
                collection="dramatic-intents",
                ordinal=ordinal,
                identity_key=f"dialogue:{dialogue.start}:{dialogue.end}",
                payload={
                    "dramaticIntentId": stable_id(
                        input_fingerprint,
                        "dramatic-intents",
                        f"dialogue:{dialogue.start}:{dialogue.end}",
                    ),
                    "subjectType": "dialogue",
                    "sceneId": scene_ids[dialogue.scene_index],
                    "dialogueLineId": dialogue_ids[ordinal],
                    "intent": intent,
                    "dramaticFunction": (
                        "tension"
                        if intent in {"question", "direct", "command", "persuade"}
                        else "relationship_change"
                        if intent == "connect"
                        else "character_development"
                        if intent == "reassure"
                        else "unknown"
                    ),
                    "note": (
                        "A bounded dialogue-form rule identified this intent."
                        if intent != "unknown"
                        else "No bounded dialogue-form rule identified an intent."
                    ),
                    "status": "pursued" if intent != "unknown" else "uncertain",
                },
                score=intent_score,
                basis=intent_basis,
                evidence=intent_evidence,
                start=dialogue.start,
                end=dialogue.end,
                parent_id=dialogue_ids[ordinal],
                warnings=intent_warnings,
            )
        )
    observe("emotion_intent", ("emotional-states", "dramatic-intents"))

    def timeline_event_id_at(offset: int) -> str:
        scene_index = next(
            (
                candidate_index
                for candidate_index, candidate_scene in enumerate(scenes)
                if candidate_scene.start <= offset < candidate_scene.end
            ),
            0,
        )
        return str(collections["timeline-events"][scene_index]["entityId"])

    def append_uncertainty_finding(
        *,
        identity_key: str,
        category: str,
        explanation: str,
        suggested_action: str,
        related_entity_ids: list[str],
        evidence: list[dict[str, Any]],
        basis: str,
        score: float,
        severity: str = "warning",
        start: int | None = None,
        end: int | None = None,
    ) -> None:
        bounded_related_ids = list(dict.fromkeys(related_entity_ids))[:32]
        bounded_evidence = [
            dict(value) for value in evidence if int(value["startOffset"]) < int(value["endOffset"])
        ][:MAX_EVIDENCE_SPANS]
        if not bounded_related_ids or not bounded_evidence:
            return
        collections["continuity-findings"].append(
            _entity(
                input_fingerprint=input_fingerprint,
                collection="continuity-findings",
                ordinal=len(collections["continuity-findings"]),
                identity_key=identity_key,
                payload={
                    "continuityFindingId": stable_id(
                        input_fingerprint,
                        "continuity-findings",
                        identity_key,
                    ),
                    "category": category,
                    "kind": category,
                    "severity": severity,
                    "machineStatus": "open",
                    "status": "open",
                    "disposition": "unresolved",
                    "explanation": explanation,
                    "suggestedReviewAction": suggested_action,
                    "suggestedAction": suggested_action,
                    "relatedEntityIds": bounded_related_ids,
                    "requiresHumanReview": True,
                    "effectiveAuthority": "runtime_agent",
                },
                score=score,
                basis=basis,
                evidence=bounded_evidence,
                start=start,
                end=end,
            )
        )

    properties: dict[tuple[str, str], tuple[str, int, int]] = {}
    for property_match in _PROPERTY.finditer(text):
        guard()
        property_subject = property_match.group(1).casefold()
        property_key = (property_subject, property_match.group(3).casefold())
        property_value = property_match.group(2).casefold()
        previous_property = properties.get(property_key)
        if previous_property is not None and previous_property[0] != property_value:
            continuity_identity = (
                f"property:{property_key[0]}:{property_key[1]}:{property_match.start()}"
            )
            collections["continuity-findings"].append(
                _entity(
                    input_fingerprint=input_fingerprint,
                    collection="continuity-findings",
                    ordinal=len(collections["continuity-findings"]),
                    identity_key=continuity_identity,
                    payload={
                        "continuityFindingId": stable_id(
                            input_fingerprint,
                            "continuity-findings",
                            continuity_identity,
                        ),
                        "category": "attribute_conflict",
                        "kind": "attribute_conflict",
                        "severity": "warning",
                        "machineStatus": "open",
                        "status": "open",
                        "disposition": "unresolved",
                        "subject": property_match.group(1),
                        "previousValue": previous_property[0],
                        "currentValue": property_value,
                        "explanation": "Two explicit attribute values conflict.",
                        "suggestedReviewAction": ("Confirm whether the change is intentional."),
                        "suggestedAction": "Confirm whether the change is intentional.",
                        "relatedEntityIds": list(
                            dict.fromkeys(
                                (
                                    timeline_event_id_at(previous_property[1]),
                                    timeline_event_id_at(property_match.start()),
                                )
                            )
                        ),
                        "requiresHumanReview": True,
                        "effectiveAuthority": "runtime_agent",
                    },
                    score=0.9,
                    basis="conflicting_explicit_properties",
                    evidence=[
                        _evidence(
                            previous_property[1],
                            previous_property[2],
                            "previous_property",
                            0.9,
                        ),
                        _evidence(
                            property_match.start(),
                            property_match.end(),
                            "current_property",
                            0.9,
                        ),
                    ],
                    start=property_match.start(),
                    end=property_match.end(),
                )
            )
        properties[property_key] = (
            property_value,
            property_match.start(),
            property_match.end(),
        )

    damaged_objects: dict[str, tuple[int, int, str]] = {}
    for damage_match in _OBJECT_DAMAGE.finditer(text):
        guard()
        object_name = damage_match.group("object").casefold()
        damage_state_match = re.search(
            r"(?i)\b(shattered|broken|destroyed|torn|smashed|cracked)\b",
            damage_match.group(0),
        )
        if damage_state_match is None:
            continue
        damaged_objects[object_name] = (
            damage_match.start(),
            damage_match.end(),
            damage_state_match.group(1).casefold(),
        )
    for restored_match in _OBJECT_RESTORED.finditer(text):
        guard()
        object_name = restored_match.group("object").casefold()
        prior_damage = damaged_objects.get(object_name)
        if prior_damage is None or prior_damage[0] >= restored_match.start():
            continue
        restored_state_match = re.search(
            r"(?i)\b(unbroken|whole|intact|restored|repaired)\b",
            restored_match.group(0),
        )
        restored_state = (
            restored_state_match.group(1).casefold()
            if restored_state_match is not None
            else "restored"
        )
        continuity_identity = (
            f"object-state:{object_name}:{prior_damage[0]}:{restored_match.start()}"
        )
        collections["continuity-findings"].append(
            _entity(
                input_fingerprint=input_fingerprint,
                collection="continuity-findings",
                ordinal=len(collections["continuity-findings"]),
                identity_key=continuity_identity,
                payload={
                    "continuityFindingId": stable_id(
                        input_fingerprint,
                        "continuity-findings",
                        continuity_identity,
                    ),
                    "category": "unexplained_object_state_change",
                    "kind": "unexplained_object_state_change",
                    "severity": "warning",
                    "machineStatus": "open",
                    "status": "open",
                    "disposition": "unresolved",
                    "subject": object_name,
                    "previousValue": prior_damage[2],
                    "currentValue": restored_state,
                    "explanation": (
                        "An object is explicitly damaged and later appears restored "
                        "without an explanation."
                    ),
                    "suggestedReviewAction": (
                        "Confirm the restoration is intentional or add an explanation."
                    ),
                    "suggestedAction": (
                        "Confirm the restoration is intentional or add an explanation."
                    ),
                    "relatedEntityIds": list(
                        dict.fromkeys(
                            (
                                timeline_event_id_at(prior_damage[0]),
                                timeline_event_id_at(restored_match.start()),
                            )
                        )
                    ),
                    "requiresHumanReview": True,
                    "effectiveAuthority": "runtime_agent",
                },
                score=0.96,
                basis="conflicting_explicit_object_states",
                evidence=[
                    _evidence(
                        prior_damage[0],
                        prior_damage[1],
                        "damaged_object_state",
                        0.96,
                    ),
                    _evidence(
                        restored_match.start(),
                        restored_match.end(),
                        "restored_object_state",
                        0.96,
                    ),
                ],
                start=restored_match.start(),
                end=restored_match.end(),
            )
        )
    unresolved_dialogue_items = [
        value for value in collections["dialogue-lines"] if value["payload"]["requiresHumanReview"]
    ]
    unresolved_dialogue = len(unresolved_dialogue_items)
    if unresolved_dialogue:
        unresolved_dialogue_evidence = [
            dict(evidence) for value in unresolved_dialogue_items for evidence in value["evidence"]
        ][:MAX_EVIDENCE_SPANS]
        continuity_identity = "unresolved-dialogue-summary"
        collections["continuity-findings"].append(
            _entity(
                input_fingerprint=input_fingerprint,
                collection="continuity-findings",
                ordinal=len(collections["continuity-findings"]),
                identity_key=continuity_identity,
                payload={
                    "continuityFindingId": stable_id(
                        input_fingerprint,
                        "continuity-findings",
                        continuity_identity,
                    ),
                    "category": "dialogue_speaker_conflict",
                    "kind": "dialogue_speaker_conflict",
                    "severity": "warning",
                    "machineStatus": "open",
                    "status": "open",
                    "disposition": "unresolved",
                    "count": unresolved_dialogue,
                    "explanation": "One or more dialogue speakers remain unresolved.",
                    "suggestedReviewAction": "Review the bounded speaker candidates.",
                    "suggestedAction": "Review the bounded speaker candidates.",
                    "relatedEntityIds": [value["entityId"] for value in unresolved_dialogue_items][
                        :32
                    ],
                    "requiresHumanReview": True,
                    "effectiveAuthority": "runtime_agent",
                },
                score=1.0,
                basis="unresolved_dialogue_count",
                evidence=unresolved_dialogue_evidence,
            )
        )

    ambiguous_character_groups: dict[str, list[dict[str, Any]]] = {}
    for character_item in collections["characters"]:
        guard()
        if character_item["payload"]["identityStatus"] != "ambiguous":
            continue
        normalized_name = str(character_item["payload"]["normalizedCanonicalName"])
        ambiguous_character_groups.setdefault(normalized_name, []).append(character_item)
    for normalized_name, character_items in sorted(ambiguous_character_groups.items()):
        if len(character_items) < 2:
            continue
        append_uncertainty_finding(
            identity_key=f"possible-duplicate-character:{normalized_name}",
            category="possible_duplicate_character",
            explanation=(
                "Multiple scene-derived character identities share the same "
                "normalized name; equivalence was not assumed."
            ),
            suggested_action=(
                "Review the exact identity evidence before merging these characters."
            ),
            related_entity_ids=[str(value["entityId"]) for value in character_items],
            evidence=[
                dict(evidence) for value in character_items for evidence in value["evidence"]
            ],
            basis="duplicate_normalized_character_name",
            score=0.8,
            start=min(int(value["startOffset"]) for value in character_items),
            end=max(int(value["endOffset"]) for value in character_items),
        )

    for temporal_constraint in collections["temporal-constraints"]:
        guard()
        if temporal_constraint["payload"]["status"] != "conflicting":
            continue
        append_uncertainty_finding(
            identity_key=f"chronology:{temporal_constraint['entityId']}",
            category="chronology_conflict",
            explanation=(
                "The same bounded scene contains explicit backward and forward "
                "story-time signals; their relation remains unknown."
            ),
            suggested_action="Confirm the intended story-time relation.",
            related_entity_ids=[
                str(temporal_constraint["payload"]["sourceEventId"]),
                str(temporal_constraint["payload"]["targetEventId"]),
            ],
            evidence=[dict(value) for value in temporal_constraint["evidence"]],
            basis="conflicting_explicit_temporal_signals",
            score=0.95,
        )

    location_groups: dict[str, list[dict[str, Any]]] = {}
    for location_item in collections["locations"]:
        guard()
        normalized_name = str(location_item["payload"]["normalizedCanonicalName"])
        location_groups.setdefault(normalized_name, []).append(location_item)
    for normalized_name, location_items in sorted(location_groups.items()):
        if len(location_items) < 2:
            continue
        append_uncertainty_finding(
            identity_key=f"location-label-ambiguity:{normalized_name}",
            category="location_conflict",
            explanation=(
                "The same normalized location label occurs in separate scenes, "
                "but no exact co-reference evidence establishes one location."
            ),
            suggested_action=(
                "Review the scene-scoped evidence before merging location identities."
            ),
            related_entity_ids=[str(value["entityId"]) for value in location_items],
            evidence=[dict(evidence) for value in location_items for evidence in value["evidence"]],
            basis="scene_scoped_ambiguous_location_label",
            score=0.68,
            severity="info",
            start=min(int(value["startOffset"]) for value in location_items),
            end=max(int(value["endOffset"]) for value in location_items),
        )

    uncertain_pov_items = [
        value
        for value in collections["pov-segments"]
        if value["payload"]["shiftKind"] == "uncertain"
        or value["payload"]["mode"] in {"mixed", "unknown"}
    ]
    if uncertain_pov_items:
        append_uncertainty_finding(
            identity_key="pov-continuity-uncertainty",
            category="pov_discontinuity",
            explanation=(
                "One or more POV segments lack enough bounded evidence to establish "
                "continuous viewpoint; no discontinuity was asserted."
            ),
            suggested_action="Review the flagged POV spans and confirm viewpoint continuity.",
            related_entity_ids=[str(value["entityId"]) for value in uncertain_pov_items],
            evidence=[
                dict(evidence) for value in uncertain_pov_items for evidence in value["evidence"]
            ],
            basis="uncertain_pov_continuity",
            score=0.0,
            severity="info",
            start=min(int(value["startOffset"]) for value in uncertain_pov_items),
            end=max(int(value["endOffset"]) for value in uncertain_pov_items),
        )

    inferred_scene_items = [
        value for value in collections["scenes"] if value["payload"]["boundaryKind"] == "inferred"
    ]
    if inferred_scene_items:
        append_uncertainty_finding(
            identity_key="scene-boundary-uncertainty",
            category="scene_boundary_uncertainty",
            explanation=(
                "One or more scene boundaries were structural fallbacks rather "
                "than explicit scene headings."
            ),
            suggested_action="Review inferred scene boundaries before approval.",
            related_entity_ids=[str(value["entityId"]) for value in inferred_scene_items],
            evidence=[
                dict(evidence) for value in inferred_scene_items for evidence in value["evidence"]
            ],
            basis="inferred_scene_boundary",
            score=0.72,
            severity="info",
            start=min(int(value["startOffset"]) for value in inferred_scene_items),
            end=max(int(value["endOffset"]) for value in inferred_scene_items),
        )

    extraction_offsets = [offset for offset, character in enumerate(text) if character == "\ufffd"]
    ascii_quote_offsets = [offset for offset, character in enumerate(text) if character == '"']
    if len(ascii_quote_offsets) % 2:
        extraction_offsets.append(ascii_quote_offsets[-1])
    extraction_offsets = list(dict.fromkeys(extraction_offsets))[:MAX_EVIDENCE_SPANS]
    if extraction_offsets:
        related_scene_ids = [
            scene_ids[
                next(
                    (
                        scene_index
                        for scene_index, scene in enumerate(scenes)
                        if scene.start <= offset < scene.end
                    ),
                    0,
                )
            ]
            for offset in extraction_offsets
        ]
        append_uncertainty_finding(
            identity_key="source-extraction-uncertainty",
            category="extraction_uncertainty",
            explanation=(
                "The approved text contains a replacement character or an "
                "unbalanced quotation delimiter; affected semantics remain uncertain."
            ),
            suggested_action="Review the exact source spans and, if needed, re-import.",
            related_entity_ids=related_scene_ids,
            evidence=[
                _evidence(
                    offset,
                    offset + 1,
                    "source_extraction_uncertainty",
                    1.0,
                )
                for offset in extraction_offsets
            ],
            basis="source_extraction_uncertainty",
            score=1.0,
            start=min(extraction_offsets),
            end=max(extraction_offsets) + 1,
        )
    observe("continuity", ("continuity-findings",))

    # Some navigation fields are completed only after all child entities exist.
    # Recompute every machine fingerprint from the final semantic value so persisted
    # entity fingerprints cannot describe a pre-linking intermediate state.
    for values in collections.values():
        for entity_value in values:
            guard()
            entity_value["fingerprint"] = request_fingerprint(
                {key: item for key, item in entity_value.items() if key != "fingerprint"}
            )
    collection_fingerprints = {
        name: request_fingerprint(values) for name, values in collections.items()
    }
    gate_fingerprints = {
        "story_structure_review": request_fingerprint(
            {
                name: collection_fingerprints[name]
                for name in ("chapters", "scenes", "beats", "pov-segments")
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
    summary = {
        "collectionCounts": {name: len(values) for name, values in collections.items()},
        "warningCount": sum(
            len(value["warnings"]) for values in collections.values() for value in values
        ),
        "requiresHumanReview": unresolved_dialogue > 0,
    }
    output_identity = {
        "analysisContractVersion": ANALYSIS_CONTRACT_VERSION,
        "profile": profile.to_wire(),
        "profileFingerprint": profile.fingerprint,
        "agentRegistryFingerprint": AGENT_REGISTRY_FINGERPRINT,
        "inputFingerprint": input_fingerprint,
        "correctionSetFingerprint": correction_set_fingerprint,
        "collections": collections,
        "collectionFingerprints": collection_fingerprints,
        "gateFingerprints": gate_fingerprints,
        "summary": summary,
    }
    output_identity["outputFingerprint"] = request_fingerprint(output_identity)
    guard(force=True)
    if result_checkpoint_observer is not None:
        result_checkpoint_observer(output_identity)
    if stage_observer is not None:
        stage_observer(
            "synthesis",
            {
                "role": "synthesis",
                "summary": summary,
                "gateFingerprints": gate_fingerprints,
                "outputFingerprint": output_identity["outputFingerprint"],
            },
        )
    return output_identity


def agent_envelopes(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return bounded immutable envelopes with no timestamps or manuscript content."""

    values: list[dict[str, Any]] = []
    for ordinal, definition in enumerate(AGENT_REGISTRY):
        output = {
            collection: result["collectionFingerprints"][collection]
            for collection in definition.collections
        }
        if definition.role == "synthesis":
            output = {
                "wholeBook": result["outputFingerprint"],
                "summary": request_fingerprint(result["summary"]),
            }
        warnings = [
            warning_value
            for collection in definition.collections
            for entity in result["collections"][collection]
            for warning_value in entity["warnings"]
        ][:32]
        envelope = {
            "schemaVersion": ANALYSIS_CONTRACT_VERSION,
            "ordinal": ordinal,
            "role": definition.role,
            "agentId": definition.agent_id,
            "agentVersion": definition.version,
            "acceptedInputs": {
                "inputFingerprint": result["inputFingerprint"],
                "profileFingerprint": result["profileFingerprint"],
                "correctionSetFingerprint": result["correctionSetFingerprint"],
            },
            "typedOutputs": output,
            "outcome": "succeeded",
            "confidence": confidence(
                1.0 if not warnings else 0.8,
                "deterministic_bounded_rules",
            ),
            "warnings": warnings,
            "requiresHumanReview": any(
                bool(value.get("requiresHumanReview")) for value in warnings
            ),
            "retryPolicy": {"maxAttempts": 3, "retryableFailures": ["internal"]},
            "failurePolicy": "fail_closed_without_partial_publication",
            "checkpointPolicy": "after_agent_boundary",
            "provenance": {
                "producerType": "runtime_agent",
                "providerId": "local-deterministic-rules",
                "modelId": None,
                "modelVersion": None,
                "determinism": "deterministic",
                "cost": {
                    "currency": "USD",
                    "amount": 0,
                    "state": "known_zero",
                },
            },
        }
        envelope["outputFingerprint"] = request_fingerprint(output)
        envelope["envelopeFingerprint"] = request_fingerprint(envelope)
        values.append(envelope)
    return values


def stage_snapshots(result: dict[str, Any]) -> list[dict[str, Any]]:
    groups = (
        ("structure", ("chapters", "scenes", "beats", "narration-spans")),
        ("characters", ("characters", "mentions", "dialogue-lines")),
        ("world", ("pov-segments", "locations", "timeline-events", "temporal-constraints")),
        (
            "narrative",
            ("relationships", "emotional-states", "dramatic-intents", "continuity-findings"),
        ),
        ("synthesis", ENTITY_COLLECTIONS),
    )
    snapshots: list[dict[str, Any]] = []
    for ordinal, (stage, names) in enumerate(groups):
        manifest = {
            "stage": stage,
            "collections": {
                name: {
                    "count": len(result["collections"][name]),
                    "fingerprint": result["collectionFingerprints"][name],
                }
                for name in names
            },
        }
        snapshots.append(
            {
                "ordinal": ordinal,
                "stage": stage,
                "entityCount": sum(len(result["collections"][name]) for name in names),
                "fingerprint": request_fingerprint(manifest),
                "manifest": manifest,
            }
        )
    return snapshots


def canonical_profile_json(profile: AnalysisProfile = DEFAULT_ANALYSIS_PROFILE) -> str:
    return canonical_json(profile.to_wire())
