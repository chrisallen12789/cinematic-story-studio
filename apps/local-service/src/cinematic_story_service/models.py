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
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_length: Mapped[int] = mapped_column(Integer)
    encoding: Mapped[str | None] = mapped_column(String(24), nullable=True)
    newline_style: Mapped[str | None] = mapped_column(String(24), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    imported_at: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source_revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True
    )
    extraction_status: Mapped[str] = mapped_column(String(24), default="complete")
    provenance_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_revision",
            name="uq_source_project_source_revision",
        ),
        CheckConstraint("byte_length >= 0", name="ck_source_byte_length"),
        CheckConstraint("revision >= 1", name="ck_source_revision"),
        CheckConstraint("source_revision >= 1", name="ck_source_logical_revision"),
        CheckConstraint(
            "extraction_status IN ('pending', 'running', 'complete', 'partial', 'failed')",
            name="ck_source_extraction_status",
        ),
        Index("ix_source_project_imported", "project_id", "imported_at", "id"),
        Index("ix_source_project_hash", "project_id", "content_sha256"),
    )


class DocumentExtractionRow(Base):
    """One immutable extraction result for a source revision.

    A source can be re-extracted by appending a higher extraction revision. Terminal
    records are never overwritten; lifecycle progress belongs to the job and parser
    execution records.
    """

    __tablename__ = "document_extractions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_extraction_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24))
    format: Mapped[str] = mapped_column(String(16))
    extractor_name: Mapped[str] = mapped_column(String(80))
    extractor_version: Mapped[str] = mapped_column(String(40))
    input_sha256: Mapped[str] = mapped_column(String(64))
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    character_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    encoding: Mapped[str | None] = mapped_column(String(24), nullable=True)
    newline_style: Mapped[str | None] = mapped_column(String(24), nullable=True)
    exact_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    manifest_json: Mapped[str] = mapped_column(Text)
    sections_json: Mapped[str] = mapped_column(Text, default="[]")
    source_mappings_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "revision",
            name="uq_extraction_source_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_extraction_revision"),
        CheckConstraint(
            "status IN ('pending', 'running', 'complete', 'partial', 'failed')",
            name="ck_extraction_status",
        ),
        CheckConstraint(
            "character_count IS NULL OR character_count >= 0",
            name="ck_extraction_character_count",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0",
            name="ck_extraction_page_count",
        ),
        Index(
            "ix_extraction_project_source_created",
            "project_id",
            "source_document_id",
            "created_at",
            "id",
        ),
    )


class ImportedStoryRow(Base):
    __tablename__ = "imported_stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"), unique=True
    )
    extraction_revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    exact_text: Mapped[str] = mapped_column(Text)
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provenance_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_story_revision"),
        CheckConstraint("extraction_revision >= 1", name="ck_story_extraction_revision"),
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


class ImportReviewRow(Base):
    """Append-only snapshot in the Import Review decision history."""

    __tablename__ = "import_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"), index=True
    )
    candidate_story_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    preview_text: Mapped[str] = mapped_column(Text)
    preview_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    warning_acknowledgements_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision_rationale: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    decided_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supersedes_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("import_reviews.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("review_id", "revision", name="uq_import_review_revision"),
        UniqueConstraint(
            "review_id",
            "idempotency_key",
            name="uq_import_review_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_import_review_revision"),
        CheckConstraint(
            "state IN ('pending', 'approved', 'changes_requested', 'rejected', 'invalidated')",
            name="ck_import_review_state",
        ),
        Index(
            "ix_import_review_project_created",
            "project_id",
            "created_at",
            "review_id",
            "revision",
        ),
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
    target_type: Mapped[str] = mapped_column(String(40), default="story")
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
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
        Index("ix_job_target", "target_type", "target_id", "created_at", "id"),
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


class ParserExecutionRow(Base):
    """Append-only parser outcome for one persisted extraction job attempt."""

    __tablename__ = "parser_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    parser_name: Mapped[str] = mapped_column(String(80))
    parser_version: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(24))
    input_sha256: Mapped[str] = mapped_column(String(64))
    limits_fingerprint: Mapped[str] = mapped_column(String(64))
    output_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_json: Mapped[str] = mapped_column(Text)
    sections_json: Mapped[str] = mapped_column(Text, default="[]")
    source_mappings_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[str] = mapped_column(String(32))
    finished_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("job_id", "attempt", name="uq_parser_job_attempt"),
        CheckConstraint("attempt >= 1", name="ck_parser_attempt"),
        CheckConstraint(
            "outcome IN ('succeeded', 'partial', 'failed', 'cancelled', 'interrupted')",
            name="ck_parser_outcome",
        ),
        Index(
            "ix_parser_extraction_attempt",
            "extraction_id",
            "attempt",
            "id",
        ),
    )


class AnalysisRunRow(Base):
    """Immutable governed input/configuration record for one whole-book analysis."""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    story_id: Mapped[str] = mapped_column(
        ForeignKey("imported_stories.id", ondelete="RESTRICT"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"), index=True
    )
    import_review_record_id: Mapped[str] = mapped_column(
        ForeignKey("import_reviews.id", ondelete="RESTRICT"), index=True
    )
    review_id: Mapped[str] = mapped_column(String(36))
    review_revision: Mapped[int] = mapped_column(Integer)
    review_decision_id: Mapped[str] = mapped_column(String(36))
    approval_evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    story_revision: Mapped[int] = mapped_column(Integer)
    extraction_revision: Mapped[int] = mapped_column(Integer)
    extracted_text_sha256: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    correction_set_fingerprint: Mapped[str] = mapped_column(String(64))
    profile_json: Mapped[str] = mapped_column(Text)
    profile_fingerprint: Mapped[str] = mapped_column(String(64))
    producer_id: Mapped[str] = mapped_column(String(80))
    producer_version: Mapped[str] = mapped_column(String(40))
    run_fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True, index=True
    )
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("review_revision >= 1", name="ck_analysis_run_review_revision"),
        CheckConstraint("source_revision >= 1", name="ck_analysis_run_source_revision"),
        CheckConstraint("story_revision >= 1", name="ck_analysis_run_story_revision"),
        CheckConstraint(
            "extraction_revision >= 1",
            name="ck_analysis_run_extraction_revision",
        ),
        Index(
            "ix_analysis_run_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_analysis_run_project_extraction",
            "project_id",
            "extraction_id",
            "extraction_revision",
            "id",
        ),
    )


class AnalysisExecutionRow(Base):
    """Append-only terminal evidence for one persisted analysis job attempt."""

    __tablename__ = "analysis_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    profile_fingerprint: Mapped[str] = mapped_column(String(64))
    agent_registry_fingerprint: Mapped[str] = mapped_column(String(64))
    output_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[str] = mapped_column(String(32))
    finished_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("run_id", "attempt", name="uq_analysis_execution_run_attempt"),
        UniqueConstraint("job_id", "attempt", name="uq_analysis_execution_job_attempt"),
        CheckConstraint("attempt >= 1", name="ck_analysis_execution_attempt"),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_analysis_execution_outcome",
        ),
        Index(
            "ix_analysis_execution_project_run_attempt",
            "project_id",
            "run_id",
            "attempt",
            "id",
        ),
    )


class AnalysisSnapshotRow(Base):
    """Immutable stage or final manifest produced by an analysis execution."""

    __tablename__ = "analysis_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(40))
    fingerprint: Mapped[str] = mapped_column(String(64))
    entity_count: Mapped[int] = mapped_column(Integer)
    manifest_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "ordinal",
            name="uq_analysis_snapshot_execution_ordinal",
        ),
        UniqueConstraint(
            "execution_id",
            "stage",
            name="uq_analysis_snapshot_execution_stage",
        ),
        CheckConstraint("ordinal >= 0", name="ck_analysis_snapshot_ordinal"),
        CheckConstraint("entity_count >= 0", name="ck_analysis_snapshot_entity_count"),
        Index(
            "ix_analysis_snapshot_project_run_order",
            "project_id",
            "run_id",
            "ordinal",
            "id",
        ),
    )


class AnalysisStageCheckpointRow(Base):
    """Append-only compatible checkpoint evidence for each governed job stage."""

    __tablename__ = "analysis_stage_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(48))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    profile_fingerprint: Mapped[str] = mapped_column(String(64))
    payload_fingerprint: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "attempt",
            "ordinal",
            name="uq_analysis_stage_checkpoint_ordinal",
        ),
        UniqueConstraint(
            "job_id",
            "attempt",
            "stage",
            name="uq_analysis_stage_checkpoint_stage",
        ),
        CheckConstraint("attempt >= 1", name="ck_analysis_stage_checkpoint_attempt"),
        CheckConstraint("ordinal >= 0", name="ck_analysis_stage_checkpoint_ordinal"),
        Index(
            "ix_analysis_stage_checkpoint_project_run_attempt",
            "project_id",
            "run_id",
            "attempt",
            "ordinal",
            "id",
        ),
    )


class AnalysisAgentExecutionRow(Base):
    """Bounded immutable runtime-agent envelope; agents have no storage authority."""

    __tablename__ = "analysis_agent_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(40))
    agent_id: Mapped[str] = mapped_column(String(80))
    agent_version: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(24))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    output_fingerprint: Mapped[str] = mapped_column(String(64))
    confidence_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    envelope_json: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(String(32))
    finished_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "ordinal",
            name="uq_analysis_agent_execution_ordinal",
        ),
        UniqueConstraint(
            "execution_id",
            "role",
            name="uq_analysis_agent_execution_role",
        ),
        CheckConstraint("ordinal >= 0", name="ck_analysis_agent_ordinal"),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'skipped', 'cancelled', 'interrupted')",
            name="ck_analysis_agent_outcome",
        ),
        Index(
            "ix_analysis_agent_project_run_order",
            "project_id",
            "run_id",
            "ordinal",
            "id",
        ),
    )


class AnalysisEntityRow(Base):
    """One immutable, independently pageable story-intelligence claim."""

    __tablename__ = "analysis_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="CASCADE"), index=True
    )
    collection: Mapped[str] = mapped_column(String(40))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    identity_key: Mapped[str] = mapped_column(String(160))
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    confidence_score: Mapped[int] = mapped_column(Integer)
    confidence_class: Mapped[str] = mapped_column(String(12))
    confidence_basis: Mapped[str] = mapped_column(String(160))
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "collection",
            "ordinal",
            name="uq_analysis_entity_run_collection_ordinal",
        ),
        CheckConstraint("ordinal >= 0", name="ck_analysis_entity_ordinal"),
        CheckConstraint("revision >= 1", name="ck_analysis_entity_revision"),
        CheckConstraint(
            "(start_offset IS NULL AND end_offset IS NULL) OR "
            "(start_offset >= 0 AND end_offset >= start_offset)",
            name="ck_analysis_entity_span",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1000000",
            name="ck_analysis_entity_confidence",
        ),
        CheckConstraint(
            "confidence_class IN ('unknown', 'low', 'medium', 'high')",
            name="ck_analysis_entity_confidence_class",
        ),
        Index(
            "ix_analysis_entity_project_run_collection_order",
            "project_id",
            "run_id",
            "collection",
            "ordinal",
            "id",
        ),
        Index(
            "ix_analysis_entity_project_run_identity",
            "project_id",
            "run_id",
            "collection",
            "identity_key",
            "id",
        ),
    )


