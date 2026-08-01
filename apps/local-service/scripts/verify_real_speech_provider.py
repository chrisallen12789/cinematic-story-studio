from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from cinematic_story_service.auditions import (
    MAX_AUDITION_AUDIO_BYTES,
    AuditionAudioQc,
    inspect_audition_wav,
)
from cinematic_story_service.local_speech import (
    SpeechArtifact,
    SpeechInvocationContext,
    SpeechPronunciationOverrideSpan,
    SpeechSynthesisRequest,
)
from cinematic_story_service.model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    ModelPackageManifest,
    ModelPackageVerification,
    verify_model_package_path,
)
from cinematic_story_service.speech_runtime import (
    SPEECH_RUNTIME_PROTOCOL_VERSION,
    ManagedSpeechRuntime,
    SpeechRuntimeConfig,
    SpeechRuntimeExitEvidence,
    SpeechWorkerIdentity,
)
from cinematic_story_service.util import canonical_json, sha256_text, utc_now

VERIFICATION_SCHEMA_VERSION = "1.0.0"
FIXED_MODEL_DIRECTORY = Path("local-models") / "kokoro-phase3b"
FIXED_OUTPUT_DIRECTORY = Path("local-renders") / "phase3b-real-provider"
WAV_FILENAME = "kokoro-synthetic-audition.wav"
EVIDENCE_FILENAME = "kokoro-synthetic-audition-evidence.json"

# This short phrase is repository-owned synthetic test material. The command accepts no text.
SYNTHETIC_PHRASE = "A restricted local voice."
SYNTHETIC_OVERRIDE = SpeechPronunciationOverrideSpan(
    source_start=2,
    source_end=12,
    grapheme="restricted",
    pronunciation="restricted",
    representation="neutral",
    entry_id="phase3b-verification-pronunciation-restricted",
    entry_revision=1,
)

STARTUP_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 180.0
IDLE_TIMEOUT_SECONDS = 180.0
MAX_EVIDENCE_BYTES = 128 * 1024
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_RECONCILIATION_ENTRIES = 1_024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_RUN_DIRECTORY = re.compile(r"^run-\d{8}T\d{9}Z-[0-9a-f]{32}$")
_STAGING_DIRECTORY = re.compile(r"^\.run-\d{8}T\d{9}Z-[0-9a-f]{32}\.tmp$")
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(slots=True)
class VerificationFailure(Exception):
    code: str
    message: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    evidence: dict[str, object]
    evidence_path: str
    evidence_sha256: str
    wav_path: str


class SpeechRuntime(Protocol):
    @property
    def identity(self) -> SpeechWorkerIdentity | None: ...

    @property
    def last_exit(self) -> SpeechRuntimeExitEvidence | None: ...

    @property
    def is_running(self) -> bool: ...

    def start(self) -> SpeechWorkerIdentity: ...

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> SpeechArtifact: ...

    def stop(
        self,
        *,
        reason: Literal["clean", "idle"] = "clean",
    ) -> SpeechRuntimeExitEvidence | None: ...


RuntimeFactory = Callable[[SpeechRuntimeConfig], SpeechRuntime]
ModelVerifier = Callable[[Path, ModelPackageManifest], ModelPackageVerification]
PlatformVerifier = Callable[[], None]
TimestampFactory = Callable[[], str]
NonceFactory = Callable[[], str]
FileHasher = Callable[[Path], tuple[int, str]]


def _create_managed_runtime(config: SpeechRuntimeConfig) -> SpeechRuntime:
    return ManagedSpeechRuntime(config)


def _new_nonce() -> str:
    return uuid4().hex


def _repository_root() -> Path:
    return Path(__file__).absolute().parents[3]


def _is_reparse_or_link(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
    )


