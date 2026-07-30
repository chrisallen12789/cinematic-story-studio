from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service import jobs as jobs_module
from cinematic_story_service import whole_book_analysis as analysis_module
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.util import canonical_json, sha256_text
from cinematic_story_service.whole_book_analysis import (
    AnalysisCancelled,
    analyze_whole_book,
)
from tests.conftest import TOKEN, create_imported_project, wait_for_job
from tests.test_phase2_api import (
    create_phase2_run,
    queue_phase2_run,
)

_OVERSIZED_JSON = b'{"padding":"' + (b"x" * (64 * 1024)) + b'"}'
_PHASE_2_MUTATION_PATHS = (
    "/api/v1/projects/project-1/analysis-runs",
    "/api/v1/projects/project-1/analysis-runs/run-1/corrections",
    ("/api/v1/projects/project-1/analysis-runs/run-1/reviews/story_structure_review/decisions"),
)


def _chunked_body() -> Iterator[bytes]:
    yield _OVERSIZED_JSON[:32_768]
    yield _OVERSIZED_JSON[32_768:]


def _large_story(*, scene_count: int = 200) -> str:
    parts = ["# Chapter One: Scale"]
    for scene in range(scene_count):
        parts.append(f"## Scene {scene}: Relay {scene}")
        parts.append(" ".join(["signal"] * 440) + ".")
        for line in range(10):
            speaker = "Mara" if line % 2 == 0 else "Ivo"
            parts.append(f'{speaker}: "Hold relay {scene} line {line}."')
    return "\n\n".join(parts)


def _many_character_story(character_count: int) -> str:
    def letters(value: int) -> str:
        result = ""
        remaining = value
        while True:
            result = chr(ord("a") + remaining % 26) + result
            remaining = remaining // 26 - 1
            if remaining < 0:
                return result

    lines = ["# Chapter One: Registry Scale", "## Scene One: Assembly"]
    for ordinal in range(character_count):
        suffix = letters(ordinal)
        lines.append(f'Name{suffix} Person{suffix}: "Go now."')
    return "\n\n".join(lines)


@pytest.mark.parametrize("path", _PHASE_2_MUTATION_PATHS)
def test_phase2_mutations_reject_oversized_content_length_bodies(
    client: TestClient,
    auth_headers: dict[str, str],
    path: str,
) -> None:
    response = client.post(
        path,
        headers={**auth_headers, "Content-Type": "application/json"},
        content=_OVERSIZED_JSON,
    )

    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


@pytest.mark.parametrize("path", _PHASE_2_MUTATION_PATHS)
def test_phase2_mutations_reject_chunked_bodies_without_content_length(
    client: TestClient,
    auth_headers: dict[str, str],
    path: str,
) -> None:
    response = client.post(
        path,
        headers={
            **auth_headers,
            "Content-Type": "application/json",
            "Transfer-Encoding": "chunked",
        },
        content=_chunked_body(),
    )

    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


def test_phase2_body_cap_is_scoped_away_from_non_json_requests(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        _PHASE_2_MUTATION_PATHS[0],
        headers={**auth_headers, "Content-Type": "text/plain"},
        content=_OVERSIZED_JSON,
    )

    assert response.status_code == 422


