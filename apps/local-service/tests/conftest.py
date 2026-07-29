from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app

TOKEN = "ab" * 32
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
    imported = client.post(
        f"/api/v1/projects/{project['projectId']}/imports",
        headers={**auth_headers, "Idempotency-Key": import_key},
        data={"declaredFormat": "markdown"},
        files={"file": ("sample-story.md", story_bytes, "text/markdown")},
    )
    assert imported.status_code == 200, imported.text
    return {
        "project": project,
        "source": imported.json()["sourceDocument"],
        "story": imported.json()["story"],
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
    timeout: float = 5,
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
