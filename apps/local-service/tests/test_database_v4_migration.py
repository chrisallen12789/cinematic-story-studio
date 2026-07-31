from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cinematic_story_service.database import Database
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.models import Base
from tests.test_database_safety import _create_phase0_v1_database

_PHASE3A_TABLES = (
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

TableSnapshot = tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]


def _create_frozen_v3(path: Path) -> None:
    path.parent.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "phase2-v3-schema.sql"
    with sqlite3.connect(path) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))


def _create_frozen_v2(path: Path) -> None:
    path.parent.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "phase1-v2-schema.sql"
    with sqlite3.connect(path) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))


def _value(path: Path, statement: str) -> object:
    with sqlite3.connect(path) as connection:
        row = connection.execute(statement).fetchone()
        assert row is not None
        return row[0]


def _logical_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(path) as connection:
        for statement in connection.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _table_snapshots(path: Path) -> dict[str, TableSnapshot]:
    snapshots: dict[str, TableSnapshot] = {}
    with sqlite3.connect(path) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'schema_migrations' ORDER BY name"
            )
        ]
        for table in tables:
            columns = tuple(
                str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
            )
            rows = tuple(
                connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid'  # noqa: S608
                ).fetchall()
            )
            snapshots[table] = (columns, rows)
    return snapshots


