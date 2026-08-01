"""Deterministic Phase 3B audition identities, governance, and bounded audio QC."""

from __future__ import annotations

import math
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from .audio_qc import AudioValidationError, WavMeasurements, inspect_pcm_wav
from .util import request_fingerprint, sha256_text, stable_id

MAX_AUDITION_SCRIPTS_PER_SESSION: Final = 20
MAX_AUDITION_SESSIONS_PER_PROJECT: Final = 2_000
MAX_AUDITION_CACHE_RECORDS_PER_PROJECT: Final = 10_000
MAX_AUDITION_AUDIO_BYTES: Final = 24 * 1024 * 1024
MAX_AUDITION_DURATION_MS: Final = 30_000.0
MIN_AUDITION_DURATION_MS: Final = 100.0
MIN_USUAL_AUDITION_DURATION_MS: Final = 500.0
MAX_USUAL_AUDITION_DURATION_MS: Final = 20_000.0
MAX_AUDITION_CLIPPED_SAMPLE_RATIO_PPM: Final = 1_000
MIN_AUDITION_RMS_DBFS_WARNING: Final = -40.0
MAX_AUDITION_SILENCE_RATIO_PPM_WARNING: Final = 700_000
AUDITION_SAMPLE_RATE_HZ: Final = 24_000
AUDITION_CHANNEL_COUNT: Final = 1
AUDITION_SAMPLE_WIDTH_BYTES: Final = 2
AUDITION_PROFILE_VERSION: Final = "1.0.0"

AUDITION_GATE_IDS: Final = (
    "per_role_audition_review",
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review",
)

AuditionGateId = Literal[
    "per_role_audition_review",
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review",
]
ReviewState = Literal["blocked", "pending", "approved", "rejected", "stale"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

AUDITION_PROFILE_VALUES: Final = {
    "audio": {
        "channelCount": AUDITION_CHANNEL_COUNT,
        "maximumBytes": MAX_AUDITION_AUDIO_BYTES,
        "maximumDurationMs": MAX_AUDITION_DURATION_MS,
        "minimumDurationMs": MIN_AUDITION_DURATION_MS,
        "sampleRateHz": AUDITION_SAMPLE_RATE_HZ,
        "sampleWidthBytes": AUDITION_SAMPLE_WIDTH_BYTES,
    },
    "limits": {
        "maximumCacheRecordsPerProject": MAX_AUDITION_CACHE_RECORDS_PER_PROJECT,
        "maximumScriptsPerSession": MAX_AUDITION_SCRIPTS_PER_SESSION,
        "maximumSessionsPerProject": MAX_AUDITION_SESSIONS_PER_PROJECT,
    },
    "quality": {
        "clippingRiskWarningAtOrAboveSampleCount": 1,
        "excessiveClippingAboveRatioPpm": (MAX_AUDITION_CLIPPED_SAMPLE_RATIO_PPM),
        "highSilenceWarningAboveRatioPpm": (MAX_AUDITION_SILENCE_RATIO_PPM_WARNING),
        "lowLevelWarningBelowRmsDbfs": MIN_AUDITION_RMS_DBFS_WARNING,
        "unusualDurationAboveMs": MAX_USUAL_AUDITION_DURATION_MS,
        "unusualDurationBelowMs": MIN_USUAL_AUDITION_DURATION_MS,
    },
    "profileVersion": AUDITION_PROFILE_VERSION,
}
AUDITION_PROFILE_FINGERPRINT: Final = request_fingerprint(AUDITION_PROFILE_VALUES)


class AuditionError(ValueError):
    """An audition contract, artifact, or governance snapshot is invalid."""


@dataclass(frozen=True, slots=True)
class AuditionScript:
    script_id: str
    role_id: str
    kind: Literal["narrator", "character", "pronunciation"]
    source_text_sha256: str
    source_start: int
    source_end: int
    normalized_text_sha256: str
    normalization_fingerprint: str
    pronunciation_plan_fingerprint: str
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        role_id: str,
        kind: Literal["narrator", "character", "pronunciation"],
        source_text: str,
        source_start: int,
        source_end: int,
        normalized_text_sha256: str,
        normalization_fingerprint: str,
        pronunciation_plan_fingerprint: str,
    ) -> AuditionScript:
        if not role_id or kind not in {"narrator", "character", "pronunciation"}:
            raise AuditionError("The audition script role or kind is invalid.")
        if not 0 <= source_start < source_end <= len(source_text):
            raise AuditionError("The audition script source span is invalid.")
        for digest in (
            normalized_text_sha256,
            normalization_fingerprint,
            pronunciation_plan_fingerprint,
        ):
            if _SHA256.fullmatch(digest) is None:
                raise AuditionError("The audition script fingerprint is invalid.")
        selected_sha256 = sha256_text(source_text[source_start:source_end])
        material = {
            "kind": kind,
            "normalizationFingerprint": normalization_fingerprint,
            "normalizedTextSha256": normalized_text_sha256,
            "pronunciationPlanFingerprint": pronunciation_plan_fingerprint,
            "roleId": role_id,
            "sourceEnd": source_end,
            "sourceStart": source_start,
            "sourceTextSha256": selected_sha256,
        }
        fingerprint = request_fingerprint(material)
        return cls(
            script_id=stable_id("phase3b-audition-script", fingerprint),
            role_id=role_id,
            kind=kind,
            source_text_sha256=selected_sha256,
            source_start=source_start,
            source_end=source_end,
            normalized_text_sha256=normalized_text_sha256,
            normalization_fingerprint=normalization_fingerprint,
            pronunciation_plan_fingerprint=pronunciation_plan_fingerprint,
            fingerprint=fingerprint,
        )


