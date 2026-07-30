from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class StrictRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=False,
    )


class CreateProjectRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Project names cannot be blank.")
        if any(ord(character) < 32 for character in value):
            raise ValueError("Project names cannot contain control characters.")
        return value.strip()


class CorrectDialogueSpeakerRequest(StrictRequest):
    character_id: str | None
    reason: str | None = Field(default=None, max_length=500)
    expected_revision: int = Field(ge=1)

    @field_validator("character_id")
    @classmethod
    def validate_character_id(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value) > 80):
            raise ValueError("The character ID is invalid.")
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("A supplied correction reason cannot be blank.")
        if any(ord(character) < 32 and character not in "\t" for character in value):
            raise ValueError("The correction reason contains control characters.")
        return value


class CreateJobRequest(StrictRequest):
    type: Literal["analyze_story"]
    input_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not value.strip() or any(ord(character) < 33 for character in value):
            raise ValueError("The idempotency key is invalid.")
        return value


class DecideImportReviewRequest(StrictRequest):
    review_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approved", "changes_requested", "rejected"]
    rationale: str | None = Field(default=None, max_length=2000)
    expected_revision: int = Field(ge=1)
    evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if any(ord(character) < 32 and character != "\t" for character in value):
            raise ValueError("The Import Review rationale contains control characters.")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_review_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class AnalysisProfileRequest(StrictRequest):
    profile_id: Literal["whole-book-intelligence-v1"] = "whole-book-intelligence-v1"
    semantic_version: Literal["1.0.0"] = "1.0.0"
    fingerprint: Literal[
        "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec"
    ] = "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec"


class CreateAnalysisRunRequest(StrictRequest):
    expected_extraction_id: str = Field(min_length=1, max_length=128)
    expected_extraction_revision: int = Field(ge=1)
    expected_review_id: str = Field(min_length=1, max_length=128)
    expected_review_revision: int = Field(ge=1)
    expected_evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: AnalysisProfileRequest = Field(default_factory=AnalysisProfileRequest)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def validate_analysis_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)

    @model_validator(mode="after")
    def validate_profile_fingerprint(self) -> CreateAnalysisRunRequest:
        if self.profile.fingerprint != self.expected_profile_fingerprint:
            raise ValueError(
                "profile.fingerprint must equal expectedProfileFingerprint."
            )
        return self


AnalysisCollection = Literal[
    "agent-executions",
    "chapters",
    "scenes",
    "beats",
    "characters",
    "mentions",
    "dialogue-lines",
    "narration-spans",
    "pov-segments",
    "locations",
    "timeline-events",
    "temporal-constraints",
    "relationships",
    "emotional-states",
    "dramatic-intents",
    "continuity-findings",
]

CorrectionCategory = Literal[
    "structure_boundary",
    "structure_label",
    "character_identity",
    "character_alias",
    "character_merge",
    "character_split",
    "mention_resolution",
    "dialogue_speaker",
    "point_of_view",
    "location_identity",
    "location_alias",
    "temporal_order",
    "relationship",
    "emotional_state",
    "dramatic_intent",
    "continuity_disposition",
]


class AppendAnalysisCorrectionRequest(StrictRequest):
    category: CorrectionCategory
    target_collection: AnalysisCollection
    target_entity_id: str = Field(min_length=1, max_length=128)
    expected_target_revision: int = Field(ge=1)
    expected_run_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_value_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    patch: dict[str, JsonValue] = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=1000)
    supersedes_correction_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("reason")
    @classmethod
    def validate_correction_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("The correction reason must not be blank.")
        if any(ord(character) < 32 and character != "\t" for character in cleaned):
            raise ValueError("The correction reason contains control characters.")
        return cleaned

    @field_validator("idempotency_key")
    @classmethod
    def validate_correction_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class DecideAnalysisReviewRequest(StrictRequest):
    decision: Literal["approve", "reject", "request_changes"]
    expected_revision: int = Field(ge=1)
    expected_artifact_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledged_warning_ids: list[str] = Field(default_factory=list, max_length=32)
    rationale: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("rationale")
    @classmethod
    def validate_analysis_rationale(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A review rationale is required.")
        if any(ord(character) < 32 and character != "\t" for character in cleaned):
            raise ValueError("The review rationale contains control characters.")
        return cleaned

    @field_validator("acknowledged_warning_ids")
    @classmethod
    def validate_acknowledged_warning_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Warning acknowledgements must be unique.")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_analysis_review_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)
