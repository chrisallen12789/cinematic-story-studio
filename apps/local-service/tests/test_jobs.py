from __future__ import annotations

import concurrent.futures
import hashlib
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service import document_ingest as document_ingest_module
from cinematic_story_service import jobs as jobs_module
from cinematic_story_service import parser_process as parser_process_module
from cinematic_story_service.database import Database
from cinematic_story_service.document_ingest import (
    INGEST_CONTRACT_VERSION,
    DocumentExtractionRequest,
    DocumentExtractionResult,
)
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.jobs import JobRepository, JobWorker
from cinematic_story_service.models import (
    DocumentExtractionRow,
    JobAttemptRow,
    JobCheckpointRow,
    JobEventRow,
    JobRow,
    ParserExecutionRow,
    ProjectRow,
    SourceDocumentRow,
)
from cinematic_story_service.parser_process import (
    PARSER_PROCESS_MEMORY_LIMIT_BYTES,
    DocumentExtractionRunner,
    SpawnedDocumentExtractionRunner,
)
from cinematic_story_service.util import canonical_json, parse_json, utc_now

from .conftest import (
    SYNTHETIC_BYTES,
    TOKEN,
    create_analysis_job,
    wait_for_job,
)


def _blocking_parser_target(
    result_connection: Connection,
    _request: DocumentExtractionRequest,
) -> None:
    result_connection.send_bytes(b'{"progress":500000,"stage":"blocking_test","type":"progress"}')
    time.sleep(60)


def _terminal_then_blocking_parser_target(
    result_connection: Connection,
    _request: DocumentExtractionRequest,
) -> None:
    result_connection.send_bytes(b'{"progress":500000,"stage":"terminal_sent","type":"progress"}')
    result_connection.send_bytes(
        b'{"code":"SYNTHETIC_PARSER_ERROR","retryable":true,"status_code":422,"type":"error"}'
    )
    time.sleep(60)


def _owned_parser_without_ready_envelope(
    _result_connection: Connection,
    _start_connection: Connection,
    _ownership_connection: Connection,
    _request: DocumentExtractionRequest,
    job_object_name: str | None,
    _child_target: Any,
) -> None:
    if sys.platform == "win32":
        assert job_object_name is not None
        assert parser_process_module._self_assign_windows_job(job_object_name)
    time.sleep(60)


def _invalid_result_parser_target(
    result_connection: Connection,
    request: DocumentExtractionRequest,
) -> None:
    try:
        result = document_ingest_module.adapter_for(request.declared_format).extract(
            request,
            cancelled=lambda: False,
            progress=lambda _stage, _value: None,
        )
        result_connection.send_bytes(
            canonical_json(
                {
                    "type": "result",
                    "result": asdict(replace(result, confidence=1.1)),
                }
            ).encode("utf-8")
        )
    finally:
        result_connection.close()


class _StartFailingProcess:
    pid = None
    exitcode = None

    def __init__(self) -> None:
        self.closed = False

    @staticmethod
    def start() -> None:
        raise OSError("synthetic start failure")

    @staticmethod
    def is_alive() -> bool:
        raise AssertionError("is_alive must not be called before a successful start")

    @staticmethod
    def join(_timeout: float | None = None) -> None:
        raise AssertionError("join must not be called before a successful start")

    @staticmethod
    def terminate() -> None:
        raise AssertionError("terminate must not be called before a successful start")

    @staticmethod
    def kill() -> None:
        raise AssertionError("kill must not be called before a successful start")

    def close(self) -> None:
        self.closed = True


def _repository_pair(app: Any) -> tuple[JobRepository, JobRepository]:
    return (
        JobRepository(app.state.database, app.state.projects, "repository-one"),
        JobRepository(app.state.database, app.state.projects, "repository-two"),
    )


def create_imported_project(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    story_bytes: bytes = SYNTHETIC_BYTES,
    create_key: str = "create-project-key",
    import_key: str = "import-story-key",
) -> dict[str, Any]:
    """Create, extract, and approve a synthetic import for analysis job tests."""

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
    assert imported.status_code == 202, imported.text
    import_payload = imported.json()
    job = import_payload["job"]
    app = client.app
    worker = app.state.worker
    gates = [
        worker.controls.claim_gate,
        worker.controls.execution_gate,
        worker.controls.before_publication_gate,
        worker.controls.publication_claim_gate,
    ]
    previously_set = [gate.is_set() for gate in gates]
    try:
        for gate in gates:
            gate.set()
        worker.wake()
        current = app.state.jobs.get_job(job["jobId"])
        if current["state"] == "queued":
            claimed = app.state.jobs.claim_next()
            if claimed is not None:
                assert claimed["jobId"] == job["jobId"]
                worker._run_extraction(claimed)
        terminal = wait_for_job(
            client,
            auth_headers,
            job["jobId"],
            {"succeeded", "failed", "cancelled", "interrupted"},
        )
        assert terminal["state"] == "succeeded", terminal
    finally:
        for gate, was_set in zip(gates, previously_set, strict=True):
            if not was_set:
                gate.clear()
        worker.controls.publication_claimed.clear()

    detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    pending_review = detail.json()["importReviews"][0]
    decision = client.post(
        (
            f"/api/v1/projects/{project['projectId']}/imports/"
            f"{pending_review['reviewId']}/review/decision"
        ),
        headers=auth_headers,
        json={
            "reviewId": pending_review["reviewId"],
            "decision": "approved",
            "rationale": "Synthetic fixture approved for job verification.",
            "expectedRevision": pending_review["revision"],
            "evidenceFingerprint": pending_review["evidenceFingerprint"],
            "idempotencyKey": f"approve-{import_key}",
        },
    )
    assert decision.status_code == 200, decision.text
    approved_detail = client.get(
        f"/api/v1/projects/{project['projectId']}",
        headers=auth_headers,
    ).json()
    assert approved_detail["analysisAllowed"] is True
    assert approved_detail["story"] is not None
    return {
        "project": project,
        "source": import_payload["sourceDocument"],
        "extraction": import_payload["extraction"],
        "story": approved_detail["story"],
    }


@dataclass(frozen=True, slots=True)
class _ExtractionInput:
    project_id: str
    source_document_id: str
    extraction_id: str
    extraction_revision: int
    source_path: Path
    display_name: str
    declared_format: str
    source_sha256: str
    source_byte_count: int