@dataclass(frozen=True, slots=True)
class AuditionCacheIdentity:
    project_id: str
    provider_id: str
    adapter_version: str
    runtime_fingerprint: str
    model_package_fingerprint: str
    voice_profile_id: str
    voice_runtime_binding_fingerprint: str
    provider_voice_id: str
    voice_assignment_id: str
    voice_assignment_revision: int
    normalized_text_sha256: str
    pronunciation_plan_fingerprint: str
    provider_control_fingerprint: str
    output_profile_fingerprint: str
    producer_version: str

    def key(self) -> str:
        if not self.project_id or not self.provider_id or self.voice_assignment_revision < 1:
            raise AuditionError("The audition cache identity is incomplete.")
        values = (
            self.__dict__
            if hasattr(self, "__dict__")
            else {field: getattr(self, field) for field in self.__dataclass_fields__}
        )
        for field, value in values.items():
            if field.endswith("fingerprint") or field.endswith("sha256"):
                if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                    raise AuditionError("The audition cache identity contains an invalid digest.")
        return request_fingerprint(
            {
                "adapterVersion": self.adapter_version,
                "modelPackageFingerprint": self.model_package_fingerprint,
                "normalizedTextSha256": self.normalized_text_sha256,
                "outputProfileFingerprint": self.output_profile_fingerprint,
                "privacyScope": {"projectId": self.project_id},
                "producerVersion": self.producer_version,
                "pronunciationPlanFingerprint": self.pronunciation_plan_fingerprint,
                "providerControlFingerprint": self.provider_control_fingerprint,
                "providerId": self.provider_id,
                "providerVoiceId": self.provider_voice_id,
                "runtimeFingerprint": self.runtime_fingerprint,
                "voiceAssignmentId": self.voice_assignment_id,
                "voiceAssignmentRevision": self.voice_assignment_revision,
                "voiceProfileId": self.voice_profile_id,
                "voiceRuntimeBindingFingerprint": self.voice_runtime_binding_fingerprint,
            }
        )


@dataclass(frozen=True, slots=True)
class AuditionAudioQc:
    measurements: WavMeasurements
    byte_size: int
    warnings: tuple[str, ...]
    blocking_findings: tuple[str, ...]
    fingerprint: str


