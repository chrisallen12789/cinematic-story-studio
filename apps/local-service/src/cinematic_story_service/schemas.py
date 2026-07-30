from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
