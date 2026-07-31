from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cinematic_story_service.database import Database
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.models import Base
from cinematic_story_service.util import request_fingerprint
from tests.test_database_safety import _create_phase0_v1_database

_LEGACY_V2_PRESERVATION_TABLES = (
    "projects",
    "source_documents",
    "document_extractions",
    "parser_executions",
    "import_reviews",
    "imported_stories",
    "chapters",
    "scenes",
    "story_beats",
    "characters",
    "dialogue_lines",
    "dialogue_attributions",
    "jobs",
    "human_corrections",
)

TableSnapshot = tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]


def _value(path: Path, statement: str) -> object:
    with sqlite3.connect(path) as connection:
        row = connection.execute(statement).fetchone()
        assert row is not None
        return row[0]


def _create_frozen_v2(path: Path) -> None:
    path.parent.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "phase1-v2-schema.sql"
    with sqlite3.connect(path) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))


def _legacy_table_snapshots(path: Path) -> dict[str, TableSnapshot]:
    snapshots: dict[str, TableSnapshot] = {}
    with sqlite3.connect(path) as connection:
        for table in _LEGACY_V2_PRESERVATION_TABLES:
            table_info = connection.execute(
                "SELECT name, pk FROM pragma_table_info(?) ORDER BY cid",
                (table,),
            ).fetchall()
            assert table_info, f"missing frozen-v2 table {table}"
            columns = tuple(str(row[0]) for row in table_info)
            primary_key_columns = tuple(
                str(row[0]) for row in sorted(table_info, key=lambda row: int(row[1])) if row[1]
            )
            assert primary_key_columns, f"frozen-v2 table {table} must have a primary key"
            order_by = ", ".join(f'"{column}"' for column in primary_key_columns)
            rows = tuple(
                connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY {order_by}'  # noqa: S608
                ).fetchall()
            )
            snapshots[table] = (columns, rows)
    return snapshots


def _snapshot_rows(
    snapshots: dict[str, TableSnapshot],
    table: str,
) -> tuple[dict[str, object], ...]:
    columns, rows = snapshots[table]
    return tuple(dict(zip(columns, row, strict=True)) for row in rows)


def test_fresh_v3_schema_is_frozen_and_reopens_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "fresh" / "studio.sqlite3"
    database = Database(path)
    database.close()

    assert _value(path, "PRAGMA user_version") == 3
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        analysis_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'analysis_%'"
            )
        }
        reason_column = next(
            row
            for row in connection.execute("PRAGMA table_info(analysis_corrections)")
            if row[1] == "reason"
        )
        correction_table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'analysis_corrections'"
        ).fetchone()
        rationale_column = next(
            row
            for row in connection.execute("PRAGMA table_info(analysis_review_decisions)")
            if row[1] == "rationale"
        )
        review_table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'analysis_review_decisions'"
        ).fetchone()
        assert reason_column[3] == 1
        assert correction_table_sql is not None
        assert "ck_analysis_correction_reason" in correction_table_sql[0]
        assert rationale_column[3] == 1
        assert review_table_sql is not None
        assert "ck_analysis_review_rationale" in review_table_sql[0]
    assert analysis_tables == {
        "analysis_runs",
        "analysis_executions",
        "analysis_snapshots",
        "analysis_stage_checkpoints",
        "analysis_agent_executions",
        "analysis_entities",
        "analysis_evidence_spans",
        "analysis_corrections",
        "analysis_review_decisions",
    }

    reopened = Database(path)
    reopened.close()


