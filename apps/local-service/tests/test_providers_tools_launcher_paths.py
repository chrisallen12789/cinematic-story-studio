from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings
from cinematic_story_service.launcher import _is_256_bit_token, _prebound_loopback_socket
from cinematic_story_service.providers import ProviderRegistry
from cinematic_story_service.tools import FfmpegCapabilityChecker, _run_bounded_process
from cinematic_story_service.util import resolve_beneath, safe_display_filename

from .conftest import TOKEN, synthetic_fixture


class _FakeResponse:
    status = 200

    def read(self, _limit: int) -> bytes:
        return b'{"status":"ok"}'


class _FakeConnection:
    def __init__(self) -> None:
        self.request_args: tuple[Any, ...] | None = None
        self.closed = False

    def request(self, *args: Any, **kwargs: Any) -> None:
        self.request_args = (*args, kwargs)

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse()

    def close(self) -> None:
        self.closed = True


def test_provider_health_is_typed_content_free_and_cloud_disabled(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/providers/health", headers=auth_headers)
    assert response.status_code == 200
    providers = {item["providerId"]: item for item in response.json()["providers"]}
    assert providers["deterministic-story-analyzer"]["status"] == "available"
    assert providers["kokoro-docker-dev"]["status"] == "unavailable"
    assert providers["cloud-speech"]["status"] == "disabled"
    assert providers["cloud-language"]["status"] == "disabled"
    assert all(item["capabilities"] for item in providers.values())
    serialized = json.dumps(providers)
    assert "Rain traced silver lines" not in serialized
    assert "requestBody" not in serialized


def test_enabled_kokoro_uses_only_fixed_loopback_content_free_probe(tmp_path: Path) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path,
        bearer_token=TOKEN,
        kokoro_development_enabled=True,
        kokoro_development_url="http://127.0.0.1:9123/health",
    ).validated()
    connection = _FakeConnection()
    captured: dict[str, Any] = {}

    def factory(host: str, port: int, *, timeout: float) -> _FakeConnection:
        captured.update(host=host, port=port, timeout=timeout)
        return connection

    health = ProviderRegistry(settings, connection_factory=factory).health()
    kokoro = next(item for item in health if item["providerId"] == "kokoro-docker-dev")
    assert kokoro["status"] == "available"
    assert captured == {"host": "127.0.0.1", "port": 9123, "timeout": 0.25}
    assert connection.request_args is not None
    assert connection.request_args[0:2] == ("GET", "/health")
    assert connection.closed is True


def test_kokoro_absence_degrades_gracefully_and_non_loopback_is_rejected(
    tmp_path: Path,
) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "absent",
        bearer_token=TOKEN,
        kokoro_development_enabled=True,
    ).validated()

    def unavailable(*_args: Any, **_kwargs: Any) -> _FakeConnection:
        raise OSError("synthetic unavailable")

    health = ProviderRegistry(settings, connection_factory=unavailable).health()
    kokoro = next(item for item in health if item["providerId"] == "kokoro-docker-dev")
    assert kokoro["status"] == "unavailable"
    assert "synthetic" not in json.dumps(kokoro)

    with pytest.raises(ValueError, match="loopback"):
        ServiceSettings(
            data_dir=tmp_path / "unsafe",
            bearer_token=TOKEN,
            kokoro_development_enabled=True,
            kokoro_development_url="http://example.test:8880/health",
        ).validated()


def test_ffmpeg_probe_uses_argv_without_shell_and_parses_version(tmp_path: Path) -> None:
    executable = tmp_path / "ffmpeg synthetic.exe"
    executable.write_bytes(b"synthetic")
    captured: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="ffmpeg version 7.1.2-synthetic Copyright",
            stderr="",
        )

    settings = ServiceSettings(
        data_dir=tmp_path / "data",
        bearer_token=TOKEN,
        ffmpeg_executable=str(executable.resolve()),
    ).validated()
    result = FfmpegCapabilityChecker(settings, process_runner=runner).check()
    assert result["status"] == "available"
    assert result["version"] == "7.1.2-synthetic"
    assert captured["argv"] == [str(executable.resolve()), "-hide_banner", "-version"]
    assert captured["kwargs"]["shell"] is False
    assert isinstance(captured["argv"], list)


def test_ffmpeg_missing_is_nonfatal_and_does_not_invoke_process(tmp_path: Path) -> None:
    called = False

    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("must not run")

    settings = ServiceSettings(
        data_dir=tmp_path / "data",
        bearer_token=TOKEN,
        ffmpeg_executable=str((tmp_path / "missing.exe").resolve()),
    ).validated()
    result = FfmpegCapabilityChecker(settings, process_runner=runner).check()
    assert result["status"] == "missing"
    assert result["executableOrigin"] == "configured"
    assert called is False


