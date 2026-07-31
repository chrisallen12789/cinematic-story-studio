from __future__ import annotations

import json
from typing import Literal, cast

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
    fingerprint: Literal["6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec"] = (
        "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec"
    )


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
            raise ValueError("profile.fingerprint must equal expectedProfileFingerprint.")
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


class AnalysisGateDecisionIdsRequest(StrictRequest):
    story_structure_review: str = Field(min_length=1, max_length=128)
    character_registry_review: str = Field(min_length=1, max_length=128)
    dialogue_attribution_review: str = Field(min_length=1, max_length=128)
    whole_book_analysis_review: str = Field(min_length=1, max_length=128)


class CreateCastingRunRequest(StrictRequest):
    expected_analysis_run_id: str = Field(min_length=1, max_length=128)
    expected_snapshot_id: str = Field(min_length=1, max_length=128)
    expected_snapshot_revision: int = Field(ge=1)
    expected_snapshot_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_correction_set_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_import_review_decision_id: str = Field(min_length=1, max_length=128)
    expected_analysis_gate_decision_ids: AnalysisGateDecisionIdsRequest
    expected_catalog_revision_id: str = Field(min_length=1, max_length=128)
    expected_catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_casting_profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def validate_casting_run_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class CastingAgePresentationRangeRequest(StrictRequest):
    minimum: int = Field(ge=0, le=120)
    maximum: int = Field(ge=0, le=120)

    @model_validator(mode="after")
    def validate_order(self) -> CastingAgePresentationRangeRequest:
        if self.minimum > self.maximum:
            raise ValueError("The casting age-presentation range is invalid.")
        return self


class CastingSpeakingRateRangeRequest(StrictRequest):
    minimum: float = Field(ge=0.25, le=4.0)
    maximum: float = Field(ge=0.25, le=4.0)
    unit: Literal["multiplier"]

    @model_validator(mode="after")
    def validate_order(self) -> CastingSpeakingRateRangeRequest:
        if self.minimum > self.maximum:
            raise ValueError("The casting speaking-rate range is invalid.")
        return self


class ProductionRoleRequirementRequest(StrictRequest):
    language: str = Field(min_length=1, max_length=35)
    locales: list[str] = Field(max_length=20)
    age_presentation_range: CastingAgePresentationRangeRequest | None
    vocal_presentations: list[
        Literal[
            "feminine",
            "masculine",
            "androgynous",
            "neutral",
            "varied",
            "unspecified",
        ]
    ] = Field(max_length=6)
    preferred_textures: list[
        Literal[
            "airy",
            "bright",
            "clear",
            "crisp",
            "gravelly",
            "resonant",
            "smooth",
            "warm",
            "textured",
            "varied",
            "unspecified",
        ]
    ] = Field(max_length=11)
    speaking_rate_range: CastingSpeakingRateRangeRequest | None
    required_expressive_range: list[str] = Field(max_length=32)
    long_form_required: bool

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("The casting language is required.")
        return cleaned

    @field_validator("locales")
    @classmethod
    def validate_locales(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 35 for item in cleaned):
            raise ValueError("Casting requirement values are invalid.")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Casting requirement values must be unique.")
        return cleaned

    @field_validator("required_expressive_range")
    @classmethod
    def validate_expressive_range(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 80 for item in cleaned):
            raise ValueError("Casting requirement values are invalid.")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Casting requirement values must be unique.")
        return cleaned

    @field_validator("vocal_presentations", "preferred_textures")
    @classmethod
    def validate_enum_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Casting requirement values must be unique.")
        return value


class CreateCustomProductionRoleRequest(StrictRequest):
    definition_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$",
    )
    label: str = Field(min_length=1, max_length=200)
    performance_requirements: ProductionRoleRequirementRequest
    reason: str = Field(min_length=1, max_length=1000)
    expected_run_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_catalog_revision_id: str = Field(min_length=1, max_length=128)
    expected_catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_snapshot_id: str = Field(min_length=1, max_length=128)
    expected_snapshot_revision: int = Field(ge=1)
    expected_snapshot_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_correction_set_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_casting_profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("label", "reason")
    @classmethod
    def validate_human_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Custom-role human text cannot be blank.")
        if any(ord(character) < 32 and character != "\t" for character in cleaned):
            raise ValueError("Custom-role human text contains control characters.")
        return cleaned

    @field_validator("idempotency_key")
    @classmethod
    def validate_custom_role_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


CastingCorrectionOperation = Literal[
    "select_voice",
    "clear_assignment",
    "lock_assignment",
    "unlock_assignment",
    "mark_intentionally_uncast",
    "change_role_label",
    "change_casting_requirement",
    "acknowledge_restricted_rights",
    "approve_voice_reuse",
    "reject_candidate",
    "record_custom_rationale",
]


