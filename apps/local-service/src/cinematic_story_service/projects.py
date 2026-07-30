from __future__ import annotations

import base64
import hashlib
import os
import stat
import threading
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .config import ServiceSettings
from .database import Database
from .document_ingest import (
    IMPORT_PREVIEW_CHARACTERS,
    DocumentExtractionResult,
    SupportedFormat,
    probe_document,
    validate_plain_text_source,
)
from .errors import ServiceError, not_found
from .models import (
    ChapterRow,
    CharacterRow,
    DialogueAttributionRow,
    DialogueLineRow,
    DocumentExtractionRow,
    HumanCorrectionRow,
    IdempotencyRow,
    ImportedStoryRow,
    ImportReviewRow,
    JobRow,
    ParserExecutionRow,
    ProjectRow,
    SceneRow,
    SourceDocumentRow,
    StoryBeatRow,
)
from .util import (
    SCHEMA_VERSION,
    canonical_json,
    ensure_private_directory,
    new_id,
    parse_json,
    provenance,
    request_fingerprint,
    resolve_beneath,
    safe_display_filename,
    sha256_text,
    text_span,
    utc_now,
)

_IMPORT_CHUNK_BYTES = 64 * 1024
_SUPPORTED_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/epub+zip",
    "application/pdf",
}
_MEDIA_TYPES_BY_FORMAT = {
    "txt": "text/plain",
    "markdown": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
}


@dataclass(frozen=True, slots=True)
class ImportResult:
    source_document: dict[str, Any]
    extraction: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExtractionJobInput:
    project_id: str
    source_document_id: str
    extraction_id: str
    extraction_revision: int
    source_path: Path
    display_name: str
    declared_format: SupportedFormat
    source_sha256: str
    source_byte_count: int


def _project_provenance(row: ProjectRow) -> dict[str, Any]:
    return provenance(
        origin="system",
        actor_id="project-service@1.0.0",
        recorded_at=row.created_at,
        notes="Created in the private local project catalog.",
    )


def _project_dict(session: Session, row: ProjectRow) -> dict[str, Any]:
    source_ids = list(
        session.scalars(
            select(SourceDocumentRow.id)
            .where(SourceDocumentRow.project_id == row.id)
            .order_by(SourceDocumentRow.imported_at, SourceDocumentRow.id)
        )
    )
    approval_ids = list(
        session.scalars(
            select(ImportReviewRow.decision_id)
            .where(
                ImportReviewRow.project_id == row.id,
                ImportReviewRow.decision_id.is_not(None),
            )
            .order_by(ImportReviewRow.created_at, ImportReviewRow.id)
        )
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": row.revision,
        "provenance": _project_provenance(row),
        "projectId": row.id,
        "name": row.name,
        "status": row.status,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
        "storyId": row.story_id,
        "sourceDocumentIds": source_ids,
        "activeTimelineId": None,
        "approvalDecisionIds": approval_ids,
        "dataClassification": "private_local_content",
        "settings": {
            "defaultLanguage": "en",
            "cloudTransmissionPolicy": "local_only",
            "audioProfile": "cinematic_stereo_v1",
        },
    }


def _source_dict(row: SourceDocumentRow) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "revision": row.revision,
        "provenance": parse_json(row.provenance_json, {}),
        "documentId": row.id,
        "projectId": row.project_id,
        "displayName": row.display_name,
        "mediaType": row.media_type,
        "declaredFormat": row.declared_format,
        "contentSha256": row.content_sha256,
        "byteLength": row.byte_length,
        "importedAt": row.imported_at,
        "originalTextPreserved": True,
        "originalBytesPreserved": True,
        "storageKey": row.storage_key,
        "extractionStatus": row.extraction_status,
        "sourceRevision": row.source_revision,
        "warnings": parse_json(row.warnings_json, []),
    }
    if row.text_sha256 is not None:
        result["textSha256"] = row.text_sha256
    if row.encoding is not None:
        result["encoding"] = row.encoding
    if row.newline_style is not None:
        result["newlineStyle"] = row.newline_style
    if row.supersedes_document_id is not None:
        result["supersedesDocumentId"] = row.supersedes_document_id
    return result


def _warning_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        code = str(value.get("code", "EXTRACTION_WARNING"))
        severity = str(value.get("severity", "warning"))
        message = str(value.get("message", "Document extraction requires review."))
        review = bool(value.get("requiresHumanReview", value.get("requires_human_review", True)))
    else:
        code = str(getattr(value, "code", "EXTRACTION_WARNING"))
        severity = str(getattr(value, "severity", "warning"))
        message = str(getattr(value, "message", "Document extraction requires review."))
        review = bool(getattr(value, "requires_human_review", True))
    return {
        "code": code[:80],
        "severity": severity if severity in {"info", "warning", "error"} else "warning",
        "message": message[:1_000],
        "requiresHumanReview": review,
        "relatedEntities": [],
    }


def _manifest_value(row: DocumentExtractionRow) -> dict[str, Any]:
    value = parse_json(row.manifest_json, {})
    return value if isinstance(value, dict) else {}


def _extraction_dict(row: DocumentExtractionRow) -> dict[str, Any]:
    manifest = _manifest_value(row)
    parser = manifest.get("parserExecution", {})
    parser = parser if isinstance(parser, dict) else {}
    warnings = [
        _warning_dict(value)
        for value in parse_json(row.warnings_json, [])
        if isinstance(value, dict)
    ]
    quality = manifest.get("quality", {})
    quality = quality if isinstance(quality, dict) else {}
    detected_format = str(manifest.get("detectedFormat", row.format))
    media_type = str(
        manifest.get(
            "mediaType",
            _MEDIA_TYPES_BY_FORMAT.get(row.format, "application/octet-stream"),
        )
    )
    result: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "revision": row.revision,
        "provenance": manifest.get(
            "provenance",
            provenance(
                origin="import",
                actor_id=f"{row.extractor_name}@{row.extractor_version}",
                recorded_at=row.created_at,
                input_fingerprint=row.input_sha256,
            ),
        ),
        "extractionId": row.id,
        "projectId": row.project_id,
        "sourceDocumentId": row.source_document_id,
        "declaredFormat": row.format,
        "detectedFormat": detected_format,
        "mediaType": media_type,
        "status": row.status,
        "adapterId": str(manifest.get("adapterId", row.extractor_name)),
        "adapterVersion": str(manifest.get("adapterVersion", row.extractor_version)),
        "parserDependency": str(
            manifest.get(
                "parserDependency",
                parser.get(
                    "parserDependency",
                    parser.get("parser_dependency", row.extractor_name),
                ),
            )
        ),
        "parserVersion": str(
            manifest.get(
                "parserVersion",
                parser.get(
                    "parserVersion",
                    parser.get("parser_version", row.extractor_version),
                ),
            )
        ),
        "sourceSha256": row.input_sha256,
        "sourceByteCount": int(manifest.get("sourceByteCount", 0)),
        "warnings": warnings,
        "quality": {
            "classification": str(quality.get("classification", "pending")),
            "confidence": float(quality.get("confidence", 0.0)),
        },
        "retryability": str(manifest.get("retryability", "not_retryable")),
        "reviewRequired": bool(manifest.get("reviewRequired", True)),
        "originalPreserved": True,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }
    optional = {
        "extractedTitle": manifest.get("title"),
        "extractedTextSha256": row.text_sha256,
        "extractedCharacterCount": row.character_count,
        "sectionCount": manifest.get("sectionCount"),
        "pageCount": row.page_count,
        "completedAt": manifest.get("completedAt"),
    }
    result.update({key: value for key, value in optional.items() if value is not None})
    return result


def _approval_dict(session: Session, row: ImportReviewRow) -> dict[str, Any] | None:
    if row.decision_id is None or row.decided_at is None:
        return None
    extraction_revision = session.scalar(
        select(DocumentExtractionRow.revision).where(DocumentExtractionRow.id == row.extraction_id)
    )
    if extraction_revision is None:
        raise ServiceError(
            500,
            "IMPORT_REVIEW_EVIDENCE_INVALID",
            "The extraction referenced by the Import Review decision is unavailable.",
        )
    superseded_decision_id: str | None = None
    if row.supersedes_record_id:
        superseded_decision_id = session.scalar(
            select(ImportReviewRow.decision_id).where(
                ImportReviewRow.id == row.supersedes_record_id
            )
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": row.revision,
        "provenance": parse_json(row.provenance_json, {}),
        "decisionId": row.decision_id,
        "projectId": row.project_id,
        "gateId": "import_review",
        "scope": {
            "entityType": "DocumentExtraction",
            "entityId": row.extraction_id,
            "revision": extraction_revision,
        },
        "decision": row.state,
        "actor": {"type": "human", "actorId": row.actor_id or "local_user"},
        "rationale": row.decision_rationale or row.reason or "No rationale supplied.",
        "evidenceFingerprint": row.evidence_fingerprint,
        "warningAcknowledgements": parse_json(row.warning_acknowledgements_json, []),
        "decidedAt": row.decided_at,
        "immutable": True,
        **({"supersedesDecisionId": superseded_decision_id} if superseded_decision_id else {}),
    }


