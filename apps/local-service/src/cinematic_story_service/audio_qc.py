"""Deterministic Phase 0 audio contract helpers.

This module is deliberately production-neutral. It provides inspection, policy, timeline, and
provider-recovery seams that can be tested with generated PCM. The full synthesis, mixing,
mastering, and atomic render-publication pipeline remains deferred.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import wave
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .tools import _run_bounded_process
from .util import canonical_json, sha256_text

_PCM_CHUNK_FRAMES = 4096
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACK_PRIORITY = {
    "narration": 0,
    "dialogue": 1,
    "foley": 2,
    "ambience": 3,
    "music": 4,
}

CueTrack = Literal["narration", "dialogue", "foley", "ambience", "music"]
FindingValue = str | int | float | bool | None
ProviderStatus = Literal["succeeded", "failed"]


class AudioValidationError(ValueError):
    """An audio artifact or audio contract is invalid."""


class ProviderFailure(RuntimeError):
    """A sanitized provider failure classification used by the recovery seam."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        if not code or len(code) > 80 or not code.replace("_", "").isalnum():
            raise ValueError("The provider failure code is invalid.")
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class WavMeasurements:
    sample_rate_hz: int
    channel_count: int
    sample_width_bytes: int
    frame_count: int
    duration_ms: float
    peak_dbfs: float
    rms_dbfs: float
    clipped: bool
    leading_silence_ms: float
    trailing_silence_ms: float
    non_silent_frames: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class WavExpectations:
    expected_duration_ms: float
    expected_channels: int
    expected_sample_rate_hz: int
    duration_tolerance_ms: float = 1.0
    min_leading_silence_ms: float = 0.0
    max_leading_silence_ms: float | None = None
    min_trailing_silence_ms: float = 0.0
    max_trailing_silence_ms: float | None = None
    allow_clipping: bool = False

    def __post_init__(self) -> None:
        numeric_values = (
            self.expected_duration_ms,
            self.duration_tolerance_ms,
            self.min_leading_silence_ms,
            self.min_trailing_silence_ms,
        )
        if any(not math.isfinite(value) or value < 0 for value in numeric_values):
            raise ValueError("Audio expectation values must be finite and non-negative.")
        if self.expected_channels < 1 or self.expected_sample_rate_hz < 1:
            raise ValueError("Expected channel count and sample rate must be positive.")
        for maximum in (
            self.max_leading_silence_ms,
            self.max_trailing_silence_ms,
        ):
            if maximum is not None and (not math.isfinite(maximum) or maximum < 0):
                raise ValueError("Silence maxima must be finite and non-negative.")
        if (
            self.max_leading_silence_ms is not None
            and self.max_leading_silence_ms < self.min_leading_silence_ms
        ):
            raise ValueError("The leading-silence range is invalid.")
        if (
            self.max_trailing_silence_ms is not None
            and self.max_trailing_silence_ms < self.min_trailing_silence_ms
        ):
            raise ValueError("The trailing-silence range is invalid.")


@dataclass(frozen=True, slots=True)
class QcFinding:
    code: str
    message: str
    blocking: bool
    measured: FindingValue = None
    expected: FindingValue = None


@dataclass(frozen=True, slots=True)
class LoudnessMeasurement:
    integrated_lufs: float
    true_peak_dbtp: float | None
    source: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.integrated_lufs):
            raise ValueError("Integrated loudness must be finite.")
        if self.true_peak_dbtp is not None and not math.isfinite(self.true_peak_dbtp):
            raise ValueError("True peak must be finite when supplied.")
        if not self.source:
            raise ValueError("A loudness measurement source is required.")


@dataclass(frozen=True, slots=True)
class LoudnessPolicy:
    target_lufs: float = -18.0
    tolerance_lu: float = 1.0
    true_peak_ceiling_dbtp: float = -1.0
    require_true_peak: bool = True

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value)
            for value in (
                self.target_lufs,
                self.tolerance_lu,
                self.true_peak_ceiling_dbtp,
            )
        ):
            raise ValueError("Loudness policy values must be finite.")
        if self.tolerance_lu < 0:
            raise ValueError("Loudness tolerance cannot be negative.")


