from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from .errors import ServiceError
from .models import Base
from .util import (
    SERVICE_VERSION,
    ensure_private_directory,
    new_id,
    request_fingerprint,
    utc_now,
)

_DATABASE_SCHEMA_VERSION = 4
_OLDEST_MIGRATABLE_SCHEMA_VERSION = 1
_PHASE1_SCHEMA_VERSION = 2
_PHASE2_SCHEMA_VERSION = 3
_SCHEMA_LEDGER_TABLE = "schema_migrations"
_STORAGE_LOCK_FILENAME = ".cinematic-story-studio.lock"

# Schema version 2 is an explicit compatibility contract. Keep this allow-list
# independent from runtime ORM reflection so model changes cannot silently redefine an
# already-issued database version.
_V2_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "projects": frozenset(
        {
            "id",
            "name",
            "status",
            "revision",
            "story_id",
            "created_at",
            "updated_at",
        }
    ),
    "source_documents": frozenset(
        {
            "id",
            "project_id",
            "display_name",
            "media_type",
            "declared_format",
            "content_sha256",
            "text_sha256",
            "byte_length",
            "encoding",
            "newline_style",
            "storage_key",
            "imported_at",
            "revision",
            "source_revision",
            "supersedes_document_id",
            "extraction_status",
            "provenance_json",
            "warnings_json",
        }
    ),
    "document_extractions": frozenset(
        {
            "id",
            "project_id",
            "source_document_id",
            "revision",
            "supersedes_extraction_id",
            "status",
            "format",
            "extractor_name",
            "extractor_version",
            "input_sha256",
            "text_sha256",
            "character_count",
            "page_count",
            "encoding",
            "newline_style",
            "exact_text",
            "text_storage_key",
            "manifest_json",
            "sections_json",
            "source_mappings_json",
            "evidence_fingerprint",
            "warnings_json",
            "created_at",
            "updated_at",
        }
    ),
    "imported_stories": frozenset(
        {
            "id",
            "project_id",
            "source_document_id",
            "extraction_id",
            "extraction_revision",
            "title",
            "exact_text",
            "content_fingerprint",
            "imported_at",
            "revision",
            "provenance_json",
            "warnings_json",
        }
    ),
    "chapters": frozenset(
        {
            "id",
            "project_id",
            "story_id",
            "ordinal",
            "title",
            "start_offset",
            "end_offset",
            "revision",
            "provenance_json",
        }
    ),
    "scenes": frozenset(
        {
            "id",
            "project_id",
            "chapter_id",
            "ordinal",
            "heading",
            "location",
            "mood",
            "start_offset",
            "end_offset",
            "revision",
            "confidence_json",
            "warnings_json",
            "provenance_json",
        }
    ),
    "story_beats": frozenset(
        {
            "id",
            "project_id",
            "scene_id",
            "ordinal",
            "kind",
            "start_offset",
            "end_offset",
            "summary",
            "dialogue_line_id",
            "revision",
            "provenance_json",
        }
    ),
    "characters": frozenset(
        {
            "id",
            "project_id",
            "story_id",
            "display_name",
            "normalized_name",
            "aliases_json",
            "evidence_json",
            "revision",
            "confidence_json",
            "warnings_json",
            "provenance_json",
        }
    ),
    "dialogue_lines": frozenset(
        {
            "id",
            "project_id",
            "scene_id",
            "beat_id",
            "ordinal",
            "start_offset",
            "end_offset",
            "verbatim_text",
            "text_sha256",
            "revision",
            "provenance_json",
        }
    ),
    "dialogue_attributions": frozenset(
        {
            "id",
            "project_id",
            "line_id",
            "proposed_speaker_id",
            "effective_speaker_id",
            "effective_authority",
            "evidence_json",
            "revision",
            "confidence_json",
            "warnings_json",
            "provenance_json",
            "updated_at",
        }
    ),
    "human_corrections": frozenset(
        {
            "id",
            "project_id",
            "line_id",
            "attribution_id",
            "previous_value_fingerprint",
            "previous_character_id",
            "corrected_character_id",
            "reason",
            "actor_id",
            "line_revision",
            "recorded_at",
            "supersedes_correction_id",
        }
    ),
    "import_reviews": frozenset(
        {
            "id",
            "review_id",
            "project_id",
            "source_document_id",
            "extraction_id",
            "candidate_story_id",
            "revision",
            "state",
            "evidence_fingerprint",
            "preview_text",
            "preview_truncated",
            "warnings_json",
            "warning_acknowledgements_json",
            "provenance_json",
            "decision_id",
            "decision_rationale",
            "reason",
            "actor_id",
            "idempotency_key",
            "decided_at",
            "supersedes_record_id",
            "created_at",
        }
    ),
    "idempotency_records": frozenset(
        {
            "scope",
            "key",
            "request_hash",
            "resource_id",
            "created_at",
        }
    ),
    "jobs": frozenset(
        {
            "id",
            "project_id",
            "type",
            "state",
            "input_revision",
            "input_fingerprint",
            "target_type",
            "target_id",
            "payload_json",
            "current_attempt",
            "stage",
            "progress",
            "checkpoint_available",
            "cancellation_requested",
            "resume_requested",
            "warnings_json",
            "error_code",
            "error_message",
            "error_retryable",
            "created_at",
            "updated_at",
            "terminal_at",
        }
    ),
    "job_attempts": frozenset(
        {
            "job_id",
            "number",
            "worker_instance_id",
            "started_at",
            "ended_at",
            "outcome",
            "error_code",
            "error_message",
            "producer_version",
        }
    ),
    "job_events": frozenset(
        {
            "job_id",
            "sequence",
            "attempt",
            "type",
            "state",
            "stage",
            "progress",
            "completed_units",
            "total_units",
            "warning_json",
            "error_code",
            "error_message",
            "error_retryable",
            "created_at",
        }
    ),
    "job_checkpoints": frozenset(
        {
            "job_id",
            "attempt",
            "sequence",
            "checkpoint_type",
            "schema_version",
            "input_revision",
            "input_fingerprint",
            "producer_version",
            "payload_json",
            "payload_sha256",
            "created_at",
        }
    ),
    "parser_executions": frozenset(
        {
            "id",
            "project_id",
            "source_document_id",
            "extraction_id",
            "job_id",
            "attempt",
            "parser_name",
            "parser_version",
            "outcome",
            "input_sha256",
            "limits_fingerprint",
            "output_text_sha256",
            "manifest_json",
            "sections_json",
            "source_mappings_json",
            "warnings_json",
            "error_code",
            "error_message",
            "error_retryable",
            "started_at",
            "finished_at",
        }
    ),
    _SCHEMA_LEDGER_TABLE: frozenset(
        {
            "version",
            "applied_at",
            "service_version",
        }
    ),
}

_V2_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "projects": ("id",),
    "source_documents": ("id",),
    "document_extractions": ("id",),
    "imported_stories": ("id",),
    "chapters": ("id",),
    "scenes": ("id",),
    "story_beats": ("id",),
    "characters": ("id",),
    "dialogue_lines": ("id",),
    "dialogue_attributions": ("id",),
    "human_corrections": ("id",),
    "import_reviews": ("id",),
    "idempotency_records": ("scope", "key"),
    "jobs": ("id",),
    "job_attempts": ("job_id", "number"),
    "job_events": ("job_id", "sequence"),
    "job_checkpoints": ("job_id", "attempt"),
    "parser_executions": ("id",),
    _SCHEMA_LEDGER_TABLE: ("version",),
}

_ForeignKeySignature = tuple[tuple[str, ...], str, tuple[str, ...], str]
_V2_CRITICAL_FOREIGN_KEYS: dict[str, frozenset[_ForeignKeySignature]] = {
    "source_documents": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("supersedes_document_id",), "source_documents", ("id",), "RESTRICT"),
        }
    ),
    "document_extractions": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("source_document_id",), "source_documents", ("id",), "RESTRICT"),
            (
                ("supersedes_extraction_id",),
                "document_extractions",
                ("id",),
                "RESTRICT",
            ),
        }
    ),
    "imported_stories": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("source_document_id",), "source_documents", ("id",), "RESTRICT"),
            (("extraction_id",), "document_extractions", ("id",), "RESTRICT"),
        }
    ),
    "import_reviews": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("source_document_id",), "source_documents", ("id",), "RESTRICT"),
            (("extraction_id",), "document_extractions", ("id",), "RESTRICT"),
            (("supersedes_record_id",), "import_reviews", ("id",), "RESTRICT"),
        }
    ),
    "parser_executions": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("source_document_id",), "source_documents", ("id",), "RESTRICT"),
            (("extraction_id",), "document_extractions", ("id",), "RESTRICT"),
            (("job_id",), "jobs", ("id",), "RESTRICT"),
        }
    ),
    "jobs": frozenset({(("project_id",), "projects", ("id",), "CASCADE")}),
    "job_attempts": frozenset({(("job_id",), "jobs", ("id",), "CASCADE")}),
    "job_events": frozenset({(("job_id",), "jobs", ("id",), "CASCADE")}),
    "job_checkpoints": frozenset({(("job_id",), "jobs", ("id",), "CASCADE")}),
}

_V2_CRITICAL_UNIQUE_COLUMNS: dict[str, frozenset[tuple[str, ...]]] = {
    "source_documents": frozenset({("project_id", "source_revision")}),
    "document_extractions": frozenset({("source_document_id", "revision")}),
    "imported_stories": frozenset({("extraction_id",)}),
    "import_reviews": frozenset(
        {
            ("review_id", "revision"),
            ("review_id", "idempotency_key"),
        }
    ),
    "parser_executions": frozenset({("job_id", "attempt")}),
    "jobs": frozenset(),
    "job_attempts": frozenset(),
    "job_events": frozenset(),
    "job_checkpoints": frozenset(),
}

_CheckSignature = tuple[str, str]
_V2_CRITICAL_CHECKS: dict[str, frozenset[_CheckSignature]] = {
    "source_documents": frozenset(
        {
            ("ck_source_byte_length", "byte_length >= 0"),
            ("ck_source_revision", "revision >= 1"),
            ("ck_source_logical_revision", "source_revision >= 1"),
            (
                "ck_source_extraction_status",
                "extraction_status in ('pending', 'running', 'complete', 'partial', 'failed')",
            ),
        }
    ),
    "document_extractions": frozenset(
        {
            ("ck_extraction_revision", "revision >= 1"),
            (
                "ck_extraction_status",
                "status in ('pending', 'running', 'complete', 'partial', 'failed')",
            ),
            (
                "ck_extraction_character_count",
                "character_count is null or character_count >= 0",
            ),
            ("ck_extraction_page_count", "page_count is null or page_count >= 0"),
        }
    ),
    "imported_stories": frozenset(
        {
            ("ck_story_revision", "revision >= 1"),
            ("ck_story_extraction_revision", "extraction_revision >= 1"),
        }
    ),
    "import_reviews": frozenset(
        {
            ("ck_import_review_revision", "revision >= 1"),
            (
                "ck_import_review_state",
                "state in ('pending', 'approved', 'changes_requested', 'rejected', 'invalidated')",
            ),
        }
    ),
    "parser_executions": frozenset(
        {
            ("ck_parser_attempt", "attempt >= 1"),
            (
                "ck_parser_outcome",
                "outcome in ('succeeded', 'partial', 'failed', 'cancelled', 'interrupted')",
            ),
        }
    ),
    "jobs": frozenset(
        {
            ("ck_job_input_revision", "input_revision >= 1"),
            ("ck_job_attempt", "current_attempt >= 1"),
            ("ck_job_progress", "progress >= 0 and progress <= 1000000"),
        }
    ),
    "job_attempts": frozenset({("ck_attempt_number", "number >= 1")}),
    "job_events": frozenset(
        {
            ("ck_event_sequence", "sequence >= 1"),
            ("ck_event_attempt", "attempt >= 1"),
            (
                "ck_event_progress",
                "progress is null or (progress >= 0 and progress <= 1000000)",
            ),
        }
    ),
    "job_checkpoints": frozenset(
        {
            ("ck_checkpoint_attempt", "attempt >= 1"),
            ("ck_checkpoint_sequence", "sequence >= 1"),
            ("ck_checkpoint_schema", "schema_version >= 1"),
        }
    ),
}