def _populate_phase2_records(path: Path) -> None:
    """Populate every v3 Phase 2 table with synthetic governed evidence."""

    fingerprint_a = "1" * 64
    fingerprint_b = "2" * 64
    fingerprint_c = "3" * 64
    now = "2026-02-01T00:00:00Z"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO analysis_runs ("
            "id, project_id, story_id, source_document_id, source_revision, "
            "extraction_id, import_review_record_id, review_id, review_revision, "
            "review_decision_id, approval_evidence_fingerprint, story_revision, "
            "extraction_revision, extracted_text_sha256, input_fingerprint, "
            "correction_set_fingerprint, profile_json, profile_fingerprint, "
            "producer_id, producer_version, run_fingerprint, job_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "analysis-run-v3",
                "project-1",
                "story-2",
                "source-2",
                2,
                "source-2",
                "review-story-2-r2",
                "story-2",
                2,
                "decision-story-2-r2",
                "b" * 64,
                1,
                1,
                "b" * 64,
                fingerprint_a,
                fingerprint_b,
                '{"profileId":"synthetic-v3"}',
                fingerprint_c,
                "fixture-analyzer",
                "3.0.0",
                fingerprint_a,
                "job-1",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO analysis_executions ("
            "id, project_id, run_id, job_id, attempt, outcome, input_fingerprint, "
            "profile_fingerprint, agent_registry_fingerprint, output_fingerprint, "
            "warnings_json, error_code, error_message, error_retryable, started_at, finished_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "analysis-execution-v3",
                "project-1",
                "analysis-run-v3",
                "job-1",
                1,
                "succeeded",
                fingerprint_a,
                fingerprint_c,
                fingerprint_b,
                fingerprint_c,
                "[]",
                None,
                None,
                None,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO analysis_snapshots ("
            "id, project_id, run_id, execution_id, ordinal, stage, fingerprint, "
            "entity_count, manifest_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "analysis-snapshot-v3",
                "project-1",
                "analysis-run-v3",
                "analysis-execution-v3",
                0,
                "complete",
                fingerprint_c,
                1,
                '{"entityCount":1,"fixture":"phase2-v3"}',
                now,
            ),
        )
        connection.execute(
            "INSERT INTO analysis_stage_checkpoints ("
            "id, project_id, run_id, job_id, attempt, ordinal, stage, input_fingerprint, "
            "profile_fingerprint, payload_fingerprint, payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "analysis-checkpoint-v3",
                "project-1",
                "analysis-run-v3",
                "job-1",
                1,
                0,
                "publish_reviewable_snapshot",
                fingerprint_a,
                fingerprint_c,
                fingerprint_b,
                '{"complete":true}',
                now,
            ),
        )
        connection.execute(
            "INSERT INTO analysis_agent_executions ("
            "id, project_id, run_id, execution_id, ordinal, role, agent_id, "
            "agent_version, outcome, input_fingerprint, output_fingerprint, "
            "confidence_json, warnings_json, provenance_json, envelope_json, "
            "started_at, finished_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "analysis-agent-v3",
                "project-1",
                "analysis-run-v3",
                "analysis-execution-v3",
                0,
                "structure",
                "fixture-agent",
                "3.0.0",
                "succeeded",
                fingerprint_a,
                fingerprint_b,
                '{"class":"high","score":900000}',
                "[]",
                '{"origin":"fixture"}',
                '{"bounded":true}',
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO analysis_entities ("
            "id, project_id, run_id, snapshot_id, collection, ordinal, "
            "parent_entity_id, identity_key, start_offset, end_offset, revision, "
            "payload_json, fingerprint, confidence_score, confidence_class, "
            "confidence_basis, warnings_json, provenance_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "analysis-entity-v3",
                "project-1",
                "analysis-run-v3",
                "analysis-snapshot-v3",
                "characters",
                0,
                None,
                "character:synthetic",
                0,
                6,
                1,
                '{"displayName":"Synthetic Character"}',
                fingerprint_b,
                900000,
                "high",
                "synthetic fixture evidence",
                "[]",
                '{"origin":"fixture"}',
            ),
        )
        connection.execute(
            "INSERT INTO analysis_evidence_spans ("
            "id, project_id, run_id, entity_id, ordinal, start_offset, end_offset, "
            "text_sha256, basis, confidence_score, provenance_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "analysis-evidence-v3",
                "project-1",
                "analysis-run-v3",
                "analysis-entity-v3",
                0,
                0,
                6,
                fingerprint_a,
                "synthetic fixture span",
                900000,
                '{"origin":"fixture"}',
            ),
        )
        gates = (
            "story_structure_review",
            "character_registry_review",
            "dialogue_attribution_review",
            "whole_book_analysis_review",
        )
        for ordinal, gate_id in enumerate(gates, start=1):
            connection.execute(
                "INSERT INTO analysis_review_decisions ("
                "id, project_id, run_id, snapshot_id, gate_id, revision, state, "
                "artifact_fingerprint, evidence_fingerprint, eligible, rationale, "
                "warning_acknowledgements_json, provenance_json, actor_id, "
                "idempotency_key, supersedes_decision_id, decided_at, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"analysis-decision-v3-{ordinal}",
                    "project-1",
                    "analysis-run-v3",
                    "analysis-snapshot-v3",
                    gate_id,
                    1,
                    "approved",
                    fingerprint_c,
                    fingerprint_b,
                    1,
                    "Approved synthetic Phase 2 fixture evidence.",
                    "[]",
                    '{"origin":"fixture-human"}',
                    "fixture-human",
                    f"fixture-gate-{ordinal}",
                    None,
                    now,
                    now,
                ),
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_frozen_v3_to_v4_preserves_all_history_and_creates_no_casting_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade" / "studio.sqlite3"
    _create_frozen_v3(path)
    _populate_phase2_records(path)
    before = _table_snapshots(path)
    source_digest = Database._verified_v3_digest(path)
    source_logical_digest = _logical_digest(path)

    database = Database(path)
    backup = database.v3_backup_path
    database.close()

    assert _value(path, "PRAGMA user_version") == 4
    assert _value(backup, "PRAGMA user_version") == 3
    assert Database._verified_v3_digest(backup) == source_digest
    assert _logical_digest(backup) == source_logical_digest
    assert _table_snapshots(backup) == before
    after = _table_snapshots(path)
    assert {table: after[table] for table in before} == before
    for table in _PHASE3A_TABLES:
        assert after[table][1] == ()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("origin", ["v1", "v2", "v3"])