@dataclass(frozen=True, slots=True)
class CuePlacement:
    cue_id: str
    track: CueTrack
    start_ms: int
    duration_ms: int
    asset_sha256: str
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    gain_millidb: int = 0
    pan_milli: int = 0
    required: bool = True

    def __post_init__(self) -> None:
        if _CUE_ID.fullmatch(self.cue_id) is None:
            raise ValueError("The cue ID is invalid.")
        if self.track not in _TRACK_PRIORITY:
            raise ValueError("The cue track is invalid.")
        for name, value in (
            ("start_ms", self.start_ms),
            ("duration_ms", self.duration_ms),
            ("fade_in_ms", self.fade_in_ms),
            ("fade_out_ms", self.fade_out_ms),
            ("gain_millidb", self.gain_millidb),
            ("pan_milli", self.pan_milli),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer.")
        if self.start_ms < 0 or self.duration_ms <= 0:
            raise ValueError("Cue timing must have a non-negative start and positive duration.")
        if self.fade_in_ms < 0 or self.fade_out_ms < 0:
            raise ValueError("Cue fades cannot be negative.")
        if self.fade_in_ms + self.fade_out_ms > self.duration_ms:
            raise ValueError("Cue fades cannot exceed the cue duration.")
        if not -96_000 <= self.gain_millidb <= 24_000:
            raise ValueError("Cue gain is outside the bounded range.")
        if not -1_000 <= self.pan_milli <= 1_000:
            raise ValueError("Cue pan is outside the bounded range.")
        if _SHA256.fullmatch(self.asset_sha256) is None:
            raise ValueError("The cue asset digest must be a lowercase SHA-256.")


@dataclass(frozen=True, slots=True)
class CompiledCue:
    cue_id: str
    track: CueTrack
    start_sample: int
    duration_samples: int
    fade_in_samples: int
    fade_out_samples: int
    gain_millidb: int
    pan_milli: int
    asset_sha256: str
    required: bool
    placement_sha256: str


@dataclass(frozen=True, slots=True)
class CompiledTimeline:
    sample_rate_hz: int
    cues: tuple[CompiledCue, ...]
    timeline_sha256: str


@dataclass(frozen=True, slots=True)
class ProviderRecoveryOutcome:
    status: ProviderStatus
    attempts: int
    artifact_path: Path | None
    artifact_sha256: str | None
    error_code: str | None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _dbfs(amplitude: float) -> float:
    if amplitude <= 0:
        return float("-inf")
    return 20.0 * math.log10(amplitude)


def inspect_pcm_wav(
    path: Path,
    *,
    silence_threshold_dbfs: float = -60.0,
) -> WavMeasurements:
    """Inspect integer PCM WAVE content without loading the artifact into memory."""

    if (
        not math.isfinite(silence_threshold_dbfs)
        or silence_threshold_dbfs > 0
        or silence_threshold_dbfs < -200
    ):
        raise ValueError("The silence threshold must be finite and between -200 and 0 dBFS.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AudioValidationError("The PCM WAVE artifact is unavailable.") from exc
    if not resolved.is_file():
        raise AudioValidationError("The PCM WAVE artifact is unavailable.")

    try:
        with wave.open(str(resolved), "rb") as source:
            if source.getcomptype() != "NONE":
                raise AudioValidationError("Only uncompressed integer PCM WAVE is supported.")
            channel_count = source.getnchannels()
            sample_rate_hz = source.getframerate()
            sample_width = source.getsampwidth()
            declared_frames = source.getnframes()
            if channel_count < 1 or sample_rate_hz < 1 or sample_width not in {1, 2, 3, 4}:
                raise AudioValidationError("The PCM WAVE format is unsupported.")

            frame_width = channel_count * sample_width
            bits = sample_width * 8
            minimum_sample = -(1 << (bits - 1))
            maximum_sample = (1 << (bits - 1)) - 1
            full_scale = float(1 << (bits - 1))
            silence_amplitude = full_scale * 10 ** (silence_threshold_dbfs / 20.0)
            frame_count = 0
            sample_count = 0
            peak_sample = 0
            sum_squares = 0
            clipped = False
            non_silent_frames = 0
            leading_silent_frames = 0
            trailing_silent_frames = 0
            found_non_silent = False

            while raw_frames := source.readframes(_PCM_CHUNK_FRAMES):
                if len(raw_frames) % frame_width != 0:
                    raise AudioValidationError("The PCM WAVE data is truncated.")
                chunk_frames = len(raw_frames) // frame_width
                for frame_index in range(chunk_frames):
                    frame_peak = 0
                    frame_offset = frame_index * frame_width
                    for channel_index in range(channel_count):
                        sample_offset = frame_offset + channel_index * sample_width
                        if sample_width == 1:
                            sample = raw_frames[sample_offset] - 128
                        else:
                            sample = int.from_bytes(
                                raw_frames[sample_offset : sample_offset + sample_width],
                                byteorder="little",
                                signed=True,
                            )
                        absolute_sample = abs(sample)
                        frame_peak = max(frame_peak, absolute_sample)
                        peak_sample = max(peak_sample, absolute_sample)
                        sum_squares += sample * sample
                        sample_count += 1
                        if sample == minimum_sample or sample == maximum_sample:
                            clipped = True
                    frame_count += 1
                    if frame_peak <= silence_amplitude:
                        if not found_non_silent:
                            leading_silent_frames += 1
                        trailing_silent_frames += 1
                    else:
                        found_non_silent = True
                        non_silent_frames += 1
                        trailing_silent_frames = 0
            if frame_count != declared_frames:
                raise AudioValidationError("The PCM WAVE data is truncated.")
    except wave.Error as exc:
        raise AudioValidationError("The artifact is not a valid PCM WAVE file.") from exc

    duration_ms = frame_count * 1000.0 / sample_rate_hz
    rms_amplitude = (
        math.sqrt(sum_squares / sample_count) / full_scale if sample_count > 0 else 0.0
    )
    return WavMeasurements(
        sample_rate_hz=sample_rate_hz,
        channel_count=channel_count,
        sample_width_bytes=sample_width,
        frame_count=frame_count,
        duration_ms=duration_ms,
        peak_dbfs=_dbfs(peak_sample / full_scale),
        rms_dbfs=_dbfs(rms_amplitude),
        clipped=clipped,
        leading_silence_ms=leading_silent_frames * 1000.0 / sample_rate_hz,
        trailing_silence_ms=trailing_silent_frames * 1000.0 / sample_rate_hz,
        non_silent_frames=non_silent_frames,
        content_sha256=_sha256_file(resolved),
    )


def evaluate_wav(
    measurements: WavMeasurements,
    expectations: WavExpectations,
) -> tuple[QcFinding, ...]:
    findings: list[QcFinding] = []
    if measurements.frame_count == 0:
        findings.append(QcFinding("AUDIO_EMPTY", "The audio has no frames.", True))
    elif measurements.non_silent_frames == 0:
        findings.append(QcFinding("AUDIO_ALL_SILENT", "The audio is entirely silent.", True))
    if (
        abs(measurements.duration_ms - expectations.expected_duration_ms)
        > expectations.duration_tolerance_ms
    ):
        findings.append(
            QcFinding(
                "DURATION_MISMATCH",
                "The audio duration is outside tolerance.",
                True,
                measurements.duration_ms,
                expectations.expected_duration_ms,
            )
        )
    if measurements.channel_count != expectations.expected_channels:
        findings.append(
            QcFinding(
                "CHANNEL_COUNT_MISMATCH",
                "The audio channel count is unexpected.",
                True,
                measurements.channel_count,
                expectations.expected_channels,
            )
        )
    if measurements.sample_rate_hz != expectations.expected_sample_rate_hz:
        findings.append(
            QcFinding(
                "SAMPLE_RATE_MISMATCH",
                "The audio sample rate is unexpected.",
                True,
                measurements.sample_rate_hz,
                expectations.expected_sample_rate_hz,
            )
        )
    if measurements.clipped and not expectations.allow_clipping:
        findings.append(
            QcFinding("CLIPPING_DETECTED", "The PCM audio reaches a clipping boundary.", True)
        )
    if (
        measurements.leading_silence_ms < expectations.min_leading_silence_ms
        or (
            expectations.max_leading_silence_ms is not None
            and measurements.leading_silence_ms > expectations.max_leading_silence_ms
        )
    ):
        findings.append(
            QcFinding(
                "LEADING_SILENCE_OUT_OF_BOUNDS",
                "Leading silence is outside the configured boundary.",
                True,
                measurements.leading_silence_ms,
                _range_label(
                    expectations.min_leading_silence_ms,
                    expectations.max_leading_silence_ms,
                ),
            )
        )
    if (
        measurements.trailing_silence_ms < expectations.min_trailing_silence_ms
        or (
            expectations.max_trailing_silence_ms is not None
            and measurements.trailing_silence_ms > expectations.max_trailing_silence_ms
        )
    ):
        findings.append(
            QcFinding(
                "TRAILING_SILENCE_OUT_OF_BOUNDS",
                "Trailing silence is outside the configured boundary.",
                True,
                measurements.trailing_silence_ms,
                _range_label(
                    expectations.min_trailing_silence_ms,
                    expectations.max_trailing_silence_ms,
                ),
            )
        )
    return tuple(findings)


def _range_label(minimum: float, maximum: float | None) -> str:
    return f"{minimum:g}..{maximum:g} ms" if maximum is not None else f">={minimum:g} ms"


def evaluate_loudness(
    measurement: LoudnessMeasurement,
    policy: LoudnessPolicy | None = None,
) -> tuple[QcFinding, ...]:
    """Evaluate externally measured LUFS/dBTP; PCM RMS is intentionally not treated as LUFS."""

    if policy is None:
        policy = LoudnessPolicy()
    findings: list[QcFinding] = []
    if abs(measurement.integrated_lufs - policy.target_lufs) > policy.tolerance_lu:
        findings.append(
            QcFinding(
                "LOUDNESS_OUT_OF_TARGET",
                "Integrated loudness is outside the mastering target.",
                True,
                measurement.integrated_lufs,
                f"{policy.target_lufs:g} +/- {policy.tolerance_lu:g} LUFS",
            )
        )
    if measurement.true_peak_dbtp is None:
        if policy.require_true_peak:
            findings.append(
                QcFinding(
                    "TRUE_PEAK_UNAVAILABLE",
                    "A required true-peak measurement is unavailable.",
                    True,
                )
            )
    elif measurement.true_peak_dbtp > policy.true_peak_ceiling_dbtp:
        findings.append(
            QcFinding(
                "TRUE_PEAK_EXCEEDED",
                "True peak exceeds the mastering ceiling.",
                True,
                measurement.true_peak_dbtp,
                policy.true_peak_ceiling_dbtp,
            )
        )
    return tuple(findings)


def milliseconds_to_samples(milliseconds: int, sample_rate_hz: int) -> int:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or milliseconds < 0:
        raise ValueError("Milliseconds must be a non-negative integer.")
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, int)
        or not 8_000 <= sample_rate_hz <= 384_000
    ):
        raise ValueError("The sample rate is outside the supported range.")
    return (milliseconds * sample_rate_hz + 500) // 1000


