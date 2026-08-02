from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal
from uuid import UUID, uuid4

from .local_speech import (
    MAX_SPEECH_AUDIO_BYTES,
    SpeechArtifact,
    SpeechInvocationContext,
    SpeechPronunciationOverrideSpan,
    SpeechProviderError,
    SpeechSynthesisRequest,
)
from .util import canonical_json

SPEECH_RUNTIME_PROTOCOL_VERSION = "1.0.0"
MAX_SPEECH_RUNTIME_FRAME_BYTES = 48 * 1024 * 1024
MAX_SPEECH_RUNTIME_BOOTSTRAP_BYTES = 16 * 1024
MIN_SPEECH_RUNTIME_SECRET_BYTES = 32
MAX_SPEECH_RUNTIME_RETRIES = 2
MIN_RUNTIME_TIMEOUT_SECONDS = 0.05
MAX_RUNTIME_TIMEOUT_SECONDS = 10 * 60
MAX_SPEECH_RUNTIME_PROCESS_MEMORY_BYTES = 1024 * 1024 * 1024
_PROCESS_EXIT_GRACE_SECONDS = 5.0
_FROZEN_WORKER_FLAG = "--speech-runtime-worker"
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_JOB_OBJECT_ASSIGN_PROCESS = 0x0001
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_STILL_ACTIVE = 259


@dataclass(slots=True)
class SpeechRuntimeError(Exception):
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


@dataclass(frozen=True, slots=True)
class SpeechRuntimeConfig:
    provider_id: str
    runtime_id: str
    runtime_version: str
    model_id: str
    model_version: str
    model_manifest_fingerprint: str
    model_package_path: Path | None = None
    python_executable: Path = Path(sys.executable)
    startup_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 60.0
    idle_timeout_seconds: float = 120.0
    max_retries: int = 1
    launch_mode: Literal["auto", "python-module", "frozen-executable"] = "auto"

    def __post_init__(self) -> None:
        for value in (
            self.provider_id,
            self.runtime_id,
            self.runtime_version,
            self.model_id,
            self.model_version,
        ):
            if not value or len(value) > 160 or any(character.isspace() for character in value):
                raise ValueError("Runtime identities must be bounded and whitespace-free.")
        if len(self.model_manifest_fingerprint) != 64 or any(
            value not in "0123456789abcdef" for value in self.model_manifest_fingerprint
        ):
            raise ValueError("A lowercase model-manifest SHA-256 is required.")
        for timeout in (
            self.startup_timeout_seconds,
            self.request_timeout_seconds,
            self.idle_timeout_seconds,
        ):
            if not MIN_RUNTIME_TIMEOUT_SECONDS <= timeout <= MAX_RUNTIME_TIMEOUT_SECONDS:
                raise ValueError("Runtime timeouts are outside their fixed bounds.")
        if not 0 <= self.max_retries <= MAX_SPEECH_RUNTIME_RETRIES:
            raise ValueError("Runtime retries are outside their fixed bound.")
        if self.launch_mode not in {"auto", "python-module", "frozen-executable"}:
            raise ValueError("The speech runtime launch mode was invalid.")
        executable = self.python_executable.absolute()
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("The runtime executable must be an explicit existing file.")
        if self.model_package_path is not None and not self.model_package_path.absolute().is_dir():
            raise ValueError("The configured model package path must be an existing directory.")


@dataclass(frozen=True, slots=True)
class SpeechWorkerIdentity:
    pid: int
    parent_pid: int
    launcher_pid: int
    process_parent_pid: int
    executable: Path
    created_at_unix_ns: int
    creation_nonce: str
    protocol_version: str
    provider_id: str
    runtime_id: str
    runtime_version: str
    model_id: str
    model_version: str
    model_manifest_fingerprint: str
    launch_mode: Literal["python-module", "frozen-executable"]
    ownership_job_name: str
    job_object_assigned: bool
    denied_network_attempt_count: int


@dataclass(frozen=True, slots=True)
class SpeechRuntimeExitEvidence:
    pid: int
    launcher_pid: int
    exit_code: int | None
    reason: Literal["clean", "idle", "deadline", "protocol_error", "process_error"]
    ownership_confirmed: bool
    shutdown_acknowledged: bool
    graceful_shutdown_confirmed: bool
    terminated_by_parent: bool
    confirmed_exited: bool
    job_object_assigned: bool
    owned_processes_confirmed_exited: bool
    denied_network_attempt_count: int


_ReaderItem = bytes | BaseException | None


def _network_attempt_count(payload: dict[str, Any]) -> int:
    value = payload.get("deniedNetworkAttemptCount")
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1_000_000:
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "The speech worker returned an invalid network-denial count.",
            retryable=True,
        )
    return value


def _runtime_error_exit_reason(
    error: SpeechRuntimeError,
) -> Literal["deadline", "protocol_error", "process_error"]:
    if error.code == "SPEECH_WORKER_DEADLINE_EXCEEDED":
        return "deadline"
    if error.code in {
        "SPEECH_WORKER_AUTHENTICATION_FAILED",
        "SPEECH_WORKER_IDENTITY_INVALID",
        "SPEECH_WORKER_NETWORK_ATTEMPT_DENIED",
        "SPEECH_WORKER_OWNERSHIP_INVALID",
        "SPEECH_WORKER_PROTOCOL_INVALID",
    }:
        return "protocol_error"
    return "process_error"


