from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import json
import os
import re
import select
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Literal, NoReturn

from .local_speech import (
    MAX_SPEECH_PRONUNCIATION_OVERRIDES,
    SpeechInvocationContext,
    SpeechPronunciationOverrideSpan,
    SpeechProviderError,
    SpeechSynthesisRequest,
)
from .model_packages import KOKORO_LOCAL_ONNX_MANIFEST
from .speech_providers import (
    FIXTURE_PROVIDER_ID,
    KOKORO_PROVIDER_ID,
    DeterministicPcmWavSpeechProvider,
    KokoroLocalOnnxSpeechProvider,
)
from .speech_runtime import (
    MAX_SPEECH_RUNTIME_BOOTSTRAP_BYTES,
    MAX_SPEECH_RUNTIME_FRAME_BYTES,
    MIN_SPEECH_RUNTIME_SECRET_BYTES,
    SPEECH_RUNTIME_PROTOCOL_VERSION,
    _owned_process_exists,
    _self_assign_windows_speech_job,
    artifact_to_payload,
    decode_authenticated_frame,
    encode_authenticated_frame,
)

_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")
_MIN_IDLE_TIMEOUT_MILLISECONDS = 50
_MAX_IDLE_TIMEOUT_MILLISECONDS = 10 * 60 * 1_000


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--owner-pid", required=True, type=int)
    parser.add_argument("--launch-mode", required=True)
    parser.add_argument("--ownership-job-name", required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--runtime-version", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--model-manifest-fingerprint", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    created_at_unix_ns = time.time_ns()
    try:
        args = _parser().parse_args(argv)
        _validate_cli(args)
        launcher_pid, process_parent_pid = _establish_process_lineage(args)
        job_object_assigned = _self_assign_windows_speech_job(args.ownership_job_name)
        if job_object_assigned != (sys.platform == "win32"):
            return 2
        bootstrap = _read_bootstrap(sys.stdin.buffer)
        secret = _validate_bootstrap(args, bootstrap)
        network_deny_state = _deny_network()
        provider = _provider_from_bootstrap(args, bootstrap)
        ready_payload: dict[str, object] = {
            "createdAtUnixNs": created_at_unix_ns,
            "creationNonce": bootstrap["creationNonce"],
            "deniedNetworkAttemptCount": network_deny_state.count,
            "executable": str(Path(sys.executable).resolve(strict=True)),
            "jobObjectAssigned": job_object_assigned,
            "launchMode": args.launch_mode,
            "launcherPid": launcher_pid,
            "modelId": args.model_id,
            "modelManifestFingerprint": args.model_manifest_fingerprint,
            "modelVersion": args.model_version,
            "ownershipJobName": args.ownership_job_name,
            "parentPid": args.owner_pid,
            "pid": os.getpid(),
            "processParentPid": process_parent_pid,
            "protocolVersion": SPEECH_RUNTIME_PROTOCOL_VERSION,
            "providerId": args.provider_id,
            "runtimeId": args.runtime_id,
            "runtimeVersion": args.runtime_version,
        }
        _write_frame(encode_authenticated_frame(secret, "ready", 0, ready_payload))
        idle_timeout = int(bootstrap["idleTimeoutMilliseconds"]) / 1_000
        return _serve(
            provider,
            secret=secret,
            owner_pid=args.owner_pid,
            launcher_pid=launcher_pid,
            process_parent_pid=process_parent_pid,
            idle_timeout_seconds=idle_timeout,
            network_deny_state=network_deny_state,
        )
    except Exception:
        return 2


def _serve(
    provider: DeterministicPcmWavSpeechProvider | KokoroLocalOnnxSpeechProvider,
    *,
    secret: bytes,
    owner_pid: int,
    launcher_pid: int,
    process_parent_pid: int,
    idle_timeout_seconds: float,
    network_deny_state: _NetworkDenyState,
) -> int:
    pending = bytearray()
    input_descriptor = sys.stdin.fileno()
    expected_sequence = 1
    last_activity = time.monotonic()
    while True:
        if (
            not _owned_process_exists(owner_pid)
            or not _owned_process_exists(launcher_pid)
            or os.getppid() != process_parent_pid
        ):
            return 0
        remaining_idle = idle_timeout_seconds - (time.monotonic() - last_activity)
        if remaining_idle <= 0:
            _write_frame(
                encode_authenticated_frame(
                    secret,
                    "stopped",
                    expected_sequence,
                    {
                        "deniedNetworkAttemptCount": network_deny_state.count,
                        "reason": "idle",
                    },
                )
            )
            return 0
        try:
            item = _poll_input_frame(
                input_descriptor,
                pending,
                timeout_seconds=min(0.25, remaining_idle),
            )
        except (OSError, ValueError):
            return 2
        if item is None:
            continue
        if not item:
            return 0
        last_activity = time.monotonic()
        try:
            message_type, sequence, payload = decode_authenticated_frame(secret, item)
        except Exception:
            return 2
        if sequence != expected_sequence:
            return 2
        expected_sequence += 1
        if message_type == "shutdown":
            if set(payload) != {"reason"} or payload.get("reason") not in {"clean", "idle"}:
                return 2
            _write_frame(
                encode_authenticated_frame(
                    secret,
                    "stopped",
                    sequence,
                    {
                        "deniedNetworkAttemptCount": network_deny_state.count,
                        "reason": str(payload["reason"]),
                    },
                )
            )
            return 0
        if message_type != "synthesize":
            return 2
        try:
            request, context = _parse_synthesis_payload(payload)
            with contextlib.redirect_stdout(sys.stderr):
                artifact = provider.synthesize(request, context)
            artifact_payload = artifact_to_payload(artifact)
            artifact_payload["deniedNetworkAttemptCount"] = network_deny_state.count
            response = encode_authenticated_frame(
                secret,
                "artifact",
                sequence,
                artifact_payload,
            )
        except SpeechProviderError as exc:
            code = exc.code if _SAFE_ERROR_CODE.fullmatch(exc.code) else "SPEECH_PROVIDER_FAILED"
            response = encode_authenticated_frame(
                secret,
                "error",
                sequence,
                {
                    "code": code,
                    "deniedNetworkAttemptCount": network_deny_state.count,
                    "retryable": exc.retryable,
                },
            )
        except (TypeError, ValueError):
            response = encode_authenticated_frame(
                secret,
                "error",
                sequence,
                {
                    "code": "SPEECH_REQUEST_INVALID",
                    "deniedNetworkAttemptCount": network_deny_state.count,
                    "retryable": False,
                },
            )
        except Exception:
            response = encode_authenticated_frame(
                secret,
                "error",
                sequence,
                {
                    "code": "SPEECH_PROVIDER_FAILED",
                    "deniedNetworkAttemptCount": network_deny_state.count,
                    "retryable": True,
                },
            )
        _write_frame(response)


def _validate_cli(args: argparse.Namespace) -> None:
    if args.owner_pid <= 0:
        raise ValueError("Invalid owner PID.")
    if args.launch_mode not in {"python-module", "frozen-executable"}:
        raise ValueError("Invalid launch mode.")
    if not isinstance(args.ownership_job_name, str) or not 1 <= len(args.ownership_job_name) <= 160:
        raise ValueError("Invalid ownership job name.")
    for value in (
        args.provider_id,
        args.runtime_id,
        args.runtime_version,
        args.model_id,
        args.model_version,
    ):
        if not isinstance(value, str) or not value or len(value) > 160:
            raise ValueError("Invalid runtime identity.")
    fingerprint = args.model_manifest_fingerprint
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(value not in "0123456789abcdef" for value in fingerprint)
    ):
        raise ValueError("Invalid model fingerprint.")