def compile_cue_timeline(
    cues: Iterable[CuePlacement],
    *,
    sample_rate_hz: int = 48_000,
) -> CompiledTimeline:
    milliseconds_to_samples(0, sample_rate_hz)
    cue_list = list(cues)
    cue_ids = [cue.cue_id for cue in cue_list]
    if len(cue_ids) != len(set(cue_ids)):
        raise AudioValidationError("Cue IDs must be unique within a timeline.")

    compiled: list[CompiledCue] = []
    for cue in cue_list:
        duration_samples = milliseconds_to_samples(cue.duration_ms, sample_rate_hz)
        if duration_samples == 0:
            raise AudioValidationError("The cue duration rounds to zero samples.")
        placement_payload = {
            "assetSha256": cue.asset_sha256,
            "cueId": cue.cue_id,
            "durationMs": cue.duration_ms,
            "fadeInMs": cue.fade_in_ms,
            "fadeOutMs": cue.fade_out_ms,
            "gainMillidb": cue.gain_millidb,
            "panMilli": cue.pan_milli,
            "required": cue.required,
            "startMs": cue.start_ms,
            "track": cue.track,
            "version": 1,
        }
        compiled.append(
            CompiledCue(
                cue_id=cue.cue_id,
                track=cue.track,
                start_sample=milliseconds_to_samples(cue.start_ms, sample_rate_hz),
                duration_samples=duration_samples,
                fade_in_samples=milliseconds_to_samples(cue.fade_in_ms, sample_rate_hz),
                fade_out_samples=milliseconds_to_samples(cue.fade_out_ms, sample_rate_hz),
                gain_millidb=cue.gain_millidb,
                pan_milli=cue.pan_milli,
                asset_sha256=cue.asset_sha256,
                required=cue.required,
                placement_sha256=sha256_text(canonical_json(placement_payload)),
            )
        )
    compiled.sort(
        key=lambda cue: (
            cue.start_sample,
            _TRACK_PRIORITY[cue.track],
            cue.cue_id,
        )
    )
    timeline_payload = {
        "cuePlacementSha256": [cue.placement_sha256 for cue in compiled],
        "sampleRateHz": sample_rate_hz,
        "version": 1,
    }
    return CompiledTimeline(
        sample_rate_hz=sample_rate_hz,
        cues=tuple(compiled),
        timeline_sha256=sha256_text(canonical_json(timeline_payload)),
    )


