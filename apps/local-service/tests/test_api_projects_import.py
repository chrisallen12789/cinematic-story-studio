from __future__ import annotations

import asyncio
import codecs
import concurrent.futures
import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.app import _BodyLimitExceeded, _CappedReceive
from cinematic_story_service.models import (
    DocumentExtractionRow,
    IdempotencyRow,
    ImportedStoryRow,
    ImportReviewRow,
    JobRow,
    SourceDocumentRow,
)
from cinematic_story_service.projects import StoryImportService

from .conftest import (
    SYNTHETIC_BYTES,
    SYNTHETIC_STORY,
    TOKEN,
    create_analysis_job,
    create_imported_project,
    decide_import_review,
    review_for_extraction,
    submit_import,
    synthetic_fixture,
    wait_for_extraction,
    wait_for_job,
)


def _spool_observation(upload: Any) -> tuple[bool, Path]:
    spool = upload.file
    return spool._rolled, Path(spool._file.name).resolve()


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

    assert repeated.status_code == 202
    assert repeated.json()["sourceDocument"]["documentId"] == imported["source"]["documentId"]
    assert repeated.json()["extraction"]["extractionId"] == imported["extraction"]["extractionId"]
    assert repeated.json()["job"]["jobId"] == imported["extractionJob"]["jobId"]
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
        assert session.query(DocumentExtractionRow).count() == 1
        assert session.query(ImportReviewRow).count() == 2


@pytest.mark.parametrize(
    "document_format",
    ["txt", "markdown", "docx", "epub", "pdf"],
)
def test_all_supported_formats_archive_exact_bytes_then_require_import_review(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    document_format: str,
) -> None:
    filename, content, media_type = synthetic_fixture(document_format)
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": f"Synthetic {document_format}"},
    ).json()["project"]
    queued = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename=filename,
        content=content,
        media_type=media_type,
        declared_format=document_format,
        idempotency_key=f"all-formats-{document_format}",
    )

    assert queued["sourceDocument"]["contentSha256"] == hashlib.sha256(content).hexdigest()
    assert queued["sourceDocument"]["byteLength"] == len(content)
    assert queued["sourceDocument"]["declaredFormat"] == document_format
    assert queued["sourceDocument"]["originalTextPreserved"] is True
    assert queued["sourceDocument"]["originalBytesPreserved"] is True
    assert queued["sourceDocument"]["sourceRevision"] == 1
    assert queued["sourceDocument"]["extractionStatus"] == "pending"
    assert queued["extraction"]["status"] == "pending"
    assert queued["job"]["type"] == "extract_document"
    assert queued["job"]["target"] == {
        "type": "document_extraction",
        "id": queued["extraction"]["extractionId"],
    }
    wait_for_extraction(client, auth_headers, queued)

    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    source = detail["sourceDocuments"][0]
    extraction = detail["extractions"][0]
    review = detail["importReviews"][0]
    stored_path = app.state.settings.data_dir / source["storageKey"]
    assert not Path(source["storageKey"]).is_absolute()
    assert stored_path.read_bytes() == content
    assert hashlib.sha256(stored_path.read_bytes()).hexdigest() == source["contentSha256"]
    assert source["extractionStatus"] in {"complete", "partial"}
    assert extraction["status"] in {"complete", "partial"}
    assert extraction["sourceSha256"] == source["contentSha256"]
    assert extraction["extractedTextSha256"]
    assert extraction["extractedCharacterCount"] > 0
    assert review["state"] == "pending"
    assert review["extractionId"] == extraction["extractionId"]
    assert review["previewText"]
    assert detail["analysisAllowed"] is False
    assert detail["story"] is None
    if document_format == "pdf":
        assert extraction["pageCount"] == 2
    with app.state.database.session() as session:
        row = session.get(DocumentExtractionRow, extraction["extractionId"])
        assert row is not None
        assert row.exact_text
        if document_format in {"txt", "markdown"}:
            assert row.exact_text == content.decode("utf-8")