class _ExtractionProjects:
    def __init__(self, database: Database, extraction_input: _ExtractionInput) -> None:
        self.database = database
        self.extraction_input = extraction_input
        self.published_job_ids: list[str] = []

    @staticmethod
    def require_project(session: Session, project_id: str) -> ProjectRow:
        project = session.get(ProjectRow, project_id)
        if project is None:
            raise ServiceError(404, "PROJECT_NOT_FOUND", "The project was not found.")
        return project

    def get_extraction_input(self, extraction_id: str) -> _ExtractionInput:
        assert extraction_id == self.extraction_input.extraction_id
        return self.extraction_input

    def publish_extraction(
        self,
        *,
        job_id: str,
        result: DocumentExtractionResult,
        session: Session,
    ) -> None:
        job = session.get(JobRow, job_id)
        assert job is not None
        assert job.target_id == self.extraction_input.extraction_id
        extraction = session.get(DocumentExtractionRow, self.extraction_input.extraction_id)
        source = session.get(SourceDocumentRow, self.extraction_input.source_document_id)
        assert extraction is not None
        assert source is not None
        assert result.source_sha256 == source.content_sha256
        payload = parse_json(job.payload_json, {})
        assert isinstance(payload, dict)
        limits_fingerprint = payload["limitsFingerprint"]
        assert isinstance(limits_fingerprint, str)

        extraction.status = result.status
        extraction.extractor_name = result.adapter_id
        extraction.extractor_version = result.adapter_version
        extraction.text_sha256 = result.extracted_text_sha256
        extraction.character_count = len(result.canonical_text)
        extraction.page_count = result.page_count
        extraction.encoding = result.encoding
        extraction.newline_style = result.newline_style
        extraction.exact_text = result.canonical_text
        extraction.manifest_json = result.manifest_json()
        extraction.sections_json = canonical_json([asdict(section) for section in result.sections])
        extraction.source_mappings_json = canonical_json(
            [asdict(section.location) for section in result.sections]
        )
        extraction.evidence_fingerprint = result.extracted_text_sha256
        extraction.warnings_json = canonical_json([asdict(warning) for warning in result.warnings])
        extraction.updated_at = result.completed_at
        source.text_sha256 = result.extracted_text_sha256
        source.encoding = result.encoding
        source.newline_style = result.newline_style
        source.extraction_status = result.status
        session.add(
            ParserExecutionRow(
                id=f"parser-{job.id}-{job.current_attempt}",
                project_id=job.project_id,
                source_document_id=source.id,
                extraction_id=extraction.id,
                job_id=job.id,
                attempt=job.current_attempt,
                parser_name=result.adapter_id,
                parser_version=result.adapter_version,
                outcome="partial" if result.status == "partial" else "succeeded",
                input_sha256=result.source_sha256,
                limits_fingerprint=limits_fingerprint,
                output_text_sha256=result.extracted_text_sha256,
                manifest_json=result.manifest_json(),
                sections_json=extraction.sections_json,
                source_mappings_json=extraction.source_mappings_json,
                warnings_json=extraction.warnings_json,
                error_code=None,
                error_message=None,
                error_retryable=None,
                started_at=result.started_at,
                finished_at=result.completed_at,
            )
        )
        self.published_job_ids.append(job.id)


class _InlineDocumentExtractionRunner:
    def run(
        self,
        request: Any,
        *,
        cancelled: Any,
        progress: Any,
    ) -> DocumentExtractionResult:
        return document_ingest_module.adapter_for(request.declared_format).extract(
            request,
            cancelled=cancelled,
            progress=progress,
        )


