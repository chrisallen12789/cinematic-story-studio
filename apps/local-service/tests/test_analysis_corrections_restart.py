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
from cinematic_story_service.models import (
    AnalysisAgentExecutionRow,
    AnalysisCorrectionRow,
    AnalysisEntityRow,
    AnalysisExecutionRow,
    HumanCorrectionRow,
    ImportReviewRow,
    JobAttemptRow,
    ParserExecutionRow,
)
from cinematic_story_service.util import request_fingerprint
from tests.test_phase2_api import create_phase2_run, queue_phase2_run

from .conftest import (
    SYNTHETIC_BYTES,
    TOKEN,
    create_analysis_job,
    create_imported_project,
    decide_import_review,
    review_for_extraction,
    submit_import,
    synthetic_fixture,
    wait_for_job,
)


def _phase2_collection(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    collection: str,
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/{collection}",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _assert_effective_structure_integrity(
    *,
    chapters: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    beats: list[dict[str, Any]],
) -> None:
    chapters_by_id = {value["entityId"]: value for value in chapters}
    scenes_by_id = {value["entityId"]: value for value in scenes}
    assert len(chapters_by_id) == len(chapters)
    assert len(scenes_by_id) == len(scenes)
    assert all(scene["chapterId"] in chapters_by_id for scene in scenes)
    assert all(beat["sceneId"] in scenes_by_id for beat in beats)
    assert all(beat["chapterId"] == scenes_by_id[beat["sceneId"]]["chapterId"] for beat in beats)

    scenes_by_chapter: dict[str, list[dict[str, Any]]] = {
        chapter_id: [] for chapter_id in chapters_by_id
    }
    for scene in scenes:
        scenes_by_chapter[scene["chapterId"]].append(scene)
    for chapter_id, chapter in chapters_by_id.items():
        children = sorted(
            scenes_by_chapter[chapter_id],
            key=lambda value: (value["ordinal"], value["entityId"]),
        )
        assert chapter["sceneCount"] == len(children)
        assert chapter["firstSceneId"] == (children[0]["entityId"] if children else None)
        assert chapter["lastSceneId"] == (children[-1]["entityId"] if children else None)

    beats_by_scene: dict[str, list[dict[str, Any]]] = {scene_id: [] for scene_id in scenes_by_id}
    for beat in beats:
        beats_by_scene[beat["sceneId"]].append(beat)
    for scene_id, scene in scenes_by_id.items():
        children = sorted(
            beats_by_scene[scene_id],
            key=lambda value: (value["ordinal"], value["entityId"]),
        )
        assert scene["beatCount"] == len(children)
        assert scene["firstBeatId"] == (children[0]["entityId"] if children else None)
        assert scene["lastBeatId"] == (children[-1]["entityId"] if children else None)


def _append_phase2_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run: dict[str, Any],
    category: str,
    collection: str,
    target: dict[str, Any],
    patch: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run['runId']}/corrections",
        headers=auth_headers,
        json={
            "category": category,
            "targetCollection": collection,
            "targetEntityId": target["entityId"],
            "expectedTargetRevision": target["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": target["effectiveValueFingerprint"],
            "patch": patch,
            "reason": f"Exercise {category} effective-view semantics.",
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["correction"]


def _phase2_reviews(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
) -> dict[str, dict[str, Any]]:
    response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return {value["gateId"]: value for value in response.json()["items"]}


def _approve_phase2_gate(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    gate_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    review = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )[gate_id]
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews/{gate_id}/decisions",
        headers=auth_headers,
        json={
            "decision": "approve",
            "expectedRevision": review["revision"],
            "expectedArtifactFingerprint": review["artifactFingerprint"],
            "expectedEvidenceFingerprint": review["evidenceFingerprint"],
            "acknowledgedWarningIds": review["openWarningIds"],
            "rationale": "Approve the governed evidence.",
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _decide_phase2_gate(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run_id: str,
    gate_id: str,
    decision: str,
    idempotency_key: str,
    rationale: str = "Approve the governed evidence.",
) -> dict[str, Any]:
    review = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )[gate_id]
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews/{gate_id}/decisions",
        headers=auth_headers,
        json={
            "decision": decision,
            "expectedRevision": review["revision"],
            "expectedArtifactFingerprint": review["artifactFingerprint"],
            "expectedEvidenceFingerprint": review["evidenceFingerprint"],
            "acknowledgedWarningIds": (review["openWarningIds"] if decision == "approve" else []),
            "rationale": rationale,
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


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
        "Chapter Two: Borrowed Hours",
        "Chapter Three: The Last Signal",
    ]
    assert [scene["heading"] for scene in detail["scenes"]] == [
        "Scene One: Platform Glass",
        "Scene Two: The Clock Room",
        "Scene Three: Six Years Earlier, Archive Vault",
        "Scene Four: North Signal Tower",
        "Scene Five: Flooded Concourse",
        "Scene Six: Dawn Switchyard",
    ]
    narration_summaries = [
        beat["summary"] for beat in detail["beats"] if beat["kind"] == "narration"
    ]
    assert all(summary is not None for summary in narration_summaries)
    assert all(not summary.lstrip().startswith("#") for summary in narration_summaries)
    assert all(summary.strip() != "---" for summary in narration_summaries)
    assert [chapter["sceneIds"] for chapter in detail["chapters"]] == [
        [detail["scenes"][0]["sceneId"], detail["scenes"][1]["sceneId"]],
        [detail["scenes"][2]["sceneId"], detail["scenes"][3]["sceneId"]],
        [detail["scenes"][4]["sceneId"], detail["scenes"][5]["sceneId"]],
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


def test_fresh_v3_phase0_speaker_alias_correction_is_effective_in_phase2_and_restart(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "phase0-phase2-correction-bridge"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    _fixture_name, fixture_bytes, _fixture_media_type = synthetic_fixture("markdown")
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        imported = create_imported_project(
            first,
            headers,
            story_bytes=fixture_bytes,
            create_key="phase0-phase2-bridge-project",
            import_key="phase0-phase2-bridge-import",
        )
        project_id = imported["project"]["projectId"]
        phase1_job = create_analysis_job(
            first,
            headers,
            project_id,
            imported["story"]["revision"],
            idempotency_key="phase0-phase2-bridge-phase1",
        )
        wait_for_job(first, headers, phase1_job["jobId"], {"succeeded"})
        before = first.get(
            f"/api/v1/projects/{project_id}",
            headers=headers,
        ).json()
        line = before["dialogueLines"][0]
        selected_character = next(
            value for value in before["characters"] if value["displayName"] == "Mira"
        )
        correction_response = first.put(
            f"/api/v1/projects/{project_id}/dialogue-lines/{line['lineId']}/speaker",
            headers=headers,
            json={
                "characterId": selected_character["characterId"],
                "reason": "Phase 0 choice carried into Phase 2.",
                "expectedRevision": line["revision"],
            },
        )
        assert correction_response.status_code == 200, correction_response.text
        legacy_correction_id = correction_response.json()["appendedCorrection"]["correctionId"]
        with first_app.state.database.session() as session:
            generalized = session.get(
                AnalysisCorrectionRow,
                legacy_correction_id,
            )
            assert generalized is not None
            assert generalized.run_id is None
            assert generalized.legacy_correction_id == legacy_correction_id

        phase2 = queue_phase2_run(
            first,
            headers,
            imported=imported,
            idempotency_key="phase0-phase2-bridge-run",
        )
        wait_for_job(
            first,
            headers,
            phase2["job"]["jobId"],
            {"succeeded"},
            timeout=20,
        )
        run_id = phase2["run"]["runId"]
        phase2_characters = _phase2_collection(
            first,
            headers,
            project_id=project_id,
            run_id=run_id,
            collection="characters",
        )
        phase2_character = next(
            value
            for value in phase2_characters
            if any(
                alias["normalizedAlias"] == selected_character["displayName"].casefold()
                for alias in value["aliases"]
            )
        )
        assert phase2_character["normalizedCanonicalName"] != selected_character[
            "displayName"
        ].casefold()
        dialogue = _phase2_collection(
            first,
            headers,
            project_id=project_id,
            run_id=run_id,
            collection="dialogue-lines",
        )
        corrected_dialogue = next(
            value
            for value in dialogue
            if value["provenance"].get("correctionId") == legacy_correction_id
        )
        assert (
            corrected_dialogue["effectiveAttribution"]["speakerCharacterId"]
            == phase2_character["entityId"]
        )
        assert corrected_dialogue["effectiveAttribution"]["authority"] == ("human_correction")
        corrections = first.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
            headers=headers,
        ).json()["items"]
        projected = next(
            value for value in corrections if value["correctionId"] == legacy_correction_id
        )
        assert projected["runId"] == run_id
        assert projected["targetEntityId"] == corrected_dialogue["entityId"]
        assert projected["patch"] == {
            "speakerCharacterId": phase2_character["entityId"],
            "selectedCandidateId": None,
            "requiresHumanReview": False,
        }
        assert projected["reason"] == ("Phase 0 choice carried into Phase 2.")

    restarted = create_app(
        ServiceSettings(
            data_dir=data_dir,
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    with TestClient(restarted) as second:
        restored_dialogue = _phase2_collection(
            second,
            headers,
            project_id=project_id,
            run_id=run_id,
            collection="dialogue-lines",
        )
        restored = next(
            value
            for value in restored_dialogue
            if value["provenance"].get("correctionId") == legacy_correction_id
        )
        assert (
            restored["effectiveAttribution"]["speakerCharacterId"] == (phase2_character["entityId"])
        )
        restored_corrections = second.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
            headers=headers,
        ).json()["items"]
        assert any(value["correctionId"] == legacy_correction_id for value in restored_corrections)


def test_phase0_speaker_correction_after_phase2_publication_overlays_and_invalidates_gate(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="phase0-after-phase2-project",
        import_key="phase0-after-phase2-import",
    )
    project_id = imported["project"]["projectId"]
    phase1_job = create_analysis_job(
        client,
        auth_headers,
        project_id,
        imported["story"]["revision"],
        idempotency_key="phase0-after-phase2-phase1",
    )
    wait_for_job(client, auth_headers, phase1_job["jobId"], {"succeeded"})
    phase2 = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase0-after-phase2-run",
    )
    wait_for_job(
        client,
        auth_headers,
        phase2["job"]["jobId"],
        {"succeeded"},
        timeout=20,
    )
    run_id = phase2["run"]["runId"]
    approved = _approve_phase2_gate(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        gate_id="dialogue_attribution_review",
        idempotency_key="phase0-after-phase2-dialogue-approval",
    )["review"]
    assert approved["state"] == "approved"

    phase0_detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    line = phase0_detail["dialogueLines"][0]
    current_speaker = phase0_detail["dialogueAttributions"][0]["effectiveSpeakerId"]
    selected_character = next(
        character
        for character in phase0_detail["characters"]
        if character["characterId"] != current_speaker
    )
    correction_response = client.put(
        f"/api/v1/projects/{project_id}/dialogue-lines/{line['lineId']}/speaker",
        headers=auth_headers,
        json={
            "characterId": selected_character["characterId"],
            "reason": "Correct after the Phase 2 snapshot was published.",
            "expectedRevision": line["revision"],
        },
    )
    assert correction_response.status_code == 200, correction_response.text
    correction_id = correction_response.json()["appendedCorrection"]["correctionId"]

    phase2_characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    expected_phase2_character = next(
        character
        for character in phase2_characters
        if character["normalizedCanonicalName"] == selected_character["displayName"].casefold()
    )
    corrected_dialogue = next(
        value
        for value in _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="dialogue-lines",
        )
        if value["provenance"].get("correctionId") == correction_id
    )
    assert corrected_dialogue["speakerState"] == "corrected"
    assert (
        corrected_dialogue["effectiveAttribution"]["speakerCharacterId"]
        == expected_phase2_character["entityId"]
    )
    corrected_filter = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/dialogue-lines",
        headers=auth_headers,
        params={"speakerState": "corrected", "limit": 200},
    )
    assert corrected_filter.status_code == 200, corrected_filter.text
    assert corrected_dialogue["entityId"] in {
        value["entityId"] for value in corrected_filter.json()["items"]
    }
    dialogue_review = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )["dialogue_attribution_review"]
    assert dialogue_review["state"] == "invalidated"
    assert dialogue_review["revision"] == approved["revision"] + 1
    reapproved = _approve_phase2_gate(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        gate_id="dialogue_attribution_review",
        idempotency_key="phase0-after-phase2-dialogue-reapproval",
    )["review"]
    assert reapproved["state"] == "approved"
    assert reapproved["revision"] == dialogue_review["revision"] + 1