def test_malformed_pdf_fails_persisted_job_without_publishing_review_or_story(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Malformed PDF"},
    ).json()["project"]
    queued = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="malformed.pdf",
        content=b"%PDF-synthetic",
        media_type="application/pdf",
        declared_format="pdf",
        idempotency_key="malformed-pdf",
    )
    terminal = wait_for_job(
        client,
        auth_headers,
        queued["job"]["jobId"],
        {"failed"},
    )

    assert terminal["error"]["code"] == "PDF_MALFORMED"
    assert terminal["error"]["message"] == ("Document extraction could not be completed safely.")
    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    assert detail["story"] is None
    assert detail["importReviews"] == []
    assert detail["analysisAllowed"] is False
    assert detail["extractions"][0]["status"] == "failed"
    assert detail["sourceDocuments"][0]["extractionStatus"] == "failed"
    assert str(app.state.settings.data_dir) not in terminal["error"]["message"]
    reextract = client.post(
        (
            f"/api/v1/projects/{project['projectId']}/imports/"
            f"{queued['sourceDocument']['documentId']}/reextract"
        ),
        headers={**auth_headers, "Idempotency-Key": "malformed-pdf-reextract"},
    )
    assert reextract.status_code == 202
    assert reextract.json()["extraction"]["revision"] == 2
    wait_for_job(
        client,
        auth_headers,
        reextract.json()["job"]["jobId"],
        {"failed"},
    )


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "code"),
    [
        ("story.exe", b"MZsynthetic", "application/octet-stream", "UNSUPPORTED_IMPORT_FORMAT"),
        ("story.md", b"MZsynthetic", "text/markdown", "UNSAFE_FILE_SIGNATURE"),
        ("story.md", b"\xff\xfe\xff", "text/markdown", "SOURCE_DECODE_FAILED"),
        ("story.md", b"safe\x00binary", "text/markdown", "UNSAFE_TEXT_CONTENT"),
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


def test_large_multipart_upload_rolls_over_only_in_private_application_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "private-spool-data",
        bearer_token=TOKEN,
        max_import_bytes=2 * 1024 * 1024,
        worker_enabled=False,
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    observed: dict[str, object] = {}
    original_import_upload = StoryImportService.import_upload

    async def observe_import_upload(
        service: StoryImportService,
        **kwargs: object,
    ) -> object:
        observed["rolled"], observed["path"] = _spool_observation(kwargs["upload"])
        return await original_import_upload(service, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(StoryImportService, "import_upload", observe_import_upload)
    source = b"# Private spool\n\n" + (b"synthetic local text\n" * 64 * 1024)
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Private multipart spool"},
        ).json()["project"]
        response = client.post(
            f"/api/v1/projects/{project['projectId']}/imports",
            headers=headers,
            files={"file": ("large.md", source, "text/markdown")},
        )

    private_root = (settings.data_dir / "multipart-staging").resolve()
    spool_path = observed["path"]
    assert response.status_code == 202
    assert observed["rolled"] is True
    assert isinstance(spool_path, Path)
    assert spool_path.is_relative_to(private_root)
    assert not spool_path.exists()
    assert not any(private_root.iterdir())


def test_restart_removes_only_recognized_abandoned_import_staging(
    tmp_path: Path,
) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "restart-staging-data",
        bearer_token=TOKEN,
        worker_enabled=False,
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(settings)
    with TestClient(first_app) as client:
        project = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Staging recovery"},
        ).json()["project"]

    staging_root = settings.data_dir / "projects" / project["projectId"] / "staging"
    abandoned = staging_root / "00000000-0000-4000-8000-000000000001"
    abandoned.mkdir(parents=True)
    (abandoned / "source.upload").write_bytes(b"abandoned synthetic upload")
    unknown = staging_root / "00000000-0000-4000-8000-000000000002"
    unknown.mkdir()
    (unknown / "source.upload").write_bytes(b"unknown synthetic upload")
    (unknown / "unexpected.bin").write_bytes(b"leave untouched")

    restarted_app = create_app(settings)
    assert not abandoned.exists()
    assert (unknown / "source.upload").read_bytes() == b"unknown synthetic upload"
    assert (unknown / "unexpected.bin").read_bytes() == b"leave untouched"
    with TestClient(restarted_app) as client:
        assert client.get("/api/v1/health", headers=headers).status_code == 200


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
    assert all(status == 202 for status, _body in outcomes)
    source_ids = {
        str(body["sourceDocument"]["documentId"])
        for _status, body in outcomes
        if isinstance(body.get("sourceDocument"), dict)
    }
    extraction_ids = {
        str(body["extraction"]["extractionId"])
        for _status, body in outcomes
        if isinstance(body.get("extraction"), dict)
    }
    job_ids = {
        str(body["job"]["jobId"]) for _status, body in outcomes if isinstance(body.get("job"), dict)
    }
    assert len(source_ids) == len(extraction_ids) == len(job_ids) == 1
    detail = client.get(f"/api/v1/projects/{project['projectId']}", headers=auth_headers).json()
    assert len(detail["sourceDocuments"]) == 1
    assert len(detail["extractions"]) == 1
    source = detail["sourceDocuments"][0]
    final_path = app.state.settings.data_dir / source["storageKey"]
    assert final_path.read_bytes() == SYNTHETIC_BYTES
    assert source["contentSha256"] == hashlib.sha256(final_path.read_bytes()).hexdigest()