class AnalysisEvidenceSpanRow(Base):
    """Bounded source-grounding metadata; excerpts are derived only at response time."""

    __tablename__ = "analysis_evidence_spans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_entities.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    text_sha256: Mapped[str] = mapped_column(String(64))
    basis: Mapped[str] = mapped_column(String(160))
    confidence_score: Mapped[int] = mapped_column(Integer)
    provenance_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "ordinal",
            name="uq_analysis_evidence_entity_ordinal",
        ),
        CheckConstraint("ordinal >= 0", name="ck_analysis_evidence_ordinal"),
        CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset",
            name="ck_analysis_evidence_span",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1000000",
            name="ck_analysis_evidence_confidence",
        ),
        Index(
            "ix_analysis_evidence_project_run_entity_order",
            "project_id",
            "run_id",
            "entity_id",
            "ordinal",
            "id",
        ),
    )


class AnalysisCorrectionRow(Base):
    """Append-only human correction overlay, including migrated speaker corrections."""

    __tablename__ = "analysis_corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(40))
    target_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_key: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer)
    expected_target_revision: Mapped[int] = mapped_column(Integer)
    expected_run_fingerprint: Mapped[str] = mapped_column(String(64))
    previous_value_fingerprint: Mapped[str] = mapped_column(String(64))
    patch_json: Mapped[str] = mapped_column(Text)
    correction_fingerprint: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80))
    supersedes_correction_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_corrections.id", ondelete="RESTRICT"), nullable=True
    )
    legacy_correction_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    recorded_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "category",
            "target_key",
            "revision",
            name="uq_analysis_correction_target_revision",
        ),
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_analysis_correction_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_analysis_correction_revision"),
        CheckConstraint(
            "expected_target_revision >= 1",
            name="ck_analysis_correction_expected_revision",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 1 AND length(reason) <= 1000",
            name="ck_analysis_correction_reason",
        ),
        Index(
            "ix_analysis_correction_project_run_recorded",
            "project_id",
            "run_id",
            "recorded_at",
            "id",
        ),
        Index(
            "ix_analysis_correction_project_target",
            "project_id",
            "category",
            "target_key",
            "revision",
            "id",
        ),
    )


class AnalysisReviewDecisionRow(Base):
    """Append-only state history for one of the four governed review gates."""

    __tablename__ = "analysis_review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), index=True
    )
    gate_id: Mapped[str] = mapped_column(String(48))
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24))
    artifact_fingerprint: Mapped[str] = mapped_column(String(64))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    eligible: Mapped[bool] = mapped_column(Boolean)
    rationale: Mapped[str] = mapped_column(String(4000), nullable=False)
    warning_acknowledgements_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    supersedes_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_review_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decided_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "gate_id",
            "revision",
            name="uq_analysis_review_gate_revision",
        ),
        UniqueConstraint(
            "run_id",
            "gate_id",
            "idempotency_key",
            name="uq_analysis_review_gate_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_analysis_review_revision"),
        CheckConstraint(
            "gate_id IN ("
            "'story_structure_review', "
            "'character_registry_review', "
            "'dialogue_attribution_review', "
            "'whole_book_analysis_review'"
            ")",
            name="ck_analysis_review_gate",
        ),
        CheckConstraint(
            "state IN ('pending', 'approved', 'rejected', 'changes_requested', 'invalidated')",
            name="ck_analysis_review_state",
        ),
        CheckConstraint(
            "length(trim(rationale)) >= 1 AND length(rationale) <= 4000",
            name="ck_analysis_review_rationale",
        ),
        Index(
            "ix_analysis_review_project_run_gate_revision",
            "project_id",
            "run_id",
            "gate_id",
            "revision",
            "id",
        ),
    )


class VoiceCatalogRevisionRow(Base):
    """Immutable provider-neutral catalog publication used by a casting run."""

    __tablename__ = "voice_catalog_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    catalog_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    semantic_version: Mapped[str] = mapped_column(String(40))
    catalog_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    provider_set_fingerprint: Mapped[str] = mapped_column(String(64))
    rights_policy_version: Mapped[str] = mapped_column(String(40))
    source_kind: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "catalog_id",
            "revision",
            name="uq_voice_catalog_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_voice_catalog_revision"),
        CheckConstraint(
            "source_kind IN ('development_fixture', 'local_static')",
            name="ck_voice_catalog_source_kind",
        ),
        Index(
            "ix_voice_catalog_active_created",
            "active",
            "created_at",
            "catalog_id",
            "revision",
            "id",
        ),
    )


class VoiceProviderDescriptorRow(Base):
    """One immutable provider capability declaration within a catalog revision."""

    __tablename__ = "voice_provider_descriptors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    catalog_revision_id: Mapped[str] = mapped_column(
        ForeignKey("voice_catalog_revisions.id", ondelete="CASCADE"),
        index=True,
    )
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    provider_type: Mapped[str] = mapped_column(String(32))
    runtime_availability: Mapped[str] = mapped_column(String(24))
    catalog_availability: Mapped[str] = mapped_column(String(24))
    synthesis_implemented: Mapped[bool] = mapped_column(Boolean)
    network_required: Mapped[bool] = mapped_column(Boolean)
    credentials_required: Mapped[bool] = mapped_column(Boolean)
    supported_operating_systems_json: Mapped[str] = mapped_column(Text)
    supported_languages_json: Mapped[str] = mapped_column(Text)
    output_capabilities_json: Mapped[str] = mapped_column(Text)
    rights_metadata_capabilities_json: Mapped[str] = mapped_column(Text)
    health_status: Mapped[str] = mapped_column(String(24))
    descriptor_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "catalog_revision_id",
            "provider_id",
            name="uq_voice_provider_catalog_provider",
        ),
        CheckConstraint(
            "provider_type IN ('local', 'cloud_capable_disabled', 'development_fixture')",
            name="ck_voice_provider_type",
        ),
        CheckConstraint(
            "runtime_availability IN ('available', 'unavailable', 'disabled', 'unknown')",
            name="ck_voice_provider_runtime_availability",
        ),
        CheckConstraint(
            "catalog_availability IN ('available', 'unavailable', 'unknown')",
            name="ck_voice_provider_catalog_availability",
        ),
        CheckConstraint(
            "health_status IN ('healthy', 'degraded', 'unavailable', 'disabled', 'unknown')",
            name="ck_voice_provider_health_status",
        ),
        Index(
            "ix_voice_provider_catalog_order",
            "catalog_revision_id",
            "provider_id",
            "id",
        ),
    )


class VoiceModelDescriptorRow(Base):
    """Immutable declared model capabilities; no capability is inferred at runtime."""

    __tablename__ = "voice_model_descriptors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    catalog_revision_id: Mapped[str] = mapped_column(
        ForeignKey("voice_catalog_revisions.id", ondelete="CASCADE"),
        index=True,
    )
    provider_descriptor_id: Mapped[str] = mapped_column(
        ForeignKey("voice_provider_descriptors.id", ondelete="CASCADE"),
        index=True,
    )
    model_id: Mapped[str] = mapped_column(String(80))
    model_name: Mapped[str] = mapped_column(String(120))
    model_version: Mapped[str] = mapped_column(String(40))
    supported_languages_json: Mapped[str] = mapped_column(Text)
    supported_locales_json: Mapped[str] = mapped_column(Text)
    expressive_controls_json: Mapped[str] = mapped_column(Text)
    speaking_rate_controls_json: Mapped[str] = mapped_column(Text)
    pitch_style_controls_json: Mapped[str] = mapped_column(Text)
    output_capabilities_json: Mapped[str] = mapped_column(Text)
    execution_classification: Mapped[str] = mapped_column(String(24))
    rights_classification: Mapped[str] = mapped_column(String(32))
    availability: Mapped[str] = mapped_column(String(24))
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    descriptor_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "catalog_revision_id",
            "model_id",
            name="uq_voice_model_catalog_model",
        ),
        CheckConstraint(
            "execution_classification IN ('local', 'remote_disabled', 'fixture')",
            name="ck_voice_model_execution",
        ),
        CheckConstraint(
            "availability IN ('available', 'unavailable', 'disabled', 'unknown')",
            name="ck_voice_model_availability",
        ),
        Index(
            "ix_voice_model_catalog_provider_order",
            "catalog_revision_id",
            "provider_descriptor_id",
            "model_id",
            "id",
        ),
    )


class VoiceProfileRow(Base):
    """Project-independent declared casting attributes for one catalog voice."""

    __tablename__ = "voice_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    profile_version: Mapped[str] = mapped_column(String(80))
    catalog_revision_id: Mapped[str] = mapped_column(
        ForeignKey("voice_catalog_revisions.id", ondelete="CASCADE"),
        index=True,
    )
    provider_descriptor_id: Mapped[str] = mapped_column(
        ForeignKey("voice_provider_descriptors.id", ondelete="CASCADE"),
        index=True,
    )
    model_descriptor_id: Mapped[str] = mapped_column(
        ForeignKey("voice_model_descriptors.id", ondelete="CASCADE"),
        index=True,
    )
    provider_voice_id: Mapped[str] = mapped_column(String(120))
    display_label: Mapped[str] = mapped_column(String(160))
    language: Mapped[str] = mapped_column(String(16))
    locale: Mapped[str] = mapped_column(String(32))
    declared_accent_dialect: Mapped[str | None] = mapped_column(String(120), nullable=True)
    declared_age_presentation_json: Mapped[str] = mapped_column(Text)
    declared_vocal_presentation: Mapped[str] = mapped_column(String(80))
    vocal_weight_texture_json: Mapped[str] = mapped_column(Text)
    pitch_range_classification: Mapped[str] = mapped_column(String(40))
    speaking_rate_range_json: Mapped[str] = mapped_column(Text)
    energy_range_json: Mapped[str] = mapped_column(Text)
    expressive_range_json: Mapped[str] = mapped_column(Text)
    narration_suitability: Mapped[str] = mapped_column(String(24))
    dialogue_suitability: Mapped[str] = mapped_column(String(24))
    long_form_suitability: Mapped[str] = mapped_column(String(24))
    character_role_suitability_json: Mapped[str] = mapped_column(Text)
    known_limitations_json: Mapped[str] = mapped_column(Text)
    rights_state: Mapped[str] = mapped_column(String(16))
    consent_status: Mapped[str] = mapped_column(String(24))
    license_scope: Mapped[str] = mapped_column(String(120))
    commercial_use_status: Mapped[str] = mapped_column(String(24))
    attribution_required: Mapped[bool] = mapped_column(Boolean)
    voice_cloning_classification: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    profile_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "catalog_revision_id",
            "profile_id",
            name="uq_voice_profile_catalog_profile",
        ),
        UniqueConstraint(
            "catalog_revision_id",
            "provider_descriptor_id",
            "model_descriptor_id",
            "provider_voice_id",
            name="uq_voice_profile_catalog_provider_voice",
        ),
        CheckConstraint("revision >= 1", name="ck_voice_profile_revision"),
        CheckConstraint(
            "narration_suitability IN ('preferred', 'suitable', 'limited', "
            "'unsuitable', 'unknown')",
            name="ck_voice_profile_narration_suitability",
        ),
        CheckConstraint(
            "dialogue_suitability IN ('preferred', 'suitable', 'limited', 'unsuitable', 'unknown')",
            name="ck_voice_profile_dialogue_suitability",
        ),
        CheckConstraint(
            "long_form_suitability IN ('preferred', 'suitable', 'limited', "
            "'unsuitable', 'unknown')",
            name="ck_voice_profile_long_form_suitability",
        ),
        CheckConstraint(
            "rights_state IN ('verified', 'restricted', 'unknown', 'prohibited')",
            name="ck_voice_profile_rights_state",
        ),
        CheckConstraint(
            "consent_status IN "
            "('not_applicable_synthetic_fixture', 'verified', 'restricted', "
            "'missing', 'unknown', 'prohibited')",
            name="ck_voice_profile_consent_status",
        ),
        CheckConstraint(
            "commercial_use_status IN ('permitted', 'restricted', 'unknown', 'prohibited')",
            name="ck_voice_profile_commercial_use",
        ),
        CheckConstraint(
            "state IN ('active', 'unavailable', 'deprecated', 'blocked')",
            name="ck_voice_profile_state",
        ),
        Index(
            "ix_voice_profile_catalog_state_label",
            "catalog_revision_id",
            "state",
            "display_label",
            "profile_id",
            "id",
        ),
        Index(
            "ix_voice_profile_catalog_language",
            "catalog_revision_id",
            "language",
            "locale",
            "state",
            "id",
        ),
    )


