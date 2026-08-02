from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Literal

import pytest

from cinematic_story_service import launcher as service_launcher
from cinematic_story_service import speech_runtime, speech_runtime_worker
from cinematic_story_service.local_speech import (
    SpeechInvocationContext,
    SpeechPronunciationOverrideSpan,
    SpeechProviderError,
    SpeechSynthesisRequest,
    inspect_pcm_wav,
)
from cinematic_story_service.speech_providers import FIXTURE_PROVIDER_ID
from cinematic_story_service.speech_runtime import (
    MAX_SPEECH_RUNTIME_BOOTSTRAP_BYTES,
    ManagedSpeechRuntime,
    SpeechRuntimeConfig,
    SpeechRuntimeError,
    decode_authenticated_frame,
    encode_authenticated_frame,
    read_bounded_frame,
)

_FIXTURE_MANIFEST_FINGERPRINT = hashlib.sha256(b"fixture-worker-manifest").hexdigest()


def _runtime_config(
    *,
    idle_timeout_seconds: float = 5.0,
    max_retries: int = 1,
    model_package_path: Path | None = None,
    model_id: str = "deterministic-square-wave",
    python_executable: Path = Path(sys.executable),
    request_timeout_seconds: float = 5.0,
    startup_timeout_seconds: float = 15.0,
    launch_mode: Literal["auto", "python-module", "frozen-executable"] = "python-module",
) -> SpeechRuntimeConfig:
    return SpeechRuntimeConfig(
        provider_id=FIXTURE_PROVIDER_ID,
        runtime_id="python-integer-pcm",
        runtime_version="1.0.0",
        model_id=model_id,
        model_version="1.0.0",
        model_manifest_fingerprint=_FIXTURE_MANIFEST_FINGERPRINT,
        model_package_path=model_package_path,
        python_executable=python_executable,
        startup_timeout_seconds=startup_timeout_seconds,
        request_timeout_seconds=request_timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        max_retries=max_retries,
        launch_mode=launch_mode,
    )


def _request() -> SpeechSynthesisRequest:
    return SpeechSynthesisRequest(
        request_id="runtime-request-1",
        text="Authenticated child process synthesis.",
        voice_id="fixture-narrator-01",
    )


def _request_with_pronunciation_override() -> SpeechSynthesisRequest:
    return SpeechSynthesisRequest(
        request_id="runtime-request-with-override",
        text="Aster authenticates this child process synthesis.",
        voice_id="fixture-narrator-01",
        pronunciation_overrides=(
            SpeechPronunciationOverrideSpan(
                source_start=0,
                source_end=5,
                grapheme="Aster",
                pronunciation="ˈæstɚ",
                representation="ipa",
                entry_id="pronunciation-entry-aster",
                entry_revision=4,
            ),
        ),
    )


def _context(*, expires_in_seconds: float = 10.0) -> SpeechInvocationContext:
    return SpeechInvocationContext(
        correlation_id="runtime-correlation-1",
        job_id="runtime-job-1",
        attempt_id="runtime-attempt-1",
        idempotency_key="runtime-idempotency-1",
        deadline_monotonic=time.monotonic() + expires_in_seconds,
    )


def test_dispatch_callback_failure_does_not_consume_worker_sequence() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    identity = runtime.start()
    callbacks: list[str] = []

    def reject_dispatch() -> None:
        callbacks.append("attempted")
        raise RuntimeError("injected durable dispatch failure")

    with pytest.raises(RuntimeError, match="injected durable dispatch failure"):
        runtime.synthesize(
            _request(),
            _context(),
            on_dispatch_committed=reject_dispatch,
        )

    assert callbacks == ["attempted"]
    assert runtime._sequence == 0
    assert runtime.identity == identity
    assert runtime.is_running is True
    artifact = runtime.synthesize(_request(), _context())
    assert artifact.voice_id == _request().voice_id
    assert runtime._sequence == 1
    evidence = runtime.stop()
    assert evidence is not None
    assert evidence.graceful_shutdown_confirmed is True


