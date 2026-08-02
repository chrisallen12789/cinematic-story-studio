from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from cinematic_story_service.database import Database
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.models import Base


def _sqlite_value(database_path: Path, statement: str) -> Any:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(statement).fetchone()[0]


def _database_file_image(database_path: Path) -> dict[str, bytes]:
    return {
        candidate.name: candidate.read_bytes()
        for candidate in database_path.parent.iterdir()
        if candidate.name.startswith(database_path.name)
    }


def _tamper_current_table_definition(
    database_path: Path,
    *,
    table_name: str,
    expected_fragment: str,
    replacement_fragment: str,
) -> None:
    """Rewrite one isolated test schema without changing its version or ledger."""

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        assert row is not None
        table_sql = str(row[0])
        assert table_sql.count(expected_fragment) == 1
        tampered_sql = table_sql.replace(
            expected_fragment,
            replacement_fragment,
            1,
        )
        connection.execute("PRAGMA writable_schema=ON")
        try:
            connection.execute(
                "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?",
                (tampered_sql, table_name),
            )
            # Force future connections to parse the changed catalog entry.
            connection.execute("PRAGMA schema_version = 9002")
        finally:
            connection.execute("PRAGMA writable_schema=OFF")


def _create_phase0_v1_database(database_path: Path) -> None:
    """Create a populated database from the exact Phase 0 schema fixture."""

    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        schema_path = Path(__file__).parent / "fixtures" / "phase0-v1-schema.sql"
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        timestamp = "2026-01-01T00:00:00Z"
        source_a_hash = "a" * 64
        source_b_hash = "b" * 64
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("project-1", "Synthetic Phase 0", "analysis", 3, "story-2", timestamp, timestamp),
        )
        connection.executemany(
            "INSERT INTO source_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "source-1",
                    "project-1",
                    "first.md",
                    "text/markdown",
                    "markdown",
                    source_a_hash,
                    source_a_hash,
                    13,
                    "utf-8",
                    "lf",
                    "projects/project-1/sources/a.md",
                    "2026-01-01T00:00:00Z",
                    1,
                    '{"origin":"import"}',
                    "[]",
                ),
                (
                    "source-2",
                    "project-1",
                    "second.md",
                    "text/markdown",
                    "markdown",
                    source_b_hash,
                    source_b_hash,
                    14,
                    "utf-8",
                    "crlf",
                    "projects/project-1/sources/b.md",
                    "2026-01-02T00:00:00Z",
                    1,
                    '{"origin":"import"}',
                    "[]",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO imported_stories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "story-1",
                    "project-1",
                    "source-1",
                    "First",
                    "# First\nText",
                    source_a_hash,
                    "2026-01-01T00:00:00Z",
                    1,
                    '{"origin":"import"}',
                    "[]",
                ),
                (
                    "story-2",
                    "project-1",
                    "source-2",
                    "Second",
                    "# Second\r\nText",
                    source_b_hash,
                    "2026-01-02T00:00:00Z",
                    1,
                    '{"origin":"import"}',
                    "[]",
                ),
            ],
        )
        connection.execute(
            "INSERT INTO chapters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "chapter-1",
                "project-1",
                "story-2",
                0,
                "Second",
                0,
                14,
                1,
                '{"origin":"analysis"}',
            ),
        )
        connection.execute(
            "INSERT INTO scenes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "scene-1",
                "project-1",
                "chapter-1",
                0,
                "Second",
                None,
                None,
                0,
                14,
                1,
                '{"score":0.9}',
                "[]",
                '{"origin":"analysis"}',
            ),
        )
        connection.execute(
            "INSERT INTO story_beats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "beat-1",
                "project-1",
                "scene-1",
                0,
                "dialogue",
                9,
                13,
                None,
                None,
                1,
                '{"origin":"analysis"}',
            ),
        )
        connection.execute(
            "INSERT INTO characters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "character-1",
                "project-1",
                "story-2",
                "Narrator",
                "narrator",
                "[]",
                "[]",
                1,
                '{"score":0.8}',
                "[]",
                '{"origin":"analysis"}',
            ),
        )
        connection.execute(
            "INSERT INTO dialogue_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "line-1",
                "project-1",
                "scene-1",
                "beat-1",
                0,
                9,
                13,
                "Text",
                "d" * 64,
                2,
                '{"origin":"analysis"}',
            ),
        )
        connection.execute(
            "INSERT INTO dialogue_attributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "attribution-1",
                "project-1",
                "line-1",
                None,
                "character-1",
                "human",
                "[]",
                2,
                '{"score":1.0}',
                "[]",
                '{"origin":"human"}',
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "job-1",
                "project-1",
                "analyze_story",
                "succeeded",
                1,
                source_b_hash,
                1,
                "completed",
                1_000_000,
                1,
                0,
                0,
                "[]",
                None,
                None,
                None,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO human_corrections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "correction-1",
                "project-1",
                "line-1",
                "attribution-1",
                "c" * 64,
                None,
                "character-1",
                "Synthetic human choice",
                "local-human",
                2,
                timestamp,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (1, timestamp, "0.1.0"),
        )
        connection.execute("PRAGMA user_version = 1")