class VoiceRightsRecordRow(Base):
    """Versioned rights and consent evidence for one catalog voice profile."""

    __tablename__ = "voice_rights_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rights_record_id: Mapped[str] = mapped_column(String(128))
    voice_profile_record_id: Mapped[str] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="CASCADE"),
        index=True,
    )
    provider_descriptor_id: Mapped[str] = mapped_column(
        ForeignKey("voice_provider_descriptors.id", ondelete="RESTRICT"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    rights_state: Mapped[str] = mapped_column(String(16))
    license_identifier: Mapped[str | None] = mapped_column(String(160), nullable=True)
    rights_basis: Mapped[str] = mapped_column(String(500))
    license_scope: Mapped[str] = mapped_column(String(240))
    commercial_use_status: Mapped[str] = mapped_column(String(24))
    attribution_required: Mapped[bool] = mapped_column(Boolean)
    distribution_limitations_json: Mapped[str] = mapped_column(Text)
    voice_cloning_status: Mapped[str] = mapped_column(String(32))
    consent_status: Mapped[str] = mapped_column(String(24))
    effective_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expiration_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_reference: Mapped[str] = mapped_column(String(500))
    human_verification_status: Mapped[str] = mapped_column(String(24))
    rights_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "voice_profile_record_id",
            "revision",
            name="uq_voice_rights_profile_revision",
        ),
        UniqueConstraint(
            "voice_profile_record_id",
            "rights_record_id",
            name="uq_voice_rights_profile_external_id",
        ),
        CheckConstraint("revision >= 1", name="ck_voice_rights_revision"),
        CheckConstraint(
            "rights_state IN ('verified', 'restricted', 'unknown', 'prohibited')",
            name="ck_voice_rights_state",
        ),
        CheckConstraint(
            "commercial_use_status IN ('permitted', 'restricted', 'unknown', 'prohibited')",
            name="ck_voice_rights_commercial_use",
        ),
        CheckConstraint(
            "consent_status IN "
            "('not_applicable_synthetic_fixture', 'verified', 'restricted', "
            "'missing', 'unknown', 'prohibited')",
            name="ck_voice_rights_consent",
        ),
        CheckConstraint(
            "human_verification_status IN "
            "('verified', 'not_required_fixture', 'pending', 'rejected')",
            name="ck_voice_rights_human_verification",
        ),
        CheckConstraint(
            "voice_cloning_status IN "
            "('not_applicable_synthetic_fixture', 'not_permitted', "
            "'permitted_with_consent', 'unknown', 'prohibited')",
            name="ck_voice_rights_cloning_status",
        ),
        Index(
            "ix_voice_rights_profile_revision",
            "voice_profile_record_id",
            "revision",
            "id",
        ),
    )


class CastingProfileRow(Base):
    """Immutable deterministic ruleset for provider-neutral candidate generation."""

    __tablename__ = "casting_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(80))
    semantic_version: Mapped[str] = mapped_column(String(40))
    producer_id: Mapped[str] = mapped_column(String(80))
    producer_version: Mapped[str] = mapped_column(String(40))
    compatibility_rules_json: Mapped[str] = mapped_column(Text)
    hard_constraints_json: Mapped[str] = mapped_column(Text)
    soft_preferences_json: Mapped[str] = mapped_column(Text)
    conflict_rules_json: Mapped[str] = mapped_column(Text)
    rights_eligibility_rules_json: Mapped[str] = mapped_column(Text)
    pre_reduction_candidate_limit: Mapped[int] = mapped_column(Integer)
    candidate_limit: Mapped[int] = mapped_column(Integer)
    explanation_requirements_json: Mapped[str] = mapped_column(Text)
    profile_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "semantic_version",
            name="uq_casting_profile_version",
        ),
        CheckConstraint(
            "pre_reduction_candidate_limit >= 1 AND pre_reduction_candidate_limit <= 50",
            name="ck_casting_profile_pre_reduction_limit",
        ),
        CheckConstraint(
            "candidate_limit >= 1 AND candidate_limit <= pre_reduction_candidate_limit",
            name="ck_casting_profile_candidate_limit",
        ),
        Index(
            "ix_casting_profile_identity",
            "profile_id",
            "semantic_version",
            "id",
        ),
    )


class CastingRunRow(Base):
    """Immutable Phase 2/catalog/profile evidence envelope for one casting run."""

    __tablename__ = "casting_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        index=True,
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"),
        index=True,
    )
    extraction_revision: Mapped[int] = mapped_column(Integer)
    extracted_text_sha256: Mapped[str] = mapped_column(String(64))
    import_review_decision_id: Mapped[str] = mapped_column(String(36))
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    analysis_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    analysis_snapshot_revision: Mapped[int] = mapped_column(Integer)
    analysis_snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    analysis_correction_set_fingerprint: Mapped[str] = mapped_column(String(64))
    character_registry_fingerprint: Mapped[str] = mapped_column(String(64))
    phase2_gate_decision_ids_json: Mapped[str] = mapped_column(Text)
    phase2_gate_evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    casting_profile_id: Mapped[str] = mapped_column(
        ForeignKey("casting_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    casting_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    catalog_revision_id: Mapped[str] = mapped_column(
        ForeignKey("voice_catalog_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    effective_correction_set_fingerprint: Mapped[str] = mapped_column(String(64))
    producer_id: Mapped[str] = mapped_column(String(80))
    producer_version: Mapped[str] = mapped_column(String(40))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    run_fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(24))
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String(32))
    published_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint("source_revision >= 1", name="ck_casting_run_source_revision"),
        CheckConstraint(
            "extraction_revision >= 1",
            name="ck_casting_run_extraction_revision",
        ),
        CheckConstraint(
            "analysis_snapshot_revision >= 1",
            name="ck_casting_run_snapshot_revision",
        ),
        CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_casting_run_state",
        ),
        Index(
            "ix_casting_run_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_casting_run_project_analysis",
            "project_id",
            "analysis_run_id",
            "analysis_snapshot_id",
            "created_at",
            "id",
        ),
    )


class ProductionRoleRow(Base):
    """One stable, bounded voice-bearing role derived from approved Phase 2 evidence."""

    __tablename__ = "production_roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    role_type: Mapped[str] = mapped_column(String(40))
    phase2_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    character_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role_importance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    effective_display_label: Mapped[str] = mapped_column(String(200))
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    analysis_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    dialogue_line_count: Mapped[int] = mapped_column(Integer)
    narration_span_count: Mapped[int] = mapped_column(Integer)
    approximate_word_count: Mapped[int] = mapped_column(Integer)
    chapter_range_json: Mapped[str] = mapped_column(Text)
    scene_range_json: Mapped[str] = mapped_column(Text)
    language_requirements_json: Mapped[str] = mapped_column(Text)
    performance_requirements_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    role_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "casting_run_id",
            "ordinal",
            name="uq_production_role_run_ordinal",
        ),
        CheckConstraint("ordinal >= 0", name="ck_production_role_ordinal"),
        CheckConstraint(
            "role_type IN "
            "('primary_narrator', 'secondary_narrator', 'named_character', "
            "'unresolved_speaker', 'group_or_crowd', "
            "'quoted_document_or_announcement', 'internal_thought', 'custom')",
            name="ck_production_role_type",
        ),
        CheckConstraint(
            "role_importance IS NULL OR role_importance IN "
            "('major', 'supporting', 'minor', 'unresolved')",
            name="ck_production_role_importance",
        ),
        CheckConstraint(
            "dialogue_line_count >= 0 AND narration_span_count >= 0 "
            "AND approximate_word_count >= 0",
            name="ck_production_role_workload",
        ),
        CheckConstraint(
            "status IN ('active', 'unresolved', 'intentionally_uncast', 'invalidated')",
            name="ck_production_role_status",
        ),
        Index(
            "ix_production_role_project_run_order",
            "project_id",
            "casting_run_id",
            "ordinal",
            "id",
        ),
        Index(
            "ix_production_role_project_type",
            "project_id",
            "casting_run_id",
            "role_type",
            "ordinal",
            "id",
        ),
    )


class CastingCandidateRow(Base):
    """Explainable machine assessment for one bounded role/voice pair."""

    __tablename__ = "casting_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="CASCADE"),
        index=True,
    )
    voice_profile_record_id: Mapped[str] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    role_revision: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    compatibility_status: Mapped[str] = mapped_column(String(16))
    compatibility_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_class: Mapped[str] = mapped_column(String(12))
    hard_constraint_results_json: Mapped[str] = mapped_column(Text)
    soft_preference_results_json: Mapped[str] = mapped_column(Text)
    rights_eligibility: Mapped[str] = mapped_column(String(16))
    language_eligibility: Mapped[str] = mapped_column(String(16))
    provider_availability: Mapped[str] = mapped_column(String(16))
    model_availability: Mapped[str] = mapped_column(String(16))
    long_form_suitability: Mapped[str] = mapped_column(String(24))
    conflict_warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    explanation_json: Mapped[str] = mapped_column(Text)
    provenance_json: Mapped[str] = mapped_column(Text)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    output_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "role_revision",
            "voice_profile_record_id",
            name="uq_casting_candidate_role_revision_voice",
        ),
        UniqueConstraint(
            "role_id",
            "role_revision",
            "ordinal",
            name="uq_casting_candidate_role_revision_ordinal",
        ),
        CheckConstraint("role_revision >= 1", name="ck_casting_candidate_role_revision"),
        CheckConstraint("ordinal >= 0 AND ordinal < 50", name="ck_casting_candidate_ordinal"),
        CheckConstraint(
            "compatibility_status IN ('eligible', 'conditional', 'ineligible', 'unknown')",
            name="ck_casting_candidate_status",
        ),
        CheckConstraint(
            "compatibility_score IS NULL OR "
            "(compatibility_score >= 0 AND compatibility_score <= 1000000)",
            name="ck_casting_candidate_score",
        ),
        CheckConstraint(
            "confidence_class IN ('unknown', 'low', 'medium', 'high')",
            name="ck_casting_candidate_confidence",
        ),
        CheckConstraint(
            "rights_eligibility IN ('eligible', 'restricted', 'ineligible', 'unknown')",
            name="ck_casting_candidate_rights",
        ),
        Index(
            "ix_casting_candidate_project_run_role_order",
            "project_id",
            "casting_run_id",
            "role_id",
            "role_revision",
            "ordinal",
            "id",
        ),
        Index(
            "ix_casting_candidate_role_score",
            "role_id",
            "role_revision",
            "compatibility_status",
            "compatibility_score",
            "ordinal",
            "id",
        ),
    )