def _assert_failed_runtime_was_reaped(runtime: ManagedSpeechRuntime) -> None:
    assert runtime.is_running is False
    assert runtime.last_exit is not None
    assert runtime.last_exit.confirmed_exited is True
    assert runtime.last_exit.owned_processes_confirmed_exited is True
    assert runtime._process is None
    assert runtime._job is None


def _suspend_windows_process(pid: int) -> None:
    process_suspend_resume = 0x0800
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
    ntdll.NtSuspendProcess.restype = ctypes.c_long
    handle = kernel32.OpenProcess(process_suspend_resume, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        status = ntdll.NtSuspendProcess(handle)
        if status != 0:
            raise OSError(status, "NtSuspendProcess failed")
    finally:
        kernel32.CloseHandle(handle)


def _terminate_windows_process(pid: int, *, exit_code: int = 23) -> None:
    process_terminate = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(process_terminate, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        if not kernel32.TerminateProcess(handle, exit_code):
            raise OSError(ctypes.get_last_error(), "TerminateProcess failed")
    finally:
        kernel32.CloseHandle(handle)


def _frozen_service_executable() -> Path:
    raw = os.environ.get("CINEMATIC_STORY_TEST_FROZEN_SERVICE")
    if not raw:
        pytest.skip("Set CINEMATIC_STORY_TEST_FROZEN_SERVICE after a PyInstaller build.")
    executable = Path(raw).absolute()
    if not executable.is_file():
        pytest.fail("CINEMATIC_STORY_TEST_FROZEN_SERVICE did not identify a file.")
    return executable


def test_authenticated_runtime_frames_reject_tampering_and_enforce_bounds() -> None:
    secret = b"s" * 32
    frame = encode_authenticated_frame(secret, "ready", 0, {"value": 1})
    assert decode_authenticated_frame(secret, frame) == ("ready", 0, {"value": 1})

    decoded = json.loads(frame)
    decoded["payload"]["value"] = 2
    tampered = (json.dumps(decoded, separators=(",", ":")) + "\n").encode()
    with pytest.raises(SpeechRuntimeError) as authentication_error:
        decode_authenticated_frame(secret, tampered)
    assert authentication_error.value.code == "SPEECH_WORKER_AUTHENTICATION_FAILED"

    with pytest.raises(SpeechRuntimeError) as bound_error:
        read_bounded_frame(io.BytesIO(b"01234567890\n"), limit=10)
    assert bound_error.value.code == "SPEECH_WORKER_PROTOCOL_INVALID"


def test_managed_runtime_executes_fixture_with_authenticated_identity_and_clean_exit() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config())
    identity = runtime.start()

    assert identity.pid > 0
    assert identity.parent_pid > 0
    if identity.launcher_pid == identity.pid:
        assert identity.process_parent_pid == identity.parent_pid
    else:
        assert sys.platform == "win32"
        assert identity.process_parent_pid == identity.launcher_pid
    assert identity.executable == Path(sys.executable).resolve(strict=True)
    assert identity.provider_id == FIXTURE_PROVIDER_ID
    assert identity.runtime_id == "python-integer-pcm"
    assert identity.model_id == "deterministic-square-wave"
    assert identity.model_manifest_fingerprint == _FIXTURE_MANIFEST_FINGERPRINT
    assert identity.launch_mode == "python-module"
    assert identity.job_object_assigned is (sys.platform == "win32")
    assert identity.denied_network_attempt_count == 0

    artifact = runtime.synthesize(_request(), _context())
    assert artifact.provider_id == identity.provider_id
    assert artifact.runtime_id == identity.runtime_id
    assert artifact.runtime_version == identity.runtime_version
    assert artifact.model_id == identity.model_id
    assert artifact.model_version == identity.model_version
    assert inspect_pcm_wav(artifact.wav_bytes) == (24_000, 1, 2, 6_000)
    assert runtime.denied_network_attempt_count == 0

    evidence = runtime.stop(reason="clean")
    assert evidence is not None
    assert evidence.pid == identity.pid
    assert evidence.launcher_pid == identity.launcher_pid
    assert evidence.reason == "clean"
    assert evidence.exit_code == 0
    assert evidence.ownership_confirmed is True
    assert evidence.shutdown_acknowledged is True
    assert evidence.graceful_shutdown_confirmed is True
    assert evidence.terminated_by_parent is False
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True
    assert evidence.denied_network_attempt_count == 0
    assert runtime.is_running is False


def test_startup_deadline_preserves_deadline_exit_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))

    def expire_startup(_timeout_seconds: float) -> bytes:
        raise SpeechRuntimeError(
            "SPEECH_WORKER_DEADLINE_EXCEEDED",
            "The speech worker did not respond before its deadline.",
            retryable=True,
        )

    monkeypatch.setattr(runtime, "_await_frame", expire_startup)
    with pytest.raises(SpeechRuntimeError) as error:
        runtime.start()

    assert error.value.code == "SPEECH_WORKER_DEADLINE_EXCEEDED"
    assert error.value.retryable is True
    evidence = runtime.last_exit
    assert evidence is not None
    assert evidence.reason == "deadline"
    assert evidence.terminated_by_parent is True
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True
    assert runtime.is_running is False