def _extraction_job_harness(
    tmp_path: Path,
    *,
    parser_runner: DocumentExtractionRunner | None = None,
    parser_deadline_seconds: float = 1,
) -> tuple[Database, JobRepository, JobWorker, _ExtractionProjects]:
    settings = ServiceSettings(
        data_dir=tmp_path / "extraction-job-data",
        bearer_token=TOKEN,
        worker_enabled=False,
        parser_deadline_seconds=parser_deadline_seconds,
    ).validated()
    raw = b"# Synthetic extraction\n\nExact text.\r\n"
    source_sha256 = hashlib.sha256(raw).hexdigest()
    storage_key = "projects/project-extraction/sources/source.txt"
    source_path = settings.data_dir / storage_key
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(raw)
    database = Database(settings.database_path)
    now = utc_now()
    with database.session() as session:
        session.add(
            ProjectRow(
                id="project-extraction",
                name="Synthetic Extraction",
                status="draft",
                revision=1,
                story_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SourceDocumentRow(
                id="source-extraction",
                project_id="project-extraction",
                display_name="source.txt",
                media_type="text/plain",
                declared_format="txt",
                content_sha256=source_sha256,
                text_sha256=None,
                byte_length=len(raw),
                encoding=None,
                newline_style=None,
                storage_key=storage_key,
                imported_at=now,
                revision=1,
                source_revision=1,
                supersedes_document_id=None,
                extraction_status="pending",
                provenance_json="{}",
                warnings_json="[]",
            )
        )
        session.flush()
        session.add(
            DocumentExtractionRow(
                id="extraction-1",
                project_id="project-extraction",
                source_document_id="source-extraction",
                revision=1,
                supersedes_extraction_id=None,
                status="pending",
                format="txt",
                extractor_name="pending",
                extractor_version=INGEST_CONTRACT_VERSION,
                input_sha256=source_sha256,
                text_sha256=None,
                character_count=None,
                page_count=None,
                encoding=None,
                newline_style=None,
                exact_text=None,
                text_storage_key=None,
                manifest_json="{}",
                sections_json="[]",
                source_mappings_json="[]",
                evidence_fingerprint=source_sha256,
                warnings_json="[]",
                created_at=now,
                updated_at=now,
            )
        )
    extraction_input = _ExtractionInput(
        project_id="project-extraction",
        source_document_id="source-extraction",
        extraction_id="extraction-1",
        extraction_revision=1,
        source_path=source_path,
        display_name="source.txt",
        declared_format="txt",
        source_sha256=source_sha256,
        source_byte_count=len(raw),
    )
    projects = _ExtractionProjects(database, extraction_input)
    jobs = JobRepository(
        database,
        projects,  # type: ignore[arg-type]
        "extraction-test-worker",
        settings.parser_deadline_seconds,
    )
    worker = JobWorker(
        settings,
        jobs,
        projects,  # type: ignore[arg-type]
        parser_runner=parser_runner or _InlineDocumentExtractionRunner(),
    )
    return database, jobs, worker, projects


def _create_extraction_job(
    jobs: JobRepository,
    projects: _ExtractionProjects,
    *,
    idempotency_key: str = "extract-synthetic",
) -> dict[str, Any]:
    value = projects.extraction_input
    return jobs.create_extraction_job(
        project_id=value.project_id,
        extraction_id=value.extraction_id,
        input_revision=value.extraction_revision,
        input_fingerprint=value.source_sha256,
        idempotency_key=idempotency_key,
    )


def test_startup_recovers_pending_extraction_without_a_job(tmp_path: Path) -> None:
    database, jobs, _worker, projects = _extraction_job_harness(tmp_path)
    try:
        assert jobs.reconcile_orphaned_extractions() == 1
        assert jobs.reconcile_orphaned_extractions() == 0

        with database.session() as session:
            recovered = (
                session.query(JobRow)
                .filter_by(
                    project_id=projects.extraction_input.project_id,
                    type="extract_document",
                    target_type="document_extraction",
                    target_id=projects.extraction_input.extraction_id,
                )
                .one()
            )
            assert recovered.state == "queued"
            assert recovered.input_fingerprint == projects.extraction_input.source_sha256
            assert (
                session.query(JobEventRow)
                .filter_by(job_id=recovered.id, type="created", state="queued")
                .count()
                == 1
            )
    finally:
        database.close()


def test_extract_document_job_is_targeted_idempotent_and_publishes_atomically(
    tmp_path: Path,
) -> None:
    database, jobs, worker, projects = _extraction_job_harness(tmp_path)
    try:
        created = _create_extraction_job(jobs, projects)
        duplicate_active = _create_extraction_job(
            jobs,
            projects,
            idempotency_key="extract-synthetic-again",
        )
        assert duplicate_active["jobId"] == created["jobId"]
        assert created["type"] == "extract_document"
        assert created["target"] == {
            "type": "document_extraction",
            "id": "extraction-1",
        }
        assert created["inputRevision"] == 1
        assert created["inputFingerprint"] == projects.extraction_input.source_sha256

        claimed = jobs.claim_next()
        assert claimed is not None
        worker._run_extraction(claimed)

        completed = jobs.get_job(created["jobId"])
        assert completed["state"] == "succeeded"
        assert completed["progress"] == 1
        assert projects.published_job_ids == [created["jobId"]]
        duplicate_terminal = _create_extraction_job(
            jobs,
            projects,
            idempotency_key="extract-synthetic-terminal",
        )
        assert duplicate_terminal["jobId"] == created["jobId"]
        with database.session() as session:
            extraction = session.get(DocumentExtractionRow, "extraction-1")
            assert extraction is not None
            assert extraction.status == "complete"
            assert extraction.exact_text == "# Synthetic extraction\n\nExact text.\r\n"
            executions = session.query(ParserExecutionRow).filter_by(job_id=created["jobId"]).all()
            assert [(row.attempt, row.outcome) for row in executions] == [(1, "succeeded")]
        events, _last = jobs.get_events(created["jobId"], after_sequence=0)
        progress = [event["progress"] for event in events if "progress" in event]
        assert progress == sorted(progress)
        assert events[-1]["state"] == "succeeded"
    finally:
        database.close()


def test_extract_document_failure_is_redacted_and_retry_appends_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, jobs, worker, projects = _extraction_job_harness(tmp_path)
    original_adapter_for = document_ingest_module.adapter_for

    class _FailingAdapter:
        def extract(self, *_args: object, **_kwargs: object) -> DocumentExtractionResult:
            raise RuntimeError(
                f"private source leaked from {projects.extraction_input.source_path}"
            )

    try:
        monkeypatch.setattr(
            document_ingest_module,
            "adapter_for",
            lambda _format: _FailingAdapter(),
        )
        created = _create_extraction_job(jobs, projects)
        claimed = jobs.claim_next()
        assert claimed is not None
        worker._run_extraction(claimed)

        failed = jobs.get_job(created["jobId"])
        assert failed["state"] == "failed"
        assert failed["error"] == {
            "code": "EXTRACTION_FAILED",
            "message": "Document extraction could not be completed safely.",
            "retryable": True,
        }
        assert str(projects.extraction_input.source_path) not in canonical_json(failed)
        with database.session() as session:
            first_execution = (
                session.query(ParserExecutionRow)
                .filter_by(job_id=created["jobId"], attempt=1)
                .one()
            )
            assert first_execution.outcome == "failed"
            assert first_execution.error_message == (
                "Document extraction could not be completed safely."
            )
            assert str(projects.extraction_input.source_path) not in (
                first_execution.error_message or ""
            )
            assert (
                session.get(
                    DocumentExtractionRow,
                    projects.extraction_input.extraction_id,
                ).status
                == "failed"
            )
            assert (
                session.get(
                    SourceDocumentRow,
                    projects.extraction_input.source_document_id,
                ).extraction_status
                == "failed"
            )

        retry = jobs.retry(created["jobId"])
        assert retry["attempt"] == 2
        monkeypatch.setattr(document_ingest_module, "adapter_for", original_adapter_for)
        retry_claim = jobs.claim_next()
        assert retry_claim is not None
        worker._run_extraction(retry_claim)
        assert jobs.get_job(created["jobId"])["state"] == "succeeded"
        with database.session() as session:
            executions = (
                session.query(ParserExecutionRow)
                .filter_by(job_id=created["jobId"])
                .order_by(ParserExecutionRow.attempt)
                .all()
            )
            assert [(row.attempt, row.outcome) for row in executions] == [
                (1, "failed"),
                (2, "succeeded"),
            ]
    finally:
        database.close()


def test_extract_document_cancel_and_interruption_are_durable(
    tmp_path: Path,
) -> None:
    cancel_database, cancel_jobs, cancel_worker, cancel_projects = _extraction_job_harness(
        tmp_path / "cancel"
    )
    try:
        cancelled_job = _create_extraction_job(cancel_jobs, cancel_projects)
        cancelled_claim = cancel_jobs.claim_next()
        assert cancelled_claim is not None
        assert cancel_jobs.cancel(cancelled_job["jobId"])["state"] == "cancel_requested"
        cancel_worker._run_extraction(cancelled_claim)
        assert cancel_jobs.get_job(cancelled_job["jobId"])["state"] == "cancelled"
        with cancel_database.session() as session:
            execution = (
                session.query(ParserExecutionRow).filter_by(job_id=cancelled_job["jobId"]).one()
            )
            assert execution.outcome == "cancelled"
            assert execution.error_code == "EXTRACTION_CANCELLED"
            assert (
                session.get(
                    DocumentExtractionRow,
                    cancel_projects.extraction_input.extraction_id,
                ).status
                == "failed"
            )
    finally:
        cancel_database.close()

    restart_database, restart_jobs, _restart_worker, restart_projects = _extraction_job_harness(
        tmp_path / "cancel-before-restart"
    )
    try:
        restart_job = _create_extraction_job(restart_jobs, restart_projects)
        restart_claim = restart_jobs.claim_next()
        assert restart_claim is not None
        assert restart_jobs.cancel(restart_job["jobId"])["state"] == "cancel_requested"

        restarted_repository = JobRepository(
            restart_database,
            restart_projects,  # type: ignore[arg-type]
            "restarted-extraction-worker",
        )
        assert restarted_repository.reconcile_interrupted() == 1
        assert restarted_repository.get_job(restart_job["jobId"])["state"] == "cancelled"
        with restart_database.session() as session:
            execution = (
                session.query(ParserExecutionRow).filter_by(job_id=restart_job["jobId"]).one()
            )
            assert execution.outcome == "cancelled"
            assert execution.error_code == "EXTRACTION_CANCELLED"
    finally:
        restart_database.close()

    shutdown_database, shutdown_jobs, _shutdown_worker, shutdown_projects = _extraction_job_harness(
        tmp_path / "cancel-during-shutdown"
    )
    try:
        shutdown_job = _create_extraction_job(shutdown_jobs, shutdown_projects)
        shutdown_claim = shutdown_jobs.claim_next()
        assert shutdown_claim is not None
        assert shutdown_jobs.cancel(shutdown_job["jobId"])["state"] == "cancel_requested"
        shutdown_jobs.interrupt_active(shutdown_job["jobId"])
        assert shutdown_jobs.get_job(shutdown_job["jobId"])["state"] == "cancelled"
    finally:
        shutdown_database.close()

    interrupted_database, interrupted_jobs, interrupted_worker, interrupted_projects = (
        _extraction_job_harness(tmp_path / "interrupted")
    )
    try:
        interrupted_job = _create_extraction_job(interrupted_jobs, interrupted_projects)
        interrupted_claim = interrupted_jobs.claim_next()
        assert interrupted_claim is not None
        interrupted_jobs.interrupt_active(interrupted_job["jobId"])
        assert interrupted_jobs.get_job(interrupted_job["jobId"])["state"] == "interrupted"
        with interrupted_database.session() as session:
            assert (
                session.get(
                    DocumentExtractionRow,
                    interrupted_projects.extraction_input.extraction_id,
                ).status
                == "pending"
            )
        resumed = interrupted_jobs.resume(interrupted_job["jobId"])
        assert resumed["attempt"] == 2
        resumed_claim = interrupted_jobs.claim_next()
        assert resumed_claim is not None
        interrupted_worker._run_extraction(resumed_claim)
        assert interrupted_jobs.get_job(interrupted_job["jobId"])["state"] == "succeeded"
        with interrupted_database.session() as session:
            executions = (
                session.query(ParserExecutionRow)
                .filter_by(job_id=interrupted_job["jobId"])
                .order_by(ParserExecutionRow.attempt)
                .all()
            )
            assert [(row.attempt, row.outcome) for row in executions] == [
                (1, "interrupted"),
                (2, "succeeded"),
            ]
    finally:
        interrupted_database.close()


def test_spawned_parser_hard_deadline_terminates_exact_owned_target_without_publication(
    tmp_path: Path,
) -> None:
    runner = SpawnedDocumentExtractionRunner(
        poll_seconds=0.01,
        child_target=_blocking_parser_target,
    )
    database, jobs, worker, projects = _extraction_job_harness(
        tmp_path,
        parser_runner=runner,
        parser_deadline_seconds=10,
    )
    try:
        created = _create_extraction_job(jobs, projects)
        claimed = jobs.claim_next()
        assert claimed is not None

        started = time.monotonic()
        worker._run_extraction(claimed)
        elapsed = time.monotonic() - started

        failed = jobs.get_job(created["jobId"])
        assert failed["state"] == "failed"
        assert failed["error"] == {
            "code": "PARSER_TIMEOUT",
            "message": "Document extraction could not be completed safely.",
            "retryable": True,
        }
        assert elapsed < 12
        assert projects.published_job_ids == []
        events, _last = jobs.get_events(created["jobId"], after_sequence=0)
        assert any(event["stage"] == "blocking_test" for event in events), (
            events,
            runner.last_evidence,
        )

        evidence = runner.last_evidence
        assert evidence is not None
        assert evidence.pid > 0
        assert evidence.launcher_pid > 0
        assert evidence.reason == "deadline"
        assert evidence.terminated_by_parent is True
        assert evidence.confirmed_exited is True
        assert runner.active_pid is None
        if sys.platform == "win32":
            assert evidence.job_object_assigned is True
            assert evidence.process_memory_limit_bytes == PARSER_PROCESS_MEMORY_LIMIT_BYTES

        with database.session() as session:
            execution = session.query(ParserExecutionRow).filter_by(job_id=created["jobId"]).one()
            assert execution.outcome == "failed"
            assert execution.error_code == "PARSER_TIMEOUT"
            assert execution.error_retryable is True
        assert jobs.retry(created["jobId"])["state"] == "queued"
    finally:
        database.close()


def test_spawned_parser_parent_cancellation_terminates_exact_owned_target(
    tmp_path: Path,
) -> None:
    runner = SpawnedDocumentExtractionRunner(
        poll_seconds=0.01,
        child_target=_blocking_parser_target,
    )
    database, jobs, worker, projects = _extraction_job_harness(
        tmp_path,
        parser_runner=runner,
        parser_deadline_seconds=10,
    )
    try:
        created = _create_extraction_job(jobs, projects)
        claimed = jobs.claim_next()
        assert claimed is not None
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            extraction = pool.submit(worker._run_extraction, claimed)
            ownership_deadline = time.monotonic() + 5
            owned_pid = None
            while time.monotonic() < ownership_deadline:
                owned_pid = runner.active_pid
                if owned_pid is not None:
                    break
                time.sleep(0.01)
            assert owned_pid is not None

            cancelled_at = time.monotonic()
            assert jobs.cancel(created["jobId"])["state"] == "cancel_requested"
            extraction.result(timeout=5)
            assert time.monotonic() - cancelled_at < 2

        assert jobs.get_job(created["jobId"])["state"] == "cancelled"
        assert projects.published_job_ids == []
        evidence = runner.last_evidence
        assert evidence is not None
        assert evidence.pid == owned_pid
        assert evidence.reason == "cancelled"
        assert evidence.terminated_by_parent is True
        assert evidence.confirmed_exited is True
        assert runner.active_pid is None
        if sys.platform == "win32":
            assert evidence.job_object_assigned is True
            assert evidence.process_memory_limit_bytes == PARSER_PROCESS_MEMORY_LIMIT_BYTES
        with database.session() as session:
            execution = session.query(ParserExecutionRow).filter_by(job_id=created["jobId"]).one()
            assert execution.outcome == "cancelled"
            assert execution.error_code == "EXTRACTION_CANCELLED"
    finally:
        database.close()


def test_spawned_parser_rejects_terminal_envelope_until_target_exits_voluntarily(
    tmp_path: Path,
) -> None:
    runner = SpawnedDocumentExtractionRunner(
        poll_seconds=0.01,
        child_target=_terminal_then_blocking_parser_target,
    )
    database, jobs, worker, projects = _extraction_job_harness(
        tmp_path,
        parser_runner=runner,
        parser_deadline_seconds=10,
    )
    try:
        created = _create_extraction_job(jobs, projects)
        claimed = jobs.claim_next()
        assert claimed is not None

        worker._run_extraction(claimed)

        failed = jobs.get_job(created["jobId"])
        assert failed["state"] == "failed"
        assert failed["error"]["code"] == "PARSER_TIMEOUT"
        assert failed["error"]["code"] != "SYNTHETIC_PARSER_ERROR"
        assert projects.published_job_ids == []
        events, _last = jobs.get_events(created["jobId"], after_sequence=0)
        assert any(event["stage"] == "terminal_sent" for event in events)
        evidence = runner.last_evidence
        assert evidence is not None
        assert evidence.reason == "deadline"
        assert evidence.terminated_by_parent is True
        assert evidence.confirmed_exited is True
        assert evidence.owned_processes_confirmed_exited is True
    finally:
        database.close()


def test_spawned_parser_ownership_timeout_kills_tree_without_claiming_unknown_target_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parser_process_module,
        "_parser_process_bootstrap",
        _owned_parser_without_ready_envelope,
    )
    runner = SpawnedDocumentExtractionRunner(poll_seconds=0.01)
    database, jobs, worker, projects = _extraction_job_harness(
        tmp_path,
        parser_runner=runner,
        parser_deadline_seconds=2,
    )
    try:
        created = _create_extraction_job(jobs, projects)
        claimed = jobs.claim_next()
        assert claimed is not None

        worker._run_extraction(claimed)

        failed = jobs.get_job(created["jobId"])
        assert failed["state"] == "failed"
        assert failed["error"]["code"] == "PARSER_TIMEOUT"
        assert projects.published_job_ids == []
        evidence = runner.last_evidence
        assert evidence is not None
        assert evidence.pid == 0
        assert evidence.launcher_pid > 0
        assert evidence.reason == "deadline"
        assert evidence.terminated_by_parent is True
        assert evidence.confirmed_exited is True
        assert evidence.owned_processes_confirmed_exited is True
        if sys.platform == "win32":
            assert evidence.job_object_assigned is False
            assert evidence.launcher_job_object_assigned is True
            assert evidence.process_memory_limit_bytes == PARSER_PROCESS_MEMORY_LIMIT_BYTES
    finally:
        database.close()