def evaluate_missing_clips(
    cues: Iterable[CuePlacement],
    available_sha256: Collection[str],
) -> tuple[QcFinding, ...]:
    findings: list[QcFinding] = []
    available = set(available_sha256)
    for digest in available:
        if _SHA256.fullmatch(digest) is None:
            raise ValueError("Available clip digests must be lowercase SHA-256 values.")
    for cue in sorted(cues, key=lambda value: value.cue_id):
        if cue.asset_sha256 in available:
            continue
        if cue.required:
            findings.append(
                QcFinding(
                    "MISSING_CLIP",
                    "A required cue clip is unavailable.",
                    True,
                    cue.asset_sha256,
                    cue.cue_id,
                )
            )
        else:
            findings.append(
                QcFinding(
                    "OPTIONAL_CLIP_MISSING",
                    "An optional cue clip is unavailable.",
                    False,
                    cue.asset_sha256,
                    cue.cue_id,
                )
            )
    return tuple(findings)


def recover_required_provider_clip(
    provider: Callable[[int], Path],
    *,
    max_attempts: int = 2,
) -> ProviderRecoveryOutcome:
    """Run a bounded provider recovery policy without fabricating a fallback artifact."""

    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise TypeError("max_attempts must be an integer.")
    if not 1 <= max_attempts <= 3:
        raise ValueError("Provider recovery is bounded to one through three attempts.")

    last_code = "PROVIDER_CALL_FAILED"
    for attempt in range(1, max_attempts + 1):
        try:
            artifact_path = provider(attempt)
            measurements = inspect_pcm_wav(artifact_path)
            if measurements.non_silent_frames == 0:
                return ProviderRecoveryOutcome(
                    "failed",
                    attempt,
                    None,
                    None,
                    "PROVIDER_OUTPUT_SILENT",
                )
            return ProviderRecoveryOutcome(
                "succeeded",
                attempt,
                artifact_path.resolve(strict=True),
                measurements.content_sha256,
                None,
            )
        except ProviderFailure as exc:
            last_code = exc.code
            if not exc.retryable:
                return ProviderRecoveryOutcome("failed", attempt, None, None, last_code)
        except AudioValidationError:
            return ProviderRecoveryOutcome(
                "failed",
                attempt,
                None,
                None,
                "PROVIDER_OUTPUT_INVALID",
            )
        except (OSError, RuntimeError, ValueError, TypeError):
            return ProviderRecoveryOutcome(
                "failed",
                attempt,
                None,
                None,
                "PROVIDER_CALL_FAILED",
            )
    return ProviderRecoveryOutcome("failed", max_attempts, None, None, last_code)


