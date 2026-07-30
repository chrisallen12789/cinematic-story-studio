from __future__ import annotations

import ctypes
import logging
import math
import multiprocessing
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .document_ingest import (
    MAX_ARCHIVE_MEMBER_NAME_CHARACTERS,
    MAX_EXTRACTED_CHARACTERS,
    MAX_EXTRACTED_SECTIONS,
    MAX_PDF_PAGES,
    PARSER_DEADLINE_SECONDS,
    PARSER_PROCESS_MEMORY_LIMIT_BYTES,
    DocumentExtractionRequest,
    DocumentExtractionResult,
    adapter_for,
    parser_limits_fingerprint,
    parser_limits_profile,
)
from .errors import ServiceError
from .util import canonical_json, sha256_text

_LOGGER = logging.getLogger("cinematic_story_service.parser_process")

PARSER_RESULT_MESSAGE_LIMIT_BYTES = 64 * 1024 * 1024
_PARSER_CONTROL_MESSAGE_LIMIT_BYTES = 1
_PARSER_OWNERSHIP_MESSAGE_LIMIT_BYTES = 4 * 1024
_PARSER_PROCESS_EXIT_GRACE_SECONDS = 0.5
_PARSER_POLL_MAX_SECONDS = 0.05
_MAX_WARNINGS = 256
_MAX_PROVENANCE_BYTES = 64 * 1024
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_WARNING_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_:-]{0,79}$")
_CANONICAL_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_MAX_JSON_DEPTH = 20
_MAX_JSON_NODES = 2_048

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
_WAIT_OBJECT_0 = 0

ParserProcessReason = Literal[
    "succeeded",
    "parser_error",
    "cancelled",
    "deadline",
    "protocol_error",
    "ownership_error",
    "process_error",
]


@dataclass(frozen=True, slots=True)
class ParserProcessEvidence:
    pid: int
    launcher_pid: int
    reason: ParserProcessReason
    exit_code: int | None
    terminated_by_parent: bool
    launcher_terminated_by_parent: bool
    confirmed_exited: bool
    job_object_assigned: bool
    launcher_job_object_assigned: bool
    owned_processes_confirmed_exited: bool
    process_memory_limit_bytes: int | None
    duration_ms: int


class DocumentExtractionRunner(Protocol):
    def run(
        self,
        request: DocumentExtractionRequest,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[str, int], None],
    ) -> DocumentExtractionResult: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ProgressEnvelope(_StrictModel):
    type: Literal["progress"]
    stage: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    progress: int = Field(ge=0, lt=1_000_000)


class _ResultEnvelope(_StrictModel):
    type: Literal["result"]
    result: DocumentExtractionResult


class _ErrorEnvelope(_StrictModel):
    type: Literal["error"]
    status_code: int = Field(ge=400, le=599)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    retryable: bool


class _ReadyEnvelope(_StrictModel):
    type: Literal["ready"]
    pid: int = Field(gt=0)
    job_object_assigned: bool


_ParserEnvelope = Annotated[
    _ProgressEnvelope | _ResultEnvelope | _ErrorEnvelope,
    Field(discriminator="type"),
]
_PARSER_ENVELOPE_ADAPTER: TypeAdapter[_ParserEnvelope] = TypeAdapter(_ParserEnvelope)


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