def test_startup_deadline_reaps_reader_before_descriptor_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    original_reader_loop = speech_runtime._reader_loop
    reader_reached_eof = threading.Event()
    release_reader = threading.Event()
    join_called = threading.Event()
    captured_readers: list[threading.Thread] = []

    def delayed_reader_loop(
        stream: object,
        messages: object,
    ) -> None:
        original_reader_loop(stream, messages)  # type: ignore[arg-type]
        reader_reached_eof.set()
        release_reader.wait(timeout=2.0)

    def expire_startup(_timeout_seconds: float) -> bytes:
        assert runtime._reader is not None
        reader = runtime._reader
        captured_readers.append(reader)
        original_join = reader.join

        def release_then_join(timeout: float | None = None) -> None:
            join_called.set()
            release_reader.set()
            original_join(timeout=timeout)

        monkeypatch.setattr(reader, "join", release_then_join)
        raise SpeechRuntimeError(
            "SPEECH_WORKER_DEADLINE_EXCEEDED",
            "The speech worker did not respond before its deadline.",
            retryable=True,
        )

    monkeypatch.setattr(speech_runtime, "_reader_loop", delayed_reader_loop)
    monkeypatch.setattr(runtime, "_await_frame", expire_startup)
    try:
        with pytest.raises(SpeechRuntimeError) as error:
            runtime.start()
        assert error.value.code == "SPEECH_WORKER_DEADLINE_EXCEEDED"
        assert reader_reached_eof.wait(timeout=1.0)
        assert len(captured_readers) == 1
        assert join_called.is_set()
        assert captured_readers[0].is_alive() is False

        monkeypatch.setattr(speech_runtime, "_reader_loop", original_reader_loop)
        successor = ManagedSpeechRuntime(_runtime_config(max_retries=0))
        identity = successor.start()
        successor_exit = successor.stop(reason="clean")
        assert identity.launcher_pid > 0
        assert successor_exit is not None
        assert successor_exit.confirmed_exited is True
        assert successor_exit.owned_processes_confirmed_exited is True
    finally:
        release_reader.set()
        for reader in captured_readers:
            reader.join(timeout=1.0)


def test_shutdown_rejects_forged_authenticated_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    runtime.start()
    assert runtime._secret is not None

    def forged_acknowledgement(_timeout_seconds: float) -> bytes:
        assert runtime._secret is not None
        return encode_authenticated_frame(
            runtime._secret,
            "stopped",
            runtime._sequence + 1,
            {"deniedNetworkAttemptCount": 0, "reason": "clean"},
        )

    monkeypatch.setattr(runtime, "_await_frame", forged_acknowledgement)
    evidence = runtime.stop(reason="clean")

    assert evidence is not None
    assert evidence.reason == "protocol_error"
    assert evidence.shutdown_acknowledged is False
    assert evidence.graceful_shutdown_confirmed is False
    assert evidence.terminated_by_parent is True
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True


