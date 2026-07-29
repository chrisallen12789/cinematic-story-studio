from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service import jobs as jobs_module
from cinematic_story_service.analysis import MAX_DERIVED_ENTITIES
from cinematic_story_service.models import HumanCorrectionRow

from .conftest import (
    TOKEN,
    create_analysis_job,
    create_imported_project,
    wait_for_job,
)


def test_canonical_markdown_fixture_has_exact_chapter_and_scene_hierarchy(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    fixture_path = Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    imported = create_imported_project(
        client,
        auth_headers,
        story_bytes=fixture_path.read_bytes(),
        create_key="canonical-hierarchy-project",
        import_key="canonical-hierarchy-import",
    )
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="canonical-hierarchy-analysis",
    )
    wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}",
        headers=auth_headers,
    ).json()

    assert [chapter["title"] for chapter in detail["chapters"]] == [
        "Chapter One: The Quiet Relay",
        "Chapter Two: A Borrowed Dawn",
    ]
    assert [scene["heading"] for scene in detail["scenes"]] == [
        "Scene One: Platform Glass",
        "Scene Two: The Unmarked Door",
        "Scene Three: The Clock Room",
    ]
    narration_summaries = [
        beat["summary"] for beat in detail["beats"] if beat["kind"] == "narration"
    ]
    assert all(summary is not None for summary in narration_summaries)
    assert all(not summary.lstrip().startswith("#") for summary in narration_summaries)
    assert all(summary.strip() != "---" for summary in narration_summaries)
    assert [chapter["sceneIds"] for chapter in detail["chapters"]] == [
        [detail["scenes"][0]["sceneId"], detail["scenes"][1]["sceneId"]],
        [detail["scenes"][2]["sceneId"]],
    ]


def test_analysis_entity_limit_fails_redacted_without_checkpoint_or_partial_publication(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="bounded-analysis-project",
        import_key="bounded-analysis-import",
    )
    original_analyze_story = jobs_module.analyze_story

    def over_limit_analysis(**kwargs: Any) -> dict[str, Any]:
        result = original_analyze_story(**kwargs)
        result["beats"] = [result["beats"][0]] * (MAX_DERIVED_ENTITIES + 1)
        return result

    monkeypatch.setattr(jobs_module, "analyze_story", over_limit_analysis)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="bounded-analysis-job",
    )
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"failed"})
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}",
        headers=auth_headers,
    ).json()

    assert terminal["checkpointAvailable"] is False
    assert terminal["error"] == {
        "code": "ANALYSIS_ENTITY_LIMIT_EXCEEDED",
        "message": "Story analysis could not be completed safely.",
        "retryable": False,
    }
    assert all(
        detail[collection] == []
        for collection in (
            "chapters",
            "scenes",
            "beats",
            "characters",
            "dialogueLines",
            "dialogueAttributions",
        )
    )


def test_analysis_produces_ordered_source_grounded_content_and_uncertainty(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="analysis-one",
    )
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}",
        headers=auth_headers,
    ).json()

    assert terminal["progress"] == 1
    assert detail["chapters"]
    assert detail["scenes"]
    assert any(beat["kind"] == "narration" for beat in detail["beats"])
    assert len(detail["dialogueLines"]) == 3
    assert [line["ordinal"] for line in detail["dialogueLines"]] == [0, 1, 2]
    assert {"Mara", "Ivo"} <= {character["displayName"] for character in detail["characters"]}
    assert len(detail["castingPlaceholders"]) == len(detail["characters"])
    assert all(
        placeholder
        == {
            "characterId": placeholder["characterId"],
            "status": "unassigned",
            "providerId": None,
            "voiceId": None,
        }
        for placeholder in detail["castingPlaceholders"]
    )
    assert any(scene["characterIds"] for scene in detail["scenes"])
    for entity in [
        *detail["chapters"],
        *detail["scenes"],
        *detail["beats"],
        *detail["characters"],
        *detail["dialogueLines"],
        *detail["dialogueAttributions"],
    ]:
        assert entity["revision"] >= 1
        assert entity["provenance"]["origin"] == "runtime_agent"
    for attribution in detail["dialogueAttributions"]:
        assert 0 <= attribution["confidence"]["score"] <= 1
        assert attribution["confidence"]["basis"]
        assert "1.0.0" in attribution["provenance"]["actorId"]
        assert isinstance(attribution["warnings"], list)
    unresolved = [
        attribution
        for attribution in detail["dialogueAttributions"]
        if attribution["effectiveSpeakerId"] is None
    ]
    assert unresolved
    assert unresolved[0]["warnings"][0]["code"] == "DIALOGUE_SPEAKER_UNCERTAIN"
    assert unresolved[0]["warnings"][0]["requiresHumanReview"] is True