def test_restart_during_extraction_resumes_new_attempt_without_partial_review(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "interrupted-extraction-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        project = first.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Interrupted Extraction"},
        ).json()["project"]
        first_app.state.worker.controls.execution_gate.clear()
        queued = submit_import(
            first,
            headers,
            project_id=project["projectId"],
            filename="interrupted.md",
            content=SYNTHETIC_BYTES,
            media_type="text/markdown",
            declared_format="markdown",
            idempotency_key="interrupted-extraction",
        )
        running = wait_for_job(
            first,
            headers,
            queued["job"]["jobId"],
            {"running"},
        )
        assert running["checkpointAvailable"] is False
        before_restart = first.get(
            f"/api/v1/projects/{project['projectId']}",
            headers=headers,
        ).json()
        assert before_restart["importReviews"] == []
        assert before_restart["story"] is None

    second_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(second_app) as second:
        interrupted = second.get(
            f"/api/v1/jobs/{queued['job']['jobId']}",
            headers=headers,
        ).json()["job"]
        assert interrupted["state"] == "interrupted"
        resumed = second.post(
            f"/api/v1/jobs/{queued['job']['jobId']}/resume",
            headers=headers,
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["job"]["attempt"] == 2
        succeeded = wait_for_job(
            second,
            headers,
            queued["job"]["jobId"],
            {"succeeded"},
        )
        assert succeeded["attempt"] == 2
        review = review_for_extraction(
            second,
            headers,
            project_id=project["projectId"],
            extraction_id=queued["extraction"]["extractionId"],
        )
        assert review["state"] == "pending"
        detail = second.get(
            f"/api/v1/projects/{project['projectId']}",
            headers=headers,
        ).json()
        assert detail["story"] is None
        assert detail["analysisAllowed"] is False
        with second_app.state.database.session() as session:
            attempts = (
                session.query(JobAttemptRow)
                .filter_by(job_id=queued["job"]["jobId"])
                .order_by(JobAttemptRow.number)
                .all()
            )
            executions = (
                session.query(ParserExecutionRow)
                .filter_by(job_id=queued["job"]["jobId"])
                .order_by(ParserExecutionRow.attempt)
                .all()
            )
            assert [(row.number, row.outcome) for row in attempts] == [
                (1, "interrupted"),
                (2, "succeeded"),
            ]
            assert [(row.attempt, row.outcome) for row in executions] == [
                (1, "interrupted"),
                (2, "succeeded"),
            ]


def test_extraction_retry_and_cancelled_attempt_survive_restart(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "retry-cancel-extraction-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        project = first.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Retry and Cancel"},
        ).json()["project"]
        first_app.state.worker.fail_next_attempt()
        retry_import = submit_import(
            first,
            headers,
            project_id=project["projectId"],
            filename="retry.md",
            content=SYNTHETIC_BYTES,
            media_type="text/markdown",
            declared_format="markdown",
            idempotency_key="retry-extraction-import",
        )
        failed = wait_for_job(
            first,
            headers,
            retry_import["job"]["jobId"],
            {"failed"},
        )
        assert failed["error"] == {
            "code": "EXTRACTION_FAILED",
            "message": "Document extraction could not be completed safely.",
            "retryable": True,
        }
        retried = first.post(
            f"/api/v1/jobs/{retry_import['job']['jobId']}/retry",
            headers=headers,
        )
        assert retried.status_code == 200
        assert retried.json()["job"]["attempt"] == 2
        wait_for_job(
            first,
            headers,
            retry_import["job"]["jobId"],
            {"succeeded"},
        )

        first_app.state.worker.controls.execution_gate.clear()
        cancelled_import = submit_import(
            first,
            headers,
            project_id=project["projectId"],
            filename="cancelled.md",
            content=SYNTHETIC_BYTES + b"\r\nCancelled candidate.\r\n",
            media_type="text/markdown",
            declared_format="markdown",
            idempotency_key="cancelled-extraction-import",
        )
        wait_for_job(
            first,
            headers,
            cancelled_import["job"]["jobId"],
            {"running"},
        )
        cancellation = first.post(
            f"/api/v1/jobs/{cancelled_import['job']['jobId']}/cancel",
            headers=headers,
        )
        assert cancellation.status_code == 200
        first_app.state.worker.controls.execution_gate.set()
        wait_for_job(
            first,
            headers,
            cancelled_import["job"]["jobId"],
            {"cancelled"},
        )

    second_app = create_app(
        ServiceSettings(
            data_dir=data_dir,
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    with TestClient(second_app) as second:
        assert (
            second.get(
                f"/api/v1/jobs/{retry_import['job']['jobId']}",
                headers=headers,
            ).json()["job"]["state"]
            == "succeeded"
        )
        cancelled = second.get(
            f"/api/v1/jobs/{cancelled_import['job']['jobId']}",
            headers=headers,
        ).json()["job"]
        assert cancelled["state"] == "cancelled"
        detail = second.get(
            f"/api/v1/projects/{project['projectId']}",
            headers=headers,
        ).json()
        assert len(detail["sourceDocuments"]) == 2
        assert len(detail["importReviews"]) == 1
        assert (
            detail["importReviews"][0]["extractionId"]
            == (retry_import["extraction"]["extractionId"])
        )
        with second_app.state.database.session() as session:
            retry_executions = (
                session.query(ParserExecutionRow)
                .filter_by(job_id=retry_import["job"]["jobId"])
                .order_by(ParserExecutionRow.attempt)
                .all()
            )
            cancelled_execution = (
                session.query(ParserExecutionRow)
                .filter_by(job_id=cancelled_import["job"]["jobId"])
                .one()
            )
            assert [(row.attempt, row.outcome) for row in retry_executions] == [
                (1, "failed"),
                (2, "succeeded"),
            ]
            assert cancelled_execution.outcome == "cancelled"


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


def test_phase2_cancel_persists_agent_lifecycle_without_snapshot_across_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "phase2-cancel-restart"
    settings = ServiceSettings(data_dir=data_dir, bearer_token=TOKEN)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    entered_agent = threading.Event()
    release_agent = threading.Event()
    original = jobs_module.analyze_whole_book

    def blocked_analysis(**kwargs: Any) -> dict[str, Any]:
        entered_agent.set()
        assert release_agent.wait(timeout=10)
        return original(**kwargs)

    monkeypatch.setattr(jobs_module, "analyze_whole_book", blocked_analysis)
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        imported = create_imported_project(
            first,
            headers,
            create_key="phase2-cancel-project",
            import_key="phase2-cancel-import",
        )
        created = queue_phase2_run(
            first,
            headers,
            imported=imported,
            idempotency_key="phase2-cancel-run",
        )
        assert entered_agent.wait(timeout=10)
        run_id = created["run"]["runId"]
        job_id = created["job"]["jobId"]
        running = first.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
            headers=headers,
        ).json()["run"]
        assert running["currentSnapshot"] is None
        assert running["latestExecution"]["status"] == "running"

        cancellation = first.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
        assert cancellation.status_code == 200, cancellation.text
        release_agent.set()
        terminal = wait_for_job(first, headers, job_id, {"cancelled"}, timeout=20)
        assert terminal["state"] == "cancelled"
        cancelled_run = first.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
            headers=headers,
        ).json()["run"]
        assert cancelled_run["snapshotCount"] == 0
        assert cancelled_run["currentSnapshot"] is None
        assert cancelled_run["latestExecution"]["status"] == "cancelled"
        assert cancelled_run["latestExecution"]["failure"]["redacted"] is True

    restarted = create_app(
        ServiceSettings(
            data_dir=data_dir,
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    with TestClient(restarted) as second:
        restored = second.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
            headers=headers,
        ).json()["run"]
        assert restored["status"] == "cancelled"
        assert restored["currentSnapshot"] is None
        assert restored["latestExecution"]["status"] == "cancelled"


def test_phase2_failed_agent_is_inspectable_and_retry_publishes_one_snapshot(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="phase2-agent-failure-project",
        import_key="phase2-agent-failure-import",
    )
    original = jobs_module.analyze_whole_book

    def failed_agent(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("private failure detail must not escape")

    monkeypatch.setattr(jobs_module, "analyze_whole_book", failed_agent)
    created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-agent-failure-run",
    )
    run_id = created["run"]["runId"]
    job_id = created["job"]["jobId"]
    failed = wait_for_job(client, auth_headers, job_id, {"failed"}, timeout=20)
    assert failed["error"] == {
        "code": "ANALYSIS_FAILED",
        "message": "Whole-book analysis could not be completed safely.",
        "retryable": True,
    }
    failed_run = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    assert failed_run["snapshotCount"] == 0
    assert failed_run["latestExecution"]["status"] == "failed"
    assert failed_run["latestExecution"]["failure"]["message"] == (
        "Whole-book analysis could not be completed safely."
    )
    assert "private failure" not in str(failed_run)

    monkeypatch.setattr(jobs_module, "analyze_whole_book", original)
    retried = client.post(f"/api/v1/jobs/{job_id}/retry", headers=auth_headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["job"]["attempt"] == 2
    succeeded = wait_for_job(client, auth_headers, job_id, {"succeeded"}, timeout=20)
    assert succeeded["attempt"] == 2
    restored = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    assert restored["snapshotCount"] == 5
    assert restored["currentSnapshot"] is not None
    assert restored["latestExecution"]["status"] == "succeeded"

    with app.state.database.session() as session:
        executions = (
            session.query(AnalysisExecutionRow)
            .filter_by(run_id=run_id)
            .order_by(AnalysisExecutionRow.attempt)
            .all()
        )
        assert [(row.attempt, row.outcome) for row in executions] == [
            (1, "failed"),
            (2, "succeeded"),
        ]
        agent_rows = (
            session.query(AnalysisAgentExecutionRow)
            .filter_by(run_id=run_id)
            .order_by(
                AnalysisAgentExecutionRow.execution_id,
                AnalysisAgentExecutionRow.ordinal,
            )
            .all()
        )
        assert len(agent_rows) == 22
        assert any(row.outcome == "failed" for row in agent_rows)


@pytest.mark.parametrize(
    "tamper",
    (
        "entity_offset",
        "evidence_offset",
        "entity_id",
        "parent_reference",
        "missing_payload_field",
        "entity_fingerprint",
    ),
)
def test_phase2_publish_rejects_tampered_analysis_output_without_partial_rows(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    original = jobs_module.analyze_whole_book

    def tampered_analysis(**kwargs: Any) -> dict[str, Any]:
        result = original(**kwargs)
        scene = result["collections"]["scenes"][0]
        if tamper == "entity_offset":
            scene["endOffset"] = len(str(kwargs["text"])) + 1
        elif tamper == "evidence_offset":
            scene["evidence"][0]["endOffset"] = len(str(kwargs["text"])) + 1
        elif tamper == "entity_id":
            scene["entityId"] = "tampered-scene-id"
            scene["payload"]["sceneId"] = "tampered-scene-id"
        elif tamper == "parent_reference":
            scene["parentEntityId"] = "missing-chapter-id"
        elif tamper == "missing_payload_field":
            result["collections"]["chapters"][0]["payload"].pop("sceneCount")
        else:
            scene["fingerprint"] = "0" * 64
        return result

    monkeypatch.setattr(jobs_module, "analyze_whole_book", tampered_analysis)
    imported = create_imported_project(
        client,
        auth_headers,
        create_key=f"tampered-output-{tamper}-project",
        import_key=f"tampered-output-{tamper}-import",
    )
    created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key=f"tampered-output-{tamper}-run",
    )
    terminal = wait_for_job(
        client,
        auth_headers,
        created["job"]["jobId"],
        {"failed"},
        timeout=20,
    )
    assert terminal["error"]["code"] == "ANALYSIS_OUTPUT_INVALID"
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    assert run["snapshotCount"] == 0
    with app.state.database.session() as session:
        assert session.query(AnalysisEntityRow).filter_by(run_id=run_id).count() == 0


def test_phase2_publish_rejects_rehashed_malformed_dialogue_candidate(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = jobs_module.analyze_whole_book

    def malformed_analysis(**kwargs: Any) -> dict[str, Any]:
        kwargs.pop("result_checkpoint_observer", None)
        result = original(**kwargs)
        dialogue = result["collections"]["dialogue-lines"][0]
        dialogue["payload"]["candidates"][0].pop("rank")
        dialogue["fingerprint"] = request_fingerprint(
            {key: value for key, value in dialogue.items() if key != "fingerprint"}
        )
        collection_fingerprints = {
            collection: request_fingerprint(values)
            for collection, values in result["collections"].items()
        }
        result["collectionFingerprints"] = collection_fingerprints
        result["gateFingerprints"] = {
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
        result["outputFingerprint"] = request_fingerprint(
            {key: value for key, value in result.items() if key != "outputFingerprint"}
        )
        return result

    monkeypatch.setattr(jobs_module, "analyze_whole_book", malformed_analysis)
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="malformed-dialogue-candidate-project",
        import_key="malformed-dialogue-candidate-import",
    )
    created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="malformed-dialogue-candidate-run",
    )
    terminal = wait_for_job(
        client,
        auth_headers,
        created["job"]["jobId"],
        {"failed"},
        timeout=20,
    )
    assert terminal["error"]["code"] == "ANALYSIS_OUTPUT_INVALID"
    run_id = created["run"]["runId"]
    with app.state.database.session() as session:
        assert session.query(AnalysisEntityRow).filter_by(run_id=run_id).count() == 0


def test_phase2_publish_rechecks_current_approval_after_analysis_race(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = jobs_module.analyze_whole_book

    def invalidate_after_analysis(**kwargs: Any) -> dict[str, Any]:
        result = original(**kwargs)
        with app.state.database.session() as session:
            review = (
                session.query(ImportReviewRow)
                .filter_by(state="approved")
                .order_by(ImportReviewRow.created_at.desc())
                .first()
            )
            assert review is not None
            review.state = "invalidated"
        return result

    monkeypatch.setattr(
        jobs_module,
        "analyze_whole_book",
        invalidate_after_analysis,
    )
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="phase2-publication-approval-race-project",
        import_key="phase2-publication-approval-race-import",
    )
    created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-publication-approval-race-run",
    )
    terminal = wait_for_job(
        client,
        auth_headers,
        created["job"]["jobId"],
        {"failed"},
        timeout=20,
    )
    assert terminal["error"]["code"] == "ANALYSIS_RUN_APPROVAL_STALE"
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    assert run["snapshotCount"] == 0
    with app.state.database.session() as session:
        assert session.query(AnalysisEntityRow).filter_by(run_id=run_id).count() == 0


def test_phase2_publish_rechecks_applicable_correction_set_after_analysis_race(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="phase2-publication-correction-race-project",
        import_key="phase2-publication-correction-race-import",
    )
    project_id = imported["project"]["projectId"]
    phase1_job = create_analysis_job(
        client,
        auth_headers,
        project_id,
        imported["story"]["revision"],
        idempotency_key="phase2-publication-correction-race-phase1",
    )
    wait_for_job(client, auth_headers, phase1_job["jobId"], {"succeeded"})
    detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    line = detail["dialogueLines"][0]
    current_speaker = detail["dialogueAttributions"][0]["effectiveSpeakerId"]
    selected_character_id = next(
        character["characterId"]
        for character in detail["characters"]
        if character["characterId"] != current_speaker
    )
    original = jobs_module.analyze_whole_book

    def correct_after_analysis(**kwargs: Any) -> dict[str, Any]:
        result = original(**kwargs)
        app.state.projects.correct_speaker(
            project_id=project_id,
            line_id=line["lineId"],
            character_id=selected_character_id,
            reason="Race a durable correction against publication.",
            expected_revision=line["revision"],
        )
        return result

    monkeypatch.setattr(
        jobs_module,
        "analyze_whole_book",
        correct_after_analysis,
    )
    created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-publication-correction-race-run",
    )
    terminal = wait_for_job(
        client,
        auth_headers,
        created["job"]["jobId"],
        {"failed"},
        timeout=20,
    )
    assert terminal["error"]["code"] == "ANALYSIS_RUN_APPROVAL_STALE"
    run_id = created["run"]["runId"]
    with app.state.database.session() as session:
        assert session.query(AnalysisEntityRow).filter_by(run_id=run_id).count() == 0