class CastingConflictRow(Base):
    """Metadata-only differentiation, availability, reuse, and rights risk."""

    __tablename__ = "casting_conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    primary_role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="CASCADE"),
        index=True,
    )
    secondary_role_id: Mapped[str | None] = mapped_column(
        ForeignKey("production_roles.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    voice_profile_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    details_json: Mapped[str] = mapped_column(Text)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(
            "category IN "
            "('incompatible_voice_reuse', 'narrator_major_character_reuse', "
            "'metadata_similarity_risk', 'accent_or_locale_mismatch', "
            "'insufficient_expressive_range', 'rights_conflict', "
            "'provider_or_model_unavailable', 'deprecated_voice', "
            "'role_length_suitability', 'unresolved_role_assignment', "
            "'voice_reuse_threshold_exceeded')",
            name="ck_casting_conflict_category",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'blocking')",
            name="ck_casting_conflict_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'superseded')",
            name="ck_casting_conflict_status",
        ),
        Index(
            "ix_casting_conflict_project_run_status",
            "project_id",
            "casting_run_id",
            "status",
            "severity",
            "id",
        ),
        Index(
            "ix_casting_conflict_run_roles",
            "casting_run_id",
            "primary_role_id",
            "secondary_role_id",
            "category",
            "id",
        ),
    )


class CastAssignmentRow(Base):
    """Immutable machine proposal or human-authority assignment revision."""

    __tablename__ = "cast_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="CASCADE"),
        index=True,
    )
    correction_id: Mapped[str | None] = mapped_column(
        ForeignKey("casting_corrections.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    voice_profile_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    catalog_revision_id: Mapped[str] = mapped_column(
        ForeignKey("voice_catalog_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    casting_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    phase2_snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    effective_correction_set_fingerprint: Mapped[str] = mapped_column(String(64))
    authority: Mapped[str] = mapped_column(String(24))
    assignment_state: Mapped[str] = mapped_column(String(24))
    rationale: Mapped[str] = mapped_column(String(4000))
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    rights_state: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    provenance_json: Mapped[str] = mapped_column(Text)
    supersedes_assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("cast_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "revision",
            name="uq_cast_assignment_role_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_cast_assignment_revision"),
        CheckConstraint(
            "authority IN ('machine_proposal', 'human_selection', 'human_locked')",
            name="ck_cast_assignment_authority",
        ),
        CheckConstraint(
            "assignment_state IN "
            "('proposed', 'selected', 'locked', 'cleared', 'intentionally_uncast')",
            name="ck_cast_assignment_state",
        ),
        CheckConstraint(
            "rights_state IN ('verified', 'restricted', 'unknown', 'prohibited')",
            name="ck_cast_assignment_rights_state",
        ),
        CheckConstraint(
            "(authority = 'machine_proposal' AND correction_id IS NULL) OR "
            "(authority IN ('human_selection', 'human_locked') "
            "AND correction_id IS NOT NULL)",
            name="ck_cast_assignment_correction_authority",
        ),
        CheckConstraint(
            "length(trim(rationale)) >= 1 AND length(rationale) <= 4000",
            name="ck_cast_assignment_rationale",
        ),
        Index(
            "ix_cast_assignment_project_run_role_revision",
            "project_id",
            "casting_run_id",
            "role_id",
            "revision",
            "id",
        ),
    )


class CastAssignmentInvalidationRow(Base):
    """Append-only evidence that external catalog or rights drift invalidated an assignment."""

    __tablename__ = "cast_assignment_invalidations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="CASCADE"),
        index=True,
    )
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("cast_assignments.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    reason_codes_json: Mapped[str] = mapped_column(Text)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        Index(
            "ix_cast_assignment_invalidation_project_run_role",
            "project_id",
            "casting_run_id",
            "role_id",
            "created_at",
            "id",
        ),
    )


class CastingCorrectionRow(Base):
    """Append-only human correction overlay; automated reruns cannot overwrite it."""

    __tablename__ = "casting_corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="RESTRICT"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(48))
    revision: Mapped[int] = mapped_column(Integer)
    prior_effective_fingerprint: Mapped[str] = mapped_column(String(64))
    corrected_value_json: Mapped[str] = mapped_column(Text)
    correction_fingerprint: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(2000))
    provenance_json: Mapped[str] = mapped_column(Text)
    supersedes_correction_id: Mapped[str | None] = mapped_column(
        ForeignKey("casting_corrections.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    recorded_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "revision",
            name="uq_casting_correction_role_revision",
        ),
        UniqueConstraint(
            "casting_run_id",
            "idempotency_key",
            name="uq_casting_correction_idempotency",
        ),
        UniqueConstraint(
            "supersedes_correction_id",
            name="uq_casting_correction_single_successor",
        ),
        CheckConstraint("revision >= 1", name="ck_casting_correction_revision"),
        CheckConstraint(
            "kind IN "
            "('select_voice', 'clear_assignment', 'lock_assignment', "
            "'unlock_assignment', 'mark_intentionally_uncast', 'change_role_label', "
            "'change_casting_requirement', 'acknowledge_restricted_rights', "
            "'approve_voice_reuse', 'reject_candidate', 'record_custom_rationale')",
            name="ck_casting_correction_kind",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 1 AND length(reason) <= 2000",
            name="ck_casting_correction_reason",
        ),
        Index(
            "ix_casting_correction_project_run_role_revision",
            "project_id",
            "casting_run_id",
            "role_id",
            "revision",
            "id",
        ),
        Index(
            "ix_casting_correction_project_recorded",
            "project_id",
            "recorded_at",
            "id",
        ),
    )


class ApprovedCastSnapshotRow(Base):
    """Immutable review artifact; the table name does not imply automatic approval."""

    __tablename__ = "approved_cast_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    phase2_snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    catalog_revision_id: Mapped[str] = mapped_column(
        ForeignKey("voice_catalog_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    casting_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    effective_correction_set_fingerprint: Mapped[str] = mapped_column(String(64))
    role_count: Mapped[int] = mapped_column(Integer)
    assignment_count: Mapped[int] = mapped_column(Integer)
    unresolved_role_count: Mapped[int] = mapped_column(Integer)
    restricted_rights_count: Mapped[int] = mapped_column(Integer)
    ineligible_rights_count: Mapped[int] = mapped_column(Integer)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    manifest_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "casting_run_id",
            "revision",
            name="uq_approved_cast_snapshot_run_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_approved_cast_snapshot_revision"),
        CheckConstraint(
            "role_count >= 0 AND assignment_count >= 0 "
            "AND unresolved_role_count >= 0 AND restricted_rights_count >= 0 "
            "AND ineligible_rights_count >= 0",
            name="ck_approved_cast_snapshot_counts",
        ),
        Index(
            "ix_approved_cast_snapshot_project_run_revision",
            "project_id",
            "casting_run_id",
            "revision",
            "id",
        ),
    )


class CastingGateReviewRow(Base):
    """Immutable eligibility evaluation for one casting approval gate revision."""

    __tablename__ = "casting_gate_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    cast_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("approved_cast_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    gate_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    eligible: Mapped[bool] = mapped_column(Boolean)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    required_gate_decision_ids_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "casting_run_id",
            "gate_id",
            "revision",
            name="uq_casting_gate_review_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_casting_gate_review_revision"),
        CheckConstraint(
            "gate_id IN "
            "('narrator_casting_review', 'character_casting_review', "
            "'complete_cast_review')",
            name="ck_casting_gate_review_gate",
        ),
        Index(
            "ix_casting_gate_review_project_run_gate_revision",
            "project_id",
            "casting_run_id",
            "gate_id",
            "revision",
            "id",
        ),
    )


class CastingGateDecisionRow(Base):
    """Append-only human decision history for the three governed casting gates."""

    __tablename__ = "casting_gate_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="CASCADE"),
        index=True,
    )
    cast_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("approved_cast_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    gate_review_id: Mapped[str] = mapped_column(
        ForeignKey("casting_gate_reviews.id", ondelete="RESTRICT"),
        index=True,
    )
    gate_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(24))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    warning_acknowledgements_json: Mapped[str] = mapped_column(Text, default="[]")
    rationale: Mapped[str] = mapped_column(String(4000))
    provenance_json: Mapped[str] = mapped_column(Text)
    supersedes_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("casting_gate_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    decided_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "casting_run_id",
            "gate_id",
            "revision",
            name="uq_casting_gate_decision_revision",
        ),
        UniqueConstraint(
            "casting_run_id",
            "gate_id",
            "idempotency_key",
            name="uq_casting_gate_decision_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_casting_gate_decision_revision"),
        CheckConstraint(
            "gate_id IN "
            "('narrator_casting_review', 'character_casting_review', "
            "'complete_cast_review')",
            name="ck_casting_gate_decision_gate",
        ),
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'changes_requested', 'invalidated')",
            name="ck_casting_gate_decision_state",
        ),
        CheckConstraint(
            "length(trim(rationale)) >= 1 AND length(rationale) <= 4000",
            name="ck_casting_gate_decision_rationale",
        ),
        Index(
            "ix_casting_gate_decision_project_run_gate_revision",
            "project_id",
            "casting_run_id",
            "gate_id",
            "revision",
            "id",
        ),
    )