def test_v1_to_v2_to_v3_preserves_history_and_does_not_fabricate_analysis(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade" / "studio.sqlite3"
    _create_phase0_v1_database(path)

    database = Database(path)
    v1_backup = database.v1_backup_path
    v2_backup = database.v2_backup_path
    database.close()

    assert _value(v1_backup, "PRAGMA user_version") == 1
    assert _value(v2_backup, "PRAGMA user_version") == 2
    assert _value(path, "PRAGMA user_version") == 3
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT exact_text FROM imported_stories WHERE id = 'story-2'"
        ).fetchone() == ("# Second\r\nText",)
        assert connection.execute(
            "SELECT reason FROM human_corrections WHERE id = 'correction-1'"
        ).fetchone() == ("Synthetic human choice",)
        assert connection.execute(
            "SELECT category, run_id, legacy_correction_id "
            "FROM analysis_corrections WHERE id = 'correction-1'"
        ).fetchone() == ("dialogue_speaker", None, "correction-1")
        migrated_correction = connection.execute(
            "SELECT project_id, category, target_key, revision, "
            "previous_value_fingerprint, patch_json, reason, "
            "legacy_correction_id, correction_fingerprint "
            "FROM analysis_corrections WHERE id = 'correction-1'"
        ).fetchone()
        assert migrated_correction is not None
        assert migrated_correction[8] == request_fingerprint(
            {
                "projectId": migrated_correction[0],
                "category": migrated_correction[1],
                "targetKey": migrated_correction[2],
                "revision": migrated_correction[3],
                "previousValueFingerprint": migrated_correction[4],
                "patch": json.loads(migrated_correction[5]),
                "reason": migrated_correction[6],
                "legacyCorrectionId": migrated_correction[7],
            }
        )
        assert migrated_correction[8] != migrated_correction[4]
        assert connection.execute("SELECT count(*) FROM analysis_runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM analysis_snapshots").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM analysis_review_decisions").fetchone() == (
            0,
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v2_blank_legacy_correction_reason_gets_deterministic_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "blank-legacy-reason" / "studio.sqlite3"
    _create_frozen_v2(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE human_corrections SET reason = '   ' WHERE id = 'correction-1'")

    migrated = Database(path)
    migrated.close()

    with sqlite3.connect(path) as connection:
        reason = connection.execute(
            "SELECT reason FROM analysis_corrections WHERE id = 'correction-1'"
        ).fetchone()
        assert reason == ("Migrated Phase 0 speaker correction.",)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE analysis_corrections SET reason = '' WHERE id = 'correction-1'"
            )