class ManagedSpeechRuntime:
    """Own one authenticated stdin/stdout speech worker and its exact process handle."""

    def __init__(self, config: SpeechRuntimeConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._messages: queue.Queue[_ReaderItem] | None = None
        self._secret: bytes | None = None
        self._sequence = 0
        self._identity: SpeechWorkerIdentity | None = None
        self._last_identity: SpeechWorkerIdentity | None = None
        self._last_used_monotonic: float | None = None
        self._last_exit: SpeechRuntimeExitEvidence | None = None
        self._job: _WindowsSpeechJobObject | None = None
        self._denied_network_attempt_count: int | None = None

    @property
    def identity(self) -> SpeechWorkerIdentity | None:
        with self._lock:
            return self._identity

    @property
    def last_exit(self) -> SpeechRuntimeExitEvidence | None:
        with self._lock:
            return self._last_exit

    @property
    def last_identity(self) -> SpeechWorkerIdentity | None:
        """Return the last authenticated identity, including after exact exit."""

        with self._lock:
            return self._last_identity

    @property
    def has_owned_process_handle(self) -> bool:
        """Report retained process-tree ownership without probing any external PID."""

        with self._lock:
            return self._process is not None or self._job is not None

    @property
    def denied_network_attempt_count(self) -> int | None:
        with self._lock:
            if self._denied_network_attempt_count is not None:
                return self._denied_network_attempt_count
            if self._last_exit is not None:
                return self._last_exit.denied_network_attempt_count
            return None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self) -> SpeechWorkerIdentity:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                if self._last_exit is not None:
                    raise SpeechRuntimeError(
                        "SPEECH_WORKER_TERMINATION_PENDING",
                        "The prior managed speech worker is awaiting verified termination.",
                        retryable=True,
                    )
                if self._identity is None:
                    raise SpeechRuntimeError(
                        "SPEECH_WORKER_OWNERSHIP_INVALID",
                        "The speech worker did not establish its identity.",
                    )
                return self._identity
            if self._process is not None:
                self.reap_if_idle()
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_EXITED",
                    "The prior managed speech worker exited before reuse.",
                    retryable=True,
                )
            self._clear_process_state()
            self._last_exit = None
            self._last_identity = None
            secret = secrets.token_bytes(MIN_SPEECH_RUNTIME_SECRET_BYTES)
            creation_nonce = secrets.token_hex(32)
            launch_started_ns = time.time_ns()
            launch_mode = _resolve_launch_mode(self.config.launch_mode)
            try:
                job = _WindowsSpeechJobObject(
                    memory_limit_bytes=MAX_SPEECH_RUNTIME_PROCESS_MEMORY_BYTES
                )
            except OSError as exc:
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_OWNERSHIP_INVALID",
                    "The speech worker process-tree owner could not be created.",
                    retryable=True,
                ) from exc
            self._job = job
            argv = self._worker_argv(
                launch_mode=launch_mode,
                ownership_job_name=job.name,
            )
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            try:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    env=_runtime_environment(),
                    shell=False,
                    close_fds=True,
                    bufsize=0,
                    creationflags=creation_flags,
                )
            except OSError as exc:
                job.close()
                self._job = None
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_START_FAILED",
                    "The managed speech worker could not be started.",
                    retryable=True,
                ) from exc
            self._process = process
            try:
                job.assign_launcher(process.pid)
            except OSError as exc:
                self._terminate_owned("process_error")
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_OWNERSHIP_INVALID",
                    "The speech worker launcher could not be placed in its owned process tree.",
                    retryable=True,
                ) from exc
            self._secret = secret
            self._messages = queue.Queue()
            assert process.stdout is not None
            self._reader = threading.Thread(
                target=_reader_loop,
                args=(process.stdout, self._messages),
                name=f"speech-worker-{process.pid}-stdout",
                daemon=True,
            )
            self._reader.start()
            bootstrap = {
                "creationNonce": creation_nonce,
                "idleTimeoutMilliseconds": round(self.config.idle_timeout_seconds * 1_000),
                "modelId": self.config.model_id,
                "modelManifestFingerprint": self.config.model_manifest_fingerprint,
                "modelPackagePath": (
                    str(self.config.model_package_path.absolute())
                    if self.config.model_package_path is not None
                    else None
                ),
                "modelVersion": self.config.model_version,
                "launchMode": launch_mode,
                "ownershipJobName": job.name,
                "protocolVersion": SPEECH_RUNTIME_PROTOCOL_VERSION,
                "providerId": self.config.provider_id,
                "runtimeId": self.config.runtime_id,
                "runtimeVersion": self.config.runtime_version,
                "secret": base64.b64encode(secret).decode("ascii"),
            }
            try:
                _write_plain_frame(process, bootstrap, MAX_SPEECH_RUNTIME_BOOTSTRAP_BYTES)
                frame = self._await_frame(self.config.startup_timeout_seconds)
                message_type, sequence, payload = decode_authenticated_frame(secret, frame)
                if message_type != "ready" or sequence != 0:
                    raise SpeechRuntimeError(
                        "SPEECH_WORKER_PROTOCOL_INVALID",
                        "The speech worker returned an invalid ready frame.",
                    )
                identity = self._validate_identity(
                    process,
                    payload,
                    creation_nonce=creation_nonce,
                    launch_started_ns=launch_started_ns,
                )
            except Exception as exc:
                exit_reason = (
                    _runtime_error_exit_reason(exc)
                    if isinstance(exc, SpeechRuntimeError)
                    else "protocol_error"
                )
                self._terminate_owned(exit_reason)
                if isinstance(exc, SpeechRuntimeError):
                    raise
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_PROTOCOL_INVALID",
                    "The speech worker did not complete an authenticated handshake.",
                    retryable=True,
                ) from exc
            self._identity = identity
            self._last_identity = identity
            self._denied_network_attempt_count = identity.denied_network_attempt_count
            self._last_used_monotonic = time.monotonic()
            return identity

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
        *,
        on_dispatch_committed: Callable[[], None] | None = None,
        expected_identity: SpeechWorkerIdentity | None = None,
    ) -> SpeechArtifact:
        with self._lock:
            if on_dispatch_committed is not None and self.config.max_retries != 0:
                raise ValueError("Durable speech dispatches cannot use internal retries.")
            attempts = 0
            while True:
                attempts += 1
                try:
                    return self._synthesize_once(
                        request,
                        context,
                        on_dispatch_committed=on_dispatch_committed,
                        expected_identity=expected_identity,
                    )
                except SpeechRuntimeError as exc:
                    self._terminate_owned(_runtime_error_exit_reason(exc))
                    if (
                        not exc.retryable
                        or attempts >= self.config.max_retries + 1
                        or context.remaining_seconds() <= MIN_RUNTIME_TIMEOUT_SECONDS
                    ):
                        raise

    def reap_if_idle(self, *, now_monotonic: float | None = None) -> bool:
        with self._lock:
            if self._process is None:
                return False
            if self._process.poll() is not None:
                if self._observe_natural_idle_exit() is None:
                    self._terminate_owned("process_error")
                return True
            now = time.monotonic() if now_monotonic is None else now_monotonic
            if (
                self._last_used_monotonic is None
                or now - self._last_used_monotonic < self.config.idle_timeout_seconds
            ):
                return False
            self.stop(reason="idle")
            return True

    def stop(
        self,
        *,
        reason: Literal["clean", "idle"] = "clean",
    ) -> SpeechRuntimeExitEvidence | None:
        with self._lock:
            process = self._process
            if process is None:
                return self._last_exit
            if self._last_exit is not None and not (
                self._last_exit.confirmed_exited
                and self._last_exit.owned_processes_confirmed_exited
            ):
                prior_reason = self._last_exit.reason
                if prior_reason == "deadline":
                    retry_reason: Literal["deadline", "protocol_error", "process_error"] = (
                        "deadline"
                    )
                elif prior_reason == "protocol_error":
                    retry_reason = "protocol_error"
                else:
                    retry_reason = "process_error"
                return self._terminate_owned(retry_reason)
            if process.poll() is not None:
                observed_idle = self._observe_natural_idle_exit()
                if observed_idle is not None:
                    return observed_idle
                return self._terminate_owned("process_error")
            shutdown_acknowledged = False
            acknowledged_reason: Literal["clean", "idle"] = reason
            if process.poll() is None and self._secret is not None and self._identity is not None:
                self._sequence += 1
                try:
                    self._write_authenticated("shutdown", self._sequence, {"reason": reason})
                    frame = self._await_frame(_PROCESS_EXIT_GRACE_SECONDS)
                    message_type, sequence, payload = decode_authenticated_frame(
                        self._secret,
                        frame,
                    )
                    if (
                        message_type != "stopped"
                        or sequence != self._sequence
                        or set(payload) != {"deniedNetworkAttemptCount", "reason"}
                        or not (
                            payload.get("reason") == reason
                            or (reason == "clean" and payload.get("reason") == "idle")
                        )
                    ):
                        raise SpeechRuntimeError(
                            "SPEECH_WORKER_PROTOCOL_INVALID",
                            "The speech worker returned an invalid shutdown acknowledgement.",
                        )
                    if payload.get("reason") == "idle":
                        acknowledged_reason = "idle"
                    self._denied_network_attempt_count = _network_attempt_count(payload)
                    shutdown_acknowledged = True
                except (SpeechRuntimeError, OSError) as exc:
                    if reason == "clean" and (
                        isinstance(exc, OSError)
                        or (
                            isinstance(exc, SpeechRuntimeError)
                            and exc.code == "SPEECH_WORKER_EXITED"
                        )
                    ):
                        observed_idle = self._observe_natural_idle_exit(
                            expected_sequence=self._sequence,
                        )
                        if observed_idle is not None:
                            return observed_idle
                    if reason == "idle":
                        observed_idle = self._observe_natural_idle_exit(
                            expected_sequence=self._sequence,
                        )
                        if observed_idle is not None:
                            return observed_idle
                    return self._terminate_owned("protocol_error")
            assert process.stdin is not None
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=_PROCESS_EXIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                return self._terminate_owned("process_error")
            identity = self._identity
            job = self._job
            owned_processes_exited = job is None or job.wait_empty(_PROCESS_EXIT_GRACE_SECONDS)
            if not owned_processes_exited:
                return self._terminate_owned("process_error")
            ownership_confirmed = (
                identity is not None
                and identity.launcher_pid == process.pid
                and (job is None or job.target_confirmed)
            )
            job_object_requirement_satisfied = job is None or (
                job.required and job.launcher_assigned
            )
            job_object_assigned = job is not None and job.required and job.launcher_assigned
            confirmed_exited = process.poll() is not None
            denied_network_attempt_count = self._denied_network_attempt_count or 0
            graceful_shutdown_confirmed = (
                shutdown_acknowledged
                and process.returncode == 0
                and ownership_confirmed
                and job_object_requirement_satisfied
                and confirmed_exited
                and owned_processes_exited
                and denied_network_attempt_count == 0
            )
            evidence = SpeechRuntimeExitEvidence(
                pid=identity.pid if identity is not None else process.pid,
                launcher_pid=process.pid,
                exit_code=process.returncode,
                reason=acknowledged_reason if graceful_shutdown_confirmed else "process_error",
                ownership_confirmed=ownership_confirmed,
                shutdown_acknowledged=shutdown_acknowledged,
                graceful_shutdown_confirmed=graceful_shutdown_confirmed,
                terminated_by_parent=False,
                confirmed_exited=confirmed_exited,
                job_object_assigned=job_object_assigned,
                owned_processes_confirmed_exited=owned_processes_exited,
                denied_network_attempt_count=denied_network_attempt_count,
            )
            self._last_exit = evidence
            if confirmed_exited and owned_processes_exited:
                self._finalize_exited_process(process)
            return evidence

    def _observe_natural_idle_exit(
        self,
        *,
        expected_sequence: int | None = None,
    ) -> SpeechRuntimeExitEvidence | None:
        """Authenticate and finalize the worker's configured natural idle exit."""

        process = self._process
        secret = self._secret
        identity = self._identity
        if process is None or secret is None or identity is None:
            return None
        sequence = self._sequence + 1 if expected_sequence is None else expected_sequence
        try:
            frame = self._await_frame(0.5)
            message_type, response_sequence, payload = decode_authenticated_frame(
                secret,
                frame,
            )
            if (
                message_type != "stopped"
                or response_sequence != sequence
                or set(payload) != {"deniedNetworkAttemptCount", "reason"}
                or payload.get("reason") != "idle"
            ):
                return None
            self._denied_network_attempt_count = _network_attempt_count(payload)
            process.wait(timeout=_PROCESS_EXIT_GRACE_SECONDS)
        except (OSError, SpeechRuntimeError, subprocess.TimeoutExpired):
            return None
        job = self._job
        owned_processes_exited = job is None or job.wait_empty(_PROCESS_EXIT_GRACE_SECONDS)
        ownership_confirmed = identity.launcher_pid == process.pid and (
            job is None or job.target_confirmed
        )
        job_object_requirement_satisfied = job is None or (job.required and job.launcher_assigned)
        job_object_assigned = bool(job is not None and job.required and job.launcher_assigned)
        confirmed_exited = process.poll() is not None
        denied_network_attempt_count = self._denied_network_attempt_count or 0
        graceful_shutdown_confirmed = (
            process.returncode == 0
            and ownership_confirmed
            and job_object_requirement_satisfied
            and confirmed_exited
            and owned_processes_exited
            and denied_network_attempt_count == 0
        )
        evidence = SpeechRuntimeExitEvidence(
            pid=identity.pid,
            launcher_pid=process.pid,
            exit_code=process.returncode,
            reason="idle" if graceful_shutdown_confirmed else "process_error",
            ownership_confirmed=ownership_confirmed,
            shutdown_acknowledged=True,
            graceful_shutdown_confirmed=graceful_shutdown_confirmed,
            terminated_by_parent=False,
            confirmed_exited=confirmed_exited,
            job_object_assigned=job_object_assigned,
            owned_processes_confirmed_exited=owned_processes_exited,
            denied_network_attempt_count=denied_network_attempt_count,
        )
        self._last_exit = evidence
        if confirmed_exited and owned_processes_exited:
            self._finalize_exited_process(process)
        return evidence

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> ManagedSpeechRuntime:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _synthesize_once(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
        *,
        on_dispatch_committed: Callable[[], None] | None,
        expected_identity: SpeechWorkerIdentity | None,
    ) -> SpeechArtifact:
        context.require_time()
        if expected_identity is None:
            self.start()
        elif (
            self._identity != expected_identity
            or self._process is None
            or self._last_exit is not None
        ):
            raise SpeechRuntimeError(
                "SPEECH_WORKER_IDENTITY_INVALID",
                "The acquired speech worker identity changed before dispatch.",
                retryable=True,
            )
        elif self._process.poll() is not None:
            self._observe_natural_idle_exit()
            raise SpeechRuntimeError(
                "SPEECH_WORKER_EXITED",
                "The acquired speech worker exited before dispatch.",
                retryable=True,
            )
        remaining = min(context.remaining_seconds(), self.config.request_timeout_seconds)
        if remaining < MIN_RUNTIME_TIMEOUT_SECONDS:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_DEADLINE_EXCEEDED",
                "The speech worker request exceeded its deadline.",
                retryable=True,
            )
        sequence = self._sequence + 1
        payload: dict[str, object] = {
            "context": {
                "attemptId": context.attempt_id,
                "correlationId": context.correlation_id,
                "idempotencyKey": context.idempotency_key,
                "invocationPurpose": context.invocation_purpose,
                "jobId": context.job_id,
                "networkAccessPermitted": context.network_access_permitted,
                "restrictedVoiceAcknowledged": context.restricted_voice_acknowledged,
                "rightsRecordId": context.rights_record_id,
                "rightsRecordRevision": context.rights_record_revision,
            },
            "request": {
                "language": request.language,
                "outputFormat": request.output_format,
                "pronunciationOverrides": [
                    _pronunciation_override_to_payload(span)
                    for span in request.pronunciation_overrides
                ],
                "requestId": request.request_id,
                "sampleRateHz": request.sample_rate_hz,
                "speed": request.speed,
                "text": request.text,
                "voiceId": request.voice_id,
            },
            "timeoutMilliseconds": max(1, round(remaining * 1_000)),
        }
        process = self._process
        secret = self._secret
        if process is None or secret is None:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_NOT_RUNNING",
                "The managed speech worker was not running.",
                retryable=True,
            )
        frame = encode_authenticated_frame(secret, "synthesize", sequence, payload)
        if process.poll() is not None or process.stdin is None:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_EXITED",
                "The speech worker exited before accepting a frame.",
                retryable=True,
            )
        if on_dispatch_committed is not None:
            try:
                on_dispatch_committed()
            except Exception:
                if process.poll() is not None:
                    self._terminate_owned("process_error")
                raise
        self._sequence = sequence
        _write_bytes(process, frame)
        frame = self._await_frame(remaining)
        assert self._secret is not None
        message_type, response_sequence, response_payload = decode_authenticated_frame(
            self._secret,
            frame,
        )
        if response_sequence != sequence:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_PROTOCOL_INVALID",
                "The speech worker returned an out-of-sequence frame.",
                retryable=True,
            )
        self._last_used_monotonic = time.monotonic()
        if message_type == "error":
            if set(response_payload) != {
                "code",
                "deniedNetworkAttemptCount",
                "retryable",
            }:
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_PROTOCOL_INVALID",
                    "The speech worker returned an invalid error frame.",
                    retryable=True,
                )
            code = response_payload.get("code")
            retryable = response_payload.get("retryable")
            if not isinstance(code, str) or not isinstance(retryable, bool):
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_PROTOCOL_INVALID",
                    "The speech worker returned an invalid error frame.",
                    retryable=True,
                )
            denied_count = _network_attempt_count(response_payload)
            self._denied_network_attempt_count = denied_count
            if denied_count:
                raise SpeechRuntimeError(
                    "SPEECH_WORKER_NETWORK_ATTEMPT_DENIED",
                    "The speech worker attempted a denied Python socket operation.",
                )
            self._terminate_owned("process_error")
            raise SpeechProviderError(
                code,
                "The local speech provider rejected the bounded request.",
                retryable=retryable,
            )
        if message_type != "artifact":
            raise SpeechRuntimeError(
                "SPEECH_WORKER_PROTOCOL_INVALID",
                "The speech worker returned an unexpected frame.",
                retryable=True,
            )
        try:
            denied_count = _network_attempt_count(response_payload)
            artifact_payload = dict(response_payload)
            del artifact_payload["deniedNetworkAttemptCount"]
            artifact = _artifact_from_payload(artifact_payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_PROTOCOL_INVALID",
                "The speech worker returned an invalid artifact.",
                retryable=True,
            ) from exc
        self._denied_network_attempt_count = denied_count
        if denied_count:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_NETWORK_ATTEMPT_DENIED",
                "The speech worker attempted a denied Python socket operation.",
            )
        if (
            artifact.provider_id != self.config.provider_id
            or artifact.runtime_id != self.config.runtime_id
            or artifact.runtime_version != self.config.runtime_version
            or artifact.model_id != self.config.model_id
            or artifact.model_version != self.config.model_version
        ):
            raise SpeechRuntimeError(
                "SPEECH_WORKER_IDENTITY_INVALID",
                "The speech worker artifact identity did not match its authenticated identity.",
                retryable=True,
            )
        return artifact

    def _worker_argv(
        self,
        *,
        launch_mode: Literal["python-module", "frozen-executable"] | None = None,
        ownership_job_name: str = "not-applicable",
    ) -> list[str]:
        resolved_mode = launch_mode or _resolve_launch_mode(self.config.launch_mode)
        prefix = (
            [str(self.config.python_executable.absolute()), _FROZEN_WORKER_FLAG]
            if resolved_mode == "frozen-executable"
            else [
                str(self.config.python_executable.absolute()),
                "-m",
                "cinematic_story_service.speech_runtime_worker",
            ]
        )
        return [
            *prefix,
            "--owner-pid",
            str(os.getpid()),
            "--launch-mode",
            resolved_mode,
            "--ownership-job-name",
            ownership_job_name,
            "--provider-id",
            self.config.provider_id,
            "--runtime-id",
            self.config.runtime_id,
            "--runtime-version",
            self.config.runtime_version,
            "--model-id",
            self.config.model_id,
            "--model-version",
            self.config.model_version,
            "--model-manifest-fingerprint",
            self.config.model_manifest_fingerprint,
        ]

    def _write_authenticated(
        self,
        message_type: str,
        sequence: int,
        payload: dict[str, object],
    ) -> None:
        if self._process is None or self._secret is None:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_NOT_RUNNING",
                "The managed speech worker was not running.",
                retryable=True,
            )
        frame = encode_authenticated_frame(self._secret, message_type, sequence, payload)
        _write_bytes(self._process, frame)

    def _await_frame(self, timeout_seconds: float) -> bytes:
        messages = self._messages
        if messages is None:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_NOT_RUNNING",
                "The managed speech worker was not running.",
                retryable=True,
            )
        try:
            value = messages.get(timeout=max(MIN_RUNTIME_TIMEOUT_SECONDS, timeout_seconds))
        except queue.Empty as exc:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_DEADLINE_EXCEEDED",
                "The speech worker did not respond before its deadline.",
                retryable=True,
            ) from exc
        if value is None:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_EXITED",
                "The speech worker exited before returning a frame.",
                retryable=True,
            )
        if isinstance(value, BaseException):
            raise SpeechRuntimeError(
                "SPEECH_WORKER_PROTOCOL_INVALID",
                "The speech worker returned an invalid bounded frame.",
                retryable=True,
            ) from value
        return value

    def _validate_identity(
        self,
        process: subprocess.Popen[bytes],
        payload: dict[str, Any],
        *,
        creation_nonce: str,
        launch_started_ns: int,
    ) -> SpeechWorkerIdentity:
        expected_keys = {
            "createdAtUnixNs",
            "creationNonce",
            "executable",
            "jobObjectAssigned",
            "launchMode",
            "launcherPid",
            "modelId",
            "modelManifestFingerprint",
            "modelVersion",
            "ownershipJobName",
            "parentPid",
            "pid",
            "processParentPid",
            "protocolVersion",
            "providerId",
            "runtimeId",
            "runtimeVersion",
            "deniedNetworkAttemptCount",
        }
        if set(payload) != expected_keys:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_IDENTITY_INVALID",
                "The speech worker identity was invalid.",
            )
        pid = payload.get("pid")
        parent_pid = payload.get("parentPid")
        launcher_pid = payload.get("launcherPid")
        process_parent_pid = payload.get("processParentPid")
        created_at = payload.get("createdAtUnixNs")
        executable = payload.get("executable")
        job_object_assigned = payload.get("jobObjectAssigned")
        launch_mode = payload.get("launchMode")
        ownership_job_name = payload.get("ownershipJobName")
        denied_network_attempt_count = payload.get("deniedNetworkAttemptCount")
        expected_launch_mode = _resolve_launch_mode(self.config.launch_mode)
        job = self._job
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(parent_pid, int)
            or isinstance(parent_pid, bool)
            or parent_pid != os.getpid()
            or not isinstance(launcher_pid, int)
            or isinstance(launcher_pid, bool)
            or launcher_pid != process.pid
            or not isinstance(process_parent_pid, int)
            or isinstance(process_parent_pid, bool)
            or process_parent_pid <= 0
            or not isinstance(created_at, int)
            or isinstance(created_at, bool)
            or not launch_started_ns - 5_000_000_000 <= created_at <= time.time_ns() + 5_000_000_000
            or not isinstance(executable, str)
            or not isinstance(job_object_assigned, bool)
            or launch_mode != expected_launch_mode
            or not isinstance(ownership_job_name, str)
            or not isinstance(denied_network_attempt_count, int)
            or isinstance(denied_network_attempt_count, bool)
            or denied_network_attempt_count != 0
            or job is None
            or ownership_job_name != job.name
        ):
            raise SpeechRuntimeError(
                "SPEECH_WORKER_IDENTITY_INVALID",
                "The speech worker process identity was invalid.",
            )
        direct_topology = (
            pid == launcher_pid
            and process_parent_pid == parent_pid
            and expected_launch_mode in {"python-module", "frozen-executable"}
        )
        intermediary_topology = (
            sys.platform == "win32" and pid != launcher_pid and process_parent_pid == launcher_pid
        )
        if not direct_topology and not intermediary_topology:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_OWNERSHIP_INVALID",
                "The speech worker process topology was invalid.",
            )
        if job_object_assigned != (sys.platform == "win32"):
            raise SpeechRuntimeError(
                "SPEECH_WORKER_OWNERSHIP_INVALID",
                "The speech worker process-tree ownership proof was invalid.",
            )
        expected_executable = str(self.config.python_executable.absolute().resolve(strict=True))
        reported_executable = str(Path(executable).absolute().resolve(strict=True))
        compare_reported = (
            reported_executable.casefold() if sys.platform == "win32" else reported_executable
        )
        compare_expected = (
            expected_executable.casefold() if sys.platform == "win32" else expected_executable
        )
        if compare_reported != compare_expected:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_IDENTITY_INVALID",
                "The speech worker executable identity was invalid.",
            )
        expected_values = {
            "creationNonce": creation_nonce,
            "launchMode": expected_launch_mode,
            "modelId": self.config.model_id,
            "modelManifestFingerprint": self.config.model_manifest_fingerprint,
            "modelVersion": self.config.model_version,
            "ownershipJobName": job.name,
            "protocolVersion": SPEECH_RUNTIME_PROTOCOL_VERSION,
            "providerId": self.config.provider_id,
            "runtimeId": self.config.runtime_id,
            "runtimeVersion": self.config.runtime_version,
        }
        if any(payload.get(key) != value for key, value in expected_values.items()):
            raise SpeechRuntimeError(
                "SPEECH_WORKER_IDENTITY_INVALID",
                "The speech worker runtime or model identity was invalid.",
            )
        try:
            job.confirm_target(pid)
        except OSError as exc:
            raise SpeechRuntimeError(
                "SPEECH_WORKER_OWNERSHIP_INVALID",
                "The authenticated speech worker was outside its owned process tree.",
            ) from exc
        return SpeechWorkerIdentity(
            pid=pid,
            parent_pid=parent_pid,
            launcher_pid=launcher_pid,
            process_parent_pid=process_parent_pid,
            executable=Path(reported_executable),
            created_at_unix_ns=created_at,
            creation_nonce=creation_nonce,
            protocol_version=SPEECH_RUNTIME_PROTOCOL_VERSION,
            provider_id=self.config.provider_id,
            runtime_id=self.config.runtime_id,
            runtime_version=self.config.runtime_version,
            model_id=self.config.model_id,
            model_version=self.config.model_version,
            model_manifest_fingerprint=self.config.model_manifest_fingerprint,
            launch_mode=expected_launch_mode,
            ownership_job_name=job.name,
            job_object_assigned=job_object_assigned,
            denied_network_attempt_count=denied_network_attempt_count,
        )

    def _terminate_owned(
        self,
        reason: Literal["deadline", "protocol_error", "process_error"],
    ) -> SpeechRuntimeExitEvidence | None:
        process = self._process
        if process is None:
            return self._last_exit
        identity = self._identity
        job = self._job
        prior_exit = self._last_exit
        same_prior_process = (
            prior_exit is not None
            and prior_exit.launcher_pid == process.pid
            and (identity is None or prior_exit.pid == identity.pid)
        )
        ownership_confirmed = (
            identity is not None
            and identity.launcher_pid == process.pid
            and (job is None or job.target_confirmed)
        )
        terminated = bool(
            same_prior_process and prior_exit is not None and prior_exit.terminated_by_parent
        )
        try:
            job_has_processes = job is not None and not job.wait_empty(0.0)
        except Exception:
            job_has_processes = job is not None
        if process.poll() is None or job_has_processes:
            terminated = True
            try:
                if job is not None and job.required and job.launcher_assigned:
                    job.terminate()
                elif process.poll() is None:
                    process.terminate()
            except Exception:
                terminated = True
            try:
                process.wait(timeout=_PROCESS_EXIT_GRACE_SECONDS)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                try:
                    process.kill()
                    process.wait(timeout=_PROCESS_EXIT_GRACE_SECONDS)
                except (OSError, subprocess.TimeoutExpired, ValueError):
                    pass
        try:
            owned_processes_exited = job is None or job.wait_empty(_PROCESS_EXIT_GRACE_SECONDS)
        except Exception:
            owned_processes_exited = False
        effective_reason: Literal["deadline", "protocol_error", "process_error"] = reason
        if same_prior_process and prior_exit is not None:
            if prior_exit.reason == "deadline":
                effective_reason = "deadline"
            elif prior_exit.reason == "protocol_error":
                effective_reason = "protocol_error"
            elif prior_exit.reason == "process_error":
                effective_reason = "process_error"
        evidence = SpeechRuntimeExitEvidence(
            pid=identity.pid if identity is not None else process.pid,
            launcher_pid=process.pid,
            exit_code=process.poll(),
            reason=effective_reason,
            ownership_confirmed=ownership_confirmed,
            shutdown_acknowledged=False,
            graceful_shutdown_confirmed=False,
            terminated_by_parent=terminated,
            confirmed_exited=process.poll() is not None and owned_processes_exited,
            job_object_assigned=(job is not None and job.required and job.launcher_assigned),
            owned_processes_confirmed_exited=owned_processes_exited,
            denied_network_attempt_count=self._denied_network_attempt_count or 0,
        )
        self._last_exit = evidence
        if evidence.confirmed_exited and evidence.owned_processes_confirmed_exited:
            self._finalize_exited_process(process)
        return evidence

    def _finalize_exited_process(self, process: subprocess.Popen[bytes]) -> bool:
        """Release an exited worker only after its stdout reader reaches EOF."""

        reader = self._reader
        if reader is not None:
            if reader is threading.current_thread():
                return False
            reader.join(timeout=_PROCESS_EXIT_GRACE_SECONDS)
            if reader.is_alive():
                return False
        self._close_process_streams(process)
        self._clear_process_state()
        return True

    @staticmethod
    def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _clear_process_state(self) -> None:
        job = self._job
        self._job = None
        if job is not None:
            job.close()
        self._process = None
        self._reader = None
        self._messages = None
        self._secret = None
        self._sequence = 0
        self._identity = None
        self._last_used_monotonic = None
        self._denied_network_attempt_count = None


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobObjectBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class _WindowsSpeechJobObject:
    """Own an exact launcher tree, including a PyInstaller one-file worker."""

    def __init__(self, *, memory_limit_bytes: int) -> None:
        self.required = sys.platform == "win32"
        self.name = f"Local\\CinematicStorySpeech-{uuid4()}" if self.required else "not-applicable"
        self.launcher_assigned = not self.required
        self.target_confirmed = not self.required
        self._kernel32: Any | None = None
        self._handle: Any | None = None
        self._target_handle: Any | None = None
        if not self.required:
            return
        kernel32 = _windows_kernel32()
        handle = kernel32.CreateJobObjectW(None, self.name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        )
        limits.ProcessMemoryLimit = memory_limit_bytes
        if not kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self._kernel32 = kernel32
        self._handle = handle

    def assign_launcher(self, pid: int) -> None:
        if not self.required:
            return
        process_handle = self._open_process(pid)
        try:
            self._assign_handle(process_handle)
        finally:
            assert self._kernel32 is not None
            self._kernel32.CloseHandle(process_handle)
        self.launcher_assigned = True

    def confirm_target(self, pid: int) -> None:
        if not self.required:
            return
        if self._target_handle is not None:
            raise OSError("The speech worker target was already confirmed.")
        process_handle = self._open_process(pid)
        if not self._contains_handle(process_handle):
            assert self._kernel32 is not None
            self._kernel32.CloseHandle(process_handle)
            raise OSError("The speech worker target did not join its owned job.")
        self._target_handle = process_handle
        self.target_confirmed = True

    def terminate(self) -> None:
        if not self.required:
            return
        if self._kernel32 is None or self._handle is None:
            raise OSError("The speech worker job object was unavailable.")
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def wait_empty(self, timeout_seconds: float) -> bool:
        if not self.required:
            return True
        expires = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            if self._active_process_count() == 0:
                return True
            remaining = expires - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))

    def close(self) -> None:
        target_handle = self._target_handle
        self._target_handle = None
        if target_handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(target_handle)
        handle = self._handle
        self._handle = None
        if handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(handle)

    def _open_process(self, pid: int) -> Any:
        if self._kernel32 is None:
            raise OSError("The speech worker job object was unavailable.")
        process_handle = self._kernel32.OpenProcess(
            _PROCESS_TERMINATE
            | _PROCESS_SET_QUOTA
            | _PROCESS_QUERY_LIMITED_INFORMATION
            | _SYNCHRONIZE,
            False,
            pid,
        )
        if not process_handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        return process_handle

    def _assign_handle(self, process_handle: Any) -> None:
        if self._kernel32 is None or self._handle is None:
            raise OSError("The speech worker job object was unavailable.")
        if self._contains_handle(process_handle):
            return
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        if not self._contains_handle(process_handle):
            raise OSError("The speech worker launcher did not join its owned job.")

    def _contains_handle(self, process_handle: Any) -> bool:
        if self._kernel32 is None or self._handle is None:
            return False
        contained = ctypes.c_int()
        if not self._kernel32.IsProcessInJob(
            process_handle,
            self._handle,
            ctypes.byref(contained),
        ):
            raise OSError(ctypes.get_last_error(), "IsProcessInJob failed")
        return bool(contained.value)

    def _active_process_count(self) -> int:
        if self._kernel32 is None or self._handle is None:
            raise OSError("The speech worker job object was unavailable.")
        accounting = _JobObjectBasicAccountingInformation()
        returned_length = ctypes.c_uint32()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            ctypes.byref(returned_length),
        ):
            raise OSError(ctypes.get_last_error(), "QueryInformationJobObject failed")
        return int(accounting.ActiveProcesses)


