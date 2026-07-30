from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert

from cinematic_story_service.models import AnalysisCorrectionRow, AnalysisRunRow
from cinematic_story_service.util import (
    request_fingerprint,
    sha256_text,
    stable_id,
    utc_now,
)
from cinematic_story_service.whole_book_analysis import analyze_whole_book
from tests.test_phase2_api import create_phase2_run, queue_phase2_run

from .conftest import wait_for_job


def _collection(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    collection: str,
    speaker_state: str | None = None,
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/{collection}",
        headers=auth_headers,
        params={
            "limit": 200,
            **({"speakerState": speaker_state} if speaker_state is not None else {}),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _run_detail(
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


def _wait_until_after(timestamp: str) -> None:
    deadline = time.monotonic() + 1
    while utc_now() <= timestamp:
        assert time.monotonic() < deadline
        time.sleep(0.001)


def test_prior_dialogue_correction_is_frozen_at_run_creation_time(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story = b"""# Chapter One

## Scene One

Mara: "Hold the line."
"""
    imported, first_created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-dialogue-temporal-freeze-one",
        story_bytes=story,
    )
    project_id = imported["project"]["projectId"]
    first_run_id = first_created["run"]["runId"]
    first_run = _run_detail(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
    )
    first_dialogue = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="dialogue-lines",
    )[0]

    second_created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-dialogue-temporal-freeze-two",
    )
    wait_for_job(
        client,
        auth_headers,
        second_created["job"]["jobId"],
        {"succeeded"},
        timeout=20,
    )
    second_run_id = second_created["run"]["runId"]
    second_run = _run_detail(
        client,
        auth_headers,
        project_id=project_id,
        run_id=second_run_id,
    )

    _wait_until_after(second_run["createdAt"])
    corrected = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "dialogue_speaker",
            "targetCollection": "dialogue-lines",
            "targetEntityId": first_dialogue["entityId"],
            "expectedTargetRevision": first_dialogue["effectiveRevision"],
            "expectedRunFingerprint": first_run["runFingerprint"],
            "previousValueFingerprint": first_dialogue["effectiveValueFingerprint"],
            "patch": {
                "speakerCharacterId": None,
                "selectedCandidateId": None,
                "requiresHumanReview": True,
            },
            "reason": "Exercise the compatible-run temporal correction boundary.",
            "idempotencyKey": "phase2-dialogue-temporal-freeze-correction",
        },
    )
    assert corrected.status_code == 200, corrected.text
    correction = corrected.json()["correction"]
    assert correction["recordedAt"] > second_run["createdAt"]

    second_dialogue = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=second_run_id,
            collection="dialogue-lines",
        )
        if value["stableSemanticId"] == first_dialogue["stableSemanticId"]
    )
    assert second_dialogue["effectiveRevision"] == 1
    assert second_dialogue["effectiveAuthority"] != "human"
    assert second_dialogue["speakerState"] != "corrected"
    assert all(
        value["stableSemanticId"] != first_dialogue["stableSemanticId"]
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=second_run_id,
            collection="dialogue-lines",
            speaker_state="corrected",
        )
    )

    _wait_until_after(correction["recordedAt"])
    third_created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-dialogue-temporal-freeze-three",
    )
    wait_for_job(
        client,
        auth_headers,
        third_created["job"]["jobId"],
        {"succeeded"},
        timeout=20,
    )
    third_run_id = third_created["run"]["runId"]
    third_run = _run_detail(
        client,
        auth_headers,
        project_id=project_id,
        run_id=third_run_id,
    )
    assert third_run["createdAt"] > correction["recordedAt"]

    third_dialogue = next(
        value
        for value in _collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=third_run_id,
            collection="dialogue-lines",
        )
        if value["stableSemanticId"] == first_dialogue["stableSemanticId"]
    )
    assert third_dialogue["effectiveRevision"] == 2
    assert third_dialogue["effectiveAuthority"] == "human"
    assert third_dialogue["speakerState"] == "corrected"
    assert third_dialogue["effectiveAttribution"]["correctionId"] == correction["correctionId"]


def test_long_ascii_and_curly_dialogue_quotes_preserve_exact_offsets() -> None:
    quote_cases = [
        ('"', '"', "a" * 1_000),
        ('"', '"', "b" * 1_001),
        ("\u201c", "\u201d", "c" * 1_000),
        ("\u201c", "\u201d", "d" * 1_001),
    ]
    expected_quotes = [
        f"{opening}{content}{closing}"
        for opening, closing, content in quote_cases
    ]
    text = (
        "# Chapter One\n\n## Scene One\n\n"
        + "\n".join(f"Mara said, {quote}" for quote in expected_quotes)
        + "\n"
    )

    result = analyze_whole_book(
        text=text,
        input_fingerprint=sha256_text(text),
        registry_scope="phase2-long-quote-project",
        story_scope="phase2-long-quote-story",
    )
    dialogue_lines = result["collections"]["dialogue-lines"]
    assert len(dialogue_lines) == len(expected_quotes)

    for dialogue_line, expected_quote in zip(
        dialogue_lines,
        expected_quotes,
        strict=True,
    ):
        payload = dialogue_line["payload"]
        start = payload["quoteStartOffset"]
        end = payload["quoteEndOffset"]
        assert dialogue_line["startOffset"] == start
        assert dialogue_line["endOffset"] == end
        assert end - start == len(expected_quote)
        assert text[start:end] == expected_quote
        assert dialogue_line["evidence"][0]["startOffset"] == start
        assert dialogue_line["evidence"][0]["endOffset"] == end


def test_projection_cap_ignores_more_than_4096_incompatible_corrections(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported, first_created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-incompatible-projection-cap-one",
    )
    project_id = imported["project"]["projectId"]
    first_run_id = first_created["run"]["runId"]

    second_created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-incompatible-projection-cap-two",
    )
    wait_for_job(
        client,
        auth_headers,
        second_created["job"]["jobId"],
        {"succeeded"},
        timeout=20,
    )
    second_run_id = second_created["run"]["runId"]

    with app.state.database.session() as session:
        first_run = session.get(AnalysisRunRow, first_run_id)
        incompatible_run = session.get(AnalysisRunRow, second_run_id)
        assert first_run is not None
        assert incompatible_run is not None
        incompatible_run.profile_fingerprint = request_fingerprint(
            {"profile": "deliberately-incompatible"}
        )
        rows = [
            {
                "id": stable_id(second_run_id, "incompatible-correction", index),
                "project_id": project_id,
                "run_id": second_run_id,
                "category": "structure_boundary",
                "target_entity_id": None,
                "target_key": f"scenes:incompatible-{index}",
                "revision": 1,
                "expected_target_revision": 1,
                "expected_run_fingerprint": incompatible_run.run_fingerprint,
                "previous_value_fingerprint": "0" * 64,
                "patch_json": "{}",
                "correction_fingerprint": request_fingerprint(
                    {"incompatibleCorrection": index}
                ),
                "reason": "Direct incompatible projection-cap fixture.",
                "actor_id": "regression-test",
                "supersedes_correction_id": None,
                "legacy_correction_id": None,
                "idempotency_key": None,
                "recorded_at": first_run.created_at,
            }
            for index in range(4_097)
        ]
        session.execute(insert(AnalysisCorrectionRow), rows)
        session.commit()

    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}/entities/scenes",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] > 0
    assert len(body["items"]) == body["total"]