def test_verified_v2_backup_migrates_directly_to_v3(tmp_path: Path) -> None:
    direct = tmp_path / "direct-v2" / "studio.sqlite3"
    _create_frozen_v2(direct)

    before = _legacy_table_snapshots(direct)
    projects = _snapshot_rows(before, "projects")
    sources = _snapshot_rows(before, "source_documents")
    extractions = _snapshot_rows(before, "document_extractions")
    parsers = _snapshot_rows(before, "parser_executions")
    import_reviews = _snapshot_rows(before, "import_reviews")
    stories = _snapshot_rows(before, "imported_stories")
    chapters = _snapshot_rows(before, "chapters")
    scenes = _snapshot_rows(before, "scenes")
    beats = _snapshot_rows(before, "story_beats")
    characters = _snapshot_rows(before, "characters")
    dialogue_lines = _snapshot_rows(before, "dialogue_lines")
    attributions = _snapshot_rows(before, "dialogue_attributions")
    jobs = _snapshot_rows(before, "jobs")
    corrections = _snapshot_rows(before, "human_corrections")

    assert projects == (
        {
            "id": "project-1",
            "name": "Synthetic Phase 0",
            "status": "analysis",
            "revision": 3,
            "story_id": "story-2",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    assert [
        (row["id"], row["source_revision"], row["supersedes_document_id"]) for row in sources
    ] == [
        ("source-1", 1, None),
        ("source-2", 2, "source-1"),
    ]
    assert all(row["revision"] == 1 for row in sources)
    assert [row["provenance_json"] for row in sources] == [
        '{"origin":"import"}',
        '{"origin":"import"}',
    ]
    assert [
        (
            row["id"],
            row["source_document_id"],
            row["revision"],
            row["supersedes_extraction_id"],
            row["status"],
            row["extractor_name"],
            row["extractor_version"],
            row["evidence_fingerprint"],
        )
        for row in extractions
    ] == [
        (
            "source-1",
            "source-1",
            1,
            None,
            "complete",
            "legacy_phase0_import",
            "1.0.0",
            "a" * 64,
        ),
        (
            "source-2",
            "source-2",
            1,
            None,
            "complete",
            "legacy_phase0_import",
            "1.0.0",
            "b" * 64,
        ),
    ]
    assert [
        (
            row["id"],
            row["extraction_id"],
            row["attempt"],
            row["parser_name"],
            row["parser_version"],
            row["outcome"],
            row["limits_fingerprint"],
        )
        for row in parsers
    ] == [
        ("source-1", "source-1", 1, "legacy_phase0_import", "1.0.0", "succeeded", "0" * 64),
        ("source-2", "source-2", 1, "legacy_phase0_import", "1.0.0", "succeeded", "0" * 64),
    ]
    assert [
        (row["id"], row["review_id"], row["revision"], row["state"]) for row in import_reviews
    ] == [
        ("review-story-2-r2", "story-2", 2, "approved"),
        ("story-1", "story-1", 1, "pending"),
        ("story-2", "story-2", 1, "pending"),
    ]
    approved_review = next(row for row in import_reviews if row["state"] == "approved")
    assert approved_review["decision_id"] == "decision-story-2-r2"
    assert approved_review["decision_rationale"] == (
        "Approved the deterministic synthetic extraction."
    )
    assert approved_review["actor_id"] == "local-human"
    assert approved_review["supersedes_record_id"] == "story-2"
    assert approved_review["provenance_json"] == (
        '{"origin":"human","actorId":"local-human",'
        '"inputEvidenceFingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}'
    )
    assert [(row["id"], row["extraction_revision"], row["revision"]) for row in stories] == [
        ("story-1", 1, 1),
        ("story-2", 1, 1),
    ]
    assert [(row["id"], row["revision"], row["provenance_json"]) for row in chapters] == [
        ("chapter-1", 1, '{"origin":"analysis"}')
    ]
    assert [(row["id"], row["revision"], row["provenance_json"]) for row in scenes] == [
        ("scene-1", 1, '{"origin":"analysis"}')
    ]
    assert [(row["id"], row["revision"], row["provenance_json"]) for row in beats] == [
        ("beat-1", 1, '{"origin":"analysis"}')
    ]
    assert [(row["id"], row["revision"], row["provenance_json"]) for row in characters] == [
        ("character-1", 1, '{"origin":"analysis"}')
    ]
    assert [
        (row["id"], row["revision"], row["text_sha256"], row["provenance_json"])
        for row in dialogue_lines
    ] == [("line-1", 2, "d" * 64, '{"origin":"analysis"}')]
    assert [
        (
            row["id"],
            row["revision"],
            row["effective_authority"],
            row["effective_speaker_id"],
            row["provenance_json"],
        )
        for row in attributions
    ] == [("attribution-1", 2, "human", "character-1", '{"origin":"human"}')]
    assert [
        (
            row["id"],
            row["type"],
            row["state"],
            row["input_revision"],
            row["input_fingerprint"],
            row["current_attempt"],
            row["stage"],
            row["progress"],
            row["checkpoint_available"],
            row["target_type"],
            row["target_id"],
            row["payload_json"],
        )
        for row in jobs
    ] == [
        (
            "job-1",
            "analyze_story",
            "succeeded",
            1,
            "b" * 64,
            1,
            "completed",
            1_000_000,
            1,
            "story",
            "story-2",
            '{"kind":"analyze_story","legacySchemaVersion":1}',
        )
    ]
    assert [
        (
            row["id"],
            row["line_revision"],
            row["previous_value_fingerprint"],
            row["corrected_character_id"],
            row["reason"],
            row["actor_id"],
            row["recorded_at"],
        )
        for row in corrections
    ] == [
        (
            "correction-1",
            2,
            "c" * 64,
            "character-1",
            "Synthetic human choice",
            "local-human",
            "2026-01-01T00:00:00Z",
        )
    ]

    migrated = Database(direct)
    migrated.close()

    backup = direct.with_name("studio.v2-backup.sqlite3")
    assert _value(direct, "PRAGMA user_version") == 3
    assert _value(backup, "PRAGMA user_version") == 2
    assert _legacy_table_snapshots(direct) == before
    assert _legacy_table_snapshots(backup) == before
    with sqlite3.connect(direct) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert connection.execute("SELECT count(*) FROM analysis_runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM analysis_snapshots").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM analysis_review_decisions").fetchone() == (
            0,
        )
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]


