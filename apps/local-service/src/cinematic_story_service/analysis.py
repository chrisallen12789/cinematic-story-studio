from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import ServiceError
from .util import (
    ANALYZER_ID,
    ANALYZER_VERSION,
    SCHEMA_VERSION,
    provenance,
    sha256_text,
    stable_id,
    text_span,
)

MAX_DERIVED_ENTITIES = 20_000
_DERIVED_COLLECTIONS = (
    "chapters",
    "scenes",
    "beats",
    "characters",
    "dialogueLines",
    "dialogueAttributions",
)

_CHAPTER_HEADING = re.compile(r"(?m)^(#)[ \t]+(.+?)[ \t]*(?:\r?\n|$)")
_SCENE_HEADING = re.compile(
    r"(?im)^(?:#{2,6}[ \t]+.+|(?:INT|EXT|INT/EXT|EXT/INT)\.[^\r\n]+)[ \t]*(?:\r?\n|$)"
)
_SCENE_SEPARATOR = re.compile(r"(?m)^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*(?:\r?\n|$)")
_QUOTE = re.compile(r'["“]([^"”\r\n]+)["”]')
_PREFIX_SPEAKER = re.compile(
    r"(?:^|[\r\n])[ \t]*(?:\*\*)?([A-Z][A-Za-z0-9'’-]*(?:[ \t]+"
    r"[A-Z][A-Za-z0-9'’-]*){0,2})(?:\*\*)?[ \t]*:[ \t]*$"
)
_UPPER_SPEAKER = re.compile(
    r"(?:^|[\r\n])[ \t]*([A-Z][A-Z0-9'’-]*(?:[ \t]+[A-Z][A-Z0-9'’-]*){0,2})"
    r"[ \t]*(?:\r?\n)[ \t]*$"
)
_SUFFIX_NAME_VERB = re.compile(
    r"^[ \t]*(?:[,;.!?—-]+[ \t]*)?([A-Z][A-Za-z'’-]*(?:[ \t]+"
    r"[A-Z][A-Za-z'’-]*){0,2})[ \t]+"
    r"(?:said|asked|replied|answered|whispered|murmured|called|shouted|cried|added)\b"
)
_SUFFIX_VERB_NAME = re.compile(
    r"^[ \t]*(?:[,;.!?—-]+[ \t]*)?"
    r"(?:said|asked|replied|answered|whispered|murmured|called|shouted|cried|added)"
    r"[ \t]+([A-Z][A-Za-z'’-]*(?:[ \t]+[A-Z][A-Za-z'’-]*){0,2})\b"
)
_NON_CHARACTER_NAMES = {
    "A",
    "An",
    "Chapter",
    "Ext",
    "He",
    "I",
    "Int",
    "It",
    "Narrator",
    "Scene",
    "She",
    "The",
    "They",
    "We",
    "You",
}


@dataclass(frozen=True, slots=True)
class _Range:
    start: int
    end: int
    title: str | None = None
    heading: str | None = None


@dataclass(frozen=True, slots=True)
class _Dialogue:
    start: int
    end: int
    text: str
    speaker_name: str | None
    speaker_start: int | None
    confidence: float
    basis: str


def validate_analysis_entity_limit(analysis: dict[str, Any]) -> None:
    """Reject a projection that exceeds the desktop/service collection contract."""

    total = 0
    for collection_name in _DERIVED_COLLECTIONS:
        collection = analysis.get(collection_name)
        if not isinstance(collection, list):
            raise ServiceError(
                500,
                "ANALYSIS_PROJECTION_INVALID",
                "The analysis projection is invalid.",
            )
        total += len(collection)
        if total > MAX_DERIVED_ENTITIES:
            raise ServiceError(
                422,
                "ANALYSIS_ENTITY_LIMIT_EXCEEDED",
                "The analysis contains too many derived entities.",
                retryable=False,
            )


def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _chapters(text: str) -> list[_Range]:
    headings = list(_CHAPTER_HEADING.finditer(text))
    if not headings:
        return [_Range(0, len(text), title=None)]
    result: list[_Range] = []
    if text[: headings[0].start()].strip():
        result.append(_Range(0, headings[0].start(), title=None))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        result.append(_Range(heading.start(), end, title=heading.group(2).strip()))
    return result