class SpeechRuntimeProfileRow(Base):
    """Immutable managed-runtime policy and compatibility identity."""

    __tablename__ = "speech_runtime_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(80))
    profile_version: Mapped[str] = mapped_column(String(40))
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    runtime_id: Mapped[str] = mapped_column(String(80))
    runtime_version: Mapped[str] = mapped_column(String(40))
    protocol_version: Mapped[str] = mapped_column(String(40))
    platform: Mapped[str] = mapped_column(String(24))
    architecture: Mapped[str] = mapped_column(String(24))
    network_policy: Mapped[str] = mapped_column(String(24))
    startup_timeout_ms: Mapped[int] = mapped_column(Integer)
    request_timeout_ms: Mapped[int] = mapped_column(Integer)
    idle_shutdown_ms: Mapped[int] = mapped_column(Integer)
    maximum_concurrency: Mapped[int] = mapped_column(Integer)
    output_format_json: Mapped[str] = mapped_column(Text)
    limits_json: Mapped[str] = mapped_column(Text)
    profile_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "profile_version",
            name="uq_speech_runtime_profile_version",
        ),
        CheckConstraint(
            "network_policy = 'deny_during_synthesis'",
            name="ck_speech_runtime_profile_network",
        ),
        CheckConstraint(
            "platform = 'windows' AND architecture IN ('x64', 'arm64')",
            name="ck_speech_runtime_profile_platform",
        ),
        CheckConstraint(
            "startup_timeout_ms >= 1 AND startup_timeout_ms <= 300000 "
            "AND request_timeout_ms >= 1 AND request_timeout_ms <= 300000 "
            "AND idle_shutdown_ms >= 0 AND idle_shutdown_ms <= 3600000",
            name="ck_speech_runtime_profile_deadlines",
        ),
        CheckConstraint(
            "maximum_concurrency >= 1 AND maximum_concurrency <= 16",
            name="ck_speech_runtime_profile_concurrency",
        ),
        Index(
            "ix_speech_runtime_profile_provider_active",
            "provider_id",
            "active",
            "profile_version",
            "id",
        ),
    )


class ModelPackageManifestRow(Base):
    """Allow-listed model-package inventory and immutable trust evidence."""

    __tablename__ = "model_package_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(String(120))
    manifest_version: Mapped[str] = mapped_column(String(40))
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    model_id: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(40))
    runtime_id: Mapped[str] = mapped_column(String(80))
    runtime_version: Mapped[str] = mapped_column(String(40))
    platform: Mapped[str] = mapped_column(String(24))
    architecture: Mapped[str] = mapped_column(String(24))
    source_classification: Mapped[str] = mapped_column(String(32))
    official_source_reference: Mapped[str] = mapped_column(String(1000))
    license_identifier: Mapped[str] = mapped_column(String(160))
    commercial_use_classification: Mapped[str] = mapped_column(String(32))
    attribution_requirements_json: Mapped[str] = mapped_column(Text)
    file_inventory_json: Mapped[str] = mapped_column(Text)
    file_count: Mapped[int] = mapped_column(Integer)
    total_expanded_size: Mapped[int] = mapped_column(Integer)
    package_archive_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    required_runtime_dependencies_json: Mapped[str] = mapped_column(Text)
    compatibility_constraints_json: Mapped[str] = mapped_column(Text)
    revocation_state: Mapped[str] = mapped_column(String(24))
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "package_id",
            "manifest_version",
            name="uq_model_package_manifest_version",
        ),
        CheckConstraint(
            "source_classification IN "
            "('official_release', 'official_model_repository', "
            "'maintainer_referenced_conversion', 'repository_fixture')",
            name="ck_model_package_source",
        ),
        CheckConstraint(
            "commercial_use_classification IN "
            "('allowed', 'restricted', 'fixture_only', 'unknown')",
            name="ck_model_package_commercial_use",
        ),
        CheckConstraint(
            "revocation_state IN ('active', 'deprecated', 'revoked')",
            name="ck_model_package_revocation",
        ),
        CheckConstraint(
            "file_count >= 1 AND file_count <= 4096 "
            "AND total_expanded_size >= 1",
            name="ck_model_package_inventory_size",
        ),
        Index(
            "ix_model_package_provider_model_version",
            "provider_id",
            "model_id",
            "model_version",
            "revocation_state",
            "id",
        ),
    )


class ModelInstallationRow(Base):
    """Append-only state transition for one stable managed installation."""

    __tablename__ = "model_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(36))
    manifest_id: Mapped[str] = mapped_column(
        ForeignKey("model_package_manifests.id", ondelete="RESTRICT"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(24))
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    installed_byte_count: Mapped[int] = mapped_column(Integer)
    package_fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    supersedes_installation_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_installations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str] = mapped_column(String(1000))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
    completed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "revision",
            name="uq_model_installation_revision",
        ),
        UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_model_installation_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_model_installation_revision"),
        CheckConstraint(
            "operation IN ('install', 'verify', 'activate', 'deactivate', 'repair', 'remove')",
            name="ck_model_installation_operation",
        ),
        CheckConstraint(
            "state IN "
            "('pending', 'installed', 'active', 'inactive', "
            "'repair_required', 'removed', 'failed')",
            name="ck_model_installation_state",
        ),
        CheckConstraint(
            "installed_byte_count >= 0",
            name="ck_model_installation_byte_count",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 1 AND length(reason) <= 1000",
            name="ck_model_installation_reason",
        ),
        Index(
            "ix_model_installation_identity_revision",
            "installation_id",
            "revision",
            "id",
        ),
        Index(
            "ix_model_installation_manifest_state",
            "manifest_id",
            "state",
            "created_at",
            "id",
        ),
    )


class ModelVerificationRow(Base):
    """Immutable exact-file verification evidence for an installation revision."""

    __tablename__ = "model_verifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_record_id: Mapped[str] = mapped_column(
        ForeignKey("model_installations.id", ondelete="RESTRICT"),
        index=True,
    )
    installation_id: Mapped[str] = mapped_column(String(36))
    manifest_id: Mapped[str] = mapped_column(
        ForeignKey("model_package_manifests.id", ondelete="RESTRICT"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24))
    manifest_fingerprint: Mapped[str] = mapped_column(String(64))
    package_fingerprint: Mapped[str] = mapped_column(String(64))
    verified_file_count: Mapped[int] = mapped_column(Integer)
    verified_byte_count: Mapped[int] = mapped_column(Integer)
    verifier_id: Mapped[str] = mapped_column(String(80))
    verifier_version: Mapped[str] = mapped_column(String(40))
    findings_json: Mapped[str] = mapped_column(Text)
    verification_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    supersedes_verification_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_verifications.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provenance_json: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(String(32))
    finished_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "revision",
            name="uq_model_verification_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_model_verification_revision"),
        CheckConstraint(
            "outcome IN ('verified', 'mismatch', 'missing', 'unsafe')",
            name="ck_model_verification_outcome",
        ),
        CheckConstraint(
            "verified_file_count >= 0 AND verified_byte_count >= 0",
            name="ck_model_verification_counts",
        ),
        Index(
            "ix_model_verification_installation_revision",
            "installation_id",
            "revision",
            "id",
        ),
    )


class SpeechRuntimeInstanceRow(Base):
    """One managed worker process identity and bounded lifecycle record."""

    __tablename__ = "speech_runtime_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    runtime_profile_id: Mapped[str] = mapped_column(
        ForeignKey("speech_runtime_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    model_installation_record_id: Mapped[str] = mapped_column(
        ForeignKey("model_installations.id", ondelete="RESTRICT"),
        index=True,
    )
    model_verification_id: Mapped[str] = mapped_column(
        ForeignKey("model_verifications.id", ondelete="RESTRICT"),
        index=True,
    )
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    runtime_id: Mapped[str] = mapped_column(String(80))
    runtime_version: Mapped[str] = mapped_column(String(40))
    model_id: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(40))
    model_package_fingerprint: Mapped[str] = mapped_column(String(64))
    runtime_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    protocol_version: Mapped[str] = mapped_column(String(40))
    handshake_fingerprint: Mapped[str] = mapped_column(String(64))
    worker_pid: Mapped[int] = mapped_column(Integer)
    parent_pid: Mapped[int] = mapped_column(Integer)
    executable_identity: Mapped[str] = mapped_column(String(200))
    executable_sha256: Mapped[str] = mapped_column(String(64))
    creation_identity: Mapped[str] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(24))
    health_status: Mapped[str] = mapped_column(String(24))
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(String(32))
    ready_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_health_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stopped_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint("worker_pid >= 1 AND parent_pid >= 1", name="ck_speech_runtime_pids"),
        CheckConstraint(
            "state IN "
            "('starting', 'ready', 'busy', 'idle', 'stopping', 'stopped', 'failed')",
            name="ck_speech_runtime_state",
        ),
        CheckConstraint(
            "health_status IN "
            "('available', 'degraded', 'unavailable', 'unauthorized', 'disabled', 'restricted')",
            name="ck_speech_runtime_health",
        ),
        Index(
            "ix_speech_runtime_profile_state_started",
            "runtime_profile_id",
            "state",
            "started_at",
            "id",
        ),
        Index(
            "ix_speech_runtime_process_identity",
            "parent_pid",
            "worker_pid",
            "creation_identity",
            "id",
        ),
    )


class VoiceRuntimeBindingRow(Base):
    """Exact governed catalog voice to local runtime/model/voice binding."""

    __tablename__ = "voice_runtime_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    binding_kind: Mapped[str] = mapped_column(String(32))
    voice_profile_record_id: Mapped[str] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    voice_profile_id: Mapped[str] = mapped_column(String(80))
    voice_profile_version: Mapped[str] = mapped_column(String(40))
    voice_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    source_provider_descriptor_id: Mapped[str] = mapped_column(
        ForeignKey("voice_provider_descriptors.id", ondelete="RESTRICT"),
        index=True,
    )
    source_provider_id: Mapped[str] = mapped_column(String(80))
    source_provider_version: Mapped[str] = mapped_column(String(40))
    source_provider_fingerprint: Mapped[str] = mapped_column(String(64))
    source_model_descriptor_id: Mapped[str] = mapped_column(
        ForeignKey("voice_model_descriptors.id", ondelete="RESTRICT"),
        index=True,
    )
    source_model_id: Mapped[str] = mapped_column(String(80))
    source_model_version: Mapped[str] = mapped_column(String(40))
    source_model_fingerprint: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    provider_voice_id: Mapped[str] = mapped_column(String(120))
    model_id: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(40))
    model_manifest_id: Mapped[str] = mapped_column(
        ForeignKey("model_package_manifests.id", ondelete="RESTRICT"),
        index=True,
    )
    model_package_id: Mapped[str] = mapped_column(String(120))
    model_package_fingerprint: Mapped[str] = mapped_column(String(64))
    runtime_profile_record_id: Mapped[str] = mapped_column(
        ForeignKey("speech_runtime_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    runtime_profile_id: Mapped[str] = mapped_column(String(120))
    runtime_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    binding_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(
            "binding_kind IN ('exact_provider_match', 'declared_fixture_adapter')",
            name="ck_voice_runtime_binding_kind",
        ),
        UniqueConstraint(
            "voice_profile_record_id",
            "provider_id",
            "model_package_fingerprint",
            "runtime_profile_fingerprint",
            "provider_voice_id",
            name="uq_voice_runtime_binding_exact_target",
        ),
        CheckConstraint(
            "length(voice_profile_fingerprint) = 64 "
            "AND length(source_provider_fingerprint) = 64 "
            "AND length(source_model_fingerprint) = 64 "
            "AND length(model_package_fingerprint) = 64 "
            "AND length(runtime_profile_fingerprint) = 64 "
            "AND length(binding_fingerprint) = 64",
            name="ck_voice_runtime_binding_fingerprints",
        ),
        Index(
            "ix_voice_runtime_binding_profile_active",
            "voice_profile_record_id",
            "active",
            "created_at",
            "id",
        ),
        Index(
            "ix_voice_runtime_binding_target",
            "provider_id",
            "model_id",
            "provider_voice_id",
            "active",
            "id",
        ),
    )


class PronunciationDictionaryRow(Base):
    """Immutable project dictionary revision and active-entry manifest."""

    __tablename__ = "pronunciation_dictionaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    dictionary_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    default_language: Mapped[str] = mapped_column(String(16))
    default_locale: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entry_count: Mapped[int] = mapped_column(Integer)
    active_entry_ids_json: Mapped[str] = mapped_column(Text)
    dictionary_fingerprint: Mapped[str] = mapped_column(String(64))
    producer_id: Mapped[str] = mapped_column(String(80))
    producer_version: Mapped[str] = mapped_column(String(40))
    supersedes_dictionary_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("pronunciation_dictionaries.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "dictionary_id",
            "revision",
            name="uq_pronunciation_dictionary_revision",
        ),
        UniqueConstraint(
            "project_id",
            "dictionary_fingerprint",
            name="uq_pronunciation_dictionary_fingerprint",
        ),
        CheckConstraint("revision >= 1", name="ck_pronunciation_dictionary_revision"),
        CheckConstraint(
            "entry_count >= 0 AND entry_count <= 1000",
            name="ck_pronunciation_dictionary_entry_count",
        ),
        Index(
            "ix_pronunciation_dictionary_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_pronunciation_dictionary_identity_revision",
            "project_id",
            "dictionary_id",
            "revision",
            "id",
        ),
    )