def test_spawned_parser_records_protocol_error_when_semantic_result_validation_fails(
    tmp_path: Path,
) -> None:
    runner = SpawnedDocumentExtractionRunner(
        poll_seconds=0.01,
        child_target=_invalid_result_parser_target,
    )
    database, jobs, worker, projects = _extraction_job_harness(
        tmp_path,
        parser_runner=runner,
        parser_deadline_seconds=5,
    )
    try:
        created = _create_extraction_job(jobs, projects)
        claimed = jobs.claim_next()
        assert claimed is not None

        worker._run_extraction(claimed)

        failed = jobs.get_job(created["jobId"])
        assert failed["state"] == "failed"
        assert failed["error"]["code"] == "PARSER_PROCESS_PROTOCOL_INVALID"
        assert projects.published_job_ids == []
        evidence = runner.last_evidence
        assert evidence is not None
        assert evidence.reason == "protocol_error"
        assert evidence.terminated_by_parent is False
        assert evidence.confirmed_exited is True
        assert evidence.owned_processes_confirmed_exited is True
    finally:
        database.close()


def test_spawned_parser_start_failure_does_not_join_or_inspect_unstarted_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"Exact text.\n"
    source_path = tmp_path / "source.txt"
    source_path.write_bytes(raw)
    request = DocumentExtractionRequest(
        contract_version=INGEST_CONTRACT_VERSION,
        source_path=source_path,
        display_name="source.txt",
        declared_format="txt",
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_byte_count=len(raw),
        deadline_seconds=5,
    )
    runner = SpawnedDocumentExtractionRunner(poll_seconds=0.01)
    process = _StartFailingProcess()
    monkeypatch.setattr(
        type(runner._context),
        "Process",
        lambda _context, **_kwargs: process,
    )

    with pytest.raises(ServiceError) as raised:
        runner.run(
            request,
            cancelled=lambda: False,
            progress=lambda _stage, _value: None,
        )

    assert raised.value.code == "PARSER_PROCESS_OWNERSHIP_FAILED"
    assert process.closed is True
    evidence = runner.last_evidence
    assert evidence is not None
    assert evidence.pid == 0
    assert evidence.launcher_pid == 0
    assert evidence.reason == "ownership_error"
    assert evidence.terminated_by_parent is False
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True