# These object and fingerprint constants are frozen schema-version contracts. They
# deliberately do not derive from Base.metadata: changing the ORM without issuing a
# migration must not silently make an already-issued database version compatible.
_V1_TABLES = frozenset(
    {
        "chapters",
        "characters",
        "dialogue_attributions",
        "dialogue_lines",
        "human_corrections",
        "idempotency_records",
        "imported_stories",
        "job_attempts",
        "job_checkpoints",
        "job_events",
        "jobs",
        "projects",
        "scenes",
        _SCHEMA_LEDGER_TABLE,
        "source_documents",
        "story_beats",
    }
)
_V1_NAMED_INDEXES: dict[str, frozenset[str]] = {
    "chapters": frozenset(
        {
            "ix_chapter_project_story_order",
            "ix_chapters_project_id",
            "ix_chapters_story_id",
        }
    ),
    "characters": frozenset(
        {
            "ix_character_project_story_name",
            "ix_characters_project_id",
            "ix_characters_story_id",
        }
    ),
    "dialogue_attributions": frozenset(
        {"ix_attribution_project_line", "ix_dialogue_attributions_project_id"}
    ),
    "dialogue_lines": frozenset(
        {
            "ix_dialogue_lines_beat_id",
            "ix_dialogue_lines_project_id",
            "ix_dialogue_lines_scene_id",
            "ix_line_project_scene_order",
        }
    ),
    "human_corrections": frozenset(
        {
            "ix_correction_project_line_time",
            "ix_human_corrections_attribution_id",
            "ix_human_corrections_line_id",
            "ix_human_corrections_project_id",
        }
    ),
    "imported_stories": frozenset({"ix_imported_stories_project_id", "ix_story_project_imported"}),
    "job_events": frozenset({"ix_event_job_attempt_sequence"}),
    "jobs": frozenset({"ix_job_project_created", "ix_job_queue", "ix_jobs_project_id"}),
    "scenes": frozenset(
        {"ix_scene_project_chapter_order", "ix_scenes_chapter_id", "ix_scenes_project_id"}
    ),
    "source_documents": frozenset({"ix_source_documents_project_id", "ix_source_project_imported"}),
    "story_beats": frozenset(
        {"ix_beat_project_scene_order", "ix_story_beats_project_id", "ix_story_beats_scene_id"}
    ),
}
_V2_NAMED_INDEXES: dict[str, frozenset[str]] = {
    "chapters": frozenset(
        {
            "ix_chapter_project_story_order",
            "ix_chapters_project_id",
            "ix_chapters_story_id",
        }
    ),
    "characters": frozenset(
        {
            "ix_character_project_story_name",
            "ix_characters_project_id",
            "ix_characters_story_id",
        }
    ),
    "dialogue_attributions": frozenset(
        {"ix_attribution_project_line", "ix_dialogue_attributions_project_id"}
    ),
    "dialogue_lines": frozenset(
        {
            "ix_dialogue_lines_beat_id",
            "ix_dialogue_lines_project_id",
            "ix_dialogue_lines_scene_id",
            "ix_line_project_scene_order",
        }
    ),
    "document_extractions": frozenset(
        {
            "ix_document_extractions_project_id",
            "ix_document_extractions_source_document_id",
            "ix_extraction_project_source_created",
        }
    ),
    "human_corrections": frozenset(
        {
            "ix_correction_project_line_time",
            "ix_human_corrections_attribution_id",
            "ix_human_corrections_line_id",
            "ix_human_corrections_project_id",
        }
    ),
    "import_reviews": frozenset(
        {
            "ix_import_review_project_created",
            "ix_import_reviews_extraction_id",
            "ix_import_reviews_project_id",
            "ix_import_reviews_review_id",
            "ix_import_reviews_source_document_id",
        }
    ),
    "imported_stories": frozenset(
        {
            "ix_imported_stories_project_id",
            "ix_imported_stories_source_document_id",
            "ix_story_project_imported",
        }
    ),
    "job_events": frozenset({"ix_event_job_attempt_sequence"}),
    "jobs": frozenset(
        {"ix_job_project_created", "ix_job_queue", "ix_job_target", "ix_jobs_project_id"}
    ),
    "parser_executions": frozenset(
        {
            "ix_parser_executions_extraction_id",
            "ix_parser_executions_job_id",
            "ix_parser_executions_project_id",
            "ix_parser_executions_source_document_id",
            "ix_parser_extraction_attempt",
        }
    ),
    "scenes": frozenset(
        {"ix_scene_project_chapter_order", "ix_scenes_chapter_id", "ix_scenes_project_id"}
    ),
    "source_documents": frozenset(
        {
            "ix_source_documents_project_id",
            "ix_source_project_hash",
            "ix_source_project_imported",
        }
    ),
    "story_beats": frozenset(
        {"ix_beat_project_scene_order", "ix_story_beats_project_id", "ix_story_beats_scene_id"}
    ),
}

# table_xinfo includes column order, declared type, nullability, SQL default,
# primary-key ordinal, and hidden/generated state. Version 2 has two deliberately
# accepted layouts: fresh creation and the v1 migration, whose three ALTER TABLE
# job columns retain migration-only defaults and later column ordinals.
_V1_TABLE_XINFO_FINGERPRINT = "53a1ca2cbcb890b452771725de28e022539c24d4eac59ee2064c53e62fc36ed3"
_V2_TABLE_XINFO_FINGERPRINTS = frozenset(
    {
        "d18ed7f51eadc397aa13003676e3d960560367df40f60dfe2b29db874bfc779f",
        "482f3edf97b806740eaba45362205ea535c38625e763f18ee7819efe0608be48",
    }
)

# The index fingerprint includes every named and SQLite-generated index, uniqueness,
# origin/partial flags, key order, descending flags, and collations. Column ids are
# intentionally excluded because the two accepted v2 layouts give migrated job
# columns different physical ordinals while preserving the same index semantics.
_V1_INDEX_FINGERPRINT = "6a4c7791bf45a0fc65e79154be0dd347bbe686656ed2fb516f1e66e1d86ed764"
_V2_INDEX_FINGERPRINT = "642f1ff95a7ad1066dd1b03cedb4af6dc6fd0f94af03613d0b7c6e1f2ac735d3"

# Normalized sqlite_master SQL freezes all table constraints and named index
# definitions. The two v2 values correspond to fresh and migrated construction.
_V1_SCHEMA_SQL_FINGERPRINT = "c5545d96d488c51bed5ed9fba85959d241bba405a396abb0ebde8f5204a88e0a"
_V2_SCHEMA_SQL_FINGERPRINTS = frozenset(
    {
        "07cae8d475d7c0f31b292541bb79320eb930389449a32ca25c5eb5be7aec55b6",
        "35321f14654bef4360237cad738bec07e2098c3dd0a095f5d62372c9915bcfa7",
    }
)

# Schema version 3 is another explicit compatibility contract. These frozen maps
# intentionally do not derive from ORM metadata, so same-version drift fails closed.
_V3_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    **_V2_TABLE_COLUMNS,
    "analysis_runs": frozenset(
        {
            "id",
            "project_id",
            "story_id",
            "source_document_id",
            "source_revision",
            "extraction_id",
            "import_review_record_id",
            "review_id",
            "review_revision",
            "review_decision_id",
            "approval_evidence_fingerprint",
            "story_revision",
            "extraction_revision",
            "extracted_text_sha256",
            "input_fingerprint",
            "correction_set_fingerprint",
            "profile_json",
            "profile_fingerprint",
            "producer_id",
            "producer_version",
            "run_fingerprint",
            "job_id",
            "created_at",
        }
    ),
    "analysis_executions": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "job_id",
            "attempt",
            "outcome",
            "input_fingerprint",
            "profile_fingerprint",
            "agent_registry_fingerprint",
            "output_fingerprint",
            "warnings_json",
            "error_code",
            "error_message",
            "error_retryable",
            "started_at",
            "finished_at",
        }
    ),
    "analysis_snapshots": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "execution_id",
            "ordinal",
            "stage",
            "fingerprint",
            "entity_count",
            "manifest_json",
            "created_at",
        }
    ),
    "analysis_stage_checkpoints": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "job_id",
            "attempt",
            "ordinal",
            "stage",
            "input_fingerprint",
            "profile_fingerprint",
            "payload_fingerprint",
            "payload_json",
            "created_at",
        }
    ),
    "analysis_agent_executions": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "execution_id",
            "ordinal",
            "role",
            "agent_id",
            "agent_version",
            "outcome",
            "input_fingerprint",
            "output_fingerprint",
            "confidence_json",
            "warnings_json",
            "provenance_json",
            "envelope_json",
            "started_at",
            "finished_at",
        }
    ),
    "analysis_entities": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "snapshot_id",
            "collection",
            "ordinal",
            "parent_entity_id",
            "identity_key",
            "start_offset",
            "end_offset",
            "revision",
            "payload_json",
            "fingerprint",
            "confidence_score",
            "confidence_class",
            "confidence_basis",
            "warnings_json",
            "provenance_json",
        }
    ),
    "analysis_evidence_spans": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "entity_id",
            "ordinal",
            "start_offset",
            "end_offset",
            "text_sha256",
            "basis",
            "confidence_score",
            "provenance_json",
        }
    ),
    "analysis_corrections": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "category",
            "target_entity_id",
            "target_key",
            "revision",
            "expected_target_revision",
            "expected_run_fingerprint",
            "previous_value_fingerprint",
            "patch_json",
            "correction_fingerprint",
            "reason",
            "actor_id",
            "supersedes_correction_id",
            "legacy_correction_id",
            "idempotency_key",
            "recorded_at",
        }
    ),
    "analysis_review_decisions": frozenset(
        {
            "id",
            "project_id",
            "run_id",
            "snapshot_id",
            "gate_id",
            "revision",
            "state",
            "artifact_fingerprint",
            "evidence_fingerprint",
            "eligible",
            "rationale",
            "warning_acknowledgements_json",
            "provenance_json",
            "actor_id",
            "idempotency_key",
            "supersedes_decision_id",
            "decided_at",
            "created_at",
        }
    ),
}

_V3_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    **_V2_PRIMARY_KEYS,
    "analysis_runs": ("id",),
    "analysis_executions": ("id",),
    "analysis_snapshots": ("id",),
    "analysis_stage_checkpoints": ("id",),
    "analysis_agent_executions": ("id",),
    "analysis_entities": ("id",),
    "analysis_evidence_spans": ("id",),
    "analysis_corrections": ("id",),
    "analysis_review_decisions": ("id",),
}