def test_clean_shutdown_accepts_authenticated_natural_idle_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(idle_timeout_seconds=0.2, max_retries=0))
    identity = runtime.start()

    def allow_worker_natural_idle(
        message_type: str,
        sequence: int,
        payload: dict[str, object],
    ) -> None:
        assert message_type == "shutdown"
        assert sequence == 1
        assert payload == {"reason": "clean"}
        time.sleep(0.5)

    monkeypatch.setattr(runtime, "_write_authenticated", allow_worker_natural_idle)
    evidence = runtime.stop(reason="clean")

    assert evidence is not None
    assert evidence.pid == identity.pid
    assert evidence.reason == "idle"
    assert evidence.exit_code == 0
    assert evidence.shutdown_acknowledged is True
    assert evidence.graceful_shutdown_confirmed is True
    assert evidence.ownership_confirmed is True
    assert evidence.terminated_by_parent is False
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True
    assert evidence.denied_network_attempt_count == 0


@pytest.mark.parametrize(
    "write_error",
    [
        SpeechRuntimeError(
            "SPEECH_WORKER_EXITED",
            "The speech worker exited before accepting a frame.",
            retryable=True,
        ),
        BrokenPipeError("The speech worker closed its input pipe."),
    ],
    ids=("typed-exit", "broken-pipe"),
)
def test_clean_shutdown_recovers_authenticated_idle_after_write_exit_race(
    monkeypatch: pytest.MonkeyPatch,
    write_error: BaseException,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(idle_timeout_seconds=0.2, max_retries=0))
    identity = runtime.start()

    def lose_write_to_natural_idle(
        message_type: str,
        sequence: int,
        payload: dict[str, object],
    ) -> None:
        assert message_type == "shutdown"
        assert sequence == 1
        assert payload == {"reason": "clean"}
        time.sleep(0.5)
        raise write_error

    monkeypatch.setattr(runtime, "_write_authenticated", lose_write_to_natural_idle)
    evidence = runtime.stop(reason="clean")

    assert evidence is not None
    assert evidence.pid == identity.pid
    assert evidence.reason == "idle"
    assert evidence.exit_code == 0
    assert evidence.shutdown_acknowledged is True
    assert evidence.graceful_shutdown_confirmed is True
    assert evidence.ownership_confirmed is True
    assert evidence.terminated_by_parent is False
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True
    assert evidence.denied_network_attempt_count == 0


def test_clean_shutdown_rejects_authenticated_disallowed_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    runtime.start()
    assert runtime._secret is not None

    def disallowed_acknowledgement(_timeout_seconds: float) -> bytes:
        assert runtime._secret is not None
        return encode_authenticated_frame(
            runtime._secret,
            "stopped",
            runtime._sequence,
            {"deniedNetworkAttemptCount": 0, "reason": "process_error"},
        )

    monkeypatch.setattr(runtime, "_await_frame", disallowed_acknowledgement)
    evidence = runtime.stop(reason="clean")

    assert evidence is not None
    assert evidence.reason == "protocol_error"
    assert evidence.shutdown_acknowledged is False
    assert evidence.graceful_shutdown_confirmed is False
    assert evidence.ownership_confirmed is True
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True
    assert evidence.job_object_assigned is (sys.platform == "win32")


def test_clean_shutdown_write_exit_without_idle_frame_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    runtime.start()

    def fail_write(
        message_type: str,
        sequence: int,
        payload: dict[str, object],
    ) -> None:
        assert message_type == "shutdown"
        assert sequence == 1
        assert payload == {"reason": "clean"}
        raise SpeechRuntimeError(
            "SPEECH_WORKER_EXITED",
            "The speech worker exited before accepting a frame.",
            retryable=True,
        )

    monkeypatch.setattr(runtime, "_write_authenticated", fail_write)
    evidence = runtime.stop(reason="clean")

    assert evidence is not None
    assert evidence.reason == "protocol_error"
    assert evidence.shutdown_acknowledged is False
    assert evidence.graceful_shutdown_confirmed is False
    assert evidence.terminated_by_parent is True
    assert evidence.ownership_confirmed is True
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True
    assert evidence.job_object_assigned is (sys.platform == "win32")


