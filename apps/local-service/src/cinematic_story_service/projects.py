from __future__ import annotations

import base64
import codecs
import hashlib
import os
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
from .errors import ServiceError, not_found
from .models import (
    ChapterRow,
    CharacterRow,
    DialogueAttributionRow,
    DialogueLineRow,
    HumanCorrectionRow,
    IdempotencyRow,
    ImportedStoryRow,
    JobRow,
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
}
_FORMAT_BY_SUFFIX = {
    ".txt": ("txt", "text/plain"),
    ".md": ("markdown", "text/markdown"),
    ".markdown": ("markdown", "text/markdown"),
}


@dataclass(frozen=True, slots=True)
class ImportResult:
    source_document: dict[str, Any]
    story: dict[str, Any]


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
        "approvalDecisionIds": [],
        "dataClassification": "private_local_content",
        "settings": {
            "defaultLanguage": "en",
            "cloudTransmissionPolicy": "local_only",
            "audioProfile": "cinematic_stereo_v1",
        },
    }


def _source_dict(row: SourceDocumentRow) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": row.revision,
        "provenance": parse_json(row.provenance_json, {}),
        "documentId": row.id,
        "projectId": row.project_id,
        "displayName": row.display_name,
        "mediaType": row.media_type,
        "declaredFormat": row.declared_format,
        "contentSha256": row.content_sha256,
        "textSha256": row.text_sha256,
        "byteLength": row.byte_length,
        "encoding": row.encoding,
        "newlineStyle": row.newline_style,
        "importedAt": row.imported_at,
        "originalTextPreserved": True,
        "storageKey": row.storage_key,
        "extractionStatus": "complete",
        "warnings": parse_json(row.warnings_json, []),
    }


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
            session.expunge(project)
            session.expunge(story)
            session.expunge(source)
            return project, story, source

    def publish_import(
        self,
        *,
        project_id: str,
        display_name: str,
        declared_format: str,
        media_type: str,
        byte_sha256: str,
        text_sha256: str,
        byte_length: int,
        encoding: str,
        newline_style: str,
        storage_key: str,
        exact_text: str,
        idempotency_key: str | None,
    ) -> ImportResult:
        fingerprint = request_fingerprint(
            {
                "projectId": project_id,
                "displayName": display_name,
                "declaredFormat": declared_format,
                "contentSha256": byte_sha256,
            }
        )
        scope = f"import_story:{project_id}"
        with self.database.session() as session:
            project = self.require_project(session, project_id)
            if idempotency_key:
                existing_idempotency = session.get(
                    IdempotencyRow, {"scope": scope, "key": idempotency_key}
                )
                if existing_idempotency is not None:
                    if existing_idempotency.request_hash != fingerprint:
                        raise ServiceError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "That idempotency key was already used for another import.",
                        )
                    source = session.get(SourceDocumentRow, existing_idempotency.resource_id)
                    if source is None:
                        raise ServiceError(
                            500,
                            "IDEMPOTENCY_RECORD_INVALID",
                            "The saved import record is unavailable.",
                        )
                    story = session.scalar(
                        select(ImportedStoryRow).where(
                            ImportedStoryRow.source_document_id == source.id
                        )
                    )
                    if story is None:
                        raise ServiceError(
                            500,
                            "IDEMPOTENCY_RECORD_INVALID",
                            "The saved story record is unavailable.",
                        )
                    return ImportResult(_source_dict(source), _story_dict(session, story))

            source = session.scalar(
                select(SourceDocumentRow).where(
                    SourceDocumentRow.project_id == project_id,
                    SourceDocumentRow.content_sha256 == byte_sha256,
                )
            )
            if source is not None:
                story = session.scalar(
                    select(ImportedStoryRow).where(ImportedStoryRow.source_document_id == source.id)
                )
                if story is None:
                    raise ServiceError(
                        500,
                        "SOURCE_UNAVAILABLE",
                        "The imported story is unavailable.",
                    )
                if idempotency_key:
                    session.add(
                        IdempotencyRow(
                            scope=scope,
                            key=idempotency_key,
                            request_hash=fingerprint,
                            resource_id=source.id,
                            created_at=utc_now(),
                        )
                    )
                return ImportResult(_source_dict(source), _story_dict(session, story))

            now = utc_now()
            source_id = new_id()
            story_id = new_id()
            source_provenance = provenance(
                origin="import",
                actor_id="strict-text-importer@1.0.0",
                recorded_at=now,
                input_fingerprint=byte_sha256,
                notes=f"Strict {encoding} decode; original source bytes retained.",
            )
            source = SourceDocumentRow(
                id=source_id,
                project_id=project_id,
                display_name=display_name,
                media_type=media_type,
                declared_format=declared_format,
                content_sha256=byte_sha256,
                text_sha256=text_sha256,
                byte_length=byte_length,
                encoding=encoding,
                newline_style=newline_style,
                storage_key=storage_key,
                imported_at=now,
                revision=1,
                provenance_json=canonical_json(source_provenance),
                warnings_json="[]",
            )
            story = ImportedStoryRow(
                id=story_id,
                project_id=project_id,
                source_document_id=source_id,
                title=Path(display_name).stem,
                exact_text=exact_text,
                content_fingerprint=text_sha256,
                imported_at=now,
                revision=1,
                provenance_json=canonical_json(source_provenance),
                warnings_json="[]",
            )
            # Explicitly publish the parent source row before its story child. SQLAlchemy cannot
            # infer mapper ordering from relationships because persistence models intentionally
            # expose no cross-boundary ORM object graph.
            try:
                session.add(source)
                session.flush()
                session.add(story)
                project.story_id = story_id
                project.revision += 1
                project.updated_at = now
                if idempotency_key:
                    session.add(
                        IdempotencyRow(
                            scope=scope,
                            key=idempotency_key,
                            request_hash=fingerprint,
                            resource_id=source_id,
                            created_at=now,
                        )
                    )
                session.flush()
            except IntegrityError as exc:
                raise ServiceError(
                    409,
                    "IMPORT_CONFLICT",
                    "The source was imported concurrently; refresh the project.",
                ) from exc
            return ImportResult(_source_dict(source), _story_dict(session, story))

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
            story = session.get(ImportedStoryRow, project.story_id) if project.story_id else None
            if story is None:
                return {
                    "project": _project_dict(session, project),
                    "sourceDocuments": [_source_dict(source) for source in sources],
                    "story": None,
                    "chapters": [],
                    "scenes": [],
                    "beats": [],
                    "characters": [],
                    "dialogueLines": [],
                    "dialogueAttributions": [],
                    "castingAssignments": [],
                    "castingPlaceholders": [],
                    "approvals": [],
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
                "approvals": [],
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
                if character is None or character.project_id != project_id:
                    raise ServiceError(
                        422,
                        "INVALID_CHARACTER_REFERENCE",
                        "The selected character does not belong to this project.",
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
        suffix = Path(display_name).suffix.casefold()
        detected = _FORMAT_BY_SUFFIX.get(suffix)
        if detected is None:
            raise ServiceError(
                400,
                "UNSUPPORTED_IMPORT_FORMAT",
                "Only TXT and Markdown sources are supported in this release.",
            )
        detected_format, media_type = detected
        if declared_format is not None and declared_format not in {"txt", "markdown"}:
            raise ServiceError(
                400,
                "UNSUPPORTED_IMPORT_FORMAT",
                "Only TXT and Markdown sources are supported in this release.",
            )
        if declared_format is not None and declared_format != detected_format:
            raise ServiceError(
                400,
                "IMPORT_FORMAT_MISMATCH",
                "The declared source format does not match the filename.",
            )
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
            self._validate_signature(bytes(signature_prefix))
            exact_text, encoding = self._decode_text_file(
                staging_file,
                bytes(signature_prefix),
            )
            if not exact_text:
                raise ServiceError(422, "EMPTY_SOURCE", "The source contains no text.")
            if "\x00" in exact_text:
                raise ServiceError(
                    400,
                    "UNSAFE_TEXT_CONTENT",
                    "The source contains unsupported binary control data.",
                )
            newline_style = self._newline_style(exact_text)
            byte_hash = byte_digest.hexdigest()
            text_hash = sha256_text(exact_text)
            sources_root = ensure_private_directory(
                self.settings.data_dir / "projects" / project_id / "sources"
            )
            final_path = resolve_beneath(
                sources_root,
                f"sha256-{byte_hash}{suffix}",
            )
            if final_path.exists():
                if final_path.is_symlink():
                    raise ServiceError(
                        500,
                        "SOURCE_STORAGE_CONFLICT",
                        "The managed source location failed integrity verification.",
                    )
                existing_hash, existing_size = self._hash_file(final_path)
                if existing_hash != byte_hash or existing_size != byte_count:
                    raise ServiceError(
                        500,
                        "SOURCE_STORAGE_CONFLICT",
                        "The managed source location failed integrity verification.",
                    )
            else:
                os.replace(staging_file, final_path)
                published_hash, published_size = self._hash_file(final_path)
                if published_hash != byte_hash or published_size != byte_count:
                    raise ServiceError(
                        500,
                        "SOURCE_STORAGE_CONFLICT",
                        "The managed source location failed integrity verification.",
                    )
            storage_key = final_path.relative_to(self.settings.data_dir).as_posix()
            return self.projects.publish_import(
                project_id=project_id,
                display_name=display_name,
                declared_format=detected_format,
                media_type=media_type,
                byte_sha256=byte_hash,
                text_sha256=text_hash,
                byte_length=byte_count,
                encoding=encoding,
                newline_style=newline_style,
                storage_key=storage_key,
                exact_text=exact_text,
                idempotency_key=idempotency_key,
            )
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
    def _validate_signature(signature_prefix: bytes) -> None:
        if signature_prefix.startswith((b"MZ", b"\x7fELF", b"PK\x03\x04", b"%PDF-")):
            raise ServiceError(
                400,
                "UNSAFE_FILE_SIGNATURE",
                "The source signature does not match a plain text document.",
            )

    @staticmethod
    def _decode_text_file(path: Path, signature_prefix: bytes) -> tuple[str, str]:
        if signature_prefix.startswith(codecs.BOM_UTF8):
            encoding = "utf-8-sig"
        elif signature_prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            encoding = "utf-8"
        try:
            with path.open(
                "r",
                encoding=encoding,
                errors="strict",
                newline="",
            ) as source:
                return source.read(), encoding
        except (UnicodeDecodeError, UnicodeError) as exc:
            raise ServiceError(
                400,
                "SOURCE_DECODE_FAILED",
                "The source is not valid UTF-8 or BOM-marked UTF-16 text.",
            ) from exc

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(_IMPORT_CHUNK_BYTES):
                size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _newline_style(text: str) -> str:
        crlf_count = text.count("\r\n")
        without_crlf = text.replace("\r\n", "")
        lf_count = without_crlf.count("\n")
        cr_count = without_crlf.count("\r")
        styles = sum(count > 0 for count in (crlf_count, lf_count, cr_count))
        if styles == 0:
            return "none"
        if styles > 1:
            return "mixed"
        if crlf_count:
            return "crlf"
        if lf_count:
            return "lf"
        return "cr"