def test_parent_rejects_non_finite_out_of_range_and_malformed_parser_results(
    tmp_path: Path,
) -> None:
    raw = b"# Synthetic extraction\n\nExact text.\r\n"
    source_path = tmp_path / "source.txt"
    source_path.write_bytes(raw)
    request = DocumentExtractionRequest(
        contract_version=INGEST_CONTRACT_VERSION,
        source_path=source_path,
        display_name="source.txt",
        declared_format="txt",
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_byte_count=len(raw),
        deadline_seconds=30,
    )
    result = document_ingest_module.adapter_for("txt").extract(
        request,
        cancelled=lambda: False,
        progress=lambda _stage, _value: None,
    )
    SpawnedDocumentExtractionRunner._validate_result(result, request)

    malformed_timestamp = "2026-99-99T99:99:99.999Z"
    invalid_results = [
        replace(result, confidence=float("nan")),
        replace(result, confidence=float("inf")),
        replace(result, confidence=-0.01),
        replace(result, confidence=1.01),
        replace(
            result,
            parser_execution=replace(result.parser_execution, duration_ms=30_001),
        ),
        replace(
            result,
            started_at=malformed_timestamp,
            parser_execution=replace(
                result.parser_execution,
                started_at=malformed_timestamp,
            ),
        ),
        replace(
            result,
            adapter_id="unsafe\nadapter",
            parser_execution=replace(
                result.parser_execution,
                adapter_id="unsafe\nadapter",
            ),
        ),
        replace(
            result,
            provenance={**result.provenance, "untrustedNumber": float("nan")},
        ),
        replace(
            result,
            sections=(
                replace(
                    result.sections[0],
                    location=replace(result.sections[0].location, start=-1),
                ),
                *result.sections[1:],
            ),
        ),
    ]
    for invalid in invalid_results:
        with pytest.raises(ServiceError) as raised:
            SpawnedDocumentExtractionRunner._validate_result(invalid, request)
        assert raised.value.code == "PARSER_PROCESS_PROTOCOL_INVALID"


