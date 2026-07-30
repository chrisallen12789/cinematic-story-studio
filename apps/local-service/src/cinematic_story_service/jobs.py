from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .analysis import analyze_story, validate_analysis_entity_limit
from .config import ServiceSettings
from .database import Database
from .document_ingest import (
    INGEST_CONTRACT_VERSION,
    PARSER_DEADLINE_SECONDS,
    DocumentExtractionRequest,
    DocumentExtractionResult,
    parser_limits_fingerprint,
)
from .errors import ServiceError, not_found
from .models import (
    DocumentExtractionRow,
    IdempotencyRow,
    JobAttemptRow,
    JobCheckpointRow,
    JobEventRow,
    JobRow,
    ParserExecutionRow,
    SourceDocumentRow,
)
from .parser_process import DocumentExtractionRunner, SpawnedDocumentExtractionRunner
from .projects import ProjectRepository
from .util import (
    ANALYZER_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    canonical_json,
    new_id,
    parse_json,
    request_fingerprint,
    sha256_text,
    utc_now,
)

_PROGRESS_SCALE = 1_000_000
_ACTIVE_STATES = {"queued", "running", "cancel_requested"}
_ACTIVE_JOB_SCOPE = "active_job"
_EXTRACTION_PRODUCER_VERSION = f"document-ingest@{INGEST_CONTRACT_VERSION}"
_EXTRACTION_TARGET_TYPE = "document_extraction"


def _progress_to_wire(value: int) -> float:
    return value / _PROGRESS_SCALE


def job_dict(row: JobRow) -> dict[str, Any]:
    result: dict[str, Any] = {
        "jobId": row.id,
        "projectId": row.project_id,
        "type": row.type,
        "state": row.state,
        "target": {
            "type": row.target_type,
            "id": row.target_id,
        },
        "inputRevision": row.input_revision,
        "inputFingerprint": row.input_fingerprint,
        "attempt": row.current_attempt,
        "stage": row.stage,
        "progress": _progress_to_wire(row.progress),
        "checkpointAvailable": row.checkpoint_available,
        "cancellationRequested": row.cancellation_requested,
        "warnings": parse_json(row.warnings_json, []),
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }
    if row.error_code is not None:
        result["error"] = {
            "code": row.error_code,
            "message": row.error_message or "The job failed.",
            "retryable": bool(row.error_retryable),
        }
    if row.terminal_at is not None:
        result["terminalAt"] = row.terminal_at
    return result


def event_dict(row: JobEventRow) -> dict[str, Any]:
    result: dict[str, Any] = {
        "jobId": row.job_id,
        "attempt": row.attempt,
        "sequence": row.sequence,
        "type": row.type,
        "createdAt": row.created_at,
    }
    if row.state is not None:
        result["state"] = row.state
    if row.stage is not None:
        result["stage"] = row.stage
    if row.progress is not None:
        result["progress"] = _progress_to_wire(row.progress)
    if row.completed_units is not None:
        result["completedUnits"] = row.completed_units
    if row.total_units is not None:
        result["totalUnits"] = row.total_units
    if row.warning_json is not None:
        result["warning"] = parse_json(row.warning_json, {})
    if row.error_code is not None:
        result["error"] = {
            "code": row.error_code,
            "message": row.error_message or "The job failed.",
            "retryable": bool(row.error_retryable),
        }
    return result