def _scenes(text: str, chapter: _Range) -> list[_Range]:
    chapter_text = text[chapter.start : chapter.end]
    content_start = chapter.start
    chapter_heading = _CHAPTER_HEADING.match(chapter_text)
    if chapter_heading is not None:
        content_start = chapter.start + chapter_heading.end()

    boundaries: list[tuple[int, str | None]] = [(content_start, None)]
    for match in _SCENE_HEADING.finditer(text, content_start, chapter.end):
        heading = match.group(0).strip().lstrip("#").strip()
        boundaries.append((match.start(), heading))
    for match in _SCENE_SEPARATOR.finditer(text, content_start, chapter.end):
        boundaries.append((match.end(), None))
    boundaries.sort(key=lambda item: item[0])

    unique: list[tuple[int, str | None]] = []
    for start, current_heading in boundaries:
        if unique and start == unique[-1][0]:
            if current_heading is not None:
                unique[-1] = (start, current_heading)
            continue
        unique.append((start, current_heading))

    result: list[_Range] = []
    for index, (start, current_heading) in enumerate(unique):
        end = unique[index + 1][0] if index + 1 < len(unique) else chapter.end
        trimmed_start, trimmed_end = _trim_range(text, start, end)
        if trimmed_start >= trimmed_end:
            continue
        result.append(_Range(start, end, heading=current_heading))
    if not result:
        result.append(_Range(chapter.start, chapter.end))
    return result


def _clean_character_name(candidate: str) -> str | None:
    name = " ".join(candidate.replace("**", "").split()).strip(" ,:;.!?—-")
    if not name or len(name) > 80:
        return None
    display_name = " ".join(part if not part.isupper() else part.title() for part in name.split())
    if display_name in _NON_CHARACTER_NAMES:
        return None
    return display_name


def _infer_speaker(
    text: str,
    scene_start: int,
    scene_end: int,
    match: re.Match[str],
) -> tuple[str | None, int | None, float, str]:
    line_start = max(
        text.rfind("\n", scene_start, match.start()),
        text.rfind("\r", scene_start, match.start()),
    )
    line_start = scene_start if line_start < 0 else line_start + 1
    line_end_candidates = [
        position
        for position in (
            text.find("\n", match.end(), scene_end),
            text.find("\r", match.end(), scene_end),
        )
        if position >= 0
    ]
    line_end = min(line_end_candidates) if line_end_candidates else scene_end
    prefix = text[line_start : match.start()]
    suffix = text[match.end() : line_end]

    prefix_match = _PREFIX_SPEAKER.search(prefix)
    if prefix_match is not None:
        name = _clean_character_name(prefix_match.group(1))
        if name is not None:
            return name, line_start + prefix_match.start(1), 0.98, "explicit_name_prefix"

    previous_context_start = max(scene_start, line_start - 160)
    previous_context = text[previous_context_start : match.start()]
    uppercase_match = _UPPER_SPEAKER.search(previous_context)
    if uppercase_match is not None:
        name = _clean_character_name(uppercase_match.group(1))
        if name is not None:
            return (
                name,
                previous_context_start + uppercase_match.start(1),
                0.94,
                "screenplay_speaker_label",
            )

    for expression, basis in (
        (_SUFFIX_NAME_VERB, "explicit_name_speech_verb"),
        (_SUFFIX_VERB_NAME, "speech_verb_explicit_name"),
    ):
        suffix_match = expression.search(suffix)
        if suffix_match is not None:
            name = _clean_character_name(suffix_match.group(1))
            if name is not None:
                return name, match.end() + suffix_match.start(1), 0.92, basis

    return None, None, 0.25, "no_explicit_speaker_evidence"


def _dialogues(text: str, scene: _Range) -> list[_Dialogue]:
    result: list[_Dialogue] = []
    for match in _QUOTE.finditer(text, scene.start, scene.end):
        speaker, speaker_start, confidence, basis = _infer_speaker(
            text, scene.start, scene.end, match
        )
        result.append(
            _Dialogue(
                start=match.start(1),
                end=match.end(1),
                text=match.group(1),
                speaker_name=speaker,
                speaker_start=speaker_start,
                confidence=confidence,
                basis=basis,
            )
        )
    return result


def _scene_content_range(text: str, scene: _Range) -> tuple[int, int]:
    """Exclude structural Markdown markers from derived narration spans."""

    start = scene.start
    heading = _SCENE_HEADING.match(text, start, scene.end)
    if heading is not None:
        start = heading.end()

    end = scene.end
    separators = list(_SCENE_SEPARATOR.finditer(text, start, end))
    if separators and separators[-1].end() == end:
        end = separators[-1].start()
    return _trim_range(text, start, end)


def _derived_provenance(
    *,
    story_id: str,
    story_revision: int,
    input_fingerprint: str,
    recorded_at: str,
) -> dict[str, Any]:
    return provenance(
        origin="runtime_agent",
        actor_id=f"{ANALYZER_ID}@{ANALYZER_VERSION}",
        recorded_at=recorded_at,
        source_references=[
            {"entityType": "ImportedStory", "entityId": story_id, "revision": story_revision}
        ],
        input_fingerprint=input_fingerprint,
        notes="Deterministic local analysis; no story content was transmitted.",
    )