class AppendCastingCorrectionRequest(StrictRequest):
    operation: CastingCorrectionOperation
    target_role_id: str = Field(min_length=1, max_length=128)
    expected_role_revision: int = Field(ge=1)
    expected_run_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_snapshot_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_correction_set_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_effective_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    voice_profile_id: str | None = Field(default=None, max_length=128)
    corrected_value: dict[str, JsonValue] | None = Field(
        default=None,
        max_length=32,
    )
    reason: str = Field(min_length=1, max_length=1000)
    supersedes_correction_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("reason")
    @classmethod
    def validate_casting_correction_reason(cls, value: str) -> str:
        return AppendAnalysisCorrectionRequest.validate_correction_reason(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_casting_correction_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)

    @model_validator(mode="after")
    def validate_operation_payload(self) -> AppendCastingCorrectionRequest:
        requires_voice = self.operation in {
            "select_voice",
            "acknowledge_restricted_rights",
            "reject_candidate",
        }
        forbids_voice = self.operation in {
            "clear_assignment",
            "lock_assignment",
            "unlock_assignment",
            "mark_intentionally_uncast",
            "change_role_label",
            "change_casting_requirement",
            "approve_voice_reuse",
            "record_custom_rationale",
        }
        if requires_voice and self.voice_profile_id is None:
            raise ValueError("This casting correction requires voiceProfileId.")
        if forbids_voice and self.voice_profile_id is not None:
            raise ValueError("This casting correction does not accept voiceProfileId.")
        if (
            self.operation
            in {
                "clear_assignment",
                "lock_assignment",
                "unlock_assignment",
                "change_role_label",
                "change_casting_requirement",
                "acknowledge_restricted_rights",
                "approve_voice_reuse",
                "reject_candidate",
                "record_custom_rationale",
            }
            and not self.corrected_value
        ):
            raise ValueError("This casting correction requires correctedValue.")
        if self.corrected_value is not None:
            encoded = json.dumps(
                self.corrected_value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(encoded) > 16_384:
                raise ValueError("The casting correction value is too large.")
        value = self.corrected_value or {}

        def valid_identifier(candidate: JsonValue, *, maximum: int = 128) -> bool:
            return (
                isinstance(candidate, str)
                and bool(candidate)
                and len(candidate) <= maximum
            )

        if self.operation == "select_voice" and value:
            if (
                set(value) != {"voiceProfileId"}
                or value.get("voiceProfileId") != self.voice_profile_id
            ):
                raise ValueError("The voice selection value is invalid.")
        elif self.operation == "clear_assignment":
            if set(value) != {"expectedAssignmentId"} or not valid_identifier(
                value.get("expectedAssignmentId")
            ):
                raise ValueError("The assignment-clear value is invalid.")
        elif self.operation == "lock_assignment":
            if set(value) != {"assignmentId"} or not valid_identifier(
                value.get("assignmentId")
            ):
                raise ValueError("The assignment-lock value is invalid.")
        elif self.operation == "unlock_assignment":
            if set(value) != {"lockedAssignmentId"} or not valid_identifier(
                value.get("lockedAssignmentId")
            ):
                raise ValueError("The assignment-unlock value is invalid.")
        elif self.operation == "mark_intentionally_uncast" and value:
            if (
                set(value) != {"intentionallyUncast"}
                or value.get("intentionallyUncast") is not True
            ):
                raise ValueError("The intentionally-uncast value is invalid.")
        elif self.operation == "change_role_label":
            label = value.get("effectiveDisplayLabel")
            if (
                set(value) != {"effectiveDisplayLabel"}
                or not isinstance(label, str)
                or not label.strip()
                or len(label) > 200
            ):
                raise ValueError("The role-label correction is invalid.")
        elif self.operation == "change_casting_requirement":
            requirement = value.get("requirement")
            required_keys = {
                "language",
                "locales",
                "agePresentationRange",
                "vocalPresentations",
                "preferredTextures",
                "speakingRateRange",
                "requiredExpressiveRange",
                "longFormRequired",
            }
            if (
                set(value) != {"requirement"}
                or not isinstance(requirement, dict)
                or set(requirement) != required_keys
            ):
                raise ValueError("The casting requirement value is invalid.")
            language = requirement.get("language")
            locales = requirement.get("locales")
            presentations = requirement.get("vocalPresentations")
            textures = requirement.get("preferredTextures")
            expressions = requirement.get("requiredExpressiveRange")
            if (
                not isinstance(language, str)
                or not language.strip()
                or len(language) > 35
                or not isinstance(locales, list)
                or len(locales) > 20
                or any(
                    not isinstance(item, str) or not item.strip() or len(item) > 35
                    for item in locales
                )
                or len(locales) != len(set(locales))
                or not isinstance(presentations, list)
                or len(presentations) > 6
                or any(
                    item
                    not in {
                        "feminine",
                        "masculine",
                        "androgynous",
                        "neutral",
                        "varied",
                        "unspecified",
                    }
                    for item in presentations
                )
                or len(presentations) != len(set(presentations))
                or not isinstance(textures, list)
                or len(textures) > 11
                or any(
                    item
                    not in {
                        "airy",
                        "bright",
                        "clear",
                        "crisp",
                        "gravelly",
                        "resonant",
                        "smooth",
                        "warm",
                        "textured",
                        "varied",
                        "unspecified",
                    }
                    for item in textures
                )
                or len(textures) != len(set(textures))
                or not isinstance(expressions, list)
                or len(expressions) > 32
                or any(
                    not isinstance(item, str)
                    or not item.strip()
                    or len(item) > 80
                    for item in expressions
                )
                or len(expressions) != len(set(expressions))
                or not isinstance(requirement.get("longFormRequired"), bool)
            ):
                raise ValueError("The casting requirement metadata is invalid.")
            age_range = requirement.get("agePresentationRange")
            if age_range is not None and (
                not isinstance(age_range, dict)
                or set(age_range) != {"minimum", "maximum"}
                or not isinstance(age_range.get("minimum"), int)
                or isinstance(age_range.get("minimum"), bool)
                or not isinstance(age_range.get("maximum"), int)
                or isinstance(age_range.get("maximum"), bool)
                or not 0
                <= cast(int, age_range["minimum"])
                <= cast(int, age_range["maximum"])
                <= 120
            ):
                raise ValueError("The casting age-presentation range is invalid.")
            speaking_rate = requirement.get("speakingRateRange")
            if speaking_rate is not None and (
                not isinstance(speaking_rate, dict)
                or set(speaking_rate) != {"minimum", "maximum", "unit"}
                or speaking_rate.get("unit") != "multiplier"
                or not isinstance(speaking_rate.get("minimum"), (int, float))
                or isinstance(speaking_rate.get("minimum"), bool)
                or not isinstance(speaking_rate.get("maximum"), (int, float))
                or isinstance(speaking_rate.get("maximum"), bool)
                or not 0.25
                <= float(cast(int | float, speaking_rate["minimum"]))
                <= float(cast(int | float, speaking_rate["maximum"]))
                <= 4.0
            ):
                raise ValueError("The casting speaking-rate range is invalid.")
        elif self.operation == "approve_voice_reuse":
            conflict_id = value.get("conflictId")
            role_ids = value.get("approvedRoleIds")
            if (
                set(value) != {"conflictId", "approvedRoleIds"}
                or not isinstance(conflict_id, str)
                or not conflict_id
                or len(conflict_id) > 128
                or not isinstance(role_ids, list)
                or not 2 <= len(role_ids) <= 300
                or any(
                    not isinstance(item, str) or not item or len(item) > 128
                    for item in role_ids
                )
                or len(role_ids) != len(set(role_ids))
            ):
                raise ValueError("The voice-reuse disposition is invalid.")
        elif self.operation == "acknowledge_restricted_rights":
            rights_record_id = value.get("rightsRecordId")
            rights_record_revision = value.get("rightsRecordRevision")
            if (
                set(value) != {"rightsRecordId", "rightsRecordRevision"}
                or not valid_identifier(rights_record_id)
                or not isinstance(rights_record_revision, int)
                or isinstance(rights_record_revision, bool)
                or rights_record_revision < 1
            ):
                raise ValueError("The restricted-rights acknowledgement is invalid.")
        elif self.operation == "reject_candidate":
            if set(value) != {"candidateId"} or not valid_identifier(
                value.get("candidateId")
            ):
                raise ValueError("The candidate-rejection value is invalid.")
        elif self.operation == "record_custom_rationale":
            rationale = value.get("rationale")
            if (
                set(value) != {"rationale"}
                or not isinstance(rationale, str)
                or not rationale.strip()
                or len(rationale) > 2_000
            ):
                raise ValueError("The custom casting rationale is invalid.")
        elif self.operation not in {
            "select_voice",
            "clear_assignment",
            "lock_assignment",
            "unlock_assignment",
            "mark_intentionally_uncast",
            "change_role_label",
            "change_casting_requirement",
            "acknowledge_restricted_rights",
            "approve_voice_reuse",
            "reject_candidate",
            "record_custom_rationale",
        } and value:
            raise ValueError("This casting correction does not accept correctedValue.")
        return self


class DecideCastingReviewRequest(StrictRequest):
    decision: Literal["approve", "reject", "request_changes"]
    expected_revision: int = Field(ge=1)
    expected_evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_run_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_approved_cast_snapshot_id: str = Field(min_length=1, max_length=128)
    expected_approved_cast_snapshot_revision: int = Field(ge=1)
    warning_acknowledgement_ids: list[str] = Field(
        default_factory=list,
        max_length=32,
    )
    rationale: str = Field(min_length=1, max_length=4000)
    supersedes_decision_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("rationale")
    @classmethod
    def validate_casting_review_rationale(cls, value: str) -> str:
        return DecideAnalysisReviewRequest.validate_analysis_rationale(value)

    @field_validator("warning_acknowledgement_ids")
    @classmethod
    def validate_casting_warning_acknowledgements(cls, value: list[str]) -> list[str]:
        return DecideAnalysisReviewRequest.validate_acknowledged_warning_ids(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_casting_review_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)
