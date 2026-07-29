from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import ServiceSettings
from .util import utc_now

_VERSION = re.compile(r"(?i)\bffmpeg version[ \t]+([^\s]+)")
_REQUIRED_CAPABILITIES = ["decode_audio", "encode_pcm_wav"]
_MAX_PROBE_STREAM_BYTES = 8192

RunProcess = Callable[..., subprocess.CompletedProcess[str]]


class FfmpegCapabilityChecker:
    def __init__(
        self,
        settings: ServiceSettings,
        *,
        process_runner: RunProcess | None = None,
    ) -> None:
        self.settings = settings
        self.process_runner = process_runner

    def check(self) -> dict[str, Any]:
        executable, origin = self._resolve_executable()
        checked_at = utc_now()
        if executable is None:
            return {
                "status": "missing",
                "executableOrigin": origin,
                "capabilities": [],
                "missingCapabilities": _REQUIRED_CAPABILITIES,
                "redactedReason": "FFmpeg was not found in an approved location.",
                "checkedAt": checked_at,
            }

        argv = [executable, "-hide_banner", "-version"]
        environment = {
            key: value
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
            if (value := os.environ.get(key)) is not None
        }
        try:
            if self.process_runner is None:
                completed = _run_bounded_process(
                    argv,
                    cwd=self.settings.data_dir,
                    environment=environment,
                    timeout=3,
                )
            else:
                # Tests may inject a non-executing runner to inspect the exact subprocess contract.
                completed = self.process_runner(
                    argv,
                    shell=False,
                    cwd=self.settings.data_dir,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3,
                    check=False,
                )
        except FileNotFoundError:
            return {
                "status": "missing",
                "executableOrigin": origin,
                "capabilities": [],
                "missingCapabilities": _REQUIRED_CAPABILITIES,
                "redactedReason": "FFmpeg was not found in an approved location.",
                "checkedAt": checked_at,
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "failed",
                "executableOrigin": origin,
                "capabilities": [],
                "missingCapabilities": _REQUIRED_CAPABILITIES,
                "redactedReason": "FFmpeg did not answer the bounded capability probe.",
                "checkedAt": checked_at,
            }
        except OSError:
            return {
                "status": "failed",
                "executableOrigin": origin,
                "capabilities": [],
                "missingCapabilities": _REQUIRED_CAPABILITIES,
                "redactedReason": "FFmpeg could not be started.",
                "checkedAt": checked_at,
            }

        bounded_output = f"{completed.stdout}\n{completed.stderr}"[: 2 * _MAX_PROBE_STREAM_BYTES]
        match = _VERSION.search(bounded_output)
        if completed.returncode != 0:
            return {
                "status": "failed",
                "executableOrigin": origin,
                "capabilities": [],
                "missingCapabilities": _REQUIRED_CAPABILITIES,
                "redactedReason": "FFmpeg returned a capability-probe error.",
                "checkedAt": checked_at,
            }
        if match is None:
            return {
                "status": "incompatible",
                "executableOrigin": origin,
                "capabilities": [],
                "missingCapabilities": _REQUIRED_CAPABILITIES,
                "redactedReason": "The executable did not report a compatible FFmpeg version.",
                "checkedAt": checked_at,
            }
        return {
            "status": "available",
            "executableOrigin": origin,
            "version": match.group(1)[:80],
            "capabilities": _REQUIRED_CAPABILITIES,
            "missingCapabilities": [],
            "checkedAt": checked_at,
        }

    def _resolve_executable(self) -> tuple[str | None, str]:
        if self.settings.ffmpeg_executable is not None:
            configured = Path(self.settings.ffmpeg_executable)
            if not configured.is_absolute():
                return None, "configured"
            resolved = configured.resolve(strict=False)
            if not resolved.is_file():
                return None, "configured"
            return str(resolved), "configured"
        if not self.settings.allow_ffmpeg_path_lookup:
            return None, "none"
        path_value = shutil.which("ffmpeg", path=os.environ.get("PATH", ""))
        if path_value is None:
            return None, "none"
        return str(Path(path_value).resolve(strict=False)), "path_lookup"


def _run_bounded_process(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run one owned capability probe while retaining at most 8 KiB per output stream."""

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        argv,
        shell=False,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()

    def drain(stream: Any, destination: bytearray) -> None:
        try:
            while chunk := stream.read(4096):
                remaining = _MAX_PROBE_STREAM_BYTES - len(destination)
                if remaining > 0:
                    destination.extend(chunk[:remaining])
        except (OSError, ValueError):
            return

    assert process.stdout is not None
    assert process.stderr is not None
    readers = [
        threading.Thread(
            target=drain,
            args=(process.stdout, stdout_buffer),
            name="ffmpeg-probe-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, stderr_buffer),
            name="ffmpeg-probe-stderr",
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.5)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=0.5)
        process.stdout.close()
        process.stderr.close()

    return subprocess.CompletedProcess(
        argv,
        return_code,
        stdout=stdout_buffer.decode("utf-8", errors="replace"),
        stderr=stderr_buffer.decode("utf-8", errors="replace"),
    )