def test_content_reimport_a_b_a_appends_revision_then_current_duplicate_reuses_it(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Source Revisions"},
    ).json()["project"]
    first = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="revision.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="source-revision-one",
    )
    wait_for_extraction(client, auth_headers, first)
    duplicate = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="renamed.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="source-revision-duplicate",
    )
    changed_bytes = SYNTHETIC_BYTES + b"\r\nA changed synthetic ending.\r\n"
    changed = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="revision.md",
        content=changed_bytes,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="source-revision-two",
    )
    wait_for_extraction(client, auth_headers, changed)
    restored = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="restored.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="source-revision-three",
    )
    wait_for_extraction(client, auth_headers, restored)
    current_duplicate = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="restored-renamed.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="source-revision-current-duplicate",
    )

    assert duplicate["sourceDocument"]["documentId"] == first["sourceDocument"]["documentId"]
    assert duplicate["extraction"]["extractionId"] == first["extraction"]["extractionId"]
    assert duplicate["job"]["jobId"] == first["job"]["jobId"]
    assert changed["sourceDocument"]["documentId"] != first["sourceDocument"]["documentId"]
    assert changed["sourceDocument"]["sourceRevision"] == 2
    assert (
        changed["sourceDocument"]["supersedesDocumentId"] == first["sourceDocument"]["documentId"]
    )
    assert restored["sourceDocument"]["documentId"] not in {
        first["sourceDocument"]["documentId"],
        changed["sourceDocument"]["documentId"],
    }
    assert restored["sourceDocument"]["sourceRevision"] == 3
    assert (
        restored["sourceDocument"]["supersedesDocumentId"]
        == changed["sourceDocument"]["documentId"]
    )
    assert restored["sourceDocument"]["storageKey"] == first["sourceDocument"]["storageKey"]
    assert restored["extraction"]["extractionId"] != first["extraction"]["extractionId"]
    assert restored["job"]["jobId"] != first["job"]["jobId"]
    assert (
        current_duplicate["sourceDocument"]["documentId"]
        == restored["sourceDocument"]["documentId"]
    )
    assert current_duplicate["extraction"]["extractionId"] == restored["extraction"]["extractionId"]
    assert current_duplicate["job"]["jobId"] == restored["job"]["jobId"]
    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    assert [source["sourceRevision"] for source in detail["sourceDocuments"]] == [1, 2, 3]
    assert len(detail["extractions"]) == 3
    assert len(detail["importReviews"]) == 3
    for source, expected in zip(
        detail["sourceDocuments"],
        [SYNTHETIC_BYTES, changed_bytes, SYNTHETIC_BYTES],
        strict=True,
    ):
        assert (app.state.settings.data_dir / source["storageKey"]).read_bytes() == expected


