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
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
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
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
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
            "state IN ("
            "'pending', 'approved', 'rejected', 'changes_requested', 'invalidated'"
            ")",
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