def test_human_speaker_correction_is_append_only_conflict_safe_and_protected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    imported = create_imported_project(client, auth_headers)
    first_job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="analysis-for-correction",
    )
    wait_for_job(client, auth_headers, first_job["jobId"], {"succeeded"})
    project_url = f"/api/v1/projects/{imported['project']['projectId']}"
    before = client.get(project_url, headers=auth_headers).json()
    line = before["dialogueLines"][0]
    old_project_revision = before["project"]["revision"]
    current_speaker = before["dialogueAttributions"][0]["effectiveSpeakerId"]
    other_character = next(
        character
        for character in before["characters"]
        if character["characterId"] != current_speaker
    )
    correction_url = f"{project_url}/dialogue-lines/{line['lineId']}/speaker"
    corrected = client.put(
        correction_url,
        headers=auth_headers,
        json={
            "characterId": other_character["characterId"],
            "reason": "fixture correction",
            "expectedRevision": line["revision"],
        },
    )
    assert corrected.status_code == 200
    corrected_body = corrected.json()
    assert corrected_body["attribution"]["effectiveSpeakerId"] == other_character["characterId"]
    assert corrected_body["attribution"]["effectiveAuthority"] == "human"
    assert corrected_body["lineRevision"] == line["revision"] + 1
    assert corrected_body["projectRevision"] == old_project_revision + 1
    provenance = corrected_body["appendedCorrection"]
    assert provenance["reason"] == "fixture correction"
    assert provenance["previousCharacterId"] == current_speaker
    assert provenance["correctedCharacterId"] == other_character["characterId"]
    assert provenance["immutable"] is True
    assert provenance["lockedAgainstAutomation"] is True

    stale = client.put(
        correction_url,
        headers=auth_headers,
        json={
            "characterId": current_speaker,
            "reason": "stale change",
            "expectedRevision": line["revision"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    assert stale.json()["error"]["details"]["currentRevision"] == line["revision"] + 1

    rerun = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="analysis-after-human-correction",
    )
    wait_for_job(client, auth_headers, rerun["jobId"], {"succeeded"})
    after = client.get(project_url, headers=auth_headers).json()
    attribution = next(
        value for value in after["dialogueAttributions"] if value["lineId"] == line["lineId"]
    )
    assert attribution["effectiveSpeakerId"] == other_character["characterId"]
    assert attribution["effectiveAuthority"] == "human"
    assert len(after["humanCorrections"]) == 1
    assert after["humanCorrections"][0]["reason"] == "fixture correction"


def test_sqlite_restart_preserves_import_analysis_and_correction(tmp_path: Path) -> None:
    data_dir = tmp_path / "restart-data"
    settings = ServiceSettings(data_dir=data_dir, bearer_token=TOKEN)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="restart-analysis",
        )
        wait_for_job(first, headers, job["jobId"], {"succeeded"})
        detail = first.get(
            f"/api/v1/projects/{imported['project']['projectId']}", headers=headers
        ).json()
        line = detail["dialogueLines"][0]
        current = detail["dialogueAttributions"][0]["effectiveSpeakerId"]
        other = next(
            character["characterId"]
            for character in detail["characters"]
            if character["characterId"] != current
        )
        corrected = first.put(
            (
                f"/api/v1/projects/{imported['project']['projectId']}"
                f"/dialogue-lines/{line['lineId']}/speaker"
            ),
            headers=headers,
            json={
                "characterId": other,
                "reason": "restart provenance",
                "expectedRevision": line["revision"],
            },
        ).json()
        expected_revision = corrected["projectRevision"]
        expected_hash = detail["sourceDocuments"][0]["contentSha256"]

    second_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(second_app) as second:
        restored = second.get(
            f"/api/v1/projects/{imported['project']['projectId']}", headers=headers
        ).json()
        assert restored["project"]["revision"] == expected_revision
        assert restored["sourceDocuments"][0]["contentSha256"] == expected_hash
        assert restored["story"]["text"] == imported["story"]["text"]
        assert restored["chapters"]
        assert restored["scenes"]
        assert restored["dialogueAttributions"][0]["effectiveSpeakerId"] == other
        assert restored["dialogueAttributions"][0]["effectiveAuthority"] == "human"
        assert restored["humanCorrections"][0]["reason"] == "restart provenance"


def test_concurrent_same_revision_corrections_have_one_cas_winner(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="analysis-for-concurrent-correction",
    )
    wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    project_url = f"/api/v1/projects/{imported['project']['projectId']}"
    before = client.get(project_url, headers=auth_headers).json()
    line = before["dialogueLines"][0]
    other_character = next(
        character["characterId"]
        for character in before["characters"]
        if character["characterId"] != before["dialogueAttributions"][0]["effectiveSpeakerId"]
    )
    correction_url = f"{project_url}/dialogue-lines/{line['lineId']}/speaker"
    start = threading.Barrier(3)

    def submit(character_id: str | None, reason: str) -> tuple[int, dict[str, object]]:
        start.wait()
        response = client.put(
            correction_url,
            headers=auth_headers,
            json={
                "characterId": character_id,
                "reason": reason,
                "expectedRevision": line["revision"],
            },
        )
        return response.status_code, response.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, other_character, "concurrent one")
        second = pool.submit(submit, None, "concurrent two")
        start.wait()
        outcomes = [first.result(timeout=5), second.result(timeout=5)]

    assert sorted(status for status, _body in outcomes) == [200, 409]
    loser = next(body for status, body in outcomes if status == 409)
    loser_error = loser["error"]
    assert isinstance(loser_error, dict)
    assert loser_error["code"] == "REVISION_CONFLICT"
    after = client.get(project_url, headers=auth_headers).json()
    assert after["dialogueLines"][0]["revision"] == line["revision"] + 1
    assert after["project"]["revision"] == before["project"]["revision"] + 1
    assert len(after["humanCorrections"]) == 1
    with app.state.database.session() as session:
        assert session.query(HumanCorrectionRow).filter_by(line_id=line["lineId"]).count() == 1
