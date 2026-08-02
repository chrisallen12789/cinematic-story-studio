from __future__ import annotations

import math
import struct
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cinematic_story_service.auditions import (
    AUDITION_GATE_IDS,
    AUDITION_PROFILE_FINGERPRINT,
    MAX_AUDITION_CLIPPED_SAMPLE_RATIO_PPM,
    MAX_AUDITION_SILENCE_RATIO_PPM_WARNING,
    MAX_USUAL_AUDITION_DURATION_MS,
    MIN_AUDITION_RMS_DBFS_WARNING,
    MIN_USUAL_AUDITION_DURATION_MS,
    AuditionCacheIdentity,
    AuditionError,
    AuditionScript,
    GateEvidence,
    dependent_gate_ids,
    evaluate_gate_state,
    inspect_audition_wav,
    voice_readiness_fingerprint,
)
from cinematic_story_service.util import sha256_text


def _digest(label: str) -> str:
    return sha256_text(label)


def _identity() -> AuditionCacheIdentity:
    return AuditionCacheIdentity(
        project_id="project-1",
        provider_id="fixture-pcm",
        adapter_version="1.0.0",
        runtime_fingerprint=_digest("runtime"),
        model_package_fingerprint=_digest("model"),
        voice_profile_id="voice-1",
        voice_runtime_binding_fingerprint=_digest("voice-runtime-binding"),
        provider_voice_id="fixture-narrator-01",
        voice_assignment_id="assignment-1",
        voice_assignment_revision=1,
        normalized_text_sha256=_digest("text"),
        pronunciation_plan_fingerprint=_digest("pronunciation"),
        provider_control_fingerprint=_digest("controls"),
        output_profile_fingerprint=AUDITION_PROFILE_FINGERPRINT,
        producer_version="1.0.0",
    )


def _write_wav(path: Path, *, rate: int = 24_000, frames: int = 2_400) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        samples = [int(math.sin(index / 8.0) * 8_000) for index in range(frames)]
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def _write_sample_segments(
    path: Path,
    segments: list[tuple[int, int]],
    *,
    rate: int = 24_000,
) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        for sample_count, sample in segments:
            remaining = sample_count
            while remaining > 0:
                chunk_count = min(remaining, 4_096)
                output.writeframes(struct.pack("<h", sample) * chunk_count)
                remaining -= chunk_count


def test_cache_key_binds_private_scope_and_every_generation_dependency() -> None:
    identity = _identity()
    key = identity.key()
    assert len(key) == 64
    assert replace(identity, project_id="project-2").key() != key
    assert replace(identity, voice_assignment_revision=2).key() != key
    assert replace(identity, pronunciation_plan_fingerprint=_digest("new-plan")).key() != key
    with pytest.raises(AuditionError, match="digest"):
        replace(identity, runtime_fingerprint="bad").key()


def test_scripts_bind_owned_source_span_without_exposing_text() -> None:
    source = "Synthetic Aster dialogue."
    script = AuditionScript.create(
        role_id="role-1",
        kind="character",
        source_text=source,
        source_start=10,
        source_end=16,
        normalized_text_sha256=_digest("Aster"),
        normalization_fingerprint=_digest("normalization"),
        pronunciation_plan_fingerprint=_digest("pronunciation"),
    )
    assert script.source_text_sha256 == _digest("Aster ")
    assert "Synthetic" not in repr(script)
    assert script == AuditionScript.create(
        role_id="role-1",
        kind="character",
        source_text=source,
        source_start=10,
        source_end=16,
        normalized_text_sha256=_digest("Aster"),
        normalization_fingerprint=_digest("normalization"),
        pronunciation_plan_fingerprint=_digest("pronunciation"),
    )