@pytest.mark.parametrize(
    "simulate_legacy_record",
    [False, True],
    ids=["exact-ledger", "legacy-ledger"],
)
def test_import_replay_after_reextraction_returns_original_exact_resources(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
    simulate_legacy_record: bool,
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Exact Import Replay"},
    ).json()["project"]
    project_id = project["projectId"]
    import_key = "exact-import-replay"
    original = submit_import(
        client,
        auth_headers,
        project_id=project_id,
        filename="exact-replay.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key=import_key,
    )
    wait_for_extraction(client, auth_headers, original)

    extraction_scope = f"import_extraction:{project_id}"
    if simulate_legacy_record:
        with app.state.database.session() as session:
            exact_record = session.get(
                IdempotencyRow,
                {"scope": extraction_scope, "key": import_key},
            )
            assert exact_record is not None
            session.delete(exact_record)

    reextracted_response = client.post(
        (
            f"/api/v1/projects/{project_id}/imports/"
            f"{original['sourceDocument']['documentId']}/reextract"
        ),
        headers={**auth_headers, "Idempotency-Key": "exact-replay-reextract"},
    )
    assert reextracted_response.status_code == 202, reextracted_response.text
    reextracted = reextracted_response.json()
    wait_for_extraction(client, auth_headers, reextracted)
    assert reextracted["extraction"]["extractionId"] != original["extraction"]["extractionId"]

    replayed = submit_import(
        client,
        auth_headers,
        project_id=project_id,
        filename="exact-replay.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key=import_key,
    )

    assert replayed["sourceDocument"]["documentId"] == original["sourceDocument"]["documentId"]
    assert replayed["extraction"]["extractionId"] == original["extraction"]["extractionId"]
    assert replayed["job"]["jobId"] == original["job"]["jobId"]
    detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    assert len(detail["sourceDocuments"]) == 1
    assert len(detail["extractions"]) == 2
    assert len(detail["importReviews"]) == 2
    with app.state.database.session() as session:
        exact_record = session.get(
            IdempotencyRow,
            {"scope": extraction_scope, "key": import_key},
        )
        assert exact_record is not None
        assert exact_record.resource_id == original["extraction"]["extractionId"]
        assert session.query(JobRow).count() == 2


def test_import_idempotency_conflict_removes_new_unreferenced_source_bytes(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Archive Cleanup"},
    ).json()["project"]
    first = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="first.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="archive-cleanup-key",
    )
    wait_for_extraction(client, auth_headers, first)

    conflict = client.post(
        f"/api/v1/projects/{project['projectId']}/imports",
        headers={
            **auth_headers,
            "Idempotency-Key": "archive-cleanup-key",
        },
        data={"declaredFormat": "markdown"},
        files={
            "file": (
                "changed.md",
                SYNTHETIC_BYTES + b"\nConflicting source bytes.\n",
                "text/markdown",
            )
        },
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    assert len(detail["sourceDocuments"]) == 1
    sources_root = app.state.settings.data_dir / "projects" / project["projectId"] / "sources"
    archived_files = list(sources_root.glob("*.source"))
    assert len(archived_files) == 1
    assert archived_files[0].read_bytes() == SYNTHETIC_BYTES


def test_changed_source_blocks_prior_approval_until_current_review(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="changed-source-gate-project",
        import_key="changed-source-gate-first",
    )
    project_id = imported["project"]["projectId"]
    changed = submit_import(
        client,
        auth_headers,
        project_id=project_id,
        filename="changed-source.md",
        content=SYNTHETIC_BYTES + b"\nChanged source revision.\n",
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="changed-source-gate-second",
    )
    wait_for_extraction(client, auth_headers, changed)
    detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    assert detail["story"]["storyId"] == imported["story"]["storyId"]
    assert detail["analysisAllowed"] is False
    blocked = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        headers=auth_headers,
        json={
            "type": "analyze_story",
            "inputRevision": detail["story"]["revision"],
            "idempotencyKey": "changed-source-analysis-blocked",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "IMPORT_APPROVAL_REQUIRED"


def test_changed_source_rejects_a_stale_pending_import_review(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Stale source review"},
    ).json()["project"]
    first = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="first.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="stale-source-first",
    )
    wait_for_extraction(client, auth_headers, first)
    first_review = review_for_extraction(
        client,
        auth_headers,
        project_id=project["projectId"],
        extraction_id=first["extraction"]["extractionId"],
    )

    second = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="second.md",
        content=SYNTHETIC_BYTES + b"\nA changed source revision.\n",
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="stale-source-second",
    )
    wait_for_extraction(client, auth_headers, second)

    stale = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=first_review,
        decision="approved",
        idempotency_key="stale-source-old-approval",
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "IMPORT_REVIEW_STALE"

    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    saved_first_review = next(
        review
        for review in detail["importReviews"]
        if review["reviewId"] == first_review["reviewId"]
    )
    assert saved_first_review["state"] == "pending"
    assert saved_first_review["revision"] == 1
    assert detail["story"] is None
    assert detail["analysisAllowed"] is False


