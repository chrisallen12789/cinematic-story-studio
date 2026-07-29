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


def test_database_initializes_schema_v1_with_matching_ledger(tmp_path: Path) -> None:
    database_path = tmp_path / "schema-v1" / "studio.sqlite3"
    database = Database(database_path)
    database.close()
    reopened = Database(database_path)
    reopened.close()

    assert _sqlite_value(database_path, "PRAGMA user_version") == 1
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT version, service_version FROM schema_migrations"
        ).fetchall()
    assert rows == [(1, "0.1.0")]


def test_database_rejects_newer_schema_before_orm_table_creation(tmp_path: Path) -> None:
    database_path = tmp_path / "newer-schema" / "studio.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(ServiceError) as raised:
        Database(database_path)
    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert str(database_path) not in raised.value.message
    assert _sqlite_value(database_path, "PRAGMA user_version") == 2
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
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
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
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
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "projects" not in tables
    assert "schema_migrations" not in tables


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
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "incomplete_schema" not in tables
    assert "schema_migrations" not in tables
