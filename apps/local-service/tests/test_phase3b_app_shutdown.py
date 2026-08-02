from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app


def test_lifespan_keeps_database_open_when_worker_cannot_quiesce_after_runtime_shutdown(
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(replace(settings, worker_enabled=True))
    events: list[str] = []
    original_database_close = app.state.database.close

    monkeypatch.setattr(app.state.worker, "start", lambda: events.append("worker-start"))

    def fail_worker_stop() -> None:
        events.append("worker-stop")
        raise RuntimeError("repository-owned worker stop failure")

    def shutdown_runtimes() -> tuple[object, ...]:
        events.append("runtime-shutdown")
        return ()

    def close_database() -> None:
        events.append("database-close")

    monkeypatch.setattr(app.state.worker, "stop", fail_worker_stop)
    monkeypatch.setattr(
        app.state.auditions,
        "begin_runtime_shutdown",
        lambda: events.append("runtime-admission-sealed"),
    )
    monkeypatch.setattr(app.state.auditions, "shutdown_runtimes", shutdown_runtimes)
    monkeypatch.setattr(app.state.database, "close", close_database)
    try:
        with pytest.raises(RuntimeError, match="repository-owned worker stop failure"):
            with TestClient(app):
                pass
    finally:
        original_database_close()

    assert events == [
        "worker-start",
        "runtime-admission-sealed",
        "worker-stop",
        "runtime-shutdown",
        "worker-stop",
    ]


def test_lifespan_retries_worker_drain_after_runtime_shutdown_before_database_close(
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(replace(settings, worker_enabled=True))
    events: list[str] = []
    stop_calls = 0
    original_database_close = app.state.database.close

    monkeypatch.setattr(app.state.worker, "start", lambda: events.append("worker-start"))

    def stop_worker() -> None:
        nonlocal stop_calls
        stop_calls += 1
        events.append(f"worker-stop-{stop_calls}")
        if stop_calls == 1:
            raise RuntimeError("initial worker drain timeout")

    monkeypatch.setattr(app.state.worker, "stop", stop_worker)
    monkeypatch.setattr(
        app.state.auditions,
        "begin_runtime_shutdown",
        lambda: events.append("runtime-admission-sealed"),
    )
    monkeypatch.setattr(
        app.state.auditions,
        "shutdown_runtimes",
        lambda: events.append("runtime-shutdown") or (),
    )
    monkeypatch.setattr(app.state.database, "close", lambda: events.append("database-close"))
    try:
        with TestClient(app):
            pass
    finally:
        original_database_close()

    assert events == [
        "worker-start",
        "runtime-admission-sealed",
        "worker-stop-1",
        "runtime-shutdown",
        "worker-stop-2",
        "runtime-shutdown",
        "database-close",
    ]


@pytest.mark.parametrize("final_runtime_shutdown_succeeds", [True, False])
def test_lifespan_requires_final_runtime_exit_pass_before_database_close(
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
    final_runtime_shutdown_succeeds: bool,
) -> None:
    app = create_app(replace(settings, worker_enabled=False))
    events: list[str] = []
    shutdown_calls = 0
    original_database_close = app.state.database.close

    def shutdown_runtimes() -> tuple[object, ...]:
        nonlocal shutdown_calls
        shutdown_calls += 1
        events.append(f"runtime-shutdown-{shutdown_calls}")
        if shutdown_calls == 1 or not final_runtime_shutdown_succeeds:
            raise RuntimeError(f"runtime shutdown incomplete {shutdown_calls}")
        return ()

    monkeypatch.setattr(
        app.state.auditions,
        "begin_runtime_shutdown",
        lambda: events.append("runtime-admission-sealed"),
    )
    monkeypatch.setattr(app.state.auditions, "shutdown_runtimes", shutdown_runtimes)
    monkeypatch.setattr(app.state.database, "close", lambda: events.append("database-close"))
    try:
        if final_runtime_shutdown_succeeds:
            with TestClient(app):
                pass
        else:
            with pytest.raises(RuntimeError, match="runtime shutdown incomplete 2"):
                with TestClient(app):
                    pass
    finally:
        original_database_close()

    expected = [
        "runtime-admission-sealed",
        "runtime-shutdown-1",
        "runtime-shutdown-2",
    ]
    if final_runtime_shutdown_succeeds:
        expected.append("database-close")
    assert events == expected