class PronunciationEntryRow(Base):
    """Append-only pronunciation entry revision with explicit scope and authority."""

    __tablename__ = "pronunciation_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    dictionary_record_id: Mapped[str] = mapped_column(
        ForeignKey("pronunciation_dictionaries.id", ondelete="RESTRICT"),
        index=True,
    )
    dictionary_id: Mapped[str] = mapped_column(String(36))
    dictionary_revision: Mapped[int] = mapped_column(Integer)
    entry_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    written_form: Mapped[str] = mapped_column(String(1000))
    normalized_lookup_form: Mapped[str] = mapped_column(String(1000))
    language: Mapped[str] = mapped_column(String(16))
    locale: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_neutral_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    ipa_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider_specific_json: Mapped[str] = mapped_column(Text, default="{}")
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    whole_word: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    verification_state: Mapped[str] = mapped_column(String(24))
    entry_fingerprint: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(1000))
    supersedes_entry_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("pronunciation_entries.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "entry_id",
            "revision",
            name="uq_pronunciation_entry_revision",
        ),
        UniqueConstraint(
            "dictionary_record_id",
            "entry_fingerprint",
            name="uq_pronunciation_entry_dictionary_fingerprint",
        ),
        CheckConstraint(
            "dictionary_revision >= 1 AND revision >= 1",
            name="ck_pronunciation_entry_revisions",
        ),
        CheckConstraint(
            "scope_type IN "
            "('project', 'narrator', 'character_role', 'chapter', 'scene', 'custom')",
            name="ck_pronunciation_entry_scope",
        ),
        CheckConstraint(
            "(scope_type = 'project' AND scope_target_id IS NULL) OR "
            "(scope_type != 'project' AND scope_target_id IS NOT NULL)",
            name="ck_pronunciation_entry_scope_target",
        ),
        CheckConstraint(
            "priority >= -1000 AND priority <= 1000",
            name="ck_pronunciation_entry_priority",
        ),
        CheckConstraint(
            "verification_state IN "
            "('pending', 'approved', 'rejected', 'changes_requested', 'superseded')",
            name="ck_pronunciation_entry_verification",
        ),
        CheckConstraint(
            "length(written_form) >= 1 AND length(written_form) <= 120 "
            "AND length(normalized_lookup_form) >= 1 "
            "AND length(normalized_lookup_form) <= 120",
            name="ck_pronunciation_entry_forms",
        ),
        CheckConstraint(
            "(provider_neutral_value IS NULL OR "
            "(length(provider_neutral_value) >= 1 AND length(provider_neutral_value) <= 256)) "
            "AND (ipa_value IS NULL OR "
            "(length(ipa_value) >= 1 AND length(ipa_value) <= 256))",
            name="ck_pronunciation_entry_values",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 1 AND length(reason) <= 2000",
            name="ck_pronunciation_entry_reason",
        ),
        Index(
            "ix_pronunciation_entry_project_lookup_scope",
            "project_id",
            "normalized_lookup_form",
            "scope_type",
            "scope_target_id",
            "priority",
            "id",
        ),
        Index(
            "ix_pronunciation_entry_dictionary_order",
            "dictionary_record_id",
            "normalized_lookup_form",
            "entry_id",
            "revision",
            "id",
        ),
    )


class AuditionSessionRow(Base):
    """Immutable governed evidence envelope for one role audition session."""

    __tablename__ = "audition_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        index=True,
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"),
        index=True,
    )
    extraction_revision: Mapped[int] = mapped_column(Integer)
    extracted_text_sha256: Mapped[str] = mapped_column(String(64))
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    analysis_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    analysis_snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    analysis_correction_set_fingerprint: Mapped[str] = mapped_column(String(64))
    casting_run_id: Mapped[str] = mapped_column(
        ForeignKey("casting_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    cast_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("approved_cast_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    cast_snapshot_revision: Mapped[int] = mapped_column(Integer)
    cast_snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    phase2_gate_decision_ids_json: Mapped[str] = mapped_column(Text)
    phase3a_gate_decision_ids_json: Mapped[str] = mapped_column(Text)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="RESTRICT"),
        index=True,
    )
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("cast_assignments.id", ondelete="RESTRICT"),
        index=True,
    )
    assignment_revision: Mapped[int] = mapped_column(Integer)
    voice_profile_record_id: Mapped[str] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    voice_profile_id: Mapped[str] = mapped_column(String(80))
    voice_profile_version: Mapped[str] = mapped_column(String(40))
    voice_runtime_binding_id: Mapped[str] = mapped_column(
        ForeignKey("voice_runtime_bindings.id", ondelete="RESTRICT"),
        index=True,
    )
    voice_runtime_binding_fingerprint: Mapped[str] = mapped_column(String(64))
    provider_voice_id: Mapped[str] = mapped_column(String(120))
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    model_id: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(40))
    catalog_revision_id: Mapped[str] = mapped_column(
        ForeignKey("voice_catalog_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    rights_record_id: Mapped[str] = mapped_column(
        ForeignKey("voice_rights_records.id", ondelete="RESTRICT"),
        index=True,
    )
    rights_revision: Mapped[int] = mapped_column(Integer)
    pronunciation_dictionary_record_id: Mapped[str] = mapped_column(
        ForeignKey("pronunciation_dictionaries.id", ondelete="RESTRICT"),
        index=True,
    )
    pronunciation_dictionary_revision: Mapped[int] = mapped_column(Integer)
    pronunciation_dictionary_fingerprint: Mapped[str] = mapped_column(String(64))
    runtime_profile_id: Mapped[str] = mapped_column(
        ForeignKey("speech_runtime_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    runtime_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    model_manifest_id: Mapped[str] = mapped_column(
        ForeignKey("model_package_manifests.id", ondelete="RESTRICT"),
        index=True,
    )
    model_installation_record_id: Mapped[str] = mapped_column(
        ForeignKey("model_installations.id", ondelete="RESTRICT"),
        index=True,
    )
    model_verification_id: Mapped[str] = mapped_column(
        ForeignKey("model_verifications.id", ondelete="RESTRICT"),
        index=True,
    )
    model_package_fingerprint: Mapped[str] = mapped_column(String(64))
    producer_id: Mapped[str] = mapped_column(String(80))
    producer_version: Mapped[str] = mapped_column(String(40))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    supersedes_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
    published_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_audition_session_idempotency",
        ),
        CheckConstraint(
            "revision >= 1 AND source_revision >= 1 AND extraction_revision >= 1 "
            "AND cast_snapshot_revision >= 1 AND assignment_revision >= 1 "
            "AND rights_revision >= 1 AND pronunciation_dictionary_revision >= 1",
            name="ck_audition_session_revisions",
        ),
        CheckConstraint(
            "state IN "
            "('draft', 'queued', 'generating', 'reviewable', 'failed', "
            "'cancelled', 'invalidated')",
            name="ck_audition_session_state",
        ),
        Index(
            "ix_audition_session_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audition_session_project_role_created",
            "project_id",
            "role_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audition_session_assignment_evidence",
            "project_id",
            "assignment_id",
            "assignment_revision",
            "created_at",
            "id",
        ),
    )


class AuditionScriptRow(Base):
    """Versioned bounded script metadata; manuscript text remains in private storage."""

    __tablename__ = "audition_scripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    script_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    script_type: Mapped[str] = mapped_column(String(32))
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="RESTRICT"),
        index=True,
    )
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    extraction_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_extractions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_analysis_entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_entities.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_analysis_entity_collection: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    source_analysis_entity_effective_revision: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    source_analysis_entity_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    exact_text_sha256: Mapped[str] = mapped_column(String(64))
    text_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    synthetic_text_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    text_codepoint_count: Mapped[int] = mapped_column(Integer)
    script_fingerprint: Mapped[str] = mapped_column(String(64))
    supersedes_script_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("audition_scripts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "script_id",
            "revision",
            name="uq_audition_script_revision",
        ),
        UniqueConstraint(
            "session_id",
            "script_fingerprint",
            name="uq_audition_script_session_fingerprint",
        ),
        CheckConstraint("revision >= 1", name="ck_audition_script_revision"),
        CheckConstraint(
            "script_type IN "
            "('standardized_synthetic', 'approved_manuscript_excerpt', "
            "'role_dialogue_excerpt', "
            "'narrator_excerpt', 'pronunciation_test', 'synthetic_fallback')",
            name="ck_audition_script_type",
        ),
        CheckConstraint(
            "(source_document_id IS NULL AND extraction_id IS NULL "
            "AND source_start_offset IS NULL AND source_end_offset IS NULL) OR "
            "(source_document_id IS NOT NULL AND extraction_id IS NOT NULL "
            "AND source_start_offset >= 0 AND source_end_offset > source_start_offset)",
            name="ck_audition_script_source_span",
        ),
        CheckConstraint(
            "((script_type IN ('role_dialogue_excerpt', 'narrator_excerpt')) "
            "AND source_analysis_entity_id IS NOT NULL "
            "AND source_analysis_entity_collection IS NOT NULL "
            "AND source_analysis_entity_effective_revision >= 1 "
            "AND length(source_analysis_entity_fingerprint) = 64) OR "
            "((script_type NOT IN ('role_dialogue_excerpt', 'narrator_excerpt')) "
            "AND source_analysis_entity_id IS NULL "
            "AND source_analysis_entity_collection IS NULL "
            "AND source_analysis_entity_effective_revision IS NULL "
            "AND source_analysis_entity_fingerprint IS NULL)",
            name="ck_audition_script_semantic_source",
        ),
        CheckConstraint(
            "text_codepoint_count >= 1 AND text_codepoint_count <= 4000",
            name="ck_audition_script_text_count",
        ),
        CheckConstraint(
            "text_storage_key IS NOT NULL OR synthetic_text_id IS NOT NULL",
            name="ck_audition_script_storage_source",
        ),
        Index(
            "ix_audition_script_session_created",
            "session_id",
            "created_at",
            "id",
        ),
    )