def test_database_holds_exclusive_lock_for_exact_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "shared-data"
    first = Database(data_dir / "first.sqlite3")
    try:
        with pytest.raises(ServiceError) as raised:
            Database(data_dir / "second.sqlite3")
        assert raised.value.code == "STORAGE_LOCKED"
        assert raised.value.retryable is True
        assert str(data_dir) not in raised.value.message
    finally:
        first.close()

    reopened = Database(data_dir / "second.sqlite3")
    reopened.close()


def test_database_initializes_schema_v5_with_contiguous_ledger(tmp_path: Path) -> None:
    database_path = tmp_path / "schema-v5" / "studio.sqlite3"
    database = Database(database_path)
    database.close()
    reopened = Database(database_path)
    reopened.close()

    assert _sqlite_value(database_path, "PRAGMA user_version") == 5
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT version, service_version FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert rows == [
        (1, "0.1.0"),
        (2, "0.1.0"),
        (3, "0.1.0"),
        (4, "0.1.0"),
        (5, "0.1.0"),
    ]
    assert {"document_extractions", "parser_executions", "import_reviews"} <= tables


def test_database_rejects_newer_schema_before_orm_table_creation(tmp_path: Path) -> None:
    database_path = tmp_path / "newer-schema" / "studio.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 5")

    with pytest.raises(ServiceError) as raised:
        Database(database_path)
    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert str(database_path) not in raised.value.message
    assert _sqlite_value(database_path, "PRAGMA user_version") == 5
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "projects" not in tables
    assert "schema_migrations" not in tables


def test_database_rejects_unversioned_nonempty_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "unknown-unversioned" / "studio.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE unknown_data (value TEXT)")

    with pytest.raises(ServiceError) as raised:
        Database(database_path)
    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert _sqlite_value(database_path, "PRAGMA user_version") == 0
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert tables == {"unknown_data"}


def test_database_rejects_v1_without_matching_ledger(tmp_path: Path) -> None:
    database_path = tmp_path / "invalid-v1" / "studio.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 1")

    with pytest.raises(ServiceError) as raised:
        Database(database_path)
    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "projects" not in tables
    assert "schema_migrations" not in tables


