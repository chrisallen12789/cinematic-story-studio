"""Versioned, redacted checkpoints for durable Phase 3B audition generation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Final, Literal, cast

from .util import request_fingerprint, stable_id

AUDITION_CHECKPOINT_SCHEMA_VERSION: Final = 1
AUDITION_PIPELINE_STAGES: Final = (
    "validate_phase3a_prerequisites",
    "validate_rights",
    "freeze_assignment_and_catalog",
    "resolve_model_package",
    "acquire_local_runtime",
    "compile_pronunciation_plan",
    "build_normalization_plan",
    "calculate_cache_key",
    "cache_or_synthesize",
    "validate_audio_artifact",
    "publish_audition_clip",
    "publish_audition_session",
    "release_or_idle_runtime",
)

AuditionPipelineStage = Literal[
    "validate_phase3a_prerequisites",
    "validate_rights",
    "freeze_assignment_and_catalog",
    "resolve_model_package",
    "acquire_local_runtime",
    "compile_pronunciation_plan",
    "build_normalization_plan",
    "calculate_cache_key",
    "cache_or_synthesize",
    "validate_audio_artifact",
    "publish_audition_clip",
    "publish_audition_session",
    "release_or_idle_runtime",
]

_DIGEST_FIELDS: Final = (
    "input_fingerprint",
    "casting_snapshot_fingerprint",
    "model_package_fingerprint",
    "runtime_fingerprint",
    "pronunciation_plan_fingerprint",
    "normalization_plan_fingerprint",
    "cache_key_fingerprint",
    "artifact_fingerprint",
)


class AuditionCheckpointError(ValueError):
    """A durable audition checkpoint is stale, malformed, or out of order."""


@dataclass(frozen=True, slots=True)
class AuditionFailure:
    code: str
    retryable: bool
    public_message: str

    def __post_init__(self) -> None:
        if (
            not self.code
            or len(self.code) > 80
            or not self.code.replace("_", "").isalnum()
            or len(self.public_message) > 300
        ):
            raise AuditionCheckpointError("The audition failure classification is invalid.")


@dataclass(frozen=True, slots=True)
class AuditionCheckpoint:
    job_id: str
    attempt: int
    input_fingerprint: str
    completed_stage_count: int = 0
    casting_snapshot_fingerprint: str | None = None
    model_package_fingerprint: str | None = None
    runtime_fingerprint: str | None = None
    pronunciation_plan_fingerprint: str | None = None
    normalization_plan_fingerprint: str | None = None
    cache_key_fingerprint: str | None = None
    cache_hit: bool | None = None
    artifact_fingerprint: str | None = None
    clip_id: str | None = None
    session_published: bool = False
    runtime_released: bool = False
    schema_version: int = AUDITION_CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.job_id or self.attempt < 1:
            raise AuditionCheckpointError("The audition checkpoint identity is invalid.")
        if self.schema_version != AUDITION_CHECKPOINT_SCHEMA_VERSION:
            raise AuditionCheckpointError("The audition checkpoint version is unsupported.")
        if not 0 <= self.completed_stage_count <= len(AUDITION_PIPELINE_STAGES):
            raise AuditionCheckpointError("The audition checkpoint stage is invalid.")
        for field in _DIGEST_FIELDS:
            value = getattr(self, field)
            if value is not None and (
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            ):
                raise AuditionCheckpointError("The audition checkpoint contains an invalid digest.")
        if self.completed_stage_count >= 3 and self.casting_snapshot_fingerprint is None:
            raise AuditionCheckpointError("The frozen casting evidence is missing.")
        if self.completed_stage_count >= 4 and self.model_package_fingerprint is None:
            raise AuditionCheckpointError("The model-package evidence is missing.")
        if self.completed_stage_count >= 5 and self.runtime_fingerprint is None:
            raise AuditionCheckpointError("The runtime evidence is missing.")
        if self.completed_stage_count >= 6 and self.pronunciation_plan_fingerprint is None:
            raise AuditionCheckpointError("The pronunciation evidence is missing.")
        if self.completed_stage_count >= 7 and self.normalization_plan_fingerprint is None:
            raise AuditionCheckpointError("The normalization evidence is missing.")
        if self.completed_stage_count >= 8 and self.cache_key_fingerprint is None:
            raise AuditionCheckpointError("The cache identity is missing.")
        if self.completed_stage_count >= 9 and self.cache_hit is None:
            raise AuditionCheckpointError("The cache-or-synthesis outcome is missing.")
        if self.completed_stage_count >= 10 and self.artifact_fingerprint is None:
            raise AuditionCheckpointError("Validated audio evidence is missing.")
        if self.completed_stage_count >= 11 and self.clip_id is None:
            raise AuditionCheckpointError("The published clip identity is missing.")
        if self.completed_stage_count >= 12 and not self.session_published:
            raise AuditionCheckpointError("The audition session is not published.")
        if self.completed_stage_count >= 13 and not self.runtime_released:
            raise AuditionCheckpointError("The provider runtime was not released.")

    @property
    def next_stage(self) -> AuditionPipelineStage | None:
        if self.completed_stage_count == len(AUDITION_PIPELINE_STAGES):
            return None
        return AUDITION_PIPELINE_STAGES[self.completed_stage_count]

    @property
    def successful(self) -> bool:
        return self.completed_stage_count == len(AUDITION_PIPELINE_STAGES)

    def material(self) -> dict[str, object]:
        return {
            "artifactFingerprint": self.artifact_fingerprint,
            "attempt": self.attempt,
            "cacheHit": self.cache_hit,
            "cacheKeyFingerprint": self.cache_key_fingerprint,
            "castingSnapshotFingerprint": self.casting_snapshot_fingerprint,
            "clipId": self.clip_id,
            "completedStageCount": self.completed_stage_count,
            "inputFingerprint": self.input_fingerprint,
            "jobId": self.job_id,
            "modelPackageFingerprint": self.model_package_fingerprint,
            "normalizationPlanFingerprint": self.normalization_plan_fingerprint,
            "pronunciationPlanFingerprint": self.pronunciation_plan_fingerprint,
            "runtimeFingerprint": self.runtime_fingerprint,
            "runtimeReleased": self.runtime_released,
            "schemaVersion": self.schema_version,
            "sessionPublished": self.session_published,
        }

    @property
    def fingerprint(self) -> str:
        return request_fingerprint(self.material())


def new_checkpoint(*, job_id: str, attempt: int, input_fingerprint: str) -> AuditionCheckpoint:
    return AuditionCheckpoint(job_id=job_id, attempt=attempt, input_fingerprint=input_fingerprint)


def advance_checkpoint(
    checkpoint: AuditionCheckpoint,
    stage: AuditionPipelineStage,
    *,
    evidence: Mapping[str, object] | None = None,
) -> AuditionCheckpoint:
    """Advance exactly one stage; missing or extra evidence fails closed."""

    if checkpoint.next_stage != stage:
        raise AuditionCheckpointError("The audition checkpoint transition is out of order.")
    supplied = dict(evidence or {})
    changes: dict[str, object] = {"completed_stage_count": checkpoint.completed_stage_count + 1}
    expected: set[str]
    if stage in {"validate_phase3a_prerequisites", "validate_rights"}:
        expected = set()
    elif stage == "freeze_assignment_and_catalog":
        expected = {"casting_snapshot_fingerprint"}
    elif stage == "resolve_model_package":
        expected = {"model_package_fingerprint"}
    elif stage == "acquire_local_runtime":
        expected = {"runtime_fingerprint"}
    elif stage == "compile_pronunciation_plan":
        expected = {"pronunciation_plan_fingerprint"}
    elif stage == "build_normalization_plan":
        expected = {"normalization_plan_fingerprint"}
    elif stage == "calculate_cache_key":
        expected = {"cache_key_fingerprint"}
    elif stage == "cache_or_synthesize":
        expected = {"cache_hit"}
    elif stage == "validate_audio_artifact":
        expected = {"artifact_fingerprint"}
    elif stage == "publish_audition_clip":
        expected = {"clip_id"}
    elif stage == "publish_audition_session":
        expected = set()
        changes["session_published"] = True
    else:
        expected = set()
        changes["runtime_released"] = True
    if set(supplied) != expected:
        raise AuditionCheckpointError("The audition checkpoint evidence shape is invalid.")
    changes.update(supplied)
    return replace(checkpoint, **cast(Any, changes))


def retry_checkpoint(checkpoint: AuditionCheckpoint, *, next_attempt: int) -> AuditionCheckpoint:
    """Resume durable evidence but always reacquire an ephemeral provider runtime."""

    if next_attempt != checkpoint.attempt + 1:
        raise AuditionCheckpointError("Audition retries must increment the attempt exactly once.")
    durable_stage_count = min(checkpoint.completed_stage_count, 4)
    return AuditionCheckpoint(
        job_id=checkpoint.job_id,
        attempt=next_attempt,
        input_fingerprint=checkpoint.input_fingerprint,
        completed_stage_count=durable_stage_count,
        casting_snapshot_fingerprint=checkpoint.casting_snapshot_fingerprint,
        model_package_fingerprint=checkpoint.model_package_fingerprint,
    )


def deterministic_clip_id(checkpoint: AuditionCheckpoint) -> str:
    if checkpoint.completed_stage_count < 10 or checkpoint.artifact_fingerprint is None:
        raise AuditionCheckpointError("Validated audio is required before clip publication.")
    return stable_id(
        "phase3b-audition-clip",
        checkpoint.job_id,
        checkpoint.input_fingerprint,
        checkpoint.artifact_fingerprint,
    )


def redacted_progress_event(checkpoint: AuditionCheckpoint) -> dict[str, object]:
    """Return event material without manuscript text, paths, controls, or cache keys."""

    stage = "completed" if checkpoint.successful else checkpoint.next_stage or "completed"
    return {
        "attempt": checkpoint.attempt,
        "checkpointFingerprint": checkpoint.fingerprint,
        "completedStageCount": checkpoint.completed_stage_count,
        "jobId": checkpoint.job_id,
        "stage": stage,
        "totalStageCount": len(AUDITION_PIPELINE_STAGES),
    }