_V3_CRITICAL_FOREIGN_KEYS: dict[str, frozenset[_ForeignKeySignature]] = {
    **_V2_CRITICAL_FOREIGN_KEYS,
    "analysis_runs": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("story_id",), "imported_stories", ("id",), "RESTRICT"),
            (("source_document_id",), "source_documents", ("id",), "RESTRICT"),
            (("extraction_id",), "document_extractions", ("id",), "RESTRICT"),
            (("import_review_record_id",), "import_reviews", ("id",), "RESTRICT"),
            (("job_id",), "jobs", ("id",), "RESTRICT"),
        }
    ),
    "analysis_executions": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "CASCADE"),
            (("job_id",), "jobs", ("id",), "RESTRICT"),
        }
    ),
    "analysis_snapshots": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "CASCADE"),
            (("execution_id",), "analysis_executions", ("id",), "CASCADE"),
        }
    ),
    "analysis_stage_checkpoints": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "CASCADE"),
            (("job_id",), "jobs", ("id",), "CASCADE"),
        }
    ),
    "analysis_agent_executions": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "CASCADE"),
            (("execution_id",), "analysis_executions", ("id",), "CASCADE"),
        }
    ),
    "analysis_entities": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "CASCADE"),
            (("snapshot_id",), "analysis_snapshots", ("id",), "CASCADE"),
        }
    ),
    "analysis_evidence_spans": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "CASCADE"),
            (("entity_id",), "analysis_entities", ("id",), "CASCADE"),
        }
    ),
    "analysis_corrections": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "RESTRICT"),
            (
                ("supersedes_correction_id",),
                "analysis_corrections",
                ("id",),
                "RESTRICT",
            ),
        }
    ),
    "analysis_review_decisions": frozenset(
        {
            (("project_id",), "projects", ("id",), "CASCADE"),
            (("run_id",), "analysis_runs", ("id",), "CASCADE"),
            (("snapshot_id",), "analysis_snapshots", ("id",), "RESTRICT"),
            (
                ("supersedes_decision_id",),
                "analysis_review_decisions",
                ("id",),
                "RESTRICT",
            ),
        }
    ),
}

_V3_CRITICAL_UNIQUE_COLUMNS: dict[str, frozenset[tuple[str, ...]]] = {
    **_V2_CRITICAL_UNIQUE_COLUMNS,
    # job_id is enforced by the frozen unique named index created for the column.
    "analysis_runs": frozenset(),
    "analysis_executions": frozenset({("run_id", "attempt"), ("job_id", "attempt")}),
    "analysis_snapshots": frozenset({("execution_id", "ordinal"), ("execution_id", "stage")}),
    "analysis_stage_checkpoints": frozenset(
        {
            ("job_id", "attempt", "ordinal"),
            ("job_id", "attempt", "stage"),
        }
    ),
    "analysis_agent_executions": frozenset({("execution_id", "ordinal"), ("execution_id", "role")}),
    "analysis_entities": frozenset({("run_id", "collection", "ordinal")}),
    "analysis_evidence_spans": frozenset({("entity_id", "ordinal")}),
    "analysis_corrections": frozenset(
        {
            ("run_id", "category", "target_key", "revision"),
            ("run_id", "idempotency_key"),
            ("legacy_correction_id",),
        }
    ),
    "analysis_review_decisions": frozenset(
        {
            ("run_id", "gate_id", "revision"),
            ("run_id", "gate_id", "idempotency_key"),
        }
    ),
}

_V3_CRITICAL_CHECKS: dict[str, frozenset[_CheckSignature]] = {
    **_V2_CRITICAL_CHECKS,
    "analysis_runs": frozenset(
        {
            ("ck_analysis_run_review_revision", "review_revision >= 1"),
            ("ck_analysis_run_source_revision", "source_revision >= 1"),
            ("ck_analysis_run_story_revision", "story_revision >= 1"),
            ("ck_analysis_run_extraction_revision", "extraction_revision >= 1"),
        }
    ),
    "analysis_executions": frozenset(
        {
            ("ck_analysis_execution_attempt", "attempt >= 1"),
            (
                "ck_analysis_execution_outcome",
                "outcome in ('succeeded', 'failed', 'cancelled', 'interrupted')",
            ),
        }
    ),
    "analysis_snapshots": frozenset(
        {
            ("ck_analysis_snapshot_ordinal", "ordinal >= 0"),
            ("ck_analysis_snapshot_entity_count", "entity_count >= 0"),
        }
    ),
    "analysis_stage_checkpoints": frozenset(
        {
            ("ck_analysis_stage_checkpoint_attempt", "attempt >= 1"),
            ("ck_analysis_stage_checkpoint_ordinal", "ordinal >= 0"),
        }
    ),
    "analysis_agent_executions": frozenset(
        {
            ("ck_analysis_agent_ordinal", "ordinal >= 0"),
            (
                "ck_analysis_agent_outcome",
                "outcome in ('succeeded', 'failed', 'skipped', 'cancelled', 'interrupted')",
            ),
        }
    ),
    "analysis_entities": frozenset(
        {
            ("ck_analysis_entity_ordinal", "ordinal >= 0"),
            ("ck_analysis_entity_revision", "revision >= 1"),
            (
                "ck_analysis_entity_span",
                "(start_offset is null and end_offset is null) or "
                "(start_offset >= 0 and end_offset >= start_offset)",
            ),
            (
                "ck_analysis_entity_confidence",
                "confidence_score >= 0 and confidence_score <= 1000000",
            ),
            (
                "ck_analysis_entity_confidence_class",
                "confidence_class in ('unknown', 'low', 'medium', 'high')",
            ),
        }
    ),
    "analysis_evidence_spans": frozenset(
        {
            ("ck_analysis_evidence_ordinal", "ordinal >= 0"),
            (
                "ck_analysis_evidence_span",
                "start_offset >= 0 and end_offset >= start_offset",
            ),
            (
                "ck_analysis_evidence_confidence",
                "confidence_score >= 0 and confidence_score <= 1000000",
            ),
        }
    ),
    "analysis_corrections": frozenset(
        {
            ("ck_analysis_correction_revision", "revision >= 1"),
            (
                "ck_analysis_correction_expected_revision",
                "expected_target_revision >= 1",
            ),
            (
                "ck_analysis_correction_reason",
                "length(trim(reason)) >= 1 and length(reason) <= 1000",
            ),
        }
    ),
    "analysis_review_decisions": frozenset(
        {
            ("ck_analysis_review_revision", "revision >= 1"),
            (
                "ck_analysis_review_gate",
                "gate_id in ('story_structure_review', 'character_registry_review', "
                "'dialogue_attribution_review', 'whole_book_analysis_review')",
            ),
            (
                "ck_analysis_review_state",
                "state in ('pending', 'approved', 'rejected', 'changes_requested', 'invalidated')",
            ),
            (
                "ck_analysis_review_rationale",
                "length(trim(rationale)) >= 1 and length(rationale) <= 4000",
            ),
        }
    ),
}

_V3_NAMED_INDEXES: dict[str, frozenset[str]] = {
    **_V2_NAMED_INDEXES,
    "analysis_runs": frozenset(
        {
            "ix_analysis_runs_project_id",
            "ix_analysis_runs_story_id",
            "ix_analysis_runs_source_document_id",
            "ix_analysis_runs_extraction_id",
            "ix_analysis_runs_import_review_record_id",
            "ix_analysis_runs_job_id",
            "ix_analysis_run_project_created",
            "ix_analysis_run_project_extraction",
        }
    ),
    "analysis_executions": frozenset(
        {
            "ix_analysis_executions_project_id",
            "ix_analysis_executions_run_id",
            "ix_analysis_executions_job_id",
            "ix_analysis_execution_project_run_attempt",
        }
    ),
    "analysis_snapshots": frozenset(
        {
            "ix_analysis_snapshots_project_id",
            "ix_analysis_snapshots_run_id",
            "ix_analysis_snapshots_execution_id",
            "ix_analysis_snapshot_project_run_order",
        }
    ),
    "analysis_stage_checkpoints": frozenset(
        {
            "ix_analysis_stage_checkpoints_project_id",
            "ix_analysis_stage_checkpoints_run_id",
            "ix_analysis_stage_checkpoints_job_id",
            "ix_analysis_stage_checkpoint_project_run_attempt",
        }
    ),
    "analysis_agent_executions": frozenset(
        {
            "ix_analysis_agent_executions_project_id",
            "ix_analysis_agent_executions_run_id",
            "ix_analysis_agent_executions_execution_id",
            "ix_analysis_agent_project_run_order",
        }
    ),
    "analysis_entities": frozenset(
        {
            "ix_analysis_entities_project_id",
            "ix_analysis_entities_run_id",
            "ix_analysis_entities_snapshot_id",
            "ix_analysis_entity_project_run_collection_order",
            "ix_analysis_entity_project_run_identity",
        }
    ),
    "analysis_evidence_spans": frozenset(
        {
            "ix_analysis_evidence_spans_project_id",
            "ix_analysis_evidence_spans_run_id",
            "ix_analysis_evidence_spans_entity_id",
            "ix_analysis_evidence_project_run_entity_order",
        }
    ),
    "analysis_corrections": frozenset(
        {
            "ix_analysis_corrections_project_id",
            "ix_analysis_corrections_run_id",
            "ix_analysis_correction_project_run_recorded",
            "ix_analysis_correction_project_target",
        }
    ),
    "analysis_review_decisions": frozenset(
        {
            "ix_analysis_review_decisions_project_id",
            "ix_analysis_review_decisions_run_id",
            "ix_analysis_review_decisions_snapshot_id",
            "ix_analysis_review_project_run_gate_revision",
        }
    ),
}

# Fresh v3 creation and the supported v1->v2->v3 physical layout differ only in
# historical v2 column/default ordering. Both exact layouts are frozen here.
_V3_TABLE_XINFO_FINGERPRINTS = frozenset(
    {
        "c081f3592ab1b1c6a92319203970d73b29531a5f74839065a65cf99906a99035",
        "3d2a9b60c6316d3c9d4905e822a3e9458ddd59bea36bea7b5df0b7fe58421c35",
    }
)
_V3_INDEX_FINGERPRINT = "ae1d457ec0dd5d2a38c434a9ff717b3902b22560aa85e5a81a8e90db2956f2fa"
_V3_SCHEMA_SQL_FINGERPRINTS = frozenset(
    {
        "460fff58c07d49ecf682109c418a9a1f0df6ab4f917484326a1fb3c73f4bfc9c",
        "1d88e8dd478e6358021f2b14344b395e9017f29e8aad9e4da39ddead314b9623",
    }
)