def _is_reparse_or_symlink(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def inspect_audition_wav(path: Path, *, managed_root: Path) -> AuditionAudioQc:
    try:
        lexical_root = managed_root.absolute()
        lexical_path = path.absolute()
        lexical_path.relative_to(lexical_root)
        current = lexical_path
        while True:
            metadata = current.lstat()
            if _is_reparse_or_symlink(current):
                raise AuditionError("The audition artifact traverses a link or reparse point.")
            if current == lexical_path:
                if not stat.S_ISREG(metadata.st_mode):
                    raise AuditionError("The audition artifact is not a regular file.")
            elif not stat.S_ISDIR(metadata.st_mode):
                raise AuditionError("The audition artifact storage chain is invalid.")
            if current == lexical_root:
                break
            if current == current.parent:
                raise ValueError("The audition artifact escaped managed storage.")
            current = current.parent
        root = lexical_root.resolve(strict=True)
        resolved = lexical_path.resolve(strict=True)
        resolved.relative_to(root)
    except AuditionError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise AuditionError("The audition artifact is outside managed storage.") from exc
    byte_size = resolved.stat().st_size
    if byte_size <= 44 or byte_size > MAX_AUDITION_AUDIO_BYTES:
        raise AuditionError("The audition artifact byte size is out of bounds.")
    try:
        measurements = inspect_pcm_wav(resolved)
    except AudioValidationError as exc:
        raise AuditionError("The audition artifact is not valid bounded PCM WAVE audio.") from exc
    return _audition_audio_qc(measurements, byte_size=byte_size)


def inspect_audition_wav_bytes(payload: bytes) -> AuditionAudioQc:
    """Inspect the exact already-bounded bytes read from managed storage."""

    byte_size = len(payload)
    if byte_size <= 44 or byte_size > MAX_AUDITION_AUDIO_BYTES:
        raise AuditionError("The audition artifact byte size is out of bounds.")
    try:
        measurements = inspect_pcm_wav(payload)
    except AudioValidationError as exc:
        raise AuditionError("The audition artifact is not valid bounded PCM WAVE audio.") from exc
    return _audition_audio_qc(measurements, byte_size=byte_size)


def _audition_audio_qc(
    measurements: WavMeasurements,
    *,
    byte_size: int,
) -> AuditionAudioQc:
    blocking: list[str] = []
    warnings: list[str] = []
    duration_in_bounds = (
        MIN_AUDITION_DURATION_MS <= measurements.duration_ms <= MAX_AUDITION_DURATION_MS
    )
    if not duration_in_bounds:
        blocking.append("AUDITION_DURATION_OUT_OF_BOUNDS")
    elif not (
        MIN_USUAL_AUDITION_DURATION_MS <= measurements.duration_ms <= MAX_USUAL_AUDITION_DURATION_MS
    ):
        warnings.append("AUDITION_UNUSUAL_DURATION")
    provider_profile_mismatch = False
    if measurements.sample_rate_hz != AUDITION_SAMPLE_RATE_HZ:
        blocking.append("AUDITION_SAMPLE_RATE_MISMATCH")
        provider_profile_mismatch = True
    if measurements.channel_count != AUDITION_CHANNEL_COUNT:
        blocking.append("AUDITION_CHANNEL_COUNT_MISMATCH")
        provider_profile_mismatch = True
    if measurements.sample_width_bytes != AUDITION_SAMPLE_WIDTH_BYTES:
        blocking.append("AUDITION_SAMPLE_FORMAT_MISMATCH")
        provider_profile_mismatch = True
    if provider_profile_mismatch:
        warnings.append("AUDITION_PROVIDER_PROFILE_MISMATCH")
    if measurements.non_silent_frames == 0:
        blocking.append("AUDITION_ALL_SILENT")
    elif (
        math.isfinite(measurements.rms_dbfs)
        and measurements.rms_dbfs < MIN_AUDITION_RMS_DBFS_WARNING
    ):
        warnings.append("AUDITION_LOW_LEVEL")
    silent_frame_count = max(
        0,
        measurements.frame_count - measurements.non_silent_frames,
    )
    silence_ratio_ppm = (
        silent_frame_count * 1_000_000 // measurements.frame_count
        if measurements.frame_count > 0
        else 1_000_000
    )
    if (
        measurements.non_silent_frames > 0
        and silent_frame_count * 1_000_000
        > MAX_AUDITION_SILENCE_RATIO_PPM_WARNING * measurements.frame_count
    ):
        warnings.append("AUDITION_HIGH_SILENCE_RATIO")
    total_sample_count = measurements.frame_count * measurements.channel_count
    clipped_sample_ratio_ppm = (
        measurements.clipped_sample_count * 1_000_000 // total_sample_count
        if total_sample_count > 0
        else 0
    )
    if measurements.clipped_sample_count > 0:
        warnings.append("AUDITION_CLIPPING_DETECTED")
    if (
        total_sample_count > 0
        and measurements.clipped_sample_count * 1_000_000
        > MAX_AUDITION_CLIPPED_SAMPLE_RATIO_PPM * total_sample_count
    ):
        blocking.append("AUDITION_EXCESSIVE_CLIPPING")
    if not math.isfinite(measurements.peak_dbfs):
        blocking.append("AUDITION_PEAK_UNAVAILABLE")
    material = {
        "blockingFindings": sorted(blocking),
        "byteSize": byte_size,
        "clippedSampleCount": measurements.clipped_sample_count,
        "clippedSampleRatioPpm": clipped_sample_ratio_ppm,
        "contentSha256": measurements.content_sha256,
        "durationMs": round(measurements.duration_ms, 3),
        "peakMilliDbfs": (
            round(measurements.peak_dbfs * 1_000) if math.isfinite(measurements.peak_dbfs) else None
        ),
        "profileFingerprint": AUDITION_PROFILE_FINGERPRINT,
        "rmsMilliDbfs": (
            round(measurements.rms_dbfs * 1_000) if math.isfinite(measurements.rms_dbfs) else None
        ),
        "sampleRateHz": measurements.sample_rate_hz,
        "channelCount": measurements.channel_count,
        "sampleWidthBytes": measurements.sample_width_bytes,
        "silenceRatioPpm": silence_ratio_ppm,
        "warnings": sorted(warnings),
    }
    return AuditionAudioQc(
        measurements=measurements,
        byte_size=byte_size,
        warnings=tuple(sorted(warnings)),
        blocking_findings=tuple(sorted(blocking)),
        fingerprint=request_fingerprint(material),
    )


@dataclass(frozen=True, slots=True)
class GateEvidence:
    gate_id: AuditionGateId
    evidence_fingerprint: str
    prerequisites: tuple[str, ...]
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.gate_id not in AUDITION_GATE_IDS
            or _SHA256.fullmatch(self.evidence_fingerprint) is None
        ):
            raise AuditionError("The audition review evidence is invalid.")