class TextNormalizationPlanRow(Base):
    """Inspectable normalization and compiled-pronunciation plan."""

    __tablename__ = "text_normalization_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    script_id: Mapped[str] = mapped_column(
        ForeignKey("audition_scripts.id", ondelete="RESTRICT"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    normalization_profile_id: Mapped[str] = mapped_column(String(80))
    normalization_profile_version: Mapped[str] = mapped_column(String(40))
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    original_text_sha256: Mapped[str] = mapped_column(String(64))
    normalized_text_sha256: Mapped[str] = mapped_column(String(64))
    transformations_json: Mapped[str] = mapped_column(Text)
    pronunciation_dictionary_record_id: Mapped[str] = mapped_column(
        ForeignKey("pronunciation_dictionaries.id", ondelete="RESTRICT"),
        index=True,
    )
    pronunciation_dictionary_revision: Mapped[int] = mapped_column(Integer)
    pronunciation_dictionary_fingerprint: Mapped[str] = mapped_column(String(64))
    pronunciation_entry_ids_json: Mapped[str] = mapped_column(Text)
    compiled_pronunciation_json: Mapped[str] = mapped_column(Text)
    pronunciation_plan_fingerprint: Mapped[str] = mapped_column(String(64))
    unsupported_characters_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    human_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    plan_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "script_id",
            "revision",
            name="uq_text_normalization_plan_revision",
        ),
        UniqueConstraint(
            "session_id",
            "plan_fingerprint",
            name="uq_text_normalization_plan_fingerprint",
        ),
        CheckConstraint("revision >= 1", name="ck_text_normalization_plan_revision"),
        CheckConstraint(
            "pronunciation_dictionary_revision >= 1",
            name="ck_text_normalization_dictionary_revision",
        ),
        Index(
            "ix_text_normalization_session_script_revision",
            "project_id",
            "session_id",
            "script_id",
            "revision",
            "id",
        ),
    )


class SpeechProviderRequestRow(Base):
    """Content-free durable execution record for a cache lookup or synthesis call."""

    __tablename__ = "speech_provider_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    script_id: Mapped[str] = mapped_column(
        ForeignKey("audition_scripts.id", ondelete="RESTRICT"),
        index=True,
    )
    normalization_plan_id: Mapped[str] = mapped_column(
        ForeignKey("text_normalization_plans.id", ondelete="RESTRICT"),
        index=True,
    )
    runtime_profile_id: Mapped[str] = mapped_column(
        ForeignKey("speech_runtime_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    runtime_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("speech_runtime_instances.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    model_installation_record_id: Mapped[str] = mapped_column(
        ForeignKey("model_installations.id", ondelete="RESTRICT"),
        index=True,
    )
    model_verification_id: Mapped[str] = mapped_column(
        ForeignKey("model_verifications.id", ondelete="RESTRICT"),
        index=True,
    )
    voice_profile_record_id: Mapped[str] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("cast_assignments.id", ondelete="RESTRICT"),
        index=True,
    )
    assignment_revision: Mapped[int] = mapped_column(Integer)
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    provider_operation_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model_id: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(40))
    model_package_fingerprint: Mapped[str] = mapped_column(String(64))
    runtime_profile_fingerprint: Mapped[str] = mapped_column(String(64))
    voice_profile_id: Mapped[str] = mapped_column(String(80))
    voice_profile_version: Mapped[str] = mapped_column(String(40))
    voice_runtime_binding_id: Mapped[str] = mapped_column(
        ForeignKey("voice_runtime_bindings.id", ondelete="RESTRICT"),
        index=True,
    )
    voice_runtime_binding_fingerprint: Mapped[str] = mapped_column(String(64))
    provider_voice_id: Mapped[str] = mapped_column(String(120))
    normalized_text_sha256: Mapped[str] = mapped_column(String(64))
    pronunciation_plan_fingerprint: Mapped[str] = mapped_column(String(64))
    provider_control_fingerprint: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    outcome: Mapped[str] = mapped_column(String(24))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    output_artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_properties_json: Mapped[str] = mapped_column(Text, default="{}")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "attempt",
            "request_fingerprint",
            name="uq_speech_provider_request_attempt",
        ),
        UniqueConstraint(
            "job_id",
            "idempotency_key",
            name="uq_speech_provider_request_idempotency",
        ),
        CheckConstraint(
            "attempt >= 1 AND assignment_revision >= 1",
            name="ck_speech_provider_request_revisions",
        ),
        CheckConstraint(
            "outcome IN "
            "('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_speech_provider_request_outcome",
        ),
        Index(
            "ix_speech_provider_request_project_started",
            "project_id",
            "started_at",
            "id",
        ),
        Index(
            "ix_speech_provider_request_cache_key",
            "project_id",
            "cache_key",
            "started_at",
            "id",
        ),
    )


class AudioArtifactRow(Base):
    """Opaque managed PCM WAV artifact metadata; paths never cross the API boundary."""

    __tablename__ = "audio_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    provider_request_id: Mapped[str] = mapped_column(
        ForeignKey("speech_provider_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(512))
    content_sha256: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(Integer)
    container_format: Mapped[str] = mapped_column(String(16))
    codec: Mapped[str] = mapped_column(String(24))
    sample_format: Mapped[str] = mapped_column(String(24))
    sample_rate_hz: Mapped[int] = mapped_column(Integer)
    channel_count: Mapped[int] = mapped_column(Integer)
    sample_width_bytes: Mapped[int] = mapped_column(Integer)
    frame_count: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    artifact_fingerprint: Mapped[str] = mapped_column(String(64))
    availability: Mapped[str] = mapped_column(String(24))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
    purged_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "storage_key",
            name="uq_audio_artifact_storage_key",
        ),
        UniqueConstraint(
            "project_id",
            "artifact_fingerprint",
            name="uq_audio_artifact_fingerprint",
        ),
        CheckConstraint(
            "byte_count >= 1 AND byte_count <= 25165824",
            name="ck_audio_artifact_byte_count",
        ),
        CheckConstraint(
            "container_format = 'wav' AND codec = 'pcm_s16le'",
            name="ck_audio_artifact_format",
        ),
        CheckConstraint(
            "sample_rate_hz = 24000 AND channel_count = 1 "
            "AND sample_width_bytes = 2",
            name="ck_audio_artifact_sample_properties",
        ),
        CheckConstraint(
            "frame_count >= 1 AND duration_ms >= 1 AND duration_ms <= 30000",
            name="ck_audio_artifact_duration",
        ),
        CheckConstraint(
            "availability IN ('present', 'purged', 'corrupt', 'quarantined')",
            name="ck_audio_artifact_availability",
        ),
        Index(
            "ix_audio_artifact_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audio_artifact_project_content",
            "project_id",
            "content_sha256",
            "id",
        ),
    )


class AuditionCacheRecordRow(Base):
    """Project-private cache index with verified artifact identity and tombstone state."""

    __tablename__ = "audition_cache_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    cache_key: Mapped[str] = mapped_column(String(64))
    voice_runtime_binding_id: Mapped[str] = mapped_column(
        ForeignKey("voice_runtime_bindings.id", ondelete="RESTRICT"),
        index=True,
    )
    voice_runtime_binding_fingerprint: Mapped[str] = mapped_column(String(64))
    provider_voice_id: Mapped[str] = mapped_column(String(120))
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("audio_artifacts.id", ondelete="RESTRICT"),
        index=True,
    )
    provider_request_id: Mapped[str] = mapped_column(
        ForeignKey("speech_provider_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    script_id: Mapped[str] = mapped_column(
        ForeignKey("audition_scripts.id", ondelete="RESTRICT"),
        index=True,
    )
    expected_artifact_sha256: Mapped[str] = mapped_column(String(64))
    expected_byte_count: Mapped[int] = mapped_column(Integer)
    expected_audio_properties_json: Mapped[str] = mapped_column(Text)
    verification_fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32))
    last_verified_at: Mapped[str] = mapped_column(String(32))
    last_hit_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    purged_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "cache_key",
            name="uq_audition_cache_project_key",
        ),
        CheckConstraint(
            "expected_byte_count >= 1 AND hit_count >= 0",
            name="ck_audition_cache_counts",
        ),
        CheckConstraint(
            "state IN ('verified', 'corrupt', 'missing', 'cleared')",
            name="ck_audition_cache_state",
        ),
        Index(
            "ix_audition_cache_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audition_cache_project_state_verified",
            "project_id",
            "state",
            "last_verified_at",
            "id",
        ),
    )


class AuditionClipRow(Base):
    """Immutable published clip binding a request to one verified artifact."""

    __tablename__ = "audition_clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    script_id: Mapped[str] = mapped_column(
        ForeignKey("audition_scripts.id", ondelete="RESTRICT"),
        index=True,
    )
    provider_request_id: Mapped[str] = mapped_column(
        ForeignKey("speech_provider_requests.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("audio_artifacts.id", ondelete="RESTRICT"),
        index=True,
    )
    cache_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("audition_cache_records.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="RESTRICT"),
        index=True,
    )
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("cast_assignments.id", ondelete="RESTRICT"),
        index=True,
    )
    assignment_revision: Mapped[int] = mapped_column(Integer)
    voice_profile_record_id: Mapped[str] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str] = mapped_column(String(64))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    clip_fingerprint: Mapped[str] = mapped_column(String(64))
    producer_id: Mapped[str] = mapped_column(String(80))
    producer_version: Mapped[str] = mapped_column(String(40))
    supersedes_clip_id: Mapped[str | None] = mapped_column(
        ForeignKey("audition_clips.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "script_id",
            "revision",
            name="uq_audition_clip_revision",
        ),
        UniqueConstraint(
            "project_id",
            "clip_fingerprint",
            name="uq_audition_clip_fingerprint",
        ),
        CheckConstraint(
            "revision >= 1 AND assignment_revision >= 1",
            name="ck_audition_clip_revisions",
        ),
        Index(
            "ix_audition_clip_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audition_clip_project_role_created",
            "project_id",
            "role_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audition_clip_session_script_revision",
            "session_id",
            "script_id",
            "revision",
            "id",
        ),
    )


