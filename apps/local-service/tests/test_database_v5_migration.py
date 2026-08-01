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

_PHASE3B_TABLES = (
    "speech_runtime_profiles",
    "speech_runtime_instances",
    "model_package_manifests",
    "model_installations",
    "model_verifications",
    "voice_runtime_bindings",
    "pronunciation_dictionaries",
    "pronunciation_entries",
    "audition_sessions",
    "audition_scripts",
    "text_normalization_plans",
    "speech_provider_requests",
    "audition_clips",
    "audio_artifacts",
    "audition_cache_records",
    "audio_quality_records",
    "audition_review_records",
    "audition_review_decisions",
    "voice_readiness_snapshots",
    "voice_readiness_reviews",
    "voice_readiness_decisions",
    "audition_evidence_invalidations",
)

TableSnapshot = tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]


def _create_frozen_v4(path: Path) -> None:
    path.parent.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "phase3a-v4-schema.sql"
    with sqlite3.connect(path) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))


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


def _seed_representative_v4_history(path: Path) -> None:
    """Add independent Phase 0 and Phase 3A records without changing v4 structure."""

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO projects "
            "(id, name, status, revision, story_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "project-v4-history",
                "Synthetic v4 history",
                "draft",
                1,
                None,
                "2026-03-09T00:00:00Z",
                "2026-03-09T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO voice_catalog_revisions "
            "(id, catalog_id, revision, semantic_version, catalog_fingerprint, "
            "provider_set_fingerprint, rights_policy_version, source_kind, active, "
            "provenance_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "catalog-v4-history",
                "synthetic-v4-catalog",
                1,
                "1.0.0",
                "a" * 64,
                "b" * 64,
                "1.0.0",
                "local_static",
                1,
                '{"origin":"frozen-v4-migration-test"}',
                "2026-03-09T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO casting_profiles "
            "(id, profile_id, semantic_version, producer_id, producer_version, "
            "compatibility_rules_json, hard_constraints_json, soft_preferences_json, "
            "conflict_rules_json, rights_eligibility_rules_json, "
            "pre_reduction_candidate_limit, candidate_limit, explanation_requirements_json, "
            "profile_fingerprint, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "casting-profile-v4-history",
                "synthetic-v4-profile",
                "1.0.0",
                "fixture-producer",
                "1.0.0",
                "[]",
                "[]",
                "[]",
                "[]",
                "[]",
                10,
                5,
                "{}",
                "c" * 64,
                '{"origin":"frozen-v4-migration-test"}',
                "2026-03-09T00:00:00Z",
            ),
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_frozen_v4_fixture_is_the_exact_issued_phase3a_schema(tmp_path: Path) -> None:
    path = tmp_path / "fixture" / "studio.sqlite3"
    _create_frozen_v4(path)

    assert _value(path, "PRAGMA user_version") == 4
    assert Database._verified_v4_digest(path) == _logical_digest(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert len(tables) == 44
        assert not set(_PHASE3B_TABLES) & tables


def test_frozen_v4_to_v5_preserves_history_and_retains_verified_v4_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade" / "studio.sqlite3"
    _create_frozen_v4(path)
    _seed_representative_v4_history(path)
    before = _table_snapshots(path)
    source_digest = Database._verified_v4_digest(path)
    source_logical_digest = _logical_digest(path)

    database = Database(path)
    backup = database.v4_backup_path
    database.close()

    assert _value(path, "PRAGMA user_version") == 5
    assert _value(backup, "PRAGMA user_version") == 4
    assert Database._verified_v4_digest(backup) == source_digest
    assert _logical_digest(backup) == source_logical_digest
    assert _table_snapshots(backup) == before
    after = _table_snapshots(path)
    assert {table: after[table] for table in before} == before
    for table in _PHASE3B_TABLES:
        assert after[table][1] == ()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("origin", ["v1", "v2", "v3", "v4"])
def test_supported_historical_chain_reaches_exact_v5(
    tmp_path: Path,
    origin: str,
) -> None:
    path = tmp_path / origin / "studio.sqlite3"
    if origin == "v1":
        _create_phase0_v1_database(path)
    elif origin == "v2":
        _create_frozen_v2(path)
    elif origin == "v3":
        _create_frozen_v3(path)
    else:
        _create_frozen_v4(path)

    database = Database(path)
    v4_backup = database.v4_backup_path
    database.close()

    assert _value(path, "PRAGMA user_version") == 5
    assert _value(v4_backup, "PRAGMA user_version") == 4
    assert Database._verified_v4_digest(v4_backup)
    if origin == "v1":
        assert path.with_name("studio.v1-backup.sqlite3").exists()
        assert path.with_name("studio.v2-backup.sqlite3").exists()
        assert path.with_name("studio.v3-backup.sqlite3").exists()
    elif origin == "v2":
        assert path.with_name("studio.v2-backup.sqlite3").exists()
        assert path.with_name("studio.v3-backup.sqlite3").exists()
    elif origin == "v3":
        assert path.with_name("studio.v3-backup.sqlite3").exists()
    reopened = Database(path)
    reopened.close()


def test_fresh_v5_has_contiguous_ledger_empty_phase3b_storage_and_reopens(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fresh" / "studio.sqlite3"
    database = Database(path)
    assert not database.v4_backup_path.exists()
    database.close()

    assert _value(path, "PRAGMA user_version") == 5
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]
        for table in _PHASE3B_TABLES:
            assert connection.execute(
                f'SELECT count(*) FROM "{table}"'  # noqa: S608
            ).fetchone() == (0,)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened = Database(path)
    reopened.close()


def test_v5_schema_has_restrictive_history_links_exact_gates_and_targeted_invalidation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "relationships" / "studio.sqlite3"
    database = Database(path)
    database.close()

    with sqlite3.connect(path) as connection:
        installation_foreign_keys = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in connection.execute("PRAGMA foreign_key_list(model_installations)")
        }
        invalidation_foreign_keys = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in connection.execute(
                "PRAGMA foreign_key_list(audition_evidence_invalidations)"
            )
        }
        assert (
            "manifest_id",
            "model_package_manifests",
            "id",
            "RESTRICT",
        ) in installation_foreign_keys
        assert ("clip_id", "audition_clips", "id", "RESTRICT") in invalidation_foreign_keys

        review_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'audition_review_records'"
            ).fetchone()[0]
        )
        readiness_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'voice_readiness_reviews'"
            ).fetchone()[0]
        )
        for gate_id in (
            "per_role_audition_review",
            "narrator_audition_review",
            "character_audition_review",
            "pronunciation_review",
        ):
            assert gate_id in review_sql
        assert "voice_readiness_review" in readiness_sql

        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(audition_evidence_invalidations)")
        }
        assert "ix_audition_evidence_invalidation_source" in indexes
        assert "ix_audition_evidence_invalidation_role" in indexes