def test_concurrent_different_keys_cannot_create_duplicate_active_job(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "duplicate-active-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        repositories = _repository_pair(app)
        start = threading.Barrier(3)

        def create(repository: JobRepository, key: str) -> tuple[str, object]:
            start.wait()
            try:
                return (
                    "created",
                    repository.create_job(
                        project_id=imported["project"]["projectId"],
                        job_type="analyze_story",
                        input_revision=imported["story"]["revision"],
                        idempotency_key=key,
                    ),
                )
            except ServiceError as exc:
                return ("error", exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create, repositories[0], "concurrent-create-one")
            second = pool.submit(create, repositories[1], "concurrent-create-two")
            start.wait()
            outcomes = [first.result(timeout=5), second.result(timeout=5)]

        assert [kind for kind, _value in outcomes].count("created") == 1
        loser = next(value for kind, value in outcomes if kind == "error")
        assert isinstance(loser, ServiceError)
        assert (loser.status_code, loser.code) == (409, "JOB_ALREADY_ACTIVE")
        with app.state.database.session() as session:
            active = session.query(JobRow).filter(JobRow.state.in_(["queued", "running"])).all()
            assert len(active) == 1


def test_two_repositories_cannot_double_claim_one_queued_job(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "double-claim-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="double-claim",
        )
        repositories = _repository_pair(app)
        start = threading.Barrier(3)

        def claim(repository: JobRepository) -> dict[str, Any] | None:
            start.wait()
            return repository.claim_next()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(claim, repositories[0])
            second = pool.submit(claim, repositories[1])
            start.wait()
            claims = [first.result(timeout=5), second.result(timeout=5)]

        assert sum(claimed is not None for claimed in claims) == 1
        claimed = next(value for value in claims if value is not None)
        assert claimed["jobId"] == job["jobId"]
        events, _last = repositories[0].get_events(job["jobId"], after_sequence=0)
        assert sum(event.get("state") == "running" for event in events) == 1


def test_progress_vs_cancel_never_appends_running_event_after_cancel_wins(
    tmp_path: Path,
) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "progress-cancel-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="progress-cancel",
        )
        repositories = _repository_pair(app)
        assert repositories[0].claim_next() is not None
        start = threading.Barrier(3)

        def cancel() -> dict[str, Any]:
            start.wait()
            return repositories[0].cancel(job["jobId"])

        def progress() -> bool:
            start.wait()
            return repositories[1].update_progress(
                job["jobId"],
                stage="racing_progress",
                progress=0.2,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            cancellation = pool.submit(cancel)
            progress_update = pool.submit(progress)
            start.wait()
            cancellation.result(timeout=5)
            progress_update.result(timeout=5)

        repositories[0].settle_pending_cancellation()
        events, _last = repositories[0].get_events(job["jobId"], after_sequence=0)
        cancel_sequence = next(
            event["sequence"] for event in events if event.get("state") == "cancel_requested"
        )
        assert not any(
            event["sequence"] > cancel_sequence
            and event["type"] == "progress"
            and event.get("state") == "running"
            for event in events
        )
        assert repositories[0].get_job(job["jobId"])["state"] == "cancelled"


def test_concurrent_event_allocation_is_contiguous_and_unique(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "event-sequence-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="event-sequence",
        )
        assert app.state.jobs.claim_next() is not None
        repository_count = 8
        repositories = [
            JobRepository(app.state.database, app.state.projects, f"sequence-{index}")
            for index in range(repository_count)
        ]
        start = threading.Barrier(repository_count + 1)

        def update_once(repository: JobRepository, index: int) -> bool:
            start.wait()
            return repository.update_progress(
                job["jobId"],
                stage=f"parallel-{index}",
                progress=0.2,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=repository_count) as pool:
            futures = [
                pool.submit(update_once, repository, index)
                for index, repository in enumerate(repositories)
            ]
            start.wait()
            assert all(future.result(timeout=10) for future in futures)

        events, last_sequence = app.state.jobs.get_events(job["jobId"], after_sequence=0)
        sequences = [event["sequence"] for event in events]
        assert sequences == list(range(1, len(events) + 1))
        assert last_sequence == sequences[-1]
        assert sum(event["type"] == "progress" for event in events) == repository_count


def test_concurrent_retry_has_one_cas_winner_and_one_new_attempt(tmp_path: Path) -> None:
    app = create_app(
        ServiceSettings(
            data_dir=tmp_path / "retry-cas-data",
            bearer_token=TOKEN,
            worker_enabled=False,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        imported = create_imported_project(client, headers)
        job = create_analysis_job(
            client,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="retry-cas",
        )
        assert app.state.jobs.claim_next() is not None
        app.state.jobs.finish_failed(job["jobId"])
        repositories = _repository_pair(app)
        start = threading.Barrier(3)

        def retry(repository: JobRepository) -> tuple[str, object]:
            start.wait()
            try:
                return ("queued", repository.retry(job["jobId"]))
            except ServiceError as exc:
                return ("error", exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(retry, repositories[0])
            second = pool.submit(retry, repositories[1])
            start.wait()
            outcomes = [first.result(timeout=5), second.result(timeout=5)]

        assert [kind for kind, _value in outcomes].count("queued") == 1
        loser = next(value for kind, value in outcomes if kind == "error")
        assert isinstance(loser, ServiceError)
        assert loser.code == "JOB_STATE_CONFLICT"
        with app.state.database.session() as session:
            attempts = (
                session.query(JobAttemptRow)
                .filter_by(job_id=job["jobId"])
                .order_by(JobAttemptRow.number)
                .all()
            )
            assert [attempt.number for attempt in attempts] == [1, 2]


def test_job_lifecycle_events_progress_and_reconnect(
    client: TestClient, app: object, auth_headers: dict[str, str]
) -> None:
    app.state.worker.controls.claim_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="lifecycle",
    )
    assert job["state"] == "queued"
    app.state.worker.controls.claim_gate.set()
    app.state.worker.wake()
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    assert terminal["progress"] == 1

    all_events = client.get(f"/api/v1/jobs/{job['jobId']}/events", headers=auth_headers).json()
    sequences = [event["sequence"] for event in all_events["events"]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    progress = [
        event["progress"]
        for event in all_events["events"]
        if event["attempt"] == 1 and "progress" in event
    ]
    assert progress == sorted(progress)
    assert all(0 <= value <= 1 for value in progress)
    assert progress[-1] == 1

    split_sequence = sequences[len(sequences) // 2]
    later = client.get(
        f"/api/v1/jobs/{job['jobId']}/events",
        headers=auth_headers,
        params={"afterSequence": split_sequence},
    ).json()
    assert later["events"]
    assert all(event["sequence"] > split_sequence for event in later["events"])
    assert later["lastSequence"] == all_events["lastSequence"]


def test_cancel_is_idempotent_and_does_not_publish(
    client: TestClient, app: object, auth_headers: dict[str, str]
) -> None:
    app.state.worker.controls.execution_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="cancel",
    )
    wait_for_job(client, auth_headers, job["jobId"], {"running", "cancel_requested"})
    first = client.post(f"/api/v1/jobs/{job['jobId']}/cancel", headers=auth_headers)
    second = client.post(f"/api/v1/jobs/{job['jobId']}/cancel", headers=auth_headers)
    assert first.status_code == second.status_code == 200
    app.state.worker.controls.execution_gate.set()
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"cancelled"})
    assert terminal["progress"] < 1
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}", headers=auth_headers
    ).json()
    assert detail["chapters"] == []

    later = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="after-cancel",
    )
    assert wait_for_job(client, auth_headers, later["jobId"], {"succeeded"})["state"] == "succeeded"