def test_injected_mid_migration_failure_rolls_back_and_retains_v2_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "rollback" / "studio.sqlite3"
    _create_frozen_v2(path)
    original_create_all = Base.metadata.create_all

    def fail_after_creating_tables(bind: Any, *args: Any, **kwargs: Any) -> None:
        original_create_all(bind, *args, **kwargs)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(Base.metadata, "create_all", fail_after_creating_tables)
    with pytest.raises(ServiceError):
        Database(path)

    backup = path.with_name("studio.v2-backup.sqlite3")
    assert _value(path, "PRAGMA user_version") == 2
    assert _value(backup, "PRAGMA user_version") == 2
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "analysis_runs" not in tables
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_corrupt_existing_v2_backup_is_rejected_without_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "corrupt-backup" / "studio.sqlite3"
    _create_frozen_v2(path)
    backup = path.with_name("studio.v2-backup.sqlite3")
    backup.write_bytes(b"not a sqlite database")
    before = path.read_bytes()

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_BACKUP_FAILED"
    assert path.read_bytes() == before
    assert _value(path, "PRAGMA user_version") == 2


def test_backup_publish_failure_leaves_v2_usable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "unwritable-backup" / "studio.sqlite3"
    _create_frozen_v2(path)
    original_replace = os.replace

    def fail_backup_replace(source: object, destination: object) -> None:
        if "v2-backup" in str(destination):
            raise OSError("injected unwritable backup")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_backup_replace)
    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_BACKUP_FAILED"
    assert _value(path, "PRAGMA user_version") == 2


@pytest.mark.parametrize("drift", ["ledger", "future_version"])
def test_v2_ledger_and_future_version_mismatches_are_rejected(
    tmp_path: Path,
    drift: str,
) -> None:
    path = tmp_path / drift / "studio.sqlite3"
    _create_frozen_v2(path)
    with sqlite3.connect(path) as connection:
        if drift == "ledger":
            connection.execute("DELETE FROM schema_migrations WHERE version = 2")
        else:
            connection.execute("PRAGMA user_version = 4")
    before = path.read_bytes()

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert path.read_bytes() == before


def test_v3_entity_page_query_uses_the_frozen_covering_index(tmp_path: Path) -> None:
    path = tmp_path / "query-plan" / "studio.sqlite3"
    database = Database(path)
    database.close()

    with sqlite3.connect(path) as connection:
        index_names = {row[1] for row in connection.execute("PRAGMA index_list(analysis_entities)")}
        plan = connection.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id FROM analysis_entities "
            "WHERE project_id = ? AND run_id = ? AND collection = ? AND ordinal > ? "
            "ORDER BY ordinal, id LIMIT 51",
            ("project-1", "run-1", "mentions", -1),
        ).fetchall()
    assert "ix_analysis_entity_project_run_collection_order" in index_names
    assert any("ix_analysis_entity_project_run_collection_order" in str(row) for row in plan)


def test_same_version_schema_drift_is_rejected_without_repair(tmp_path: Path) -> None:
    path = tmp_path / "drift" / "studio.sqlite3"
    database = Database(path)
    database.close()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unexpected_phase2_table (id TEXT PRIMARY KEY)")
    before = path.read_bytes()

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert path.read_bytes() == before