class _WindowsJobObject:
    """Own the PyInstaller launcher and actual parser target through one named job."""

    def __init__(self, *, memory_limit_bytes: int) -> None:
        self.memory_limit_bytes = memory_limit_bytes
        self.assigned = False
        self.launcher_assigned = False
        self.name = f"Local\\CinematicStoryParser-{uuid.uuid4()}"
        self._kernel32: Any | None = None
        self._handle: Any | None = None
        self._target_handle: Any | None = None
        self._target_pid: int | None = None
        if sys.platform != "win32":
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
        configured = kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not configured:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self._kernel32 = kernel32
        self._handle = handle

    def assign_launcher(self, pid: int) -> None:
        if sys.platform != "win32":
            return
        process_handle = self._open_process(pid)
        try:
            self._assign_handle(process_handle)
        finally:
            assert self._kernel32 is not None
            self._kernel32.CloseHandle(process_handle)
        self.launcher_assigned = True

    def confirm_target(self, pid: int) -> None:
        if sys.platform != "win32":
            return
        if self._target_handle is not None:
            raise OSError("The parser target is already established.")
        process_handle = self._open_process(pid)
        if not self._contains_handle(process_handle):
            assert self._kernel32 is not None
            self._kernel32.CloseHandle(process_handle)
            raise OSError("The parser target did not join the owned job.")
        self._target_handle = process_handle
        self._target_pid = pid
        self.assigned = True

    def terminate(self) -> None:
        if sys.platform != "win32":
            return
        if self._kernel32 is None or self._handle is None:
            raise OSError("The parser job object is unavailable.")
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def wait_target(self, timeout_seconds: float) -> bool:
        if sys.platform != "win32":
            return True
        if self._kernel32 is None or self._target_handle is None:
            return False
        timeout_ms = max(0, min(0xFFFFFFFE, math.ceil(timeout_seconds * 1_000)))
        return bool(
            self._kernel32.WaitForSingleObject(self._target_handle, timeout_ms) == _WAIT_OBJECT_0
        )

    def wait_empty(self, timeout_seconds: float) -> bool:
        if sys.platform != "win32":
            return True
        expires = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            if self._active_process_count() == 0:
                return True
            remaining = expires - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))

    def target_exit_code(self) -> int | None:
        if sys.platform != "win32" or self._kernel32 is None or self._target_handle is None:
            return None
        value = ctypes.c_uint32()
        if not self._kernel32.GetExitCodeProcess(self._target_handle, ctypes.byref(value)):
            return None
        return int(value.value)

    def _open_process(self, pid: int) -> Any:
        if self._kernel32 is None:
            raise OSError("The parser job object is unavailable.")
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
            raise OSError("The parser job object is unavailable.")
        if self._contains_handle(process_handle):
            return
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        if not self._contains_handle(process_handle):
            raise OSError("The parser process did not join the owned job.")

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
            raise OSError("The parser job object is unavailable.")
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

    def close(self) -> None:
        target_handle = self._target_handle
        self._target_handle = None
        if target_handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(target_handle)
        handle = self._handle
        self._handle = None
        if handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(handle)


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
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _self_assign_windows_job(name: str) -> bool:
    if sys.platform != "win32":
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


ParserChildTarget = Callable[[Connection, DocumentExtractionRequest], None]


def _send_parser_message(connection: Connection, value: dict[str, Any]) -> None:
    payload = canonical_json(value).encode("utf-8")
    if len(payload) > PARSER_RESULT_MESSAGE_LIMIT_BYTES:
        raise ValueError("The parser IPC result exceeded its fixed bound.")
    connection.send_bytes(payload)


def _parser_process_work(
    result_connection: Connection,
    request: DocumentExtractionRequest,
) -> None:
    """Run adapter work after the bootstrap has proven process ownership."""

    try:

        def report_progress(stage: str, value: int) -> None:
            _send_parser_message(
                result_connection,
                {"type": "progress", "stage": stage, "progress": value},
            )

        result = adapter_for(request.declared_format).extract(
            request,
            cancelled=lambda: False,
            progress=report_progress,
        )
        try:
            _send_parser_message(
                result_connection,
                {"type": "result", "result": asdict(result)},
            )
        except ValueError:
            _send_parser_message(
                result_connection,
                {
                    "type": "error",
                    "status_code": 422,
                    "code": "PARSER_RESULT_LIMIT",
                    "retryable": False,
                },
            )
    except ServiceError as exc:
        code = exc.code if _SAFE_ERROR_CODE.fullmatch(exc.code) else "EXTRACTION_FAILED"
        status_code = exc.status_code if 400 <= exc.status_code <= 599 else 500
        try:
            _send_parser_message(
                result_connection,
                {
                    "type": "error",
                    "status_code": status_code,
                    "code": code,
                    "retryable": exc.retryable,
                },
            )
        except (BrokenPipeError, EOFError, OSError, ValueError):
            pass
    except Exception:
        try:
            _send_parser_message(
                result_connection,
                {
                    "type": "error",
                    "status_code": 500,
                    "code": "EXTRACTION_FAILED",
                    "retryable": True,
                },
            )
        except (BrokenPipeError, EOFError, OSError, ValueError):
            pass
    finally:
        result_connection.close()


def _parser_process_bootstrap(
    result_connection: Connection,
    start_connection: Connection,
    ownership_connection: Connection,
    request: DocumentExtractionRequest,
    job_object_name: str | None,
    child_target: ParserChildTarget,
) -> None:
    """Self-bind the actual frozen target before allowing any untrusted parse."""

    assigned = False
    try:
        assigned = (
            _self_assign_windows_job(job_object_name)
            if sys.platform == "win32" and job_object_name is not None
            else False
        )
        _send_parser_message(
            ownership_connection,
            {
                "type": "ready",
                "pid": os.getpid(),
                "job_object_assigned": assigned,
            },
        )
    except Exception:
        return
    finally:
        ownership_connection.close()
    if sys.platform == "win32" and not assigned:
        result_connection.close()
        start_connection.close()
        return
    try:
        start_message = start_connection.recv_bytes(_PARSER_CONTROL_MESSAGE_LIMIT_BYTES)
    except (EOFError, OSError):
        result_connection.close()
        return
    finally:
        start_connection.close()
    if start_message != b"\x01":
        result_connection.close()
        return
    child_target(result_connection, request)