def test_reextraction_rejects_a_stale_pending_import_review(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Stale extraction review"},
    ).json()["project"]
    first = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="source.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="stale-extraction-first",
    )
    wait_for_extraction(client, auth_headers, first)
    first_review = review_for_extraction(
        client,
        auth_headers,
        project_id=project["projectId"],
        extraction_id=first["extraction"]["extractionId"],
    )

    reextracted_response = client.post(
        (
            f"/api/v1/projects/{project['projectId']}/imports/"
            f"{first['sourceDocument']['documentId']}/reextract"
        ),
        headers={
            **auth_headers,
            "Idempotency-Key": "stale-extraction-second",
        },
    )
    assert reextracted_response.status_code == 202, reextracted_response.text
    reextracted = reextracted_response.json()
    wait_for_extraction(client, auth_headers, reextracted)
    new_review = review_for_extraction(
        client,
        auth_headers,
        project_id=project["projectId"],
        extraction_id=reextracted["extraction"]["extractionId"],
    )

    stale = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=first_review,
        decision="approved",
        idempotency_key="stale-extraction-old-approval",
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "IMPORT_REVIEW_STALE"

    approved = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=new_review,
        decision="approved",
        idempotency_key="stale-extraction-new-approval",
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["analysisAllowed"] is True


def test_speaker_correction_rejects_a_character_from_a_prior_story_revision(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    first = create_imported_project(
        client,
        auth_headers,
        create_key="cross-story-character-project",
        import_key="cross-story-character-first",
    )
    project_id = first["project"]["projectId"]
    first_analysis = create_analysis_job(
        client,
        auth_headers,
        project_id,
        first["story"]["revision"],
        idempotency_key="cross-story-character-first-analysis",
    )
    wait_for_job(client, auth_headers, first_analysis["jobId"], {"succeeded"})
    first_detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    prior_character = first_detail["characters"][0]

    second = submit_import(
        client,
        auth_headers,
        project_id=project_id,
        filename="second-story.md",
        content=SYNTHETIC_BYTES + b"\nA distinct second story revision.\n",
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="cross-story-character-second",
    )
    wait_for_extraction(client, auth_headers, second)
    second_review = review_for_extraction(
        client,
        auth_headers,
        project_id=project_id,
        extraction_id=second["extraction"]["extractionId"],
    )
    approved = decide_import_review(
        client,
        auth_headers,
        project_id=project_id,
        review=second_review,
        decision="approved",
        idempotency_key="cross-story-character-second-approval",
    )
    assert approved.status_code == 200, approved.text
    approved_detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    second_analysis = create_analysis_job(
        client,
        auth_headers,
        project_id,
        approved_detail["story"]["revision"],
        idempotency_key="cross-story-character-second-analysis",
    )
    wait_for_job(client, auth_headers, second_analysis["jobId"], {"succeeded"})

    second_detail = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    line = second_detail["dialogueLines"][0]
    assert all(
        character["characterId"] != prior_character["characterId"]
        for character in second_detail["characters"]
    )
    rejected = client.put(
        f"/api/v1/projects/{project_id}/dialogue-lines/{line['lineId']}/speaker",
        headers=auth_headers,
        json={
            "characterId": prior_character["characterId"],
            "reason": "Reject a stale character reference.",
            "expectedRevision": line["revision"],
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "INVALID_CHARACTER_REFERENCE"

    unchanged = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    current_line = next(
        value for value in unchanged["dialogueLines"] if value["lineId"] == line["lineId"]
    )
    assert current_line["revision"] == line["revision"]
    assert unchanged["humanCorrections"] == []


def test_import_review_approval_is_append_only_idempotent_and_conflict_safe(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Review Approval"},
    ).json()["project"]
    queued = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="approval.md",
        content=SYNTHETIC_BYTES,
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="approval-import",
    )
    wait_for_extraction(client, auth_headers, queued)
    review = review_for_extraction(
        client,
        auth_headers,
        project_id=project["projectId"],
        extraction_id=queued["extraction"]["extractionId"],
    )

    blocked = client.post(
        f"/api/v1/projects/{project['projectId']}/jobs",
        headers=auth_headers,
        json={
            "type": "analyze_story",
            "inputRevision": 1,
            "idempotencyKey": "analysis-before-approval",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "STORY_REQUIRED"
    fetched = client.get(
        (f"/api/v1/projects/{project['projectId']}/imports/{review['reviewId']}/review"),
        headers=auth_headers,
    )
    assert fetched.status_code == 200
    assert fetched.json()["review"] == review

    approved = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=review,
        decision="approved",
        rationale="Synthetic evidence reviewed.",
        idempotency_key="approval-decision",
    )
    repeated = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=review,
        decision="approved",
        rationale="Synthetic evidence reviewed.",
        idempotency_key="approval-decision",
    )
    rationale_conflict = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=review,
        decision="approved",
        rationale="A different human rationale.",
        idempotency_key="approval-decision",
    )
    stale = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=review,
        decision="rejected",
        idempotency_key="stale-review-decision",
    )

    assert approved.status_code == repeated.status_code == 200
    assert repeated.json()["decision"]["decisionId"] == approved.json()["decision"]["decisionId"]
    assert rationale_conflict.status_code == 409
    assert rationale_conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert approved.json()["decision"]["immutable"] is True
    assert approved.json()["decision"]["rationale"] == "Synthetic evidence reviewed."
    assert approved.json()["analysisAllowed"] is True
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "IMPORT_REVIEW_CONFLICT"
    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    assert detail["analysisAllowed"] is True
    assert detail["story"] is not None
    with app.state.database.session() as session:
        history = (
            session.query(ImportReviewRow)
            .filter_by(review_id=review["reviewId"])
            .order_by(ImportReviewRow.revision)
            .all()
        )
        assert [(row.revision, row.state) for row in history] == [
            (1, "pending"),
            (2, "approved"),
        ]
        assert history[0].decision_id is None
        assert history[1].supersedes_record_id == history[0].id


