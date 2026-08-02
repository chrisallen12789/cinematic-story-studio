from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cinematic_story_service.audition_repository import AuditionRepository
from cinematic_story_service.models import PronunciationEntryRow
from cinematic_story_service.pronunciation import (
    PronunciationContext,
    PronunciationEntry,
    PronunciationError,
    compile_pronunciation_plan,
    compile_provider_text,
)


def _entry(
    entry_id: str,
    pronunciation: str,
    *,
    scope: str = "project",
    scope_id: str | None = None,
    locale: str = "en",
    priority: int = 0,
    case_sensitive: bool = False,
    whole_word: bool = True,
) -> PronunciationEntry:
    return PronunciationEntry(
        entry_id=entry_id,
        revision=1,
        grapheme="Aster",
        pronunciation=pronunciation,
        representation="ipa",
        locale=locale,
        scope=scope,  # type: ignore[arg-type]
        scope_id=scope_id,
        priority=priority,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
        approved=True,
    )


def test_pronunciation_precedence_and_locale_are_deterministic() -> None:
    entries = [
        _entry("project", "project"),
        _entry("role", "role", scope="role", scope_id="role-1"),
        _entry("chapter", "chapter", scope="chapter", scope_id="chapter-1"),
        _entry("scene-fallback", "scene-fallback", scope="scene", scope_id="scene-1"),
        _entry(
            "scene-exact",
            "scene-exact",
            scope="scene",
            scope_id="scene-1",
            locale="en-US",
        ),
    ]
    plan = compile_pronunciation_plan(
        "Aster enters.",
        entries=entries,
        dictionary_revision=5,
        context=PronunciationContext(
            locale="en-US", role_id="role-1", chapter_id="chapter-1", scene_id="scene-1"
        ),
    )

    assert len(plan.spans) == 1
    assert plan.spans[0].entry_id == "scene-exact"
    assert plan.spans[0].pronunciation == "scene-exact"
    assert plan.dependency_entry_revisions == (("scene-exact", 1),)


def test_unrelated_dictionary_revision_does_not_change_effective_plan() -> None:
    applicable = _entry("aster", "\u02c8\u00e6st\u025a")
    unrelated = PronunciationEntry(
        entry_id="unrelated",
        revision=1,
        grapheme="Beacon",
        pronunciation="b\u02c8ik\u0259n",
        representation="ipa",
        locale="en",
        scope="project",
        scope_id=None,
        approved=True,
    )
    first = compile_pronunciation_plan(
        "Aster enters.",
        entries=[applicable, unrelated],
        dictionary_revision=2,
        context=PronunciationContext(locale="en-US"),
    )
    changed_unrelated = replace(unrelated, revision=2, pronunciation="changed")
    second = compile_pronunciation_plan(
        "Aster enters.",
        entries=[applicable, changed_unrelated],
        dictionary_revision=3,
        context=PronunciationContext(locale="en-US"),
    )
    changed_applicable = compile_pronunciation_plan(
        "Aster enters.",
        entries=[replace(applicable, revision=2, pronunciation="changed"), unrelated],
        dictionary_revision=3,
        context=PronunciationContext(locale="en-US"),
    )

    assert first.dictionary_fingerprint != second.dictionary_fingerprint
    assert first.effective_fingerprint == second.effective_fingerprint
    assert first.effective_fingerprint != changed_applicable.effective_fingerprint


def test_pronunciation_ties_markup_and_stale_compilation_fail_closed() -> None:
    with pytest.raises(PronunciationError, match="ambiguous"):
        compile_pronunciation_plan(
            "Aster",
            entries=[_entry("one", "one"), _entry("two", "two")],
            dictionary_revision=1,
            context=PronunciationContext(locale="en-US"),
        )
    with pytest.raises(PronunciationError, match="markup"):
        _entry("bad", "<phoneme>bad</phoneme>")

    plan = compile_pronunciation_plan(
        "Aster",
        entries=[_entry("good", "\u02c8\u00e6st\u025a")],
        dictionary_revision=1,
        context=PronunciationContext(locale="en-US"),
    )
    assert compile_provider_text("Aster", plan) == "[Aster](/\u02c8\u00e6st\u025a/)"
    with pytest.raises(PronunciationError, match="does not match"):
        compile_provider_text("Changed", plan)


def test_case_and_match_rules_are_applied_exactly() -> None:
    case_sensitive = _entry(
        "case-sensitive",
        "exact",
        case_sensitive=True,
    )
    phrase = replace(
        _entry("phrase", "embedded", whole_word=False),
        grapheme="ster",
    )

    wrong_case = compile_pronunciation_plan(
        "aster",
        entries=[case_sensitive],
        dictionary_revision=1,
        context=PronunciationContext(locale="en-US"),
    )
    exact_case = compile_pronunciation_plan(
        "Aster",
        entries=[case_sensitive],
        dictionary_revision=1,
        context=PronunciationContext(locale="en-US"),
    )
    embedded_phrase = compile_pronunciation_plan(
        "Aster",
        entries=[phrase],
        dictionary_revision=1,
        context=PronunciationContext(locale="en-US"),
    )
    whole_word = compile_pronunciation_plan(
        "Asteroid",
        entries=[_entry("whole-word", "blocked")],
        dictionary_revision=1,
        context=PronunciationContext(locale="en-US"),
    )

    assert wrong_case.spans == ()
    assert [span.entry_id for span in exact_case.spans] == ["case-sensitive"]
    assert [span.entry_id for span in embedded_phrase.spans] == ["phrase"]
    assert whole_word.spans == ()


def test_pronunciation_mutation_payload_is_bounded_and_replays_exactly(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = cast(AuditionRepository, client.app.state.auditions)
    entry = PronunciationEntryRow(
        project_id="repository-owned-project",
        entry_id="repository-owned-entry",
        provenance_json="{}",
    )
    monkeypatch.setattr(
        repository,
        "_pronunciation_entry_wire",
        lambda _session, _entry: {"entryId": "repository-owned-entry"},
    )
    monkeypatch.setattr(
        repository,
        "_dictionary_wire",
        lambda _session, _project_id, _dictionary: {"revision": 7},
    )
    invalidated = [f"invalidated-{index:04d}" for index in reversed(range(2_205))]
    preserved = [f"preserved-{index:04d}" for index in reversed(range(2_111))]
    # Duplicate identities must not inflate exact counts or samples.
    invalidated.append(invalidated[0])
    preserved.append(preserved[0])

    with Session() as session:
        result = repository._pronunciation_mutation_wire(
            session,
            entry,
            None,
            invalidated_clip_ids=invalidated,
            preserved_clip_ids=preserved,
            invalidated_gate_ids=("pronunciation_review", "voice_readiness_review"),
        )
        repository._record_pronunciation_mutation_result(entry, result)
        replay = repository._pronunciation_mutation_replay_wire(
            session,
            entry,
            None,
        )

    assert result["invalidatedClipCount"] == 2_205
    assert result["preservedClipCount"] == 2_111
    assert len(result["invalidatedClipIds"]) == 200
    assert len(result["preservedClipIds"]) == 200
    assert result["invalidatedClipIds"] == sorted(result["invalidatedClipIds"])
    assert result["preservedClipIds"] == sorted(result["preservedClipIds"])
    assert result["invalidatedClipIdsTruncated"] is True
    assert result["preservedClipIdsTruncated"] is True
    assert cast(dict[str, Any], replay) == result