class JobRepository:
    def __init__(
        self,
        database: Database,
        projects: ProjectRepository,
        instance_id: str,
        parser_deadline_seconds: float = PARSER_DEADLINE_SECONDS,
    ) -> None:
        self.database = database
        self.projects = projects
        self.instance_id = instance_id
        self.parser_deadline_seconds = parser_deadline_seconds

    @staticmethod
    def _begin_immediate(session: Session) -> None:
        """Serialize a predicate read plus write under SQLite's one-writer contract."""

        session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def _active_job_key(
        *,
        project_id: str,
        job_type: str,
        input_revision: int,
        input_fingerprint: str,
        target_type: str,
        target_id: str | None,
    ) -> str:
        return request_fingerprint(
            {
                "projectId": project_id,
                "type": job_type,
                "targetType": target_type,
                "targetId": target_id,
                "inputRevision": input_revision,
                "inputFingerprint": input_fingerprint,
            }
        )

    def _active_key_for_job(self, job: JobRow) -> str:
        return self._active_job_key(
            project_id=job.project_id,
            job_type=job.type,
            input_revision=job.input_revision,
            input_fingerprint=job.input_fingerprint,
            target_type=job.target_type,
            target_id=job.target_id,
        )

    def _active_conflict(
        self,
        session: Session,
        *,
        project_id: str,
        job_type: str,
        input_revision: int,
        input_fingerprint: str,
        target_type: str,
        target_id: str | None,
        excluding_job_id: str | None = None,
    ) -> JobRow | None:
        statement = select(JobRow).where(
            JobRow.project_id == project_id,
            JobRow.type == job_type,
            JobRow.target_type == target_type,
            JobRow.target_id == target_id,
            JobRow.input_revision == input_revision,
            JobRow.input_fingerprint == input_fingerprint,
            JobRow.state.in_(_ACTIVE_STATES),
        )
        if excluding_job_id is not None:
            statement = statement.where(JobRow.id != excluding_job_id)
        return session.scalar(statement.order_by(JobRow.created_at, JobRow.id).limit(1))

    def _acquire_active_key(self, session: Session, job: JobRow) -> None:
        key = self._active_key_for_job(job)
        existing = session.get(IdempotencyRow, {"scope": _ACTIVE_JOB_SCOPE, "key": key})
        if existing is not None:
            if existing.resource_id == job.id:
                return
            active = session.get(JobRow, existing.resource_id)
            if active is not None and active.state in _ACTIVE_STATES:
                raise ServiceError(
                    409,
                    "JOB_ALREADY_ACTIVE",
                    "Analysis is already active for this story revision.",
                    details={"jobId": active.id},
                )
            session.delete(existing)
            session.flush()
        session.add(
            IdempotencyRow(
                scope=_ACTIVE_JOB_SCOPE,
                key=key,
                request_hash=key,
                resource_id=job.id,
                created_at=utc_now(),
            )
        )
        session.flush()

    def _release_active_key(self, session: Session, job: JobRow) -> None:
        session.execute(
            delete(IdempotencyRow).where(
                IdempotencyRow.scope == _ACTIVE_JOB_SCOPE,
                IdempotencyRow.resource_id == job.id,
            )
        )

    def reconcile_interrupted(self) -> int:
        count = 0
        with self.database.session() as session:
            job_ids = list(
                session.scalars(
                    select(JobRow.id).where(JobRow.state.in_(["running", "cancel_requested"]))
                )
            )
            for job_id in job_ids:
                job = session.get(JobRow, job_id)
                if (
                    job is not None
                    and job.state == "cancel_requested"
                    and job.cancellation_requested
                ):
                    if self._finish_cancelled(session, job.id):
                        count += 1
                    continue
                now = utc_now()
                transitioned = session.execute(
                    update(JobRow)
                    .where(
                        JobRow.id == job_id,
                        JobRow.state.in_(["running", "cancel_requested"]),
                    )
                    .values(
                        state="interrupted",
                        stage="interrupted",
                        updated_at=now,
                        terminal_at=now,
                        cancellation_requested=False,
                    )
                    .returning(JobRow.id)
                    .execution_options(synchronize_session=False)
                )
                if transitioned.scalar_one_or_none() is None:
                    continue
                job = session.get(JobRow, job_id, populate_existing=True)
                if job is None:
                    continue
                count += 1
                attempt = session.get(
                    JobAttemptRow,
                    {"job_id": job.id, "number": job.current_attempt},
                )
                if attempt is not None:
                    attempt.ended_at = now
                    attempt.outcome = "interrupted"
                self._append_extraction_parser_outcome(
                    session,
                    job,
                    outcome="interrupted",
                    error_code="EXTRACTION_INTERRUPTED",
                    error_message="Document extraction was interrupted before publication.",
                    error_retryable=True,
                    finished_at=now,
                )
                self._append_event(
                    session,
                    job,
                    event_type="state_changed",
                    state="interrupted",
                    stage="interrupted",
                    progress=job.progress,
                )
                self._release_active_key(session, job)
        return count

    def reconcile_orphaned_extractions(self) -> int:
        """Recover the durable import-to-job crash window without duplicating work."""

        existing_job = (
            select(JobRow.id)
            .where(
                JobRow.type == "extract_document",
                JobRow.target_type == _EXTRACTION_TARGET_TYPE,
                JobRow.target_id == DocumentExtractionRow.id,
            )
            .exists()
        )
        with self.database.session() as session:
            targets = list(
                session.execute(
                    select(
                        DocumentExtractionRow.id,
                        DocumentExtractionRow.project_id,
                        DocumentExtractionRow.revision,
                        DocumentExtractionRow.input_sha256,
                    )
                    .where(
                        DocumentExtractionRow.status == "pending",
                        ~existing_job,
                    )
                    .order_by(
                        DocumentExtractionRow.created_at,
                        DocumentExtractionRow.id,
                    )
                ).all()
            )

        for extraction_id, project_id, revision, input_sha256 in targets:
            self.create_extraction_job(
                project_id=project_id,
                extraction_id=extraction_id,
                input_revision=revision,
                input_fingerprint=input_sha256,
                idempotency_key=f"startup-recovery-{extraction_id}",
            )
        return len(targets)

    def create_job(
        self,
        *,
        project_id: str,
        job_type: str,
        input_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if job_type != "analyze_story":
            raise ServiceError(
                422,
                "INVALID_JOB_TYPE",
                "Document extraction jobs can only be created by the import service.",
            )
        project, story, _source = self.projects.get_story_snapshot(project_id)
        if story.revision != input_revision:
            raise ServiceError(
                409,
                "INPUT_REVISION_CONFLICT",
                "The requested story revision is not current.",
                details={"currentRevision": story.revision},
            )
        fingerprint = self._active_job_key(
            project_id=project_id,
            job_type=job_type,
            input_revision=input_revision,
            input_fingerprint=story.content_fingerprint,
            target_type="story",
            target_id=story.id,
        )
        scope = f"create_job:{project_id}"
        try:
            with self.database.session() as session:
                # Preserve schema v1 while making the active-key predicate and insert one
                # serialized DB operation. The active key itself is protected by an existing
                # composite primary key in idempotency_records.
                self._begin_immediate(session)
                existing = session.get(
                    IdempotencyRow,
                    {"scope": scope, "key": idempotency_key},
                )
                if existing is not None:
                    if existing.request_hash != fingerprint:
                        raise ServiceError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "That idempotency key was already used for another job.",
                        )
                    job = session.get(JobRow, existing.resource_id)
                    if job is None:
                        raise ServiceError(
                            500,
                            "IDEMPOTENCY_RECORD_INVALID",
                            "The saved job record is unavailable.",
                        )
                    return job_dict(job)

                active = self._active_conflict(
                    session,
                    project_id=project_id,
                    job_type=job_type,
                    input_revision=input_revision,
                    input_fingerprint=story.content_fingerprint,
                    target_type="story",
                    target_id=story.id,
                )
                if active is not None:
                    raise ServiceError(
                        409,
                        "JOB_ALREADY_ACTIVE",
                        "Analysis is already active for this story revision.",
                        details={"jobId": active.id},
                    )

                now = utc_now()
                job = JobRow(
                    id=new_id(),
                    project_id=project.id,
                    type=job_type,
                    state="queued",
                    input_revision=input_revision,
                    input_fingerprint=story.content_fingerprint,
                    target_type="story",
                    target_id=story.id,
                    payload_json=canonical_json(
                        {
                            "schemaVersion": 1,
                            "storyId": story.id,
                            "storyRevision": story.revision,
                        }
                    ),
                    current_attempt=1,
                    stage="queued",
                    progress=0,
                    checkpoint_available=False,
                    cancellation_requested=False,
                    resume_requested=False,
                    warnings_json="[]",
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
                session.flush()
                self._acquire_active_key(session, job)
                session.add(
                    JobAttemptRow(
                        job_id=job.id,
                        number=1,
                        producer_version=ANALYZER_VERSION,
                    )
                )
                session.add(
                    IdempotencyRow(
                        scope=scope,
                        key=idempotency_key,
                        request_hash=fingerprint,
                        resource_id=job.id,
                        created_at=now,
                    )
                )
                self._append_event(
                    session,
                    job,
                    event_type="created",
                    state="queued",
                    stage="queued",
                    progress=0,
                )
                return job_dict(job)
        except IntegrityError as exc:
            raise ServiceError(
                409,
                "JOB_ALREADY_ACTIVE",
                "Analysis is already active for this story revision.",
            ) from exc

    def create_extraction_job(
        self,
        *,
        project_id: str,
        extraction_id: str,
        input_revision: int,
        input_fingerprint: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create the sole persisted extraction job for an immutable extraction target."""

        scope = f"create_extraction_job:{project_id}"
        try:
            with self.database.session() as session:
                self._begin_immediate(session)
                self.projects.require_project(session, project_id)
                extraction = session.get(DocumentExtractionRow, extraction_id)
                source = (
                    session.get(SourceDocumentRow, extraction.source_document_id)
                    if extraction is not None
                    else None
                )
                if (
                    source is None
                    or source.project_id != project_id
                    or extraction is None
                    or extraction.project_id != project_id
                    or extraction.source_document_id != source.id
                ):
                    raise ServiceError(
                        422,
                        "INVALID_EXTRACTION_TARGET",
                        "The extraction target does not belong to this project source.",
                    )
                if (
                    extraction.input_sha256 != source.content_sha256
                    or input_fingerprint != source.content_sha256
                    or input_revision != extraction.revision
                ):
                    raise ServiceError(
                        409,
                        "EXTRACTION_INPUT_CHANGED",
                        "The frozen source fingerprint does not match the extraction target.",
                    )

                limits_fingerprint = parser_limits_fingerprint(self.parser_deadline_seconds)
                payload = {
                    "schemaVersion": 1,
                    "sourceDocumentId": source.id,
                    "sourceRevision": source.source_revision,
                    "extractionId": extraction.id,
                    "extractionRevision": extraction.revision,
                    "declaredFormat": extraction.format,
                    "limitsFingerprint": limits_fingerprint,
                }
                fingerprint = self._active_job_key(
                    project_id=project_id,
                    job_type="extract_document",
                    input_revision=extraction.revision,
                    input_fingerprint=source.content_sha256,
                    target_type=_EXTRACTION_TARGET_TYPE,
                    target_id=extraction.id,
                )
                existing = session.get(
                    IdempotencyRow,
                    {"scope": scope, "key": idempotency_key},
                )
                if existing is not None:
                    if existing.request_hash != fingerprint:
                        raise ServiceError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "That idempotency key was already used for another extraction.",
                        )
                    job = session.get(JobRow, existing.resource_id)
                    if job is None:
                        raise ServiceError(
                            500,
                            "IDEMPOTENCY_RECORD_INVALID",
                            "The saved extraction job is unavailable.",
                        )
                    return job_dict(job)

                prior = session.scalar(
                    select(JobRow)
                    .where(
                        JobRow.project_id == project_id,
                        JobRow.type == "extract_document",
                        JobRow.target_type == _EXTRACTION_TARGET_TYPE,
                        JobRow.target_id == extraction.id,
                    )
                    .order_by(JobRow.created_at, JobRow.id)
                    .limit(1)
                )
                if prior is not None:
                    session.add(
                        IdempotencyRow(
                            scope=scope,
                            key=idempotency_key,
                            request_hash=fingerprint,
                            resource_id=prior.id,
                            created_at=utc_now(),
                        )
                    )
                    return job_dict(prior)

                now = utc_now()
                job = JobRow(
                    id=new_id(),
                    project_id=project_id,
                    type="extract_document",
                    state="queued",
                    input_revision=extraction.revision,
                    input_fingerprint=source.content_sha256,
                    target_type=_EXTRACTION_TARGET_TYPE,
                    target_id=extraction.id,
                    payload_json=canonical_json(payload),
                    current_attempt=1,
                    stage="queued",
                    progress=0,
                    checkpoint_available=False,
                    cancellation_requested=False,
                    resume_requested=False,
                    warnings_json="[]",
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
                session.flush()
                self._acquire_active_key(session, job)
                session.add(
                    JobAttemptRow(
                        job_id=job.id,
                        number=1,
                        producer_version=_EXTRACTION_PRODUCER_VERSION,
                    )
                )
                session.add(
                    IdempotencyRow(
                        scope=scope,
                        key=idempotency_key,
                        request_hash=fingerprint,
                        resource_id=job.id,
                        created_at=now,
                    )
                )
                self._append_event(
                    session,
                    job,
                    event_type="created",
                    state="queued",
                    stage="queued",
                    progress=0,
                )
                return job_dict(job)
        except IntegrityError as exc:
            raise ServiceError(
                409,
                "JOB_ALREADY_ACTIVE",
                "Document extraction is already active for this source revision.",
            ) from exc

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            job = session.get(JobRow, job_id)
            if job is None:
                raise not_found("job")
            return job_dict(job)

    def get_events(self, job_id: str, *, after_sequence: int) -> tuple[list[dict[str, Any]], int]:
        with self.database.session() as session:
            if session.get(JobRow, job_id) is None:
                raise not_found("job")
            rows = list(
                session.scalars(
                    select(JobEventRow)
                    .where(
                        JobEventRow.job_id == job_id,
                        JobEventRow.sequence > after_sequence,
                    )
                    .order_by(JobEventRow.sequence)
                    .limit(1000)
                )
            )
            last_sequence = (
                session.scalar(
                    select(func.max(JobEventRow.sequence)).where(JobEventRow.job_id == job_id)
                )
                or 0
            )
            return [event_dict(row) for row in rows], last_sequence

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            now = utc_now()
            transitioned = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.state.in_(["queued", "running"]),
                )
                .values(
                    state="cancel_requested",
                    stage="cancelling",
                    cancellation_requested=True,
                    updated_at=now,
                )
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if transitioned.scalar_one_or_none() is not None:
                job = session.get(JobRow, job_id)
                if job is None:
                    raise not_found("job")
                self._append_event(
                    session,
                    job,
                    event_type="state_changed",
                    state="cancel_requested",
                    stage="cancelling",
                    progress=job.progress,
                )
                return job_dict(job)

            job = session.get(JobRow, job_id)
            if job is None:
                raise not_found("job")
            if job.state in {"cancel_requested", "cancelled"}:
                return job_dict(job)
            raise ServiceError(
                409,
                "JOB_STATE_CONFLICT",
                "Only queued or running work can be cancelled.",
                details={"state": job.state},
            )

    def retry(self, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self._begin_immediate(session)
            job = session.get(JobRow, job_id)
            if job is None:
                raise not_found("job")
            if job.state != "failed" or not job.error_retryable:
                raise ServiceError(
                    409,
                    "JOB_STATE_CONFLICT",
                    "This job is not in a recoverable failed state.",
                    details={"state": job.state},
                )
            active = self._active_conflict(
                session,
                project_id=job.project_id,
                job_type=job.type,
                input_revision=job.input_revision,
                input_fingerprint=job.input_fingerprint,
                target_type=job.target_type,
                target_id=job.target_id,
                excluding_job_id=job.id,
            )
            if active is not None:
                raise ServiceError(
                    409,
                    "JOB_ALREADY_ACTIVE",
                    "Analysis is already active for this story revision.",
                    details={"jobId": active.id},
                )
            queued = self._queue_new_attempt(
                session,
                job,
                expected_states=("failed",),
                resume=False,
                stage="queued_for_retry",
                require_retryable=True,
            )
            if queued is None:
                raise ServiceError(
                    409,
                    "JOB_STATE_CONFLICT",
                    "This job is not in a recoverable failed state.",
                )
            return job_dict(queued)

    def resume(self, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self._begin_immediate(session)
            job = session.get(JobRow, job_id)
            if job is None:
                raise not_found("job")
            if job.state not in {"interrupted", "paused"}:
                raise ServiceError(
                    409,
                    "JOB_STATE_CONFLICT",
                    "Only interrupted or paused work can be resumed.",
                    details={"state": job.state},
                )
            checkpoint = session.scalar(
                select(JobCheckpointRow)
                .where(JobCheckpointRow.job_id == job.id)
                .order_by(JobCheckpointRow.attempt.desc())
                .limit(1)
            )
            resume_from_checkpoint = checkpoint is not None
            if checkpoint is not None and not self._checkpoint_verified(job, checkpoint):
                raise ServiceError(
                    409,
                    "CHECKPOINT_INCOMPATIBLE",
                    "This work cannot resume after the input changed; retry it.",
                    details={"jobId": job.id},
                )
            active = self._active_conflict(
                session,
                project_id=job.project_id,
                job_type=job.type,
                input_revision=job.input_revision,
                input_fingerprint=job.input_fingerprint,
                target_type=job.target_type,
                target_id=job.target_id,
                excluding_job_id=job.id,
            )
            if active is not None:
                raise ServiceError(
                    409,
                    "JOB_ALREADY_ACTIVE",
                    "Analysis is already active for this story revision.",
                    details={"jobId": active.id},
                )
            queued = self._queue_new_attempt(
                session,
                job,
                expected_states=("interrupted", "paused"),
                resume=resume_from_checkpoint,
                stage="queued_for_resume" if resume_from_checkpoint else "queued_for_restart",
            )
            if queued is None:
                raise ServiceError(
                    409,
                    "JOB_STATE_CONFLICT",
                    "Only interrupted or paused work can be resumed.",
                )
            return job_dict(queued)

    def _queue_new_attempt(
        self,
        session: Session,
        job: JobRow,
        *,
        expected_states: tuple[str, ...],
        resume: bool,
        stage: str,
        require_retryable: bool = False,
    ) -> JobRow | None:
        now = utc_now()
        conditions = [
            JobRow.id == job.id,
            JobRow.state.in_(expected_states),
            JobRow.current_attempt == job.current_attempt,
        ]
        if require_retryable:
            conditions.append(JobRow.error_retryable.is_(True))
        transitioned = session.execute(
            update(JobRow)
            .where(*conditions)
            .values(
                current_attempt=JobRow.current_attempt + 1,
                state="queued",
                stage=stage,
                progress=0,
                cancellation_requested=False,
                resume_requested=resume,
                error_code=None,
                error_message=None,
                error_retryable=None,
                updated_at=now,
                terminal_at=None,
            )
            .returning(JobRow.id)
            .execution_options(synchronize_session=False)
        )
        if transitioned.scalar_one_or_none() is None:
            return None
        queued = session.get(JobRow, job.id, populate_existing=True)
        if queued is None:
            return None
        self._acquire_active_key(session, queued)
        session.add(
            JobAttemptRow(
                job_id=queued.id,
                number=queued.current_attempt,
                producer_version=(
                    _EXTRACTION_PRODUCER_VERSION
                    if queued.type == "extract_document"
                    else ANALYZER_VERSION
                ),
            )
        )
        self._append_event(
            session,
            queued,
            event_type="state_changed",
            state="queued",
            stage=stage,
            progress=0,
        )
        return queued

    def settle_pending_cancellation(self) -> bool:
        with self.database.session() as session:
            job = session.scalar(
                select(JobRow)
                .where(JobRow.state == "cancel_requested")
                .order_by(JobRow.created_at, JobRow.id)
                .limit(1)
            )
            if job is None:
                return False
            return self._finish_cancelled(session, job.id)

    def claim_next(self) -> dict[str, Any] | None:
        with self.database.session() as session:
            candidate_id = (
                select(JobRow)
                .where(JobRow.state == "queued")
                .order_by(JobRow.created_at, JobRow.id)
                .limit(1)
                .with_only_columns(JobRow.id)
                .scalar_subquery()
            )
            now = utc_now()
            claimed = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == candidate_id,
                    JobRow.state == "queued",
                )
                .values(
                    state="running",
                    stage="starting",
                    progress=case(
                        (JobRow.progress < 50_000, 50_000),
                        else_=JobRow.progress,
                    ),
                    updated_at=now,
                )
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            job_id = claimed.scalar_one_or_none()
            if job_id is None:
                return None
            job = session.get(JobRow, job_id, populate_existing=True)
            if job is None:
                return None
            attempt = session.get(JobAttemptRow, {"job_id": job.id, "number": job.current_attempt})
            if attempt is None:
                raise ServiceError(500, "JOB_ATTEMPT_MISSING", "The job attempt is unavailable.")
            attempt.worker_instance_id = self.instance_id
            attempt.started_at = now
            if job.type == "extract_document" and job.target_id is not None:
                extraction = session.get(DocumentExtractionRow, job.target_id)
                if (
                    extraction is not None
                    and extraction.project_id == job.project_id
                    and extraction.status in {"pending", "running", "failed"}
                ):
                    extraction.status = "running"
                    extraction.updated_at = now
                    source = session.get(
                        SourceDocumentRow,
                        extraction.source_document_id,
                    )
                    if source is not None and source.project_id == job.project_id:
                        source.extraction_status = "running"
            self._append_event(
                session,
                job,
                event_type="state_changed",
                state="running",
                stage="starting",
                progress=job.progress,
            )
            return job_dict(job)

    def should_cancel(self, job_id: str) -> bool:
        with self.database.session() as session:
            job = session.get(JobRow, job_id)
            return job is None or job.state == "cancel_requested" or job.cancellation_requested

    def update_progress(
        self,
        job_id: str,
        *,
        stage: str,
        progress: float,
        completed_units: int | None = None,
        total_units: int | None = None,
    ) -> bool:
        scaled = round(progress * _PROGRESS_SCALE)
        if scaled < 0 or scaled >= _PROGRESS_SCALE:
            raise ServiceError(
                500,
                "INVALID_JOB_PROGRESS",
                "The worker produced invalid progress.",
            )
        with self.database.session() as session:
            now = utc_now()
            transitioned = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.state == "running",
                    JobRow.cancellation_requested.is_(False),
                    JobRow.progress <= scaled,
                )
                .values(progress=scaled, stage=stage, updated_at=now)
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if transitioned.scalar_one_or_none() is not None:
                job = session.get(JobRow, job_id, populate_existing=True)
                if job is None:
                    return False
                self._append_event(
                    session,
                    job,
                    event_type="progress",
                    state="running",
                    stage=stage,
                    progress=scaled,
                    completed_units=completed_units,
                    total_units=total_units,
                )
                return True

            job = session.get(JobRow, job_id, populate_existing=True)
            if job is None:
                return False
            if job.state == "cancel_requested" or job.cancellation_requested:
                self._finish_cancelled(session, job.id)
                return False
            if job.state == "running" and scaled < job.progress:
                raise ServiceError(
                    500,
                    "INVALID_JOB_PROGRESS",
                    "The worker produced invalid progress.",
                )
            return False

    def save_checkpoint(self, job_id: str, payload: dict[str, Any]) -> bool:
        payload_json = canonical_json(payload)
        payload_sha256 = sha256_text(payload_json)
        with self.database.session() as session:
            self._begin_immediate(session)
            job = session.get(JobRow, job_id)
            if job is None:
                return False
            if job.state == "cancel_requested" or job.cancellation_requested:
                self._finish_cancelled(session, job.id)
                return False
            if job.state != "running":
                return False
            existing = session.get(
                JobCheckpointRow,
                {"job_id": job.id, "attempt": job.current_attempt},
            )
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise ServiceError(
                        409,
                        "CHECKPOINT_CONFLICT",
                        "A different checkpoint already exists for this attempt.",
                    )
                return True
            progress = max(job.progress, 650_000)
            now = utc_now()
            transitioned = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job.id,
                    JobRow.state == "running",
                    JobRow.cancellation_requested.is_(False),
                    JobRow.current_attempt == job.current_attempt,
                )
                .values(
                    checkpoint_available=True,
                    stage="checkpointed",
                    progress=progress,
                    updated_at=now,
                )
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if transitioned.scalar_one_or_none() is None:
                return False
            job = session.get(JobRow, job.id, populate_existing=True)
            if job is None:
                return False
            sequence = self._next_sequence(session, job.id)
            checkpoint = JobCheckpointRow(
                job_id=job.id,
                attempt=job.current_attempt,
                sequence=sequence,
                checkpoint_type="analysis_projection",
                schema_version=CHECKPOINT_SCHEMA_VERSION,
                input_revision=job.input_revision,
                input_fingerprint=job.input_fingerprint,
                producer_version=ANALYZER_VERSION,
                payload_json=payload_json,
                payload_sha256=payload_sha256,
                created_at=now,
            )
            session.add(checkpoint)
            session.add(
                JobEventRow(
                    job_id=job.id,
                    sequence=sequence,
                    attempt=job.current_attempt,
                    type="checkpoint",
                    state="running",
                    stage="checkpointed",
                    progress=progress,
                    created_at=now,
                )
            )
            return True

    def load_resume_checkpoint(self, job_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            job = session.get(JobRow, job_id)
            if job is None or not job.resume_requested:
                return None
            checkpoint = session.scalar(
                select(JobCheckpointRow)
                .where(JobCheckpointRow.job_id == job.id)
                .order_by(JobCheckpointRow.attempt.desc())
                .limit(1)
            )
            if checkpoint is None or not self._checkpoint_verified(job, checkpoint):
                raise ServiceError(
                    409,
                    "CHECKPOINT_INCOMPATIBLE",
                    "The saved analysis checkpoint is incompatible.",
                )
            value = parse_json(checkpoint.payload_json, {})
            if not isinstance(value, dict):
                raise ServiceError(
                    409,
                    "CHECKPOINT_INCOMPATIBLE",
                    "The saved analysis checkpoint failed verification.",
                )
            return value

    @staticmethod
    def _checkpoint_compatible(job: JobRow, checkpoint: JobCheckpointRow) -> bool:
        return (
            checkpoint.checkpoint_type == "analysis_projection"
            and checkpoint.schema_version == CHECKPOINT_SCHEMA_VERSION
            and checkpoint.input_revision == job.input_revision
            and checkpoint.input_fingerprint == job.input_fingerprint
            and checkpoint.producer_version == ANALYZER_VERSION
        )

    @classmethod
    def _checkpoint_verified(cls, job: JobRow, checkpoint: JobCheckpointRow) -> bool:
        if (
            not cls._checkpoint_compatible(job, checkpoint)
            or sha256_text(checkpoint.payload_json) != checkpoint.payload_sha256
        ):
            return False
        try:
            value = parse_json(checkpoint.payload_json, {})
        except (TypeError, ValueError):
            return False
        return isinstance(value, dict)

    def _append_extraction_parser_outcome(
        self,
        session: Session,
        job: JobRow,
        *,
        outcome: str,
        error_code: str | None,
        error_message: str | None,
        error_retryable: bool | None,
        finished_at: str,
    ) -> None:
        if job.type != "extract_document" or job.target_id is None:
            return
        existing = session.scalar(
            select(ParserExecutionRow).where(
                ParserExecutionRow.job_id == job.id,
                ParserExecutionRow.attempt == job.current_attempt,
            )
        )
        if existing is not None:
            return
        extraction = session.get(DocumentExtractionRow, job.target_id)
        if extraction is None or extraction.project_id != job.project_id:
            return
        payload = parse_json(job.payload_json, {})
        limits_fingerprint = payload.get("limitsFingerprint") if isinstance(payload, dict) else None
        if not isinstance(limits_fingerprint, str) or len(limits_fingerprint) != 64:
            limits_fingerprint = parser_limits_fingerprint(self.parser_deadline_seconds)
        attempt = session.get(
            JobAttemptRow,
            {"job_id": job.id, "number": job.current_attempt},
        )
        started_at = (
            attempt.started_at
            if attempt is not None and attempt.started_at is not None
            else job.created_at
        )
        session.add(
            ParserExecutionRow(
                id=new_id(),
                project_id=job.project_id,
                source_document_id=extraction.source_document_id,
                extraction_id=extraction.id,
                job_id=job.id,
                attempt=job.current_attempt,
                parser_name="document-ingest",
                parser_version=INGEST_CONTRACT_VERSION,
                outcome=outcome,
                input_sha256=job.input_fingerprint,
                limits_fingerprint=limits_fingerprint,
                output_text_sha256=None,
                manifest_json="{}",
                sections_json="[]",
                source_mappings_json="[]",
                warnings_json=job.warnings_json,
                error_code=error_code,
                error_message=error_message,
                error_retryable=error_retryable,
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        source = session.get(SourceDocumentRow, extraction.source_document_id)
        if outcome in {"failed", "cancelled"}:
            extraction.status = "failed"
            extraction.updated_at = finished_at
            if source is not None and source.project_id == job.project_id:
                source.extraction_status = "failed"
        elif outcome == "interrupted" and extraction.status not in {
            "complete",
            "partial",
        }:
            extraction.status = "pending"
            extraction.updated_at = finished_at
            if source is not None and source.project_id == job.project_id:
                source.extraction_status = "pending"

    def finish_success(self, job_id: str) -> None:
        with self.database.session() as session:
            now = utc_now()
            transitioned = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.state == "running",
                    JobRow.cancellation_requested.is_(False),
                )
                .values(
                    state="succeeded",
                    stage="completed",
                    progress=_PROGRESS_SCALE,
                    cancellation_requested=False,
                    resume_requested=False,
                    updated_at=now,
                    terminal_at=now,
                )
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if transitioned.scalar_one_or_none() is None:
                job = session.get(JobRow, job_id, populate_existing=True)
                if job is not None and (
                    job.state == "cancel_requested" or job.cancellation_requested
                ):
                    self._finish_cancelled(session, job.id)
                return
            job = session.get(JobRow, job_id, populate_existing=True)
            if job is None:
                return
            attempt = session.get(JobAttemptRow, {"job_id": job.id, "number": job.current_attempt})
            if attempt is not None:
                attempt.ended_at = now
                attempt.outcome = "succeeded"
            self._append_event(
                session,
                job,
                event_type="completed",
                state="succeeded",
                stage="completed",
                progress=_PROGRESS_SCALE,
                completed_units=1,
                total_units=1,
            )
            self._release_active_key(session, job)

    def publish_extraction_and_finish(
        self,
        job_id: str,
        *,
        result: DocumentExtractionResult,
        after_write_claim: Callable[[], bool] | None = None,
    ) -> bool:
        """Atomically publish an extraction and complete its persisted job attempt."""

        with self.database.session() as session:
            write_claim = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.type == "extract_document",
                    JobRow.target_type == _EXTRACTION_TARGET_TYPE,
                    JobRow.state == "running",
                    JobRow.cancellation_requested.is_(False),
                )
                .values(updated_at=JobRow.updated_at)
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if write_claim.scalar_one_or_none() is None:
                job = session.get(JobRow, job_id, populate_existing=True)
                if job is not None and (
                    job.state == "cancel_requested" or job.cancellation_requested
                ):
                    self._finish_cancelled(session, job.id)
                return False
            if after_write_claim is not None and not after_write_claim():
                return False

            job = session.get(JobRow, job_id)
            if job is None or job.target_id is None:
                return False
            if (
                job.input_fingerprint != result.source_sha256
                or result.contract_version != INGEST_CONTRACT_VERSION
            ):
                raise ServiceError(
                    409,
                    "EXTRACTION_INPUT_CHANGED",
                    "The frozen extraction input no longer matches the job.",
                )
            payload = parse_json(job.payload_json, {})
            expected_limits_fingerprint = (
                payload.get("limitsFingerprint") if isinstance(payload, dict) else None
            )
            if expected_limits_fingerprint != result.parser_execution.limits_fingerprint:
                raise ServiceError(
                    409,
                    "EXTRACTION_LIMITS_CHANGED",
                    "The frozen parser limits no longer match the extraction result.",
                )

            self.projects.publish_extraction(
                job_id=job.id,
                result=result,
                session=session,
            )
            now = utc_now()
            job.state = "succeeded"
            job.stage = "completed"
            job.progress = _PROGRESS_SCALE
            job.cancellation_requested = False
            job.resume_requested = False
            job.updated_at = now
            job.terminal_at = now
            attempt = session.get(
                JobAttemptRow,
                {"job_id": job.id, "number": job.current_attempt},
            )
            if attempt is not None:
                attempt.ended_at = now
                attempt.outcome = "succeeded"
            self._append_event(
                session,
                job,
                event_type="completed",
                state="succeeded",
                stage="completed",
                progress=_PROGRESS_SCALE,
                completed_units=1,
                total_units=1,
            )
            self._release_active_key(session, job)
            return True

    def publish_analysis_and_finish(
        self,
        job_id: str,
        *,
        project_id: str,
        analysis: dict[str, Any],
        after_write_claim: Callable[[], bool] | None = None,
    ) -> bool:
        """Atomically publish analysis and transition its job attempt to succeeded.

        A cancellation committed before this transaction wins. Once this short SQLite write
        transaction starts, a cancellation waits and then observes a succeeded job, so no API
        state can claim cancellation while published projections exist.
        """

        validate_analysis_entity_limit(analysis)
        with self.database.session() as session:
            write_claim = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.project_id == project_id,
                    JobRow.state == "running",
                    JobRow.cancellation_requested.is_(False),
                )
                .values(updated_at=JobRow.updated_at)
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if write_claim.scalar_one_or_none() is None:
                job = session.get(JobRow, job_id, populate_existing=True)
                if job is not None and (
                    job.state == "cancel_requested" or job.cancellation_requested
                ):
                    self._finish_cancelled(session, job.id)
                return False
            if after_write_claim is not None and not after_write_claim():
                return False

            job = session.get(JobRow, job_id)
            if job is None:
                return False
            if (
                job.project_id != project_id
                or job.input_revision != analysis.get("inputRevision")
                or job.input_fingerprint != analysis.get("inputFingerprint")
            ):
                raise ServiceError(
                    409,
                    "ANALYSIS_INPUT_CHANGED",
                    "The frozen analysis input no longer matches the job.",
                )

            self.projects.publish_analysis(
                project_id=project_id,
                analysis=analysis,
                session=session,
            )
            now = utc_now()
            job.state = "succeeded"
            job.stage = "completed"
            job.progress = _PROGRESS_SCALE
            job.cancellation_requested = False
            job.resume_requested = False
            job.updated_at = now
            job.terminal_at = now
            attempt = session.get(
                JobAttemptRow,
                {"job_id": job.id, "number": job.current_attempt},
            )
            if attempt is not None:
                attempt.ended_at = now
                attempt.outcome = "succeeded"
            self._append_event(
                session,
                job,
                event_type="completed",
                state="succeeded",
                stage="completed",
                progress=_PROGRESS_SCALE,
                completed_units=1,
                total_units=1,
            )
            self._release_active_key(session, job)
            return True

    def finish_failed(
        self,
        job_id: str,
        *,
        code: str = "ANALYSIS_FAILED",
        message: str = "Story analysis could not be completed.",
        retryable: bool = True,
    ) -> None:
        with self.database.session() as session:
            now = utc_now()
            transitioned = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.state == "running",
                    JobRow.cancellation_requested.is_(False),
                )
                .values(
                    state="failed",
                    stage="failed",
                    error_code=code,
                    error_message=message,
                    error_retryable=retryable,
                    updated_at=now,
                    terminal_at=now,
                )
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if transitioned.scalar_one_or_none() is None:
                job = session.get(JobRow, job_id, populate_existing=True)
                if job is not None and (
                    job.state == "cancel_requested" or job.cancellation_requested
                ):
                    self._finish_cancelled(session, job.id)
                return
            job = session.get(JobRow, job_id, populate_existing=True)
            if job is None:
                return
            attempt = session.get(JobAttemptRow, {"job_id": job.id, "number": job.current_attempt})
            if attempt is not None:
                attempt.ended_at = now
                attempt.outcome = "failed"
                attempt.error_code = code
                attempt.error_message = message
            self._append_extraction_parser_outcome(
                session,
                job,
                outcome="failed",
                error_code=code,
                error_message=message,
                error_retryable=retryable,
                finished_at=now,
            )
            self._append_event(
                session,
                job,
                event_type="failed",
                state="failed",
                stage="failed",
                progress=job.progress,
                error_code=code,
                error_message=message,
                error_retryable=retryable,
            )
            self._release_active_key(session, job)

    def interrupt_active(self, job_id: str) -> None:
        with self.database.session() as session:
            job = session.get(JobRow, job_id)
            if job is not None and job.state == "cancel_requested" and job.cancellation_requested:
                self._finish_cancelled(session, job.id)
                return
            now = utc_now()
            transitioned = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.state == "running",
                )
                .values(
                    state="interrupted",
                    stage="interrupted",
                    cancellation_requested=False,
                    updated_at=now,
                    terminal_at=now,
                )
                .returning(JobRow.id)
                .execution_options(synchronize_session=False)
            )
            if transitioned.scalar_one_or_none() is None:
                return
            job = session.get(JobRow, job_id, populate_existing=True)
            if job is None:
                return
            attempt = session.get(JobAttemptRow, {"job_id": job.id, "number": job.current_attempt})
            if attempt is not None:
                attempt.ended_at = now
                attempt.outcome = "interrupted"
            self._append_extraction_parser_outcome(
                session,
                job,
                outcome="interrupted",
                error_code="EXTRACTION_INTERRUPTED",
                error_message="Document extraction was interrupted before publication.",
                error_retryable=True,
                finished_at=now,
            )
            self._append_event(
                session,
                job,
                event_type="state_changed",
                state="interrupted",
                stage="interrupted",
                progress=job.progress,
            )
            self._release_active_key(session, job)

    def _finish_cancelled(self, session: Session, job_id: str) -> bool:
        now = utc_now()
        transitioned = session.execute(
            update(JobRow)
            .where(
                JobRow.id == job_id,
                JobRow.state == "cancel_requested",
                JobRow.cancellation_requested.is_(True),
            )
            .values(
                state="cancelled",
                stage="cancelled",
                cancellation_requested=True,
                updated_at=now,
                terminal_at=now,
            )
            .returning(JobRow.id)
            .execution_options(synchronize_session=False)
        )
        if transitioned.scalar_one_or_none() is None:
            return False
        job = session.get(JobRow, job_id, populate_existing=True)
        if job is None:
            return False
        attempt = session.get(JobAttemptRow, {"job_id": job.id, "number": job.current_attempt})
        if attempt is not None:
            attempt.ended_at = now
            attempt.outcome = "cancelled"
        self._append_extraction_parser_outcome(
            session,
            job,
            outcome="cancelled",
            error_code="EXTRACTION_CANCELLED",
            error_message="Document extraction was cancelled.",
            error_retryable=False,
            finished_at=now,
        )
        self._append_event(
            session,
            job,
            event_type="state_changed",
            state="cancelled",
            stage="cancelled",
            progress=job.progress,
        )
        self._release_active_key(session, job)
        return True

    def _next_sequence(self, session: Session, job_id: str) -> int:
        # The no-op row write obtains SQLite's writer lock before MAX is observed. With the
        # process-lifetime data-directory lock, this makes allocation atomic without a schema-v2
        # counter column. A later allocator cannot read until this event transaction commits.
        owned = session.execute(
            update(JobRow)
            .where(JobRow.id == job_id)
            .values(updated_at=JobRow.updated_at)
            .returning(JobRow.id)
            .execution_options(synchronize_session=False)
        )
        if owned.scalar_one_or_none() is None:
            raise ServiceError(500, "JOB_EVENT_ORPHANED", "The job event owner is unavailable.")
        session.flush()
        return (
            session.scalar(
                select(func.max(JobEventRow.sequence)).where(JobEventRow.job_id == job_id)
            )
            or 0
        ) + 1

    def _append_event(
        self,
        session: Session,
        job: JobRow,
        *,
        event_type: str,
        state: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        completed_units: int | None = None,
        total_units: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        error_retryable: bool | None = None,
    ) -> None:
        session.add(
            JobEventRow(
                job_id=job.id,
                sequence=self._next_sequence(session, job.id),
                attempt=job.current_attempt,
                type=event_type,
                state=state,
                stage=stage,
                progress=progress,
                completed_units=completed_units,
                total_units=total_units,
                error_code=error_code,
                error_message=error_message,
                error_retryable=error_retryable,
                created_at=utc_now(),
            )
        )


@dataclass(slots=True)
class WorkerControls:
    claim_gate: threading.Event
    execution_gate: threading.Event
    after_checkpoint_gate: threading.Event
    before_publication_gate: threading.Event
    publication_claim_gate: threading.Event
    publication_claimed: threading.Event


class JobWorker:
    """One bounded in-process worker for the Phase 0 persisted queue."""

    def __init__(
        self,
        settings: ServiceSettings,
        jobs: JobRepository,
        projects: ProjectRepository,
        parser_runner: DocumentExtractionRunner | None = None,
    ) -> None:
        self.settings = settings
        self.jobs = jobs
        self.projects = projects
        self.parser_runner = parser_runner or SpawnedDocumentExtractionRunner(
            poll_seconds=settings.worker_poll_seconds
        )
        self.controls = WorkerControls(
            claim_gate=threading.Event(),
            execution_gate=threading.Event(),
            after_checkpoint_gate=threading.Event(),
            before_publication_gate=threading.Event(),
            publication_claim_gate=threading.Event(),
            publication_claimed=threading.Event(),
        )
        self.controls.claim_gate.set()
        self.controls.execution_gate.set()
        self.controls.after_checkpoint_gate.set()
        self.controls.before_publication_gate.set()
        self.controls.publication_claim_gate.set()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_job_id: str | None = None
        self._failures_to_inject = 0
        self._failure_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="cinematic-story-job-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        self.controls.claim_gate.set()
        self.controls.execution_gate.set()
        self.controls.after_checkpoint_gate.set()
        self.controls.before_publication_gate.set()
        self.controls.publication_claim_gate.set()
        thread = self._thread
        if thread is None:
            return
        active_job_id = self._active_job_id
        thread.join(timeout)
        if thread.is_alive():
            # Keep both the thread reference and Database lifetime lock owned. The app lifespan
            # deliberately skips database.close() when this exception escapes.
            raise ServiceError(
                503,
                "WORKER_STOP_TIMEOUT",
                "The background worker did not reach a safe stop boundary.",
                retryable=True,
            )
        if active_job_id is not None:
            self.jobs.interrupt_active(active_job_id)
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def fail_next_attempt(self) -> None:
        with self._failure_lock:
            self._failures_to_inject += 1
        self.wake()

    def _consume_injected_failure(self) -> bool:
        with self._failure_lock:
            if self._failures_to_inject == 0:
                return False
            self._failures_to_inject -= 1
            return True

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.jobs.settle_pending_cancellation():
                continue
            if not self.controls.claim_gate.wait(self.settings.worker_poll_seconds):
                continue
            if self._stop.is_set():
                break
            claimed = self.jobs.claim_next()
            if claimed is None:
                self._wake.wait(self.settings.worker_poll_seconds)
                self._wake.clear()
                continue
            self._active_job_id = claimed["jobId"]
            try:
                if claimed["type"] == "extract_document":
                    self._run_extraction(claimed)
                elif claimed["type"] == "analyze_story":
                    self._run_analysis(claimed)
                else:
                    self.jobs.finish_failed(
                        claimed["jobId"],
                        code="JOB_TYPE_UNSUPPORTED",
                        message="The queued job type is not supported.",
                        retryable=False,
                    )
            finally:
                self._active_job_id = None

    def _wait_at_boundary(self, gate: threading.Event, job_id: str) -> bool:
        while not gate.wait(self.settings.worker_poll_seconds):
            if self._stop.is_set():
                self.jobs.interrupt_active(job_id)
                return False
            if self.jobs.should_cancel(job_id):
                # A progress safe-point atomically settles the cancellation.
                self.jobs.update_progress(job_id, stage="cancelling", progress=0.05)
                return False
        if self._stop.is_set():
            self.jobs.interrupt_active(job_id)
            return False
        if self.jobs.should_cancel(job_id):
            self.jobs.update_progress(job_id, stage="cancelling", progress=0.05)
            return False
        return True

    def _continue_after_bounded_work(self, job_id: str) -> bool:
        if self._stop.is_set():
            self.jobs.interrupt_active(job_id)
            return False
        if self.jobs.should_cancel(job_id):
            self.jobs.update_progress(job_id, stage="cancelling", progress=0.05)
            return False
        return True

    def _run_extraction(self, claimed: dict[str, Any]) -> None:
        job_id = claimed["jobId"]
        try:
            if not self._wait_at_boundary(self.controls.execution_gate, job_id):
                return
            if not self.jobs.update_progress(
                job_id,
                stage="loading_source",
                progress=0.1,
                completed_units=0,
                total_units=3,
            ):
                return

            target = claimed.get("target")
            if not isinstance(target, dict):
                self.jobs.finish_failed(
                    job_id,
                    code="EXTRACTION_TARGET_INVALID",
                    message="The frozen extraction target is unavailable.",
                    retryable=False,
                )
                return
            extraction_id = target.get("id")
            if not isinstance(extraction_id, str) or target.get("type") != _EXTRACTION_TARGET_TYPE:
                self.jobs.finish_failed(
                    job_id,
                    code="EXTRACTION_TARGET_INVALID",
                    message="The frozen extraction target is unavailable.",
                    retryable=False,
                )
                return
            extraction_input = self.projects.get_extraction_input(extraction_id)
            source_sha256 = getattr(
                extraction_input,
                "source_sha256",
                getattr(extraction_input, "content_sha256", None),
            )
            source_byte_count = getattr(
                extraction_input,
                "source_byte_count",
                getattr(extraction_input, "byte_length", None),
            )
            if (
                getattr(extraction_input, "project_id", None) != claimed["projectId"]
                or getattr(extraction_input, "extraction_id", None) != extraction_id
                or getattr(extraction_input, "extraction_revision", None)
                != claimed["inputRevision"]
                or source_sha256 != claimed["inputFingerprint"]
                or not isinstance(source_byte_count, int)
            ):
                self.jobs.finish_failed(
                    job_id,
                    code="EXTRACTION_INPUT_CHANGED",
                    message="The frozen extraction input no longer matches the job.",
                    retryable=False,
                )
                return
            request = DocumentExtractionRequest(
                contract_version=INGEST_CONTRACT_VERSION,
                source_path=extraction_input.source_path,
                display_name=extraction_input.display_name,
                declared_format=extraction_input.declared_format,
                source_sha256=source_sha256,
                source_byte_count=source_byte_count,
                deadline_seconds=self.settings.parser_deadline_seconds,
            )

            def cancelled() -> bool:
                return self._stop.is_set() or self.jobs.should_cancel(job_id)

            def progress(stage: str, scaled_progress: int) -> None:
                if not 0 <= scaled_progress < _PROGRESS_SCALE:
                    raise ServiceError(
                        500,
                        "INVALID_JOB_PROGRESS",
                        "The parser produced invalid progress.",
                    )
                self.jobs.update_progress(
                    job_id,
                    stage=stage,
                    progress=scaled_progress / _PROGRESS_SCALE,
                )

            result = self.parser_runner.run(
                request,
                cancelled=cancelled,
                progress=progress,
            )
            if not self._continue_after_bounded_work(job_id):
                return
            if self._consume_injected_failure():
                self.jobs.finish_failed(
                    job_id,
                    code="EXTRACTION_FAILED",
                    message="Document extraction could not be completed safely.",
                    retryable=True,
                )
                return
            if not self.jobs.update_progress(
                job_id,
                stage="publishing_extraction",
                progress=0.95,
                completed_units=2,
                total_units=3,
            ):
                return
            if not self._wait_at_boundary(self.controls.before_publication_gate, job_id):
                return
            self.controls.publication_claimed.clear()
            published = self.jobs.publish_extraction_and_finish(
                job_id,
                result=result,
                after_write_claim=self._wait_after_publication_claim,
            )
            if not published and self._stop.is_set():
                self.jobs.interrupt_active(job_id)
        except ServiceError as exc:
            if exc.code == "EXTRACTION_CANCELLED":
                if self._stop.is_set():
                    self.jobs.interrupt_active(job_id)
                elif self.jobs.should_cancel(job_id):
                    self.jobs.update_progress(
                        job_id,
                        stage="cancelling",
                        progress=0.99,
                    )
                else:
                    self.jobs.finish_failed(
                        job_id,
                        code=exc.code,
                        message="Document extraction could not be completed safely.",
                        retryable=exc.retryable,
                    )
                return
            self.jobs.finish_failed(
                job_id,
                code=exc.code,
                message="Document extraction could not be completed safely.",
                retryable=exc.retryable,
            )
        except Exception:
            # Paths, source content, and parser exception strings are deliberately excluded.
            self.jobs.finish_failed(
                job_id,
                code="EXTRACTION_FAILED",
                message="Document extraction could not be completed safely.",
                retryable=True,
            )

    def _run_analysis(self, claimed: dict[str, Any]) -> None:
        job_id = claimed["jobId"]
        project_id = claimed["projectId"]
        try:
            if not self._wait_at_boundary(self.controls.execution_gate, job_id):
                return
            if not self.jobs.update_progress(
                job_id,
                stage="loading_story",
                progress=0.15,
                completed_units=0,
                total_units=3,
            ):
                return

            analysis = self.jobs.load_resume_checkpoint(job_id)
            if not self._continue_after_bounded_work(job_id):
                return
            if analysis is None:
                _project, story, source = self.projects.get_story_snapshot(project_id)
                if not self._continue_after_bounded_work(job_id):
                    return
                if (
                    story.revision != claimed["inputRevision"]
                    or story.content_fingerprint != claimed["inputFingerprint"]
                ):
                    self.jobs.finish_failed(
                        job_id,
                        code="ANALYSIS_INPUT_CHANGED",
                        message="The story changed before analysis started.",
                        retryable=False,
                    )
                    return
                if not self.jobs.update_progress(
                    job_id,
                    stage="analyzing_story",
                    progress=0.35,
                    completed_units=1,
                    total_units=3,
                ):
                    return
                if not self._continue_after_bounded_work(job_id):
                    return
                analysis = analyze_story(
                    project_id=project_id,
                    story_id=story.id,
                    story_revision=story.revision,
                    source_document_id=source.id,
                    text=story.exact_text,
                    input_fingerprint=story.content_fingerprint,
                    recorded_at=utc_now(),
                )
                if not self._continue_after_bounded_work(job_id):
                    return
                validate_analysis_entity_limit(analysis)
                if not self.jobs.save_checkpoint(job_id, analysis):
                    return
            else:
                validate_analysis_entity_limit(analysis)
                if not self.jobs.update_progress(
                    job_id,
                    stage="checkpoint_restored",
                    progress=0.65,
                    completed_units=2,
                    total_units=3,
                ):
                    return

            if not self._wait_at_boundary(self.controls.after_checkpoint_gate, job_id):
                return
            if self._consume_injected_failure():
                self.jobs.finish_failed(job_id)
                return
            if not self.jobs.update_progress(
                job_id,
                stage="publishing_analysis",
                progress=0.85,
                completed_units=2,
                total_units=3,
            ):
                return
            if not self._wait_at_boundary(self.controls.before_publication_gate, job_id):
                return
            self.controls.publication_claimed.clear()
            published = self.jobs.publish_analysis_and_finish(
                job_id,
                project_id=project_id,
                analysis=analysis,
                after_write_claim=self._wait_after_publication_claim,
            )
            if not published and self._stop.is_set():
                self.jobs.interrupt_active(job_id)
        except ServiceError as exc:
            self.jobs.finish_failed(
                job_id,
                code=exc.code,
                message="Story analysis could not be completed safely.",
                retryable=exc.retryable,
            )
        except Exception:
            # Provider/source content and exception strings are deliberately excluded.
            self.jobs.finish_failed(job_id)

    def _wait_after_publication_claim(self) -> bool:
        self.controls.publication_claimed.set()
        while not self.controls.publication_claim_gate.wait(self.settings.worker_poll_seconds):
            if self._stop.is_set():
                return False
        return not self._stop.is_set()