def test_v5_constraints_match_canonical_local_speech_contract_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "canonical-contract" / "studio.sqlite3"
    database = Database(path)
    database.close()

    with sqlite3.connect(path) as connection:
        table_sql = {
            str(name): str(sql)
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name IN "
                "('model_package_manifests', 'model_installations', "
                "'model_verifications', 'pronunciation_entries', "
                "'audition_sessions', 'audition_scripts', "
                "'speech_provider_requests', 'audition_cache_records', "
                "'audition_review_decisions', 'voice_readiness_decisions', "
                "'audition_evidence_invalidations')"
            )
        }
        assert "'fixture_only'" in table_sql["model_package_manifests"]
        assert "'prohibited'" not in table_sql["model_package_manifests"]
        assert "'official_model_repository'" in table_sql["model_package_manifests"]
        assert "'maintainer_referenced_conversion'" in table_sql["model_package_manifests"]
        assert "'repair_required'" in table_sql["model_installations"]
        for status in ("'verified'", "'mismatch'", "'missing'", "'unsafe'"):
            assert status in table_sql["model_verifications"]
        for scope in (
            "'project'",
            "'narrator'",
            "'character_role'",
            "'chapter'",
            "'scene'",
            "'custom'",
        ):
            assert scope in table_sql["pronunciation_entries"]
        assert "'queued'" in table_sql["audition_sessions"]
        assert "'invalidated'" in table_sql["audition_sessions"]
        assert "'approved_manuscript_excerpt'" in table_sql["audition_scripts"]
        assert "'role_dialogue_excerpt'" in table_sql["audition_scripts"]
        assert "'running'" in table_sql["speech_provider_requests"]
        assert "'verified'" in table_sql["audition_cache_records"]
        assert "actor_classification" in table_sql["audition_review_decisions"]
        assert "actor_classification" in table_sql["voice_readiness_decisions"]
        assert "'review_clip_binding'" in table_sql["audition_evidence_invalidations"]

        connection.execute(
            "INSERT INTO speech_runtime_profiles "
            "(id, profile_id, profile_version, provider_id, provider_version, "
            "runtime_id, runtime_version, protocol_version, platform, architecture, "
            "network_policy, startup_timeout_ms, request_timeout_ms, idle_shutdown_ms, "
            "maximum_concurrency, output_format_json, limits_json, profile_fingerprint, "
            "active, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "runtime-profile-row",
                "fixture-runtime-profile",
                "1.0.0",
                "deterministic-pcm-wav-fixture",
                "1.0.0",
                "python-integer-pcm",
                "1.0.0",
                "1.0.0",
                "windows",
                "x64",
                "deny_during_synthesis",
                10_000,
                60_000,
                120_000,
                1,
                '["pcm_s16le_wav"]',
                "{}",
                "f" * 64,
                1,
                '{"origin":"fixture_provider"}',
                "2026-03-09T00:00:00Z",
            ),
        )
        assert connection.execute("SELECT provider_id FROM speech_runtime_profiles").fetchone() == (
            "deterministic-pcm-wav-fixture",
        )


