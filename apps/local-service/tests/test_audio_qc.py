from __future__ import annotations

import math
import os
import shutil
import struct
import wave
from collections.abc import Iterable
from pathlib import Path

import pytest

from cinematic_story_service.audio_qc import (
    AudioValidationError,
    CuePlacement,
    LoudnessMeasurement,
    LoudnessPolicy,
    ProviderFailure,
    QcFinding,
    WavExpectations,
    compile_cue_timeline,
    evaluate_loudness,
    evaluate_missing_clips,
    evaluate_wav,
    inspect_pcm_wav,
    measure_loudness_with_ffmpeg,
    recover_required_provider_clip,
)


def _write_pcm_fixture(
    path: Path,
    *,
    segments: list[tuple[int, float]],
    sample_rate_hz: int = 48_000,
    channels: int = 2,
) -> Path:
    frame_index = 0
    content = bytearray()
    for duration_ms, amplitude in segments:
        frame_count = duration_ms * sample_rate_hz // 1000
        for _ in range(frame_count):
            phase = 2 * math.pi * 440 * frame_index / sample_rate_hz
            sample = round(amplitude * 28_000 * math.sin(phase + math.pi / 2))
            content.extend(struct.pack("<h", sample) * channels)
            frame_index += 1
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate_hz)
        target.writeframes(content)
    return path


def _codes(findings: Iterable[QcFinding]) -> set[str]:
    return {finding.code for finding in findings}


def test_pcm_inspection_covers_duration_channels_rate_and_silence_boundaries(
    tmp_path: Path,
) -> None:
    audio = _write_pcm_fixture(
        tmp_path / "measured.wav",
        segments=[(100, 0.0), (250, 0.4), (50, 0.0)],
    )
    measured = inspect_pcm_wav(audio)

    assert measured.duration_ms == pytest.approx(400)
    assert measured.channel_count == 2
    assert measured.sample_rate_hz == 48_000
    assert measured.leading_silence_ms == pytest.approx(100)
    assert measured.trailing_silence_ms == pytest.approx(50)
    assert measured.non_silent_frames == pytest.approx(12_000, abs=25)
    assert measured.clipped is False
    assert len(measured.content_sha256) == 64
    assert (
        evaluate_wav(
            measured,
            WavExpectations(
                expected_duration_ms=400,
                expected_channels=2,
                expected_sample_rate_hz=48_000,
                min_leading_silence_ms=99.9,
                max_leading_silence_ms=100.1,
                min_trailing_silence_ms=49.9,
                max_trailing_silence_ms=50.1,
            ),
        )
        == ()
    )


def test_pcm_qc_reports_format_duration_and_silence_boundary_mismatches(
    tmp_path: Path,
) -> None:
    measured = inspect_pcm_wav(
        _write_pcm_fixture(
            tmp_path / "mismatch.wav",
            segments=[(100, 0.0), (250, 0.3), (50, 0.0)],
        )
    )
    findings = evaluate_wav(
        measured,
        WavExpectations(
            expected_duration_ms=450,
            expected_channels=1,
            expected_sample_rate_hz=44_100,
            max_leading_silence_ms=90,
            min_trailing_silence_ms=60,
        ),
    )

    assert _codes(findings) == {
        "DURATION_MISMATCH",
        "CHANNEL_COUNT_MISMATCH",
        "SAMPLE_RATE_MISMATCH",
        "LEADING_SILENCE_OUT_OF_BOUNDS",
        "TRAILING_SILENCE_OUT_OF_BOUNDS",
    }


def test_pcm_qc_detects_integer_clipping(tmp_path: Path) -> None:
    path = tmp_path / "clipped.wav"
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(struct.pack("<hhhh", 0, 32_767, -32_768, 0))

    measured = inspect_pcm_wav(path)
    findings = evaluate_wav(
        measured,
        WavExpectations(
            expected_duration_ms=4 * 1000 / 48_000,
            expected_channels=1,
            expected_sample_rate_hz=48_000,
        ),
    )

    assert measured.clipped is True
    assert "CLIPPING_DETECTED" in _codes(findings)


def test_missing_clip_findings_distinguish_required_and_optional() -> None:
    present = "a" * 64
    required_missing = "b" * 64
    optional_missing = "c" * 64
    cues = [
        CuePlacement("present", "dialogue", 0, 100, present),
        CuePlacement("required", "foley", 100, 100, required_missing),
        CuePlacement("optional", "music", 200, 100, optional_missing, required=False),
    ]

    findings = evaluate_missing_clips(cues, {present})

    assert [(finding.code, finding.blocking) for finding in findings] == [
        ("OPTIONAL_CLIP_MISSING", False),
        ("MISSING_CLIP", True),
    ]