def test_phase2_review_decision_rejects_stale_current_import_approval(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-stale-review-approval",
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    review = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )["story_structure_review"]
    with app.state.database.session() as session:
        import_review = (
            session.query(ImportReviewRow)
            .filter_by(project_id=project_id, state="approved")
            .order_by(ImportReviewRow.created_at.desc())
            .first()
        )
        assert import_review is not None
        import_review.state = "invalidated"

    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews/story_structure_review/decisions",
        headers=auth_headers,
        json={
            "decision": "approve",
            "expectedRevision": review["revision"],
            "expectedArtifactFingerprint": review["artifactFingerprint"],
            "expectedEvidenceFingerprint": review["evidenceFingerprint"],
            "acknowledgedWarningIds": review["openWarningIds"],
            "rationale": "This stale approval must fail closed.",
            "idempotencyKey": "phase2-stale-review-decision",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ANALYSIS_RUN_APPROVAL_STALE"


def test_phase2_structure_and_registry_corrections_remap_on_compatible_rerun(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-effective-overlay",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    first_run_id = created["run"]["runId"]
    first_run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}",
        headers=auth_headers,
    ).json()["run"]
    chapters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="chapters",
    )
    scenes = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="scenes",
    )
    beats = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="beats",
    )
    assert len(chapters) >= 3
    assert len(scenes) >= 3
    chapters_by_id = {value["entityId"]: value for value in chapters}
    boundary_targets = {
        "add": scenes[0],
        "remove": scenes[1],
        "move": scenes[2],
    }
    boundary_correction_ids: dict[str, str] = {}
    for operation, target in boundary_targets.items():
        parent_id = chapters[-1]["entityId"] if operation == "move" else target["chapterId"]
        correction = _append_phase2_correction(
            client,
            auth_headers,
            project_id=project_id,
            run=first_run,
            category="structure_boundary",
            collection="scenes",
            target=target,
            patch={
                "operation": operation,
                "parentEntityId": parent_id,
                "ordinal": target["ordinal"] + 100,
                "sourceSpan": {
                    key: value
                    for key, value in target["sourceSpan"].items()
                    if key != "textSha256"
                },
                "boundaryKind": target["boundaryKind"],
            },
            idempotency_key=f"phase2-boundary-{operation}",
        )
        boundary_correction_ids[operation] = correction["correctionId"]

    corrected_scenes = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="scenes",
    )
    corrected_by_semantic_id = {value["stableSemanticId"]: value for value in corrected_scenes}
    assert len(corrected_scenes) == len(scenes)
    assert boundary_targets["remove"]["stableSemanticId"] not in corrected_by_semantic_id
    original_add = corrected_by_semantic_id[boundary_targets["add"]["stableSemanticId"]]
    assert "effectiveBoundary" not in original_add
    added_scene = next(
        value
        for value in corrected_scenes
        if value.get("effectiveBoundary", {}).get("correctionId") == boundary_correction_ids["add"]
    )
    assert added_scene["entityId"] != boundary_targets["add"]["entityId"]
    assert added_scene["stableSemanticId"] != original_add["stableSemanticId"]
    assert added_scene["effectiveBoundary"]["operation"] == "add"
    assert added_scene["effectiveBoundary"]["included"] is True
    assert added_scene["effectiveAuthority"] == "human"
    moved_scene = corrected_by_semantic_id[boundary_targets["move"]["stableSemanticId"]]
    assert moved_scene["effectiveBoundary"] == {
        "operation": "move",
        "included": True,
        "parentEntityId": chapters[-1]["entityId"],
        "ordinal": boundary_targets["move"]["ordinal"] + 100,
        "sourceSpan": boundary_targets["move"]["sourceSpan"],
        "authority": "human",
        "correctionId": boundary_correction_ids["move"],
    }
    assert moved_scene["effectiveAuthority"] == "human"
    corrected_chapters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="chapters",
    )
    corrected_beats = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="beats",
    )
    _assert_effective_structure_integrity(
        chapters=corrected_chapters,
        scenes=corrected_scenes,
        beats=corrected_beats,
    )
    assert added_scene["beatCount"] == 0
    assert added_scene["firstBeatId"] is None
    assert added_scene["lastBeatId"] is None
    removed_scene_id = boundary_targets["remove"]["entityId"]
    assert all(beat["sceneId"] != removed_scene_id for beat in corrected_beats)
    original_moved_beat_ids = {
        beat["entityId"]
        for beat in beats
        if beat["sceneId"] == boundary_targets["move"]["entityId"]
    }
    corrected_moved_beats = [
        beat for beat in corrected_beats if beat["sceneId"] == moved_scene["entityId"]
    ]
    assert {beat["entityId"] for beat in corrected_moved_beats} == (original_moved_beat_ids)
    assert all(beat["chapterId"] == chapters[-1]["entityId"] for beat in corrected_moved_beats)

    characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="characters",
    )
    assert len(characters) >= 4
    merge_source, merge_target = characters[0], characters[1]
    split_source = next(value for value in characters[2:] if value["namedMentionIds"])
    split_mention_id = split_source["namedMentionIds"][0]
    split_registry_id = "human-split-registry-character"
    merge_correction = _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=first_run,
        category="character_merge",
        collection="characters",
        target=merge_source,
        patch={"mergeIntoCharacterId": merge_target["entityId"]},
        idempotency_key="phase2-character-merge",
    )
    split_correction = _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=first_run,
        category="character_split",
        collection="characters",
        target=split_source,
        patch={
            "newRegistryCharacterId": split_registry_id,
            "canonicalName": "Split Identity",
            "normalizedCanonicalName": "split identity",
            "mentionIds": [split_mention_id],
        },
        idempotency_key="phase2-character-split",
    )

    corrected_characters = {
        value["stableSemanticId"]: value
        for value in _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=first_run_id,
            collection="characters",
        )
    }
    assert corrected_characters[merge_source["stableSemanticId"]]["effectiveRegistry"] == {
        "operation": "merge",
        "authority": "human",
        "correctionId": merge_correction["correctionId"],
        "mergeIntoCharacterId": merge_target["entityId"],
    }
    assert corrected_characters[split_source["stableSemanticId"]]["effectiveRegistry"] == {
        "operation": "split",
        "authority": "human",
        "correctionId": split_correction["correctionId"],
        "splitIdentity": {
            "registryCharacterId": split_registry_id,
            "canonicalName": "Split Identity",
            "normalizedCanonicalName": "split identity",
            "mentionIds": [split_mention_id],
        },
    }
    first_split_identity = next(
        value for value in corrected_characters.values() if value["entityId"] == split_registry_id
    )
    assert first_split_identity["entityId"] == split_registry_id
    assert first_split_identity["characterId"] == split_registry_id
    assert first_split_identity["registryCharacterId"] == split_registry_id
    assert first_split_identity["namedMentionIds"] == [split_mention_id]
    dialogue_target = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="dialogue-lines",
    )[0]
    _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=first_run,
        category="dialogue_speaker",
        collection="dialogue-lines",
        target=dialogue_target,
        patch={
            "speakerCharacterId": split_registry_id,
            "selectedCandidateId": None,
            "requiresHumanReview": False,
        },
        idempotency_key="phase2-split-identity-dialogue-reference",
    )
    assert (
        _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=first_run_id,
            collection="dialogue-lines",
        )[0]["effectiveAttribution"]["speakerCharacterId"]
        == split_registry_id
    )
    first_mentions = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="mentions",
    )
    first_split_mention = next(
        value for value in first_mentions if value["entityId"] == split_mention_id
    )
    assert first_split_mention["effectiveCharacterId"] == split_registry_id
    assert first_split_mention["effectiveAuthority"] == "human"

    second_created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-effective-overlay-rerun",
    )
    wait_for_job(
        client,
        auth_headers,
        second_created["job"]["jobId"],
        {"succeeded"},
        timeout=20,
    )
    second_run_id = second_created["run"]["runId"]
    second_chapters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=second_run_id,
        collection="chapters",
    )
    second_scenes = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=second_run_id,
        collection="scenes",
    )
    second_characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=second_run_id,
        collection="characters",
    )
    second_mentions = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=second_run_id,
        collection="mentions",
    )
    second_beats = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=second_run_id,
        collection="beats",
    )
    _assert_effective_structure_integrity(
        chapters=second_chapters,
        scenes=second_scenes,
        beats=second_beats,
    )
    second_chapters_by_semantic_id = {value["stableSemanticId"]: value for value in second_chapters}
    second_scenes_by_semantic_id = {value["stableSemanticId"]: value for value in second_scenes}
    second_characters_by_semantic_id = {
        value["stableSemanticId"]: value for value in second_characters
    }
    second_mentions_by_semantic_id = {value["stableSemanticId"]: value for value in second_mentions}
    assert set(chapters_by_id) != {value["entityId"] for value in second_chapters}
    assert boundary_targets["remove"]["stableSemanticId"] not in second_scenes_by_semantic_id
    second_original_add = second_scenes_by_semantic_id[boundary_targets["add"]["stableSemanticId"]]
    assert "effectiveBoundary" not in second_original_add
    second_added_scene = second_scenes_by_semantic_id[added_scene["stableSemanticId"]]
    assert second_added_scene["effectiveBoundary"]["operation"] == "add"
    assert (
        second_added_scene["effectiveBoundary"]["correctionId"] == (boundary_correction_ids["add"])
    )
    assert second_added_scene["entityId"] != added_scene["entityId"]
    second_moved_scene = second_scenes_by_semantic_id[boundary_targets["move"]["stableSemanticId"]]
    destination = second_chapters_by_semantic_id[chapters[-1]["stableSemanticId"]]
    assert second_moved_scene["effectiveBoundary"]["operation"] == "move"
    assert second_moved_scene["effectiveBoundary"]["parentEntityId"] == (destination["entityId"])
    assert second_moved_scene["effectiveBoundary"]["parentEntityId"] != (chapters[-1]["entityId"])

    second_merge_source = second_characters_by_semantic_id[merge_source["stableSemanticId"]]
    second_merge_target = second_characters_by_semantic_id[merge_target["stableSemanticId"]]
    assert (
        second_merge_source["effectiveRegistry"]["mergeIntoCharacterId"]
        == second_merge_target["entityId"]
    )
    assert (
        second_merge_source["effectiveRegistry"]["mergeIntoCharacterId"] != merge_target["entityId"]
    )

    second_split_source = second_characters_by_semantic_id[split_source["stableSemanticId"]]
    second_split_mention = second_mentions_by_semantic_id[first_split_mention["stableSemanticId"]]
    split_identity = second_split_source["effectiveRegistry"]["splitIdentity"]
    assert split_identity["mentionIds"] == [second_split_mention["entityId"]]
    assert split_identity["mentionIds"] != [split_mention_id]
    assert second_split_mention["effectiveCharacterId"] == split_registry_id
    assert second_split_mention["effectiveAuthority"] == "human"
    second_split_identity = next(
        value for value in second_characters if value["entityId"] == split_registry_id
    )
    assert second_split_identity["stableSemanticId"] == (first_split_identity["stableSemanticId"])
    assert second_split_identity["namedMentionIds"] == [second_split_mention["entityId"]]
    assert (
        _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=second_run_id,
            collection="dialogue-lines",
        )[0]["effectiveAttribution"]["speakerCharacterId"]
        == split_registry_id
    )