@pytest.mark.skipif(sys.platform != "win32", reason="The product worker is Windows-only.")
def test_incomplete_job_exit_retains_exact_handles_until_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    identity = runtime.start()
    job = runtime._job
    assert job is not None

    with monkeypatch.context() as scoped:
        scoped.setattr(job, "wait_empty", lambda _timeout_seconds: False)
        incomplete = runtime._terminate_owned("deadline")

    assert incomplete is not None
    assert incomplete.pid == identity.pid
    assert incomplete.ownership_confirmed is True
    assert incomplete.owned_processes_confirmed_exited is False
    assert incomplete.confirmed_exited is False
    assert incomplete.reason == "deadline"
    assert incomplete.terminated_by_parent is True
    assert runtime._process is not None
    assert runtime._job is job
    assert runtime.identity == identity

    recovered = runtime._terminate_owned("process_error")
    assert recovered is not None
    assert recovered.pid == identity.pid
    assert recovered.ownership_confirmed is True
    assert recovered.confirmed_exited is True
    assert recovered.owned_processes_confirmed_exited is True
    assert recovered.reason == "deadline"
    assert recovered.terminated_by_parent is True
    assert runtime._process is None
    assert runtime._job is None


@pytest.mark.skipif(sys.platform != "win32", reason="The product worker is Windows-only.")
def test_preexited_code_two_worker_is_never_reported_as_clean() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    identity = runtime.start()
    _terminate_windows_process(identity.pid, exit_code=2)
    assert runtime._process is not None
    runtime._process.wait(timeout=5.0)

    evidence = runtime.stop(reason="clean")

    assert evidence is not None
    assert evidence.exit_code == 2
    assert evidence.reason == "process_error"
    assert evidence.shutdown_acknowledged is False
    assert evidence.graceful_shutdown_confirmed is False
    assert evidence.terminated_by_parent is True
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True


@pytest.mark.skipif(sys.platform != "win32", reason="The product worker is Windows-only.")
def test_forced_shutdown_is_never_reported_as_graceful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(speech_runtime, "_PROCESS_EXIT_GRACE_SECONDS", 0.1)
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    identity = runtime.start()
    _suspend_windows_process(identity.pid)
    try:
        evidence = runtime.stop(reason="clean")
    finally:
        runtime._terminate_owned("process_error")

    assert evidence is not None
    assert evidence.reason in {"protocol_error", "process_error"}
    assert evidence.shutdown_acknowledged is False
    assert evidence.graceful_shutdown_confirmed is False
    assert evidence.terminated_by_parent is True
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True


def test_authenticated_runtime_dispatch_preserves_exact_pronunciation_override_plan() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    request = _request_with_pronunciation_override()

    artifact = runtime.synthesize(request, _context())

    assert artifact.input_fingerprint == request.input_fingerprint()
    without_override = SpeechSynthesisRequest(
        request_id=request.request_id,
        text=request.text,
        voice_id=request.voice_id,
    )
    assert artifact.input_fingerprint != without_override.input_fingerprint()
    evidence = runtime.stop()
    assert evidence is not None
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True


def test_managed_runtime_reaps_only_its_authenticated_idle_child() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(idle_timeout_seconds=0.5))
    identity = runtime.start()

    assert runtime.reap_if_idle(now_monotonic=time.monotonic() + 1.0) is True
    assert runtime.is_running is False
    assert runtime.last_exit is not None
    assert runtime.last_exit.pid == identity.pid
    assert runtime.last_exit.reason == "idle"
    assert runtime.last_exit.ownership_confirmed is True


def test_managed_runtime_authenticates_worker_initiated_idle_exit() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(idle_timeout_seconds=0.2))
    identity = runtime.start()
    deadline = time.monotonic() + 5.0
    while runtime.is_running and time.monotonic() < deadline:
        time.sleep(0.02)

    assert runtime.is_running is False
    assert runtime.reap_if_idle() is True
    evidence = runtime.last_exit
    assert evidence is not None
    assert evidence.pid == identity.pid
    assert evidence.reason == "idle"
    assert evidence.exit_code == 0
    assert evidence.shutdown_acknowledged is True
    assert evidence.graceful_shutdown_confirmed is True
    assert evidence.ownership_confirmed is True
    assert evidence.owned_processes_confirmed_exited is True
    assert evidence.terminated_by_parent is False
    assert evidence.denied_network_attempt_count == 0