def test_100k_word_analysis_is_bounded_deterministic_and_near_linear() -> None:
    half_text = _large_story(scene_count=100)
    full_text = _large_story()
    assert 100_000 <= len(full_text.split()) <= 105_000

    half_started = time.perf_counter()
    analyze_whole_book(
        text=half_text,
        input_fingerprint=sha256_text(half_text),
        registry_scope="scale-project",
        story_scope="scale-half",
    )
    half_elapsed = time.perf_counter() - half_started

    full_started = time.perf_counter()
    first = analyze_whole_book(
        text=full_text,
        input_fingerprint=sha256_text(full_text),
        registry_scope="scale-project",
        story_scope="scale-full",
    )
    full_elapsed = time.perf_counter() - full_started
    second = analyze_whole_book(
        text=full_text,
        input_fingerprint=sha256_text(full_text),
        registry_scope="scale-project",
        story_scope="scale-full",
    )

    counts = first["summary"]["collectionCounts"]
    assert counts["scenes"] == 200
    assert counts["dialogue-lines"] == 2_000
    assert counts["mentions"] == 2_000
    assert counts["beats"] == 4_000
    assert first["outputFingerprint"] == second["outputFingerprint"]
    assert [value["entityId"] for value in first["collections"]["dialogue-lines"]] == [
        value["entityId"] for value in second["collections"]["dialogue-lines"]
    ]
    assert len(canonical_json(first).encode()) < 16 * 1024 * 1024
    assert full_elapsed < 20
    assert full_elapsed <= max(half_elapsed * 3.5, 5)