def test_cue_placement_and_hash_are_sample_accurate_and_order_independent() -> None:
    cues = [
        CuePlacement(
            "music-bed",
            "music",
            125,
            333,
            "a" * 64,
            fade_in_ms=25,
            fade_out_ms=25,
            gain_millidb=-6_000,
        ),
        CuePlacement("dialogue-1", "dialogue", 125, 250, "b" * 64),
        CuePlacement("ambience", "ambience", 0, 500, "c" * 64),
    ]

    first = compile_cue_timeline(cues)
    second = compile_cue_timeline(reversed(cues))

    assert [cue.cue_id for cue in first.cues] == ["ambience", "dialogue-1", "music-bed"]
    assert [cue.start_sample for cue in first.cues] == [0, 6_000, 6_000]
    assert first.cues[2].duration_samples == 15_984
    assert first.cues[2].fade_in_samples == 1_200
    assert first.timeline_sha256 == second.timeline_sha256
    assert [cue.placement_sha256 for cue in first.cues] == [
        cue.placement_sha256 for cue in second.cues
    ]


def test_loudness_target_and_true_peak_policy_are_independent_of_pcm_rms() -> None:
    policy = LoudnessPolicy(target_lufs=-18, tolerance_lu=1, true_peak_ceiling_dbtp=-1)

    assert evaluate_loudness(LoudnessMeasurement(-18.4, -1.2, "fixture-meter"), policy) == ()
    findings = evaluate_loudness(LoudnessMeasurement(-20.1, -0.4, "fixture-meter"), policy)

    assert _codes(findings) == {"LOUDNESS_OUT_OF_TARGET", "TRUE_PEAK_EXCEEDED"}


def test_bounded_provider_recovery_can_succeed_after_one_transient_failure(
    tmp_path: Path,
) -> None:
    valid = _write_pcm_fixture(tmp_path / "valid.wav", segments=[(40, 0.25)])

    def provider(attempt: int) -> Path:
        if attempt == 1:
            raise ProviderFailure("PROVIDER_BUSY", retryable=True)
        return valid

    outcome = recover_required_provider_clip(provider, max_attempts=2)

    assert outcome.status == "succeeded"
    assert outcome.attempts == 2
    assert outcome.artifact_path == valid.resolve()
    assert outcome.artifact_sha256 == inspect_pcm_wav(valid).content_sha256
    assert outcome.error_code is None


def test_bounded_provider_failure_never_fabricates_silent_success(tmp_path: Path) -> None:
    attempts: list[int] = []

    def unavailable_provider(attempt: int) -> Path:
        attempts.append(attempt)
        raise ProviderFailure("PROVIDER_UNAVAILABLE", retryable=True)

    failed = recover_required_provider_clip(unavailable_provider, max_attempts=3)

    assert attempts == [1, 2, 3]
    assert failed.status == "failed"
    assert failed.attempts == 3
    assert failed.artifact_path is None
    assert failed.artifact_sha256 is None
    assert failed.error_code == "PROVIDER_UNAVAILABLE"
    assert list(tmp_path.iterdir()) == []


def test_all_silent_provider_output_is_a_failure_not_an_artifact(tmp_path: Path) -> None:
    silent = _write_pcm_fixture(tmp_path / "silent.wav", segments=[(50, 0.0)])

    outcome = recover_required_provider_clip(lambda _attempt: silent, max_attempts=2)

    assert outcome.status == "failed"
    assert outcome.attempts == 1
    assert outcome.artifact_path is None
    assert outcome.artifact_sha256 is None
    assert outcome.error_code == "PROVIDER_OUTPUT_SILENT"


def test_ffmpeg_loudness_measurement_when_explicit_executable_is_available(
    tmp_path: Path,
) -> None:
    executable = shutil.which("ffmpeg", path=os.environ.get("PATH", ""))
    if executable is None:
        pytest.skip("FFmpeg is not installed; core audio tests do not require it.")
    audio = _write_pcm_fixture(tmp_path / "meter.wav", segments=[(3_000, 0.2)])

    measurement = measure_loudness_with_ffmpeg(
        Path(executable).resolve(),
        audio,
        timeout_seconds=20,
    )

    assert measurement.source == "ffmpeg-loudnorm"
    assert math.isfinite(measurement.integrated_lufs)
    assert measurement.true_peak_dbtp is not None
    assert math.isfinite(measurement.true_peak_dbtp)


def test_ffmpeg_measurement_rejects_implicit_path_lookup(tmp_path: Path) -> None:
    audio = _write_pcm_fixture(tmp_path / "input.wav", segments=[(20, 0.2)])

    with pytest.raises(AudioValidationError):
        measure_loudness_with_ffmpeg(Path("ffmpeg"), audio)