def test_structure_chapter_add_and_remove_cascade_to_descendants(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-structure-chapter-cascade",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    chapters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="chapters",
    )
    scenes = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="scenes",
    )
    assert len(chapters) >= 2
    added_from = chapters[0]
    removed = chapters[1]
    removed_scene_ids = {
        scene["entityId"] for scene in scenes if scene["chapterId"] == removed["entityId"]
    }
    add_correction = _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        category="structure_boundary",
        collection="chapters",
        target=added_from,
        patch={
            "operation": "add",
            "parentEntityId": run["storyId"],
            "ordinal": added_from["ordinal"] + 100,
            "sourceSpan": {
                key: value
                for key, value in added_from["sourceSpan"].items()
                if key != "textSha256"
            },
        },
        idempotency_key="phase2-add-empty-chapter",
    )
    _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        category="structure_boundary",
        collection="chapters",
        target=removed,
        patch={
            "operation": "remove",
            "parentEntityId": run["storyId"],
            "ordinal": removed["ordinal"],
            "sourceSpan": {
                key: value
                for key, value in removed["sourceSpan"].items()
                if key != "textSha256"
            },
        },
        idempotency_key="phase2-remove-chapter-cascade",
    )

    corrected_chapters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="chapters",
    )
    corrected_scenes = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="scenes",
    )
    corrected_beats = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="beats",
    )
    _assert_effective_structure_integrity(
        chapters=corrected_chapters,
        scenes=corrected_scenes,
        beats=corrected_beats,
    )
    corrected_chapter_ids = {chapter["entityId"] for chapter in corrected_chapters}
    assert removed["entityId"] not in corrected_chapter_ids
    assert all(scene["entityId"] not in removed_scene_ids for scene in corrected_scenes)
    assert all(beat["sceneId"] not in removed_scene_ids for beat in corrected_beats)
    synthetic = next(
        chapter
        for chapter in corrected_chapters
        if chapter.get("effectiveBoundary", {}).get("correctionId")
        == add_correction["correctionId"]
    )
    assert synthetic["sceneCount"] == 0
    assert synthetic["firstSceneId"] is None
    assert synthetic["lastSceneId"] is None