def test_start_reports_prior_natural_exit_before_launching_a_new_identity() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(idle_timeout_seconds=0.2, max_retries=0))
    original = runtime.start()
    deadline = time.monotonic() + 5.0
    while runtime.is_running and time.monotonic() < deadline:
        time.sleep(0.02)

    with pytest.raises(SpeechRuntimeError) as error:
        runtime.start()

    assert error.value.code == "SPEECH_WORKER_EXITED"
    assert error.value.retryable is True
    evidence = runtime.last_exit
    assert evidence is not None
    assert evidence.pid == original.pid
    assert evidence.reason == "idle"
    assert evidence.graceful_shutdown_confirmed is True
    assert runtime.is_running is False

    replacement = runtime.start()
    assert replacement.creation_nonce != original.creation_nonce
    replacement_exit = runtime.stop()
    assert replacement_exit is not None
    assert replacement_exit.pid == replacement.pid
    assert replacement_exit.graceful_shutdown_confirmed is True


def test_runtime_deadline_fails_closed_without_starting_child() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config())
    with pytest.raises(SpeechProviderError) as deadline_error:
        runtime.synthesize(_request(), _context(expires_in_seconds=-1.0))
    assert deadline_error.value.code == "SPEECH_DEADLINE_EXCEEDED"
    assert runtime.is_running is False


def test_runtime_retry_count_is_strictly_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=1))
    attempts = 0

    def fail_once_per_attempt(
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
        *,
        on_dispatch_committed: object,
        expected_identity: object,
    ) -> None:
        nonlocal attempts
        del request, context, on_dispatch_committed, expected_identity
        attempts += 1
        raise SpeechRuntimeError("SPEECH_WORKER_EXITED", "failed", retryable=True)

    monkeypatch.setattr(runtime, "_synthesize_once", fail_once_per_attempt)
    with pytest.raises(SpeechRuntimeError):
        runtime.synthesize(_request(), _context())
    assert attempts == 2


def test_worker_denies_tcp_udp_and_server_socket_paths_and_counts_attempts() -> None:
    original_socket = socket.socket
    original_socket_type = socket.SocketType
    original_create_connection = socket.create_connection
    tcp: socket.socket | None = None
    udp: socket.socket | None = None
    try:
        state = speech_runtime_worker._deny_network()
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        denied_operations = (
            lambda: socket.create_connection(("127.0.0.1", 9)),
            lambda: tcp.connect(("127.0.0.1", 9)),
            lambda: tcp.connect_ex(("127.0.0.1", 9)),
            lambda: tcp.send(b"x"),
            lambda: tcp.sendall(b"x"),
            lambda: udp.sendto(b"x", ("127.0.0.1", 9)),
            lambda: tcp.sendmsg([b"x"]),
            lambda: tcp.sendfile(io.BytesIO(b"x")),
            lambda: tcp.bind(("127.0.0.1", 0)),
            lambda: tcp.listen(1),
            lambda: tcp.accept(),
        )
        for operation in denied_operations:
            with pytest.raises(OSError, match="network disabled"):
                operation()
        assert state.count == len(denied_operations)
    finally:
        if tcp is not None:
            tcp.close()
        if udp is not None:
            udp.close()
        socket.socket = original_socket
        socket.SocketType = original_socket_type
        socket.create_connection = original_create_connection


def test_authenticated_provider_error_reaps_job_on_final_attempt() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    invalid_request = SpeechSynthesisRequest(
        request_id="runtime-invalid-voice",
        text="Reject this bounded request.",
        voice_id="missing-voice",
    )

    with pytest.raises(SpeechProviderError) as error:
        runtime.synthesize(invalid_request, _context())
    assert error.value.code == "SPEECH_VOICE_NOT_FOUND"
    assert runtime.last_exit is not None
    assert runtime.last_exit.reason == "process_error"
    assert runtime.denied_network_attempt_count == 0
    _assert_failed_runtime_was_reaped(runtime)