def test_supported_historical_chain_reaches_exact_v4(
    tmp_path: Path,
    origin: str,
) -> None:
    path = tmp_path / origin / "studio.sqlite3"
    if origin == "v1":
        _create_phase0_v1_database(path)
    elif origin == "v2":
        _create_frozen_v2(path)
    else:
        _create_frozen_v3(path)

    database = Database(path)
    backup_paths = {
        "v1": database.v1_backup_path,
        "v2": database.v2_backup_path,
        "v3": database.v3_backup_path,
    }
    database.close()

    assert _value(path, "PRAGMA user_version") == 4
    assert _value(backup_paths[origin], "PRAGMA user_version") == int(origin[1:])
    if origin == "v1":
        assert backup_paths["v2"].exists()
        assert backup_paths["v3"].exists()
    if origin == "v2":
        assert backup_paths["v3"].exists()
    reopened = Database(path)
    reopened.close()


def test_fresh_v4_is_frozen_reopenable_and_empty(tmp_path: Path) -> None:
    path = tmp_path / "fresh" / "studio.sqlite3"
    database = Database(path)
    database.close()

    assert _value(path, "PRAGMA user_version") == 4
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        for table in _PHASE3A_TABLES:
            assert connection.execute(
                f'SELECT count(*) FROM "{table}"'  # noqa: S608
            ).fetchone() == (0,)
        assignment_foreign_keys = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in connection.execute("PRAGMA foreign_key_list(cast_assignments)")
        }
        assert (
            "correction_id",
            "casting_corrections",
            "id",
            "RESTRICT",
        ) in assignment_foreign_keys
        assignment_indexes = {
            str(row[1]): bool(row[2])
            for row in connection.execute("PRAGMA index_list(cast_assignments)")
        }
        assert assignment_indexes["ix_cast_assignments_correction_id"] is True
        assignment_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cast_assignments'"
            ).fetchone()[0]
        )
        assert "ck_cast_assignment_correction_authority" in assignment_sql
    reopened = Database(path)
    reopened.close()


def test_injected_v4_failure_rolls_back_and_retains_verified_v3_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "rollback" / "studio.sqlite3"
    _create_frozen_v3(path)
    before_digest = Database._verified_v3_digest(path)
    original_create_all = Base.metadata.create_all

    def fail_after_creating_tables(bind: Any, *args: Any, **kwargs: Any) -> None:
        original_create_all(bind, *args, **kwargs)
        raise RuntimeError("injected v4 migration failure")

    monkeypatch.setattr(Base.metadata, "create_all", fail_after_creating_tables)
    with pytest.raises(ServiceError) as raised:
        Database(path)

    backup = path.with_name("studio.v3-backup.sqlite3")
    assert raised.value.code == "DATABASE_UNAVAILABLE"
    assert Database._verified_v3_digest(path) == before_digest
    assert Database._verified_v3_digest(backup) == before_digest
    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert not set(_PHASE3A_TABLES) & tables
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]


