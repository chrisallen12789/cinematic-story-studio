from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    story_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (CheckConstraint("revision >= 1", name="ck_projects_revision"),)


class SourceDocumentRow(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(80))
    declared_format: Mapped[str] = mapped_column(String(16))
    content_sha256: Mapped[str] = mapped_column(String(64))
    text_sha256: Mapped[str] = mapped_column(String(64))
    byte_length: Mapped[int] = mapped_column(Integer)
    encoding: Mapped[str] = mapped_column(String(24))
    newline_style: Mapped[str] = mapped_column(String(24))
    storage_key: Mapped[str] = mapped_column(String(512))
    imported_at: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provenance_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")

    __table_args__ = (
        UniqueConstraint("project_id", "content_sha256", name="uq_source_project_hash"),
        CheckConstraint("byte_length >= 0", name="ck_source_byte_length"),
        CheckConstraint("revision >= 1", name="ck_source_revision"),
        Index("ix_source_project_imported", "project_id", "imported_at", "id"),
    )


class ImportedStoryRow(Base):
    __tablename__ = "imported_stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), unique=True
    )
    title: Mapped[str] = mapped_column(String(255))
    exact_text: Mapped[str] = mapped_column(Text)
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provenance_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_story_revision"),
        Index("ix_story_project_imported", "project_id", "imported_at", "id"),
    )


class ChapterRow(Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    story_id: Mapped[str] = mapped_column(
        ForeignKey("imported_stories.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provenance_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("story_id", "ordinal", name="uq_chapter_story_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_chapter_ordinal"),
        CheckConstraint("start_offset >= 0 AND end_offset >= start_offset", name="ck_chapter_span"),
        Index("ix_chapter_project_story_order", "project_id", "story_id", "ordinal", "id"),
    )


class SceneRow(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mood: Mapped[str | None] = mapped_column(String(120), nullable=True)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    confidence_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("chapter_id", "ordinal", name="uq_scene_chapter_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_scene_ordinal"),
        CheckConstraint("start_offset >= 0 AND end_offset >= start_offset", name="ck_scene_span"),
        Index("ix_scene_project_chapter_order", "project_id", "chapter_id", "ordinal", "id"),
    )


class StoryBeatRow(Base):
    __tablename__ = "story_beats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(String(280), nullable=True)
    dialogue_line_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provenance_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("scene_id", "ordinal", name="uq_beat_scene_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_beat_ordinal"),
        CheckConstraint("start_offset >= 0 AND end_offset >= start_offset", name="ck_beat_span"),
        Index("ix_beat_project_scene_order", "project_id", "scene_id", "ordinal", "id"),
    )


class CharacterRow(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    story_id: Mapped[str] = mapped_column(
        ForeignKey("imported_stories.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120))
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    confidence_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("story_id", "normalized_name", name="uq_character_story_name"),
        Index("ix_character_project_story_name", "project_id", "story_id", "normalized_name"),
    )


class DialogueLineRow(Base):
    __tablename__ = "dialogue_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    beat_id: Mapped[str] = mapped_column(
        ForeignKey("story_beats.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    verbatim_text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provenance_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("scene_id", "ordinal", name="uq_line_scene_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_line_ordinal"),
        CheckConstraint("start_offset >= 0 AND end_offset >= start_offset", name="ck_line_span"),
        Index("ix_line_project_scene_order", "project_id", "scene_id", "ordinal", "id"),
    )


class DialogueAttributionRow(Base):
    __tablename__ = "dialogue_attributions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    line_id: Mapped[str] = mapped_column(
        ForeignKey("dialogue_lines.id", ondelete="CASCADE"), unique=True
    )
    proposed_speaker_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id", ondelete="RESTRICT"), nullable=True
    )
    effective_speaker_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id", ondelete="RESTRICT"), nullable=True
    )
    effective_authority: Mapped[str] = mapped_column(String(24))
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    confidence_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_attribution_revision"),
        Index("ix_attribution_project_line", "project_id", "line_id"),
    )


class HumanCorrectionRow(Base):
    __tablename__ = "human_corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    line_id: Mapped[str] = mapped_column(
        ForeignKey("dialogue_lines.id", ondelete="RESTRICT"), index=True
    )
    attribution_id: Mapped[str] = mapped_column(
        ForeignKey("dialogue_attributions.id", ondelete="RESTRICT"), index=True
    )
    previous_value_fingerprint: Mapped[str] = mapped_column(String(64))
    previous_character_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    corrected_character_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    actor_id: Mapped[str] = mapped_column(String(80))
    line_revision: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[str] = mapped_column(String(32))
    supersedes_correction_id: Mapped[str | None] = mapped_column(
        ForeignKey("human_corrections.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "line_id",
            "line_revision",
            name="uq_correction_line_revision",
        ),
        CheckConstraint("line_revision >= 2", name="ck_correction_line_revision"),
        Index("ix_correction_project_line_time", "project_id", "line_id", "recorded_at", "id"),
    )


class IdempotencyRow(Base):
    __tablename__ = "idempotency_records"

    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(32))


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(24))
    input_revision: Mapped[int] = mapped_column(Integer)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    current_attempt: Mapped[int] = mapped_column(Integer, default=1)
    stage: Mapped[str] = mapped_column(String(80))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint_available: Mapped[bool] = mapped_column(Boolean, default=False)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    resume_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
    terminal_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint("input_revision >= 1", name="ck_job_input_revision"),
        CheckConstraint("current_attempt >= 1", name="ck_job_attempt"),
        CheckConstraint("progress >= 0 AND progress <= 1000000", name="ck_job_progress"),
        Index("ix_job_project_created", "project_id", "created_at", "id"),
        Index("ix_job_queue", "state", "created_at", "id"),
    )


class JobAttemptRow(Base):
    __tablename__ = "job_attempts"

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    worker_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    producer_version: Mapped[str] = mapped_column(String(40))

    __table_args__ = (CheckConstraint("number >= 1", name="ck_attempt_number"),)


class JobEventRow(Base):
    __tablename__ = "job_events"

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warning_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_event_sequence"),
        CheckConstraint("attempt >= 1", name="ck_event_attempt"),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 1000000)",
            name="ck_event_progress",
        ),
        Index("ix_event_job_attempt_sequence", "job_id", "attempt", "sequence"),
    )


class JobCheckpointRow(Base):
    __tablename__ = "job_checkpoints"

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer)
    checkpoint_type: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[int] = mapped_column(Integer)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    producer_version: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("attempt >= 1", name="ck_checkpoint_attempt"),
        CheckConstraint("sequence >= 1", name="ck_checkpoint_sequence"),
        CheckConstraint("schema_version >= 1", name="ck_checkpoint_schema"),
    )
