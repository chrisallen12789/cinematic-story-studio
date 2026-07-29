from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from .errors import ServiceError
from .models import Base
from .util import SERVICE_VERSION, ensure_private_directory, utc_now

_DATABASE_SCHEMA_VERSION = 1
_SCHEMA_LEDGER_TABLE = "schema_migrations"
_STORAGE_LOCK_FILENAME = ".cinematic-story-studio.lock"


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
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = ?",
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
                if current_version not in {0, _DATABASE_SCHEMA_VERSION}:
                    raise _unsupported_schema_error()
                if current_version == 0 and user_tables:
                    raise _unsupported_schema_error()
                if current_version == _DATABASE_SCHEMA_VERSION:
                    self._validate_schema_ledger(connection, ledger_exists)

                # End SQLAlchemy's read-only autobegin before changing journal mode.
                connection.rollback()
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                connection.commit()

                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    Base.metadata.create_all(connection)
                    if current_version == 0:
                        connection.exec_driver_sql(
                            "CREATE TABLE schema_migrations ("
                            "version INTEGER PRIMARY KEY, "
                            "applied_at TEXT NOT NULL, "
                            "service_version TEXT NOT NULL"
                            ")"
                        )
                        connection.exec_driver_sql(
                            "INSERT INTO schema_migrations "
                            "(version, applied_at, service_version) VALUES (?, ?, ?)",
                            (_DATABASE_SCHEMA_VERSION, utc_now(), SERVICE_VERSION),
                        )
                        connection.exec_driver_sql(
                            f"PRAGMA user_version = {_DATABASE_SCHEMA_VERSION}"
                        )
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
        except ServiceError:
            raise
        except Exception as exc:
            raise _database_unavailable_error() from exc

    @staticmethod
    def _validate_schema_ledger(connection: Connection, ledger_exists: bool) -> None:
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
        if versions != [_DATABASE_SCHEMA_VERSION]:
            raise _unsupported_schema_error()

    def _verify(self) -> None:
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text("PRAGMA quick_check")).scalar_one()
        except Exception as exc:
            raise _database_unavailable_error() from exc
        if result != "ok":
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

    def close(self) -> None:
        try:
            self.engine.dispose()
        finally:
            self._data_lock.close()