def _windows_kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.OpenJobObjectW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.QueryInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.IsProcessInJob.restype = ctypes.c_int
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _self_assign_windows_speech_job(name: str) -> bool:
    if sys.platform != "win32":
        return False
    if not _valid_ownership_job_name(name):
        return False
    kernel32 = _windows_kernel32()
    handle = kernel32.OpenJobObjectW(
        _JOB_OBJECT_ASSIGN_PROCESS | _JOB_OBJECT_QUERY,
        False,
        name,
    )
    if not handle:
        return False
    try:
        current_process = kernel32.GetCurrentProcess()
        contained = ctypes.c_int()
        if not kernel32.IsProcessInJob(current_process, handle, ctypes.byref(contained)):
            return False
        if not contained.value and not kernel32.AssignProcessToJobObject(handle, current_process):
            return False
        contained = ctypes.c_int()
        if not kernel32.IsProcessInJob(current_process, handle, ctypes.byref(contained)):
            return False
        return bool(contained.value)
    finally:
        kernel32.CloseHandle(handle)


def _owned_process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = _windows_kernel32()
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE,
            False,
            pid,
        )
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _valid_ownership_job_name(name: str) -> bool:
    prefix = "Local\\CinematicStorySpeech-"
    suffix = name.removeprefix(prefix)
    if not name.startswith(prefix) or len(suffix) != 36:
        return False
    try:
        return str(UUID(suffix)) == suffix
    except ValueError:
        return False