def test_invalid_decoded_response_reaps_job_on_final_attempt() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    runtime.start()
    assert runtime._messages is not None
    runtime._messages.put(b"{}\n")

    with pytest.raises(SpeechRuntimeError) as error:
        runtime.synthesize(_request(), _context())
    assert error.value.code == "SPEECH_WORKER_PROTOCOL_INVALID"
    assert runtime.last_exit is not None
    assert runtime.last_exit.reason == "protocol_error"
    _assert_failed_runtime_was_reaped(runtime)


def test_unexpected_authenticated_response_reaps_job_on_final_attempt() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    runtime.start()
    assert runtime._messages is not None
    assert runtime._secret is not None
    runtime._messages.put(
        encode_authenticated_frame(
            runtime._secret,
            "unexpected",
            1,
            {"deniedNetworkAttemptCount": 0},
        )
    )

    with pytest.raises(SpeechRuntimeError) as error:
        runtime.synthesize(_request(), _context())
    assert error.value.code == "SPEECH_WORKER_PROTOCOL_INVALID"
    _assert_failed_runtime_was_reaped(runtime)


def test_artifact_model_identity_mismatch_reaps_job_on_final_attempt() -> None:
    runtime = ManagedSpeechRuntime(
        _runtime_config(model_id="configured-model-mismatch", max_retries=0)
    )

    with pytest.raises(SpeechRuntimeError) as error:
        runtime.synthesize(_request(), _context())
    assert error.value.code == "SPEECH_WORKER_IDENTITY_INVALID"
    _assert_failed_runtime_was_reaped(runtime)


@pytest.mark.skipif(sys.platform != "win32", reason="The product worker is Windows-only.")
def test_live_suspended_worker_times_out_and_owned_job_is_empty() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0, request_timeout_seconds=0.15))
    identity = runtime.start()
    _suspend_windows_process(identity.pid)
    try:
        with pytest.raises(SpeechRuntimeError) as error:
            runtime.synthesize(_request(), _context())
        assert error.value.code == "SPEECH_WORKER_DEADLINE_EXCEEDED"
        assert runtime.last_exit is not None
        assert runtime.last_exit.reason == "deadline"
        _assert_failed_runtime_was_reaped(runtime)
    finally:
        runtime._terminate_owned("process_error")


@pytest.mark.skipif(sys.platform != "win32", reason="The product worker is Windows-only.")
def test_live_worker_crash_during_request_reaps_owned_job() -> None:
    runtime = ManagedSpeechRuntime(_runtime_config(max_retries=0))
    identity = runtime.start()
    _suspend_windows_process(identity.pid)
    crash_errors: list[BaseException] = []

    def crash_worker() -> None:
        try:
            _terminate_windows_process(identity.pid)
        except Exception as exc:  # pragma: no cover - surfaced in the parent assertion
            crash_errors.append(exc)

    crash = threading.Timer(0.1, crash_worker)
    crash.start()
    try:
        with pytest.raises(SpeechRuntimeError) as error:
            runtime.synthesize(_request(), _context())
        crash.join(timeout=5.0)
        assert not crash.is_alive()
        assert crash_errors == []
        assert error.value.code == "SPEECH_WORKER_EXITED"
        assert runtime.last_exit is not None
        assert runtime.last_exit.reason == "process_error"
        _assert_failed_runtime_was_reaped(runtime)
    finally:
        runtime._terminate_owned("process_error")


def test_frozen_archive_contains_runtime_licenses_and_metadata() -> None:
    executable = _frozen_service_executable()
    viewer = Path(sys.executable).with_name("pyi-archive_viewer.exe")
    if not viewer.is_file():
        pytest.skip("The PyInstaller archive viewer is not installed in this environment.")
    result = subprocess.run(
        [str(viewer), "-r", "-b", str(executable)],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    )
    inventory = result.stdout.replace("/", "\\").casefold()
    for required in (
        "kokorog2p-0.6.7.dist-info\\licenses\\license",
        "numpy-2.5.1.dist-info\\licenses\\license.txt",
        "numpy-2.5.1.dist-info\\metadata",
        "onnxruntime\\capi\\onnxruntime.dll",
        "onnxruntime\\license",
        "onnxruntime\\thirdpartynotices.txt",
    ):
        assert required in inventory