class SpawnedDocumentExtractionRunner:
    """Run one untrusted parser attempt in one owned, bounded spawned process."""

    def __init__(
        self,
        *,
        poll_seconds: float,
        child_target: ParserChildTarget = _parser_process_work,
    ) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._poll_seconds = min(max(poll_seconds, 0.005), _PARSER_POLL_MAX_SECONDS)
        self._child_target = child_target
        self._state_lock = threading.Lock()
        self._active_pid: int | None = None
        self._last_evidence: ParserProcessEvidence | None = None

    @property
    def active_pid(self) -> int | None:
        with self._state_lock:
            return self._active_pid

    @property
    def last_evidence(self) -> ParserProcessEvidence | None:
        with self._state_lock:
            return self._last_evidence

    def run(
        self,
        request: DocumentExtractionRequest,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[str, int], None],
    ) -> DocumentExtractionResult:
        if (
            not math.isfinite(request.deadline_seconds)
            or not 0.1 <= request.deadline_seconds <= PARSER_DEADLINE_SECONDS
        ):
            raise ServiceError(
                400,
                "PARSER_DEADLINE_INVALID",
                "The extraction deadline is outside the supported range.",
            )
        if cancelled():
            raise ServiceError(409, "EXTRACTION_CANCELLED", "Document extraction was cancelled.")

        result_receive, result_send = self._context.Pipe(duplex=False)
        start_receive, start_send = self._context.Pipe(duplex=False)
        ownership_receive, ownership_send = self._context.Pipe(duplex=False)
        try:
            job_object = (
                _WindowsJobObject(memory_limit_bytes=PARSER_PROCESS_MEMORY_LIMIT_BYTES)
                if sys.platform == "win32"
                else None
            )
        except OSError as exc:
            for connection in (
                result_receive,
                result_send,
                start_receive,
                start_send,
                ownership_receive,
                ownership_send,
            ):
                connection.close()
            raise ServiceError(
                503,
                "PARSER_PROCESS_OWNERSHIP_FAILED",
                "The parser process could not be owned safely.",
                retryable=True,
            ) from exc
        process = self._context.Process(
            target=_parser_process_bootstrap,
            args=(
                result_send,
                start_receive,
                ownership_send,
                request,
                job_object.name if job_object is not None else None,
                self._child_target,
            ),
            name="cinematic-story-parser",
            daemon=True,
        )
        started = time.monotonic()
        expires = started + request.deadline_seconds
        launcher_pid = 0
        target_pid = 0
        terminal: _ResultEnvelope | _ErrorEnvelope | None = None
        try:
            try:
                process.start()
                launcher_pid = process.pid or 0
                if launcher_pid <= 0:
                    raise OSError("The parser process did not expose an owned PID.")
                result_send.close()
                start_receive.close()
                ownership_send.close()
                if job_object is not None:
                    job_object.assign_launcher(launcher_pid)

                while target_pid <= 0:
                    if cancelled():
                        raise ServiceError(
                            409,
                            "EXTRACTION_CANCELLED",
                            "Document extraction was cancelled.",
                        )
                    remaining = expires - time.monotonic()
                    if remaining <= 0:
                        raise ServiceError(
                            422,
                            "PARSER_TIMEOUT",
                            "Document extraction exceeded its bounded deadline.",
                            retryable=True,
                        )
                    if ownership_receive.poll(min(self._poll_seconds, remaining)):
                        ready_payload = ownership_receive.recv_bytes(
                            _PARSER_OWNERSHIP_MESSAGE_LIMIT_BYTES
                        )
                        ready = _ReadyEnvelope.model_validate_json(
                            ready_payload,
                            strict=True,
                        )
                        target_pid = ready.pid
                        if job_object is not None:
                            if not ready.job_object_assigned:
                                raise OSError("The parser target rejected job ownership.")
                            job_object.confirm_target(target_pid)
                        if cancelled():
                            raise ServiceError(
                                409,
                                "EXTRACTION_CANCELLED",
                                "Document extraction was cancelled.",
                            )
                        if time.monotonic() >= expires:
                            raise ServiceError(
                                422,
                                "PARSER_TIMEOUT",
                                "Document extraction exceeded its bounded deadline.",
                                retryable=True,
                            )
                        break
                    if not process.is_alive():
                        raise OSError("The parser target stopped during ownership handshake.")

                with self._state_lock:
                    self._active_pid = target_pid
                    self._last_evidence = None
                start_send.send_bytes(b"\x01")
                start_send.close()
            except Exception as exc:
                reason: ParserProcessReason = (
                    "cancelled"
                    if isinstance(exc, ServiceError) and exc.code == "EXTRACTION_CANCELLED"
                    else (
                        "deadline"
                        if isinstance(exc, ServiceError) and exc.code == "PARSER_TIMEOUT"
                        else "ownership_error"
                    )
                )
                self._terminate_and_record(
                    process,
                    pid=target_pid,
                    launcher_pid=launcher_pid,
                    reason=reason,
                    started=started,
                    job_object=job_object,
                )
                if isinstance(exc, ServiceError):
                    raise
                raise ServiceError(
                    503,
                    "PARSER_PROCESS_OWNERSHIP_FAILED",
                    "The parser process could not be owned safely.",
                    retryable=True,
                ) from exc

            while terminal is None:
                if cancelled():
                    self._terminate_and_record(
                        process,
                        pid=target_pid,
                        launcher_pid=launcher_pid,
                        reason="cancelled",
                        started=started,
                        job_object=job_object,
                    )
                    raise ServiceError(
                        409,
                        "EXTRACTION_CANCELLED",
                        "Document extraction was cancelled.",
                    )
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    self._terminate_and_record(
                        process,
                        pid=target_pid,
                        launcher_pid=launcher_pid,
                        reason="deadline",
                        started=started,
                        job_object=job_object,
                    )
                    raise ServiceError(
                        422,
                        "PARSER_TIMEOUT",
                        "Document extraction exceeded its bounded deadline.",
                        retryable=True,
                    )

                if result_receive.poll(min(self._poll_seconds, remaining)):
                    try:
                        payload = result_receive.recv_bytes(PARSER_RESULT_MESSAGE_LIMIT_BYTES)
                        envelope = _PARSER_ENVELOPE_ADAPTER.validate_json(payload, strict=True)
                    except (EOFError, OSError, ValidationError, ValueError) as exc:
                        self._terminate_and_record(
                            process,
                            pid=target_pid,
                            launcher_pid=launcher_pid,
                            reason="protocol_error",
                            started=started,
                            job_object=job_object,
                        )
                        raise ServiceError(
                            500,
                            "PARSER_PROCESS_PROTOCOL_INVALID",
                            "The parser process returned an invalid result.",
                            retryable=True,
                        ) from exc
                    if cancelled():
                        self._terminate_and_record(
                            process,
                            pid=target_pid,
                            launcher_pid=launcher_pid,
                            reason="cancelled",
                            started=started,
                            job_object=job_object,
                        )
                        raise ServiceError(
                            409,
                            "EXTRACTION_CANCELLED",
                            "Document extraction was cancelled.",
                        )
                    if time.monotonic() >= expires:
                        self._terminate_and_record(
                            process,
                            pid=target_pid,
                            launcher_pid=launcher_pid,
                            reason="deadline",
                            started=started,
                            job_object=job_object,
                        )
                        raise ServiceError(
                            422,
                            "PARSER_TIMEOUT",
                            "Document extraction exceeded its bounded deadline.",
                            retryable=True,
                        )
                    if isinstance(envelope, _ProgressEnvelope):
                        progress(envelope.stage, envelope.progress)
                    else:
                        terminal = envelope
                    continue

                if not process.is_alive():
                    self._record_exited_process(
                        process,
                        pid=target_pid,
                        launcher_pid=launcher_pid,
                        reason="process_error",
                        started=started,
                        job_object=job_object,
                    )
                    raise ServiceError(
                        500,
                        "PARSER_PROCESS_FAILED",
                        "The parser process stopped before returning a result.",
                        retryable=True,
                    )

            if time.monotonic() >= expires:
                self._terminate_and_record(
                    process,
                    pid=target_pid,
                    launcher_pid=launcher_pid,
                    reason="deadline",
                    started=started,
                    job_object=job_object,
                )
                raise ServiceError(
                    422,
                    "PARSER_TIMEOUT",
                    "Document extraction exceeded its bounded deadline.",
                    retryable=True,
                )
            result = terminal.result if isinstance(terminal, _ResultEnvelope) else None
            self._join_terminal_process(
                process,
                pid=target_pid,
                launcher_pid=launcher_pid,
                reason="succeeded" if isinstance(terminal, _ResultEnvelope) else "parser_error",
                started=started,
                expires=expires,
                job_object=job_object,
                cancelled=cancelled,
                validate_result=(
                    (lambda: self._validate_result(result, request)) if result is not None else None
                ),
            )

            if isinstance(terminal, _ErrorEnvelope):
                raise ServiceError(
                    terminal.status_code,
                    terminal.code,
                    "Document extraction could not be completed safely.",
                    retryable=terminal.retryable,
                )
            assert result is not None
            return result
        except ServiceError:
            raise
        except Exception as exc:
            if process.is_alive():
                self._terminate_and_record(
                    process,
                    pid=target_pid,
                    launcher_pid=launcher_pid,
                    reason="protocol_error",
                    started=started,
                    job_object=job_object,
                )
            raise ServiceError(
                500,
                "PARSER_PROCESS_FAILED",
                "The parser process stopped before returning a result.",
                retryable=True,
            ) from exc
        finally:
            for connection in (
                result_receive,
                result_send,
                start_receive,
                start_send,
                ownership_receive,
                ownership_send,
            ):
                try:
                    connection.close()
                except (OSError, ValueError):
                    pass
            if job_object is not None:
                job_object.close()
            with self._state_lock:
                self._active_pid = None

    def _terminate_and_record(
        self,
        process: BaseProcess,
        *,
        pid: int,
        launcher_pid: int,
        reason: ParserProcessReason,
        started: float,
        job_object: _WindowsJobObject | None,
    ) -> None:
        terminated = False
        process_started = process.pid is not None
        launcher_was_alive = process_started and process.is_alive()
        launcher_terminated = False
        if job_object is not None and (
            process_started or job_object.launcher_assigned or job_object.assigned
        ):
            try:
                job_object.terminate()
                terminated = True
                launcher_terminated = launcher_was_alive
            except OSError:
                pass
            if process_started:
                process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)
        elif process_started and process.is_alive():
            terminated = True
            launcher_terminated = True
            process.terminate()
            process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)
        if process_started and process.is_alive():
            terminated = True
            launcher_terminated = True
            process.kill()
            process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)
        owned_processes_confirmed = self._owned_processes_confirmed_exited(
            process,
            job_object=job_object,
            timeout_seconds=_PARSER_PROCESS_EXIT_GRACE_SECONDS,
        )
        confirmed = (not process_started or not process.is_alive()) and owned_processes_confirmed
        if not confirmed and job_object is not None:
            try:
                job_object.terminate()
                terminated = True
                launcher_terminated = launcher_terminated or (
                    process_started and process.is_alive()
                )
            except OSError:
                pass
            if process_started:
                process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)
            owned_processes_confirmed = self._owned_processes_confirmed_exited(
                process,
                job_object=job_object,
                timeout_seconds=_PARSER_PROCESS_EXIT_GRACE_SECONDS,
            )
            confirmed = (
                not process_started or not process.is_alive()
            ) and owned_processes_confirmed
        self._save_evidence(
            process,
            pid=pid,
            launcher_pid=launcher_pid,
            reason=reason,
            terminated_by_parent=terminated,
            launcher_terminated_by_parent=launcher_terminated,
            confirmed_exited=confirmed,
            owned_processes_confirmed_exited=owned_processes_confirmed,
            started=started,
            job_object=job_object,
        )
        if not confirmed:
            raise ServiceError(
                503,
                "PARSER_PROCESS_TERMINATION_FAILED",
                "The parser process did not terminate safely.",
                retryable=True,
            )
        self._close_process(process)

    def _record_exited_process(
        self,
        process: BaseProcess,
        *,
        pid: int,
        launcher_pid: int,
        reason: ParserProcessReason,
        started: float,
        job_object: _WindowsJobObject | None,
    ) -> None:
        process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)
        owned_processes_confirmed = self._owned_processes_confirmed_exited(
            process,
            job_object=job_object,
            timeout_seconds=0,
        )
        if not owned_processes_confirmed:
            self._terminate_and_record(
                process,
                pid=pid,
                launcher_pid=launcher_pid,
                reason=reason,
                started=started,
                job_object=job_object,
            )
            return
        confirmed = not process.is_alive()
        self._save_evidence(
            process,
            pid=pid,
            launcher_pid=launcher_pid,
            reason=reason,
            terminated_by_parent=False,
            launcher_terminated_by_parent=False,
            confirmed_exited=confirmed,
            owned_processes_confirmed_exited=owned_processes_confirmed,
            started=started,
            job_object=job_object,
        )
        if confirmed:
            self._close_process(process)

    def _join_terminal_process(
        self,
        process: BaseProcess,
        *,
        pid: int,
        launcher_pid: int,
        reason: ParserProcessReason,
        started: float,
        expires: float,
        job_object: _WindowsJobObject | None,
        cancelled: Callable[[], bool],
        validate_result: Callable[[], None] | None,
    ) -> None:
        target_confirmed = False
        while not target_confirmed:
            if cancelled():
                self._terminate_and_record(
                    process,
                    pid=pid,
                    launcher_pid=launcher_pid,
                    reason="cancelled",
                    started=started,
                    job_object=job_object,
                )
                raise ServiceError(
                    409,
                    "EXTRACTION_CANCELLED",
                    "Document extraction was cancelled.",
                )
            remaining = expires - time.monotonic()
            if remaining <= 0:
                self._terminate_and_record(
                    process,
                    pid=pid,
                    launcher_pid=launcher_pid,
                    reason="deadline",
                    started=started,
                    job_object=job_object,
                )
                raise ServiceError(
                    422,
                    "PARSER_TIMEOUT",
                    "Document extraction exceeded its bounded deadline.",
                    retryable=True,
                )
            wait_seconds = min(self._poll_seconds, remaining)
            if job_object is not None and job_object.assigned:
                target_confirmed = job_object.wait_target(wait_seconds)
            else:
                process.join(wait_seconds)
                target_confirmed = not process.is_alive()

        if cancelled():
            self._terminate_and_record(
                process,
                pid=pid,
                launcher_pid=launcher_pid,
                reason="cancelled",
                started=started,
                job_object=job_object,
            )
            raise ServiceError(
                409,
                "EXTRACTION_CANCELLED",
                "Document extraction was cancelled.",
            )

        target_exit_code = (
            job_object.target_exit_code()
            if job_object is not None and job_object.assigned
            else process.exitcode
        )
        launcher_terminated = False
        process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)
        if process.pid is not None and process.is_alive():
            launcher_terminated = True
            process.terminate()
            process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)
        if process.pid is not None and process.is_alive():
            launcher_terminated = True
            process.kill()
            process.join(_PARSER_PROCESS_EXIT_GRACE_SECONDS)

        owned_processes_confirmed = self._owned_processes_confirmed_exited(
            process,
            job_object=job_object,
            timeout_seconds=_PARSER_PROCESS_EXIT_GRACE_SECONDS,
        )
        if process.is_alive() or not owned_processes_confirmed:
            self._terminate_and_record(
                process,
                pid=pid,
                launcher_pid=launcher_pid,
                reason="process_error",
                started=started,
                job_object=job_object,
            )
            raise ServiceError(
                503,
                "PARSER_PROCESS_TERMINATION_FAILED",
                "The parser process did not terminate safely.",
                retryable=True,
            )

        if target_exit_code != 0:
            self._save_evidence(
                process,
                pid=pid,
                launcher_pid=launcher_pid,
                reason="process_error",
                terminated_by_parent=False,
                launcher_terminated_by_parent=launcher_terminated,
                confirmed_exited=True,
                owned_processes_confirmed_exited=True,
                started=started,
                job_object=job_object,
            )
            self._close_process(process)
            raise ServiceError(
                500,
                "PARSER_PROCESS_FAILED",
                "The parser process stopped before returning a result.",
                retryable=True,
            )

        if validate_result is not None:
            try:
                validate_result()
            except ServiceError:
                self._save_evidence(
                    process,
                    pid=pid,
                    launcher_pid=launcher_pid,
                    reason="protocol_error",
                    terminated_by_parent=False,
                    launcher_terminated_by_parent=launcher_terminated,
                    confirmed_exited=True,
                    owned_processes_confirmed_exited=True,
                    started=started,
                    job_object=job_object,
                )
                self._close_process(process)
                raise

        self._save_evidence(
            process,
            pid=pid,
            launcher_pid=launcher_pid,
            reason=reason,
            terminated_by_parent=False,
            launcher_terminated_by_parent=launcher_terminated,
            confirmed_exited=True,
            owned_processes_confirmed_exited=True,
            started=started,
            job_object=job_object,
        )
        self._close_process(process)

    @staticmethod
    def _owned_processes_confirmed_exited(
        process: BaseProcess,
        *,
        job_object: _WindowsJobObject | None,
        timeout_seconds: float,
    ) -> bool:
        if job_object is None:
            return process.pid is None or not process.is_alive()
        try:
            return job_object.wait_empty(timeout_seconds)
        except OSError:
            return False

    def _save_evidence(
        self,
        process: BaseProcess,
        *,
        pid: int,
        launcher_pid: int,
        reason: ParserProcessReason,
        terminated_by_parent: bool,
        launcher_terminated_by_parent: bool,
        confirmed_exited: bool,
        owned_processes_confirmed_exited: bool,
        started: float,
        job_object: _WindowsJobObject | None,
    ) -> None:
        evidence = ParserProcessEvidence(
            pid=pid,
            launcher_pid=launcher_pid,
            reason=reason,
            exit_code=(
                job_object.target_exit_code()
                if job_object is not None and job_object.assigned
                else process.exitcode
            ),
            terminated_by_parent=terminated_by_parent,
            launcher_terminated_by_parent=launcher_terminated_by_parent,
            confirmed_exited=confirmed_exited,
            job_object_assigned=bool(job_object is not None and job_object.assigned),
            launcher_job_object_assigned=bool(
                job_object is not None and job_object.launcher_assigned
            ),
            owned_processes_confirmed_exited=owned_processes_confirmed_exited,
            process_memory_limit_bytes=(
                job_object.memory_limit_bytes
                if job_object is not None and job_object.launcher_assigned
                else None
            ),
            duration_ms=max(0, round((time.monotonic() - started) * 1_000)),
        )
        with self._state_lock:
            self._last_evidence = evidence
        _LOGGER.info(
            "parser_process_exit pid=%d launcher_pid=%d reason=%s exit_code=%s "
            "target_terminated_by_parent=%s launcher_terminated_by_parent=%s "
            "confirmed_exited=%s job_object_assigned=%s "
            "launcher_job_object_assigned=%s owned_processes_confirmed_exited=%s "
            "memory_limit_bytes=%s",
            evidence.pid,
            evidence.launcher_pid,
            evidence.reason,
            evidence.exit_code,
            evidence.terminated_by_parent,
            evidence.launcher_terminated_by_parent,
            evidence.confirmed_exited,
            evidence.job_object_assigned,
            evidence.launcher_job_object_assigned,
            evidence.owned_processes_confirmed_exited,
            evidence.process_memory_limit_bytes,
        )

    @staticmethod
    def _close_process(process: BaseProcess) -> None:
        try:
            process.close()
        except ValueError:
            pass

    @staticmethod
    def _validate_result(
        result: DocumentExtractionResult,
        request: DocumentExtractionRequest,
    ) -> None:
        started_at = _parse_canonical_timestamp(result.started_at)
        completed_at = _parse_canonical_timestamp(result.completed_at)
        if (
            result.contract_version != request.contract_version
            or result.source_sha256 != request.source_sha256
            or result.source_byte_count != request.source_byte_count
            or result.declared_format != request.declared_format
            or result.detected_format != request.declared_format
            or not result.canonical_text
            or len(result.canonical_text) > MAX_EXTRACTED_CHARACTERS
            or sha256_text(result.canonical_text) != result.extracted_text_sha256
            or len(result.sections) > MAX_EXTRACTED_SECTIONS
            or len(result.warnings) > _MAX_WARNINGS
            or not math.isfinite(result.confidence)
            or not 0 <= result.confidence <= 1
            or (result.page_count is not None and not 1 <= result.page_count <= MAX_PDF_PAGES)
            or (result.title is not None and len(result.title) > 255)
            or not _SAFE_IDENTIFIER.fullmatch(result.adapter_id)
            or len(result.adapter_id) > 100
            or not _SAFE_VERSION.fullmatch(result.adapter_version)
            or not _SAFE_IDENTIFIER.fullmatch(result.parser_dependency)
            or len(result.parser_dependency) > 100
            or not _SAFE_VERSION.fullmatch(result.parser_version)
            or started_at is None
            or completed_at is None
            or completed_at < started_at
            or not _valid_provenance(
                result.provenance,
                actor_id=result.adapter_id,
                recorded_at=result.completed_at,
                source_sha256=result.source_sha256,
            )
        ):
            raise ServiceError(
                500,
                "PARSER_PROCESS_PROTOCOL_INVALID",
                "The parser process returned an invalid result.",
                retryable=True,
            )
        for ordinal, section in enumerate(result.sections):
            if (
                section.ordinal != ordinal
                or not 0 <= section.start <= section.end <= len(result.canonical_text)
                or (section.title is not None and len(section.title) > 255)
                or not _valid_source_location(
                    section.location.kind,
                    member=section.location.member,
                    page=section.location.page,
                    start=section.location.start,
                    end=section.location.end,
                    canonical_text_length=len(result.canonical_text),
                )
            ):
                raise ServiceError(
                    500,
                    "PARSER_PROCESS_PROTOCOL_INVALID",
                    "The parser process returned an invalid result.",
                    retryable=True,
                )
        if any(
            not _SAFE_WARNING_CODE.fullmatch(warning.code)
            or not warning.message
            or len(warning.message) > 1_000
            for warning in result.warnings
        ):
            raise ServiceError(
                500,
                "PARSER_PROCESS_PROTOCOL_INVALID",
                "The parser process returned an invalid result.",
                retryable=True,
            )
        manifest = result.manifest
        execution = result.parser_execution
        expected_profile = parser_limits_profile(request.deadline_seconds)
        if (
            not manifest.original_preserved
            or manifest.contract_version != result.contract_version
            or manifest.source_sha256 != result.source_sha256
            or manifest.source_byte_count != result.source_byte_count
            or manifest.declared_format != result.declared_format
            or manifest.detected_format != result.detected_format
            or manifest.media_type != result.media_type
            or manifest.extracted_text_sha256 != result.extracted_text_sha256
            or manifest.extracted_character_count != len(result.canonical_text)
            or manifest.section_count != len(result.sections)
            or manifest.page_count != result.page_count
            or execution.contract_version != result.contract_version
            or execution.adapter_id != result.adapter_id
            or execution.adapter_version != result.adapter_version
            or execution.parser_dependency != result.parser_dependency
            or execution.parser_version != result.parser_version
            or execution.started_at != result.started_at
            or execution.completed_at != result.completed_at
            or execution.duration_ms < 0
            or execution.duration_ms > math.ceil(request.deadline_seconds * 1_000)
            or execution.retryability != result.retryability
            or execution.network_access_permitted is not False
            or execution.status != result.status
            or execution.limits_profile != expected_profile
            or execution.limits_fingerprint != parser_limits_fingerprint(request.deadline_seconds)
        ):
            raise ServiceError(
                500,
                "PARSER_PROCESS_PROTOCOL_INVALID",
                "The parser process returned an invalid result.",
                retryable=True,
            )