def _resolve_launch_mode(
    configured: Literal["auto", "python-module", "frozen-executable"],
) -> Literal["python-module", "frozen-executable"]:
    if configured == "auto":
        return "frozen-executable" if bool(getattr(sys, "frozen", False)) else "python-module"
    return configured


def encode_authenticated_frame(
    secret: bytes,
    message_type: str,
    sequence: int,
    payload: dict[str, object],
) -> bytes:
    if len(secret) < MIN_SPEECH_RUNTIME_SECRET_BYTES:
        raise ValueError("The speech runtime authentication secret was too short.")
    if not message_type or len(message_type) > 32 or not message_type.replace("_", "").isalnum():
        raise ValueError("The speech runtime frame type was invalid.")
    if sequence < 0:
        raise ValueError("The speech runtime sequence must be non-negative.")
    authenticated = {"payload": payload, "sequence": sequence, "type": message_type}
    mac = hmac.new(
        secret,
        canonical_json(authenticated).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    frame = (canonical_json({**authenticated, "mac": mac}) + "\n").encode("utf-8")
    if len(frame) > MAX_SPEECH_RUNTIME_FRAME_BYTES:
        raise ValueError("The speech runtime frame exceeded its fixed byte bound.")
    return frame


def decode_authenticated_frame(
    secret: bytes,
    frame: bytes,
) -> tuple[str, int, dict[str, Any]]:
    if not frame or len(frame) > MAX_SPEECH_RUNTIME_FRAME_BYTES or not frame.endswith(b"\n"):
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "The speech runtime frame was invalid.",
        )
    try:
        decoded = json.loads(frame)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "The speech runtime frame was invalid.",
        ) from exc
    if not isinstance(decoded, dict) or set(decoded) != {"mac", "payload", "sequence", "type"}:
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "The speech runtime frame was invalid.",
        )
    message_type = decoded.get("type")
    sequence = decoded.get("sequence")
    payload = decoded.get("payload")
    mac = decoded.get("mac")
    if (
        not isinstance(message_type, str)
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
        or not isinstance(payload, dict)
        or not isinstance(mac, str)
        or len(mac) != 64
    ):
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "The speech runtime frame was invalid.",
        )
    authenticated = {"payload": payload, "sequence": sequence, "type": message_type}
    expected = hmac.new(
        secret,
        canonical_json(authenticated).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise SpeechRuntimeError(
            "SPEECH_WORKER_AUTHENTICATION_FAILED",
            "The speech runtime frame failed authentication.",
        )
    return message_type, sequence, payload


def _pronunciation_override_to_payload(
    span: SpeechPronunciationOverrideSpan,
) -> dict[str, object]:
    return {
        "entryId": span.entry_id,
        "entryRevision": span.entry_revision,
        "grapheme": span.grapheme,
        "pronunciation": span.pronunciation,
        "representation": span.representation,
        "sourceEnd": span.source_end,
        "sourceStart": span.source_start,
    }


def artifact_to_payload(artifact: SpeechArtifact) -> dict[str, object]:
    encoded_audio = base64.b64encode(artifact.wav_bytes).decode("ascii")
    return {
        "adapterId": artifact.adapter_id,
        "adapterVersion": artifact.adapter_version,
        "channels": artifact.channels,
        "completedAt": artifact.completed_at,
        "configurationFingerprint": artifact.configuration_fingerprint,
        "deterministic": artifact.deterministic,
        "frameCount": artifact.frame_count,
        "inputFingerprint": artifact.input_fingerprint,
        "modelId": artifact.model_id,
        "modelSha256": artifact.model_sha256,
        "modelVersion": artifact.model_version,
        "providerId": artifact.provider_id,
        "runtimeId": artifact.runtime_id,
        "runtimeVersion": artifact.runtime_version,
        "sampleRateHz": artifact.sample_rate_hz,
        "sampleWidthBytes": artifact.sample_width_bytes,
        "startedAt": artifact.started_at,
        "voiceId": artifact.voice_id,
        "voiceSha256": artifact.voice_sha256,
        "warnings": list(artifact.warnings),
        "wavBase64": encoded_audio,
        "wavSha256": artifact.wav_sha256,
    }


def _artifact_from_payload(payload: dict[str, Any]) -> SpeechArtifact:
    expected_keys = {
        "adapterId",
        "adapterVersion",
        "channels",
        "completedAt",
        "configurationFingerprint",
        "deterministic",
        "frameCount",
        "inputFingerprint",
        "modelId",
        "modelSha256",
        "modelVersion",
        "providerId",
        "runtimeId",
        "runtimeVersion",
        "sampleRateHz",
        "sampleWidthBytes",
        "startedAt",
        "voiceId",
        "voiceSha256",
        "warnings",
        "wavBase64",
        "wavSha256",
    }
    if set(payload) != expected_keys:
        raise ValueError("The speech artifact payload had an invalid shape.")
    encoded = payload["wavBase64"]
    warnings = payload["warnings"]
    if (
        not isinstance(encoded, str)
        or not isinstance(warnings, list)
        or not all(isinstance(value, str) for value in warnings)
    ):
        raise ValueError("The speech artifact payload had invalid values.")
    wav_bytes = base64.b64decode(encoded, validate=True)
    if len(wav_bytes) > MAX_SPEECH_AUDIO_BYTES:
        raise ValueError("The speech artifact exceeded its fixed byte bound.")

    def string(name: str) -> str:
        value = payload[name]
        if not isinstance(value, str):
            raise ValueError("The speech artifact payload had invalid string values.")
        return value

    def integer(name: str) -> int:
        value = payload[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("The speech artifact payload had invalid integer values.")
        return value

    deterministic = payload["deterministic"]
    if not isinstance(deterministic, bool):
        raise ValueError("The speech artifact payload had an invalid determinism value.")
    return SpeechArtifact(
        provider_id=string("providerId"),
        adapter_id=string("adapterId"),
        adapter_version=string("adapterVersion"),
        runtime_id=string("runtimeId"),
        runtime_version=string("runtimeVersion"),
        model_id=string("modelId"),
        model_version=string("modelVersion"),
        model_sha256=string("modelSha256"),
        voice_id=string("voiceId"),
        voice_sha256=string("voiceSha256"),
        input_fingerprint=string("inputFingerprint"),
        configuration_fingerprint=string("configurationFingerprint"),
        wav_bytes=wav_bytes,
        wav_sha256=string("wavSha256"),
        sample_rate_hz=integer("sampleRateHz"),
        channels=integer("channels"),
        sample_width_bytes=integer("sampleWidthBytes"),
        frame_count=integer("frameCount"),
        deterministic=deterministic,
        warnings=tuple(warnings),
        started_at=string("startedAt"),
        completed_at=string("completedAt"),
    )


def read_bounded_frame(stream: BinaryIO, *, limit: int = MAX_SPEECH_RUNTIME_FRAME_BYTES) -> bytes:
    frame = stream.readline(limit + 1)
    if not frame:
        return b""
    if len(frame) > limit or not frame.endswith(b"\n"):
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "The speech runtime frame exceeded its fixed bound.",
        )
    return frame


def _reader_loop(stream: BinaryIO, messages: queue.Queue[_ReaderItem]) -> None:
    try:
        while True:
            frame = read_bounded_frame(stream)
            if not frame:
                messages.put(None)
                return
            messages.put(frame)
    except BaseException as exc:
        messages.put(exc)


def _write_plain_frame(
    process: subprocess.Popen[bytes],
    value: dict[str, object],
    limit: int,
) -> None:
    frame = (canonical_json(value) + "\n").encode("utf-8")
    if len(frame) > limit:
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "The speech worker bootstrap exceeded its fixed bound.",
        )
    _write_bytes(process, frame)


def _write_bytes(process: subprocess.Popen[bytes], frame: bytes) -> None:
    if process.poll() is not None or process.stdin is None:
        raise SpeechRuntimeError(
            "SPEECH_WORKER_EXITED",
            "The speech worker exited before accepting a frame.",
            retryable=True,
        )
    try:
        process.stdin.write(frame)
        process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise SpeechRuntimeError(
            "SPEECH_WORKER_EXITED",
            "The speech worker exited before accepting a frame.",
            retryable=True,
        ) from exc


def _runtime_environment() -> dict[str, str]:
    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH"):
        value = os.environ.get(key)
        if value is not None:
            environment[key] = value
    return environment