def test_database_rejects_incomplete_same_version_v5_without_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "incomplete-v5" / "studio.sqlite3"
    database = Database(database_path)
    database.close()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        connection.execute("DROP TABLE parser_executions")
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]
        assert connection.execute("PRAGMA user_version").fetchone() == (5,)
    before = _database_file_image(database_path)

    with pytest.raises(ServiceError) as raised:
        Database(database_path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert raised.value.message == "The project database schema is not supported by this service."
    assert _database_file_image(database_path) == before
    assert _sqlite_value(database_path, "PRAGMA journal_mode") == "delete"
    assert _sqlite_value(database_path, "PRAGMA user_version") == 5


@pytest.mark.parametrize(
    ("table_name", "expected_fragment", "replacement_fragment"),
    [
        (
            "jobs",
            "payload_json TEXT NOT NULL",
            "payload_json_tampered TEXT NOT NULL",
        ),
        (
            "import_reviews",
            "evidence_fingerprint VARCHAR(64) NOT NULL",
            "evidence_fingerprint TEXT NOT NULL",
        ),
        (
            "import_reviews",
            "evidence_fingerprint VARCHAR(64) NOT NULL",
            "evidence_fingerprint VARCHAR(64)",
        ),
        (
            "jobs",
            "payload_json TEXT NOT NULL",
            "payload_json TEXT NOT NULL DEFAULT '{}'",
        ),
        (
            "document_extractions",
            "FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT",
            "FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE CASCADE",
        ),
        (
            "source_documents",
            "UNIQUE (project_id, source_revision)",
            "UNIQUE (project_id, content_sha256)",
        ),
        (
            "jobs",
            "CHECK (progress >= 0 AND progress <= 1000000)",
            "CHECK (progress >= 0 AND progress <= 2000000)",
        ),
    ],
    ids=[
        "required-column",
        "critical-type",
        "critical-nullability",
        "critical-default",
        "critical-foreign-key",
        "critical-unique-index",
        "critical-check-constraint",
    ],
)
def test_database_rejects_tampered_same_version_v5_signature_without_mutation(
    tmp_path: Path,
    table_name: str,
    expected_fragment: str,
    replacement_fragment: str,
) -> None:
    database_path = tmp_path / f"tampered-v5-{table_name}" / "studio.sqlite3"
    database = Database(database_path)
    database.close()
    _tamper_current_table_definition(
        database_path,
        table_name=table_name,
        expected_fragment=expected_fragment,
        replacement_fragment=replacement_fragment,
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (5,)
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]
    before = _database_file_image(database_path)

    with pytest.raises(ServiceError) as raised:
        Database(database_path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert raised.value.message == "The project database schema is not supported by this service."
    assert table_name not in raised.value.message
    assert str(database_path) not in raised.value.message
    assert _database_file_image(database_path) == before
    assert _sqlite_value(database_path, "PRAGMA journal_mode") == "delete"
    assert _sqlite_value(database_path, "PRAGMA user_version") == 5
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]


@pytest.mark.parametrize(
    ("object_name", "statement"),
    [
        (
            "destructive_review_trigger",
            "CREATE TRIGGER destructive_review_trigger "
            "AFTER INSERT ON import_reviews "
            "BEGIN DELETE FROM import_reviews WHERE id = NEW.id; END",
        ),
        (
            "unrecognized_review_view",
            "CREATE VIEW unrecognized_review_view AS SELECT id FROM import_reviews",
        ),
    ],
    ids=["trigger", "view"],
)
def test_database_rejects_unrecognized_v5_objects_without_mutation(
    tmp_path: Path,
    object_name: str,
    statement: str,
) -> None:
    database_path = tmp_path / f"tampered-v5-{object_name}" / "studio.sqlite3"
    database = Database(database_path)
    database.close()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        connection.execute(statement)
    before = _database_file_image(database_path)

    with pytest.raises(ServiceError) as raised:
        Database(database_path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert object_name not in raised.value.message
    assert str(database_path) not in raised.value.message
    assert _database_file_image(database_path) == before
    assert _sqlite_value(database_path, "PRAGMA journal_mode") == "delete"


def test_database_rejects_missing_v5_index_without_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "tampered-v5-index" / "studio.sqlite3"
    database = Database(database_path)
    database.close()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        connection.execute("DROP INDEX ix_source_project_hash")
    before = _database_file_image(database_path)

    with pytest.raises(ServiceError) as raised:
        Database(database_path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert "ix_source_project_hash" not in raised.value.message
    assert str(database_path) not in raised.value.message
    assert _database_file_image(database_path) == before
    assert _sqlite_value(database_path, "PRAGMA journal_mode") == "delete"


def test_database_rejects_forged_incomplete_v1_before_backup_or_wal(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "forged-v1" / "studio.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "applied_at TEXT NOT NULL, "
            "service_version TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (1, "2026-01-01T00:00:00Z", "0.1.0"),
        )
        connection.execute("PRAGMA user_version = 1")
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    before = _database_file_image(database_path)
    backup_path = database_path.with_name("studio.v1-backup.sqlite3")

    with pytest.raises(ServiceError) as raised:
        Database(database_path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert raised.value.message == "The project database schema is not supported by this service."
    assert str(database_path) not in raised.value.message
    assert _database_file_image(database_path) == before
    assert not backup_path.exists()
    assert _sqlite_value(database_path, "PRAGMA journal_mode") == "delete"
    assert _sqlite_value(database_path, "PRAGMA user_version") == 1


def test_v1_to_v5_migration_preserves_phase0_history_and_verified_backups(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase0-upgrade" / "studio.sqlite3"
    _create_phase0_v1_database(database_path)

    database = Database(database_path)
    backup_path = database.v1_backup_path
    database.close()

    assert backup_path.exists()
    assert _sqlite_value(backup_path, "PRAGMA user_version") == 1
    assert _sqlite_value(database_path, "PRAGMA user_version") == 5
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute(
            "SELECT exact_text FROM imported_stories WHERE id = 'story-2'"
        ).fetchone() == ("# Second\r\nText",)
        assert backup.execute(
            "SELECT reason FROM human_corrections WHERE id = 'correction-1'"
        ).fetchone() == ("Synthetic human choice",)
        assert backup.execute("PRAGMA quick_check").fetchone() == ("ok",)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]
        assert connection.execute(
            "SELECT id, source_revision, supersedes_document_id, extraction_status "
            "FROM source_documents ORDER BY source_revision"
        ).fetchall() == [
            ("source-1", 1, None, "complete"),
            ("source-2", 2, "source-1", "complete"),
        ]
        assert connection.execute(
            "SELECT id, status, extractor_name, exact_text, sections_json, "
            "source_mappings_json "
            "FROM document_extractions ORDER BY source_document_id"
        ).fetchall() == [
            ("source-1", "complete", "legacy_phase0_import", "# First\nText", "[]", "[]"),
            (
                "source-2",
                "complete",
                "legacy_phase0_import",
                "# Second\r\nText",
                "[]",
                "[]",
            ),
        ]
        assert connection.execute(
            "SELECT id, outcome, job_id FROM parser_executions ORDER BY source_document_id"
        ).fetchall() == [
            ("source-1", "succeeded", None),
            ("source-2", "succeeded", None),
        ]
        # The migration records review evidence but never fabricates human approval.
        assert connection.execute(
            "SELECT review_id, revision, state, decision_id, "
            "warning_acknowledgements_json, provenance_json "
            "FROM import_reviews "
            "ORDER BY created_at"
        ).fetchall() == [
            (
                "story-1",
                1,
                "pending",
                None,
                "[]",
                '{"origin":"migration","actorId":"schema-migrator@2"}',
            ),
            (
                "story-2",
                1,
                "pending",
                None,
                "[]",
                '{"origin":"migration","actorId":"schema-migrator@2"}',
            ),
        ]
        assert connection.execute(
            "SELECT id, source_document_id, extraction_id, extraction_revision "
            "FROM imported_stories ORDER BY imported_at"
        ).fetchall() == [
            ("story-1", "source-1", "source-1", 1),
            ("story-2", "source-2", "source-2", 1),
        ]
        story_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'imported_stories'"
        ).fetchone()[0]
        assert "UNIQUE (source_document_id)" not in story_schema
        assert connection.execute(
            "SELECT target_type, target_id, payload_json FROM jobs WHERE id = 'job-1'"
        ).fetchone() == (
            "story",
            "story-2",
            '{"kind":"analyze_story","legacySchemaVersion":1}',
        )
        assert connection.execute(
            "SELECT reason, corrected_character_id FROM human_corrections WHERE id = 'correction-1'"
        ).fetchone() == ("Synthetic human choice", "character-1")
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('source_documents')")}
        assert "uq_source_project_hash" not in indexes
        assert "sqlite_autoindex_source_documents_2" in indexes
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    # Reopening v5 is idempotent and does not replace the retained v1 image.
    original_backup = backup_path.read_bytes()
    reopened = Database(database_path)
    reopened.close()
    assert backup_path.read_bytes() == original_backup


def test_v1_migration_marks_source_without_story_as_failed(tmp_path: Path) -> None:
    database_path = tmp_path / "phase0-orphan-source" / "studio.sqlite3"
    _create_phase0_v1_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM imported_stories WHERE id = 'story-1'")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    database = Database(database_path)
    database.close()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT source_documents.id, source_documents.extraction_status, "
            "document_extractions.status "
            "FROM source_documents "
            "JOIN document_extractions "
            "ON document_extractions.source_document_id = source_documents.id "
            "ORDER BY source_documents.source_revision"
        ).fetchall() == [
            ("source-1", "failed", "failed"),
            ("source-2", "complete", "complete"),
        ]
        assert connection.execute(
            "SELECT source_document_id, outcome, error_code "
            "FROM parser_executions ORDER BY source_document_id"
        ).fetchall() == [
            ("source-1", "failed", "LEGACY_STORY_MISSING"),
            ("source-2", "succeeded", None),
        ]


def test_v1_migration_failure_rolls_back_database_but_retains_recovery_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "failed-upgrade" / "studio.sqlite3"
    _create_phase0_v1_database(database_path)

    with monkeypatch.context() as migration_patch:

        def fail_after_rebuild(_connection: Connection) -> None:
            raise RuntimeError("synthetic v2 migration failure")

        migration_patch.setattr(
            Database,
            "_synthesize_phase0_ingest_history",
            staticmethod(fail_after_rebuild),
        )
        with pytest.raises(ServiceError) as raised:
            Database(database_path)
        assert raised.value.code == "DATABASE_UNAVAILABLE"

    backup_path = database_path.with_name("studio.v1-backup.sqlite3")
    assert backup_path.exists()
    assert _sqlite_value(database_path, "PRAGMA user_version") == 1
    assert _sqlite_value(backup_path, "PRAGMA user_version") == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT exact_text FROM imported_stories WHERE id = 'story-2'"
        ).fetchone() == ("# Second\r\nText",)
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('source_documents')")
        }
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info('jobs')")}
        assert "source_revision" not in source_columns
        assert "target_type" not in job_columns
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]

    # A later clean launch safely reuses the already verified backup and completes.
    recovered = Database(database_path)
    recovered.close()
    assert _sqlite_value(database_path, "PRAGMA user_version") == 5