def evaluate_gate_state(
    evidence: GateEvidence,
    *,
    current_dependency_fingerprints: Mapping[str, str],
    recorded_dependency_fingerprints: Mapping[str, str],
    latest_human_decision: Literal["approved", "rejected"] | None,
) -> ReviewState:
    if evidence.blockers:
        return "blocked"
    if set(evidence.prerequisites) - set(current_dependency_fingerprints):
        return "blocked"
    if current_dependency_fingerprints != recorded_dependency_fingerprints:
        return "stale" if latest_human_decision is not None else "pending"
    if latest_human_decision is None:
        return "pending"
    return latest_human_decision


def dependent_gate_ids(
    *,
    changed_role_ids: Sequence[str] = (),
    changed_pronunciation_entry_ids: Sequence[str] = (),
    provider_or_model_changed: bool = False,
) -> tuple[AuditionGateId, ...]:
    """Return only gates downstream of the changed evidence class."""

    gates: set[AuditionGateId] = set()
    if changed_role_ids:
        gates.update(
            {
                "per_role_audition_review",
                "narrator_audition_review",
                "character_audition_review",
                "voice_readiness_review",
            }
        )
    if changed_pronunciation_entry_ids:
        gates.update({"pronunciation_review", "voice_readiness_review"})
    if provider_or_model_changed:
        gates.update(
            {
                "per_role_audition_review",
                "narrator_audition_review",
                "character_audition_review",
                "voice_readiness_review",
            }
        )
    return tuple(gate for gate in AUDITION_GATE_IDS if gate in gates)


def voice_readiness_fingerprint(
    *,
    casting_snapshot_fingerprint: str,
    rights_snapshot_fingerprint: str,
    model_verification_fingerprint: str,
    runtime_fingerprint: str,
    narrator_review_fingerprint: str,
    character_review_fingerprint: str,
    pronunciation_review_fingerprint: str,
) -> str:
    values = {
        "castingSnapshotFingerprint": casting_snapshot_fingerprint,
        "characterReviewFingerprint": character_review_fingerprint,
        "modelVerificationFingerprint": model_verification_fingerprint,
        "narratorReviewFingerprint": narrator_review_fingerprint,
        "pronunciationReviewFingerprint": pronunciation_review_fingerprint,
        "rightsSnapshotFingerprint": rights_snapshot_fingerprint,
        "runtimeFingerprint": runtime_fingerprint,
    }
    if any(_SHA256.fullmatch(value) is None for value in values.values()):
        raise AuditionError("Voice-readiness evidence contains an invalid fingerprint.")
    return request_fingerprint(values | {"profileFingerprint": AUDITION_PROFILE_FINGERPRINT})