class AudioQualityRecordRow(Base):
    """Immutable machine integrity result; never a subjective quality claim."""

    __tablename__ = "audio_quality_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    clip_id: Mapped[str] = mapped_column(
        ForeignKey("audition_clips.id", ondelete="RESTRICT"),
        index=True,
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("audio_artifacts.id", ondelete="RESTRICT"),
        index=True,
    )
    provider_request_id: Mapped[str] = mapped_column(
        ForeignKey("speech_provider_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    policy_id: Mapped[str] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(40))
    policy_fingerprint: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(24))
    peak_millidbfs: Mapped[int] = mapped_column(Integer)
    rms_millidbfs: Mapped[int] = mapped_column(Integer)
    silence_ratio_ppm: Mapped[int] = mapped_column(Integer)
    clipped_sample_count: Mapped[int] = mapped_column(Integer)
    warning_count: Mapped[int] = mapped_column(Integer)
    blocking_finding_count: Mapped[int] = mapped_column(Integer)
    findings_json: Mapped[str] = mapped_column(Text)
    quality_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "revision",
            name="uq_audio_quality_artifact_revision",
        ),
        UniqueConstraint(
            "project_id",
            "quality_fingerprint",
            name="uq_audio_quality_fingerprint",
        ),
        CheckConstraint("revision >= 1", name="ck_audio_quality_revision"),
        CheckConstraint(
            "outcome IN ('passed', 'warning', 'blocked')",
            name="ck_audio_quality_outcome",
        ),
        CheckConstraint(
            "peak_millidbfs >= -200000 AND peak_millidbfs <= 0 "
            "AND rms_millidbfs >= -200000 AND rms_millidbfs <= 0 "
            "AND silence_ratio_ppm >= 0 AND silence_ratio_ppm <= 1000000",
            name="ck_audio_quality_measurements",
        ),
        CheckConstraint(
            "clipped_sample_count >= 0 AND warning_count >= 0 "
            "AND blocking_finding_count >= 0",
            name="ck_audio_quality_counts",
        ),
        Index(
            "ix_audio_quality_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audio_quality_clip_revision",
            "clip_id",
            "revision",
            "id",
        ),
    )


class AuditionReviewRecordRow(Base):
    """Immutable eligibility evaluation for one scoped audition gate revision."""

    __tablename__ = "audition_review_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    gate_id: Mapped[str] = mapped_column(String(48))
    scope_key: Mapped[str] = mapped_column(String(160))
    subject_type: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    clip_id: Mapped[str | None] = mapped_column(
        ForeignKey("audition_clips.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    role_id: Mapped[str | None] = mapped_column(
        ForeignKey("production_roles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    pronunciation_dictionary_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("pronunciation_dictionaries.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    eligible: Mapped[bool] = mapped_column(Boolean)
    evidence_json: Mapped[str] = mapped_column(Text)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    required_decision_ids_json: Mapped[str] = mapped_column(Text)
    blockers_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "gate_id",
            "scope_key",
            "revision",
            name="uq_audition_review_scope_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_audition_review_revision"),
        CheckConstraint(
            "gate_id IN "
            "('per_role_audition_review', 'narrator_audition_review', "
            "'character_audition_review', 'pronunciation_review')",
            name="ck_audition_review_gate",
        ),
        CheckConstraint(
            "subject_type IN "
            "('role', 'narrator_scope', 'character_scope', 'pronunciation_dictionary')",
            name="ck_audition_review_subject",
        ),
        Index(
            "ix_audition_review_project_gate_scope_revision",
            "project_id",
            "gate_id",
            "scope_key",
            "revision",
            "id",
        ),
        Index(
            "ix_audition_review_project_created",
            "project_id",
            "created_at",
            "id",
        ),
    )


class AuditionReviewDecisionRow(Base):
    """Append-only human decision or machine invalidation for an audition review."""

    __tablename__ = "audition_review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    review_record_id: Mapped[str] = mapped_column(
        ForeignKey("audition_review_records.id", ondelete="RESTRICT"),
        index=True,
    )
    gate_id: Mapped[str] = mapped_column(String(48))
    scope_key: Mapped[str] = mapped_column(String(160))
    revision: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(24))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    actor_classification: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(80))
    warning_acknowledgements_json: Mapped[str] = mapped_column(Text, default="[]")
    rationale: Mapped[str] = mapped_column(String(4000))
    supersedes_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("audition_review_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provenance_json: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "gate_id",
            "scope_key",
            "revision",
            name="uq_audition_review_decision_revision",
        ),
        UniqueConstraint(
            "project_id",
            "gate_id",
            "scope_key",
            "idempotency_key",
            name="uq_audition_review_decision_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_audition_review_decision_revision"),
        CheckConstraint(
            "gate_id IN "
            "('per_role_audition_review', 'narrator_audition_review', "
            "'character_audition_review', 'pronunciation_review')",
            name="ck_audition_review_decision_gate",
        ),
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'changes_requested', 'invalidated')",
            name="ck_audition_review_decision_state",
        ),
        CheckConstraint(
            "(decision = 'invalidated' AND actor_classification = 'system') OR "
            "(decision != 'invalidated' AND actor_classification = 'human')",
            name="ck_audition_review_decision_authority",
        ),
        CheckConstraint(
            "length(trim(rationale)) >= 1 AND length(rationale) <= 4000",
            name="ck_audition_review_decision_rationale",
        ),
        Index(
            "ix_audition_review_decision_project_gate_scope",
            "project_id",
            "gate_id",
            "scope_key",
            "revision",
            "id",
        ),
    )


class VoiceReadinessSnapshotRow(Base):
    """Immutable aggregate of all evidence required for later voice work."""

    __tablename__ = "voice_readiness_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    cast_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("approved_cast_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    model_verification_id: Mapped[str] = mapped_column(
        ForeignKey("model_verifications.id", ondelete="RESTRICT"),
        index=True,
    )
    pronunciation_dictionary_record_id: Mapped[str] = mapped_column(
        ForeignKey("pronunciation_dictionaries.id", ondelete="RESTRICT"),
        index=True,
    )
    narrator_review_decision_id: Mapped[str] = mapped_column(
        ForeignKey("audition_review_decisions.id", ondelete="RESTRICT"),
        index=True,
    )
    character_review_decision_id: Mapped[str] = mapped_column(
        ForeignKey("audition_review_decisions.id", ondelete="RESTRICT"),
        index=True,
    )
    pronunciation_review_decision_id: Mapped[str] = mapped_column(
        ForeignKey("audition_review_decisions.id", ondelete="RESTRICT"),
        index=True,
    )
    phase3a_gate_decision_ids_json: Mapped[str] = mapped_column(Text)
    required_role_count: Mapped[int] = mapped_column(Integer)
    approved_role_count: Mapped[int] = mapped_column(Integer)
    blocking_finding_count: Mapped[int] = mapped_column(Integer)
    evidence_json: Mapped[str] = mapped_column(Text)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    blockers_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "revision",
            name="uq_voice_readiness_snapshot_revision",
        ),
        UniqueConstraint(
            "project_id",
            "snapshot_fingerprint",
            name="uq_voice_readiness_snapshot_fingerprint",
        ),
        CheckConstraint("revision >= 1", name="ck_voice_readiness_snapshot_revision"),
        CheckConstraint(
            "required_role_count >= 0 AND approved_role_count >= 0 "
            "AND approved_role_count <= required_role_count "
            "AND blocking_finding_count >= 0",
            name="ck_voice_readiness_snapshot_counts",
        ),
        Index(
            "ix_voice_readiness_snapshot_project_created",
            "project_id",
            "created_at",
            "id",
        ),
    )


class VoiceReadinessReviewRow(Base):
    """Immutable eligibility calculation for Voice Readiness Review."""

    __tablename__ = "voice_readiness_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("voice_readiness_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    gate_id: Mapped[str] = mapped_column(String(48))
    revision: Mapped[int] = mapped_column(Integer)
    eligible: Mapped[bool] = mapped_column(Boolean)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    required_decision_ids_json: Mapped[str] = mapped_column(Text)
    blockers_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "revision",
            name="uq_voice_readiness_review_revision",
        ),
        CheckConstraint("revision >= 1", name="ck_voice_readiness_review_revision"),
        CheckConstraint(
            "gate_id = 'voice_readiness_review'",
            name="ck_voice_readiness_review_gate",
        ),
        Index(
            "ix_voice_readiness_review_project_revision",
            "project_id",
            "revision",
            "id",
        ),
    )


class VoiceReadinessDecisionRow(Base):
    """Append-only human decision or machine invalidation for voice readiness."""

    __tablename__ = "voice_readiness_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("voice_readiness_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    review_id: Mapped[str] = mapped_column(
        ForeignKey("voice_readiness_reviews.id", ondelete="RESTRICT"),
        index=True,
    )
    gate_id: Mapped[str] = mapped_column(String(48))
    revision: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(24))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    actor_classification: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(80))
    warning_acknowledgements_json: Mapped[str] = mapped_column(Text, default="[]")
    rationale: Mapped[str] = mapped_column(String(4000))
    supersedes_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_readiness_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provenance_json: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "revision",
            name="uq_voice_readiness_decision_revision",
        ),
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_voice_readiness_decision_idempotency",
        ),
        CheckConstraint("revision >= 1", name="ck_voice_readiness_decision_revision"),
        CheckConstraint(
            "gate_id = 'voice_readiness_review'",
            name="ck_voice_readiness_decision_gate",
        ),
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'changes_requested', 'invalidated')",
            name="ck_voice_readiness_decision_state",
        ),
        CheckConstraint(
            "(decision = 'invalidated' AND actor_classification = 'system') OR "
            "(decision != 'invalidated' AND actor_classification = 'human')",
            name="ck_voice_readiness_decision_authority",
        ),
        CheckConstraint(
            "length(trim(rationale)) >= 1 AND length(rationale) <= 4000",
            name="ck_voice_readiness_decision_rationale",
        ),
        Index(
            "ix_voice_readiness_decision_project_revision",
            "project_id",
            "revision",
            "id",
        ),
    )


class AuditionEvidenceInvalidationRow(Base):
    """Append-only targeted invalidation of one clip's dependency evidence."""

    __tablename__ = "audition_evidence_invalidations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    clip_id: Mapped[str] = mapped_column(
        ForeignKey("audition_clips.id", ondelete="RESTRICT"),
        index=True,
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("audition_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("production_roles.id", ondelete="RESTRICT"),
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(32))
    source_record_id: Mapped[str] = mapped_column(String(120))
    previous_fingerprint: Mapped[str] = mapped_column(String(64))
    current_fingerprint: Mapped[str] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(80))
    affected_review_ids_json: Mapped[str] = mapped_column(Text)
    invalidation_fingerprint: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "clip_id",
            "invalidation_fingerprint",
            name="uq_audition_evidence_invalidation",
        ),
        CheckConstraint(
            "source_kind IN "
            "('assignment', 'rights', 'model_package', 'provider', 'runtime', "
            "'pronunciation_entry', 'cast_snapshot', 'audio_integrity', "
            "'review_clip_binding')",
            name="ck_audition_evidence_invalidation_source",
        ),
        CheckConstraint(
            "length(trim(reason_code)) >= 1 AND length(reason_code) <= 80",
            name="ck_audition_evidence_invalidation_reason",
        ),
        Index(
            "ix_audition_evidence_invalidation_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audition_evidence_invalidation_source",
            "project_id",
            "source_kind",
            "source_record_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audition_evidence_invalidation_role",
            "project_id",
            "role_id",
            "created_at",
            "id",
        ),
    )
