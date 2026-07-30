from __future__ import annotations

import base64
import concurrent.futures
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app

TOKEN = "ab" * 32
# A future may consume SQLite's five-second lock wait before returning its outcome.
CONCURRENT_DATABASE_FUTURE_TIMEOUT_SECONDS = 15.0
SYNTHETIC_STORY = (
    "# Chapter One: The Signal\r\n"
    "\r\n"
    "Rain traced silver lines across the observatory glass.\r\n"
    "\r\n"
    "---\r\n"
    "\r\n"
    "### Scene: The Observatory\r\n"
    "\r\n"
    'Mara: "We begin now."\r\n'
    "\r\n"
    'The old receiver clicked. "Then listen," Ivo said.\r\n'
    "\r\n"
    'A voice crossed the static: "Who waits beyond the storm?"\r\n'
)
SYNTHETIC_BYTES = SYNTHETIC_STORY.encode("utf-8")
SYNTHETIC_FIXTURES = Path(__file__).parents[3] / "fixtures" / "synthetic-story"

_FIXTURE_MEDIA_TYPES = {
    "txt": ("sample-story.txt", "text/plain"),
    "markdown": ("sample-story.md", "text/markdown"),
    "docx": (
        "sample-story.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "epub": ("sample-story.epub", "application/epub+zip"),
    "pdf": ("sample-story.pdf", "application/pdf"),
}


def collect_concurrent_database_results(
    futures: Sequence[concurrent.futures.Future[Any]],
) -> list[Any]:
    """Resolve database race-test futures without depending on submission order."""
    _completed, pending = concurrent.futures.wait(
        futures,
        timeout=CONCURRENT_DATABASE_FUTURE_TIMEOUT_SECONDS,
        return_when=concurrent.futures.ALL_COMPLETED,
    )
    if pending:
        for future in pending:
            future.cancel()
        pytest.fail(
            f"{len(pending)} of {len(futures)} concurrent database operations "
            f"exceeded {CONCURRENT_DATABASE_FUTURE_TIMEOUT_SECONDS:.0f} seconds",
            pytrace=False,
        )
    return [future.result() for future in futures]


@pytest.fixture
def settings(tmp_path: Path) -> ServiceSettings:
    return ServiceSettings(
        data_dir=tmp_path / "app-data",
        bearer_token=TOKEN,
        ffmpeg_executable=str(tmp_path / "missing-ffmpeg.exe"),
    )


@pytest.fixture
def app(settings: ServiceSettings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def synthetic_fixture(
    document_format: str,
) -> tuple[str, bytes, str]:
    filename, media_type = _FIXTURE_MEDIA_TYPES[document_format]
    if document_format in {"txt", "markdown"}:
        content = (SYNTHETIC_FIXTURES / filename).read_bytes()
    else:
        encoded = (SYNTHETIC_FIXTURES / f"{filename}.base64").read_text("ascii")
        content = base64.b64decode("".join(encoded.splitlines()), validate=True)
    return filename, content, media_type


def submit_import(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    filename: str,
    content: bytes,
    media_type: str,
    declared_format: str,
    idempotency_key: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/imports",
        headers={**auth_headers, "Idempotency-Key": idempotency_key},
        data={"declaredFormat": declared_format},
        files={"file": (filename, content, media_type)},
    )
    assert response.status_code == 202, response.text
    return response.json()


def wait_for_extraction(
    client: TestClient,
    auth_headers: dict[str, str],
    queued_import: dict[str, Any],
) -> dict[str, Any]:
    terminal = wait_for_job(
        client,
        auth_headers,
        queued_import["job"]["jobId"],
        {"succeeded", "failed", "cancelled", "interrupted"},
        timeout=20,
    )
    assert terminal["state"] == "succeeded", terminal
    return terminal


def review_for_extraction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    extraction_id: str,
) -> dict[str, Any]:
    detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    return next(
        value for value in detail.json()["importReviews"] if value["extractionId"] == extraction_id
    )


def decide_import_review(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    review: dict[str, Any],
    decision: str,
    idempotency_key: str,
    rationale: str | None = None,
    expected_revision: int | None = None,
    evidence_fingerprint: str | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "reviewId": review["reviewId"],
        "decision": decision,
        "expectedRevision": (
            review["revision"] if expected_revision is None else expected_revision
        ),
        "evidenceFingerprint": (
            review["evidenceFingerprint"] if evidence_fingerprint is None else evidence_fingerprint
        ),
        "idempotencyKey": idempotency_key,
    }
    if rationale is not None:
        payload["rationale"] = rationale
    return client.post(
        (f"/api/v1/projects/{project_id}/imports/{review['reviewId']}/review/decision"),
        headers=auth_headers,
        json=payload,
    )


def create_imported_project(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    story_bytes: bytes = SYNTHETIC_BYTES,
    create_key: str = "create-project-key",
    import_key: str = "import-story-key",
) -> dict[str, Any]:
    created = client.post(
        "/api/v1/projects",
        headers={**auth_headers, "Idempotency-Key": create_key},
        json={"name": "Synthetic Demo"},
    )
    assert created.status_code == 200, created.text
    project = created.json()["project"]
    queued = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="sample-story.md",
        content=story_bytes,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key=import_key,
    )
    extraction_job = wait_for_extraction(client, auth_headers, queued)
    before_review = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    )
    assert before_review.status_code == 200, before_review.text
    pending_detail = before_review.json()
    assert pending_detail["analysisAllowed"] is False
    review = review_for_extraction(
        client,
        auth_headers,
        project_id=project["projectId"],
        extraction_id=queued["extraction"]["extractionId"],
    )
    approved = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=review,
        decision="approved",
        idempotency_key=f"{import_key}-approval",
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["analysisAllowed"] is True
    detail_response = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    story = detail["story"]
    assert story is not None
    source = next(
        value
        for value in detail["sourceDocuments"]
        if value["documentId"] == queued["sourceDocument"]["documentId"]
    )
    return {
        "project": detail["project"],
        "source": source,
        "story": story,
        "extraction": next(
            value
            for value in detail["extractions"]
            if value["extractionId"] == queued["extraction"]["extractionId"]
        ),
        "review": next(
            value for value in detail["importReviews"] if value["reviewId"] == review["reviewId"]
        ),
        "extractionJob": extraction_job,
    }


def create_analysis_job(
    client: TestClient,
    auth_headers: dict[str, str],
    project_id: str,
    input_revision: int,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        headers=auth_headers,
        json={
            "type": "analyze_story",
            "inputRevision": input_revision,
            "idempotencyKey": idempotency_key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["job"]


def wait_for_job(
    client: TestClient,
    auth_headers: dict[str, str],
    job_id: str,
    states: set[str],
    *,
    timeout: float = 20,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200, response.text
        last = response.json()["job"]
        if last["state"] in states:
            return last
        time.sleep(0.01)
    pytest.fail(f"Job did not enter {states}; last state was {last}")