def test_cancel_at_final_publication_boundary_cannot_leave_results(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    app.state.worker.controls.before_publication_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="cancel-publication-race",
    )
    deadline = time.monotonic() + 5
    current = job
    while time.monotonic() < deadline:
        current = client.get(f"/api/v1/jobs/{job['jobId']}", headers=auth_headers).json()["job"]
        if current["stage"] == "publishing_analysis":
            break
        time.sleep(0.01)
    assert current["stage"] == "publishing_analysis"

    requested = client.post(f"/api/v1/jobs/{job['jobId']}/cancel", headers=auth_headers)
    assert requested.status_code == 200
    assert requested.json()["job"]["state"] == "cancel_requested"
    app.state.worker.controls.before_publication_gate.set()
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"cancelled"})
    assert terminal["state"] == "cancelled"
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}", headers=auth_headers
    ).json()
    assert detail["chapters"] == []
    assert detail["scenes"] == []
    assert detail["dialogueLines"] == []


def test_publication_write_claim_orders_a_racing_cancel_after_success(
    client: TestClient,
    app: object,
    auth_headers: dict[str, str],
) -> None:
    app.state.worker.controls.publication_claim_gate.clear()
    imported = create_imported_project(client, auth_headers)
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="publication-write-claim-race",
    )
    assert app.state.worker.controls.publication_claimed.wait(timeout=5)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        cancellation = pool.submit(
            client.post,
            f"/api/v1/jobs/{job['jobId']}/cancel",
            headers=auth_headers,
        )
        time.sleep(0.1)
        assert not cancellation.done()
        app.state.worker.controls.publication_claim_gate.set()
        cancellation_response = cancellation.result(timeout=5)

    assert cancellation_response.status_code == 409
    assert cancellation_response.json()["error"]["code"] == "JOB_STATE_CONFLICT"
    terminal = wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    assert terminal["state"] == "succeeded"
    detail = client.get(
        f"/api/v1/projects/{imported['project']['projectId']}", headers=auth_headers
    ).json()
    assert detail["chapters"]
    assert detail["dialogueLines"]


def test_failed_attempt_can_retry_without_duplicate_job_or_history_loss(
    client: TestClient, app: object, auth_headers: dict[str, str]
) -> None:
    imported = create_imported_project(client, auth_headers)
    app.state.worker.fail_next_attempt()
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="retry",
    )
    failed = wait_for_job(client, auth_headers, job["jobId"], {"failed"})
    assert failed["attempt"] == 1
    assert failed["error"] == {
        "code": "ANALYSIS_FAILED",
        "message": "Story analysis could not be completed.",
        "retryable": True,
    }
    retry = client.post(f"/api/v1/jobs/{job['jobId']}/retry", headers=auth_headers)
    assert retry.status_code == 200
    assert retry.json()["job"]["jobId"] == job["jobId"]
    assert retry.json()["job"]["attempt"] == 2
    succeeded = wait_for_job(client, auth_headers, job["jobId"], {"succeeded"})
    assert succeeded["attempt"] == 2
    events = client.get(f"/api/v1/jobs/{job['jobId']}/events", headers=auth_headers).json()[
        "events"
    ]
    assert {event["attempt"] for event in events} == {1, 2}
    assert any(event["type"] == "failed" and event["attempt"] == 1 for event in events)


