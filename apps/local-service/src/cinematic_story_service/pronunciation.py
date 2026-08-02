"""Typed pronunciation dictionaries and deterministic effective-plan compilation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from .util import request_fingerprint, sha256_text

MAX_PRONUNCIATION_ENTRIES: Final = 1_000
MAX_PRONUNCIATION_VALUE_CODE_POINTS: Final = 256
MAX_GRAPHEME_CODE_POINTS: Final = 120
PRONUNCIATION_PROFILE_VERSION: Final = "1.0.0"

PronunciationRepresentation = Literal["ipa", "neutral"]
PronunciationScope = Literal["project", "role", "chapter", "scene", "custom"]

_CONTROL_OR_MARKUP = re.compile(r"[\x00-\x1f\x7f<>]")
_LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
_SCOPE_RANK: dict[PronunciationScope, int] = {
    "project": 1,
    "role": 2,
    "custom": 3,
    "chapter": 4,
    "scene": 5,
}


class PronunciationError(ValueError):
    """A pronunciation entry or resolution request is ambiguous or unsafe."""


@dataclass(frozen=True, slots=True)
class PronunciationEntry:
    entry_id: str
    revision: int
    grapheme: str
    pronunciation: str
    representation: PronunciationRepresentation
    locale: str
    scope: PronunciationScope
    scope_id: str | None
    priority: int = 0
    case_sensitive: bool = False
    whole_word: bool = True
    approved: bool = False
    superseded: bool = False

    def __post_init__(self) -> None:
        if not self.entry_id or len(self.entry_id) > 128:
            raise PronunciationError("The pronunciation entry ID is invalid.")
        if self.revision < 1:
            raise PronunciationError("The pronunciation revision must be positive.")
        if not self.grapheme.strip() or len(self.grapheme) > MAX_GRAPHEME_CODE_POINTS:
            raise PronunciationError("The pronunciation grapheme is invalid.")
        if (
            not self.pronunciation.strip()
            or len(self.pronunciation) > MAX_PRONUNCIATION_VALUE_CODE_POINTS
        ):
            raise PronunciationError("The pronunciation value is invalid.")
        if _CONTROL_OR_MARKUP.search(self.grapheme) or _CONTROL_OR_MARKUP.search(
            self.pronunciation
        ):
            raise PronunciationError("Pronunciation values cannot contain control or markup text.")
        if self.representation not in {"ipa", "neutral"}:
            raise PronunciationError("The pronunciation representation is unsupported.")
        if _LOCALE.fullmatch(self.locale) is None:
            raise PronunciationError("The pronunciation locale is invalid.")
        if self.scope not in _SCOPE_RANK:
            raise PronunciationError("The pronunciation scope is invalid.")
        if self.scope == "project" and self.scope_id is not None:
            raise PronunciationError("Project pronunciation entries cannot have a scope ID.")
        if self.scope != "project" and not self.scope_id:
            raise PronunciationError("Scoped pronunciation entries require a scope ID.")
        if not -1_000 <= self.priority <= 1_000:
            raise PronunciationError("The pronunciation priority is out of range.")

    def identity_material(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "caseSensitive": self.case_sensitive,
            "entryId": self.entry_id,
            "grapheme": self.grapheme,
            "locale": self.locale,
            "priority": self.priority,
            "pronunciation": self.pronunciation,
            "representation": self.representation,
            "revision": self.revision,
            "scope": self.scope,
            "scopeId": self.scope_id,
            "superseded": self.superseded,
            "wholeWord": self.whole_word,
        }


@dataclass(frozen=True, slots=True)
class PronunciationContext:
    locale: str
    role_id: str | None = None
    chapter_id: str | None = None
    scene_id: str | None = None
    custom_scope_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _LOCALE.fullmatch(self.locale) is None:
            raise PronunciationError("The requested locale is invalid.")


@dataclass(frozen=True, slots=True)
class PronunciationSpan:
    source_start: int
    source_end: int
    grapheme: str
    pronunciation: str
    representation: PronunciationRepresentation
    entry_id: str
    entry_revision: int

    def as_dict(self) -> dict[str, object]:
        return {
            "sourceStart": self.source_start,
            "sourceEnd": self.source_end,
            "grapheme": self.grapheme,
            "pronunciation": self.pronunciation,
            "representation": self.representation,
            "entryId": self.entry_id,
            "entryRevision": self.entry_revision,
        }


@dataclass(frozen=True, slots=True)
class PronunciationPlan:
    dictionary_revision: int
    dictionary_fingerprint: str
    source_text_sha256: str
    spans: tuple[PronunciationSpan, ...]
    dependency_entry_revisions: tuple[tuple[str, int], ...]
    effective_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return {
            "dictionaryRevision": self.dictionary_revision,
            "dictionaryFingerprint": self.dictionary_fingerprint,
            "sourceTextSha256": self.source_text_sha256,
            "spans": [span.as_dict() for span in self.spans],
            "dependencyEntryRevisions": [
                {"entryId": entry_id, "revision": revision}
                for entry_id, revision in self.dependency_entry_revisions
            ],
            "effectiveFingerprint": self.effective_fingerprint,
        }


def dictionary_fingerprint(entries: Sequence[PronunciationEntry], revision: int) -> str:
    if revision < 0 or len(entries) > MAX_PRONUNCIATION_ENTRIES:
        raise PronunciationError("The pronunciation dictionary bounds are invalid.")
    ordered = sorted(
        entries,
        key=lambda entry: (entry.entry_id, entry.revision),
    )
    return request_fingerprint(
        {
            "entries": [entry.identity_material() for entry in ordered],
            "profileVersion": PRONUNCIATION_PROFILE_VERSION,
            "revision": revision,
        }
    )


def _locale_rank(entry_locale: str, requested_locale: str) -> int:
    if entry_locale == requested_locale:
        return 2
    if (
        entry_locale.split("-", 1)[0] == requested_locale.split("-", 1)[0]
        and "-" not in entry_locale
    ):
        return 1
    return 0


def _scope_applies(entry: PronunciationEntry, context: PronunciationContext) -> bool:
    if entry.scope == "project":
        return True
    if entry.scope == "role":
        return entry.scope_id == context.role_id
    if entry.scope == "chapter":
        return entry.scope_id == context.chapter_id
    if entry.scope == "scene":
        return entry.scope_id == context.scene_id
    return entry.scope_id in context.custom_scope_ids


def _select_entry(
    candidates: Sequence[PronunciationEntry], context: PronunciationContext
) -> PronunciationEntry | None:
    applicable = [
        entry
        for entry in candidates
        if entry.approved
        and not entry.superseded
        and _scope_applies(entry, context)
        and _locale_rank(entry.locale, context.locale) > 0
    ]
    if not applicable:
        return None
    ranked = sorted(
        applicable,
        key=lambda entry: (
            _SCOPE_RANK[entry.scope],
            _locale_rank(entry.locale, context.locale),
            entry.priority,
        ),
        reverse=True,
    )
    winning_rank = (
        _SCOPE_RANK[ranked[0].scope],
        _locale_rank(ranked[0].locale, context.locale),
        ranked[0].priority,
    )
    tied = [
        entry
        for entry in ranked
        if (
            _SCOPE_RANK[entry.scope],
            _locale_rank(entry.locale, context.locale),
            entry.priority,
        )
        == winning_rank
    ]
    if len(tied) > 1:
        raise PronunciationError("Pronunciation precedence is ambiguous for a matching span.")
    return ranked[0]


def compile_pronunciation_plan(
    text: str,
    *,
    entries: Sequence[PronunciationEntry],
    dictionary_revision: int,
    context: PronunciationContext,
) -> PronunciationPlan:
    if not text or len(text) > 4_000 or _CONTROL_OR_MARKUP.search(text):
        raise PronunciationError("The pronunciation source text is invalid.")
    if len(entries) > MAX_PRONUNCIATION_ENTRIES:
        raise PronunciationError("The pronunciation dictionary exceeds its entry limit.")
    global_fingerprint = dictionary_fingerprint(entries, dictionary_revision)

    by_grapheme: dict[tuple[int, str], list[PronunciationEntry]] = {}
    for entry in entries:
        key = (len(entry.grapheme), entry.grapheme.casefold())
        by_grapheme.setdefault(key, []).append(entry)

    spans: list[PronunciationSpan] = []
    # Prefer longest grapheme at a position. Overlapping entries never apply twice.
    graphemes = sorted(by_grapheme, key=lambda value: (-value[0], value[1]))
    cursor = 0
    while cursor < len(text):
        selected_span: PronunciationSpan | None = None
        for grapheme_length, grapheme_key in graphemes:
            end = cursor + grapheme_length
            source_value = text[cursor:end]
            if source_value.casefold() != grapheme_key:
                continue
            matching_entries = [
                entry
                for entry in by_grapheme[(grapheme_length, grapheme_key)]
                if (not entry.case_sensitive or source_value == entry.grapheme)
                and (
                    not entry.whole_word
                    or (
                        (cursor == 0 or not text[cursor - 1].isalnum())
                        and (end == len(text) or not text[end].isalnum())
                    )
                )
            ]
            selected_entry = _select_entry(matching_entries, context)
            if selected_entry is None:
                continue
            selected_span = PronunciationSpan(
                source_start=cursor,
                source_end=end,
                grapheme=text[cursor:end],
                pronunciation=selected_entry.pronunciation,
                representation=selected_entry.representation,
                entry_id=selected_entry.entry_id,
                entry_revision=selected_entry.revision,
            )
            break
        if selected_span is None:
            cursor += 1
        else:
            spans.append(selected_span)
            cursor = selected_span.source_end

    dependencies = tuple(sorted({(span.entry_id, span.entry_revision) for span in spans}))
    source_text_sha256 = sha256_text(text)
    material: dict[str, object] = {
        "dependencies": [
            {"entryId": entry_id, "revision": revision} for entry_id, revision in dependencies
        ],
        "profileVersion": PRONUNCIATION_PROFILE_VERSION,
        "sourceTextSha256": source_text_sha256,
        "spans": [span.as_dict() for span in spans],
    }
    return PronunciationPlan(
        dictionary_revision=dictionary_revision,
        dictionary_fingerprint=global_fingerprint,
        source_text_sha256=source_text_sha256,
        spans=tuple(spans),
        dependency_entry_revisions=dependencies,
        effective_fingerprint=request_fingerprint(material),
    )


def compile_provider_text(text: str, plan: PronunciationPlan) -> str:
    """Compile neutral typed overrides to an escaped provider-neutral marker form."""

    if sha256_text(text) != plan.source_text_sha256:
        raise PronunciationError("The pronunciation plan does not match the source text.")
    output: list[str] = []
    cursor = 0
    for span in plan.spans:
        if span.source_start < cursor or text[span.source_start : span.source_end] != span.grapheme:
            raise PronunciationError("A pronunciation span is stale or overlapping.")
        output.append(text[cursor : span.source_start])
        # This is not SSML. Brackets, slashes, and controls were rejected at entry time.
        escaped_grapheme = span.grapheme.replace("\\", "\\\\").replace("[", "\\[")
        escaped_value = span.pronunciation.replace("\\", "\\\\").replace("/", "\\/")
        output.append(f"[{escaped_grapheme}](/{escaped_value}/)")
        cursor = span.source_end
    output.append(text[cursor:])
    return "".join(output)