def _establish_process_lineage(
    args: argparse.Namespace,
) -> tuple[int, int]:
    launch_mode: Literal["python-module", "frozen-executable"] = args.launch_mode
    running_frozen = bool(getattr(sys, "frozen", False))
    if running_frozen != (launch_mode == "frozen-executable"):
        raise ValueError("The worker launch mode did not match its executable.")
    if not _owned_process_exists(args.owner_pid):
        raise ValueError("The worker owner was not running.")
    worker_pid = os.getpid()
    process_parent_pid = os.getppid()
    if process_parent_pid == args.owner_pid:
        return worker_pid, process_parent_pid
    if sys.platform != "win32" or not _owned_process_exists(process_parent_pid):
        raise ValueError("The worker launcher lineage was invalid.")
    return process_parent_pid, process_parent_pid


def _read_bootstrap(stream: BinaryIO) -> dict[str, Any]:
    frame = stream.readline(MAX_SPEECH_RUNTIME_BOOTSTRAP_BYTES + 1)
    if not frame or len(frame) > MAX_SPEECH_RUNTIME_BOOTSTRAP_BYTES or not frame.endswith(b"\n"):
        raise ValueError("Invalid bootstrap frame.")
    try:
        decoded = json.loads(frame)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid bootstrap frame.") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Invalid bootstrap frame.")
    return decoded