def test_ffmpeg_path_lookup_is_disabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_lookup(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("production composition must not search PATH")

    monkeypatch.setattr(shutil, "which", unexpected_lookup)
    settings = ServiceSettings(data_dir=tmp_path / "data", bearer_token=TOKEN).validated()
    result = FfmpegCapabilityChecker(settings).check()
    assert settings.allow_ffmpeg_path_lookup is False
    assert result["status"] == "missing"
    assert result["executableOrigin"] == "none"


def test_ffmpeg_probe_retains_bounded_output(tmp_path: Path) -> None:
    environment = {
        key: value for key in ("SystemRoot", "WINDIR") if (value := os.environ.get(key)) is not None
    }
    completed = _run_bounded_process(
        [
            sys.executable,
            "-c",
            ("import sys;sys.stdout.write('x'*200000);sys.stderr.write('y'*200000)"),
        ],
        cwd=tmp_path,
        environment=environment,
        timeout=5,
    )
    assert completed.returncode == 0
    assert len(completed.stdout.encode()) <= 8192
    assert len(completed.stderr.encode()) <= 8192


def test_path_helpers_reject_traversal_absolute_and_metacharacters(tmp_path: Path) -> None:
    assert safe_display_filename("chapter one.md") == "chapter one.md"
    for unsafe in ("../x.md", "..\\x.md", "C:\\x.md", "/x.md", "x;rm.md", "x|y.md"):
        with pytest.raises(ValueError):
            safe_display_filename(unsafe)
    with pytest.raises(ValueError):
        resolve_beneath(tmp_path / "root", "../escape")
    with pytest.raises(ValueError):
        resolve_beneath(tmp_path / "root", Path(tmp_path.anchor) / "escape")


def test_launcher_bootstrap_ready_line_and_eof_shutdown(tmp_path: Path) -> None:
    nonce = "synthetic-nonce"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cinematic_story_service.launcher",
            "--data-dir",
            str(tmp_path / "launcher-data"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(
        json.dumps({"token": TOKEN, "nonce": nonce, "protocolVersion": "1.0.0"}) + "\n",
        timeout=15,
    )
    assert process.returncode == 0, stderr
    lines = stdout.splitlines()
    assert len(lines) == 1
    prefix, payload = lines[0].split(" ", 1)
    assert prefix == "CSS_READY"
    ready = json.loads(payload)
    assert ready["nonce"] == nonce
    assert ready["protocolVersion"] == "1.0.0"
    assert isinstance(ready["port"], int) and ready["port"] > 0
    assert ready["instanceId"]
    assert TOKEN not in stdout
    assert TOKEN not in stderr


def test_launcher_trailing_control_data_shuts_down_with_stdin_open(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cinematic_story_service.launcher",
            "--data-dir",
            str(tmp_path / "trailing-control-data"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stderr = ""
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(
            json.dumps(
                {
                    "token": TOKEN,
                    "nonce": "trailing-control-nonce",
                    "protocolVersion": "1.0.0",
                }
            )
            + "\n\x01"
        )
        process.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready_line = pool.submit(process.stdout.readline).result(timeout=15)
        assert ready_line.startswith("CSS_READY ")
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.stderr is not None:
            stderr = process.stderr.read()
            process.stderr.close()
        if process.stdout is not None:
            process.stdout.close()
    assert process.returncode == 0, stderr
    assert TOKEN not in stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spawn/control-pipe regression")
def test_launcher_control_monitor_allows_spawned_docx_parser(tmp_path: Path) -> None:
    nonce = "spawned-parser-nonce"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cinematic_story_service.launcher",
            "--data-dir",
            str(tmp_path / "spawned-parser-launcher-data"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stderr = ""
    terminal_job: dict[str, Any] | None = None
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(
            json.dumps(
                {
                    "token": TOKEN,
                    "nonce": nonce,
                    "protocolVersion": "1.0.0",
                }
            )
            + "\n"
        )
        process.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready_line = pool.submit(process.stdout.readline).result(timeout=15)
        assert ready_line.startswith("CSS_READY ")
        ready = json.loads(ready_line.removeprefix("CSS_READY "))
        assert ready["nonce"] == nonce

        filename, content, media_type = synthetic_fixture("docx")
        with httpx.Client(
            base_url=f"http://127.0.0.1:{ready['port']}",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=10,
        ) as client:
            project_response = client.post(
                "/api/v1/projects",
                headers={"Idempotency-Key": "spawned-parser-project"},
                json={"name": "Spawned parser regression"},
            )
            assert project_response.status_code == 200, project_response.text
            project_id = project_response.json()["project"]["projectId"]
            import_response = client.post(
                f"/api/v1/projects/{project_id}/imports",
                headers={"Idempotency-Key": "spawned-parser-import"},
                data={"declaredFormat": "docx"},
                files={"file": (filename, content, media_type)},
            )
            assert import_response.status_code == 202, import_response.text
            job_id = import_response.json()["job"]["jobId"]

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                terminal_job = client.get(f"/api/v1/jobs/{job_id}").json()["job"]
                if terminal_job["state"] in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    break
                time.sleep(0.05)
        assert terminal_job is not None
        assert terminal_job["state"] == "succeeded", terminal_job
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stderr is not None:
            stderr = process.stderr.read()
            process.stderr.close()
        if process.stdout is not None:
            process.stdout.close()
    assert process.returncode == 0, stderr
    assert TOKEN not in stderr


def test_launcher_rejects_short_token_without_echoing_it(tmp_path: Path) -> None:
    token = "short-secret"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "cinematic_story_service.launcher",
            "--data-dir",
            str(tmp_path / "invalid-launcher-data"),
        ],
        input=json.dumps(
            {"token": token, "nonce": "nonce", "protocolVersion": "1.0.0"}
        )
        + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 2
    assert "invalid_bootstrap" in process.stderr
    assert token not in process.stdout
    assert token not in process.stderr
    assert _is_256_bit_token(TOKEN) is True
    assert _is_256_bit_token(token) is False


def test_launcher_rejects_protocol_mismatch_before_storage_access(tmp_path: Path) -> None:
    data_dir = tmp_path / "must-not-be-created"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "cinematic_story_service.launcher",
            "--data-dir",
            str(data_dir),
        ],
        input=json.dumps(
            {"token": TOKEN, "nonce": "nonce", "protocolVersion": "2.0.0"}
        )
        + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 2
    assert process.stdout == ""
    assert process.stderr.strip() == "CSS_ERROR incompatible_protocol"
    assert not data_dir.exists()


def test_second_launcher_fails_with_redacted_storage_lock_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "shared-launcher-data"
    bootstrap = (
        json.dumps(
            {
                "token": TOKEN,
                "nonce": "first-nonce",
                "protocolVersion": "1.0.0",
            }
        )
        + "\n"
    )
    first = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cinematic_story_service.launcher",
            "--data-dir",
            str(data_dir),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert first.stdin is not None
        assert first.stdout is not None
        first.stdin.write(bootstrap)
        first.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready_line = pool.submit(first.stdout.readline).result(timeout=15)
        assert ready_line.startswith("CSS_READY ")

        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "cinematic_story_service.launcher",
                "--data-dir",
                str(data_dir),
            ],
            input=json.dumps(
                {
                    "token": TOKEN,
                    "nonce": "second-nonce",
                    "protocolVersion": "1.0.0",
                }
            )
            + "\n",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert second.returncode == 1
        assert second.stdout == ""
        assert second.stderr.strip() == "CSS_ERROR storage_locked"
        assert str(data_dir) not in second.stderr
        assert TOKEN not in second.stderr
    finally:
        if first.stdin is not None and not first.stdin.closed:
            first.stdin.close()
        try:
            first.wait(timeout=15)
        except subprocess.TimeoutExpired:
            first.kill()
            first.wait(timeout=5)
        if first.stdout is not None:
            first.stdout.close()
        if first.stderr is not None:
            first.stderr.close()
    assert first.returncode == 0


def test_launcher_rejects_incompatible_schema_with_fixed_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "incompatible-schema-data"
    data_dir.mkdir()
    database_path = data_dir / "cinematic-story-studio.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 2")

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "cinematic_story_service.launcher",
            "--data-dir",
            str(data_dir),
        ],
        input=json.dumps(
            {"token": TOKEN, "nonce": "nonce", "protocolVersion": "1.0.0"}
        )
        + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 1
    assert process.stdout == ""
    assert process.stderr.strip() == "CSS_ERROR incompatible_schema"
    assert str(data_dir) not in process.stderr
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
            ).fetchone()
            is None
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows socket hardening")
def test_launcher_socket_uses_exclusive_address_on_windows() -> None:
    listener = _prebound_loopback_socket()
    try:
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
    finally:
        listener.close()