def test_character_registry_rejects_merge_cycles_and_conflicting_split_ownership(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-registry-conflict-guards",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    mentions = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    ambiguous_mention = next(
        mention for mention in mentions if len(mention["candidateCharacterIds"]) >= 2
    )
    split_source_ids = ambiguous_mention["candidateCharacterIds"][:2]
    characters_by_id = {character["entityId"]: character for character in characters}
    merge_chain = [
        character for character in characters if character["entityId"] not in split_source_ids
    ][:3]
    assert len(merge_chain) == 3
    _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        category="character_merge",
        collection="characters",
        target=merge_chain[0],
        patch={"mergeIntoCharacterId": merge_chain[1]["entityId"]},
        idempotency_key="phase2-merge-cycle-first-link",
    )
    merge_chain[1] = next(
        character
        for character in _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="characters",
        )
        if character["entityId"] == merge_chain[1]["entityId"]
    )
    _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        category="character_merge",
        collection="characters",
        target=merge_chain[1],
        patch={"mergeIntoCharacterId": merge_chain[2]["entityId"]},
        idempotency_key="phase2-merge-cycle-second-link",
    )
    merge_chain[2] = next(
        character
        for character in _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            collection="characters",
        )
        if character["entityId"] == merge_chain[2]["entityId"]
    )
    cycle_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "character_merge",
            "targetCollection": "characters",
            "targetEntityId": merge_chain[2]["entityId"],
            "expectedTargetRevision": merge_chain[2]["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": merge_chain[2]["effectiveValueFingerprint"],
            "patch": {"mergeIntoCharacterId": merge_chain[0]["entityId"]},
            "reason": "A merge cycle must fail closed.",
            "idempotencyKey": "phase2-merge-cycle-closing-link",
        },
    )
    assert cycle_response.status_code == 422, cycle_response.text
    assert cycle_response.json()["error"]["code"] == "CORRECTION_PATCH_INVALID"

    first_split_source = characters_by_id[split_source_ids[0]]
    second_split_source = characters_by_id[split_source_ids[1]]
    _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        category="character_split",
        collection="characters",
        target=first_split_source,
        patch={
            "newRegistryCharacterId": "human-split-owner-one",
            "canonicalName": "Split Owner One",
            "normalizedCanonicalName": "split owner one",
            "mentionIds": [ambiguous_mention["entityId"]],
        },
        idempotency_key="phase2-split-owner-one",
    )
    split_conflict_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "character_split",
            "targetCollection": "characters",
            "targetEntityId": second_split_source["entityId"],
            "expectedTargetRevision": second_split_source["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": second_split_source["effectiveValueFingerprint"],
            "patch": {
                "newRegistryCharacterId": "human-split-owner-two",
                "canonicalName": "Split Owner Two",
                "normalizedCanonicalName": "split owner two",
                "mentionIds": [ambiguous_mention["entityId"]],
            },
            "reason": "One mention cannot be owned by two splits.",
            "idempotencyKey": "phase2-split-owner-two",
        },
    )
    assert split_conflict_response.status_code == 422, split_conflict_response.text
    assert split_conflict_response.json()["error"]["code"] == "CORRECTION_PATCH_INVALID"