@pytest.mark.skipif(sys.platform != "win32", reason="The frozen product worker is Windows-only.")
def test_frozen_executable_dispatches_real_authenticated_fixture_worker() -> None:
    executable = _frozen_service_executable()
    runtime = ManagedSpeechRuntime(
        _runtime_config(
            max_retries=0,
            python_executable=executable,
            startup_timeout_seconds=30.0,
            launch_mode="frozen-executable",
        )
    )

    request = _request_with_pronunciation_override()
    artifact = runtime.synthesize(request, _context(expires_in_seconds=60.0))
    assert inspect_pcm_wav(artifact.wav_bytes) == (24_000, 1, 2, 6_000)
    assert artifact.input_fingerprint == request.input_fingerprint()
    evidence = runtime.stop()
    assert evidence is not None
    assert evidence.confirmed_exited is True
    assert evidence.owned_processes_confirmed_exited is True


def test_worker_argv_excludes_secret_and_model_path(tmp_path: Path) -> None:
    model_path = tmp_path / "model-package"
    model_path.mkdir()
    runtime = ManagedSpeechRuntime(_runtime_config(model_package_path=model_path))
    argv = runtime._worker_argv()

    assert str(model_path) not in argv
    assert "--model-manifest-fingerprint" in argv
    assert all(len(argument.encode()) < MAX_SPEECH_RUNTIME_BOOTSTRAP_BYTES for argument in argv)


def test_frozen_worker_argv_uses_launcher_dispatch_without_python_module_flag() -> None:
    base = _runtime_config()
    config = SpeechRuntimeConfig(
        provider_id=base.provider_id,
        runtime_id=base.runtime_id,
        runtime_version=base.runtime_version,
        model_id=base.model_id,
        model_version=base.model_version,
        model_manifest_fingerprint=base.model_manifest_fingerprint,
        python_executable=base.python_executable,
        startup_timeout_seconds=base.startup_timeout_seconds,
        request_timeout_seconds=base.request_timeout_seconds,
        idle_timeout_seconds=base.idle_timeout_seconds,
        max_retries=base.max_retries,
        launch_mode="frozen-executable",
    )
    runtime = ManagedSpeechRuntime(config)
    argv = runtime._worker_argv(
        launch_mode="frozen-executable",
        ownership_job_name="Local\\CinematicStorySpeech-00000000-0000-0000-0000-000000000000",
    )

    assert argv[:2] == [str(Path(sys.executable).absolute()), "--speech-runtime-worker"]
    assert "-m" not in argv
    assert "cinematic_story_service.speech_runtime_worker" not in argv
    assert "--owner-pid" in argv
    assert "--ownership-job-name" in argv


def test_launcher_dispatches_internal_speech_worker_before_service_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[list[str] | None] = []

    def fake_worker(argv: list[str] | None = None) -> int:
        received.append(argv)
        return 17

    monkeypatch.setattr(speech_runtime_worker, "main", fake_worker)
    result = service_launcher.main(
        ["--speech-runtime-worker", "--owner-pid", "123", "--launch-mode", "frozen-executable"]
    )
    assert result == 17
    assert received == [["--owner-pid", "123", "--launch-mode", "frozen-executable"]]


def test_incompatible_worker_identity_fails_closed() -> None:
    config = _runtime_config()
    incompatible = SpeechRuntimeConfig(
        provider_id=config.provider_id,
        runtime_id="wrong-runtime",
        runtime_version=config.runtime_version,
        model_id=config.model_id,
        model_version=config.model_version,
        model_manifest_fingerprint=config.model_manifest_fingerprint,
        python_executable=config.python_executable,
        startup_timeout_seconds=config.startup_timeout_seconds,
        request_timeout_seconds=config.request_timeout_seconds,
        idle_timeout_seconds=config.idle_timeout_seconds,
        max_retries=0,
    )
    runtime = ManagedSpeechRuntime(incompatible)

    with pytest.raises(SpeechRuntimeError):
        runtime.start()
    assert runtime.is_running is False
    assert runtime.last_exit is not None
    assert runtime.last_exit.confirmed_exited is True