def test_v1_migration_rejects_invalid_v2_signature_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "invalid-migration-signature" / "studio.sqlite3"
    _create_phase0_v1_database(database_path)
    synthesize = Database._synthesize_phase0_ingest_history

    with monkeypatch.context() as migration_patch:

        def synthesize_with_unrecognized_table(connection: Connection) -> None:
            synthesize(connection)
            connection.exec_driver_sql("CREATE TABLE unrecognized_v2_table (id INTEGER)")

        migration_patch.setattr(
            Database,
            "_synthesize_phase0_ingest_history",
            staticmethod(synthesize_with_unrecognized_table),
        )
        with pytest.raises(ServiceError) as raised:
            Database(database_path)
        assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
        assert "unrecognized_v2_table" not in raised.value.message
        assert str(database_path) not in raised.value.message

    backup_path = database_path.with_name("studio.v1-backup.sqlite3")
    assert backup_path.exists()
    assert _sqlite_value(database_path, "PRAGMA user_version") == 1
    assert _sqlite_value(backup_path, "PRAGMA user_version") == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
        assert "source_revision" not in {
            row[1] for row in connection.execute("PRAGMA table_info('source_documents')")
        }
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'unrecognized_v2_table'"
            ).fetchone()
            is None
        )

    recovered = Database(database_path)
    recovered.close()
    assert _sqlite_value(database_path, "PRAGMA user_version") == 5