@pytest.mark.parametrize(
    ("first_category", "second_category"),
    (
        ("character_split", "mention_resolution"),
        ("mention_resolution", "character_split"),
        ("character_split", "character_merge"),
        ("character_merge", "character_split"),
    ),
)
def test_registry_cross_target_conflicts_fail_closed_in_both_orders(
    client: TestClient,
    auth_headers: dict[str, str],
    first_category: str,
    second_category: str,
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key=f"phase2-cross-target-{first_category}-{second_category}",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    mentions = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    mention = next(
        value
        for value in mentions
        if value["effectiveCharacterId"] is not None
        and value["effectiveCharacterId"] in {character["entityId"] for character in characters}
    )
    source = next(
        character
        for character in characters
        if character["entityId"] == mention["effectiveCharacterId"]
    )
    merge_target = next(
        character for character in characters if character["entityId"] != source["entityId"]
    )
    split_registry_id = (
        f"split-{first_category.replace('_', '-')}-{second_category.replace('_', '-')}"
    )

    def correction_target(category: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        if category == "mention_resolution":
            current_mention = next(
                value
                for value in _phase2_collection(
                    client,
                    auth_headers,
                    project_id=project_id,
                    run_id=run_id,
                    collection="mentions",
                )
                if value["entityId"] == mention["entityId"]
            )
            return (
                "mentions",
                current_mention,
                {
                    "resolution": "resolved",
                    "effectiveCharacterId": source["entityId"],
                    "candidateCharacterIds": [source["entityId"]],
                },
            )
        current_source = next(
            value
            for value in _phase2_collection(
                client,
                auth_headers,
                project_id=project_id,
                run_id=run_id,
                collection="characters",
            )
            if value["entityId"] == source["entityId"]
        )
        if category == "character_merge":
            return (
                "characters",
                current_source,
                {"mergeIntoCharacterId": merge_target["entityId"]},
            )
        return (
            "characters",
            current_source,
            {
                "newRegistryCharacterId": split_registry_id,
                "canonicalName": "Cross Target Split",
                "normalizedCanonicalName": "cross target split",
                "mentionIds": [mention["entityId"]],
            },
        )

    first_collection, first_target, first_patch = correction_target(first_category)
    _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        category=first_category,
        collection=first_collection,
        target=first_target,
        patch=first_patch,
        idempotency_key=f"first-{first_category}-{second_category}",
    )
    second_collection, second_target, second_patch = correction_target(second_category)
    response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": second_category,
            "targetCollection": second_collection,
            "targetEntityId": second_target["entityId"],
            "expectedTargetRevision": second_target["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": second_target["effectiveValueFingerprint"],
            "patch": second_patch,
            "reason": "Mutually conflicting registry ownership must fail closed.",
            "idempotencyKey": f"second-{first_category}-{second_category}",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "CORRECTION_PATCH_INVALID"

    effective_characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    effective_character_ids = {value["entityId"] for value in effective_characters}
    effective_mentions = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="mentions",
    )
    assert all(
        value["effectiveCharacterId"] is None
        or value["effectiveCharacterId"] in effective_character_ids
        for value in effective_mentions
    )
    assert all(
        set(value["candidateCharacterIds"]) <= effective_character_ids
        for value in effective_mentions
    )


def test_incompatible_prior_source_correction_does_not_create_phantom_revision(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-incompatible-correction-source-one",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    first_run_id = created["run"]["runId"]
    first_run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}",
        headers=auth_headers,
    ).json()["run"]
    first_scene = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=first_run_id,
        collection="scenes",
    )[0]
    _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=first_run,
        category="structure_label",
        collection="scenes",
        target=first_scene,
        patch={"heading": "First source human heading"},
        idempotency_key="phase2-incompatible-first-correction",
    )

    changed_source = story_bytes + (
        b"\n\n# Chapter Four: Changed Source\n\n## Scene: New Tail\n\nA new source-only tail.\n"
    )
    queued_import = submit_import(
        client,
        auth_headers,
        project_id=project_id,
        filename="changed-source.md",
        content=changed_source,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="phase2-incompatible-source-import",
    )
    wait_for_job(
        client,
        auth_headers,
        queued_import["job"]["jobId"],
        {"succeeded"},
    )
    new_review = review_for_extraction(
        client,
        auth_headers,
        project_id=project_id,
        extraction_id=queued_import["extraction"]["extractionId"],
    )
    approved = decide_import_review(
        client,
        auth_headers,
        project_id=project_id,
        review=new_review,
        decision="approved",
        rationale="Approve the changed source for incompatibility testing.",
        idempotency_key="phase2-incompatible-source-approval",
    )
    assert approved.status_code == 200, approved.text
    detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    changed_import = {
        "project": detail["project"],
        "extraction": next(
            value
            for value in detail["extractions"]
            if value["extractionId"] == queued_import["extraction"]["extractionId"]
        ),
        "review": next(
            value
            for value in detail["importReviews"]
            if value["reviewId"] == new_review["reviewId"]
        ),
    }
    second_created = queue_phase2_run(
        client,
        auth_headers,
        imported=changed_import,
        idempotency_key="phase2-incompatible-correction-source-two",
    )
    wait_for_job(
        client,
        auth_headers,
        second_created["job"]["jobId"],
        {"succeeded"},
    )
    second_run_id = second_created["run"]["runId"]
    second_run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{second_run_id}",
        headers=auth_headers,
    ).json()["run"]
    second_scene = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=second_run_id,
        collection="scenes",
    )[0]
    assert second_scene["effectiveRevision"] == 1
    assert second_scene.get("heading") != "First source human heading"

    second_correction = _append_phase2_correction(
        client,
        auth_headers,
        project_id=project_id,
        run=second_run,
        category="structure_label",
        collection="scenes",
        target=second_scene,
        patch={"heading": "Second source independent heading"},
        idempotency_key="phase2-incompatible-second-correction",
    )
    assert second_correction["expectedTargetRevision"] == 1
    assert "supersedesCorrectionId" not in second_correction