# Schema version 4 freezes the Phase 3A casting boundary independently from
# runtime ORM reflection. Fingerprints below accept only the two supported physical
# layouts: fresh creation and the historical v1->v2->v3->v4 chain.
_V4_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    **_V3_TABLE_COLUMNS,
    "voice_catalog_revisions": frozenset(
        {
            "id",
            "catalog_id",
            "revision",
            "semantic_version",
            "catalog_fingerprint",
            "provider_set_fingerprint",
            "rights_policy_version",
            "source_kind",
            "active",
            "provenance_json",
            "created_at",
        }
    ),
    "voice_provider_descriptors": frozenset(
        {
            "id",
            "catalog_revision_id",
            "provider_id",
            "provider_version",
            "provider_type",
            "runtime_availability",
            "catalog_availability",
            "synthesis_implemented",
            "network_required",
            "credentials_required",
            "supported_operating_systems_json",
            "supported_languages_json",
            "output_capabilities_json",
            "rights_metadata_capabilities_json",
            "health_status",
            "descriptor_fingerprint",
            "provenance_json",
            "created_at",
        }
    ),
    "voice_model_descriptors": frozenset(
        {
            "id",
            "catalog_revision_id",
            "provider_descriptor_id",
            "model_id",
            "model_name",
            "model_version",
            "supported_languages_json",
            "supported_locales_json",
            "expressive_controls_json",
            "speaking_rate_controls_json",
            "pitch_style_controls_json",
            "output_capabilities_json",
            "execution_classification",
            "rights_classification",
            "availability",
            "deprecated",
            "descriptor_fingerprint",
            "provenance_json",
            "created_at",
        }
    ),
    "voice_profiles": frozenset(
        {
            "id",
            "profile_id",
            "revision",
            "profile_version",
            "catalog_revision_id",
            "provider_descriptor_id",
            "model_descriptor_id",
            "provider_voice_id",
            "display_label",
            "language",
            "locale",
            "declared_accent_dialect",
            "declared_age_presentation_json",
            "declared_vocal_presentation",
            "vocal_weight_texture_json",
            "pitch_range_classification",
            "speaking_rate_range_json",
            "energy_range_json",
            "expressive_range_json",
            "narration_suitability",
            "dialogue_suitability",
            "long_form_suitability",
            "character_role_suitability_json",
            "known_limitations_json",
            "rights_state",
            "consent_status",
            "license_scope",
            "commercial_use_status",
            "attribution_required",
            "voice_cloning_classification",
            "state",
            "profile_fingerprint",
            "provenance_json",
            "created_at",
        }
    ),
    "voice_rights_records": frozenset(
        {
            "id",
            "rights_record_id",
            "voice_profile_record_id",
            "provider_descriptor_id",
            "revision",
            "rights_state",
            "license_identifier",
            "rights_basis",
            "license_scope",
            "commercial_use_status",
            "attribution_required",
            "distribution_limitations_json",
            "voice_cloning_status",
            "consent_status",
            "effective_date",
            "expiration_date",
            "evidence_reference",
            "human_verification_status",
            "rights_fingerprint",
            "provenance_json",
            "created_at",
        }
    ),
    "casting_profiles": frozenset(
        {
            "id",
            "profile_id",
            "semantic_version",
            "producer_id",
            "producer_version",
            "compatibility_rules_json",
            "hard_constraints_json",
            "soft_preferences_json",
            "conflict_rules_json",
            "rights_eligibility_rules_json",
            "pre_reduction_candidate_limit",
            "candidate_limit",
            "explanation_requirements_json",
            "profile_fingerprint",
            "provenance_json",
            "created_at",
        }
    ),
    "casting_runs": frozenset(
        {
            "id",
            "project_id",
            "source_document_id",
            "source_revision",
            "extraction_id",
            "extraction_revision",
            "extracted_text_sha256",
            "import_review_decision_id",
            "analysis_run_id",
            "analysis_snapshot_id",
            "analysis_snapshot_revision",
            "analysis_snapshot_fingerprint",
            "analysis_correction_set_fingerprint",
            "character_registry_fingerprint",
            "phase2_gate_decision_ids_json",
            "phase2_gate_evidence_fingerprint",
            "casting_profile_id",
            "casting_profile_fingerprint",
            "catalog_revision_id",
            "catalog_fingerprint",
            "effective_correction_set_fingerprint",
            "producer_id",
            "producer_version",
            "input_fingerprint",
            "run_fingerprint",
            "job_id",
            "state",
            "warnings_json",
            "created_at",
            "published_at",
        }
    ),
    "production_roles": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "ordinal",
            "role_type",
            "phase2_entity_id",
            "character_id",
            "role_importance",
            "effective_display_label",
            "analysis_run_id",
            "analysis_snapshot_id",
            "dialogue_line_count",
            "narration_span_count",
            "approximate_word_count",
            "chapter_range_json",
            "scene_range_json",
            "language_requirements_json",
            "performance_requirements_json",
            "warnings_json",
            "provenance_json",
            "status",
            "role_fingerprint",
            "created_at",
        }
    ),
    "casting_candidates": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "role_id",
            "voice_profile_record_id",
            "role_revision",
            "ordinal",
            "compatibility_status",
            "compatibility_score",
            "confidence_class",
            "hard_constraint_results_json",
            "soft_preference_results_json",
            "rights_eligibility",
            "language_eligibility",
            "provider_availability",
            "model_availability",
            "long_form_suitability",
            "conflict_warnings_json",
            "explanation_json",
            "provenance_json",
            "input_fingerprint",
            "output_fingerprint",
            "created_at",
        }
    ),
    "casting_conflicts": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "primary_role_id",
            "secondary_role_id",
            "voice_profile_record_id",
            "category",
            "severity",
            "status",
            "details_json",
            "evidence_fingerprint",
            "provenance_json",
            "created_at",
        }
    ),
    "cast_assignments": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "role_id",
            "correction_id",
            "voice_profile_record_id",
            "catalog_revision_id",
            "casting_profile_fingerprint",
            "phase2_snapshot_fingerprint",
            "effective_correction_set_fingerprint",
            "authority",
            "assignment_state",
            "rationale",
            "warnings_json",
            "rights_state",
            "revision",
            "provenance_json",
            "supersedes_assignment_id",
            "created_at",
        }
    ),
    "cast_assignment_invalidations": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "role_id",
            "assignment_id",
            "reason_codes_json",
            "evidence_fingerprint",
            "provenance_json",
            "created_at",
        }
    ),
    "casting_corrections": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "role_id",
            "kind",
            "revision",
            "prior_effective_fingerprint",
            "corrected_value_json",
            "correction_fingerprint",
            "actor_id",
            "reason",
            "provenance_json",
            "supersedes_correction_id",
            "idempotency_key",
            "recorded_at",
        }
    ),
    "approved_cast_snapshots": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "revision",
            "phase2_snapshot_fingerprint",
            "catalog_revision_id",
            "catalog_fingerprint",
            "casting_profile_fingerprint",
            "effective_correction_set_fingerprint",
            "role_count",
            "assignment_count",
            "unresolved_role_count",
            "restricted_rights_count",
            "ineligible_rights_count",
            "snapshot_fingerprint",
            "manifest_json",
            "created_at",
        }
    ),
    "casting_gate_reviews": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "cast_snapshot_id",
            "gate_id",
            "revision",
            "eligible",
            "evidence_fingerprint",
            "required_gate_decision_ids_json",
            "warnings_json",
            "provenance_json",
            "created_at",
        }
    ),
    "casting_gate_decisions": frozenset(
        {
            "id",
            "project_id",
            "casting_run_id",
            "cast_snapshot_id",
            "gate_review_id",
            "gate_id",
            "revision",
            "decision",
            "evidence_fingerprint",
            "actor_id",
            "warning_acknowledgements_json",
            "rationale",
            "provenance_json",
            "supersedes_decision_id",
            "idempotency_key",
            "decided_at",
            "created_at",
        }
    ),
}

_V4_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    **_V3_PRIMARY_KEYS,
    "voice_catalog_revisions": ("id",),
    "voice_provider_descriptors": ("id",),
    "voice_model_descriptors": ("id",),
    "voice_profiles": ("id",),
    "voice_rights_records": ("id",),
    "casting_profiles": ("id",),
    "casting_runs": ("id",),
    "production_roles": ("id",),
    "casting_candidates": ("id",),
    "casting_conflicts": ("id",),
    "cast_assignments": ("id",),
    "cast_assignment_invalidations": ("id",),
    "casting_corrections": ("id",),
    "approved_cast_snapshots": ("id",),
    "casting_gate_reviews": ("id",),
    "casting_gate_decisions": ("id",),
}

_V4_NAMED_INDEXES: dict[str, frozenset[str]] = {
    **_V3_NAMED_INDEXES,
    "voice_catalog_revisions": frozenset({"ix_voice_catalog_active_created"}),
    "voice_provider_descriptors": frozenset(
        {
            "ix_voice_provider_catalog_order",
            "ix_voice_provider_descriptors_catalog_revision_id",
        }
    ),
    "voice_model_descriptors": frozenset(
        {
            "ix_voice_model_catalog_provider_order",
            "ix_voice_model_descriptors_catalog_revision_id",
            "ix_voice_model_descriptors_provider_descriptor_id",
        }
    ),
    "voice_profiles": frozenset(
        {
            "ix_voice_profile_catalog_language",
            "ix_voice_profile_catalog_state_label",
            "ix_voice_profiles_catalog_revision_id",
            "ix_voice_profiles_model_descriptor_id",
            "ix_voice_profiles_provider_descriptor_id",
        }
    ),
    "voice_rights_records": frozenset(
        {
            "ix_voice_rights_profile_revision",
            "ix_voice_rights_records_provider_descriptor_id",
            "ix_voice_rights_records_voice_profile_record_id",
        }
    ),
    "casting_profiles": frozenset({"ix_casting_profile_identity"}),
    "casting_runs": frozenset(
        {
            "ix_casting_run_project_analysis",
            "ix_casting_run_project_created",
            "ix_casting_runs_analysis_run_id",
            "ix_casting_runs_analysis_snapshot_id",
            "ix_casting_runs_casting_profile_id",
            "ix_casting_runs_catalog_revision_id",
            "ix_casting_runs_extraction_id",
            "ix_casting_runs_job_id",
            "ix_casting_runs_project_id",
            "ix_casting_runs_source_document_id",
        }
    ),
    "production_roles": frozenset(
        {
            "ix_production_role_project_run_order",
            "ix_production_role_project_type",
            "ix_production_roles_analysis_run_id",
            "ix_production_roles_analysis_snapshot_id",
            "ix_production_roles_casting_run_id",
            "ix_production_roles_project_id",
        }
    ),
    "casting_candidates": frozenset(
        {
            "ix_casting_candidate_project_run_role_order",
            "ix_casting_candidate_role_score",
            "ix_casting_candidates_casting_run_id",
            "ix_casting_candidates_project_id",
            "ix_casting_candidates_role_id",
            "ix_casting_candidates_voice_profile_record_id",
        }
    ),
    "casting_conflicts": frozenset(
        {
            "ix_casting_conflict_project_run_status",
            "ix_casting_conflict_run_roles",
            "ix_casting_conflicts_casting_run_id",
            "ix_casting_conflicts_primary_role_id",
            "ix_casting_conflicts_project_id",
            "ix_casting_conflicts_secondary_role_id",
            "ix_casting_conflicts_voice_profile_record_id",
        }
    ),
    "cast_assignments": frozenset(
        {
            "ix_cast_assignment_project_run_role_revision",
            "ix_cast_assignments_casting_run_id",
            "ix_cast_assignments_catalog_revision_id",
            "ix_cast_assignments_correction_id",
            "ix_cast_assignments_project_id",
            "ix_cast_assignments_role_id",
            "ix_cast_assignments_voice_profile_record_id",
        }
    ),
    "cast_assignment_invalidations": frozenset(
        {
            "ix_cast_assignment_invalidation_project_run_role",
            "ix_cast_assignment_invalidations_assignment_id",
            "ix_cast_assignment_invalidations_casting_run_id",
            "ix_cast_assignment_invalidations_project_id",
            "ix_cast_assignment_invalidations_role_id",
        }
    ),
    "casting_corrections": frozenset(
        {
            "ix_casting_correction_project_recorded",
            "ix_casting_correction_project_run_role_revision",
            "ix_casting_corrections_casting_run_id",
            "ix_casting_corrections_project_id",
            "ix_casting_corrections_role_id",
        }
    ),
    "approved_cast_snapshots": frozenset(
        {
            "ix_approved_cast_snapshot_project_run_revision",
            "ix_approved_cast_snapshots_casting_run_id",
            "ix_approved_cast_snapshots_catalog_revision_id",
            "ix_approved_cast_snapshots_project_id",
        }
    ),
    "casting_gate_reviews": frozenset(
        {
            "ix_casting_gate_review_project_run_gate_revision",
            "ix_casting_gate_reviews_cast_snapshot_id",
            "ix_casting_gate_reviews_casting_run_id",
            "ix_casting_gate_reviews_project_id",
        }
    ),
    "casting_gate_decisions": frozenset(
        {
            "ix_casting_gate_decision_project_run_gate_revision",
            "ix_casting_gate_decisions_cast_snapshot_id",
            "ix_casting_gate_decisions_casting_run_id",
            "ix_casting_gate_decisions_gate_review_id",
            "ix_casting_gate_decisions_project_id",
        }
    ),
}

