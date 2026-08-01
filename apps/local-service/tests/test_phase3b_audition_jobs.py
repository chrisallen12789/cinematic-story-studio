from __future__ import annotations

import pytest

from cinematic_story_service.audition_jobs import (
    AUDITION_PIPELINE_STAGES,
    AuditionCheckpointError,
    advance_checkpoint,
    deterministic_clip_id,
    new_checkpoint,
    redacted_progress_event,
    retry_checkpoint,
)
from cinematic_story_service.util import sha256_text


def _digest(label: str) -> str:
    return sha256_text(label)


def _through_audio_validation():
    checkpoint = new_checkpoint(job_id="job-1", attempt=1, input_fingerprint=_digest("input"))
    evidence = {
        "freeze_assignment_and_catalog": {"casting_snapshot_fingerprint": _digest("cast")},
        "resolve_model_package": {"model_package_fingerprint": _digest("model")},
        "acquire_local_runtime": {"runtime_fingerprint": _digest("runtime")},
        "compile_pronunciation_plan": {"pronunciation_plan_fingerprint": _digest("pronunciation")},
        "build_normalization_plan": {"normalization_plan_fingerprint": _digest("normalization")},
        "calculate_cache_key": {"cache_key_fingerprint": _digest("cache")},
        "cache_or_synthesize": {"cache_hit": False},
        "validate_audio_artifact": {"artifact_fingerprint": _digest("artifact")},
    }
    for stage in AUDITION_PIPELINE_STAGES[:10]:
        checkpoint = advance_checkpoint(checkpoint, stage, evidence=evidence.get(stage))
    return checkpoint


def test_pipeline_advances_in_exact_order_and_publishes_idempotently() -> None:
    checkpoint = _through_audio_validation()
    clip_id = deterministic_clip_id(checkpoint)
    checkpoint = advance_checkpoint(
        checkpoint, "publish_audition_clip", evidence={"clip_id": clip_id}
    )
    checkpoint = advance_checkpoint(checkpoint, "publish_audition_session")
    checkpoint = advance_checkpoint(checkpoint, "release_or_idle_runtime")

    assert checkpoint.successful
    assert checkpoint.runtime_released
    assert deterministic_clip_id(_through_audio_validation()) == clip_id
    assert redacted_progress_event(checkpoint)["stage"] == "completed"


def test_pipeline_rejects_skips_extra_evidence_and_unvalidated_publication() -> None:
    checkpoint = new_checkpoint(job_id="job-1", attempt=1, input_fingerprint=_digest("input"))
    with pytest.raises(AuditionCheckpointError, match="out of order"):
        advance_checkpoint(checkpoint, "validate_rights")
    with pytest.raises(AuditionCheckpointError, match="evidence shape"):
        advance_checkpoint(
            checkpoint,
            "validate_phase3a_prerequisites",
            evidence={"manuscriptText": "must never be checkpointed"},
        )
    with pytest.raises(AuditionCheckpointError, match="Validated audio"):
        deterministic_clip_id(checkpoint)


def test_retry_preserves_durable_resolution_but_reacquires_runtime() -> None:
    checkpoint = _through_audio_validation()
    retried = retry_checkpoint(checkpoint, next_attempt=2)

    assert retried.attempt == 2
    assert retried.completed_stage_count == 4
    assert retried.model_package_fingerprint == _digest("model")
    assert retried.runtime_fingerprint is None
    assert retried.artifact_fingerprint is None
    assert retried.next_stage == "acquire_local_runtime"
    with pytest.raises(AuditionCheckpointError, match="increment"):
        retry_checkpoint(checkpoint, next_attempt=3)


def test_progress_event_is_redacted_and_bounded() -> None:
    checkpoint = _through_audio_validation()
    event = redacted_progress_event(checkpoint)
    serialized = repr(event)

    assert set(event) == {
        "attempt",
        "checkpointFingerprint",
        "completedStageCount",
        "jobId",
        "stage",
        "totalStageCount",
    }
    assert "cache" not in serialized.casefold()
    assert "artifact" not in serialized.casefold()
    assert len(serialized) < 500
