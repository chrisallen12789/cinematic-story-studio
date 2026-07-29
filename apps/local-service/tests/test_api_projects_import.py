from __future__ import annotations

import asyncio
import codecs
import concurrent.futures
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.app import _BodyLimitExceeded, _CappedReceive
from cinematic_story_service.models import ImportedStoryRow, SourceDocumentRow

from .conftest import SYNTHETIC_BYTES, SYNTHETIC_STORY, TOKEN, create_imported_project


def test_every_api_route_requires_bearer_and_health_is_no_store(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    missing = client.get("/api/v1/health")
    wrong = client.get(
        "/api/v1/health",
        headers={"Authorization": "Bearer definitely-wrong"},
    )
    correct = client.get("/api/v1/health", headers=auth_headers)

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert "correlationId" in missing.json()["error"]
    assert missing.headers["cache-control"] == "no-store"
    assert correct.status_code == 200
    assert correct.json()["status"] == "ready"
    assert correct.json()["database"]["status"] == "ready"
    assert correct.headers["cache-control"] == "no-store"
    assert correct.headers["x-correlation-id"] == correct.json()["correlationId"]


def test_non_loopback_host_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/health",
        headers={**auth_headers, "Host": "example.test"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_HOST"


def test_project_creation_is_idempotent_and_payload_conflicts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    headers = {**auth_headers, "Idempotency-Key": "same-create"}
    first = client.post("/api/v1/projects", headers=headers, json={"name": "Synthetic Demo"})
    second = client.post("/api/v1/projects", headers=headers, json={"name": "Synthetic Demo"})
    conflict = client.post("/api/v1/projects", headers=headers, json={"name": "Different"})
    listed = client.get("/api/v1/projects", headers=auth_headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["project"]["projectId"] == second.json()["project"]["projectId"]
    assert first.json()["project"]["name"] == "Synthetic Demo"
    assert first.json()["project"]["status"] == "draft"
    assert first.json()["project"]["revision"] == 1
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(listed.json()["items"]) == 1


def test_write_models_reject_unknown_fields(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Synthetic Demo", "unexpected": True},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "unexpected" not in response.text


def test_markdown_import_preserves_exact_bytes_text_and_relative_path(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    imported = create_imported_project(client, auth_headers)
    project_id = imported["project"]["projectId"]
    headers = {**auth_headers, "Idempotency-Key": "import-story-key"}
    repeated = client.post(
        f"/api/v1/projects/{project_id}/imports",
        headers=headers,
        data={"declaredFormat": "markdown"},
        files={"file": ("sample-story.md", SYNTHETIC_BYTES, "text/markdown")},
    )

    assert repeated.status_code == 200
    assert repeated.json()["sourceDocument"]["documentId"] == imported["source"]["documentId"]
    assert repeated.json()["story"]["storyId"] == imported["story"]["storyId"]
    assert imported["source"]["contentSha256"] == hashlib.sha256(SYNTHETIC_BYTES).hexdigest()
    assert (
        imported["source"]["textSha256"]
        == hashlib.sha256(SYNTHETIC_STORY.encode("utf-8")).hexdigest()
    )
    assert imported["story"]["text"] == SYNTHETIC_STORY
    assert imported["story"]["originalTextPreserved"] is True
    storage_key = imported["source"]["storageKey"]
    assert not Path(storage_key).is_absolute()
    settings = app.state.settings
    assert (settings.data_dir / storage_key).read_bytes() == SYNTHETIC_BYTES
    with app.state.database.session() as session:
        assert session.query(SourceDocumentRow).count() == 1
        assert session.query(ImportedStoryRow).count() == 1


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "code"),
    [
        ("story.exe", b"MZsynthetic", "application/octet-stream", "UNSUPPORTED_IMPORT_FORMAT"),
        ("story.md", b"MZsynthetic", "text/markdown", "UNSAFE_FILE_SIGNATURE"),
        ("story.md", b"\xff\xfe\xff", "text/markdown", "SOURCE_DECODE_FAILED"),
        ("story.md", b"safe\x00binary", "text/markdown", "UNSAFE_TEXT_CONTENT"),
        ("story.pdf", b"%PDF-synthetic", "application/pdf", "UNSUPPORTED_IMPORT_FORMAT"),
    ],
)
def test_unsafe_or_unsupported_imports_leave_no_records_or_staging_files(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    filename: str,
    content: bytes,
    content_type: str,
    code: str,
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Import Rejections"},
    ).json()["project"]
    response = client.post(
        f"/api/v1/projects/{project['projectId']}/imports",
        headers=auth_headers,
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code in {400, 415}
    assert response.json()["error"]["code"] == code
    with app.state.database.session() as session:
        assert session.query(SourceDocumentRow).count() == 0
        assert session.query(ImportedStoryRow).count() == 0
    project_root = app.state.settings.data_dir / "projects" / project["projectId"]
    staging = project_root / "staging"
    assert not staging.exists() or not any(path.is_file() for path in staging.rglob("*"))
    assert client.get("/api/v1/health", headers=auth_headers).status_code == 200


def test_oversized_import_is_bounded_and_clean(tmp_path: Path) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "data",
        bearer_token=TOKEN,
        max_import_bytes=32,
        worker_enabled=False,
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", headers=headers, json={"name": "Bounded"}).json()[
            "project"
        ]
        response = client.post(
            f"/api/v1/projects/{project['projectId']}/imports",
            headers=headers,
            files={"file": ("large.txt", b"x" * 33, "text/plain")},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "IMPORT_TOO_LARGE"
        with app.state.database.session() as session:
            assert session.query(SourceDocumentRow).count() == 0
        staging = settings.data_dir / "projects" / project["projectId"] / "staging"
        assert not staging.exists() or not any(path.is_file() for path in staging.rglob("*"))


def test_http_body_cap_counts_streamed_chunks_before_multipart_spooling(
    tmp_path: Path,
) -> None:
    messages = iter(
        [
            {"type": "http.request", "body": b"a" * 8, "more_body": True},
            {"type": "http.request", "body": b"b" * 8, "more_body": False},
        ]
    )

    async def receive() -> dict[str, object]:
        return next(messages)

    capped = _CappedReceive(receive, limit=12)  # type: ignore[arg-type]

    async def exercise() -> None:
        assert (await capped())["type"] == "http.request"
        with pytest.raises(_BodyLimitExceeded):
            await capped()

    asyncio.run(exercise())

    settings = ServiceSettings(
        data_dir=tmp_path / "body-cap-data",
        bearer_token=TOKEN,
        max_import_bytes=32,
        worker_enabled=False,
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects", headers=headers, json={"name": "Body Cap"}
        ).json()["project"]
        response = client.post(
            f"/api/v1/projects/{project['projectId']}/imports",
            headers=headers,
            files={"file": ("large.txt", b"x" * (70 * 1024), "text/plain")},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "IMPORT_TOO_LARGE"
        assert not (settings.data_dir / "projects" / project["projectId"] / "staging").exists()


def test_bom_marked_utf16_import_preserves_raw_bytes_and_exact_decoded_text(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    text = '# UTF-16\r\n\r\nMara: "Exact text."\r\n'
    raw = codecs.BOM_UTF16_LE + text.encode("utf-16-le")
    imported = create_imported_project(
        client,
        auth_headers,
        story_bytes=raw,
        create_key="utf16-project",
        import_key="utf16-import",
    )
    assert imported["source"]["contentSha256"] == hashlib.sha256(raw).hexdigest()
    assert imported["source"]["encoding"] == "utf-16"
    assert imported["source"]["newlineStyle"] == "crlf"
    assert imported["story"]["text"] == text


def test_concurrent_same_content_import_never_deletes_referenced_bytes(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Concurrent Import"},
    ).json()["project"]

    def submit(key: str) -> tuple[int, dict[str, object]]:
        response = client.post(
            f"/api/v1/projects/{project['projectId']}/imports",
            headers={**auth_headers, "Idempotency-Key": key},
            data={"declaredFormat": "markdown"},
            files={"file": ("same.md", SYNTHETIC_BYTES, "text/markdown")},
        )
        return response.status_code, response.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            future.result(timeout=5)
            for future in (pool.submit(submit, "race-a"), pool.submit(submit, "race-b"))
        ]
    assert any(status == 200 for status, _body in outcomes)
    detail = client.get(f"/api/v1/projects/{project['projectId']}", headers=auth_headers).json()
    assert len(detail["sourceDocuments"]) == 1
    source = detail["sourceDocuments"][0]
    final_path = app.state.settings.data_dir / source["storageKey"]
    assert final_path.read_bytes() == SYNTHETIC_BYTES
    assert source["contentSha256"] == hashlib.sha256(final_path.read_bytes()).hexdigest()


def test_existing_content_addressed_source_is_verified_before_reuse(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    imported = create_imported_project(client, auth_headers)
    final_path = app.state.settings.data_dir / imported["source"]["storageKey"]
    final_path.write_bytes(b"tampered synthetic bytes")
    response = client.post(
        f"/api/v1/projects/{imported['project']['projectId']}/imports",
        headers={**auth_headers, "Idempotency-Key": "tamper-retry"},
        data={"declaredFormat": "markdown"},
        files={"file": ("sample-story.md", SYNTHETIC_BYTES, "text/markdown")},
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "SOURCE_STORAGE_CONFLICT"


@pytest.mark.parametrize(
    "filename",
    ["../escape.md", "..\\escape.md", "C:\\escape.md", "/absolute.md", "story;touch.md"],
)
def test_import_filename_cannot_escape_managed_root(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    filename: str,
) -> None:
    project = client.post(
        "/api/v1/projects", headers=auth_headers, json={"name": "Path Safety"}
    ).json()["project"]
    response = client.post(
        f"/api/v1/projects/{project['projectId']}/imports",
        headers=auth_headers,
        files={"file": (filename, SYNTHETIC_BYTES, "text/markdown")},
    )
    if response.status_code == 200:
        # Some multipart clients strip a Windows drive prefix before serialization. That is an
        # acceptable safe canonicalization as long as the persisted key remains managed/relative.
        storage_key = response.json()["sourceDocument"]["storageKey"]
        assert not Path(storage_key).is_absolute()
        assert ".." not in Path(storage_key).parts
    else:
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UNSAFE_SOURCE_NAME"
    assert not (app.state.settings.data_dir.parent / "escape.md").exists()


def test_multipart_rejects_unknown_fields_and_format_mismatch(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    project = client.post(
        "/api/v1/projects", headers=auth_headers, json={"name": "Multipart"}
    ).json()["project"]
    unknown = client.post(
        f"/api/v1/projects/{project['projectId']}/imports",
        headers=auth_headers,
        data={"surprise": "value"},
        files={"file": ("story.md", SYNTHETIC_BYTES, "text/markdown")},
    )
    mismatch = client.post(
        f"/api/v1/projects/{project['projectId']}/imports",
        headers=auth_headers,
        data={"declaredFormat": "txt"},
        files={"file": ("story.md", SYNTHETIC_BYTES, "text/markdown")},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "INVALID_REQUEST"
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "IMPORT_FORMAT_MISMATCH"