# Filled with exact values generated from the frozen schemas. Keep literals rather
# than deriving them from Base.metadata at runtime.
_V4_TABLE_XINFO_FINGERPRINTS = frozenset(
    {
        "b6614c36b0eca6e77d8de0c5c120ac5bb05cc054496a52f8c5ea68f64faf0f3b",
        "0e4c66c6fe855e1eedc7d2e1ef687e2e49c9d0c505c58a0e66fff34e5389d122",
    }
)
_V4_INDEX_FINGERPRINT = "fa5c2efb6de3c7c7c6f22dc99572e8e8318663499c22ba6ed294975286460360"
_V4_SCHEMA_SQL_FINGERPRINTS = frozenset(
    {
        "f633a359fb0e1068f6638d3856e04764a8749c64b29d956e73af28fd97f1750d",
        "5eea446d0c1aefce883468880dd647494cd3ffe5292b713b02375632a907048a",
    }
)


def _signature_digest(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _quoted_sqlite_identifier(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def _validate_sqlite_object_allow_list(
    connection: Connection,
    *,
    expected_tables: frozenset[str],
    expected_named_indexes: dict[str, frozenset[str]],
) -> None:
    rows = list(
        connection.exec_driver_sql(
            "SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name, tbl_name"
        )
    )
    if any(str(row[0]) not in {"table", "index"} for row in rows):
        raise _unsupported_schema_error()

    actual_tables = frozenset(str(row[1]) for row in rows if str(row[0]) == "table")
    if actual_tables != expected_tables:
        raise _unsupported_schema_error()

    actual_named_indexes = frozenset(
        (str(row[2]), str(row[1]))
        for row in rows
        if str(row[0]) == "index" and not str(row[1]).startswith("sqlite_autoindex_")
    )
    expected_index_objects = frozenset(
        (table_name, index_name)
        for table_name, index_names in expected_named_indexes.items()
        for index_name in index_names
    )
    if actual_named_indexes != expected_index_objects:
        raise _unsupported_schema_error()


def _table_xinfo_fingerprint(
    connection: Connection,
    table_names: frozenset[str],
) -> str:
    tables: list[object] = []
    for table_name in sorted(table_names):
        quoted_table = _quoted_sqlite_identifier(table_name)
        columns: list[object] = []
        for row in connection.exec_driver_sql(f"PRAGMA table_xinfo({quoted_table})"):
            columns.append(
                [
                    int(row[0]),
                    str(row[1]),
                    str(row[2]),
                    int(row[3]),
                    None if row[4] is None else str(row[4]),
                    int(row[5]),
                    int(row[6]),
                ]
            )
        tables.append([table_name, columns])
    return _signature_digest(tables)


def _index_fingerprint(
    connection: Connection,
    table_names: frozenset[str],
) -> str:
    tables: list[object] = []
    for table_name in sorted(table_names):
        quoted_table = _quoted_sqlite_identifier(table_name)
        indexes: list[list[object]] = []
        for row in connection.exec_driver_sql(f"PRAGMA index_list({quoted_table})"):
            index_name = str(row[1])
            quoted_index = _quoted_sqlite_identifier(index_name)
            columns: list[object] = []
            for column in connection.exec_driver_sql(f"PRAGMA index_xinfo({quoted_index})"):
                columns.append(
                    [
                        int(column[0]),
                        None if column[2] is None else str(column[2]),
                        int(column[3]),
                        None if column[4] is None else str(column[4]),
                        int(column[5]),
                    ]
                )
            indexes.append(
                [
                    index_name,
                    int(row[2]),
                    str(row[3]),
                    int(row[4]),
                    columns,
                ]
            )
        tables.append([table_name, sorted(indexes, key=lambda value: str(value[0]))])
    return _signature_digest(tables)


def _schema_sql_fingerprint(connection: Connection) -> str:
    objects: list[object] = []
    for row in connection.exec_driver_sql(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name, tbl_name"
    ):
        sql = None if row[3] is None else " ".join(str(row[3]).split())
        objects.append([str(row[0]), str(row[1]), str(row[2]), sql])
    return _signature_digest(objects)


def _storage_locked_error() -> ServiceError:
    return ServiceError(
        503,
        "STORAGE_LOCKED",
        "The project storage is already in use.",
        retryable=True,
    )


def _database_unavailable_error() -> ServiceError:
    return ServiceError(
        503,
        "DATABASE_UNAVAILABLE",
        "The project database is unavailable.",
        retryable=True,
    )


def _unsupported_schema_error() -> ServiceError:
    return ServiceError(
        503,
        "DATABASE_SCHEMA_UNSUPPORTED",
        "The project database schema is not supported by this service.",
    )


def _backup_failed_error() -> ServiceError:
    return ServiceError(
        503,
        "DATABASE_BACKUP_FAILED",
        "A verified recovery backup could not be created before migration.",
    )


class _DataDirectoryLock:
    """Hold one non-blocking OS lock for the canonical service data directory."""

    def __init__(self, data_directory: Path) -> None:
        self.path = data_directory / _STORAGE_LOCK_FILENAME
        self._handle: BinaryIO | None = None
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOINHERIT", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise _database_unavailable_error() from exc

        try:
            handle = os.fdopen(descriptor, "r+b", buffering=0)
        except OSError as exc:
            os.close(descriptor)
            raise _database_unavailable_error() from exc

        try:
            self._verify_regular_file(handle)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
        except ServiceError:
            handle.close()
            raise
        except OSError as exc:
            handle.close()
            raise _database_unavailable_error() from exc
        except Exception:
            handle.close()
            raise
        try:
            self._lock(handle)
        except OSError as exc:
            handle.close()
            raise _storage_locked_error() from exc
        self._handle = handle

    def _verify_regular_file(self, handle: BinaryIO) -> None:
        try:
            opened = os.fstat(handle.fileno())
            linked = os.lstat(self.path)
        except OSError as exc:
            raise _database_unavailable_error() from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(linked.st_mode)
            or stat.S_ISLNK(linked.st_mode)
            or opened.st_dev != linked.st_dev
            or opened.st_ino != linked.st_ino
        ):
            raise _database_unavailable_error()

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            self._unlock(handle)
        except OSError:
            pass
        finally:
            handle.close()


class Database:
    def __init__(self, database_path: Path) -> None:
        data_directory = ensure_private_directory(database_path.parent).resolve(strict=False)
        self._data_lock = _DataDirectoryLock(data_directory)
        self.path = database_path.resolve(strict=False)
        try:
            self.engine = create_engine(
                f"sqlite:///{self.path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 5},
                pool_pre_ping=True,
            )
            event.listen(self.engine, "connect", self._configure_connection)
            self.sessions = sessionmaker(
                bind=self.engine,
                expire_on_commit=False,
                autoflush=False,
            )
            self._initialize_schema()
            self._verify()
        except Exception:
            engine = getattr(self, "engine", None)
            if engine is not None:
                engine.dispose()
            self._data_lock.close()
            raise

    @staticmethod
    def _configure_connection(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    def _initialize_schema(self) -> None:
        try:
            with self.engine.connect() as connection:
                current_version = int(
                    connection.exec_driver_sql("PRAGMA user_version").scalar_one()
                )
                ledger_exists = (
                    connection.exec_driver_sql(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (_SCHEMA_LEDGER_TABLE,),
                    ).scalar_one_or_none()
                    is not None
                )
                user_tables = list(
                    connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                        "ORDER BY name"
                    ).scalars()
                )
                if current_version not in {
                    0,
                    _OLDEST_MIGRATABLE_SCHEMA_VERSION,
                    _PHASE1_SCHEMA_VERSION,
                    _PHASE2_SCHEMA_VERSION,
                    _DATABASE_SCHEMA_VERSION,
                }:
                    raise _unsupported_schema_error()
                if current_version == 0 and user_tables:
                    raise _unsupported_schema_error()
                if current_version > 0:
                    self._validate_schema_ledger(
                        connection,
                        ledger_exists,
                        current_version=current_version,
                    )
                if current_version == _OLDEST_MIGRATABLE_SCHEMA_VERSION:
                    self._validate_v1_schema_signature(connection)
                elif current_version == _PHASE1_SCHEMA_VERSION:
                    self._validate_v2_schema_signature(connection)
                elif current_version == _PHASE2_SCHEMA_VERSION:
                    self._validate_v3_schema_signature(connection)
                elif current_version == _DATABASE_SCHEMA_VERSION:
                    self._validate_v4_schema_signature(connection)

                # End SQLAlchemy's read-only autobegin before backup/journal changes.
                connection.rollback()
                if current_version == _OLDEST_MIGRATABLE_SCHEMA_VERSION:
                    self._create_verified_v1_backup()
                elif current_version == _PHASE1_SCHEMA_VERSION:
                    self._create_verified_v2_backup()
                elif current_version == _PHASE2_SCHEMA_VERSION:
                    self._create_verified_v3_backup()

                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                connection.commit()

                if current_version == 0:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        Base.metadata.create_all(connection)
                        connection.exec_driver_sql(
                            "CREATE TABLE schema_migrations ("
                            "version INTEGER PRIMARY KEY, "
                            "applied_at TEXT NOT NULL, "
                            "service_version TEXT NOT NULL"
                            ")"
                        )
                        connection.exec_driver_sql(
                            "INSERT INTO schema_migrations "
                            "(version, applied_at, service_version) "
                            "VALUES (?, ?, ?), (?, ?, ?), (?, ?, ?), (?, ?, ?)",
                            (
                                1,
                                utc_now(),
                                SERVICE_VERSION,
                                _PHASE1_SCHEMA_VERSION,
                                utc_now(),
                                SERVICE_VERSION,
                                _PHASE2_SCHEMA_VERSION,
                                utc_now(),
                                SERVICE_VERSION,
                                _DATABASE_SCHEMA_VERSION,
                                utc_now(),
                                SERVICE_VERSION,
                            ),
                        )
                        connection.exec_driver_sql(
                            f"PRAGMA user_version = {_DATABASE_SCHEMA_VERSION}"
                        )
                        self._validate_v4_schema_signature(connection)
                    except Exception:
                        connection.rollback()
                        raise
                    else:
                        connection.commit()
                elif current_version == _OLDEST_MIGRATABLE_SCHEMA_VERSION:
                    self._migrate_v1_to_v2(connection)
                    self._create_verified_v2_backup()
                    self._migrate_v2_to_v3(connection)
                    self._create_verified_v3_backup()
                    self._migrate_v3_to_v4(connection)
                elif current_version == _PHASE1_SCHEMA_VERSION:
                    self._migrate_v2_to_v3(connection)
                    self._create_verified_v3_backup()
                    self._migrate_v3_to_v4(connection)
                elif current_version == _PHASE2_SCHEMA_VERSION:
                    self._migrate_v3_to_v4(connection)
        except ServiceError:
            raise
        except Exception as exc:
            raise _database_unavailable_error() from exc

    @staticmethod
    def _validate_schema_ledger(
        connection: Connection,
        ledger_exists: bool,
        *,
        current_version: int,
    ) -> None:
        if not ledger_exists:
            raise _unsupported_schema_error()
        try:
            versions = list(
                connection.exec_driver_sql(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).scalars()
            )
        except Exception as exc:
            raise _unsupported_schema_error() from exc
        if versions != list(range(1, current_version + 1)):
            raise _unsupported_schema_error()

    @staticmethod
    def _validate_v1_schema_signature(
        connection: Connection,
    ) -> None:
        """Require the exact frozen Phase 0 schema before backup or migration."""

        try:
            _validate_sqlite_object_allow_list(
                connection,
                expected_tables=_V1_TABLES,
                expected_named_indexes=_V1_NAMED_INDEXES,
            )
            if (
                _schema_sql_fingerprint(connection) != _V1_SCHEMA_SQL_FINGERPRINT
                or _table_xinfo_fingerprint(connection, _V1_TABLES) != _V1_TABLE_XINFO_FINGERPRINT
                or _index_fingerprint(connection, _V1_TABLES) != _V1_INDEX_FINGERPRINT
            ):
                raise _unsupported_schema_error()
        except ServiceError:
            raise
        except Exception as exc:
            raise _unsupported_schema_error() from exc

    @staticmethod
    def _validate_v2_schema_signature(
        connection: Connection,
    ) -> None:
        """Fail closed on same-version databases that do not match the v2 contract.

        Inspection is always read-only. Existing version-2 files are checked before
        persistent startup changes; fresh and migrated schemas are checked in their
        schema-producing transaction before commit.
        """

        try:
            v2_tables = frozenset(_V2_TABLE_COLUMNS)
            _validate_sqlite_object_allow_list(
                connection,
                expected_tables=v2_tables,
                expected_named_indexes=_V2_NAMED_INDEXES,
            )
            if (
                _schema_sql_fingerprint(connection) not in _V2_SCHEMA_SQL_FINGERPRINTS
                or _table_xinfo_fingerprint(connection, v2_tables)
                not in _V2_TABLE_XINFO_FINGERPRINTS
                or _index_fingerprint(connection, v2_tables) != _V2_INDEX_FINGERPRINT
            ):
                raise _unsupported_schema_error()

            schema = inspect(connection)
            for table_name, expected_columns in _V2_TABLE_COLUMNS.items():
                actual_columns = frozenset(
                    str(column["name"]) for column in schema.get_columns(table_name)
                )
                if actual_columns != expected_columns:
                    raise _unsupported_schema_error()

                primary_key = schema.get_pk_constraint(table_name)
                actual_primary_key = tuple(
                    str(column) for column in primary_key["constrained_columns"]
                )
                if actual_primary_key != _V2_PRIMARY_KEYS[table_name]:
                    raise _unsupported_schema_error()

            for table_name, expected_foreign_keys in _V2_CRITICAL_FOREIGN_KEYS.items():
                actual_foreign_keys = frozenset(
                    (
                        tuple(str(column) for column in foreign_key["constrained_columns"]),
                        str(foreign_key["referred_table"]),
                        tuple(str(column) for column in foreign_key["referred_columns"]),
                        str(foreign_key.get("options", {}).get("ondelete") or "").upper(),
                    )
                    for foreign_key in schema.get_foreign_keys(table_name)
                )
                if actual_foreign_keys != expected_foreign_keys:
                    raise _unsupported_schema_error()

            for table_name, expected_unique_columns in _V2_CRITICAL_UNIQUE_COLUMNS.items():
                actual_unique_columns = frozenset(
                    tuple(str(column) for column in constraint["column_names"])
                    for constraint in schema.get_unique_constraints(table_name)
                )
                if actual_unique_columns != expected_unique_columns:
                    raise _unsupported_schema_error()

            for table_name, expected_checks in _V2_CRITICAL_CHECKS.items():
                actual_checks = frozenset(
                    (
                        str(constraint["name"] or ""),
                        " ".join(str(constraint["sqltext"]).casefold().split()),
                    )
                    for constraint in schema.get_check_constraints(table_name)
                )
                if actual_checks != expected_checks:
                    raise _unsupported_schema_error()
        except ServiceError:
            raise
        except Exception as exc:
            raise _unsupported_schema_error() from exc

    @staticmethod
    def _validate_v3_schema_signature(
        connection: Connection,
    ) -> None:
        """Require the exact frozen Phase 2 schema for every same-version open."""

        try:
            v3_tables = frozenset(_V3_TABLE_COLUMNS)
            _validate_sqlite_object_allow_list(
                connection,
                expected_tables=v3_tables,
                expected_named_indexes=_V3_NAMED_INDEXES,
            )
            if (
                _schema_sql_fingerprint(connection) not in _V3_SCHEMA_SQL_FINGERPRINTS
                or _table_xinfo_fingerprint(connection, v3_tables)
                not in _V3_TABLE_XINFO_FINGERPRINTS
                or _index_fingerprint(connection, v3_tables) != _V3_INDEX_FINGERPRINT
            ):
                raise _unsupported_schema_error()
            schema = inspect(connection)
            for table_name, expected_columns in _V3_TABLE_COLUMNS.items():
                actual_columns = frozenset(
                    str(column["name"]) for column in schema.get_columns(table_name)
                )
                if actual_columns != expected_columns:
                    raise _unsupported_schema_error()
                primary_key = schema.get_pk_constraint(table_name)
                actual_primary_key = tuple(
                    str(column) for column in primary_key["constrained_columns"]
                )
                if actual_primary_key != _V3_PRIMARY_KEYS[table_name]:
                    raise _unsupported_schema_error()

            for table_name, expected_foreign_keys in _V3_CRITICAL_FOREIGN_KEYS.items():
                actual_foreign_keys = frozenset(
                    (
                        tuple(str(column) for column in foreign_key["constrained_columns"]),
                        str(foreign_key["referred_table"]),
                        tuple(str(column) for column in foreign_key["referred_columns"]),
                        str(foreign_key.get("options", {}).get("ondelete") or "").upper(),
                    )
                    for foreign_key in schema.get_foreign_keys(table_name)
                )
                if actual_foreign_keys != expected_foreign_keys:
                    raise _unsupported_schema_error()

            for table_name, expected_unique_columns in _V3_CRITICAL_UNIQUE_COLUMNS.items():
                actual_unique_columns = frozenset(
                    tuple(str(column) for column in constraint["column_names"])
                    for constraint in schema.get_unique_constraints(table_name)
                )
                if actual_unique_columns != expected_unique_columns:
                    raise _unsupported_schema_error()

            for table_name, expected_checks in _V3_CRITICAL_CHECKS.items():
                actual_checks = frozenset(
                    (
                        str(constraint["name"] or ""),
                        " ".join(str(constraint["sqltext"]).casefold().split()),
                    )
                    for constraint in schema.get_check_constraints(table_name)
                )
                if actual_checks != expected_checks:
                    raise _unsupported_schema_error()
        except ServiceError:
            raise
        except Exception as exc:
            raise _unsupported_schema_error() from exc

    @staticmethod
    def _validate_v4_schema_signature(
        connection: Connection,
    ) -> None:
        """Require the exact frozen Phase 3A schema for every same-version open."""

        try:
            v4_tables = frozenset(_V4_TABLE_COLUMNS)
            _validate_sqlite_object_allow_list(
                connection,
                expected_tables=v4_tables,
                expected_named_indexes=_V4_NAMED_INDEXES,
            )
            if (
                _schema_sql_fingerprint(connection) not in _V4_SCHEMA_SQL_FINGERPRINTS
                or _table_xinfo_fingerprint(connection, v4_tables)
                not in _V4_TABLE_XINFO_FINGERPRINTS
                or _index_fingerprint(connection, v4_tables) != _V4_INDEX_FINGERPRINT
            ):
                raise _unsupported_schema_error()

            schema = inspect(connection)
            for table_name, expected_columns in _V4_TABLE_COLUMNS.items():
                actual_columns = frozenset(
                    str(column["name"]) for column in schema.get_columns(table_name)
                )
                if actual_columns != expected_columns:
                    raise _unsupported_schema_error()
                primary_key = schema.get_pk_constraint(table_name)
                actual_primary_key = tuple(
                    str(column) for column in primary_key["constrained_columns"]
                )
                if actual_primary_key != _V4_PRIMARY_KEYS[table_name]:
                    raise _unsupported_schema_error()
        except ServiceError:
            raise
        except Exception as exc:
            raise _unsupported_schema_error() from exc

    @property
    def v1_backup_path(self) -> Path:
        """Stable recovery location retained after a successful v1-to-v2 migration."""

        return self.path.with_name(f"{self.path.stem}.v1-backup{self.path.suffix}")

    @property
    def v2_backup_path(self) -> Path:
        """Stable verified recovery location retained after the v2-to-v3 migration."""

        return self.path.with_name(f"{self.path.stem}.v2-backup{self.path.suffix}")

    @property
    def v3_backup_path(self) -> Path:
        """Stable verified recovery location retained before the v3-to-v4 migration."""

        return self.path.with_name(f"{self.path.stem}.v3-backup{self.path.suffix}")

    @staticmethod
    def _logical_database_digest(connection: sqlite3.Connection) -> str:
        digest = hashlib.sha256()
        for statement in connection.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    @classmethod
    def _verified_v1_digest(cls, path: Path) -> str:
        try:
            with closing(sqlite3.connect(path)) as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise _backup_failed_error()
                if int(connection.execute("PRAGMA user_version").fetchone()[0]) != 1:
                    raise _backup_failed_error()
                if list(connection.execute("PRAGMA foreign_key_check")):
                    raise _backup_failed_error()
                versions = [
                    int(row[0])
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                if versions != [1]:
                    raise _backup_failed_error()
                return cls._logical_database_digest(connection)
        except ServiceError:
            raise
        except Exception as exc:
            raise _backup_failed_error() from exc

    def _create_verified_v1_backup(self) -> None:
        backup_path = self.v1_backup_path
        source_digest = self._verified_v1_digest(self.path)
        if backup_path.exists():
            if self._verified_v1_digest(backup_path) != source_digest:
                raise _backup_failed_error()
            return

        temporary_path = backup_path.with_name(f"{backup_path.name}.tmp-{os.getpid()}-{new_id()}")
        try:
            with (
                closing(sqlite3.connect(self.path)) as source,
                closing(sqlite3.connect(temporary_path)) as destination,
            ):
                source.backup(destination)
                destination.commit()
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
            if self._verified_v1_digest(temporary_path) != source_digest:
                raise _backup_failed_error()
            os.replace(temporary_path, backup_path)
        except ServiceError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except Exception as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise _backup_failed_error() from exc

    @classmethod
    def _verified_v2_digest(cls, path: Path) -> str:
        try:
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise _backup_failed_error()
                if int(connection.execute("PRAGMA user_version").fetchone()[0]) != 2:
                    raise _backup_failed_error()
                if list(connection.execute("PRAGMA foreign_key_check")):
                    raise _backup_failed_error()
                versions = [
                    int(row[0])
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                if versions != [1, 2]:
                    raise _backup_failed_error()
                return cls._logical_database_digest(connection)
        except ServiceError:
            raise
        except Exception as exc:
            raise _backup_failed_error() from exc

    def _create_verified_v2_backup(self) -> None:
        backup_path = self.v2_backup_path
        source_digest = self._verified_v2_digest(self.path)
        if backup_path.exists():
            if self._verified_v2_digest(backup_path) != source_digest:
                raise _backup_failed_error()
            return

        temporary_path = backup_path.with_name(f"{backup_path.name}.tmp-{os.getpid()}-{new_id()}")
        try:
            with (
                closing(sqlite3.connect(self.path)) as source,
                closing(sqlite3.connect(temporary_path)) as destination,
            ):
                source.backup(destination)
                destination.commit()
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
            if self._verified_v2_digest(temporary_path) != source_digest:
                raise _backup_failed_error()
            os.replace(temporary_path, backup_path)
        except ServiceError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except Exception as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise _backup_failed_error() from exc

    @classmethod
    def _verified_v3_digest(cls, path: Path) -> str:
        validation_engine = create_engine(f"sqlite:///{path.resolve(strict=False).as_posix()}")
        try:
            with validation_engine.connect() as validation_connection:
                cls._validate_schema_ledger(
                    validation_connection,
                    True,
                    current_version=_PHASE2_SCHEMA_VERSION,
                )
                cls._validate_v3_schema_signature(validation_connection)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise _backup_failed_error()
                if (
                    int(connection.execute("PRAGMA user_version").fetchone()[0])
                    != _PHASE2_SCHEMA_VERSION
                ):
                    raise _backup_failed_error()
                if list(connection.execute("PRAGMA foreign_key_check")):
                    raise _backup_failed_error()
                versions = [
                    int(row[0])
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                if versions != [1, _PHASE1_SCHEMA_VERSION, _PHASE2_SCHEMA_VERSION]:
                    raise _backup_failed_error()
                return cls._logical_database_digest(connection)
        except Exception as exc:
            raise _backup_failed_error() from exc
        finally:
            validation_engine.dispose()

    def _create_verified_v3_backup(self) -> None:
        backup_path = self.v3_backup_path
        source_digest = self._verified_v3_digest(self.path)
        if backup_path.exists():
            if self._verified_v3_digest(backup_path) != source_digest:
                raise _backup_failed_error()
            return

        temporary_path = backup_path.with_name(f"{backup_path.name}.tmp-{os.getpid()}-{new_id()}")
        try:
            with (
                closing(sqlite3.connect(self.path)) as source,
                closing(sqlite3.connect(temporary_path)) as destination,
            ):
                source.backup(destination)
                destination.commit()
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
            if self._verified_v3_digest(temporary_path) != source_digest:
                raise _backup_failed_error()
            os.replace(temporary_path, backup_path)
        except ServiceError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except Exception as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise _backup_failed_error() from exc

    def _migrate_v1_to_v2(self, connection: Connection) -> None:
        # SQLite cannot remove the legacy source hash uniqueness constraint in place.
        # Disable FK enforcement only for the single table-rebuild transaction, then run
        # a complete FK check before accepting the migrated database.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()) != 0:
            raise _database_unavailable_error()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            self._rebuild_source_documents_v2(connection)
            self._rebuild_imported_stories_v2(connection)
            connection.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN target_type VARCHAR(40) NOT NULL DEFAULT 'story'"
            )
            connection.exec_driver_sql("ALTER TABLE jobs ADD COLUMN target_id VARCHAR(36)")
            connection.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"
            )
            connection.exec_driver_sql(
                "UPDATE jobs SET "
                "target_id = ("
                "SELECT imported_stories.id FROM imported_stories "
                "WHERE imported_stories.project_id = jobs.project_id "
                "AND imported_stories.revision = jobs.input_revision "
                "AND imported_stories.content_fingerprint = jobs.input_fingerprint "
                "ORDER BY imported_stories.imported_at, imported_stories.id LIMIT 1"
                "), "
                'payload_json = \'{"kind":"analyze_story","legacySchemaVersion":1}\''
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_job_target ON jobs (target_type, target_id, created_at, id)"
            )

            # Limit this historical migration to the frozen v2 model set. Newer ORM
            # tables must not leak into the verified intermediate v2 backup.
            Base.metadata.create_all(
                connection,
                tables=[
                    Base.metadata.tables[table_name]
                    for table_name in (
                        "document_extractions",
                        "import_reviews",
                        "parser_executions",
                    )
                ],
            )
            self._synthesize_phase0_ingest_history(connection)
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations "
                "(version, applied_at, service_version) VALUES (?, ?, ?)",
                (_PHASE1_SCHEMA_VERSION, utc_now(), SERVICE_VERSION),
            )
            connection.exec_driver_sql(f"PRAGMA user_version = {_PHASE1_SCHEMA_VERSION}")
            violations = list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
            if violations:
                raise ServiceError(
                    503,
                    "DATABASE_INTEGRITY_FAILED",
                    "The project database needs recovery before it can be changed.",
                )
            self._validate_v2_schema_signature(connection)
        except Exception:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            raise
        else:
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            if int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()) != 1:
                raise _database_unavailable_error()

    def _migrate_v2_to_v3(self, connection: Connection) -> None:
        """Atomically add governed analysis storage and preserve speaker provenance."""

        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            Base.metadata.create_all(
                connection,
                tables=[
                    Base.metadata.tables[table_name]
                    for table_name in (
                        "analysis_runs",
                        "analysis_executions",
                        "analysis_snapshots",
                        "analysis_stage_checkpoints",
                        "analysis_agent_executions",
                        "analysis_entities",
                        "analysis_evidence_spans",
                        "analysis_corrections",
                        "analysis_review_decisions",
                    )
                ],
            )
            # Phase 0/1 speaker corrections remain untouched in their legacy table and
            # also become immutable generalized overlays. No gate decision is created.
            connection.exec_driver_sql(
                "INSERT INTO analysis_corrections ("
                "id, project_id, run_id, category, target_entity_id, target_key, "
                "revision, expected_target_revision, expected_run_fingerprint, "
                "previous_value_fingerprint, patch_json, correction_fingerprint, "
                "reason, actor_id, supersedes_correction_id, legacy_correction_id, "
                "idempotency_key, recorded_at"
                ") "
                "SELECT human_corrections.id, human_corrections.project_id, NULL, "
                "'dialogue_speaker', NULL, "
                "'dialogue-lines:' || (dialogue_lines.start_offset - 1) || ':' || "
                "(dialogue_lines.end_offset + 1), "
                "human_corrections.line_revision - 1, "
                "human_corrections.line_revision - 1, "
                "imported_stories.content_fingerprint, "
                "human_corrections.previous_value_fingerprint, "
                "'{\"characterId\":' || "
                "CASE WHEN human_corrections.corrected_character_id IS NULL THEN 'null' "
                "ELSE '\"' || human_corrections.corrected_character_id || '\"' END || "
                '\',"kind":"dialogue_speaker","legacyPhase":0}\', '
                "human_corrections.previous_value_fingerprint, "
                "CASE WHEN human_corrections.reason IS NULL "
                "OR trim(human_corrections.reason) = '' "
                "THEN 'Migrated Phase 0 speaker correction.' "
                "ELSE substr(trim(human_corrections.reason), 1, 1000) END, "
                "human_corrections.actor_id, "
                "human_corrections.supersedes_correction_id, human_corrections.id, "
                "NULL, human_corrections.recorded_at "
                "FROM human_corrections "
                "JOIN dialogue_lines ON dialogue_lines.id = human_corrections.line_id "
                "JOIN story_beats ON story_beats.id = dialogue_lines.beat_id "
                "JOIN scenes ON scenes.id = story_beats.scene_id "
                "JOIN chapters ON chapters.id = scenes.chapter_id "
                "JOIN imported_stories ON imported_stories.id = chapters.story_id "
                "ORDER BY human_corrections.recorded_at, human_corrections.id"
            )
            for correction in connection.exec_driver_sql(
                "SELECT id, project_id, category, target_key, revision, "
                "previous_value_fingerprint, patch_json, reason, "
                "legacy_correction_id FROM analysis_corrections "
                "WHERE legacy_correction_id IS NOT NULL "
                "ORDER BY recorded_at, id"
            ).mappings():
                correction_fingerprint = request_fingerprint(
                    {
                        "projectId": str(correction["project_id"]),
                        "category": str(correction["category"]),
                        "targetKey": str(correction["target_key"]),
                        "revision": int(correction["revision"]),
                        "previousValueFingerprint": str(correction["previous_value_fingerprint"]),
                        "patch": json.loads(str(correction["patch_json"])),
                        "reason": str(correction["reason"]),
                        "legacyCorrectionId": str(correction["legacy_correction_id"]),
                    }
                )
                connection.exec_driver_sql(
                    "UPDATE analysis_corrections SET correction_fingerprint = ? WHERE id = ?",
                    (
                        correction_fingerprint,
                        str(correction["id"]),
                    ),
                )
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations "
                "(version, applied_at, service_version) VALUES (?, ?, ?)",
                (_PHASE2_SCHEMA_VERSION, utc_now(), SERVICE_VERSION),
            )
            connection.exec_driver_sql(f"PRAGMA user_version = {_PHASE2_SCHEMA_VERSION}")
            violations = list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
            if violations:
                raise ServiceError(
                    503,
                    "DATABASE_INTEGRITY_FAILED",
                    "The project database needs recovery before it can be changed.",
                )
            self._validate_v3_schema_signature(connection)
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    def _migrate_v3_to_v4(self, connection: Connection) -> None:
        """Atomically add empty governed voice-catalog and casting storage."""

        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            Base.metadata.create_all(
                connection,
                tables=[
                    Base.metadata.tables[table_name]
                    for table_name in (
                        "voice_catalog_revisions",
                        "voice_provider_descriptors",
                        "voice_model_descriptors",
                        "voice_profiles",
                        "voice_rights_records",
                        "casting_profiles",
                        "casting_runs",
                        "production_roles",
                        "casting_candidates",
                        "casting_conflicts",
                        "cast_assignments",
                        "cast_assignment_invalidations",
                        "casting_corrections",
                        "approved_cast_snapshots",
                        "casting_gate_reviews",
                        "casting_gate_decisions",
                    )
                ],
            )
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations "
                "(version, applied_at, service_version) VALUES (?, ?, ?)",
                (_DATABASE_SCHEMA_VERSION, utc_now(), SERVICE_VERSION),
            )
            connection.exec_driver_sql(f"PRAGMA user_version = {_DATABASE_SCHEMA_VERSION}")
            violations = list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
            if violations:
                raise ServiceError(
                    503,
                    "DATABASE_INTEGRITY_FAILED",
                    "The project database needs recovery before it can be changed.",
                )
            self._validate_v4_schema_signature(connection)
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    @staticmethod
    def _rebuild_source_documents_v2(connection: Connection) -> None:
        connection.exec_driver_sql(
            "CREATE TABLE source_documents_v2 ("
            "id VARCHAR(36) NOT NULL PRIMARY KEY, "
            "project_id VARCHAR(36) NOT NULL, "
            "display_name VARCHAR(255) NOT NULL, "
            "media_type VARCHAR(80) NOT NULL, "
            "declared_format VARCHAR(16) NOT NULL, "
            "content_sha256 VARCHAR(64) NOT NULL, "
            "text_sha256 VARCHAR(64), "
            "byte_length INTEGER NOT NULL, "
            "encoding VARCHAR(24), "
            "newline_style VARCHAR(24), "
            "storage_key VARCHAR(512) NOT NULL, "
            "imported_at VARCHAR(32) NOT NULL, "
            "revision INTEGER NOT NULL, "
            "source_revision INTEGER NOT NULL, "
            "supersedes_document_id VARCHAR(36), "
            "extraction_status VARCHAR(24) NOT NULL, "
            "provenance_json TEXT NOT NULL, "
            "warnings_json TEXT NOT NULL, "
            "CONSTRAINT uq_source_project_source_revision "
            "UNIQUE (project_id, source_revision), "
            "CONSTRAINT ck_source_byte_length CHECK (byte_length >= 0), "
            "CONSTRAINT ck_source_revision CHECK (revision >= 1), "
            "CONSTRAINT ck_source_logical_revision CHECK (source_revision >= 1), "
            "CONSTRAINT ck_source_extraction_status CHECK ("
            "extraction_status IN ('pending', 'running', 'complete', 'partial', 'failed')"
            "), "
            "FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, "
            "FOREIGN KEY(supersedes_document_id) "
            "REFERENCES source_documents (id) ON DELETE RESTRICT"
            ")"
        )
        connection.exec_driver_sql(
            "WITH ranked AS ("
            "SELECT source_documents.*, "
            "ROW_NUMBER() OVER ("
            "PARTITION BY project_id ORDER BY imported_at, id"
            ") AS migrated_source_revision, "
            "LAG(id) OVER ("
            "PARTITION BY project_id ORDER BY imported_at, id"
            ") AS migrated_supersedes_id "
            "FROM source_documents"
            ") "
            "INSERT INTO source_documents_v2 ("
            "id, project_id, display_name, media_type, declared_format, "
            "content_sha256, text_sha256, byte_length, encoding, newline_style, "
            "storage_key, imported_at, revision, source_revision, "
            "supersedes_document_id, extraction_status, provenance_json, warnings_json"
            ") "
            "SELECT id, project_id, display_name, media_type, declared_format, "
            "content_sha256, text_sha256, byte_length, encoding, newline_style, "
            "storage_key, imported_at, revision, migrated_source_revision, "
            "migrated_supersedes_id, "
            "CASE WHEN EXISTS ("
            "SELECT 1 FROM imported_stories "
            "WHERE imported_stories.source_document_id = ranked.id"
            ") THEN 'complete' ELSE 'failed' END, "
            "provenance_json, warnings_json "
            "FROM ranked"
        )
        connection.exec_driver_sql("DROP TABLE source_documents")
        connection.exec_driver_sql("ALTER TABLE source_documents_v2 RENAME TO source_documents")
        connection.exec_driver_sql(
            "CREATE INDEX ix_source_documents_project_id ON source_documents (project_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_source_project_imported "
            "ON source_documents (project_id, imported_at, id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_source_project_hash ON source_documents (project_id, content_sha256)"
        )

    @staticmethod
    def _rebuild_imported_stories_v2(connection: Connection) -> None:
        connection.exec_driver_sql(
            "CREATE TABLE imported_stories_v2 ("
            "id VARCHAR(36) NOT NULL PRIMARY KEY, "
            "project_id VARCHAR(36) NOT NULL, "
            "source_document_id VARCHAR(36) NOT NULL, "
            "extraction_id VARCHAR(36) NOT NULL, "
            "extraction_revision INTEGER NOT NULL, "
            "title VARCHAR(255) NOT NULL, "
            "exact_text TEXT NOT NULL, "
            "content_fingerprint VARCHAR(64) NOT NULL, "
            "imported_at VARCHAR(32) NOT NULL, "
            "revision INTEGER NOT NULL, "
            "provenance_json TEXT NOT NULL, "
            "warnings_json TEXT NOT NULL, "
            "CONSTRAINT uq_story_extraction UNIQUE (extraction_id), "
            "CONSTRAINT ck_story_revision CHECK (revision >= 1), "
            "CONSTRAINT ck_story_extraction_revision CHECK (extraction_revision >= 1), "
            "FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, "
            "FOREIGN KEY(source_document_id) "
            "REFERENCES source_documents (id) ON DELETE RESTRICT, "
            "FOREIGN KEY(extraction_id) "
            "REFERENCES document_extractions (id) ON DELETE RESTRICT"
            ")"
        )
        connection.exec_driver_sql(
            "INSERT INTO imported_stories_v2 ("
            "id, project_id, source_document_id, extraction_id, extraction_revision, "
            "title, exact_text, content_fingerprint, imported_at, revision, "
            "provenance_json, warnings_json"
            ") "
            "SELECT id, project_id, source_document_id, source_document_id, 1, "
            "title, exact_text, content_fingerprint, imported_at, revision, "
            "provenance_json, warnings_json "
            "FROM imported_stories"
        )
        connection.exec_driver_sql("DROP TABLE imported_stories")
        connection.exec_driver_sql("ALTER TABLE imported_stories_v2 RENAME TO imported_stories")
        connection.exec_driver_sql(
            "CREATE INDEX ix_imported_stories_project_id ON imported_stories (project_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_imported_stories_source_document_id "
            "ON imported_stories (source_document_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_story_project_imported "
            "ON imported_stories (project_id, imported_at, id)"
        )

    @staticmethod
    def _synthesize_phase0_ingest_history(connection: Connection) -> None:
        legacy_manifest = (
            '{"legacyPhase0":true,"schemaVersion":1,"warning":"parser limits were not recorded"}'
        )
        limits_fingerprint = "0" * 64
        connection.exec_driver_sql(
            "INSERT INTO document_extractions ("
            "id, project_id, source_document_id, revision, supersedes_extraction_id, "
            "status, format, extractor_name, extractor_version, input_sha256, "
            "text_sha256, character_count, page_count, encoding, newline_style, "
            "exact_text, text_storage_key, manifest_json, sections_json, "
            "source_mappings_json, evidence_fingerprint, warnings_json, created_at, updated_at"
            ") "
            "SELECT source_documents.id, source_documents.project_id, source_documents.id, "
            "1, NULL, "
            "CASE WHEN imported_stories.id IS NULL THEN 'failed' ELSE 'complete' END, "
            "source_documents.declared_format, 'legacy_phase0_import', '1.0.0', "
            "source_documents.content_sha256, source_documents.text_sha256, "
            "CASE WHEN imported_stories.id IS NULL THEN NULL "
            "ELSE length(imported_stories.exact_text) END, "
            "NULL, source_documents.encoding, source_documents.newline_style, "
            "imported_stories.exact_text, NULL, ?, '[]', '[]', "
            "COALESCE(imported_stories.content_fingerprint, source_documents.text_sha256), "
            "source_documents.warnings_json, source_documents.imported_at, "
            "source_documents.imported_at "
            "FROM source_documents "
            "LEFT JOIN imported_stories "
            "ON imported_stories.source_document_id = source_documents.id",
            (legacy_manifest,),
        )
        connection.exec_driver_sql(
            "INSERT INTO parser_executions ("
            "id, project_id, source_document_id, extraction_id, job_id, attempt, "
            "parser_name, parser_version, outcome, input_sha256, limits_fingerprint, "
            "output_text_sha256, manifest_json, sections_json, source_mappings_json, "
            "warnings_json, error_code, error_message, error_retryable, started_at, finished_at"
            ") "
            "SELECT source_documents.id, source_documents.project_id, source_documents.id, "
            "source_documents.id, NULL, 1, 'legacy_phase0_import', '1.0.0', "
            "CASE WHEN imported_stories.id IS NULL THEN 'failed' ELSE 'succeeded' END, "
            "source_documents.content_sha256, ?, source_documents.text_sha256, ?, "
            "'[]', '[]', source_documents.warnings_json, "
            "CASE WHEN imported_stories.id IS NULL THEN 'LEGACY_STORY_MISSING' ELSE NULL END, "
            "CASE WHEN imported_stories.id IS NULL "
            "THEN 'The legacy imported story record is unavailable.' ELSE NULL END, "
            "0, source_documents.imported_at, source_documents.imported_at "
            "FROM source_documents "
            "LEFT JOIN imported_stories "
            "ON imported_stories.source_document_id = source_documents.id",
            (limits_fingerprint, legacy_manifest),
        )
        connection.exec_driver_sql(
            "INSERT INTO import_reviews ("
            "id, review_id, project_id, source_document_id, extraction_id, "
            "candidate_story_id, revision, state, evidence_fingerprint, preview_text, "
            "preview_truncated, warnings_json, warning_acknowledgements_json, "
            "provenance_json, decision_id, decision_rationale, reason, actor_id, "
            "idempotency_key, decided_at, supersedes_record_id, created_at"
            ") "
            "SELECT imported_stories.id, imported_stories.id, imported_stories.project_id, "
            "imported_stories.source_document_id, imported_stories.source_document_id, "
            "imported_stories.id, 1, 'pending', imported_stories.content_fingerprint, "
            "substr(imported_stories.exact_text, 1, 8000), "
            "CASE WHEN length(imported_stories.exact_text) > 8000 THEN 1 ELSE 0 END, "
            "imported_stories.warnings_json, '[]', "
            '\'{"origin":"migration","actorId":"schema-migrator@2"}\', '
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
            "imported_stories.imported_at "
            "FROM imported_stories"
        )

    def _verify(self) -> None:
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text("PRAGMA quick_check")).scalar_one()
                foreign_key_violations = list(
                    connection.exec_driver_sql("PRAGMA foreign_key_check")
                )
        except Exception as exc:
            raise _database_unavailable_error() from exc
        if result != "ok" or foreign_key_violations:
            raise ServiceError(
                503,
                "DATABASE_INTEGRITY_FAILED",
                "The project database needs recovery before it can be changed.",
            )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def immediate_session(self) -> Iterator[Session]:
        """Serialize a transaction that may append evidence after an initial read."""

        session = self.sessions()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        try:
            self.engine.dispose()
        finally:
            self._data_lock.close()