def analyze_story(
    *,
    project_id: str,
    story_id: str,
    story_revision: int,
    source_document_id: str,
    text: str,
    input_fingerprint: str,
    recorded_at: str,
) -> dict[str, Any]:
    """Return a deterministic, source-grounded analysis projection.

    IDs, ordering, spans, confidence methods, and warnings are deterministic for a frozen story.
    The only operational value supplied by the caller is the provenance timestamp.
    """

    text_hash = sha256_text(text)
    derived_provenance = _derived_provenance(
        story_id=story_id,
        story_revision=story_revision,
        input_fingerprint=input_fingerprint,
        recorded_at=recorded_at,
    )

    chapter_specs = _chapters(text)
    scene_specs: list[tuple[int, int, _Range]] = []
    dialogue_specs: list[tuple[int, int, _Dialogue]] = []
    for chapter_ordinal, chapter_spec in enumerate(chapter_specs):
        for scene_ordinal, scene_spec in enumerate(_scenes(text, chapter_spec)):
            scene_specs.append((chapter_ordinal, scene_ordinal, scene_spec))
            for dialogue in _dialogues(text, scene_spec):
                dialogue_specs.append((len(scene_specs) - 1, len(dialogue_specs), dialogue))

    character_evidence: dict[str, list[tuple[int, int]]] = {}
    character_display: dict[str, str] = {}
    for _, _, dialogue in dialogue_specs:
        if dialogue.speaker_name is None or dialogue.speaker_start is None:
            continue
        normalized = dialogue.speaker_name.casefold()
        character_display.setdefault(normalized, dialogue.speaker_name)
        character_evidence.setdefault(normalized, []).append(
            (dialogue.speaker_start, dialogue.speaker_start + len(dialogue.speaker_name))
        )

    characters: list[dict[str, Any]] = []
    character_ids: dict[str, str] = {}
    for normalized, character_spans in sorted(
        character_evidence.items(), key=lambda item: (item[1][0][0], item[0])
    ):
        character_id = stable_id(story_id, "character", normalized)
        character_ids[normalized] = character_id
        characters.append(
            {
                "id": character_id,
                "displayName": character_display[normalized],
                "normalizedName": normalized,
                "aliases": [],
                "evidence": [
                    text_span(
                        source_document_id=source_document_id,
                        text=text,
                        start=start,
                        end=end,
                        text_sha256=text_hash,
                    )
                    for start, end in character_spans
                ],
                "revision": 1,
                "confidence": {
                    "score": 0.96,
                    "basis": "repeated_explicit_dialogue_attribution"
                    if len(character_spans) > 1
                    else "explicit_dialogue_attribution",
                    "calibrationId": f"{ANALYZER_ID}-{ANALYZER_VERSION}",
                },
                "warnings": [],
                "provenance": derived_provenance,
            }
        )

    chapters: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    beats: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    attributions: list[dict[str, Any]] = []
    scene_ids_by_chapter: dict[int, list[str]] = {}

    for chapter_ordinal, chapter_spec in enumerate(chapter_specs):
        chapter_id = stable_id(story_id, "chapter", chapter_ordinal, chapter_spec.start)
        chapters.append(
            {
                "id": chapter_id,
                "ordinal": chapter_ordinal,
                "title": chapter_spec.title,
                "start": chapter_spec.start,
                "end": chapter_spec.end,
                "revision": 1,
                "provenance": derived_provenance,
            }
        )
        scene_ids_by_chapter[chapter_ordinal] = []

    for flat_scene_ordinal, (chapter_ordinal, scene_ordinal, scene_spec) in enumerate(scene_specs):
        chapter_id = chapters[chapter_ordinal]["id"]
        scene_id = stable_id(story_id, "scene", chapter_ordinal, scene_ordinal, scene_spec.start)
        scene_ids_by_chapter[chapter_ordinal].append(scene_id)
        scene_dialogues = [
            dialogue
            for scene_index, _, dialogue in dialogue_specs
            if scene_index == flat_scene_ordinal
        ]
        scene_character_ids = [
            character_ids[dialogue.speaker_name.casefold()]
            for dialogue in scene_dialogues
            if dialogue.speaker_name is not None
            and dialogue.speaker_name.casefold() in character_ids
        ]
        scene_character_ids = list(dict.fromkeys(scene_character_ids))

        heading = scene_spec.heading
        location = None
        if heading and re.match(r"(?i)^(?:INT|EXT|INT/EXT|EXT/INT)\.", heading):
            location = heading.split("-", 1)[0].strip()
        scenes.append(
            {
                "id": scene_id,
                "chapterId": chapter_id,
                "ordinal": scene_ordinal,
                "heading": heading,
                "location": location,
                "mood": None,
                "start": scene_spec.start,
                "end": scene_spec.end,
                "characterIds": scene_character_ids,
                "revision": 1,
                "confidence": {
                    "score": 0.95 if heading else 0.78,
                    "basis": "explicit_scene_boundary" if heading else "chapter_content_segment",
                    "calibrationId": f"{ANALYZER_ID}-{ANALYZER_VERSION}",
                },
                "warnings": [],
                "provenance": derived_provenance,
            }
        )

        dialogue_by_start = {dialogue.start: dialogue for dialogue in scene_dialogues}
        content_start, content_end = _scene_content_range(text, scene_spec)
        cursor = content_start
        components: list[tuple[int, int, str, _Dialogue | None]] = []
        for dialogue in scene_dialogues:
            narration_start, narration_end = _trim_range(text, cursor, dialogue.start)
            if narration_start < narration_end:
                components.append((narration_start, narration_end, "narration", None))
            components.append((dialogue.start, dialogue.end, "dialogue", dialogue))
            cursor = dialogue.end
        narration_start, narration_end = _trim_range(text, cursor, content_end)
        if narration_start < narration_end:
            components.append((narration_start, narration_end, "narration", None))
        if not components:
            start, end = _trim_range(text, content_start, content_end)
            if start < end:
                components.append((start, end, "narration", None))

        line_ordinal = 0
        for beat_ordinal, (start, end, kind, component_dialogue) in enumerate(components):
            beat_id = stable_id(scene_id, "beat", beat_ordinal, start, end, kind)
            beat: dict[str, Any] = {
                "id": beat_id,
                "sceneId": scene_id,
                "ordinal": beat_ordinal,
                "kind": kind,
                "start": start,
                "end": end,
                "summary": text[start:end][:280] if kind != "dialogue" else None,
                "revision": 1,
                "provenance": derived_provenance,
            }
            if component_dialogue is not None:
                # dict lookup asserts that this component originated from the current parsed quote.
                assert dialogue_by_start[start] is component_dialogue
                line_id = stable_id(story_id, "dialogue", start, end)
                attribution_id = stable_id(line_id, "attribution")
                speaker_id = (
                    character_ids.get(component_dialogue.speaker_name.casefold())
                    if component_dialogue.speaker_name is not None
                    else None
                )
                warnings: list[dict[str, Any]] = []
                if speaker_id is None:
                    warnings.append(
                        {
                            "code": "DIALOGUE_SPEAKER_UNCERTAIN",
                            "severity": "warning",
                            "message": (
                                "No explicit speaker evidence was found; human review is required."
                            ),
                            "requiresHumanReview": True,
                            "relatedEntities": [
                                {
                                    "entityType": "DialogueLine",
                                    "entityId": line_id,
                                    "revision": 1,
                                }
                            ],
                        }
                    )
                beat["dialogueLineId"] = line_id
                lines.append(
                    {
                        "id": line_id,
                        "sceneId": scene_id,
                        "beatId": beat_id,
                        "ordinal": line_ordinal,
                        "start": start,
                        "end": end,
                        "verbatimText": component_dialogue.text,
                        "textSha256": sha256_text(component_dialogue.text),
                        "attributionId": attribution_id,
                        "revision": 1,
                        "provenance": derived_provenance,
                    }
                )
                attribution_evidence: list[dict[str, Any]] = []
                if (
                    component_dialogue.speaker_start is not None
                    and component_dialogue.speaker_name is not None
                ):
                    attribution_evidence.append(
                        text_span(
                            source_document_id=source_document_id,
                            text=text,
                            start=component_dialogue.speaker_start,
                            end=component_dialogue.speaker_start
                            + len(component_dialogue.speaker_name),
                            text_sha256=text_hash,
                        )
                    )
                attributions.append(
                    {
                        "id": attribution_id,
                        "lineId": line_id,
                        "proposedSpeakerId": speaker_id,
                        "effectiveSpeakerId": speaker_id,
                        "effectiveAuthority": "runtime_agent",
                        "evidence": attribution_evidence,
                        "revision": 1,
                        "confidence": {
                            "score": component_dialogue.confidence,
                            "basis": component_dialogue.basis,
                            "calibrationId": f"{ANALYZER_ID}-{ANALYZER_VERSION}",
                        },
                        "warnings": warnings,
                        "provenance": derived_provenance,
                        "updatedAt": recorded_at,
                    }
                )
                line_ordinal += 1
            beats.append(beat)

    result: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "producerId": ANALYZER_ID,
        "producerVersion": ANALYZER_VERSION,
        "inputRevision": story_revision,
        "inputFingerprint": input_fingerprint,
        "chapters": chapters,
        "scenes": scenes,
        "beats": beats,
        "characters": characters,
        "dialogueLines": lines,
        "dialogueAttributions": attributions,
    }
    validate_analysis_entity_limit(result)
    return result