def test_import_review_rejection_remains_blocked_and_idempotent(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "Review Rejection"},
    ).json()["project"]
    queued = submit_import(
        client,
        auth_headers,
        project_id=project["projectId"],
        filename="reject.txt",
        content=b"Synthetic text that will be rejected.\n",
        media_type="text/plain",
        declared_format="txt",
        idempotency_key="rejection-import",
    )
    wait_for_extraction(client, auth_headers, queued)
    review = review_for_extraction(
        client,
        auth_headers,
        project_id=project["projectId"],
        extraction_id=queued["extraction"]["extractionId"],
    )
    rejected = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=review,
        decision="rejected",
        rationale="Synthetic rejection.",
        idempotency_key="rejection-decision",
    )
    repeated = decide_import_review(
        client,
        auth_headers,
        project_id=project["projectId"],
        review=review,
        decision="rejected",
        rationale="Synthetic rejection.",
        idempotency_key="rejection-decision",
    )

    assert rejected.status_code == repeated.status_code == 200
    assert rejected.json()["analysisAllowed"] is False
    assert rejected.json()["review"]["state"] == "rejected"
    assert rejected.json()["decision"]["decisionId"] == repeated.json()["decision"]["decisionId"]
    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    assert detail["story"] is None
    assert detail["analysisAllowed"] is False
    blocked = client.post(
        f"/api/v1/projects/{project['projectId']}/jobs",
        headers=auth_headers,
        json={
            "type": "analyze_story",
            "inputRevision": 1,
            "idempotencyKey": "analysis-after-rejection",
        },
    )
    assert blocked.status_code == 409
    with app.state.database.session() as session:
        assert [
            row.state
            for row in (
                session.query(ImportReviewRow)
                .filter_by(review_id=review["reviewId"])
                .order_by(ImportReviewRow.revision)
                .all()
            )
        ] == ["pending", "rejected"]