def _validate_bootstrap(args: argparse.Namespace, value: dict[str, Any]) -> bytes:
    expected_keys = {
        "creationNonce",
        "idleTimeoutMilliseconds",
        "launchMode",
        "modelId",
        "modelManifestFingerprint",
        "modelPackagePath",
        "modelVersion",
        "ownershipJobName",
        "protocolVersion",
        "providerId",
        "runtimeId",
        "runtimeVersion",
        "secret",
    }
    if set(value) != expected_keys:
        raise ValueError("Invalid bootstrap frame.")
    expected = {
        "launchMode": args.launch_mode,
        "modelId": args.model_id,
        "modelManifestFingerprint": args.model_manifest_fingerprint,
        "modelVersion": args.model_version,
        "ownershipJobName": args.ownership_job_name,
        "protocolVersion": SPEECH_RUNTIME_PROTOCOL_VERSION,
        "providerId": args.provider_id,
        "runtimeId": args.runtime_id,
        "runtimeVersion": args.runtime_version,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("Incompatible bootstrap identity.")
    nonce = value.get("creationNonce")
    idle_timeout = value.get("idleTimeoutMilliseconds")
    encoded_secret = value.get("secret")
    package_path = value.get("modelPackagePath")
    if (
        not isinstance(nonce, str)
        or len(nonce) != 64
        or any(character not in "0123456789abcdef" for character in nonce)
        or not isinstance(idle_timeout, int)
        or isinstance(idle_timeout, bool)
        or not _MIN_IDLE_TIMEOUT_MILLISECONDS <= idle_timeout <= _MAX_IDLE_TIMEOUT_MILLISECONDS
        or not isinstance(encoded_secret, str)
        or (package_path is not None and not isinstance(package_path, str))
    ):
        raise ValueError("Invalid bootstrap values.")
    try:
        secret = base64.b64decode(encoded_secret, validate=True)
    except ValueError as exc:
        raise ValueError("Invalid bootstrap secret.") from exc
    if len(secret) != MIN_SPEECH_RUNTIME_SECRET_BYTES:
        raise ValueError("Invalid bootstrap secret.")
    return secret


def _provider_from_bootstrap(
    args: argparse.Namespace,
    bootstrap: dict[str, Any],
) -> DeterministicPcmWavSpeechProvider | KokoroLocalOnnxSpeechProvider:
    provider: DeterministicPcmWavSpeechProvider | KokoroLocalOnnxSpeechProvider
    if args.provider_id == FIXTURE_PROVIDER_ID:
        if bootstrap["modelPackagePath"] is not None:
            raise ValueError("The fixture provider does not accept a model path.")
        provider = DeterministicPcmWavSpeechProvider()
    elif args.provider_id == KOKORO_PROVIDER_ID:
        if (
            args.model_id != KOKORO_LOCAL_ONNX_MANIFEST.model_id
            or args.model_version != KOKORO_LOCAL_ONNX_MANIFEST.model_version
            or args.model_manifest_fingerprint != KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
        ):
            raise ValueError("The Kokoro model identity was not allow-listed.")
        raw_path = bootstrap["modelPackagePath"]
        if not isinstance(raw_path, str):
            raise ValueError("The Kokoro model package path was missing.")
        package_path = Path(raw_path)
        if not package_path.is_absolute() or not package_path.is_dir():
            raise ValueError("The Kokoro model package path was invalid.")
        provider = KokoroLocalOnnxSpeechProvider(package_path)
    else:
        raise ValueError("The speech provider was not allow-listed.")
    descriptor = provider.descriptor()
    if (
        descriptor.provider_id != args.provider_id
        or descriptor.runtime_id != args.runtime_id
        or descriptor.runtime_version != args.runtime_version
    ):
        raise ValueError("The speech provider identity did not match the worker identity.")
    return provider


def _parse_synthesis_payload(
    payload: dict[str, Any],
) -> tuple[SpeechSynthesisRequest, SpeechInvocationContext]:
    if set(payload) != {"context", "request", "timeoutMilliseconds"}:
        raise ValueError("Invalid synthesis payload.")
    request_value = payload.get("request")
    context_value = payload.get("context")
    timeout = payload.get("timeoutMilliseconds")
    if (
        not isinstance(request_value, dict)
        or not isinstance(context_value, dict)
        or not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= 10 * 60 * 1_000
    ):
        raise ValueError("Invalid synthesis payload.")
    if set(request_value) != {
        "language",
        "outputFormat",
        "pronunciationOverrides",
        "requestId",
        "sampleRateHz",
        "speed",
        "text",
        "voiceId",
    } or set(context_value) != {
        "attemptId",
        "correlationId",
        "idempotencyKey",
        "invocationPurpose",
        "jobId",
        "networkAccessPermitted",
        "restrictedVoiceAcknowledged",
        "rightsRecordId",
        "rightsRecordRevision",
    }:
        raise ValueError("Invalid synthesis payload.")

    def request_string(name: str) -> str:
        value = request_value.get(name)
        if not isinstance(value, str):
            raise ValueError("Invalid synthesis request string.")
        return value

    def context_string(name: str) -> str:
        value = context_value.get(name)
        if not isinstance(value, str):
            raise ValueError("Invalid synthesis context string.")
        return value

    speed = request_value.get("speed")
    sample_rate = request_value.get("sampleRateHz")
    pronunciation_overrides = request_value.get("pronunciationOverrides")
    restricted = context_value.get("restrictedVoiceAcknowledged")
    network = context_value.get("networkAccessPermitted")
    rights_id = context_value.get("rightsRecordId")
    rights_revision = context_value.get("rightsRecordRevision")
    invocation_purpose = context_value.get("invocationPurpose")
    if (
        not isinstance(speed, int | float)
        or isinstance(speed, bool)
        or not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or not isinstance(pronunciation_overrides, list)
        or len(pronunciation_overrides) > MAX_SPEECH_PRONUNCIATION_OVERRIDES
        or not isinstance(restricted, bool)
        or not isinstance(network, bool)
        or invocation_purpose not in {"governed_product_audition", "component_verification"}
        or (rights_id is not None and not isinstance(rights_id, str))
        or (
            rights_revision is not None
            and (not isinstance(rights_revision, int) or isinstance(rights_revision, bool))
        )
    ):
        raise ValueError("Invalid synthesis payload values.")
    request = SpeechSynthesisRequest(
        request_id=request_string("requestId"),
        text=request_string("text"),
        voice_id=request_string("voiceId"),
        language=request_string("language"),
        speed=float(speed),
        sample_rate_hz=sample_rate,
        output_format=request_string("outputFormat"),
        pronunciation_overrides=tuple(
            _parse_pronunciation_override(value) for value in pronunciation_overrides
        ),
    )
    context = SpeechInvocationContext(
        correlation_id=context_string("correlationId"),
        job_id=context_string("jobId"),
        attempt_id=context_string("attemptId"),
        idempotency_key=context_string("idempotencyKey"),
        deadline_monotonic=time.monotonic() + timeout / 1_000,
        invocation_purpose=invocation_purpose,
        restricted_voice_acknowledged=restricted,
        rights_record_id=rights_id,
        rights_record_revision=rights_revision,
        network_access_permitted=network,
    )
    return request, context


def _parse_pronunciation_override(value: object) -> SpeechPronunciationOverrideSpan:
    expected_keys = {
        "entryId",
        "entryRevision",
        "grapheme",
        "pronunciation",
        "representation",
        "sourceEnd",
        "sourceStart",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("Invalid pronunciation override span.")
    source_start = value.get("sourceStart")
    source_end = value.get("sourceEnd")
    entry_revision = value.get("entryRevision")
    if (
        not isinstance(source_start, int)
        or isinstance(source_start, bool)
        or not isinstance(source_end, int)
        or isinstance(source_end, bool)
        or not isinstance(entry_revision, int)
        or isinstance(entry_revision, bool)
    ):
        raise ValueError("Invalid pronunciation override span bounds.")

    def string(name: str) -> str:
        item = value.get(name)
        if not isinstance(item, str):
            raise ValueError("Invalid pronunciation override span value.")
        return item

    representation = string("representation")
    typed_representation: Literal["ipa", "neutral"]
    if representation == "ipa":
        typed_representation = "ipa"
    elif representation == "neutral":
        typed_representation = "neutral"
    else:
        raise ValueError("Invalid pronunciation override representation.")
    return SpeechPronunciationOverrideSpan(
        source_start=source_start,
        source_end=source_end,
        grapheme=string("grapheme"),
        pronunciation=string("pronunciation"),
        representation=typed_representation,
        entry_id=string("entryId"),
        entry_revision=entry_revision,
    )


def _poll_input_frame(
    file_descriptor: int,
    pending: bytearray,
    *,
    timeout_seconds: float,
) -> bytes | None:
    newline = pending.find(b"\n")
    if newline >= 0:
        frame = bytes(pending[: newline + 1])
        del pending[: newline + 1]
        return frame
    chunk = _poll_input_chunk(file_descriptor, timeout_seconds=timeout_seconds)
    if chunk is None:
        return None
    if not chunk:
        if pending:
            raise ValueError("The speech worker received a partial frame before EOF.")
        return b""
    pending.extend(chunk)
    if len(pending) > MAX_SPEECH_RUNTIME_FRAME_BYTES:
        raise ValueError("The speech worker input exceeded its fixed frame bound.")
    newline = pending.find(b"\n")
    if newline < 0:
        return None
    frame = bytes(pending[: newline + 1])
    del pending[: newline + 1]
    return frame


def _poll_input_chunk(file_descriptor: int, *, timeout_seconds: float) -> bytes | None:
    if sys.platform != "win32":
        readable, _writable, _exceptional = select.select(
            [file_descriptor],
            [],
            [],
            max(0.0, timeout_seconds),
        )
        if not readable:
            return None
        return os.read(file_descriptor, 64 * 1024)

    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    kernel32.PeekNamedPipe.restype = ctypes.c_int
    pipe_handle = msvcrt.get_osfhandle(file_descriptor)
    expires = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        available = ctypes.c_uint32()
        if not kernel32.PeekNamedPipe(
            pipe_handle,
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        ):
            return b""
        if available.value > 0:
            return os.read(file_descriptor, min(64 * 1024, available.value))
        remaining = expires - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.01, remaining))


def _write_frame(frame: bytes) -> None:
    if len(frame) > MAX_SPEECH_RUNTIME_FRAME_BYTES:
        raise ValueError("The speech worker response exceeded its fixed frame bound.")
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()


class _NetworkDenyState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def deny(self) -> NoReturn:
        with self._lock:
            self._count += 1
        raise OSError("network disabled by the managed speech worker")


def _deny_network() -> _NetworkDenyState:
    state = _NetworkDenyState()
    original_socket = socket.socket

    class OfflineSocket(original_socket):  # type: ignore[misc,valid-type]
        def connect(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def connect_ex(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def send(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def sendall(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def sendto(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def sendmsg(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def sendfile(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def bind(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def listen(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

        def accept(self, *_args: object, **_kwargs: object) -> NoReturn:
            state.deny()

    def denied_connection(*_args: object, **_kwargs: object) -> NoReturn:
        state.deny()

    socket.socket = OfflineSocket  # type: ignore[misc]
    socket.SocketType = OfflineSocket
    socket.create_connection = denied_connection
    return state


if __name__ == "__main__":
    raise SystemExit(main())