def test_bounded_pcm_qc_accepts_managed_audio_and_rejects_escape(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    artifact = managed / "clip.wav"
    _write_wav(artifact)

    qc = inspect_audition_wav(artifact, managed_root=managed)
    assert qc.byte_size == artifact.stat().st_size
    assert qc.measurements.sample_rate_hz == 24_000
    assert qc.measurements.non_silent_frames > 0
    assert qc.blocking_findings == ()

    escaped = tmp_path / "escaped.wav"
    _write_wav(escaped)
    with pytest.raises(AuditionError, match="outside managed"):
        inspect_audition_wav(escaped, managed_root=managed)

    wrong_rate = managed / "wrong-rate.wav"
    _write_wav(wrong_rate, rate=16_000)
    assert (
        "AUDITION_SAMPLE_RATE_MISMATCH"
        in inspect_audition_wav(wrong_rate, managed_root=managed).blocking_findings
    )
    assert (
        "AUDITION_PROVIDER_PROFILE_MISMATCH"
        in inspect_audition_wav(wrong_rate, managed_root=managed).warnings
    )


def test_audition_qc_counts_clipped_samples_and_blocks_only_above_fixed_ratio(
    tmp_path: Path,
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    total_samples = 24_000
    boundary_count = total_samples * MAX_AUDITION_CLIPPED_SAMPLE_RATIO_PPM // 1_000_000
    boundary = managed / "clipping-boundary.wav"
    _write_sample_segments(
        boundary,
        [(boundary_count, 32_767), (total_samples - boundary_count, 4_000)],
    )
    boundary_qc = inspect_audition_wav(boundary, managed_root=managed)

    assert boundary_qc.measurements.clipped_sample_count == boundary_count
    assert "AUDITION_CLIPPING_DETECTED" in boundary_qc.warnings
    assert "AUDITION_EXCESSIVE_CLIPPING" not in boundary_qc.blocking_findings

    excessive = managed / "excessive-clipping.wav"
    _write_sample_segments(
        excessive,
        [
            (boundary_count + 1, -32_768),
            (total_samples - boundary_count - 1, 4_000),
        ],
    )
    excessive_qc = inspect_audition_wav(excessive, managed_root=managed)

    assert excessive_qc.measurements.clipped_sample_count == boundary_count + 1
    assert "AUDITION_CLIPPING_DETECTED" in excessive_qc.warnings
    assert "AUDITION_EXCESSIVE_CLIPPING" in excessive_qc.blocking_findings


def test_audition_qc_warning_thresholds_are_deterministic_and_boundary_exclusive(
    tmp_path: Path,
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()

    rms_boundary = managed / "rms-boundary.wav"
    _write_sample_segments(rms_boundary, [(12_000, 328)])
    rms_boundary_qc = inspect_audition_wav(rms_boundary, managed_root=managed)
    assert rms_boundary_qc.measurements.rms_dbfs > MIN_AUDITION_RMS_DBFS_WARNING
    assert "AUDITION_LOW_LEVEL" not in rms_boundary_qc.warnings

    low_level = managed / "low-level.wav"
    _write_sample_segments(low_level, [(12_000, 327)])
    low_level_qc = inspect_audition_wav(low_level, managed_root=managed)
    assert low_level_qc.measurements.rms_dbfs < MIN_AUDITION_RMS_DBFS_WARNING
    assert "AUDITION_LOW_LEVEL" in low_level_qc.warnings

    frames = 12_000
    boundary_silent_frames = frames * MAX_AUDITION_SILENCE_RATIO_PPM_WARNING // 1_000_000
    silence_boundary = managed / "silence-boundary.wav"
    _write_sample_segments(
        silence_boundary,
        [(boundary_silent_frames, 0), (frames - boundary_silent_frames, 4_000)],
    )
    assert (
        "AUDITION_HIGH_SILENCE_RATIO"
        not in inspect_audition_wav(
            silence_boundary,
            managed_root=managed,
        ).warnings
    )

    high_silence = managed / "high-silence.wav"
    _write_sample_segments(
        high_silence,
        [(boundary_silent_frames + 1, 0), (frames - boundary_silent_frames - 1, 4_000)],
    )
    assert (
        "AUDITION_HIGH_SILENCE_RATIO"
        in inspect_audition_wav(
            high_silence,
            managed_root=managed,
        ).warnings
    )


def test_audition_qc_unusual_duration_warning_preserves_hard_duration_bounds(
    tmp_path: Path,
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    usual_minimum_frames = round(MIN_USUAL_AUDITION_DURATION_MS * 24_000 / 1_000)
    minimum_boundary = managed / "minimum-usual-boundary.wav"
    _write_sample_segments(minimum_boundary, [(usual_minimum_frames, 4_000)])
    assert (
        "AUDITION_UNUSUAL_DURATION"
        not in inspect_audition_wav(
            minimum_boundary,
            managed_root=managed,
        ).warnings
    )

    below_minimum = managed / "below-minimum-usual.wav"
    _write_sample_segments(below_minimum, [(usual_minimum_frames - 1, 4_000)])
    assert (
        "AUDITION_UNUSUAL_DURATION"
        in inspect_audition_wav(
            below_minimum,
            managed_root=managed,
        ).warnings
    )

    usual_maximum_frames = round(MAX_USUAL_AUDITION_DURATION_MS * 24_000 / 1_000)
    maximum_boundary = managed / "maximum-usual-boundary.wav"
    _write_sample_segments(maximum_boundary, [(usual_maximum_frames, 4_000)])
    assert (
        "AUDITION_UNUSUAL_DURATION"
        not in inspect_audition_wav(
            maximum_boundary,
            managed_root=managed,
        ).warnings
    )

    above_maximum = managed / "above-maximum-usual.wav"
    _write_sample_segments(above_maximum, [(usual_maximum_frames + 1, 4_000)])
    above_maximum_qc = inspect_audition_wav(above_maximum, managed_root=managed)
    assert "AUDITION_UNUSUAL_DURATION" in above_maximum_qc.warnings
    assert "AUDITION_DURATION_OUT_OF_BOUNDS" not in above_maximum_qc.blocking_findings


def test_pcm_qc_rejects_lexical_reparse_before_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    artifact = managed / "reparse.wav"
    _write_wav(artifact)
    original_lstat = Path.lstat

    def reparse_lstat(path: Path) -> object:
        metadata = original_lstat(path)
        if path == artifact:
            return SimpleNamespace(
                st_file_attributes=0x400,
                st_mode=metadata.st_mode,
                st_size=metadata.st_size,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(AuditionError, match="link or reparse"):
        inspect_audition_wav(artifact, managed_root=managed)


def test_human_gates_are_immutable_decisions_over_current_evidence() -> None:
    evidence = GateEvidence(
        gate_id="per_role_audition_review",
        evidence_fingerprint=_digest("evidence"),
        prerequisites=("assignment", "clip"),
    )
    current = {"assignment": _digest("assignment"), "clip": _digest("clip")}
    assert (
        evaluate_gate_state(
            evidence,
            current_dependency_fingerprints=current,
            recorded_dependency_fingerprints=current,
            latest_human_decision=None,
        )
        == "pending"
    )
    assert (
        evaluate_gate_state(
            evidence,
            current_dependency_fingerprints=current,
            recorded_dependency_fingerprints=current,
            latest_human_decision="approved",
        )
        == "approved"
    )
    assert (
        evaluate_gate_state(
            evidence,
            current_dependency_fingerprints=current,
            recorded_dependency_fingerprints={
                "assignment": _digest("old"),
                "clip": _digest("clip"),
            },
            latest_human_decision="approved",
        )
        == "stale"
    )
    assert (
        evaluate_gate_state(
            replace(evidence, blockers=("rights",)),
            current_dependency_fingerprints=current,
            recorded_dependency_fingerprints=current,
            latest_human_decision="approved",
        )
        == "blocked"
    )


def test_targeted_invalidation_and_readiness_identity() -> None:
    assert dependent_gate_ids(changed_pronunciation_entry_ids=["entry-1"]) == (
        "pronunciation_review",
        "voice_readiness_review",
    )
    assert dependent_gate_ids(changed_role_ids=["role-1"]) == (
        "per_role_audition_review",
        "narrator_audition_review",
        "character_audition_review",
        "voice_readiness_review",
    )
    assert set(dependent_gate_ids(provider_or_model_changed=True)) < set(AUDITION_GATE_IDS)
    readiness = voice_readiness_fingerprint(
        casting_snapshot_fingerprint=_digest("casting"),
        rights_snapshot_fingerprint=_digest("rights"),
        model_verification_fingerprint=_digest("model"),
        runtime_fingerprint=_digest("runtime"),
        narrator_review_fingerprint=_digest("narrator"),
        character_review_fingerprint=_digest("character"),
        pronunciation_review_fingerprint=_digest("pronunciation"),
    )
    assert len(readiness) == 64
    with pytest.raises(AuditionError, match="invalid fingerprint"):
        voice_readiness_fingerprint(
            casting_snapshot_fingerprint="bad",
            rights_snapshot_fingerprint=_digest("rights"),
            model_verification_fingerprint=_digest("model"),
            runtime_fingerprint=_digest("runtime"),
            narrator_review_fingerprint=_digest("narrator"),
            character_review_fingerprint=_digest("character"),
            pronunciation_review_fingerprint=_digest("pronunciation"),
        )