def test_corrupt_existing_v3_backup_is_rejected_before_migration(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-backup" / "studio.sqlite3"
    _create_frozen_v3(path)
    before = path.read_bytes()
    backup = path.with_name("studio.v3-backup.sqlite3")
    backup.write_bytes(b"not a sqlite database")

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_BACKUP_FAILED"
    assert path.read_bytes() == before
    assert _value(path, "PRAGMA user_version") == 3


def test_v3_backup_publish_failure_leaves_source_usable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "backup-publish-failure" / "studio.sqlite3"
    _create_frozen_v3(path)
    before_digest = Database._verified_v3_digest(path)
    original_replace = os.replace

    def fail_v3_publish(source: object, destination: object) -> None:
        if "v3-backup" in str(destination):
            raise OSError("injected v3 backup publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_v3_publish)
    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_BACKUP_FAILED"
    assert Database._verified_v3_digest(path) == before_digest
    assert _value(path, "PRAGMA user_version") == 3


@pytest.mark.parametrize("drift", ["extra_table", "extra_column", "ledger_gap", "future"])
def test_v3_precondition_drift_is_rejected_without_backup_or_mutation(
    tmp_path: Path,
    drift: str,
) -> None:
    path = tmp_path / f"v3-{drift}" / "studio.sqlite3"
    _create_frozen_v3(path)
    with sqlite3.connect(path) as connection:
        if drift == "extra_table":
            connection.execute("CREATE TABLE unexpected_casting_table (id TEXT PRIMARY KEY)")
        elif drift == "extra_column":
            connection.execute("ALTER TABLE analysis_runs ADD COLUMN unexpected TEXT")
        elif drift == "ledger_gap":
            connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        else:
            connection.execute("PRAGMA user_version = 5")
    before = path.read_bytes()

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert path.read_bytes() == before
    assert not path.with_name("studio.v3-backup.sqlite3").exists()


@pytest.mark.parametrize(
    ("drift", "statement"),
    [
        ("extra-object", "CREATE TABLE unexpected_v4_table (id TEXT PRIMARY KEY)"),
        ("missing-index", "DROP INDEX ix_casting_candidate_project_run_role_order"),
    ],
)
def test_same_version_v4_drift_is_rejected_without_repair(
    tmp_path: Path,
    drift: str,
    statement: str,
) -> None:
    path = tmp_path / drift / "studio.sqlite3"
    database = Database(path)
    database.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        connection.execute(statement)
    before = path.read_bytes()

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert path.read_bytes() == before
    assert _value(path, "PRAGMA user_version") == 4


def test_recovery_is_backup_only_and_forged_in_place_downgrade_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery-only" / "studio.sqlite3"
    _create_frozen_v3(path)
    database = Database(path)
    backup = database.v3_backup_path
    database.close()
    backup_digest = Database._verified_v3_digest(backup)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        connection.execute("DELETE FROM schema_migrations WHERE version = 4")
        connection.execute("PRAGMA user_version = 3")

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert Database._verified_v3_digest(backup) == backup_digest


def test_casting_pagination_queries_use_frozen_covering_indexes(tmp_path: Path) -> None:
    path = tmp_path / "query-plans" / "studio.sqlite3"
    database = Database(path)
    database.close()

    with sqlite3.connect(path) as connection:
        plans = {
            "voice": connection.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT id FROM voice_profiles "
                "WHERE catalog_revision_id = ? AND state = ? "
                "ORDER BY display_label, profile_id, id LIMIT 101",
                ("catalog-1", "active"),
            ).fetchall(),
            "role": connection.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT id FROM production_roles "
                "WHERE project_id = ? AND casting_run_id = ? AND ordinal > ? "
                "ORDER BY ordinal, id LIMIT 101",
                ("project-1", "casting-run-1", -1),
            ).fetchall(),
            "candidate": connection.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT id FROM casting_candidates "
                "WHERE project_id = ? AND casting_run_id = ? "
                "AND role_id = ? AND ordinal > ? "
                "ORDER BY ordinal, id LIMIT 51",
                ("project-1", "casting-run-1", "role-1", -1),
            ).fetchall(),
            "assignment_correction": connection.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT id FROM cast_assignments "
                "WHERE correction_id = ? AND voice_profile_record_id IS NOT NULL LIMIT 1",
                ("correction-1",),
            ).fetchall(),
        }

    assert any("ix_voice_profile_catalog_state_label" in str(row) for row in plans["voice"])
    assert any("ix_production_role_project_run_order" in str(row) for row in plans["role"])
    assert any(
        "ix_casting_candidate_project_run_role_order" in str(row) for row in plans["candidate"]
    )
    assert any(
        "ix_cast_assignments_correction_id" in str(row) for row in plans["assignment_correction"]
    )
