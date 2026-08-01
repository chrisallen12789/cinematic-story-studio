"""Reviewable, span-preserving text normalization for short speech auditions.

Phase 3B never sends an opaque rewrite of manuscript text to a provider.  This
module produces an immutable plan whose edits are individually visible and
fingerprinted.  Provider-required edits cannot be declined; optional edits are
applied only when their IDs are explicitly accepted.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from .util import request_fingerprint, sha256_text, stable_id

MAX_AUDITION_TEXT_CODE_POINTS: Final = 4_000
MAX_NORMALIZATION_EDITS: Final = 2_000
MAX_ACCEPTED_NORMALIZATION_EDIT_IDS: Final = MAX_NORMALIZATION_EDITS
MAX_UNSUPPORTED_CHARACTER_CODE_POINTS: Final = 32
MAX_NORMALIZATION_WARNINGS: Final = 32
NORMALIZATION_PROFILE_ID: Final = "audition-text-normalization"
NORMALIZATION_PROFILE_VERSION: Final = "1.0.0"
FIXTURE_PROVIDER_ID: Final = "deterministic-pcm-wav-fixture"
KOKORO_PROVIDER_ID: Final = "kokoro-local-onnx"
NORMALIZATION_REVIEW_REQUIRED_WARNING: Final = "NORMALIZATION_REVIEW_REQUIRED"

NormalizationKind = Literal[
    "line_ending",
    "control_whitespace",
    "unicode_composition",
    "typographic_quote",
    "typographic_dash",
    "ellipsis",
]

_FORBIDDEN_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RAW_MARKUP = re.compile(r"<\s*/?\s*(?:speak|voice|prosody|phoneme|audio|break)\b", re.I)
_KOKORO_SUPPORTED_NON_ASCII_PUNCTUATION: Final = frozenset(
    {
        "\u00ab",
        "\u00bb",
        "\u2010",
        "\u2011",
        "\u2012",
        "\u2013",
        "\u2014",
        "\u2015",
        "\u2018",
        "\u2019",
        "\u201a",
        "\u201b",
        "\u201c",
        "\u201d",
        "\u201e",
        "\u201f",
        "\u2026",
    }
)


class TextNormalizationError(ValueError):
    """The source text or requested normalization decision is unsafe."""


@dataclass(frozen=True, slots=True)
class NormalizationEdit:
    edit_id: str
    source_start: int
    source_end: int
    original: str
    replacement: str
    kind: NormalizationKind
    required_by_provider: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "editId": self.edit_id,
            "sourceStart": self.source_start,
            "sourceEnd": self.source_end,
            "original": self.original,
            "replacement": self.replacement,
            "kind": self.kind,
            "requiredByProvider": self.required_by_provider,
        }


@dataclass(frozen=True, slots=True)
class UnsupportedCharacterClassification:
    provider_id: str
    profile_id: str
    profile_version: str
    unsupported_character_code_points: tuple[str, ...]
    warnings: tuple[str, ...]
    fingerprint: str

    @property
    def human_review_required(self) -> bool:
        return bool(self.unsupported_character_code_points)

    def as_dict(self) -> dict[str, object]:
        return {
            "providerId": self.provider_id,
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "unsupportedCharacterCodePoints": list(self.unsupported_character_code_points),
            "warnings": list(self.warnings),
            "humanReviewRequired": self.human_review_required,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class NormalizationPlan:
    source_sha256: str
    normalized_text: str
    normalized_text_sha256: str
    edits: tuple[NormalizationEdit, ...]
    accepted_optional_edit_ids: tuple[str, ...]
    provider_id: str
    profile_id: str
    profile_version: str
    unsupported_character_code_points: tuple[str, ...]
    warnings: tuple[str, ...]
    fingerprint: str

    @property
    def human_review_required(self) -> bool:
        return bool(self.unsupported_character_code_points)

    def as_dict(self, *, include_text: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "sourceSha256": self.source_sha256,
            "normalizedTextSha256": self.normalized_text_sha256,
            "edits": [edit.as_dict() for edit in self.edits],
            "acceptedOptionalEditIds": list(self.accepted_optional_edit_ids),
            "providerId": self.provider_id,
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "unsupportedCharacterCodePoints": list(self.unsupported_character_code_points),
            "warnings": list(self.warnings),
            "humanReviewRequired": self.human_review_required,
            "fingerprint": self.fingerprint,
        }
        if include_text:
            result["normalizedText"] = self.normalized_text
        return result


def _validate_source(text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise TextNormalizationError("Audition text must contain visible characters.")
    if len(text) > MAX_AUDITION_TEXT_CODE_POINTS:
        raise TextNormalizationError("Audition text exceeds the bounded preview limit.")
    if _FORBIDDEN_CONTROL.search(text):
        raise TextNormalizationError("Audition text contains forbidden control characters.")
    if _RAW_MARKUP.search(text):
        raise TextNormalizationError("Raw provider or SSML markup is not accepted.")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
        raise TextNormalizationError("Audition text contains an invalid Unicode scalar value.")


def _validate_provider_profile(
    provider_id: str,
    profile_id: str,
    profile_version: str,
) -> None:
    values = (provider_id, profile_id, profile_version)
    if any(not isinstance(value, str) or not value or len(value) > 128 for value in values):
        raise TextNormalizationError("The normalization provider/profile identity is invalid.")
    if (
        provider_id not in {FIXTURE_PROVIDER_ID, KOKORO_PROVIDER_ID}
        or profile_id != NORMALIZATION_PROFILE_ID
        or profile_version != NORMALIZATION_PROFILE_VERSION
    ):
        raise TextNormalizationError("The normalization provider/profile is unsupported.")


def _kokoro_supports_character(character: str) -> bool:
    code_point = ord(character)
    if character == "\n" or 0x20 <= code_point <= 0x7E:
        return True
    if character in _KOKORO_SUPPORTED_NON_ASCII_PUNCTUATION:
        return True
    return unicodedata.category(character).startswith("L") and "LATIN" in unicodedata.name(
        character, ""
    )


def classify_unsupported_characters(
    text: str,
    *,
    provider_id: str,
    profile_id: str = NORMALIZATION_PROFILE_ID,
    profile_version: str = NORMALIZATION_PROFILE_VERSION,
) -> UnsupportedCharacterClassification:
    """Classify provider/profile-specific code points without rewriting text.

    The fixture provider consumes any valid Unicode scalar deterministically.  The
    real Kokoro profile is deliberately conservative: printable ASCII, Latin
    letters, newlines, and an explicit reviewed punctuation set are supported.
    Every other distinct code point is surfaced for human review.
    """

    _validate_source(text)
    _validate_provider_profile(provider_id, profile_id, profile_version)
    unsupported_ordinals: set[int] = set()
    if provider_id == KOKORO_PROVIDER_ID:
        for character in text:
            code_point = ord(character)
            if _kokoro_supports_character(character) or code_point in unsupported_ordinals:
                continue
            if len(unsupported_ordinals) >= MAX_UNSUPPORTED_CHARACTER_CODE_POINTS:
                raise TextNormalizationError(
                    "Audition text exceeds the bounded unsupported-character limit."
                )
            unsupported_ordinals.add(code_point)

    unsupported = tuple(f"U+{code_point:04X}" for code_point in sorted(unsupported_ordinals))
    warnings = (NORMALIZATION_REVIEW_REQUIRED_WARNING,) if unsupported else ()
    if len(warnings) > MAX_NORMALIZATION_WARNINGS:
        raise TextNormalizationError("Normalization warnings exceed the bounded limit.")
    material: dict[str, object] = {
        "profileId": profile_id,
        "profileVersion": profile_version,
        "providerId": provider_id,
        "unsupportedCharacterCodePoints": list(unsupported),
        "warnings": list(warnings),
    }
    return UnsupportedCharacterClassification(
        provider_id=provider_id,
        profile_id=profile_id,
        profile_version=profile_version,
        unsupported_character_code_points=unsupported,
        warnings=warnings,
        fingerprint=request_fingerprint(material),
    )


def _replacement_for(character: str) -> tuple[str, NormalizationKind, bool] | None:
    if character == "\r":
        return "\n", "line_ending", True
    if character == "\t":
        return " ", "control_whitespace", True
    if character in {"\u00a0", "\u2007", "\u202f"}:
        return " ", "control_whitespace", True
    if character in {"\u2018", "\u2019"}:
        return "'", "typographic_quote", False
    if character in {"\u201c", "\u201d", "\u00ab", "\u00bb"}:
        return '"', "typographic_quote", False
    if character in {"\u2013", "\u2014"}:
        return " - ", "typographic_dash", False
    if character == "\u2026":
        return "...", "ellipsis", False
    normalized = unicodedata.normalize("NFC", character)
    if normalized != character:
        return normalized, "unicode_composition", True
    return None


def propose_normalization(text: str) -> tuple[NormalizationEdit, ...]:
    """Return bounded, non-overlapping source-coordinate edits."""

    _validate_source(text)
    edits: list[NormalizationEdit] = []
    index = 0
    while index < len(text):
        character = text[index]
        # Treat CRLF as one edit so accepting it cannot duplicate a newline.
        if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            end = index + 2
            original = "\r\n"
            replacement: tuple[str, NormalizationKind, bool] | None = (
                "\n",
                "line_ending",
                True,
            )
        else:
            end = index + 1
            original = character
            replacement = _replacement_for(character)
        if replacement is not None:
            replacement_text, kind, required = replacement
            edit_id = stable_id(
                "phase3b-normalization-edit",
                sha256_text(text),
                index,
                end,
                kind,
                replacement_text,
            )
            edits.append(
                NormalizationEdit(
                    edit_id=edit_id,
                    source_start=index,
                    source_end=end,
                    original=original,
                    replacement=replacement_text,
                    kind=kind,
                    required_by_provider=required,
                )
            )
            if len(edits) > MAX_NORMALIZATION_EDITS:
                raise TextNormalizationError("Audition text requires too many normalization edits.")
        index = end
    return tuple(edits)


def compile_normalization(
    text: str,
    *,
    accepted_optional_edit_ids: tuple[str, ...] | list[str] = (),
    provider_id: str = FIXTURE_PROVIDER_ID,
    profile_id: str = NORMALIZATION_PROFILE_ID,
    profile_version: str = NORMALIZATION_PROFILE_VERSION,
) -> NormalizationPlan:
    """Apply required edits and only explicitly accepted optional edits."""

    if not isinstance(accepted_optional_edit_ids, (tuple, list)):
        raise TextNormalizationError("Accepted normalization edits must be a bounded list.")
    if len(accepted_optional_edit_ids) > MAX_ACCEPTED_NORMALIZATION_EDIT_IDS:
        raise TextNormalizationError("Accepted normalization edits exceed the bounded limit.")
    if any(
        not isinstance(edit_id, str) or not edit_id or len(edit_id) > 128
        for edit_id in accepted_optional_edit_ids
    ):
        raise TextNormalizationError("An accepted normalization edit ID is invalid.")
    _validate_provider_profile(provider_id, profile_id, profile_version)
    edits = propose_normalization(text)
    optional_ids = {edit.edit_id for edit in edits if not edit.required_by_provider}
    accepted = tuple(sorted(set(accepted_optional_edit_ids)))
    if any(edit_id not in optional_ids for edit_id in accepted):
        raise TextNormalizationError("An accepted normalization edit is unknown or required.")

    accepted_set = set(accepted)
    output: list[str] = []
    cursor = 0
    applied: list[NormalizationEdit] = []
    for edit in edits:
        if edit.source_start < cursor or text[edit.source_start : edit.source_end] != edit.original:
            raise TextNormalizationError("The normalization plan does not match the source text.")
        should_apply = edit.required_by_provider or edit.edit_id in accepted_set
        if should_apply:
            output.append(text[cursor : edit.source_start])
            output.append(edit.replacement)
            cursor = edit.source_end
            applied.append(edit)
    output.append(text[cursor:])
    normalized = "".join(output)
    character_support = classify_unsupported_characters(
        normalized,
        provider_id=provider_id,
        profile_id=profile_id,
        profile_version=profile_version,
    )
    source_sha256 = sha256_text(text)
    normalized_text_sha256 = sha256_text(normalized)
    material: dict[str, object] = {
        "acceptedOptionalEditIds": list(accepted),
        "appliedEdits": [edit.as_dict() for edit in applied],
        "normalizedTextSha256": normalized_text_sha256,
        "profileId": profile_id,
        "profileVersion": profile_version,
        "providerId": provider_id,
        "sourceSha256": source_sha256,
        "unsupportedCharacterClassificationFingerprint": character_support.fingerprint,
        "unsupportedCharacterCodePoints": list(character_support.unsupported_character_code_points),
        "warnings": list(character_support.warnings),
    }
    return NormalizationPlan(
        source_sha256=source_sha256,
        normalized_text=normalized,
        normalized_text_sha256=normalized_text_sha256,
        edits=tuple(applied),
        accepted_optional_edit_ids=accepted,
        provider_id=provider_id,
        profile_id=profile_id,
        profile_version=profile_version,
        unsupported_character_code_points=(character_support.unsupported_character_code_points),
        warnings=character_support.warnings,
        fingerprint=request_fingerprint(material),
    )