def test_injected_v5_failure_rolls_back_and_retains_verified_v4_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "rollback" / "studio.sqlite3"
    _create_frozen_v4(path)
    _seed_representative_v4_history(path)
    before_digest = Database._verified_v4_digest(path)
    before_snapshots = _table_snapshots(path)
    original_create_all = Base.metadata.create_all

    def fail_after_creating_tables(bind: Any, *args: Any, **kwargs: Any) -> None:
        original_create_all(bind, *args, **kwargs)
        raise RuntimeError("injected v5 migration failure")

    monkeypatch.setattr(Base.metadata, "create_all", fail_after_creating_tables)
    with pytest.raises(ServiceError) as raised:
        Database(path)

    backup = path.with_name("studio.v4-backup.sqlite3")
    assert raised.value.code == "DATABASE_UNAVAILABLE"
    assert Database._verified_v4_digest(path) == before_digest
    assert Database._verified_v4_digest(backup) == before_digest
    assert _table_snapshots(path) == before_snapshots
    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert not set(_PHASE3B_TABLES) & tables
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]


def test_corrupt_existing_v4_backup_is_rejected_before_migration(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-backup" / "studio.sqlite3"
    _create_frozen_v4(path)
    before = path.read_bytes()
    backup = path.with_name("studio.v4-backup.sqlite3")
    backup.write_bytes(b"not a sqlite database")

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_BACKUP_FAILED"
    assert path.read_bytes() == before
    assert _value(path, "PRAGMA user_version") == 4


def test_v4_backup_publish_failure_leaves_source_usable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "backup-publish-failure" / "studio.sqlite3"
    _create_frozen_v4(path)
    before_digest = Database._verified_v4_digest(path)
    original_replace = os.replace

    def fail_v4_publish(source: Any, destination: Any) -> None:
        if "v4-backup" in str(destination):
            raise OSError("injected v4 backup publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_v4_publish)
    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_BACKUP_FAILED"
    assert Database._verified_v4_digest(path) == before_digest
    assert _value(path, "PRAGMA user_version") == 4


@pytest.mark.parametrize(
    ("drift", "statement"),
    [
        ("extra-table", "CREATE TABLE unexpected_v5_table (id TEXT PRIMARY KEY)"),
        ("extra-column", "ALTER TABLE casting_runs ADD COLUMN unexpected TEXT"),
        ("missing-index", "DROP INDEX ix_casting_candidate_project_run_role_order"),
        ("ledger-gap", "DELETE FROM schema_migrations WHERE version = 4"),
        ("future", "PRAGMA user_version = 6"),
    ],
)
def test_v4_precondition_drift_is_rejected_without_backup_or_mutation(
    tmp_path: Path,
    drift: str,
    statement: str,
) -> None:
    path = tmp_path / f"v4-{drift}" / "studio.sqlite3"
    _create_frozen_v4(path)
    with sqlite3.connect(path) as connection:
        connection.execute(statement)
    before = path.read_bytes()

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert path.read_bytes() == before
    assert not path.with_name("studio.v4-backup.sqlite3").exists()


@pytest.mark.parametrize(
    ("drift", "statement"),
    [
        ("extra-object", "CREATE TABLE unexpected_phase3b_table (id TEXT PRIMARY KEY)"),
        ("extra-column", "ALTER TABLE audition_sessions ADD COLUMN unexpected TEXT"),
        ("missing-index", "DROP INDEX ix_audition_cache_project_state_verified"),
    ],
)
def test_same_version_v5_drift_is_rejected_without_repair(
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
    assert _value(path, "PRAGMA user_version") == 5


def test_recovery_is_v4_backup_only_and_forged_in_place_downgrade_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery-only" / "studio.sqlite3"
    _create_frozen_v4(path)
    database = Database(path)
    backup = database.v4_backup_path
    database.close()
    backup_digest = Database._verified_v4_digest(backup)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("PRAGMA user_version = 4")

    with pytest.raises(ServiceError) as raised:
        Database(path)

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"
    assert Database._verified_v4_digest(backup) == backup_digest


def test_phase3b_scale_queries_use_covering_indexes(tmp_path: Path) -> None:
    path = tmp_path / "query-plans" / "studio.sqlite3"
    database = Database(path)
    database.close()

    with sqlite3.connect(path) as connection:
        plans = {
            "pronunciation": connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM pronunciation_entries "
                "WHERE project_id = ? AND normalized_lookup_form = ? "
                "AND scope_type = ? AND scope_target_id = ? "
                "ORDER BY priority, id LIMIT 201",
                ("project-1", "mara", "character_role", "role-1"),
            ).fetchall(),
            "cache": connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM audition_cache_records "
                "WHERE project_id = ? AND state = ? "
                "ORDER BY last_verified_at, id LIMIT 201",
                ("project-1", "valid"),
            ).fetchall(),
            "clip": connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM audition_clips "
                "WHERE project_id = ? AND role_id = ? "
                "ORDER BY created_at, id LIMIT 201",
                ("project-1", "role-1"),
            ).fetchall(),
        }

    rendered = {name: " ".join(str(row[3]) for row in rows) for name, rows in plans.items()}
    assert "ix_pronunciation_entry_project_lookup_scope" in rendered["pronunciation"]
    assert "ix_audition_cache_project_state_verified" in rendered["cache"]
    assert "ix_audition_clip_project_role_created" in rendered["clip"]