def _require_ordinary_directory(path: Path, *, code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise VerificationFailure(code, "A required fixed local directory is unavailable.") from exc
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_or_link(path):
        raise VerificationFailure(code, "A required fixed local directory is unsafe.")


def _require_ordinary_file(path: Path, *, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise VerificationFailure(code, "A required local file is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode) or _is_reparse_or_link(path):
        raise VerificationFailure(code, "A required local file is unsafe.")
    return metadata


def _assert_supported_platform() -> None:
    if sys.platform != "win32" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise VerificationFailure(
            "UNSUPPORTED_VERIFICATION_PLATFORM",
            "The allow-listed real-provider verification requires Windows x64.",
        )


def _fixed_model_path(repository_root: Path) -> Path:
    root = repository_root.absolute()
    local_models = root / FIXED_MODEL_DIRECTORY.parent
    package_path = root / FIXED_MODEL_DIRECTORY
    _require_ordinary_directory(root, code="REPOSITORY_ROOT_UNSAFE")
    _require_ordinary_directory(local_models, code="MODEL_STORAGE_UNSAFE")
    _require_ordinary_directory(package_path, code="MODEL_PACKAGE_MISSING")
    try:
        package_path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationFailure(
            "MODEL_PACKAGE_PATH_INVALID",
            "The fixed model package escaped the repository's ignored local storage.",
        ) from exc
    return package_path


def _private_output_root(repository_root: Path) -> Path:
    root = repository_root.absolute()
    local_renders = root / FIXED_OUTPUT_DIRECTORY.parent
    output_root = root / FIXED_OUTPUT_DIRECTORY
    _require_ordinary_directory(root, code="REPOSITORY_ROOT_UNSAFE")
    try:
        local_renders.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise VerificationFailure(
            "OUTPUT_DIRECTORY_UNAVAILABLE",
            "The fixed ignored output directory could not be created.",
        ) from exc
    _require_ordinary_directory(local_renders, code="OUTPUT_DIRECTORY_UNSAFE")
    try:
        output_root.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise VerificationFailure(
            "OUTPUT_DIRECTORY_UNAVAILABLE",
            "The fixed ignored output directory could not be created.",
        ) from exc
    _require_ordinary_directory(output_root, code="OUTPUT_DIRECTORY_UNSAFE")
    try:
        output_root.resolve(strict=True).relative_to(root.resolve(strict=True))
        os.chmod(output_root, 0o700)
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationFailure(
            "OUTPUT_DIRECTORY_UNSAFE",
            "The fixed ignored output directory was unsafe.",
        ) from exc
    return output_root


def _verify_exact_model_package(
    package_path: Path,
    verifier: ModelVerifier,
) -> ModelPackageVerification:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    verification = verifier(package_path, manifest)
    expected_files = tuple(sorted(artifact.path for artifact in manifest.artifacts))
    if (
        not verification.valid
        or verification.error_codes
        or verification.package_id != manifest.package_id
        or verification.package_version != manifest.package_version
        or verification.package_path.absolute() != package_path.absolute()
        or verification.manifest_fingerprint != manifest.fingerprint
        or tuple(sorted(verification.verified_files)) != expected_files
        or verification.total_size_bytes != manifest.total_size_bytes
    ):
        raise VerificationFailure(
            "MODEL_PACKAGE_VERIFICATION_FAILED",
            "The fixed local model package did not match the exact allow-listed manifest.",
        )
    return verification


def _request() -> SpeechSynthesisRequest:
    return SpeechSynthesisRequest(
        request_id="phase3b-real-provider-verification",
        text=SYNTHETIC_PHRASE,
        voice_id=KOKORO_LOCAL_ONNX_MANIFEST.voice_id,
        pronunciation_overrides=(SYNTHETIC_OVERRIDE,),
    )


def _pronunciation_dictionary_fingerprint() -> str:
    return sha256_text(
        canonical_json(
            {
                "dictionaryVersion": "phase3b-real-provider-verification-v1",
                "entries": [SYNTHETIC_OVERRIDE.fingerprint_material()],
                "scope": "fixed-repository-owned-synthetic-phrase",
            }
        )
    )


def _runtime_config(package_path: Path) -> SpeechRuntimeConfig:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    return SpeechRuntimeConfig(
        provider_id=manifest.provider_id,
        runtime_id=manifest.runtime_id,
        runtime_version=manifest.runtime_version,
        model_id=manifest.model_id,
        model_version=manifest.model_version,
        model_manifest_fingerprint=manifest.fingerprint,
        model_package_path=package_path,
        python_executable=Path(sys.executable),
        startup_timeout_seconds=STARTUP_TIMEOUT_SECONDS,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        idle_timeout_seconds=IDLE_TIMEOUT_SECONDS,
        max_retries=0,
        launch_mode="python-module",
    )


def _context(monotonic: Callable[[], float]) -> SpeechInvocationContext:
    return SpeechInvocationContext(
        correlation_id="phase3b-real-provider-verification",
        job_id="phase3b-real-provider-verification",
        attempt_id="phase3b-real-provider-verification-attempt-1",
        idempotency_key="phase3b-real-provider-verification-v1",
        deadline_monotonic=monotonic() + REQUEST_TIMEOUT_SECONDS,
        invocation_purpose="component_verification",
        restricted_voice_acknowledged=True,
        network_access_permitted=False,
    )


def _validate_worker_identity(identity: SpeechWorkerIdentity) -> None:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    if (
        identity.pid <= 0
        or identity.parent_pid != os.getpid()
        or identity.launcher_pid <= 0
        or identity.process_parent_pid <= 0
        or identity.created_at_unix_ns <= 0
        or identity.protocol_version != SPEECH_RUNTIME_PROTOCOL_VERSION
        or identity.provider_id != manifest.provider_id
        or identity.runtime_id != manifest.runtime_id
        or identity.runtime_version != manifest.runtime_version
        or identity.model_id != manifest.model_id
        or identity.model_version != manifest.model_version
        or identity.model_manifest_fingerprint != manifest.fingerprint
        or identity.launch_mode != "python-module"
        or not identity.job_object_assigned
        or identity.denied_network_attempt_count != 0
    ):
        raise VerificationFailure(
            "WORKER_IDENTITY_NOT_PROVEN",
            "The managed speech worker did not establish the exact authenticated identity.",
        )
    try:
        if identity.executable.resolve(strict=True) != Path(sys.executable).resolve(strict=True):
            raise VerificationFailure(
                "WORKER_EXECUTABLE_MISMATCH",
                "The authenticated worker executable did not match this managed runtime.",
            )
    except OSError as exc:
        raise VerificationFailure(
            "WORKER_EXECUTABLE_MISMATCH",
            "The authenticated worker executable could not be verified.",
        ) from exc


def _validate_exit(
    identity: SpeechWorkerIdentity,
    evidence: SpeechRuntimeExitEvidence | None,
    *,
    require_clean: bool,
) -> SpeechRuntimeExitEvidence:
    if (
        evidence is None
        or evidence.pid != identity.pid
        or evidence.launcher_pid != identity.launcher_pid
        or (require_clean and evidence.reason != "clean")
        or (require_clean and evidence.exit_code != 0)
        or not evidence.ownership_confirmed
        or not evidence.shutdown_acknowledged
        or not evidence.graceful_shutdown_confirmed
        or evidence.terminated_by_parent
        or not evidence.confirmed_exited
        or not evidence.job_object_assigned
        or not evidence.owned_processes_confirmed_exited
        or evidence.denied_network_attempt_count != 0
    ):
        raise VerificationFailure(
            "WORKER_EXIT_NOT_PROVEN",
            "Exact owned speech-worker shutdown was not proven.",
        )
    return evidence


def _run_managed_synthesis(
    runtime: SpeechRuntime,
    request: SpeechSynthesisRequest,
    context: SpeechInvocationContext,
) -> tuple[SpeechArtifact, SpeechWorkerIdentity, SpeechRuntimeExitEvidence]:
    identity: SpeechWorkerIdentity | None = None
    artifact: SpeechArtifact | None = None
    operation_error: BaseException | None = None
    try:
        identity = runtime.start()
        _validate_worker_identity(identity)
        artifact = runtime.synthesize(request, context)
    except BaseException as exc:
        operation_error = exc

    shutdown_error: BaseException | None = None
    exit_evidence: SpeechRuntimeExitEvidence | None = None
    try:
        exit_evidence = runtime.stop(reason="clean")
        if identity is not None:
            exit_evidence = _validate_exit(
                identity,
                exit_evidence,
                require_clean=operation_error is None,
            )
        if runtime.is_running:
            raise VerificationFailure(
                "WORKER_EXIT_NOT_PROVEN",
                "The managed speech worker remained active after shutdown.",
            )
    except BaseException as exc:
        shutdown_error = exc

    if shutdown_error is not None:
        raise shutdown_error from operation_error
    if operation_error is not None:
        raise operation_error
    if artifact is None or identity is None or exit_evidence is None:
        raise VerificationFailure(
            "VERIFICATION_INCOMPLETE",
            "The managed real-provider verification did not complete.",
        )
    return artifact, identity, exit_evidence


def _validate_artifact(artifact: SpeechArtifact, request: SpeechSynthesisRequest) -> None:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    model_artifact = next(value for value in manifest.artifacts if value.role == "model")
    voice_artifact = next(value for value in manifest.artifacts if value.role == "voice")
    if (
        artifact.provider_id != manifest.provider_id
        or artifact.adapter_id != manifest.provider_id
        or artifact.adapter_version != manifest.provider_version
        or artifact.runtime_id != manifest.runtime_id
        or artifact.runtime_version != manifest.runtime_version
        or artifact.model_id != manifest.model_id
        or artifact.model_version != manifest.model_version
        or artifact.model_sha256 != model_artifact.sha256
        or artifact.voice_id != manifest.voice_id
        or artifact.voice_sha256 != voice_artifact.sha256
        or artifact.input_fingerprint != request.input_fingerprint()
        or _SHA256.fullmatch(artifact.configuration_fingerprint) is None
        or artifact.deterministic
    ):
        raise VerificationFailure(
            "SPEECH_ARTIFACT_IDENTITY_INVALID",
            "The generated WAV did not preserve the exact real-provider identity.",
        )


def _hash_regular_file(path: Path, *, maximum_bytes: int) -> tuple[int, str]:
    before = _require_ordinary_file(path, code="FILE_HASH_INPUT_INVALID")
    if not 0 < before.st_size <= maximum_bytes:
        raise VerificationFailure(
            "FILE_HASH_INPUT_INVALID",
            "A verification file exceeded its fixed byte bound.",
        )
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > maximum_bytes:
                    raise VerificationFailure(
                        "FILE_HASH_INPUT_INVALID",
                        "A verification file exceeded its fixed byte bound.",
                    )
                digest.update(chunk)
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise VerificationFailure(
            "FILE_HASH_INPUT_INVALID",
            "A verification file could not be hashed safely.",
        ) from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise VerificationFailure(
            "FILE_CHANGED_DURING_HASH",
            "A verification file changed while it was being hashed.",
        )
    return total, digest.hexdigest()


def _hash_executable(path: Path) -> tuple[int, str]:
    return _hash_regular_file(path, maximum_bytes=MAX_EXECUTABLE_BYTES)


def _inventory_fingerprint(manifest: ModelPackageManifest) -> str:
    return sha256_text(
        canonical_json(
            {
                "artifacts": [
                    {
                        "path": artifact.path,
                        "sha256": artifact.sha256,
                        "sizeBytes": artifact.size_bytes,
                    }
                    for artifact in sorted(manifest.artifacts, key=lambda value: value.path)
                ]
            }
        )
    )


def _write_exclusive(path: Path, value: bytes, *, maximum_bytes: int) -> None:
    if not 0 < len(value) <= maximum_bytes:
        raise VerificationFailure(
            "OUTPUT_SIZE_INVALID",
            "A private verification output exceeded its fixed byte bound.",
        )
    try:
        with path.open("xb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise VerificationFailure(
            "OUTPUT_WRITE_FAILED",
            "A private verification output could not be staged atomically.",
        ) from exc


def _cleanup_owned_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_or_link(path):
        return False
    try:
        entries: list[os.DirEntry[str]] = []
        with os.scandir(path) as iterator:
            for entry in iterator:
                if len(entries) >= 2:
                    return False
                entries.append(entry)
        if any(entry.name not in {WAV_FILENAME, EVIDENCE_FILENAME} for entry in entries):
            return False
        for entry in entries:
            entry_path = Path(entry.path)
            entry_metadata = entry_path.lstat()
            if not stat.S_ISREG(entry_metadata.st_mode) or _is_reparse_or_link(entry_path):
                return False
        for entry in entries:
            Path(entry.path).unlink()
        path.rmdir()
    except OSError:
        return False
    return True


def _owned_directory_files(path: Path) -> tuple[Path, ...] | None:
    try:
        metadata = path.lstat()
    except OSError:
        return None
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_or_link(path):
        return None
    entries: list[Path] = []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                if len(entries) >= 2 or entry.name not in {
                    WAV_FILENAME,
                    EVIDENCE_FILENAME,
                }:
                    return None
                entry_path = Path(entry.path)
                entry_metadata = entry_path.lstat()
                if not stat.S_ISREG(entry_metadata.st_mode) or _is_reparse_or_link(entry_path):
                    return None
                entries.append(entry_path)
    except OSError:
        return None
    return tuple(entries)


def _read_bounded_regular_file(path: Path, *, maximum_bytes: int) -> bytes | None:
    try:
        before = _require_ordinary_file(path, code="OUTPUT_RECONCILIATION_UNSAFE")
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            return None
        with path.open("rb") as source:
            value = source.read(maximum_bytes + 1)
        after = _require_ordinary_file(path, code="OUTPUT_RECONCILIATION_UNSAFE")
    except (OSError, VerificationFailure):
        return None
    if len(value) != before.st_size or len(value) > maximum_bytes:
        return None
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        return None
    return value


def _has_owned_final_marker(
    evidence: object,
    *,
    expected_evidence_path: str,
    expected_wav_path: str,
) -> bool:
    if not isinstance(evidence, dict):
        return False
    artifact = evidence.get("artifact")
    assertions = evidence.get("assertions")
    request = evidence.get("request")
    scope = evidence.get("scope")
    return (
        evidence.get("schemaVersion") == VERIFICATION_SCHEMA_VERSION
        and evidence.get("result") == "passed"
        and evidence.get("evidencePath") == expected_evidence_path
        and isinstance(artifact, dict)
        and artifact.get("path") == expected_wav_path
        and isinstance(assertions, dict)
        and assertions.get("privateAtomicPublication") is True
        and assertions.get("restrictedLocalUseAcknowledged") is True
        and isinstance(request, dict)
        and request.get("inputTextRecorded") is False
        and request.get("inputTextSha256") == sha256_text(SYNTHETIC_PHRASE)
        and isinstance(scope, dict)
        and scope.get("inputClassification") == "fixed-repository-owned-synthetic"
        and scope.get("rawPhraseRecorded") is False
        and scope.get("restrictedLocalUseAcknowledged") is True
    )


def _valid_owned_final(
    directory: Path,
    evidence: dict[str, object],
) -> bool:
    expected_top_level_keys = {
        "assertions",
        "artifact",
        "evidenceClassification",
        "evidencePath",
        "limitations",
        "modelPackage",
        "provider",
        "request",
        "result",
        "runnerIdentity",
        "runtimeWorker",
        "schemaVersion",
        "scope",
        "testTimestamp",
    }
    expected_assertions = {
        "authenticatedManagedWorker": True,
        "exactManifestVerified": True,
        "fixedRepositoryOwnedSyntheticInput": True,
        "networkAccessPermitted": False,
        "ownedWorkerExitProven": True,
        "privateAtomicPublication": True,
        "restrictedLocalUseAcknowledged": True,
        "validBoundedNonSilentPcmWav": True,
    }
    expected_scope = {
        "componentOnlyVerification": True,
        "governedPhase3aVoiceProfileBound": False,
        "governedRightsRecordBound": False,
        "humanListeningPerformed": False,
        "inputClassification": "fixed-repository-owned-synthetic",
        "modelDownloadedByCommand": False,
        "providerNetworkRequired": False,
        "rawPhraseRecorded": False,
        "restrictedLocalUseAcknowledged": True,
    }
    expected_artifact_keys = {
        "blockingFindingCodes",
        "byteSize",
        "channelCount",
        "clipped",
        "durationMs",
        "frameCount",
        "leadingSilenceMs",
        "nonSilentFrameCount",
        "path",
        "peakDbfs",
        "qcFingerprint",
        "rmsDbfs",
        "sampleFormat",
        "sampleRateHz",
        "sampleWidthBytes",
        "sha256",
        "trailingSilenceMs",
        "warningCodes",
    }
    expected_runtime_worker_keys = {
        "createdAtUnixNs",
        "deniedNetworkAttemptCount",
        "executable",
        "jobObjectAssigned",
        "launchMode",
        "launcherPid",
        "networkUseClassification",
        "ownerPid",
        "processParentPid",
        "protocolVersion",
        "shutdown",
        "workerPid",
    }
    expected_shutdown = {
        "confirmedExited": True,
        "exitCode": 0,
        "gracefulShutdownConfirmed": True,
        "ownedProcessesConfirmedExited": True,
        "ownershipConfirmed": True,
        "reason": "clean",
        "shutdownAcknowledged": True,
        "terminatedByParent": False,
    }
    assertions = evidence.get("assertions")
    artifact = evidence.get("artifact")
    runtime_worker = evidence.get("runtimeWorker")
    timestamp = evidence.get("testTimestamp")
    scope = evidence.get("scope")
    if (
        set(evidence) != expected_top_level_keys
        or not isinstance(assertions, dict)
        or assertions != expected_assertions
        or scope != expected_scope
        or not isinstance(artifact, dict)
        or set(artifact) != expected_artifact_keys
        or not isinstance(runtime_worker, dict)
        or set(runtime_worker) != expected_runtime_worker_keys
        or runtime_worker.get("shutdown") != expected_shutdown
        or runtime_worker.get("jobObjectAssigned") is not True
        or runtime_worker.get("deniedNetworkAttemptCount") != 0
        or not isinstance(timestamp, str)
    ):
        return False
    try:
        if directory.name != (
            f"run-{_timestamp_run_component(timestamp)}-"
            f"{directory.name.rsplit('-', maxsplit=1)[-1]}"
        ):
            return False
    except VerificationFailure:
        return False

    wav_path = directory / WAV_FILENAME
    try:
        wav_size, wav_sha256 = _hash_regular_file(
            wav_path,
            maximum_bytes=MAX_AUDITION_AUDIO_BYTES,
        )
        qc = inspect_audition_wav(wav_path, managed_root=directory)
    except (OSError, VerificationFailure, ValueError):
        return False
    measurements = qc.measurements
    expected_measurements = {
        "blockingFindingCodes": list(qc.blocking_findings),
        "byteSize": wav_size,
        "channelCount": measurements.channel_count,
        "clipped": measurements.clipped,
        "durationMs": round(measurements.duration_ms, 6),
        "frameCount": measurements.frame_count,
        "leadingSilenceMs": round(measurements.leading_silence_ms, 6),
        "nonSilentFrameCount": measurements.non_silent_frames,
        "peakDbfs": round(measurements.peak_dbfs, 6),
        "qcFingerprint": qc.fingerprint,
        "rmsDbfs": round(measurements.rms_dbfs, 6),
        "sampleFormat": "pcm_s16le",
        "sampleRateHz": measurements.sample_rate_hz,
        "sampleWidthBytes": measurements.sample_width_bytes,
        "sha256": wav_sha256,
        "trailingSilenceMs": round(measurements.trailing_silence_ms, 6),
    }
    if qc.blocking_findings or any(
        artifact.get(key) != value for key, value in expected_measurements.items()
    ):
        return False
    warning_codes = artifact.get("warningCodes")
    return (
        isinstance(warning_codes, list)
        and len(warning_codes) <= 100
        and all(isinstance(value, str) and 0 < len(value) <= 200 for value in warning_codes)
    )


def _reconcile_private_output(repository_root: Path, output_root: Path) -> None:
    try:
        names: list[str] = []
        with os.scandir(output_root) as iterator:
            for entry in iterator:
                if len(names) >= MAX_RECONCILIATION_ENTRIES:
                    raise VerificationFailure(
                        "OUTPUT_RECONCILIATION_LIMIT_EXCEEDED",
                        "The private output directory exceeded its reconciliation bound.",
                    )
                names.append(entry.name)
    except VerificationFailure:
        raise
    except OSError as exc:
        raise VerificationFailure(
            "OUTPUT_RECONCILIATION_FAILED",
            "The private output directory could not be reconciled safely.",
        ) from exc

    for name in sorted(names):
        candidate = output_root / name
        if _STAGING_DIRECTORY.fullmatch(name) is not None:
            if _owned_directory_files(candidate) is None:
                continue
            if not _cleanup_owned_directory(candidate):
                raise VerificationFailure(
                    "OUTPUT_RECONCILIATION_FAILED",
                    "An incomplete command-owned staging directory could not be removed.",
                )
            continue
        if _RUN_DIRECTORY.fullmatch(name) is None:
            continue
        owned_files = _owned_directory_files(candidate)
        if owned_files is None or {value.name for value in owned_files} != {
            WAV_FILENAME,
            EVIDENCE_FILENAME,
        }:
            continue
        evidence_bytes = _read_bounded_regular_file(
            candidate / EVIDENCE_FILENAME,
            maximum_bytes=MAX_EVIDENCE_BYTES,
        )
        if evidence_bytes is None:
            continue
        try:
            evidence = json.loads(evidence_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        expected_evidence_path = _relative_path(
            candidate / EVIDENCE_FILENAME,
            repository_root,
        )
        expected_wav_path = _relative_path(candidate / WAV_FILENAME, repository_root)
        if not _has_owned_final_marker(
            evidence,
            expected_evidence_path=expected_evidence_path,
            expected_wav_path=expected_wav_path,
        ):
            continue
        if isinstance(evidence, dict) and _valid_owned_final(
            candidate,
            evidence,
        ):
            continue
        if not _cleanup_owned_directory(candidate):
            raise VerificationFailure(
                "OUTPUT_RECONCILIATION_FAILED",
                "An invalid command-owned final directory could not be removed.",
            )


def _timestamp_run_component(timestamp: str) -> str:
    if _UTC_TIMESTAMP.fullmatch(timestamp) is None:
        raise VerificationFailure(
            "TIMESTAMP_INVALID",
            "The verification timestamp was not canonical UTC.",
        )
    return timestamp.translate(str.maketrans("", "", "-:."))


def _relative_path(path: Path, repository_root: Path) -> str:
    try:
        return path.absolute().relative_to(repository_root.absolute()).as_posix()
    except ValueError as exc:
        raise VerificationFailure(
            "OUTPUT_PATH_INVALID",
            "A private verification output escaped the repository's ignored storage.",
        ) from exc


def _build_evidence(
    *,
    timestamp: str,
    verification: ModelPackageVerification,
    request: SpeechSynthesisRequest,
    artifact: SpeechArtifact,
    qc: AuditionAudioQc,
    identity: SpeechWorkerIdentity,
    exit_evidence: SpeechRuntimeExitEvidence,
    executable_size: int,
    executable_sha256: str,
    relative_wav_path: str,
    relative_evidence_path: str,
) -> dict[str, object]:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    measurements = qc.measurements
    return {
        "assertions": {
            "authenticatedManagedWorker": True,
            "exactManifestVerified": True,
            "fixedRepositoryOwnedSyntheticInput": True,
            "networkAccessPermitted": False,
            "ownedWorkerExitProven": True,
            "privateAtomicPublication": True,
            "restrictedLocalUseAcknowledged": True,
            "validBoundedNonSilentPcmWav": True,
        },
        "artifact": {
            "blockingFindingCodes": list(qc.blocking_findings),
            "byteSize": qc.byte_size,
            "channelCount": measurements.channel_count,
            "clipped": measurements.clipped,
            "durationMs": round(measurements.duration_ms, 6),
            "frameCount": measurements.frame_count,
            "leadingSilenceMs": round(measurements.leading_silence_ms, 6),
            "nonSilentFrameCount": measurements.non_silent_frames,
            "path": relative_wav_path,
            "peakDbfs": round(measurements.peak_dbfs, 6),
            "qcFingerprint": qc.fingerprint,
            "rmsDbfs": round(measurements.rms_dbfs, 6),
            "sampleFormat": "pcm_s16le",
            "sampleRateHz": measurements.sample_rate_hz,
            "sampleWidthBytes": measurements.sample_width_bytes,
            "sha256": measurements.content_sha256,
            "trailingSilenceMs": round(measurements.trailing_silence_ms, 6),
            "warningCodes": sorted({*artifact.warnings, *qc.warnings}),
        },
        "evidenceClassification": "component_only_unbound_real_provider_verification",
        "evidencePath": relative_evidence_path,
        "limitations": [
            (
                "This component-only verification is not bound to a governed Phase 3A "
                "voice profile, cast assignment, or rights record."
            ),
            (
                "The command-line restricted-use acknowledgement is not a substitute "
                "for voice-rights, consent, or commercial-use approval."
            ),
            "Human listening was not performed by this command.",
            (
                "Signal checks do not establish intelligibility, naturalness, artistic fit, "
                "consent, commercial clearance, legal certainty, or production readiness."
            ),
            (
                "The authenticated Python socket-denial counter is defense in depth, not "
                "packet-level network capture."
            ),
        ],
        "modelPackage": {
            "artifactInventory": [
                {
                    "path": value.path,
                    "sha256": value.sha256,
                    "sizeBytes": value.size_bytes,
                }
                for value in sorted(manifest.artifacts, key=lambda item: item.path)
            ],
            "commercialUseClassification": manifest.commercial_use_classification,
            "conversionRevision": manifest.source_revision,
            "licenseId": manifest.license_id,
            "manifestSha256": manifest.fingerprint,
            "modelId": manifest.model_id,
            "modelVersion": manifest.model_version,
            "packageId": verification.package_id,
            "packageInventorySha256": _inventory_fingerprint(manifest),
            "packageVersion": verification.package_version,
            "path": FIXED_MODEL_DIRECTORY.as_posix(),
            "sourceClassification": manifest.source_classification,
            "totalSizeBytes": verification.total_size_bytes,
            "verifiedFileCount": len(verification.verified_files),
            "voiceRightsState": manifest.voice_rights_state,
        },
        "provider": {
            "adapterId": artifact.adapter_id,
            "adapterVersion": artifact.adapter_version,
            "configurationFingerprint": artifact.configuration_fingerprint,
            "modelSha256": artifact.model_sha256,
            "providerId": artifact.provider_id,
            "runtimeId": artifact.runtime_id,
            "runtimeVersion": artifact.runtime_version,
            "voiceId": artifact.voice_id,
            "voiceSha256": artifact.voice_sha256,
        },
        "request": {
            "inputFingerprint": request.input_fingerprint(),
            "inputTextRecorded": False,
            "inputTextSha256": sha256_text(SYNTHETIC_PHRASE),
            "pronunciationDictionaryFingerprint": _pronunciation_dictionary_fingerprint(),
            "pronunciationPlanFingerprint": request.pronunciation_override_plan_fingerprint(),
        },
        "result": "passed",
        "runnerIdentity": {
            "architecture": platform.machine(),
            "classification": "private-local-windows",
            "operatingSystem": "windows",
            "pythonVersion": platform.python_version(),
        },
        "runtimeWorker": {
            "createdAtUnixNs": identity.created_at_unix_ns,
            "deniedNetworkAttemptCount": exit_evidence.denied_network_attempt_count,
            "executable": {
                "byteSize": executable_size,
                "filename": identity.executable.name,
                "sha256": executable_sha256,
            },
            "jobObjectAssigned": identity.job_object_assigned,
            "launchMode": identity.launch_mode,
            "launcherPid": identity.launcher_pid,
            "networkUseClassification": "offline-python-socket-denied",
            "ownerPid": identity.parent_pid,
            "processParentPid": identity.process_parent_pid,
            "protocolVersion": identity.protocol_version,
            "shutdown": {
                "confirmedExited": exit_evidence.confirmed_exited,
                "exitCode": exit_evidence.exit_code,
                "gracefulShutdownConfirmed": (exit_evidence.graceful_shutdown_confirmed),
                "ownedProcessesConfirmedExited": (exit_evidence.owned_processes_confirmed_exited),
                "ownershipConfirmed": exit_evidence.ownership_confirmed,
                "reason": exit_evidence.reason,
                "shutdownAcknowledged": exit_evidence.shutdown_acknowledged,
                "terminatedByParent": exit_evidence.terminated_by_parent,
            },
            "workerPid": identity.pid,
        },
        "schemaVersion": VERIFICATION_SCHEMA_VERSION,
        "scope": {
            "componentOnlyVerification": True,
            "governedPhase3aVoiceProfileBound": False,
            "governedRightsRecordBound": False,
            "humanListeningPerformed": False,
            "inputClassification": "fixed-repository-owned-synthetic",
            "modelDownloadedByCommand": False,
            "providerNetworkRequired": False,
            "rawPhraseRecorded": False,
            "restrictedLocalUseAcknowledged": True,
        },
        "testTimestamp": timestamp,
    }


def _publish_private_output(
    *,
    repository_root: Path,
    output_root: Path,
    timestamp: str,
    nonce: str,
    verification: ModelPackageVerification,
    request: SpeechSynthesisRequest,
    artifact: SpeechArtifact,
    identity: SpeechWorkerIdentity,
    exit_evidence: SpeechRuntimeExitEvidence,
    executable_size: int,
    executable_sha256: str,
) -> VerificationResult:
    if _NONCE.fullmatch(nonce) is None:
        raise VerificationFailure("NONCE_INVALID", "The output publication nonce was invalid.")
    run_name = f"run-{_timestamp_run_component(timestamp)}-{nonce}"
    final_directory = output_root / run_name
    staging_directory = output_root / f".{run_name}.tmp"
    if final_directory.exists() or staging_directory.exists():
        raise VerificationFailure(
            "OUTPUT_ALREADY_EXISTS",
            "The private verification output identity already exists.",
        )
    try:
        staging_directory.mkdir(mode=0o700)
        _require_ordinary_directory(staging_directory, code="OUTPUT_STAGING_UNSAFE")
        wav_path = staging_directory / WAV_FILENAME
        evidence_path = staging_directory / EVIDENCE_FILENAME
        _write_exclusive(wav_path, artifact.wav_bytes, maximum_bytes=len(artifact.wav_bytes))
        qc = inspect_audition_wav(wav_path, managed_root=staging_directory)
        if (
            qc.blocking_findings
            or qc.measurements.content_sha256 != artifact.wav_sha256
            or qc.byte_size != len(artifact.wav_bytes)
        ):
            raise VerificationFailure(
                "WAV_QC_FAILED",
                "The generated real-provider WAV failed bounded integrity checks.",
            )
        relative_wav_path = _relative_path(final_directory / WAV_FILENAME, repository_root)
        relative_evidence_path = _relative_path(
            final_directory / EVIDENCE_FILENAME,
            repository_root,
        )
        evidence = _build_evidence(
            timestamp=timestamp,
            verification=verification,
            request=request,
            artifact=artifact,
            qc=qc,
            identity=identity,
            exit_evidence=exit_evidence,
            executable_size=executable_size,
            executable_sha256=executable_sha256,
            relative_wav_path=relative_wav_path,
            relative_evidence_path=relative_evidence_path,
        )
        evidence_bytes = (canonical_json(evidence) + "\n").encode("utf-8")
        _write_exclusive(evidence_path, evidence_bytes, maximum_bytes=MAX_EVIDENCE_BYTES)
        os.replace(staging_directory, final_directory)

        published_wav = final_directory / WAV_FILENAME
        published_evidence = final_directory / EVIDENCE_FILENAME
        published_wav_size, published_wav_sha256 = _hash_regular_file(
            published_wav,
            maximum_bytes=len(artifact.wav_bytes),
        )
        evidence_size, evidence_sha256 = _hash_regular_file(
            published_evidence,
            maximum_bytes=MAX_EVIDENCE_BYTES,
        )
        decoded = json.loads(published_evidence.read_text(encoding="utf-8"))
        if (
            published_wav_size != len(artifact.wav_bytes)
            or published_wav_sha256 != artifact.wav_sha256
            or evidence_size != len(evidence_bytes)
            or decoded != evidence
        ):
            raise VerificationFailure(
                "OUTPUT_REVALIDATION_FAILED",
                "The atomically published private verification output changed.",
            )
        return VerificationResult(
            evidence=evidence,
            evidence_path=relative_evidence_path,
            evidence_sha256=evidence_sha256,
            wav_path=relative_wav_path,
        )
    except BaseException as exc:
        cleanup_target = staging_directory if staging_directory.exists() else final_directory
        cleanup_succeeded = _cleanup_owned_directory(cleanup_target)
        if not cleanup_succeeded:
            raise VerificationFailure(
                "OUTPUT_CLEANUP_FAILED",
                "An incomplete private output could not be removed safely.",
            ) from exc
        if isinstance(exc, (KeyboardInterrupt, SystemExit, VerificationFailure)):
            raise
        raise VerificationFailure(
            "OUTPUT_PUBLICATION_FAILED",
            "The private verification output could not be published atomically.",
        ) from exc


def run_verification(
    *,
    restricted_local_use_acknowledged: bool,
    repository_root: Path | None = None,
    runtime_factory: RuntimeFactory = _create_managed_runtime,
    model_verifier: ModelVerifier = verify_model_package_path,
    platform_verifier: PlatformVerifier = _assert_supported_platform,
    timestamp_factory: TimestampFactory = utc_now,
    nonce_factory: NonceFactory = _new_nonce,
    monotonic: Callable[[], float] = time.monotonic,
    executable_hasher: FileHasher = _hash_executable,
) -> VerificationResult:
    if not restricted_local_use_acknowledged:
        raise VerificationFailure(
            "RESTRICTED_LOCAL_USE_ACKNOWLEDGEMENT_REQUIRED",
            "Explicit restricted-local-use acknowledgement is required.",
        )
    platform_verifier()
    root = (repository_root or _repository_root()).absolute()
    output_root = _private_output_root(root)
    _reconcile_private_output(root, output_root)
    package_path = _fixed_model_path(root)
    verification = _verify_exact_model_package(package_path, model_verifier)
    request = _request()
    context = _context(monotonic)
    runtime = runtime_factory(_runtime_config(package_path))
    artifact, identity, exit_evidence = _run_managed_synthesis(runtime, request, context)
    _validate_artifact(artifact, request)
    executable_size, executable_sha256 = executable_hasher(identity.executable)
    if executable_size <= 0 or _SHA256.fullmatch(executable_sha256) is None:
        raise VerificationFailure(
            "WORKER_EXECUTABLE_HASH_INVALID",
            "The managed worker executable hash was invalid.",
        )
    timestamp = timestamp_factory()
    return _publish_private_output(
        repository_root=root,
        output_root=output_root,
        timestamp=timestamp,
        nonce=nonce_factory(),
        verification=verification,
        request=request,
        artifact=artifact,
        identity=identity,
        exit_evidence=exit_evidence,
        executable_size=executable_size,
        executable_sha256=executable_sha256,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_real_speech_provider.py",
        description=(
            "Verify the fixed ignored Kokoro package with one fixed repository-owned "
            "synthetic phrase and publish private redacted evidence."
        ),
    )
    parser.add_argument(
        "--acknowledge-restricted-local-use",
        action="store_true",
        required=True,
        help="Explicitly acknowledge that this local provider and voice remain restricted.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = run_verification(
            restricted_local_use_acknowledged=arguments.acknowledge_restricted_local_use,
        )
    except Exception as exc:
        code = exc.code if isinstance(exc, VerificationFailure) else type(exc).__name__
        sys.stderr.write(canonical_json({"errorCode": code, "result": "failed"}) + "\n")
        return 1
    sys.stdout.write(
        canonical_json(
            {
                "evidencePath": result.evidence_path,
                "evidenceSha256": result.evidence_sha256,
                "result": "passed",
                "wavPath": result.wav_path,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