def test_checkpoint_is_interrupted_on_shutdown_then_resumes(tmp_path: Path) -> None:
    data_dir = tmp_path / "resume-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        first_app.state.worker.controls.after_checkpoint_gate.clear()
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="resume",
        )
        checkpointed = wait_for_job(first, headers, job["jobId"], {"running"})
        while not checkpointed["checkpointAvailable"]:
            checkpointed = wait_for_job(first, headers, job["jobId"], {"running"})
        assert checkpointed["checkpointAvailable"] is True

    second_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(second_app) as second:
        interrupted = second.get(f"/api/v1/jobs/{job['jobId']}", headers=headers).json()["job"]
        assert interrupted["state"] == "interrupted"
        resumed = second.post(f"/api/v1/jobs/{job['jobId']}/resume", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["job"]["attempt"] == 2
        terminal = wait_for_job(second, headers, job["jobId"], {"succeeded"})
        assert terminal["attempt"] == 2
        events = second.get(f"/api/v1/jobs/{job['jobId']}/events", headers=headers).json()["events"]
        assert any(
            event["stage"] == "checkpoint_restored" for event in events if event.get("stage")
        )


def test_stop_before_first_checkpoint_restarts_as_clean_attempt_and_succeeds(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "clean-restart-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        first_app.state.worker.controls.execution_gate.clear()
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="clean-restart",
        )
        running = wait_for_job(first, headers, job["jobId"], {"running"})
        assert running["checkpointAvailable"] is False
        first_app.state.worker.stop(timeout=5)
        interrupted = first.get(
            f"/api/v1/jobs/{job['jobId']}",
            headers=headers,
        ).json()["job"]
        assert interrupted["state"] == "interrupted"
        assert interrupted["checkpointAvailable"] is False

    second_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(second_app) as second:
        resumed = second.post(f"/api/v1/jobs/{job['jobId']}/resume", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["job"]["attempt"] == 2
        terminal = wait_for_job(second, headers, job["jobId"], {"succeeded"})
        assert terminal["attempt"] == 2
        events = second.get(
            f"/api/v1/jobs/{job['jobId']}/events",
            headers=headers,
        ).json()["events"]
        attempt_two_stages = {event.get("stage") for event in events if event["attempt"] == 2}
        assert "analyzing_story" in attempt_two_stages
        assert "checkpoint_restored" not in attempt_two_stages


def test_worker_stop_timeout_retains_live_thread_and_storage_ownership(
    client: TestClient,
    app: Any,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original_analyze_story = jobs_module.analyze_story

    def blocked_analysis(**kwargs: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(timeout=5)
        return original_analyze_story(**kwargs)

    monkeypatch.setattr(jobs_module, "analyze_story", blocked_analysis)
    imported = create_imported_project(
        client,
        auth_headers,
        create_key="stop-timeout-project",
        import_key="stop-timeout-import",
    )
    job = create_analysis_job(
        client,
        auth_headers,
        imported["project"]["projectId"],
        imported["story"]["revision"],
        idempotency_key="stop-timeout-job",
    )
    assert entered.wait(timeout=5)

    try:
        with pytest.raises(ServiceError) as raised:
            app.state.worker.stop(timeout=0.01)
        assert raised.value.code == "WORKER_STOP_TIMEOUT"
        worker_thread = app.state.worker._thread
        assert worker_thread is not None
        assert worker_thread.is_alive()
        # A timed-out stop must not close storage out from under the still-owned worker.
        assert app.state.jobs.get_job(job["jobId"])["state"] == "running"
    finally:
        release.set()
        app.state.worker.stop(timeout=5)
    assert app.state.worker._thread is None
    assert app.state.jobs.get_job(job["jobId"])["state"] == "interrupted"


def test_incompatible_checkpoint_remains_inspectable_and_cannot_resume(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "incompatible-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(ServiceSettings(data_dir=data_dir, bearer_token=TOKEN))
    with TestClient(first_app) as first:
        first_app.state.worker.controls.after_checkpoint_gate.clear()
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="incompatible",
        )
        current = wait_for_job(first, headers, job["jobId"], {"running"})
        while not current["checkpointAvailable"]:
            current = wait_for_job(first, headers, job["jobId"], {"running"})

    second_app = create_app(
        ServiceSettings(data_dir=data_dir, bearer_token=TOKEN, worker_enabled=False)
    )
    with TestClient(second_app) as second:
        with second_app.state.database.session() as session:
            checkpoint = session.query(JobCheckpointRow).filter_by(job_id=job["jobId"]).one()
            checkpoint.producer_version = "incompatible-test-version"
        response = second.post(f"/api/v1/jobs/{job['jobId']}/resume", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHECKPOINT_INCOMPATIBLE"
        with second_app.state.database.session() as session:
            assert session.query(JobCheckpointRow).filter_by(job_id=job["jobId"]).count() == 1


def test_startup_reconciles_abandoned_running_attempt(tmp_path: Path) -> None:
    data_dir = tmp_path / "reconcile-data"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first_app = create_app(
        ServiceSettings(data_dir=data_dir, bearer_token=TOKEN, worker_enabled=False)
    )
    with TestClient(first_app) as first:
        imported = create_imported_project(first, headers)
        job = create_analysis_job(
            first,
            headers,
            imported["project"]["projectId"],
            imported["story"]["revision"],
            idempotency_key="abandoned",
        )
        with first_app.state.database.session() as session:
            row = session.get(JobRow, job["jobId"])
            assert row is not None
            row.state = "running"
            row.stage = "analyzing_story"

    second_app = create_app(
        ServiceSettings(data_dir=data_dir, bearer_token=TOKEN, worker_enabled=False)
    )
    with TestClient(second_app) as second:
        restored = second.get(f"/api/v1/jobs/{job['jobId']}", headers=headers).json()["job"]
        assert restored["state"] == "interrupted"
        events = second.get(f"/api/v1/jobs/{job['jobId']}/events", headers=headers).json()["events"]
        assert events[-1]["state"] == "interrupted"