def test_reextract_appends_candidate_and_requires_its_own_review(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="reextract-project",
        import_key="reextract-import",
    )
    project_id = imported["project"]["projectId"]
    original_story_id = imported["story"]["storyId"]
    invalid = client.post(
        (f"/api/v1/projects/{project_id}/imports/{imported['source']['documentId']}/reextract"),
        headers={**auth_headers, "Idempotency-Key": "invalid key"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"
    with app.state.database.session() as session:
        assert session.query(DocumentExtractionRow).count() == 1

    response = client.post(
        (f"/api/v1/projects/{project_id}/imports/{imported['source']['documentId']}/reextract"),
        headers={**auth_headers, "Idempotency-Key": "reextract-job"},
    )
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["extraction"]["revision"] == 2
    wait_for_job(client, auth_headers, queued["job"]["jobId"], {"succeeded"})
    pending = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    assert len(pending["sourceDocuments"]) == 1
    assert len(pending["extractions"]) == 2
    assert len(pending["importReviews"]) == 2
    assert pending["story"]["storyId"] == original_story_id
    new_review = next(
        review
        for review in pending["importReviews"]
        if review["extractionId"] == queued["extraction"]["extractionId"]
    )
    assert new_review["state"] == "pending"
    assert pending["analysisAllowed"] is False
    blocked = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        headers=auth_headers,
        json={
            "type": "analyze_story",
            "inputRevision": pending["story"]["revision"],
            "idempotencyKey": "analysis-while-reextract-pending",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "IMPORT_APPROVAL_REQUIRED"

    approved = decide_import_review(
        client,
        auth_headers,
        project_id=project_id,
        review=new_review,
        decision="approved",
        idempotency_key="reextract-approval",
    )
    assert approved.status_code == 200, approved.text
    replayed = client.post(
        (f"/api/v1/projects/{project_id}/imports/{imported['source']['documentId']}/reextract"),
        headers={**auth_headers, "Idempotency-Key": "reextract-job"},
    )
    assert replayed.status_code == 202
    assert replayed.json()["extraction"]["extractionId"] == queued["extraction"]["extractionId"]
    assert replayed.json()["job"]["jobId"] == queued["job"]["jobId"]
    after = client.get(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
    ).json()
    assert after["story"]["storyId"] != original_story_id
    assert after["story"]["storyId"] == new_review["candidateStoryId"]
    assert after["analysisAllowed"] is True
    with app.state.database.session() as session:
        assert session.query(SourceDocumentRow).count() == 1
        assert session.query(DocumentExtractionRow).count() == 2
        assert session.query(ImportedStoryRow).count() == 2


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
    if response.status_code == 202:
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