def test_phase2_all_typed_correction_overlays_are_effective_and_survive_rerun(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-all-correction-overlays",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    first_run_id = created["run"]["runId"]
    first_run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}",
        headers=auth_headers,
    ).json()["run"]
    collection_names = (
        "chapters",
        "characters",
        "mentions",
        "dialogue-lines",
        "pov-segments",
        "locations",
        "timeline-events",
        "temporal-constraints",
        "relationships",
        "emotional-states",
        "dramatic-intents",
        "continuity-findings",
        "scenes",
    )
    first_collections = {
        collection: _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=first_run_id,
            collection=collection,
        )
        for collection in collection_names
    }
    characters = first_collections["characters"]
    assert len(characters) >= 6
    alias_remove_target = next(value for value in characters[2:] if value["aliases"])
    locations = first_collections["locations"]
    assert len(locations) >= 2
    scenes = first_collections["scenes"]
    timeline_events = first_collections["timeline-events"]
    relationship = first_collections["relationships"][0]
    alias_source_span = characters[1]["aliases"][0]["effectiveRange"]["sourceRange"]

    referenced_semantics = {
        "mention_character": characters[3]["stableSemanticId"],
        "pov_character": characters[0]["stableSemanticId"],
        "location_parent": locations[1]["stableSemanticId"],
        "relationship_source": characters[0]["stableSemanticId"],
        "relationship_target": characters[1]["stableSemanticId"],
        "relationship_first_scene": scenes[0]["stableSemanticId"],
        "relationship_last_scene": scenes[-1]["stableSemanticId"],
        "relationship_first_event": timeline_events[0]["stableSemanticId"],
        "relationship_last_event": timeline_events[-1]["stableSemanticId"],
        "intent_target": characters[4]["stableSemanticId"],
    }

    cases: list[dict[str, Any]] = [
        {
            "category": "structure_label",
            "collection": "chapters",
            "target": first_collections["chapters"][0],
            "patch": {"title": "Human Corrected Chapter"},
        },
        {
            "category": "character_identity",
            "collection": "characters",
            "target": characters[0],
            "patch": {
                "canonicalName": "The Unresolved Signal",
                "normalizedCanonicalName": "the unresolved signal",
                "identityStatus": "unresolved",
            },
        },
        {
            "category": "character_alias",
            "collection": "characters",
            "target": characters[1],
            "patch": {
                "operation": "add",
                "alias": {
                    "aliasId": "human-alias-added",
                    "characterId": characters[1]["entityId"],
                    "alias": "The Relay Keeper",
                    "normalizedAlias": "the relay keeper",
                    "kind": "nickname",
                    "ambiguous": False,
                    "effectiveRange": {
                        "sourceRange": alias_source_span,
                        "validFromEventId": timeline_events[0]["entityId"],
                        "validThroughEventId": timeline_events[-1]["entityId"],
                    },
                    "change": "introduced",
                    "confidence": {
                        "score": 1,
                        "classification": "high",
                        "basis": "human_correction",
                        "calibrationId": "governed-local-rules-v1",
                    },
                    "evidence": [],
                },
            },
        },
        {
            "category": "character_alias",
            "collection": "characters",
            "target": alias_remove_target,
            "patch": {
                "operation": "remove",
                "aliasId": alias_remove_target["aliases"][0]["aliasId"],
            },
        },
        {
            "category": "mention_resolution",
            "collection": "mentions",
            "target": first_collections["mentions"][0],
            "patch": {
                "resolution": "resolved",
                "effectiveCharacterId": characters[3]["entityId"],
                "candidateCharacterIds": [characters[3]["entityId"]],
            },
        },
        {
            "category": "dialogue_speaker",
            "collection": "dialogue-lines",
            "target": first_collections["dialogue-lines"][0],
            "patch": {
                "speakerCharacterId": None,
                "selectedCandidateId": None,
                "requiresHumanReview": True,
            },
        },
        {
            "category": "point_of_view",
            "collection": "pov-segments",
            "target": first_collections["pov-segments"][0],
            "patch": {
                "mode": "third_person_limited",
                "viewpointCharacterId": characters[0]["entityId"],
                "narratorCharacterId": characters[0]["entityId"],
            },
        },
        {
            "category": "location_identity",
            "collection": "locations",
            "target": locations[0],
            "patch": {
                "canonicalName": "Corrected Signal Annex",
                "normalizedCanonicalName": "corrected signal annex",
                "kind": "interior",
                "parentLocationId": locations[1]["entityId"],
            },
        },
        {
            "category": "location_alias",
            "collection": "locations",
            "target": locations[1],
            "patch": {
                "operation": "add",
                "alias": "The Human-Named Crossing",
            },
        },
        {
            "category": "temporal_order",
            "collection": "temporal-constraints",
            "target": first_collections["temporal-constraints"][0],
            "patch": {
                "relation": "overlaps",
                "approximate": True,
                "status": "unresolved",
            },
        },
        {
            "category": "relationship",
            "collection": "relationships",
            "target": relationship,
            "patch": {
                "sourceCharacterId": characters[0]["entityId"],
                "targetCharacterId": characters[1]["entityId"],
                "kind": "custom",
                "state": "Human-defined guarded alliance",
                "change": "strengthened",
                "scope": {
                    "kind": "scene_range",
                    "firstSceneId": scenes[0]["entityId"],
                    "lastSceneId": scenes[-1]["entityId"],
                    "sourceRange": relationship["scope"]["sourceRange"],
                },
                "validFromEventId": timeline_events[0]["entityId"],
                "validThroughEventId": timeline_events[-1]["entityId"],
            },
        },
        {
            "category": "emotional_state",
            "collection": "emotional-states",
            "target": first_collections["emotional-states"][0],
            "patch": {
                "emotion": "custom",
                "customEmotion": "watchful resolve",
                "note": "A human interpretation bounded to the evidence.",
                "valence": 0.25,
                "arousal": 0.65,
                "intensity": 0.8,
                "progression": "rising",
            },
        },
        {
            "category": "dramatic_intent",
            "collection": "dramatic-intents",
            "target": first_collections["dramatic-intents"][0],
            "patch": {
                "intent": "custom",
                "customIntent": "guard the relay secret",
                "dramaticFunction": "custom",
                "customDramaticFunction": "bind the alliance",
                "note": "Human dramatic interpretation.",
                "targetCharacterId": characters[4]["entityId"],
                "status": "pursued",
            },
        },
        {
            "category": "continuity_disposition",
            "collection": "continuity-findings",
            "target": first_collections["continuity-findings"][0],
            "patch": {
                "disposition": "intentional",
                "explanation": "The apparent discrepancy is intentional.",
            },
        },
    ]

    def assert_case(
        case: dict[str, Any],
        item: dict[str, Any],
        lookups: dict[str, dict[str, dict[str, Any]]],
    ) -> None:
        category = case["category"]
        patch = case["patch"]
        if category == "structure_label":
            assert item["title"] == patch["title"]
        elif category == "character_identity":
            assert item["canonicalName"] == patch["canonicalName"]
            assert item["normalizedCanonicalName"] == patch["normalizedCanonicalName"]
            assert item["identityStatus"] == "unresolved"
        elif category == "character_alias" and patch["operation"] == "add":
            alias = next(
                value for value in item["aliases"] if value["aliasId"] == "human-alias-added"
            )
            assert alias["alias"] == "The Relay Keeper"
            assert alias["characterId"] == item["entityId"]
            assert (
                alias["effectiveRange"]["validFromEventId"]
                == lookups["timeline-events"][referenced_semantics["relationship_first_event"]][
                    "entityId"
                ]
            )
            assert (
                alias["effectiveRange"]["validThroughEventId"]
                == lookups["timeline-events"][referenced_semantics["relationship_last_event"]][
                    "entityId"
                ]
            )
        elif category == "character_alias":
            assert all(value["aliasId"] != patch["aliasId"] for value in item["aliases"])
        elif category == "mention_resolution":
            character = lookups["characters"][referenced_semantics["mention_character"]]
            assert item["resolution"] == "resolved"
            assert item["effectiveCharacterId"] == character["entityId"]
            assert item["candidateCharacterIds"] == [character["entityId"]]
        elif category == "dialogue_speaker":
            assert item["effectiveAttribution"]["speakerCharacterId"] is None
            assert item["effectiveAttribution"]["selectedCandidateId"] is None
            assert item["effectiveAttribution"]["requiresHumanReview"] is True
            assert item["effectiveAttribution"]["authority"] == "human_correction"
        elif category == "point_of_view":
            character = lookups["characters"][referenced_semantics["pov_character"]]
            assert item["mode"] == "third_person_limited"
            assert item["viewpointCharacterId"] == character["entityId"]
            assert item["narratorCharacterId"] == character["entityId"]
        elif category == "location_identity":
            parent = lookups["locations"][referenced_semantics["location_parent"]]
            assert item["canonicalName"] == "Corrected Signal Annex"
            assert item["normalizedCanonicalName"] == ("corrected signal annex")
            assert item["kind"] == "interior"
            assert item["parentLocationId"] == parent["entityId"]
        elif category == "location_alias":
            assert "The Human-Named Crossing" in item["aliases"]
        elif category == "temporal_order":
            assert {key: item[key] for key in ("relation", "approximate", "status")} == patch
        elif category == "relationship":
            source = lookups["characters"][referenced_semantics["relationship_source"]]
            target = lookups["characters"][referenced_semantics["relationship_target"]]
            first_scene = lookups["scenes"][referenced_semantics["relationship_first_scene"]]
            last_scene = lookups["scenes"][referenced_semantics["relationship_last_scene"]]
            first_event = lookups["timeline-events"][
                referenced_semantics["relationship_first_event"]
            ]
            last_event = lookups["timeline-events"][referenced_semantics["relationship_last_event"]]
            assert item["sourceCharacterId"] == source["entityId"]
            assert item["targetCharacterId"] == target["entityId"]
            assert item["kind"] == "custom"
            assert item["state"] == "Human-defined guarded alliance"
            assert item["change"] == "strengthened"
            assert item["scope"]["firstSceneId"] == first_scene["entityId"]
            assert item["scope"]["lastSceneId"] == last_scene["entityId"]
            assert item["validFromEventId"] == first_event["entityId"]
            assert item["validThroughEventId"] == last_event["entityId"]
        elif category == "emotional_state":
            assert all(item[key] == value for key, value in patch.items())
        elif category == "dramatic_intent":
            target = lookups["characters"][referenced_semantics["intent_target"]]
            assert all(
                item[key] == value for key, value in patch.items() if key != "targetCharacterId"
            )
            assert item["targetCharacterId"] == target["entityId"]
        else:
            assert category == "continuity_disposition"
            assert item["humanDisposition"]["disposition"] == "intentional"
            assert item["humanDisposition"]["explanation"] == patch["explanation"]

    first_lookups = {
        collection: {item["stableSemanticId"]: item for item in values}
        for collection, values in first_collections.items()
    }
    for index, case in enumerate(cases):
        target = case["target"]
        correction = _append_phase2_correction(
            client,
            auth_headers,
            project_id=project_id,
            run=first_run,
            category=case["category"],
            collection=case["collection"],
            target=target,
            patch=case["patch"],
            idempotency_key=f"phase2-typed-overlay-{index}",
        )
        refreshed_values = _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=first_run_id,
            collection=case["collection"],
        )
        refreshed = next(
            value
            for value in refreshed_values
            if value["stableSemanticId"] == target["stableSemanticId"]
        )
        assert refreshed["effectiveAuthority"] == "human"
        assert refreshed["effectiveRevision"] == target["effectiveRevision"] + 1
        assert refreshed["effectiveValueFingerprint"] == correction["correctedValueFingerprint"]
        assert_case(case, refreshed, first_lookups)
        case["correctionId"] = correction["correctionId"]

    def paged_corrections(target_run_id: str) -> list[dict[str, Any]]:
        cursor: str | None = None
        values: list[dict[str, Any]] = []
        expected_total: int | None = None
        while True:
            response = client.get(
                f"/api/v1/projects/{project_id}/analysis-runs/{target_run_id}/corrections",
                headers=auth_headers,
                params={
                    "limit": 3,
                    **({"cursor": cursor} if cursor is not None else {}),
                },
            )
            assert response.status_code == 200, response.text
            page = response.json()
            expected_total = page["total"]
            assert page["pageSize"] <= 3
            values.extend(page["items"])
            cursor = page.get("nextCursor")
            if cursor is None:
                break
        assert len(values) == expected_total
        assert len({value["correctionId"] for value in values}) == len(values)
        return values

    first_correction_page = paged_corrections(first_run_id)
    assert {value["correctionId"] for value in first_correction_page} == {
        case["correctionId"] for case in cases
    }

    second_created = queue_phase2_run(
        client,
        auth_headers,
        imported=imported,
        idempotency_key="phase2-all-correction-overlays-rerun",
    )
    wait_for_job(
        client,
        auth_headers,
        second_created["job"]["jobId"],
        {"succeeded"},
        timeout=20,
    )
    second_run_id = second_created["run"]["runId"]
    second_collections = {
        collection: _phase2_collection(
            client,
            auth_headers,
            project_id=project_id,
            run_id=second_run_id,
            collection=collection,
        )
        for collection in collection_names
    }
    second_lookups = {
        collection: {item["stableSemanticId"]: item for item in values}
        for collection, values in second_collections.items()
    }
    for case in cases:
        target = second_lookups[case["collection"]][case["target"]["stableSemanticId"]]
        assert target["effectiveAuthority"] == "human"
        assert target["provenance"]["correctionId"] == case["correctionId"]
        assert_case(case, target, second_lookups)
    identity_case = next(case for case in cases if case["category"] == "character_identity")
    first_identity = first_lookups["characters"][identity_case["target"]["stableSemanticId"]]
    second_identity = second_lookups["characters"][identity_case["target"]["stableSemanticId"]]
    assert second_identity["registryCharacterId"] == first_identity["registryCharacterId"]

    second_correction_page = paged_corrections(second_run_id)
    assert {value["correctionId"] for value in second_correction_page} == {
        case["correctionId"] for case in cases
    }
    assert all(value["runId"] == second_run_id for value in second_correction_page)

    run_cursor: str | None = None
    listed_run_ids: list[str] = []
    while True:
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs",
            headers=auth_headers,
            params={
                "limit": 1,
                **({"cursor": run_cursor} if run_cursor is not None else {}),
            },
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 2
        listed_run_ids.extend(value["runId"] for value in page["runs"])
        run_cursor = page.get("nextCursor")
        if run_cursor is None:
            break
    assert listed_run_ids == [first_run_id, second_run_id]


