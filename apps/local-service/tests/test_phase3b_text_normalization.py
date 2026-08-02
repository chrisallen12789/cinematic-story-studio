from __future__ import annotations

import pytest

from cinematic_story_service.text_normalization import (
    FIXTURE_PROVIDER_ID,
    KOKORO_PROVIDER_ID,
    MAX_AUDITION_TEXT_CODE_POINTS,
    MAX_NORMALIZATION_EDITS,
    MAX_UNSUPPORTED_CHARACTER_CODE_POINTS,
    NORMALIZATION_PROFILE_ID,
    NORMALIZATION_PROFILE_VERSION,
    NORMALIZATION_REVIEW_REQUIRED_WARNING,
    TextNormalizationError,
    classify_unsupported_characters,
    compile_normalization,
    propose_normalization,
)


def test_normalization_requires_transport_edits_and_exposes_optional_edits() -> None:
    source = "A\tcurly \u201cquote\u201d.\r\nNext\u2026"
    proposals = propose_normalization(source)

    required = [edit for edit in proposals if edit.required_by_provider]
    optional = [edit for edit in proposals if not edit.required_by_provider]
    assert {edit.kind for edit in required} == {"control_whitespace", "line_ending"}
    assert {edit.kind for edit in optional} == {"typographic_quote", "ellipsis"}
    assert all(source[edit.source_start : edit.source_end] == edit.original for edit in proposals)

    required_only = compile_normalization(source)
    assert required_only.normalized_text == "A curly \u201cquote\u201d.\nNext\u2026"
    accepted = compile_normalization(
        source,
        accepted_optional_edit_ids=[edit.edit_id for edit in optional],
    )
    assert accepted.normalized_text == 'A curly "quote".\nNext...'
    assert (
        accepted.fingerprint
        == compile_normalization(
            source,
            accepted_optional_edit_ids=tuple(reversed([edit.edit_id for edit in optional])),
        ).fingerprint
    )


def test_normalization_rejects_implicit_or_unsafe_mutation() -> None:
    with pytest.raises(TextNormalizationError, match="unknown or required"):
        compile_normalization("safe text", accepted_optional_edit_ids=["unknown"])
    with pytest.raises(TextNormalizationError, match="markup"):
        propose_normalization("<speak>hidden rewrite</speak>")
    with pytest.raises(TextNormalizationError, match="control"):
        propose_normalization("unsafe\x00text")
    with pytest.raises(TextNormalizationError, match="bounded"):
        propose_normalization("x" * (MAX_AUDITION_TEXT_CODE_POINTS + 1))


def test_normalization_fingerprint_binds_source_and_decisions_without_storing_source() -> None:
    first = compile_normalization("A \u201cchoice\u201d")
    optional = propose_normalization("A \u201cchoice\u201d")
    second = compile_normalization(
        "A \u201cchoice\u201d", accepted_optional_edit_ids=[edit.edit_id for edit in optional]
    )
    changed = compile_normalization("A \u201cchanged choice\u201d")

    assert first.fingerprint != second.fingerprint
    assert first.fingerprint != changed.fingerprint
    public = first.as_dict()
    assert "normalizedText" not in public
    assert first.as_dict(include_text=True)["normalizedText"] == first.normalized_text


def test_unsupported_character_classification_is_provider_aware_and_deduplicated() -> None:
    source = "Latin caf\u00e9; snowman \u2603, repeated \u2603, emoji \U0001f600."

    fixture = classify_unsupported_characters(
        source,
        provider_id=FIXTURE_PROVIDER_ID,
    )
    kokoro = classify_unsupported_characters(
        source,
        provider_id=KOKORO_PROVIDER_ID,
    )

    assert fixture.unsupported_character_code_points == ()
    assert fixture.warnings == ()
    assert fixture.human_review_required is False
    assert kokoro.unsupported_character_code_points == ("U+2603", "U+1F600")
    assert kokoro.warnings == (NORMALIZATION_REVIEW_REQUIRED_WARNING,)
    assert kokoro.human_review_required is True
    assert fixture.fingerprint != kokoro.fingerprint


def test_normalization_plan_binds_provider_profile_and_character_review_evidence() -> None:
    source = "Review the snowman \u2603."
    fixture = compile_normalization(source, provider_id=FIXTURE_PROVIDER_ID)
    kokoro = compile_normalization(source, provider_id=KOKORO_PROVIDER_ID)

    assert kokoro.provider_id == KOKORO_PROVIDER_ID
    assert kokoro.profile_id == NORMALIZATION_PROFILE_ID
    assert kokoro.profile_version == NORMALIZATION_PROFILE_VERSION
    assert kokoro.unsupported_character_code_points == ("U+2603",)
    assert kokoro.warnings == (NORMALIZATION_REVIEW_REQUIRED_WARNING,)
    assert kokoro.human_review_required is True
    assert fixture.fingerprint != kokoro.fingerprint
    public = kokoro.as_dict()
    assert public["unsupportedCharacterCodePoints"] == ["U+2603"]
    assert public["warnings"] == [NORMALIZATION_REVIEW_REQUIRED_WARNING]
    assert public["humanReviewRequired"] is True


def test_character_classification_fails_closed_for_unknown_or_unbounded_policy_input() -> None:
    with pytest.raises(TextNormalizationError, match="provider/profile is unsupported"):
        classify_unsupported_characters("safe text", provider_id="mutable-provider")
    with pytest.raises(TextNormalizationError, match="provider/profile is unsupported"):
        classify_unsupported_characters(
            "safe text",
            provider_id=KOKORO_PROVIDER_ID,
            profile_version="latest",
        )
    with pytest.raises(TextNormalizationError, match="Unicode scalar"):
        classify_unsupported_characters("unsafe\ud800text", provider_id=KOKORO_PROVIDER_ID)

    too_many_distinct = "".join(
        chr(0x0400 + offset) for offset in range(MAX_UNSUPPORTED_CHARACTER_CODE_POINTS + 1)
    )
    with pytest.raises(TextNormalizationError, match="bounded unsupported-character"):
        classify_unsupported_characters(
            too_many_distinct,
            provider_id=KOKORO_PROVIDER_ID,
        )

    with pytest.raises(TextNormalizationError, match="bounded list"):
        compile_normalization(
            "safe text",
            accepted_optional_edit_ids=(value for value in ()),  # type: ignore[arg-type]
        )
    with pytest.raises(TextNormalizationError, match="bounded limit"):
        compile_normalization(
            "safe text",
            accepted_optional_edit_ids=["edit"] * (MAX_NORMALIZATION_EDITS + 1),
        )