def measure_loudness_with_ffmpeg(
    ffmpeg_path: Path,
    wav_path: Path,
    *,
    timeout_seconds: float = 15.0,
) -> LoudnessMeasurement:
    """Measure LUFS/true peak with an explicitly selected FFmpeg executable."""

    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 60:
        raise ValueError("The FFmpeg loudness timeout is outside the bounded range.")
    if not ffmpeg_path.is_absolute():
        raise AudioValidationError("The FFmpeg executable path must be absolute.")
    try:
        executable = ffmpeg_path.resolve(strict=True)
        source = wav_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AudioValidationError("The loudness measurement input is unavailable.") from exc
    if not executable.is_file() or not source.is_file():
        raise AudioValidationError("The loudness measurement input is unavailable.")

    argv = [
        str(executable),
        "-hide_banner",
        "-nostdin",
        "-threads",
        "1",
        "-i",
        str(source),
        "-af",
        "loudnorm=I=-18:TP=-1:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    environment = {
        key: value
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key)) is not None
    }
    try:
        completed = _run_bounded_process(
            argv,
            cwd=source.parent,
            environment=environment,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioValidationError("FFmpeg loudness measurement failed.") from exc
    if completed.returncode != 0:
        raise AudioValidationError("FFmpeg loudness measurement failed.")

    for candidate in reversed(re.findall(r"\{[^{}]*\}", completed.stderr, flags=re.DOTALL)):
        try:
            payload: object = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "input_i" not in payload or "input_tp" not in payload:
            continue
        try:
            integrated_lufs = float(str(payload["input_i"]))
            true_peak_dbtp = float(str(payload["input_tp"]))
        except (TypeError, ValueError):
            continue
        if math.isfinite(integrated_lufs) and math.isfinite(true_peak_dbtp):
            return LoudnessMeasurement(
                integrated_lufs=integrated_lufs,
                true_peak_dbtp=true_peak_dbtp,
                source="ffmpeg-loudnorm",
            )
    raise AudioValidationError("FFmpeg did not return finite loudness measurements.")