def _review_dict(session: Session, row: ImportReviewRow) -> dict[str, Any]:
    latest_decision = _approval_dict(session, row)
    if latest_decision is None:
        decision_row = session.scalar(
            select(ImportReviewRow)
            .where(
                ImportReviewRow.review_id == row.review_id,
                ImportReviewRow.decision_id.is_not(None),
            )
            .order_by(ImportReviewRow.revision.desc(), ImportReviewRow.id.desc())
            .limit(1)
        )
        latest_decision = (
            _approval_dict(session, decision_row) if decision_row is not None else None
        )
    result: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "revision": row.revision,
        "provenance": parse_json(row.provenance_json, {}),
        "reviewId": row.review_id,
        "projectId": row.project_id,
        "sourceDocumentId": row.source_document_id,
        "extractionId": row.extraction_id,
        "candidateStoryId": row.candidate_story_id,
        "candidateStoryRevision": 1,
        "state": row.state,
        "evidenceFingerprint": row.evidence_fingerprint,
        "previewText": row.preview_text,
        "previewTruncated": row.preview_truncated,
        "warnings": [
            _warning_dict(value)
            for value in parse_json(row.warnings_json, [])
            if isinstance(value, dict)
        ],
        "createdAt": row.created_at,
        "updatedAt": row.decided_at or row.created_at,
    }
    if latest_decision is not None:
        result["latestDecision"] = latest_decision
    return result


def _story_dict(session: Session, row: ImportedStoryRow) -> dict[str, Any]:
    chapter_ids = list(
        session.scalars(
            select(ChapterRow.id)
            .where(ChapterRow.story_id == row.id)
            .order_by(ChapterRow.ordinal, ChapterRow.id)
        )
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": row.revision,
        "provenance": parse_json(row.provenance_json, {}),
        "storyId": row.id,
        "projectId": row.project_id,
        "title": row.title,
        "sourceDocumentIds": [row.source_document_id],
        "contentFingerprint": row.content_fingerprint,
        "textSha256": row.content_fingerprint,
        "text": row.exact_text,
        "originalTextPreserved": True,
        "importedAt": row.imported_at,
        "chapterIds": chapter_ids,
        "warnings": parse_json(row.warnings_json, []),
    }


def _correction_dict(row: HumanCorrectionRow) -> dict[str, Any]:
    return {
        "correctionId": row.id,
        "target": {
            "entityType": "DialogueLine",
            "entityId": row.line_id,
            "revision": row.line_revision,
        },
        "fieldPath": "/effectiveSpeakerId",
        "previousValueFingerprint": row.previous_value_fingerprint,
        "previousCharacterId": row.previous_character_id,
        "correctedValue": row.corrected_character_id,
        "correctedCharacterId": row.corrected_character_id,
        "reason": row.reason,
        "authority": {"source": "human", "actorId": row.actor_id},
        "recordedAt": row.recorded_at,
        "immutable": True,
        "lockedAgainstAutomation": True,
        **(
            {"supersedesCorrectionId": row.supersedes_correction_id}
            if row.supersedes_correction_id
            else {}
        ),
    }


class ProjectRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def require_project(self, session: Session, project_id: str) -> ProjectRow:
        row = session.get(ProjectRow, project_id)
        if row is None:
            raise not_found("project")
        return row

    def create_project(self, *, name: str, idempotency_key: str | None) -> dict[str, Any]:
        fingerprint = request_fingerprint({"name": name})
        with self.database.session() as session:
            if idempotency_key:
                existing = session.get(
                    IdempotencyRow, {"scope": "create_project", "key": idempotency_key}
                )
                if existing is not None:
                    if existing.request_hash != fingerprint:
                        raise ServiceError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "That idempotency key was already used for another request.",
                        )
                    row = session.get(ProjectRow, existing.resource_id)
                    if row is None:
                        raise ServiceError(
                            500,
                            "IDEMPOTENCY_RECORD_INVALID",
                            "The saved request record is unavailable.",
                        )
                    return _project_dict(session, row)

            now = utc_now()
            row = ProjectRow(
                id=new_id(),
                name=name,
                status="draft",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            if idempotency_key:
                session.add(
                    IdempotencyRow(
                        scope="create_project",
                        key=idempotency_key,
                        request_hash=fingerprint,
                        resource_id=row.id,
                        created_at=now,
                    )
                )
            return _project_dict(session, row)

    def list_projects(
        self, *, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        offset = self._decode_cursor(cursor)
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(ProjectRow)
                    .order_by(
                        ProjectRow.updated_at.desc(),
                        func.lower(ProjectRow.name),
                        ProjectRow.id,
                    )
                    .offset(offset)
                    .limit(limit + 1)
                )
            )
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = [
                {
                    "projectId": row.id,
                    "name": row.name,
                    "status": row.status,
                    "revision": row.revision,
                    "createdAt": row.created_at,
                    "updatedAt": row.updated_at,
                }
                for row in rows
            ]
        next_cursor = self._encode_cursor(offset + limit) if has_more else None
        return items, next_cursor

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(f"v1:{offset}".encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded).decode()
            version, raw_offset = decoded.split(":", 1)
            offset = int(raw_offset)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ServiceError(400, "INVALID_CURSOR", "The project cursor is invalid.") from exc
        if version != "v1" or offset < 0:
            raise ServiceError(400, "INVALID_CURSOR", "The project cursor is invalid.")
        return offset

    def get_story_snapshot(
        self, project_id: str
    ) -> tuple[ProjectRow, ImportedStoryRow, SourceDocumentRow]:
        with self.database.session() as session:
            project = self.require_project(session, project_id)
            if project.story_id is None:
                raise ServiceError(
                    409,
                    "STORY_REQUIRED",
                    "Import a story before starting analysis.",
                )
            story = session.get(ImportedStoryRow, project.story_id)
            if story is None:
                raise ServiceError(500, "STORY_UNAVAILABLE", "The imported story is unavailable.")
            source = session.get(SourceDocumentRow, story.source_document_id)
            if source is None:
                raise ServiceError(500, "SOURCE_UNAVAILABLE", "The imported source is unavailable.")
            if not self._analysis_allowed(session, project, story):
                raise ServiceError(
                    409,
                    "IMPORT_APPROVAL_REQUIRED",
                    "Approve the current extraction in Import Review before analysis.",
                )
            session.expunge(project)
            session.expunge(story)
            session.expunge(source)
            return project, story, source

    @staticmethod
    def _latest_review(
        session: Session,
        *,
        extraction_id: str,
    ) -> ImportReviewRow | None:
        return session.scalar(
            select(ImportReviewRow)
            .where(ImportReviewRow.extraction_id == extraction_id)
            .order_by(ImportReviewRow.revision.desc(), ImportReviewRow.id.desc())
            .limit(1)
        )

    @classmethod
    def _analysis_allowed(
        cls,
        session: Session,
        project: ProjectRow,
        story: ImportedStoryRow | None,
    ) -> bool:
        if story is None or project.story_id != story.id:
            return False
        current_source_id = session.scalar(
            select(SourceDocumentRow.id)
            .where(SourceDocumentRow.project_id == project.id)
            .order_by(
                SourceDocumentRow.source_revision.desc(),
                SourceDocumentRow.imported_at.desc(),
                SourceDocumentRow.id.desc(),
            )
            .limit(1)
        )
        if current_source_id != story.source_document_id:
            return False
        extraction = session.get(DocumentExtractionRow, story.extraction_id)
        if (
            extraction is None
            or extraction.revision != story.extraction_revision
            or extraction.text_sha256 != story.content_fingerprint
        ):
            return False
        current_extraction_id = session.scalar(
            select(DocumentExtractionRow.id)
            .where(DocumentExtractionRow.source_document_id == story.source_document_id)
            .order_by(
                DocumentExtractionRow.revision.desc(),
                DocumentExtractionRow.created_at.desc(),
                DocumentExtractionRow.id.desc(),
            )
            .limit(1)
        )
        if current_extraction_id != extraction.id:
            return False
        review = cls._latest_review(session, extraction_id=extraction.id)
        return bool(
            review is not None
            and review.state == "approved"
            and review.evidence_fingerprint == extraction.evidence_fingerprint
            and review.candidate_story_id == story.id
        )

    @staticmethod
    def _record_import_idempotency(
        session: Session,
        *,
        project_id: str,
        idempotency_key: str,
        request_hash: str,
        source_document_id: str,
        extraction_id: str,
        created_at: str,
    ) -> None:
        for scope, resource_id in (
            (f"import_document:{project_id}", source_document_id),
            (f"import_extraction:{project_id}", extraction_id),
        ):
            session.add(
                IdempotencyRow(
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=resource_id,
                    created_at=created_at,
                )
            )

    @staticmethod
    def _resolve_saved_import_extraction(
        session: Session,
        *,
        project_id: str,
        source: SourceDocumentRow,
        idempotency_key: str,
        request_hash: str,
    ) -> DocumentExtractionRow:
        extraction_scope = f"import_extraction:{project_id}"
        exact_record = session.get(
            IdempotencyRow,
            {"scope": extraction_scope, "key": idempotency_key},
        )
        if exact_record is not None:
            exact_extraction = session.get(DocumentExtractionRow, exact_record.resource_id)
            if (
                exact_record.request_hash != request_hash
                or exact_extraction is None
                or exact_extraction.project_id != project_id
                or exact_extraction.source_document_id != source.id
            ):
                raise ServiceError(
                    500,
                    "IDEMPOTENCY_RECORD_INVALID",
                    "The saved import extraction record is unavailable.",
                )
            return exact_extraction

        resolved_extraction: DocumentExtractionRow | None = None
        # Pre-fix import records saved only the source ID. Their extraction job ledger
        # still identifies the exact extraction target, so use it to lazily backfill the
        # new mapping without rewriting append-only import or extraction history.
        job_record = session.get(
            IdempotencyRow,
            {
                "scope": f"create_extraction_job:{project_id}",
                "key": idempotency_key,
            },
        )
        if job_record is not None:
            job = session.get(JobRow, job_record.resource_id)
            if (
                job is not None
                and job.project_id == project_id
                and job.type == "extract_document"
                and job.target_type == "document_extraction"
                and job.target_id is not None
            ):
                candidate = session.get(DocumentExtractionRow, job.target_id)
                if (
                    candidate is not None
                    and candidate.project_id == project_id
                    and candidate.source_document_id == source.id
                    and candidate.revision == job.input_revision
                    and candidate.input_sha256 == job.input_fingerprint
                ):
                    resolved_extraction = candidate

        if resolved_extraction is None:
            resolved_extraction = session.scalar(
                select(DocumentExtractionRow)
                .where(DocumentExtractionRow.source_document_id == source.id)
                .order_by(
                    DocumentExtractionRow.revision,
                    DocumentExtractionRow.created_at,
                    DocumentExtractionRow.id,
                )
                .limit(1)
            )
        if resolved_extraction is None:
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved extraction record is unavailable.",
            )

        session.add(
            IdempotencyRow(
                scope=extraction_scope,
                key=idempotency_key,
                request_hash=request_hash,
                resource_id=resolved_extraction.id,
                created_at=utc_now(),
            )
        )
        return resolved_extraction

    def create_pending_import(
        self,
        *,
        project_id: str,
        display_name: str,
        declared_format: SupportedFormat,
        media_type: str,
        byte_sha256: str,
        byte_length: int,
        storage_key: str,
        idempotency_key: str | None,
    ) -> ImportResult:
        fingerprint = request_fingerprint(
            {
                "projectId": project_id,
                "declaredFormat": declared_format,
                "contentSha256": byte_sha256,
            }
        )
        scope = f"import_document:{project_id}"
        try:
            with self.database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                project = self.require_project(session, project_id)
                if idempotency_key:
                    record = session.get(
                        IdempotencyRow,
                        {"scope": scope, "key": idempotency_key},
                    )
                    if record is not None:
                        if record.request_hash != fingerprint:
                            raise ServiceError(
                                409,
                                "IDEMPOTENCY_CONFLICT",
                                "That idempotency key was already used for another import.",
                            )
                        source = session.get(SourceDocumentRow, record.resource_id)
                        if source is None or source.project_id != project_id:
                            raise ServiceError(
                                500,
                                "IDEMPOTENCY_RECORD_INVALID",
                                "The saved import record is unavailable.",
                            )
                        saved_extraction = self._resolve_saved_import_extraction(
                            session,
                            project_id=project_id,
                            source=source,
                            idempotency_key=idempotency_key,
                            request_hash=fingerprint,
                        )
                        return ImportResult(
                            _source_dict(source),
                            _extraction_dict(saved_extraction),
                        )

                latest_source = session.scalar(
                    select(SourceDocumentRow)
                    .where(SourceDocumentRow.project_id == project_id)
                    .order_by(SourceDocumentRow.source_revision.desc())
                    .limit(1)
                )
                if (
                    latest_source is not None
                    and latest_source.content_sha256 == byte_sha256
                    and latest_source.declared_format == declared_format
                ):
                    current_extraction = session.scalar(
                        select(DocumentExtractionRow)
                        .where(DocumentExtractionRow.source_document_id == latest_source.id)
                        .order_by(
                            DocumentExtractionRow.revision.desc(),
                            DocumentExtractionRow.created_at.desc(),
                            DocumentExtractionRow.id.desc(),
                        )
                        .limit(1)
                    )
                    if current_extraction is None:
                        raise ServiceError(
                            500,
                            "SOURCE_UNAVAILABLE",
                            "The saved extraction record is unavailable.",
                        )
                    if idempotency_key:
                        self._record_import_idempotency(
                            session,
                            project_id=project_id,
                            idempotency_key=idempotency_key,
                            request_hash=fingerprint,
                            source_document_id=latest_source.id,
                            extraction_id=current_extraction.id,
                            created_at=utc_now(),
                        )
                    return ImportResult(
                        _source_dict(latest_source),
                        _extraction_dict(current_extraction),
                    )

                now = utc_now()
                source_id = new_id()
                extraction_id = new_id()
                source_revision = 1 if latest_source is None else latest_source.source_revision + 1
                source_provenance = provenance(
                    origin="import",
                    actor_id="secure-document-archive@1.0.0",
                    recorded_at=now,
                    input_fingerprint=byte_sha256,
                    notes="Exact original bytes retained before bounded local extraction.",
                )
                source = SourceDocumentRow(
                    id=source_id,
                    project_id=project_id,
                    display_name=display_name,
                    media_type=media_type,
                    declared_format=declared_format,
                    content_sha256=byte_sha256,
                    text_sha256=None,
                    byte_length=byte_length,
                    encoding=None,
                    newline_style=None,
                    storage_key=storage_key,
                    imported_at=now,
                    revision=1,
                    source_revision=source_revision,
                    supersedes_document_id=(
                        latest_source.id if latest_source is not None else None
                    ),
                    extraction_status="pending",
                    provenance_json=canonical_json(source_provenance),
                    warnings_json="[]",
                )
                pending_manifest = {
                    "contractVersion": "1.0.0",
                    "adapterId": "pending",
                    "adapterVersion": "1.0.0",
                    "parserDependency": "pending",
                    "parserVersion": "pending",
                    "sourceByteCount": byte_length,
                    "declaredFormat": declared_format,
                    "detectedFormat": declared_format,
                    "mediaType": media_type,
                    "quality": {"classification": "pending", "confidence": 0.0},
                    "retryability": "retryable",
                    "reviewRequired": True,
                    "provenance": source_provenance,
                }
                pending_extraction = DocumentExtractionRow(
                    id=extraction_id,
                    project_id=project_id,
                    source_document_id=source_id,
                    revision=1,
                    supersedes_extraction_id=None,
                    status="pending",
                    format=declared_format,
                    extractor_name="pending",
                    extractor_version="1.0.0",
                    input_sha256=byte_sha256,
                    text_sha256=None,
                    character_count=None,
                    page_count=None,
                    encoding=None,
                    newline_style=None,
                    exact_text=None,
                    text_storage_key=None,
                    manifest_json=canonical_json(pending_manifest),
                    sections_json="[]",
                    source_mappings_json="[]",
                    evidence_fingerprint=request_fingerprint(
                        {
                            "sourceSha256": byte_sha256,
                            "extractionRevision": 1,
                            "status": "pending",
                        }
                    ),
                    warnings_json="[]",
                    created_at=now,
                    updated_at=now,
                )
                session.add(source)
                session.flush()
                session.add(pending_extraction)
                if idempotency_key:
                    self._record_import_idempotency(
                        session,
                        project_id=project_id,
                        idempotency_key=idempotency_key,
                        request_hash=fingerprint,
                        source_document_id=source_id,
                        extraction_id=extraction_id,
                        created_at=now,
                    )
                project.revision += 1
                project.updated_at = now
                session.flush()
                return ImportResult(
                    _source_dict(source),
                    _extraction_dict(pending_extraction),
                )
        except IntegrityError as exc:
            raise ServiceError(
                409,
                "IMPORT_CONFLICT",
                "The document import changed concurrently; refresh the project.",
            ) from exc

    def create_reextraction(
        self,
        *,
        project_id: str,
        source_document_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            project = self.require_project(session, project_id)
            source = session.get(SourceDocumentRow, source_document_id)
            if source is None or source.project_id != project_id:
                raise not_found("source document")
            scope = f"reextract_document:{project_id}"
            fingerprint = request_fingerprint(
                {
                    "projectId": project_id,
                    "sourceDocumentId": source.id,
                    "sourceRevision": source.source_revision,
                    "sourceSha256": source.content_sha256,
                }
            )
            if idempotency_key is not None:
                existing = session.get(
                    IdempotencyRow,
                    {"scope": scope, "key": idempotency_key},
                )
                if existing is not None:
                    if existing.request_hash != fingerprint:
                        raise ServiceError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "That idempotency key was already used for another re-extraction.",
                        )
                    extraction = session.get(
                        DocumentExtractionRow,
                        existing.resource_id,
                    )
                    if (
                        extraction is None
                        or extraction.project_id != project_id
                        or extraction.source_document_id != source.id
                    ):
                        raise ServiceError(
                            500,
                            "IDEMPOTENCY_RECORD_INVALID",
                            "The saved re-extraction record is unavailable.",
                        )
                    return _extraction_dict(extraction)
            latest = session.scalar(
                select(DocumentExtractionRow)
                .where(DocumentExtractionRow.source_document_id == source.id)
                .order_by(DocumentExtractionRow.revision.desc())
                .limit(1)
            )
            if latest is None:
                raise ServiceError(
                    500,
                    "EXTRACTION_UNAVAILABLE",
                    "The source extraction history is unavailable.",
                )
            if latest.status in {"pending", "running"}:
                if idempotency_key is not None:
                    session.add(
                        IdempotencyRow(
                            scope=scope,
                            key=idempotency_key,
                            request_hash=fingerprint,
                            resource_id=latest.id,
                            created_at=utc_now(),
                        )
                    )
                return _extraction_dict(latest)
            now = utc_now()
            row = DocumentExtractionRow(
                id=new_id(),
                project_id=project_id,
                source_document_id=source.id,
                revision=latest.revision + 1,
                supersedes_extraction_id=latest.id,
                status="pending",
                format=source.declared_format,
                extractor_name="pending",
                extractor_version="1.0.0",
                input_sha256=source.content_sha256,
                text_sha256=None,
                character_count=None,
                page_count=None,
                encoding=None,
                newline_style=None,
                exact_text=None,
                text_storage_key=None,
                manifest_json=canonical_json(
                    {
                        "contractVersion": "1.0.0",
                        "adapterId": "pending",
                        "adapterVersion": "1.0.0",
                        "parserDependency": "pending",
                        "parserVersion": "pending",
                        "sourceByteCount": source.byte_length,
                        "declaredFormat": source.declared_format,
                        "detectedFormat": source.declared_format,
                        "mediaType": source.media_type,
                        "quality": {
                            "classification": "pending",
                            "confidence": 0.0,
                        },
                        "retryability": "retryable",
                        "reviewRequired": True,
                    }
                ),
                sections_json="[]",
                source_mappings_json="[]",
                evidence_fingerprint=request_fingerprint(
                    {
                        "sourceSha256": source.content_sha256,
                        "extractionRevision": latest.revision + 1,
                        "status": "pending",
                    }
                ),
                warnings_json="[]",
                created_at=now,
                updated_at=now,
            )
            source.extraction_status = "pending"
            session.add(row)
            if idempotency_key is not None:
                session.add(
                    IdempotencyRow(
                        scope=scope,
                        key=idempotency_key,
                        request_hash=fingerprint,
                        resource_id=row.id,
                        created_at=now,
                    )
                )
            project.revision += 1
            project.updated_at = now
            session.flush()
            return _extraction_dict(row)

    def get_extraction_input(self, extraction_id: str) -> ExtractionJobInput:
        with self.database.session() as session:
            extraction = session.get(DocumentExtractionRow, extraction_id)
            if extraction is None:
                raise not_found("document extraction")
            source = session.get(SourceDocumentRow, extraction.source_document_id)
            if source is None:
                raise ServiceError(
                    500,
                    "SOURCE_UNAVAILABLE",
                    "The preserved source record is unavailable.",
                )
            if extraction.status not in {"pending", "running"}:
                raise ServiceError(
                    409,
                    "EXTRACTION_ALREADY_TERMINAL",
                    "The document extraction is already complete.",
                )
            source_path = resolve_beneath(self.database.path.parent, source.storage_key)
            if not source_path.is_file() or source_path.is_symlink():
                raise ServiceError(
                    500,
                    "SOURCE_STORAGE_CONFLICT",
                    "The managed source location failed integrity verification.",
                )
            return ExtractionJobInput(
                project_id=source.project_id,
                source_document_id=source.id,
                extraction_id=extraction.id,
                extraction_revision=extraction.revision,
                source_path=source_path,
                display_name=source.display_name,
                declared_format=source.declared_format,  # type: ignore[arg-type]
                source_sha256=source.content_sha256,
                source_byte_count=source.byte_length,
            )

    def publish_extraction(
        self,
        *,
        job_id: str,
        result: DocumentExtractionResult,
        session: Session,
    ) -> None:
        job = session.get(JobRow, job_id)
        if (
            job is None
            or job.type != "extract_document"
            or job.target_type != "document_extraction"
            or job.target_id is None
            or job.state != "running"
            or job.cancellation_requested
        ):
            raise ServiceError(
                409,
                "EXTRACTION_PUBLICATION_CONFLICT",
                "The extraction job no longer owns publication.",
            )
        extraction = session.get(DocumentExtractionRow, job.target_id)
        if extraction is None:
            raise ServiceError(
                500,
                "EXTRACTION_UNAVAILABLE",
                "The extraction record is unavailable.",
            )
        source = session.get(SourceDocumentRow, extraction.source_document_id)
        if source is None:
            raise ServiceError(
                500,
                "SOURCE_UNAVAILABLE",
                "The preserved source record is unavailable.",
            )
        if (
            extraction.status not in {"pending", "running"}
            or extraction.project_id != job.project_id
            or extraction.revision != job.input_revision
            or extraction.input_sha256 != job.input_fingerprint
            or result.source_sha256 != source.content_sha256
            or result.source_byte_count != source.byte_length
            or result.declared_format != source.declared_format
        ):
            raise ServiceError(
                409,
                "EXTRACTION_INPUT_CHANGED",
                "The frozen extraction input no longer matches the job.",
            )
        warnings = [_warning_dict(value) for value in result.warnings]
        sections = [section.to_wire() for section in result.sections]
        mappings = [section.location.to_wire() for section in result.sections]
        quality_classification = (
            "exact_text_decode"
            if result.declared_format in {"txt", "markdown"}
            else (
                "page_text_extraction"
                if result.declared_format == "pdf"
                else "structured_extraction"
            )
        )
        if any(warning["code"] == "PDF_NEAR_EMPTY_TEXT" for warning in warnings):
            quality_classification = "low_text_density"
        evidence_fingerprint = request_fingerprint(
            {
                "sourceDocumentId": source.id,
                "sourceRevision": source.source_revision,
                "sourceSha256": source.content_sha256,
                "extractionId": extraction.id,
                "extractionRevision": extraction.revision,
                "adapterId": result.adapter_id,
                "adapterVersion": result.adapter_version,
                "parserDependency": result.parser_dependency,
                "parserVersion": result.parser_version,
                "limitsFingerprint": result.parser_execution.limits_fingerprint,
                "textSha256": result.extracted_text_sha256,
                "warnings": [warning["code"] for warning in warnings],
            }
        )
        manifest = {
            "contractVersion": result.contract_version,
            "adapterId": result.adapter_id,
            "adapterVersion": result.adapter_version,
            "parserDependency": result.parser_dependency,
            "parserVersion": result.parser_version,
            "sourceByteCount": result.source_byte_count,
            "declaredFormat": result.declared_format,
            "detectedFormat": result.detected_format,
            "mediaType": result.media_type,
            "title": result.title,
            "sectionCount": len(result.sections),
            "quality": {
                "classification": quality_classification,
                "confidence": result.confidence,
            },
            "startedAt": result.started_at,
            "completedAt": result.completed_at,
            "retryability": result.retryability,
            "reviewRequired": result.review_required,
            "provenance": result.provenance,
            "importManifest": result.manifest.to_wire(),
            "parserExecution": result.parser_execution.to_wire(),
        }
        now = utc_now()
        extraction.status = result.status
        extraction.extractor_name = result.adapter_id
        extraction.extractor_version = result.adapter_version
        extraction.text_sha256 = result.extracted_text_sha256
        extraction.character_count = len(result.canonical_text)
        extraction.page_count = result.page_count
        extraction.encoding = result.encoding
        extraction.newline_style = result.newline_style
        extraction.exact_text = result.canonical_text
        extraction.manifest_json = canonical_json(manifest)
        extraction.sections_json = canonical_json(sections)
        extraction.source_mappings_json = canonical_json(mappings)
        extraction.evidence_fingerprint = evidence_fingerprint
        extraction.warnings_json = canonical_json(warnings)
        extraction.updated_at = now
        source.text_sha256 = result.extracted_text_sha256
        source.encoding = result.encoding
        source.newline_style = result.newline_style
        source.extraction_status = result.status
        source.warnings_json = canonical_json(warnings)

        story = ImportedStoryRow(
            id=new_id(),
            project_id=source.project_id,
            source_document_id=source.id,
            extraction_id=extraction.id,
            extraction_revision=extraction.revision,
            title=result.title or Path(source.display_name).stem,
            exact_text=result.canonical_text,
            content_fingerprint=result.extracted_text_sha256,
            imported_at=now,
            revision=1,
            provenance_json=canonical_json(result.provenance),
            warnings_json=canonical_json(warnings),
        )
        session.add(story)
        session.flush()
        preview = result.canonical_text[:IMPORT_PREVIEW_CHARACTERS]
        review_id = new_id()
        session.add(
            ImportReviewRow(
                id=new_id(),
                review_id=review_id,
                project_id=source.project_id,
                source_document_id=source.id,
                extraction_id=extraction.id,
                candidate_story_id=story.id,
                revision=1,
                state="pending",
                evidence_fingerprint=evidence_fingerprint,
                preview_text=preview,
                preview_truncated=(len(result.canonical_text) > IMPORT_PREVIEW_CHARACTERS),
                warnings_json=canonical_json(warnings),
                warning_acknowledgements_json="[]",
                provenance_json=canonical_json(
                    provenance(
                        origin="system",
                        actor_id="import-review-gate@1.0.0",
                        recorded_at=now,
                        input_fingerprint=evidence_fingerprint,
                        source_references=[
                            {
                                "entityType": "DocumentExtraction",
                                "entityId": extraction.id,
                                "revision": extraction.revision,
                            }
                        ],
                    )
                ),
                decision_id=None,
                decision_rationale=None,
                reason=None,
                actor_id=None,
                idempotency_key=None,
                decided_at=None,
                supersedes_record_id=None,
                created_at=now,
            )
        )
        session.add(
            ParserExecutionRow(
                id=new_id(),
                project_id=source.project_id,
                source_document_id=source.id,
                extraction_id=extraction.id,
                job_id=job.id,
                attempt=job.current_attempt,
                parser_name=result.parser_dependency,
                parser_version=result.parser_version,
                outcome="partial" if result.status == "partial" else "succeeded",
                input_sha256=source.content_sha256,
                limits_fingerprint=result.parser_execution.limits_fingerprint,
                output_text_sha256=result.extracted_text_sha256,
                manifest_json=canonical_json(manifest),
                sections_json=canonical_json(sections),
                source_mappings_json=canonical_json(mappings),
                warnings_json=canonical_json(warnings),
                error_code=None,
                error_message=None,
                error_retryable=None,
                started_at=result.started_at,
                finished_at=result.completed_at,
            )
        )
        project = self.require_project(session, source.project_id)
        project.revision += 1
        project.updated_at = now
        session.flush()

    def get_import_review(
        self,
        *,
        project_id: str,
        review_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            self.require_project(session, project_id)
            row = session.scalar(
                select(ImportReviewRow)
                .where(
                    ImportReviewRow.project_id == project_id,
                    ImportReviewRow.review_id == review_id,
                )
                .order_by(ImportReviewRow.revision.desc(), ImportReviewRow.id.desc())
                .limit(1)
            )
            if row is None:
                raise not_found("import review")
            return _review_dict(session, row)

    def decide_import_review(
        self,
        *,
        project_id: str,
        review_id: str,
        decision: str,
        rationale: str | None,
        expected_revision: int,
        evidence_fingerprint: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any], int, bool]:
        if decision not in {"approved", "changes_requested", "rejected"}:
            raise ServiceError(
                400,
                "IMPORT_REVIEW_DECISION_INVALID",
                "The Import Review decision is invalid.",
            )
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            project = self.require_project(session, project_id)
            idempotent = session.scalar(
                select(ImportReviewRow).where(
                    ImportReviewRow.review_id == review_id,
                    ImportReviewRow.idempotency_key == idempotency_key,
                )
            )
            if idempotent is not None:
                existing = _approval_dict(session, idempotent)
                if (
                    existing is None
                    or idempotent.state != decision
                    or idempotent.evidence_fingerprint != evidence_fingerprint
                    or idempotent.decision_rationale != rationale
                ):
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another review decision.",
                    )
                story = session.get(ImportedStoryRow, idempotent.candidate_story_id)
                return (
                    _review_dict(session, idempotent),
                    existing,
                    project.revision,
                    self._analysis_allowed(session, project, story),
                )
            current = session.scalar(
                select(ImportReviewRow)
                .where(
                    ImportReviewRow.project_id == project_id,
                    ImportReviewRow.review_id == review_id,
                )
                .order_by(ImportReviewRow.revision.desc(), ImportReviewRow.id.desc())
                .limit(1)
            )
            if current is None:
                raise not_found("import review")
            if (
                current.revision != expected_revision
                or current.evidence_fingerprint != evidence_fingerprint
            ):
                raise ServiceError(
                    409,
                    "IMPORT_REVIEW_CONFLICT",
                    "The Import Review evidence changed; refresh before deciding.",
                    details={"currentRevision": current.revision},
                )
            if current.state != "pending":
                raise ServiceError(
                    409,
                    "IMPORT_REVIEW_ALREADY_DECIDED",
                    "The Import Review already has an effective human decision.",
                )
            source = session.get(SourceDocumentRow, current.source_document_id)
            extraction = session.get(DocumentExtractionRow, current.extraction_id)
            story = session.get(ImportedStoryRow, current.candidate_story_id)
            if (
                source is None
                or extraction is None
                or story is None
                or source.project_id != project_id
                or extraction.project_id != project_id
                or extraction.source_document_id != source.id
                or story.project_id != project_id
                or story.source_document_id != source.id
                or story.extraction_id != extraction.id
                or extraction.status not in {"complete", "partial"}
                or extraction.evidence_fingerprint != evidence_fingerprint
                or story.extraction_revision != extraction.revision
                or story.content_fingerprint != extraction.text_sha256
            ):
                raise ServiceError(
                    409,
                    "IMPORT_REVIEW_EVIDENCE_INVALID",
                    "The extraction evidence is no longer eligible for review.",
                )
            latest_source_id = session.scalar(
                select(SourceDocumentRow.id)
                .where(SourceDocumentRow.project_id == project_id)
                .order_by(
                    SourceDocumentRow.source_revision.desc(),
                    SourceDocumentRow.imported_at.desc(),
                    SourceDocumentRow.id.desc(),
                )
                .limit(1)
            )
            latest_extraction_id = session.scalar(
                select(DocumentExtractionRow.id)
                .where(DocumentExtractionRow.source_document_id == source.id)
                .order_by(
                    DocumentExtractionRow.revision.desc(),
                    DocumentExtractionRow.created_at.desc(),
                    DocumentExtractionRow.id.desc(),
                )
                .limit(1)
            )
            if latest_source_id != source.id or latest_extraction_id != extraction.id:
                raise ServiceError(
                    409,
                    "IMPORT_REVIEW_STALE",
                    "A newer source or extraction requires its own Import Review.",
                )
            now = utc_now()
            decision_id = new_id()
            warning_codes = [
                value.get("code")
                for value in parse_json(current.warnings_json, [])
                if isinstance(value, dict) and isinstance(value.get("code"), str)
            ]
            row = ImportReviewRow(
                id=new_id(),
                review_id=current.review_id,
                project_id=current.project_id,
                source_document_id=current.source_document_id,
                extraction_id=current.extraction_id,
                candidate_story_id=current.candidate_story_id,
                revision=current.revision + 1,
                state=decision,
                evidence_fingerprint=current.evidence_fingerprint,
                preview_text=current.preview_text,
                preview_truncated=current.preview_truncated,
                warnings_json=current.warnings_json,
                warning_acknowledgements_json=canonical_json(warning_codes),
                provenance_json=canonical_json(
                    provenance(
                        origin="human",
                        actor_id="local_user",
                        recorded_at=now,
                        input_fingerprint=evidence_fingerprint,
                        source_references=[
                            {
                                "entityType": "DocumentExtraction",
                                "entityId": extraction.id,
                                "revision": extraction.revision,
                            }
                        ],
                        notes="Append-only Import Review decision.",
                    )
                ),
                decision_id=decision_id,
                decision_rationale=rationale,
                reason=rationale,
                actor_id="local_user",
                idempotency_key=idempotency_key,
                decided_at=now,
                supersedes_record_id=current.id,
                created_at=now,
            )
            session.add(row)
            if decision == "approved":
                project.story_id = story.id
            project.revision += 1
            project.updated_at = now
            session.flush()
            approval = _approval_dict(session, row)
            if approval is None:
                raise ServiceError(
                    500,
                    "IMPORT_REVIEW_PUBLICATION_FAILED",
                    "The Import Review decision could not be published.",
                )
            return (
                _review_dict(session, row),
                approval,
                project.revision,
                self._analysis_allowed(session, project, story),
            )

    def get_project_detail(self, project_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            project = self.require_project(session, project_id)
            sources = list(
                session.scalars(
                    select(SourceDocumentRow)
                    .where(SourceDocumentRow.project_id == project_id)
                    .order_by(SourceDocumentRow.imported_at, SourceDocumentRow.id)
                )
            )
            extractions = list(
                session.scalars(
                    select(DocumentExtractionRow)
                    .where(DocumentExtractionRow.project_id == project_id)
                    .order_by(
                        DocumentExtractionRow.created_at,
                        DocumentExtractionRow.id,
                    )
                )
            )
            review_history = list(
                session.scalars(
                    select(ImportReviewRow)
                    .where(ImportReviewRow.project_id == project_id)
                    .order_by(
                        ImportReviewRow.review_id,
                        ImportReviewRow.revision.desc(),
                        ImportReviewRow.id.desc(),
                    )
                )
            )
            latest_reviews_by_id: dict[str, ImportReviewRow] = {}
            for review in review_history:
                latest_reviews_by_id.setdefault(review.review_id, review)
            latest_reviews = list(latest_reviews_by_id.values())
            latest_reviews.sort(key=lambda value: (value.created_at, value.review_id))
            approvals = [
                value
                for row in review_history
                if (value := _approval_dict(session, row)) is not None
            ]
            story = session.get(ImportedStoryRow, project.story_id) if project.story_id else None
            analysis_allowed = self._analysis_allowed(session, project, story)
            if story is None:
                return {
                    "project": _project_dict(session, project),
                    "sourceDocuments": [_source_dict(source) for source in sources],
                    "extractions": [_extraction_dict(extraction) for extraction in extractions],
                    "importReviews": [_review_dict(session, review) for review in latest_reviews],
                    "analysisAllowed": False,
                    "story": None,
                    "chapters": [],
                    "scenes": [],
                    "beats": [],
                    "characters": [],
                    "dialogueLines": [],
                    "dialogueAttributions": [],
                    "castingAssignments": [],
                    "castingPlaceholders": [],
                    "approvals": approvals,
                    "jobs": self._jobs_for_project(session, project_id),
                    "humanCorrections": [],
                }

            source = session.get(SourceDocumentRow, story.source_document_id)
            if source is None:
                raise ServiceError(500, "SOURCE_UNAVAILABLE", "The imported source is unavailable.")
            chapters = list(
                session.scalars(
                    select(ChapterRow)
                    .where(ChapterRow.story_id == story.id)
                    .order_by(ChapterRow.ordinal, ChapterRow.id)
                )
            )
            chapter_ids = [chapter.id for chapter in chapters]
            scenes = (
                list(
                    session.scalars(
                        select(SceneRow)
                        .where(SceneRow.chapter_id.in_(chapter_ids))
                        .order_by(SceneRow.ordinal, SceneRow.id)
                    )
                )
                if chapter_ids
                else []
            )
            scene_order = {
                scene.id: (chapter_ids.index(scene.chapter_id), scene.ordinal, scene.id)
                for scene in scenes
            }
            scenes.sort(key=lambda scene: scene_order[scene.id])
            scene_ids = [scene.id for scene in scenes]
            beats = (
                list(
                    session.scalars(
                        select(StoryBeatRow)
                        .where(StoryBeatRow.scene_id.in_(scene_ids))
                        .order_by(StoryBeatRow.scene_id, StoryBeatRow.ordinal, StoryBeatRow.id)
                    )
                )
                if scene_ids
                else []
            )
            beats.sort(
                key=lambda beat: (
                    scene_order[beat.scene_id],
                    beat.ordinal,
                    beat.id,
                )
            )
            lines = (
                list(
                    session.scalars(
                        select(DialogueLineRow)
                        .where(DialogueLineRow.scene_id.in_(scene_ids))
                        .order_by(
                            DialogueLineRow.scene_id,
                            DialogueLineRow.ordinal,
                            DialogueLineRow.id,
                        )
                    )
                )
                if scene_ids
                else []
            )
            lines.sort(
                key=lambda line: (
                    scene_order[line.scene_id],
                    line.ordinal,
                    line.id,
                )
            )
            line_ids = [line.id for line in lines]
            attributions = (
                list(
                    session.scalars(
                        select(DialogueAttributionRow).where(
                            DialogueAttributionRow.line_id.in_(line_ids)
                        )
                    )
                )
                if line_ids
                else []
            )
            attribution_by_line = {attribution.line_id: attribution for attribution in attributions}
            attributions = [
                attribution_by_line[line_id]
                for line_id in line_ids
                if line_id in attribution_by_line
            ]
            characters = list(
                session.scalars(
                    select(CharacterRow)
                    .where(CharacterRow.story_id == story.id)
                    .order_by(CharacterRow.normalized_name, CharacterRow.id)
                )
            )
            corrections = (
                list(
                    session.scalars(
                        select(HumanCorrectionRow)
                        .where(HumanCorrectionRow.line_id.in_(line_ids))
                        .order_by(
                            HumanCorrectionRow.recorded_at,
                            HumanCorrectionRow.id,
                        )
                    )
                )
                if line_ids
                else []
            )
            corrections_by_line: dict[str, list[dict[str, Any]]] = {}
            for correction in corrections:
                corrections_by_line.setdefault(correction.line_id, []).append(
                    _correction_dict(correction)
                )

            return {
                "project": _project_dict(session, project),
                "sourceDocuments": [_source_dict(source_row) for source_row in sources],
                "extractions": [_extraction_dict(extraction) for extraction in extractions],
                "importReviews": [_review_dict(session, review) for review in latest_reviews],
                "analysisAllowed": analysis_allowed,
                "story": _story_dict(session, story),
                "chapters": [
                    self._chapter_dict(chapter, source, story, scenes) for chapter in chapters
                ],
                "scenes": [
                    self._scene_dict(scene, source, story, beats, lines, attributions)
                    for scene in scenes
                ],
                "beats": [self._beat_dict(beat, source, story) for beat in beats],
                "characters": [
                    self._character_dict(character, corrections_by_line) for character in characters
                ],
                "dialogueLines": [self._line_dict(line, source, story) for line in lines],
                "dialogueAttributions": [
                    self._attribution_dict(
                        attribution,
                        corrections_by_line.get(attribution.line_id, []),
                    )
                    for attribution in attributions
                ],
                "castingAssignments": [],
                "castingPlaceholders": [
                    {
                        "characterId": character.id,
                        "status": "unassigned",
                        "providerId": None,
                        "voiceId": None,
                    }
                    for character in characters
                ],
                "approvals": approvals,
                "jobs": self._jobs_for_project(session, project_id),
                "humanCorrections": [_correction_dict(correction) for correction in corrections],
            }

    @staticmethod
    def _chapter_dict(
        row: ChapterRow,
        source: SourceDocumentRow,
        story: ImportedStoryRow,
        scenes: list[SceneRow],
    ) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": row.revision,
            "provenance": parse_json(row.provenance_json, {}),
            "chapterId": row.id,
            "projectId": row.project_id,
            "storyId": row.story_id,
            "ordinal": row.ordinal,
            "title": row.title,
            "sourceSpan": text_span(
                source_document_id=source.id,
                text=story.exact_text,
                start=row.start_offset,
                end=row.end_offset,
                text_sha256=story.content_fingerprint,
            ),
            "sceneIds": [
                scene.id
                for scene in sorted(scenes, key=lambda value: (value.ordinal, value.id))
                if scene.chapter_id == row.id
            ],
            "approvalState": "pending",
        }

    @staticmethod
    def _scene_dict(
        row: SceneRow,
        source: SourceDocumentRow,
        story: ImportedStoryRow,
        beats: list[StoryBeatRow],
        lines: list[DialogueLineRow],
        attributions: list[DialogueAttributionRow],
    ) -> dict[str, Any]:
        scene_lines = [line for line in lines if line.scene_id == row.id]
        scene_line_ids = {line.id for line in scene_lines}
        character_ids = list(
            dict.fromkeys(
                speaker_id
                for attribution in attributions
                if attribution.line_id in scene_line_ids
                for speaker_id in [
                    attribution.effective_speaker_id or attribution.proposed_speaker_id
                ]
                if speaker_id is not None
            )
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": row.revision,
            "provenance": parse_json(row.provenance_json, {}),
            "sceneId": row.id,
            "projectId": row.project_id,
            "chapterId": row.chapter_id,
            "ordinal": row.ordinal,
            "heading": row.heading,
            "location": row.location,
            "mood": row.mood,
            "sourceSpan": text_span(
                source_document_id=source.id,
                text=story.exact_text,
                start=row.start_offset,
                end=row.end_offset,
                text_sha256=story.content_fingerprint,
            ),
            "beatIds": [
                beat.id
                for beat in sorted(beats, key=lambda value: (value.ordinal, value.id))
                if beat.scene_id == row.id
            ],
            "dialogueLineIds": [line.id for line in scene_lines],
            "characterIds": character_ids,
            "approvalState": "pending",
            "confidence": parse_json(row.confidence_json, {}),
            "warnings": parse_json(row.warnings_json, []),
        }

    @staticmethod
    def _beat_dict(
        row: StoryBeatRow, source: SourceDocumentRow, story: ImportedStoryRow
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "revision": row.revision,
            "provenance": parse_json(row.provenance_json, {}),
            "beatId": row.id,
            "projectId": row.project_id,
            "sceneId": row.scene_id,
            "ordinal": row.ordinal,
            "kind": row.kind,
            "sourceSpan": text_span(
                source_document_id=source.id,
                text=story.exact_text,
                start=row.start_offset,
                end=row.end_offset,
                text_sha256=story.content_fingerprint,
            ),
            "verbatimText": story.exact_text[row.start_offset : row.end_offset],
        }
        if row.summary is not None:
            result["summary"] = row.summary
        if row.dialogue_line_id is not None:
            result["dialogueLineId"] = row.dialogue_line_id
        return result

    @staticmethod
    def _character_dict(
        row: CharacterRow, _corrections_by_line: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": row.revision,
            "provenance": parse_json(row.provenance_json, {}),
            "characterId": row.id,
            "projectId": row.project_id,
            "storyId": row.story_id,
            "displayName": row.display_name,
            "aliases": parse_json(row.aliases_json, []),
            "sourceReferences": parse_json(row.evidence_json, []),
            "voiceProfileId": None,
            "humanCorrections": [],
            "confidence": parse_json(row.confidence_json, {}),
            "warnings": parse_json(row.warnings_json, []),
        }

    @staticmethod
    def _line_dict(
        row: DialogueLineRow, source: SourceDocumentRow, story: ImportedStoryRow
    ) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": row.revision,
            "provenance": parse_json(row.provenance_json, {}),
            "lineId": row.id,
            "projectId": row.project_id,
            "sceneId": row.scene_id,
            "beatId": row.beat_id,
            "ordinal": row.ordinal,
            "sourceSpan": text_span(
                source_document_id=source.id,
                text=story.exact_text,
                start=row.start_offset,
                end=row.end_offset,
                text_sha256=story.content_fingerprint,
            ),
            "verbatimText": row.verbatim_text,
            "textSha256": row.text_sha256,
            "originalTextPreserved": True,
            "attributionId": self_or_attribution_id(row.id),
        }

    @staticmethod
    def _attribution_dict(
        row: DialogueAttributionRow, corrections: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": row.revision,
            "provenance": parse_json(row.provenance_json, {}),
            "attributionId": row.id,
            "projectId": row.project_id,
            "lineId": row.line_id,
            "proposedSpeakerId": row.proposed_speaker_id,
            "effectiveSpeakerId": row.effective_speaker_id,
            "effectiveAuthority": row.effective_authority,
            "evidence": parse_json(row.evidence_json, []),
            "confidence": parse_json(row.confidence_json, {}),
            "warnings": parse_json(row.warnings_json, []),
            "humanCorrections": corrections,
            "updatedAt": row.updated_at,
        }

    @staticmethod
    def _jobs_for_project(session: Session, project_id: str) -> list[dict[str, Any]]:
        from .jobs import job_dict

        rows = list(
            session.scalars(
                select(JobRow)
                .where(JobRow.project_id == project_id)
                .order_by(JobRow.created_at, JobRow.id)
            )
        )
        return [job_dict(row) for row in rows]

    def correct_speaker(
        self,
        *,
        project_id: str,
        line_id: str,
        character_id: str | None,
        reason: str | None,
        expected_revision: int,
    ) -> tuple[dict[str, Any], dict[str, Any], int, int]:
        with self.database.session() as session:
            project = self.require_project(session, project_id)
            line = session.get(DialogueLineRow, line_id)
            if line is None or line.project_id != project_id:
                raise not_found("dialogue line")
            scene = session.get(SceneRow, line.scene_id)
            chapter = session.get(ChapterRow, scene.chapter_id) if scene is not None else None
            if (
                scene is None
                or chapter is None
                or scene.project_id != project_id
                or chapter.project_id != project_id
                or project.story_id != chapter.story_id
            ):
                raise ServiceError(
                    409,
                    "DIALOGUE_LINE_STALE",
                    "The dialogue line does not belong to the current story revision.",
                )
            attribution = session.scalar(
                select(DialogueAttributionRow).where(
                    DialogueAttributionRow.project_id == project_id,
                    DialogueAttributionRow.line_id == line_id,
                )
            )
            if attribution is None:
                raise not_found("dialogue attribution")
            if line.revision != expected_revision:
                raise ServiceError(
                    409,
                    "REVISION_CONFLICT",
                    "The dialogue line changed; refresh and compare.",
                    details={"currentRevision": line.revision},
                )
            if character_id is not None:
                character = session.get(CharacterRow, character_id)
                if (
                    character is None
                    or character.project_id != project_id
                    or character.story_id != chapter.story_id
                ):
                    raise ServiceError(
                        422,
                        "INVALID_CHARACTER_REFERENCE",
                        "The selected character does not belong to the current story.",
                    )

            previous_id = attribution.effective_speaker_id
            previous_fingerprint = sha256_text(canonical_json({"effectiveSpeakerId": previous_id}))
            previous_correction = session.scalar(
                select(HumanCorrectionRow)
                .where(HumanCorrectionRow.line_id == line_id)
                .order_by(HumanCorrectionRow.recorded_at.desc(), HumanCorrectionRow.id.desc())
                .limit(1)
            )
            now = utc_now()
            new_revision = line.revision + 1
            correction = HumanCorrectionRow(
                id=new_id(),
                project_id=project_id,
                line_id=line_id,
                attribution_id=attribution.id,
                previous_value_fingerprint=previous_fingerprint,
                previous_character_id=previous_id,
                corrected_character_id=character_id,
                reason=reason or "Speaker corrected by the local user.",
                actor_id="local_user",
                line_revision=new_revision,
                recorded_at=now,
                supersedes_correction_id=previous_correction.id if previous_correction else None,
            )
            session.add(correction)
            compare_and_swap = session.execute(
                update(DialogueLineRow)
                .where(
                    DialogueLineRow.id == line_id,
                    DialogueLineRow.project_id == project_id,
                    DialogueLineRow.revision == expected_revision,
                )
                .values(revision=new_revision)
                .returning(DialogueLineRow.revision)
                .execution_options(synchronize_session=False)
            )
            if compare_and_swap.scalar_one_or_none() is None:
                current_revision = session.scalar(
                    select(DialogueLineRow.revision).where(DialogueLineRow.id == line_id)
                )
                raise ServiceError(
                    409,
                    "REVISION_CONFLICT",
                    "The dialogue line changed; refresh and compare.",
                    details={"currentRevision": current_revision or expected_revision},
                )
            session.expire(line)
            session.refresh(line)
            attribution.revision += 1
            attribution.effective_speaker_id = character_id
            attribution.effective_authority = "human"
            attribution.confidence_json = canonical_json(
                {
                    "score": 1.0,
                    "basis": "durable_human_correction",
                    "calibrationId": "human-authority",
                }
            )
            attribution.warnings_json = "[]"
            attribution.provenance_json = canonical_json(
                provenance(
                    origin="human",
                    actor_id="local_user",
                    recorded_at=now,
                    source_references=[
                        {
                            "entityType": "DialogueLine",
                            "entityId": line.id,
                            "revision": new_revision,
                        }
                    ],
                    notes="Protected human speaker correction.",
                )
            )
            attribution.updated_at = now
            session.execute(
                update(ProjectRow)
                .where(ProjectRow.id == project_id)
                .values(
                    revision=ProjectRow.revision + 1,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.expire(project)
            session.refresh(project)
            session.flush()
            correction_value = _correction_dict(correction)
            return (
                self._attribution_dict(attribution, [correction_value]),
                correction_value,
                project.revision,
                line.revision,
            )

    def publish_analysis(
        self,
        *,
        project_id: str,
        analysis: dict[str, Any],
        session: Session | None = None,
    ) -> None:
        session_context = self.database.session() if session is None else nullcontext(session)
        with session_context as active_session:
            session = active_session
            project = self.require_project(session, project_id)
            story = session.get(ImportedStoryRow, project.story_id) if project.story_id else None
            if story is None:
                raise ServiceError(409, "STORY_REQUIRED", "The imported story is unavailable.")
            if not self._analysis_allowed(session, project, story):
                raise ServiceError(
                    409,
                    "IMPORT_APPROVAL_REQUIRED",
                    "The active extraction is not approved for analysis.",
                )
            if (
                story.revision != analysis["inputRevision"]
                or story.content_fingerprint != analysis["inputFingerprint"]
            ):
                raise ServiceError(
                    409,
                    "ANALYSIS_INPUT_CHANGED",
                    "The story changed before analysis could be published.",
                )
            had_analysis = session.scalar(
                select(func.count(ChapterRow.id)).where(ChapterRow.story_id == story.id)
            )

            for value in analysis["chapters"]:
                chapter_row = session.get(ChapterRow, value["id"])
                if chapter_row is None:
                    session.add(
                        ChapterRow(
                            id=value["id"],
                            project_id=project_id,
                            story_id=story.id,
                            ordinal=value["ordinal"],
                            title=value["title"],
                            start_offset=value["start"],
                            end_offset=value["end"],
                            revision=value["revision"],
                            provenance_json=canonical_json(value["provenance"]),
                        )
                    )
            session.flush()

            for value in analysis["scenes"]:
                scene_row = session.get(SceneRow, value["id"])
                if scene_row is None:
                    session.add(
                        SceneRow(
                            id=value["id"],
                            project_id=project_id,
                            chapter_id=value["chapterId"],
                            ordinal=value["ordinal"],
                            heading=value["heading"],
                            location=value["location"],
                            mood=value["mood"],
                            start_offset=value["start"],
                            end_offset=value["end"],
                            revision=value["revision"],
                            confidence_json=canonical_json(value["confidence"]),
                            warnings_json=canonical_json(value["warnings"]),
                            provenance_json=canonical_json(value["provenance"]),
                        )
                    )
            session.flush()

            for value in analysis["characters"]:
                character_row = session.get(CharacterRow, value["id"])
                if character_row is None:
                    session.add(
                        CharacterRow(
                            id=value["id"],
                            project_id=project_id,
                            story_id=story.id,
                            display_name=value["displayName"],
                            normalized_name=value["normalizedName"],
                            aliases_json=canonical_json(value["aliases"]),
                            evidence_json=canonical_json(value["evidence"]),
                            revision=value["revision"],
                            confidence_json=canonical_json(value["confidence"]),
                            warnings_json=canonical_json(value["warnings"]),
                            provenance_json=canonical_json(value["provenance"]),
                        )
                    )
            session.flush()

            for value in analysis["beats"]:
                beat_row = session.get(StoryBeatRow, value["id"])
                if beat_row is None:
                    session.add(
                        StoryBeatRow(
                            id=value["id"],
                            project_id=project_id,
                            scene_id=value["sceneId"],
                            ordinal=value["ordinal"],
                            kind=value["kind"],
                            start_offset=value["start"],
                            end_offset=value["end"],
                            summary=value["summary"],
                            dialogue_line_id=value.get("dialogueLineId"),
                            revision=value["revision"],
                            provenance_json=canonical_json(value["provenance"]),
                        )
                    )
            session.flush()

            for value in analysis["dialogueLines"]:
                line_row = session.get(DialogueLineRow, value["id"])
                if line_row is None:
                    session.add(
                        DialogueLineRow(
                            id=value["id"],
                            project_id=project_id,
                            scene_id=value["sceneId"],
                            beat_id=value["beatId"],
                            ordinal=value["ordinal"],
                            start_offset=value["start"],
                            end_offset=value["end"],
                            verbatim_text=value["verbatimText"],
                            text_sha256=value["textSha256"],
                            revision=value["revision"],
                            provenance_json=canonical_json(value["provenance"]),
                        )
                    )
                elif line_row.text_sha256 != value["textSha256"]:
                    raise ServiceError(
                        409,
                        "ANALYSIS_PROJECTION_CONFLICT",
                        "An existing dialogue projection no longer matches its immutable source.",
                    )
            session.flush()

            for value in analysis["dialogueAttributions"]:
                attribution_row = session.get(DialogueAttributionRow, value["id"])
                if attribution_row is None:
                    session.add(
                        DialogueAttributionRow(
                            id=value["id"],
                            project_id=project_id,
                            line_id=value["lineId"],
                            proposed_speaker_id=value["proposedSpeakerId"],
                            effective_speaker_id=value["effectiveSpeakerId"],
                            effective_authority=value["effectiveAuthority"],
                            evidence_json=canonical_json(value["evidence"]),
                            revision=value["revision"],
                            confidence_json=canonical_json(value["confidence"]),
                            warnings_json=canonical_json(value["warnings"]),
                            provenance_json=canonical_json(value["provenance"]),
                            updated_at=value["updatedAt"],
                        )
                    )
                elif attribution_row.effective_authority == "human":
                    attribution_row.proposed_speaker_id = value["proposedSpeakerId"]
                    warnings = parse_json(attribution_row.warnings_json, [])
                    if (
                        attribution_row.proposed_speaker_id != attribution_row.effective_speaker_id
                    ) and not any(
                        warning.get("code") == "AUTOMATION_CONFLICTS_WITH_HUMAN_CORRECTION"
                        for warning in warnings
                    ):
                        warnings.append(
                            {
                                "code": "AUTOMATION_CONFLICTS_WITH_HUMAN_CORRECTION",
                                "severity": "warning",
                                "message": (
                                    "Automated analysis differs from the protected human speaker."
                                ),
                                "requiresHumanReview": True,
                            }
                        )
                        attribution_row.warnings_json = canonical_json(warnings)
                else:
                    attribution_row.proposed_speaker_id = value["proposedSpeakerId"]
                    attribution_row.effective_speaker_id = value["effectiveSpeakerId"]
                    attribution_row.evidence_json = canonical_json(value["evidence"])
                    attribution_row.confidence_json = canonical_json(value["confidence"])
                    attribution_row.warnings_json = canonical_json(value["warnings"])
                    attribution_row.provenance_json = canonical_json(value["provenance"])
                    attribution_row.updated_at = value["updatedAt"]

            if not had_analysis:
                project.revision += 1
                project.status = "analysis"
                project.updated_at = utc_now()


def self_or_attribution_id(line_id: str) -> str:
    # Kept in one place so contract serialization and deterministic analysis cannot drift.
    from .util import stable_id

    return stable_id(line_id, "attribution")


class StoryImportService:
    def __init__(self, settings: ServiceSettings, projects: ProjectRepository) -> None:
        self.settings = settings
        self.projects = projects
        self._archive_lock = threading.Lock()

    def reconcile_staging(self) -> int:
        """Remove only recognized, abandoned upload files before accepting requests."""

        with self.projects.database.session() as session:
            project_ids = list(session.scalars(select(ProjectRow.id)))
        removed = 0
        for project_id in project_ids:
            try:
                staging_root = resolve_beneath(
                    self.settings.data_dir,
                    Path("projects") / project_id / "staging",
                )
                root_metadata = staging_root.lstat()
            except (OSError, ValueError):
                continue
            if not stat.S_ISDIR(root_metadata.st_mode) or staging_root.is_symlink():
                continue
            try:
                candidates = list(staging_root.iterdir())
            except OSError:
                continue
            for candidate_entry in candidates:
                try:
                    if str(uuid.UUID(candidate_entry.name)) != candidate_entry.name:
                        continue
                    candidate = resolve_beneath(staging_root, candidate_entry.name)
                    candidate_metadata = candidate.lstat()
                    if not stat.S_ISDIR(candidate_metadata.st_mode) or candidate.is_symlink():
                        continue
                    entries = list(candidate.iterdir())
                    if not entries:
                        candidate.rmdir()
                        removed += 1
                        continue
                    if len(entries) != 1 or entries[0].name != "source.upload":
                        continue
                    source_file = resolve_beneath(candidate, "source.upload")
                    source_metadata = source_file.lstat()
                    if not stat.S_ISREG(source_metadata.st_mode) or source_file.is_symlink():
                        continue
                    source_file.unlink()
                    candidate.rmdir()
                    removed += 1
                except (OSError, ValueError):
                    # Unknown or concurrently changed content is left untouched.
                    continue
        return removed

    async def import_upload(
        self,
        *,
        project_id: str,
        upload: UploadFile,
        declared_format: str | None,
        idempotency_key: str | None,
    ) -> ImportResult:
        # Fail before creating staging for an inaccessible project.
        with self.projects.database.session() as session:
            self.projects.require_project(session, project_id)

        try:
            display_name = safe_display_filename(upload.filename)
        except ValueError as exc:
            raise ServiceError(400, "UNSAFE_SOURCE_NAME", str(exc)) from exc
        content_type = (upload.content_type or "").split(";", 1)[0].strip().casefold()
        if content_type not in _SUPPORTED_CONTENT_TYPES:
            raise ServiceError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "The uploaded media type is not supported.",
            )

        staging_root = ensure_private_directory(
            self.settings.data_dir / "projects" / project_id / "staging"
        )
        staging_directory = ensure_private_directory(staging_root / new_id())
        staging_file = resolve_beneath(staging_directory, "source.upload")
        byte_count = 0
        byte_digest = hashlib.sha256()
        signature_prefix = bytearray()
        try:
            with staging_file.open("xb") as output_file:
                while True:
                    chunk = await upload.read(_IMPORT_CHUNK_BYTES)
                    if not chunk:
                        break
                    byte_count += len(chunk)
                    if byte_count > self.settings.max_import_bytes:
                        raise ServiceError(
                            413,
                            "IMPORT_TOO_LARGE",
                            "The source exceeds the configured import size limit.",
                        )
                    output_file.write(chunk)
                    byte_digest.update(chunk)
                    if len(signature_prefix) < 8:
                        signature_prefix.extend(chunk[: 8 - len(signature_prefix)])
                output_file.flush()
                os.fsync(output_file.fileno())
            if byte_count == 0:
                raise ServiceError(422, "EMPTY_SOURCE", "The document is empty.")
            byte_hash = byte_digest.hexdigest()
            probe = probe_document(
                display_name=display_name,
                declared_format=declared_format,
                prefix=bytes(signature_prefix),
                source_sha256=byte_hash,
                source_byte_count=byte_count,
            )
            if probe.detected_format in {"txt", "markdown"}:
                validate_plain_text_source(staging_file.read_bytes())
            with self._archive_lock:
                sources_root = ensure_private_directory(
                    self.settings.data_dir / "projects" / project_id / "sources"
                )
                with self.projects.database.session() as session:
                    existing_source = session.scalar(
                        select(SourceDocumentRow)
                        .where(
                            SourceDocumentRow.project_id == project_id,
                            SourceDocumentRow.content_sha256 == byte_hash,
                        )
                        .order_by(SourceDocumentRow.source_revision.desc())
                        .limit(1)
                    )
                published_new_file = False
                if existing_source is not None:
                    final_path = resolve_beneath(
                        self.settings.data_dir,
                        existing_source.storage_key,
                    )
                    existing_hash, existing_size = self._hash_file(final_path)
                    if existing_hash != byte_hash or existing_size != byte_count:
                        raise ServiceError(
                            500,
                            "SOURCE_STORAGE_CONFLICT",
                            "The managed source location failed integrity verification.",
                        )
                else:
                    final_path = resolve_beneath(
                        sources_root,
                        f"sha256-{byte_hash}.source",
                    )
                    if final_path.exists():
                        existing_hash, existing_size = self._hash_file(final_path)
                        if existing_hash != byte_hash or existing_size != byte_count:
                            raise ServiceError(
                                500,
                                "SOURCE_STORAGE_CONFLICT",
                                "The managed source location failed integrity verification.",
                            )
                    else:
                        os.replace(staging_file, final_path)
                        published_new_file = True
                        try:
                            os.chmod(final_path, 0o600)
                        except OSError:
                            # The private parent ACL remains authoritative on Windows.
                            pass
                storage_key = final_path.relative_to(self.settings.data_dir).as_posix()
                try:
                    published_hash, published_size = self._hash_file(final_path)
                    if published_hash != byte_hash or published_size != byte_count:
                        raise ServiceError(
                            500,
                            "SOURCE_STORAGE_CONFLICT",
                            "The managed source location failed integrity verification.",
                        )
                    return self.projects.create_pending_import(
                        project_id=project_id,
                        display_name=display_name,
                        declared_format=probe.declared_format,
                        media_type=probe.media_type,
                        byte_sha256=byte_hash,
                        byte_length=byte_count,
                        storage_key=storage_key,
                        idempotency_key=idempotency_key,
                    )
                except Exception:
                    if published_new_file:
                        self._remove_unreferenced_source(
                            final_path=final_path,
                            storage_key=storage_key,
                        )
                    raise
        finally:
            await upload.close()
            if staging_file.exists():
                staging_file.unlink()
            try:
                staging_directory.rmdir()
            except OSError:
                # The unique directory is intentionally left inspectable if an unexpected file
                # appeared; no recursive cleanup follows unknown content.
                pass

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                raise OSError
            with path.open("rb") as source:
                while chunk := source.read(_IMPORT_CHUNK_BYTES):
                    size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise ServiceError(
                500,
                "SOURCE_STORAGE_CONFLICT",
                "The managed source location failed integrity verification.",
            ) from exc
        return digest.hexdigest(), size

    def _remove_unreferenced_source(
        self,
        *,
        final_path: Path,
        storage_key: str,
    ) -> None:
        with self.projects.database.session() as session:
            referenced = session.scalar(
                select(SourceDocumentRow.id)
                .where(SourceDocumentRow.storage_key == storage_key)
                .limit(1)
            )
        if referenced is not None or not final_path.exists():
            return
        try:
            expected_path = resolve_beneath(self.settings.data_dir, storage_key)
            metadata = final_path.lstat()
            if (
                expected_path != final_path
                or not stat.S_ISREG(metadata.st_mode)
                or final_path.is_symlink()
            ):
                raise OSError
            final_path.unlink()
        except OSError as exc:
            raise ServiceError(
                500,
                "SOURCE_CLEANUP_FAILED",
                "An unreferenced managed source could not be removed safely.",
            ) from exc