def test_100k_word_analysis_peak_rss_stays_below_320_mib() -> None:
    script = """
import ctypes
import json
import sys
from cinematic_story_service.util import sha256_text
from cinematic_story_service.whole_book_analysis import analyze_whole_book
from tests.test_phase2_scale import _large_story

text = _large_story()
result = analyze_whole_book(
    text=text,
    input_fingerprint=sha256_text(text),
    registry_scope="rss-scale-project",
    story_scope="rss-scale-story",
)
if sys.platform == "win32":
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    peak_rss_bytes = counters.PeakWorkingSetSize
else:
    import resource

    raw_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_bytes = raw_peak if sys.platform == "darwin" else raw_peak * 1024

print(json.dumps({
    "peakRssBytes": peak_rss_bytes,
    "wordCount": len(text.split()),
    "entityCount": sum(result["summary"]["collectionCounts"].values()),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    measurement = json.loads(completed.stdout)

    assert 100_000 <= measurement["wordCount"] <= 105_000
    assert measurement["entityCount"] == 13_003
    assert measurement["peakRssBytes"] < 320 * 1024 * 1024


def test_many_character_registry_paths_are_preindexed_and_near_linear() -> None:
    half_text = _many_character_story(300)
    full_text = _many_character_story(600)

    half_started = time.perf_counter()
    half = analyze_whole_book(
        text=half_text,
        input_fingerprint=sha256_text(half_text),
        registry_scope="many-character-scale",
        story_scope="many-character-half",
    )
    half_elapsed = time.perf_counter() - half_started

    full_started = time.perf_counter()
    full = analyze_whole_book(
        text=full_text,
        input_fingerprint=sha256_text(full_text),
        registry_scope="many-character-scale",
        story_scope="many-character-full",
    )
    full_elapsed = time.perf_counter() - full_started

    assert half["summary"]["collectionCounts"]["characters"] >= 250
    assert full["summary"]["collectionCounts"]["characters"] >= 550
    assert full["summary"]["collectionCounts"]["dialogue-lines"] >= 550
    assert full_elapsed < 10
    assert full_elapsed <= max(half_elapsed * 3.5, 3)


def test_large_analysis_enforces_entity_budget_and_cooperative_cancellation() -> None:
    text = _large_story()
    fingerprint = sha256_text(text)
    with pytest.raises(ServiceError) as over_budget:
        analyze_whole_book(
            text=text,
            input_fingerprint=fingerprint,
            maximum_entities=128,
            registry_scope="bounded-scale-project",
            story_scope="bounded-scale-story",
        )
    assert over_budget.value.code == "ANALYSIS_ENTITY_LIMIT_EXCEEDED"

    cancellation_checks = 0

    def should_cancel() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 3

    started = time.perf_counter()
    with pytest.raises(AnalysisCancelled):
        analyze_whole_book(
            text=text,
            input_fingerprint=fingerprint,
            should_cancel=should_cancel,
            registry_scope="cancelled-scale-project",
            story_scope="cancelled-scale-story",
        )
    assert cancellation_checks == 3
    assert time.perf_counter() - started < 5


def test_run_and_correction_keyset_pagination_is_complete_and_stable(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported, first_created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-keyset-scale-run-0",
    )
    project_id = imported["project"]["projectId"]
    first_run_id = first_created["run"]["runId"]
    first_run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}",
        headers=auth_headers,
    ).json()["run"]
    dialogue_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}/entities/dialogue-lines",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert dialogue_response.status_code == 200, dialogue_response.text
    dialogue_lines = dialogue_response.json()["items"]
    assert len(dialogue_lines) >= 3
    for index, dialogue_line in enumerate(dialogue_lines[:3]):
        response = client.post(
            f"/api/v1/projects/{project_id}/analysis-runs/{first_run_id}/corrections",
            headers=auth_headers,
            json={
                "category": "dialogue_speaker",
                "targetCollection": "dialogue-lines",
                "targetEntityId": dialogue_line["entityId"],
                "expectedTargetRevision": dialogue_line["effectiveRevision"],
                "expectedRunFingerprint": first_run["runFingerprint"],
                "previousValueFingerprint": dialogue_line["effectiveValueFingerprint"],
                "patch": {
                    "speakerCharacterId": None,
                    "selectedCandidateId": None,
                    "requiresHumanReview": True,
                },
                "reason": "Exercise correction keyset pagination.",
                "idempotencyKey": f"phase2-keyset-scale-correction-{index}",
            },
        )
        assert response.status_code == 200, response.text

    run_ids = [first_run_id]
    for index in range(1, 3):
        created = queue_phase2_run(
            client,
            auth_headers,
            imported=imported,
            idempotency_key=f"phase2-keyset-scale-run-{index}",
        )
        wait_for_job(
            client,
            auth_headers,
            created["job"]["jobId"],
            {"succeeded"},
            timeout=20,
        )
        run_ids.append(created["run"]["runId"])

    cursor: str | None = None
    paged_run_ids: list[str] = []
    while True:
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs",
            headers=auth_headers,
            params={
                "limit": 1,
                **({"cursor": cursor} if cursor is not None else {}),
            },
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["pageSize"] <= 1
        paged_run_ids.extend(value["runId"] for value in page["runs"])
        cursor = page.get("nextCursor")
        if cursor is None:
            assert page["total"] == 3
            break
    assert set(paged_run_ids) == set(run_ids)
    assert len(paged_run_ids) == len(set(paged_run_ids))

    cursor = None
    correction_ids: list[str] = []
    while True:
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_ids[-1]}/corrections",
            headers=auth_headers,
            params={
                "limit": 1,
                **({"cursor": cursor} if cursor is not None else {}),
            },
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["pageSize"] <= 1
        correction_ids.extend(value["correctionId"] for value in page["items"])
        cursor = page.get("nextCursor")
        if cursor is None:
            assert page["total"] == 3
            break
    assert len(correction_ids) == 3
    assert len(correction_ids) == len(set(correction_ids))


def test_corrected_structure_page_materializes_only_bounded_query_window(
    client: TestClient,
    auth_headers: dict[str, str],
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    story = "\n\n".join(
        [
            "# Chapter One: Projection Scale",
            *[f'## Scene {ordinal}: Relay\nMara: "Hold {ordinal}."' for ordinal in range(120)],
        ]
    ).encode()
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-corrected-page-window",
        story_bytes=story,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    run = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    ).json()["run"]
    scenes_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/scenes",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert scenes_response.status_code == 200, scenes_response.text
    scenes = scenes_response.json()["items"]
    assert len(scenes) == 120
    target = scenes[60]
    correction = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "structure_boundary",
            "targetCollection": "scenes",
            "targetEntityId": target["entityId"],
            "expectedTargetRevision": target["effectiveRevision"],
            "expectedRunFingerprint": run["runFingerprint"],
            "previousValueFingerprint": target["effectiveValueFingerprint"],
            "patch": {
                "operation": "remove",
                "parentEntityId": target["chapterId"],
                "ordinal": target["ordinal"],
                "sourceSpan": {
                    key: value
                    for key, value in target["sourceSpan"].items()
                    if key != "textSha256"
                },
                "boundaryKind": target["boundaryKind"],
            },
            "reason": "Exercise bounded corrected page projection.",
            "idempotencyKey": "phase2-corrected-page-window-remove",
        },
    )
    assert correction.status_code == 200, correction.text

    repository = app.state.story_intelligence
    original_entity_dict = repository._entity_dict
    materialized = 0

    def counted_entity_dict(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal materialized
        materialized += 1
        return original_entity_dict(*args, **kwargs)

    monkeypatch.setattr(repository, "_entity_dict", counted_entity_dict)
    page_response = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/scenes",
        headers=auth_headers,
        params={"limit": 1},
    )
    assert page_response.status_code == 200, page_response.text
    page = page_response.json()
    assert page["total"] == 119
    assert page["pageSize"] == 1
    assert materialized <= 4


def test_100k_checkpoint_recovers_after_process_restart(tmp_path: Path) -> None:
    data_dir = tmp_path / "phase2-100k-restart"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        imported = create_imported_project(
            first,
            headers,
            story_bytes=_large_story().encode(),
            create_key="phase2-100k-restart-project",
            import_key="phase2-100k-restart-import",
        )
        first_app.state.worker.controls.after_checkpoint_gate.clear()
        created = queue_phase2_run(
            first,
            headers,
            imported=imported,
            idempotency_key="phase2-100k-restart-run",
        )
        job_id = created["job"]["jobId"]
        run_id = created["run"]["runId"]
        deadline = time.monotonic() + 30
        checkpointed: dict[str, object] | None = None
        while time.monotonic() < deadline:
            response = first.get(
                f"/api/v1/jobs/{job_id}",
                headers=headers,
            )
            assert response.status_code == 200, response.text
            checkpointed = response.json()["job"]
            if checkpointed["checkpointAvailable"] and checkpointed["stage"] == "checkpointed":
                break
            time.sleep(0.02)
        assert checkpointed is not None
        assert checkpointed["checkpointAvailable"] is True
        assert checkpointed["state"] == "running"
        assert checkpointed["stage"] == "checkpointed"

    second_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(second_app) as second:
        interrupted = second.get(
            f"/api/v1/jobs/{job_id}",
            headers=headers,
        ).json()["job"]
        assert interrupted["state"] == "interrupted"
        resumed = second.post(
            f"/api/v1/jobs/{job_id}/resume",
            headers=headers,
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["job"]["attempt"] == 2
        terminal = wait_for_job(
            second,
            headers,
            job_id,
            {"succeeded"},
            timeout=45,
        )
        assert terminal["checkpointAvailable"] is True
        run = second.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
            headers=headers,
        ).json()["run"]
        assert run["status"] == "succeeded"
        assert run["currentSnapshot"]["counts"]["dialogueLines"] == 2_000


def test_structure_stage_checkpoint_is_reused_after_process_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "phase2-structure-stage-restart"
    settings = ServiceSettings(data_dir=data_dir, bearer_token=TOKEN)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    structure_runs = 0
    original_structural_spans = analysis_module._structural_spans

    def counted_structural_spans(
        text: str,
    ) -> tuple[list[analysis_module._Span], list[analysis_module._Span]]:
        nonlocal structure_runs
        structure_runs += 1
        return original_structural_spans(text)

    monkeypatch.setattr(
        analysis_module,
        "_structural_spans",
        counted_structural_spans,
    )

    first_app = create_app(settings)
    with TestClient(first_app) as first:
        imported = create_imported_project(
            first,
            headers,
            story_bytes=_large_story(scene_count=2).encode(),
            create_key="phase2-stage-restart-project",
            import_key="phase2-stage-restart-import",
        )
        first_app.state.worker.controls.after_agent_checkpoint_gate.clear()
        created = queue_phase2_run(
            first,
            headers,
            imported=imported,
            idempotency_key="phase2-stage-restart-run",
        )
        job_id = created["job"]["jobId"]
        run_id = created["run"]["runId"]
        deadline = time.monotonic() + 20
        stage_payload: dict[str, object] | None = None
        while time.monotonic() < deadline:
            with sqlite3.connect(settings.database_path) as connection:
                row = connection.execute(
                    "SELECT payload_json FROM analysis_stage_checkpoints "
                    "WHERE job_id = ? AND stage = 'analyze_structure' "
                    "ORDER BY attempt DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
            if row is not None:
                stage_payload = json.loads(row[0])
                break
            time.sleep(0.02)
        assert stage_payload is not None
        assert isinstance(stage_payload.get("resumeArtifact"), dict)
        assert structure_runs == 1

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        interrupted = second.get(
            f"/api/v1/jobs/{job_id}",
            headers=headers,
        ).json()["job"]
        assert interrupted["state"] == "interrupted"
        resumed = second.post(
            f"/api/v1/jobs/{job_id}/resume",
            headers=headers,
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["job"]["stage"] == "queued_for_resume"
        terminal = wait_for_job(
            second,
            headers,
            job_id,
            {"succeeded"},
            timeout=30,
        )
        assert terminal["attempt"] == 2
        assert structure_runs == 1
        run = second.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
            headers=headers,
        ).json()["run"]
        assert run["status"] == "succeeded"
        reviews = second.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}/reviews",
            headers=headers,
        ).json()["items"]
        structure_review = next(
            value for value in reviews if value["gateId"] == "story_structure_review"
        )
        decision_response = second.post(
            f"/api/v1/projects/{imported['project']['projectId']}"
            f"/analysis-runs/{run_id}/reviews/"
            "story_structure_review/decisions",
            headers=headers,
            json={
                "decision": "approve",
                "expectedRevision": structure_review["revision"],
                "expectedArtifactFingerprint": structure_review["artifactFingerprint"],
                "expectedEvidenceFingerprint": structure_review["evidenceFingerprint"],
                "acknowledgedWarningIds": structure_review["openWarningIds"],
                "rationale": "Persist the governed restart decision.",
                "idempotencyKey": "phase2-stage-restart-review",
            },
        )
        assert decision_response.status_code == 200, decision_response.text
        expected_decision = decision_response.json()["decision"]

    with sqlite3.connect(settings.database_path) as connection:
        structure_attempts = connection.execute(
            "SELECT attempt FROM analysis_stage_checkpoints "
            "WHERE job_id = ? AND stage = 'analyze_structure' "
            "ORDER BY attempt",
            (job_id,),
        ).fetchall()
    assert structure_attempts == [(1,)]

    third_app = create_app(settings)
    with TestClient(third_app) as third:
        restored_reviews = third.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}/reviews",
            headers=headers,
        )
        assert restored_reviews.status_code == 200, restored_reviews.text
        restored_structure = next(
            value
            for value in restored_reviews.json()["items"]
            if value["gateId"] == "story_structure_review"
        )
        assert restored_structure["latestDecision"] == expected_decision


def test_synthesis_checkpoint_resumes_without_rerunning_analyzer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "phase2-synthesis-stage-restart"
    settings = ServiceSettings(data_dir=data_dir, bearer_token=TOKEN)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    synthesis_boundary = threading.Event()
    first_app = create_app(settings)
    original_complete_agent_boundary = (
        first_app.state.story_intelligence.complete_agent_boundary
    )

    def stop_after_synthesis(
        *,
        job_id: str,
        role: str,
        payload: dict[str, object],
    ) -> bool:
        completed = original_complete_agent_boundary(
            job_id=job_id,
            role=role,
            payload=payload,
        )
        if completed and role == "synthesis":
            first_app.state.worker.controls.after_agent_checkpoint_gate.clear()
            synthesis_boundary.set()
        return completed

    monkeypatch.setattr(
        first_app.state.story_intelligence,
        "complete_agent_boundary",
        stop_after_synthesis,
    )
    with TestClient(first_app) as first:
        imported = create_imported_project(
            first,
            headers,
            story_bytes=_large_story(scene_count=2).encode(),
            create_key="phase2-synthesis-restart-project",
            import_key="phase2-synthesis-restart-import",
        )
        created = queue_phase2_run(
            first,
            headers,
            imported=imported,
            idempotency_key="phase2-synthesis-restart-run",
        )
        job_id = created["job"]["jobId"]
        run_id = created["run"]["runId"]
        assert synthesis_boundary.wait(timeout=20)
        checkpointed = first.get(
            f"/api/v1/jobs/{job_id}",
            headers=headers,
        ).json()["job"]
        assert checkpointed["state"] == "running"
        assert checkpointed["stage"] == "synthesize_analysis"
        assert checkpointed["checkpointAvailable"] is True
        with sqlite3.connect(settings.database_path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM job_checkpoints "
                "WHERE job_id = ? ORDER BY attempt DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        assert row is not None
        checkpoint_payload = json.loads(row[0])
        assert checkpoint_payload["outputFingerprint"]
        assert checkpoint_payload["inputFingerprint"] == created["run"]["inputFingerprint"]

    analyzer_runs = 0

    def analyzer_must_not_run(**_kwargs: object) -> dict[str, object]:
        nonlocal analyzer_runs
        analyzer_runs += 1
        raise AssertionError("A verified synthesis checkpoint must bypass the analyzer.")

    monkeypatch.setattr(jobs_module, "analyze_whole_book", analyzer_must_not_run)
    second_app = create_app(settings)
    with TestClient(second_app) as second:
        interrupted = second.get(
            f"/api/v1/jobs/{job_id}",
            headers=headers,
        ).json()["job"]
        assert interrupted["state"] == "interrupted"
        resumed = second.post(
            f"/api/v1/jobs/{job_id}/resume",
            headers=headers,
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["job"]["stage"] == "queued_for_resume"
        terminal = wait_for_job(
            second,
            headers,
            job_id,
            {"succeeded"},
            timeout=30,
        )
        assert terminal["attempt"] == 2
        assert analyzer_runs == 0
        with sqlite3.connect(settings.database_path) as connection:
            rerun_stage_count = connection.execute(
                "SELECT COUNT(*) FROM analysis_stage_checkpoints "
                "WHERE job_id = ? AND attempt = 2 AND stage LIKE 'analyze_%'",
                (job_id,),
            ).fetchone()
        assert rerun_stage_count == (0,)
        run = second.get(
            f"/api/v1/projects/{imported['project']['projectId']}/analysis-runs/{run_id}",
            headers=headers,
        ).json()["run"]
        assert run["status"] == "succeeded"


def test_entity_pagination_is_complete_stable_and_index_backed(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
) -> None:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key="phase2-pagination-scale",
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    cursor = None
    entity_ids: list[str] = []
    expected_total = None
    while True:
        response = client.get(
            f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/mentions",
            headers=auth_headers,
            params={
                "limit": 3,
                **({"cursor": cursor} if cursor is not None else {}),
            },
        )
        assert response.status_code == 200, response.text
        page = response.json()
        expected_total = page["total"]
        entity_ids.extend(item["entityId"] for item in page["items"])
        cursor = page.get("nextCursor")
        if cursor is None:
            break
    assert len(entity_ids) == expected_total
    assert len(entity_ids) == len(set(entity_ids))

    invalid_filter = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/mentions",
        headers=auth_headers,
        params={"speakerState": "unknown"},
    )
    assert invalid_filter.status_code == 422

    with sqlite3.connect(settings.database_path) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id FROM analysis_entities "
            "WHERE project_id = ? AND run_id = ? AND collection = ? AND ordinal > ? "
            "ORDER BY ordinal, id LIMIT 51",
            (project_id, run_id, "mentions", -1),
        ).fetchall()
    assert any("ix_analysis_entity_project_run_collection_order" in str(row) for row in plan)