def test_phase2_review_decisions_append_superseding_same_snapshot_history(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-review-supersession",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    approvals = {
        gate_id: _approve_phase2_gate(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            gate_id=gate_id,
            idempotency_key=f"phase2-supersession-approve-{index}",
        )
        for index, gate_id in enumerate(
            (
                "story_structure_review",
                "character_registry_review",
                "dialogue_attribution_review",
                "whole_book_analysis_review",
            )
        )
    }
    assert all(
        value["review"]["latestDecision"] == value["decision"] for value in approvals.values()
    )

    before_request = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )["story_structure_review"]
    request_changes_payload = {
        "decision": "request_changes",
        "expectedRevision": before_request["revision"],
        "expectedArtifactFingerprint": before_request["artifactFingerprint"],
        "expectedEvidenceFingerprint": before_request["evidenceFingerprint"],
        "acknowledgedWarningIds": [],
        "rationale": "The structure needs another human pass.",
        "idempotencyKey": "phase2-supersession-request-changes",
    }
    request_changes_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}"
        "/reviews/story_structure_review/decisions",
        headers=auth_headers,
        json=request_changes_payload,
    )
    assert request_changes_response.status_code == 200, request_changes_response.text
    request_changes = request_changes_response.json()
    assert request_changes["review"]["state"] == "changes_requested"
    assert request_changes["review"]["revision"] == (before_request["revision"] + 1)
    assert (
        request_changes["decision"]["supersedesDecisionId"]
        == (approvals["story_structure_review"]["decision"]["decisionId"])
    )
    assert request_changes["review"]["latestDecision"] == (request_changes["decision"])
    replay_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}"
        "/reviews/story_structure_review/decisions",
        headers=auth_headers,
        json=request_changes_payload,
    )
    assert replay_response.status_code == 200, replay_response.text
    replay = replay_response.json()
    assert replay["decision"] == request_changes["decision"]
    assert replay["review"] == request_changes["review"]
    reviews_after_request = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    assert reviews_after_request["whole_book_analysis_review"]["state"] == ("invalidated")

    rejected = _decide_phase2_gate(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        gate_id="story_structure_review",
        decision="reject",
        rationale="The current structure is not acceptable.",
        idempotency_key="phase2-supersession-reject",
    )
    assert rejected["review"]["state"] == "rejected"
    assert rejected["review"]["latestDecision"] == rejected["decision"]
    assert (
        rejected["decision"]["supersedesDecisionId"] == (request_changes["decision"]["decisionId"])
    )
    reapproved = _decide_phase2_gate(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        gate_id="story_structure_review",
        decision="approve",
        idempotency_key="phase2-supersession-reapprove",
    )
    assert reapproved["review"]["state"] == "approved"
    assert reapproved["review"]["latestDecision"] == reapproved["decision"]
    assert reapproved["decision"]["supersedesDecisionId"] == (rejected["decision"]["decisionId"])
    assert (
        _phase2_reviews(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
        )["whole_book_analysis_review"]["state"]
        == "invalidated"
    )

    whole_book_reapproval = _approve_phase2_gate(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        gate_id="whole_book_analysis_review",
        idempotency_key="phase2-supersession-whole-book-reapprove",
    )
    assert (
        whole_book_reapproval["decision"]["supersedesDecisionId"]
        == (approvals["whole_book_analysis_review"]["decision"]["decisionId"])
    )
    persisted_reviews = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    assert persisted_reviews["story_structure_review"]["latestDecision"] == reapproved["decision"]
    assert (
        persisted_reviews["whole_book_analysis_review"]["latestDecision"]
        == whole_book_reapproval["decision"]
    )
    reapproved_run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    )
    assert reapproved_run.status_code == 200, reapproved_run.text
    assert reapproved_run.json()["run"]["reviewEligibility"] == "blocked_by_warnings"


def test_phase2_corrections_invalidate_only_dependent_gate_approvals(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    story_bytes = (
        Path(__file__).parents[3] / "fixtures" / "synthetic-story" / "sample-story.md"
    ).read_bytes()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-dependent-gates",
        story_bytes=story_bytes,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    gate_order = (
        "story_structure_review",
        "character_registry_review",
        "dialogue_attribution_review",
        "whole_book_analysis_review",
    )
    initial_decision_ids: dict[str, str] = {}
    for index, gate_id in enumerate(gate_order):
        approval = _approve_phase2_gate(
            client,
            auth_headers,
            project_id=project_id,
            run_id=run_id,
            gate_id=gate_id,
            idempotency_key=f"phase2-initial-gate-{index}",
        )
        initial_decision_ids[gate_id] = approval["decision"]["decisionId"]

    characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    dialogue_lines = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="dialogue-lines",
    )
    dialogue_character_ids = {
        candidate["characterId"]
        for line in dialogue_lines
        for candidate in line["candidates"]
        if candidate.get("characterId") is not None
    } | {
        attribution["speakerCharacterId"]
        for line in dialogue_lines
        if (attribution := line["effectiveAttribution"]).get("speakerCharacterId") is not None
    }
    unreferenced = next(
        value for value in characters if value["entityId"] not in dialogue_character_ids
    )
    unrelated_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "character_identity",
            "targetCollection": "characters",
            "targetEntityId": unreferenced["entityId"],
            "expectedTargetRevision": unreferenced["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": unreferenced["effectiveValueFingerprint"],
            "patch": {
                "canonicalName": "Unreferenced Human Identity",
                "normalizedCanonicalName": "unreferenced human identity",
                "identityStatus": "resolved",
            },
            "reason": "Correct a character outside dialogue evidence.",
            "idempotencyKey": "phase2-unreferenced-character-correction",
        },
    )
    assert unrelated_response.status_code == 200, unrelated_response.text
    assert set(unrelated_response.json()["invalidatedGateIds"]) == {
        "character_registry_review",
        "whole_book_analysis_review",
    }
    after_unrelated = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    for gate_id in (
        "story_structure_review",
        "dialogue_attribution_review",
    ):
        assert after_unrelated[gate_id]["state"] == "approved"
        assert after_unrelated[gate_id]["latestDecisionId"] == initial_decision_ids[gate_id]
    for gate_id in (
        "character_registry_review",
        "whole_book_analysis_review",
    ):
        assert after_unrelated[gate_id]["state"] == "invalidated"
        assert after_unrelated[gate_id]["latestDecisionId"] == initial_decision_ids[gate_id]

    character_reapproval = _approve_phase2_gate(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        gate_id="character_registry_review",
        idempotency_key="phase2-character-gate-reapproval",
    )
    whole_book_reapproval = _approve_phase2_gate(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        gate_id="whole_book_analysis_review",
        idempotency_key="phase2-whole-book-gate-reapproval",
    )
    reapproved_decision_ids = {
        "character_registry_review": character_reapproval["decision"]["decisionId"],
        "whole_book_analysis_review": whole_book_reapproval["decision"]["decisionId"],
    }

    refreshed_characters = _phase2_collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="characters",
    )
    referenced = next(
        value for value in refreshed_characters if value["entityId"] in dialogue_character_ids
    )
    dependent_payload = {
        "category": "character_identity",
        "targetCollection": "characters",
        "targetEntityId": referenced["entityId"],
        "expectedTargetRevision": referenced["effectiveRevision"],
        "expectedRunFingerprint": run["runFingerprint"],
        "previousValueFingerprint": referenced["effectiveValueFingerprint"],
        "patch": {
            "canonicalName": "Dialogue-Referenced Human Identity",
            "normalizedCanonicalName": ("dialogue-referenced human identity"),
            "identityStatus": "resolved",
        },
        "reason": "Correct a character included in dialogue evidence.",
        "idempotencyKey": "phase2-referenced-character-correction",
    }
    dependent_response = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json=dependent_payload,
    )
    assert dependent_response.status_code == 200, dependent_response.text
    assert set(dependent_response.json()["invalidatedGateIds"]) == {
        "character_registry_review",
        "dialogue_attribution_review",
        "whole_book_analysis_review",
    }
    replay = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json=dependent_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["correction"] == dependent_response.json()["correction"]
    assert replay.json()["invalidatedGateIds"] == dependent_response.json()["invalidatedGateIds"]
    conflict = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json=dependent_payload | {"reason": "A different immutable reason."},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    after_dependent = _phase2_reviews(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
    )
    assert after_dependent["story_structure_review"]["state"] == "approved"
    assert (
        after_dependent["story_structure_review"]["latestDecisionId"]
        == initial_decision_ids["story_structure_review"]
    )
    assert after_dependent["dialogue_attribution_review"]["state"] == ("invalidated")
    assert (
        after_dependent["dialogue_attribution_review"]["latestDecisionId"]
        == initial_decision_ids["dialogue_attribution_review"]
    )
    for gate_id, decision_id in reapproved_decision_ids.items():
        assert after_dependent[gate_id]["state"] == "invalidated"
        assert after_dependent[gate_id]["latestDecisionId"] == decision_id