def _parse_canonical_timestamp(value: str) -> datetime | None:
    if not _CANONICAL_TIMESTAMP.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo == UTC else None


def _valid_provenance(
    value: dict[str, Any],
    *,
    actor_id: str,
    recorded_at: str,
    source_sha256: str,
) -> bool:
    if (
        set(value)
        - {
            "contractVersion",
            "origin",
            "actorId",
            "recordedAt",
            "inputFingerprint",
            "sourceReferences",
            "notes",
        }
        or value.get("contractVersion") != "1.0.0"
        or value.get("origin") not in {"import", "human", "system"}
        or value.get("actorId") != actor_id
        or not isinstance(value.get("actorId"), str)
        or not _SAFE_IDENTIFIER.fullmatch(value["actorId"])
        or value.get("recordedAt") != recorded_at
        or _parse_canonical_timestamp(recorded_at) is None
        or value.get("inputFingerprint") != source_sha256
    ):
        return False
    notes = value.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 2_000):
        return False
    references = value.get("sourceReferences")
    if references is not None:
        if not isinstance(references, list) or len(references) > 256:
            return False
        for reference in references:
            if not isinstance(reference, dict) or set(reference) - {
                "entityType",
                "entityId",
                "revision",
            }:
                return False
            entity_type = reference.get("entityType")
            entity_id = reference.get("entityId")
            revision = reference.get("revision")
            if (
                not isinstance(entity_type, str)
                or not 1 <= len(entity_type) <= 80
                or not isinstance(entity_id, str)
                or not _SAFE_IDENTIFIER.fullmatch(entity_id)
                or (
                    revision is not None
                    and (
                        not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
                    )
                )
            ):
                return False
    if not _bounded_finite_json(value):
        return False
    try:
        return len(canonical_json(value).encode("utf-8")) <= _MAX_PROVENANCE_BYTES
    except (TypeError, ValueError):
        return False