def test_v1_migration_foreign_key_failure_is_detected_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "invalid-upgrade-result" / "studio.sqlite3"
    _create_phase0_v1_database(database_path)
    synthesize = Database._synthesize_phase0_ingest_history

    with monkeypatch.context() as migration_patch:

        def synthesize_invalid_history(connection: Connection) -> None:
            synthesize(connection)
            connection.exec_driver_sql(
                "UPDATE import_reviews "
                "SET extraction_id = 'missing-extraction' "
                "WHERE review_id = 'story-2'"
            )

        migration_patch.setattr(
            Database,
            "_synthesize_phase0_ingest_history",
            staticmethod(synthesize_invalid_history),
        )
        with pytest.raises(ServiceError) as raised:
            Database(database_path)
        assert raised.value.code == "DATABASE_INTEGRITY_FAILED"

    assert _sqlite_value(database_path, "PRAGMA user_version") == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
        assert "source_revision" not in {
            row[1] for row in connection.execute("PRAGMA table_info('source_documents')")
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    recovered = Database(database_path)
    recovered.close()
    assert _sqlite_value(database_path, "PRAGMA user_version") == 5


def test_fresh_v4_signature_failure_rolls_back_schema_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "invalid-fresh-signature" / "studio.sqlite3"
    create_all = Base.metadata.create_all

    def create_incomplete_schema(
        connection: Connection,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        create_all(connection)
        connection.exec_driver_sql("DROP TABLE parser_executions")

    monkeypatch.setattr(Base.metadata, "create_all", create_incomplete_schema)
    with pytest.raises(ServiceError) as raised:
        Database(database_path)
    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert "parser_executions" not in raised.value.message
    assert str(database_path) not in raised.value.message
    assert _sqlite_value(database_path, "PRAGMA user_version") == 0
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert tables == set()


def test_schema_initialization_rolls_back_partial_ddl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "failed-initialization" / "studio.sqlite3"

    def fail_after_ddl(connection: Connection, *_args: object, **_kwargs: object) -> None:
        connection.exec_driver_sql("CREATE TABLE incomplete_schema (id INTEGER)")
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(Base.metadata, "create_all", fail_after_ddl)
    with pytest.raises(ServiceError) as raised:
        Database(database_path)
    assert raised.value.code == "DATABASE_UNAVAILABLE"
    assert _sqlite_value(database_path, "PRAGMA user_version") == 0
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "incomplete_schema" not in tables
    assert "schema_migrations" not in tables