def _bounded_finite_json(value: Any) -> bool:
    nodes = 0
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            return False
        if current is None or isinstance(current, (str, bool, int)):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                return False
            continue
        if isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                return False
            pending.extend((item, depth + 1) for item in current.values())
            continue
        return False
    return True


def _valid_source_location(
    kind: str,
    *,
    member: str | None,
    page: int | None,
    start: int | None,
    end: int | None,
    canonical_text_length: int,
) -> bool:
    if kind == "text":
        return (
            member is None
            and page is None
            and start is not None
            and end is not None
            and 0 <= start <= end <= canonical_text_length
        )
    if kind == "package_part":
        if (
            member is None
            or page is not None
            or start is not None
            or end is not None
            or not member
            or len(member) > MAX_ARCHIVE_MEMBER_NAME_CHARACTERS
            or any(ord(character) < 32 or ord(character) == 127 for character in member)
            or "\\" in member
            or member.startswith("/")
            or re.match(r"^[A-Za-z]:", member)
        ):
            return False
        path = PurePosixPath(member)
        return (
            len(path.parts) <= 20
            and all(part not in {"", ".", ".."} for part in path.parts)
            and path.as_posix().rstrip("/") == member
        )
    if kind == "pdf_page":
        return (
            member is None
            and start is None
            and end is None
            and page is not None
            and 1 <= page <= MAX_PDF_PAGES
        )
    return False
