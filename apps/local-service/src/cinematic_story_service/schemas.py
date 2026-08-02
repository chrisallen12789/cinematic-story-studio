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
            return isinstance(candidate, str) and bool(candidate) and len(candidate) <= maximum

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
            if set(value) != {"assignmentId"} or not valid_identifier(value.get("assignmentId")):
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
                    not isinstance(item, str) or not item.strip() or len(item) > 80
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
                    not isinstance(item, str) or not item or len(item) > 128 for item in role_ids
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
            if set(value) != {"candidateId"} or not valid_identifier(value.get("candidateId")):
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
        elif (
            self.operation
            not in {
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
            }
            and value
        ):
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


PronunciationScopeRequest = Literal[
    "project",
    "narrator",
    "character_role",
    "chapter",
    "scene",
    "custom",
]
PronunciationRepresentationRequest = Literal[
    "provider_neutral",
    "ipa",
    "provider_specific",
]
AuditionScriptKindRequest = Literal[
    "standardized_synthetic",
    "approved_manuscript_excerpt",
    "role_dialogue_excerpt",
    "narrator_excerpt",
    "pronunciation_test",
    "synthetic_fallback",
]


def _bounded_plain_text(value: str, *, label: str) -> str:
    cleaned = value.strip()
    if not cleaned or any(ord(character) < 32 and character != "\t" for character in cleaned):
        raise ValueError(f"{label} contains invalid control characters.")
    if "<" in cleaned or ">" in cleaned:
        raise ValueError(f"{label} cannot contain provider markup.")
    return cleaned


class CreatePronunciationEntryRequest(StrictRequest):
    expected_dictionary_revision: int = Field(ge=0)
    expected_dictionary_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    written_form: str = Field(min_length=1, max_length=120)
    language: str = Field(pattern=r"^[a-z]{2,3}$")
    locale: str | None = Field(
        default=None,
        pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$",
    )
    scope: PronunciationScopeRequest
    scope_id: str | None = Field(default=None, min_length=1, max_length=128)
    representation: PronunciationRepresentationRequest
    pronunciation: str = Field(min_length=1, max_length=256)
    ipa: str | None = Field(default=None, min_length=1, max_length=256)
    provider_id: str | None = Field(default=None, min_length=1, max_length=80)
    provider_compiled_value: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
    )
    case_sensitive: bool = False
    match_rule: Literal["whole_word", "phrase"] = "whole_word"
    priority: int = Field(default=0, ge=-1000, le=1000)
    reason: str = Field(min_length=1, max_length=1000)
    supersedes_entry_id: str | None = Field(default=None, min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator(
        "written_form",
        "pronunciation",
        "ipa",
        "provider_compiled_value",
        "reason",
    )
    @classmethod
    def validate_pronunciation_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_plain_text(value, label="Pronunciation text")

    @field_validator("idempotency_key")
    @classmethod
    def validate_pronunciation_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)

    @model_validator(mode="after")
    def validate_pronunciation_contract(self) -> CreatePronunciationEntryRequest:
        if self.scope == "project" and self.scope_id is not None:
            raise ValueError("Project pronunciation entries cannot have scopeId.")
        if self.scope != "project" and self.scope_id is None:
            raise ValueError("Scoped pronunciation entries require scopeId.")
        if self.representation == "ipa" and self.ipa is None:
            raise ValueError("IPA pronunciation entries require ipa.")
        if self.representation == "provider_specific" and (
            self.provider_id is None or self.provider_compiled_value is None
        ):
            raise ValueError(
                "Provider-specific pronunciation entries require providerId and "
                "providerCompiledValue."
            )
        if self.representation != "provider_specific" and (
            self.provider_id is not None or self.provider_compiled_value is not None
        ):
            raise ValueError(
                "Only provider-specific pronunciation entries accept compiled provider values."
            )
        return self


class DecidePronunciationEntryRequest(StrictRequest):
    decision: Literal["approve", "reject", "request_changes"]
    expected_entry_revision: int = Field(ge=1)
    expected_entry_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_dictionary_revision: int = Field(ge=1)
    expected_dictionary_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    rationale: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("rationale")
    @classmethod
    def validate_pronunciation_rationale(cls, value: str) -> str:
        return DecideAnalysisReviewRequest.validate_analysis_rationale(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_pronunciation_decision_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class TextCodePointSpanRequest(StrictRequest):
    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> TextCodePointSpanRequest:
        if self.start >= self.end or self.end - self.start > 4000:
            raise ValueError("The audition source span is invalid.")
        return self


class HumanActorBindingRequest(StrictRequest):
    classification: Literal["human"]
    actor_id: str = Field(min_length=1, max_length=128)


class HumanSpeechProvenanceBindingRequest(StrictRequest):
    origin: Literal["human"]
    producer_id: str = Field(min_length=1, max_length=128)
    producer_version: str = Field(min_length=1, max_length=40)
    recorded_at: str = Field(min_length=1, max_length=64)
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class RestrictedLocalAuditionAcknowledgementBindingRequest(StrictRequest):
    contract_version: Literal["1.0.0"]
    acknowledgement_id: str = Field(min_length=1, max_length=128)
    actor: HumanActorBindingRequest
    acknowledged_at: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)
    warning_text: str = Field(min_length=1, max_length=1000)
    warning_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    inventory_record_id: str = Field(min_length=1, max_length=128)
    inventory_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_id: Literal["kokoro-local-onnx"]
    provider_version: str = Field(min_length=1, max_length=40)
    model_id: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=40)
    model_package_id: str = Field(min_length=1, max_length=128)
    model_package_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    voice_profile_id: str = Field(min_length=1, max_length=128)
    voice_profile_version: str = Field(min_length=1, max_length=40)
    voice_profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_revision_id: str = Field(min_length=1, max_length=128)
    catalog_revision_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    voice_tensor_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rights_record_id: str = Field(min_length=1, max_length=128)
    rights_record_revision: int = Field(ge=1)
    rights_record_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    restricted_rights_correction_id: str = Field(min_length=1, max_length=128)
    restricted_rights_correction_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_installation_acknowledgement_event_id: str = Field(
        min_length=1,
        max_length=128,
    )
    model_verification_id: str = Field(min_length=1, max_length=128)
    model_verification_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    private_local_audition_only: Literal[True]
    production_export_authorized: Literal[False]
    commercial_distribution_authorized: Literal[False]
    marketplace_resale_authorized: Literal[False]
    cloning_authorized: Literal[False]
    real_person_imitation_authorized: Literal[False]
    acknowledgement_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    immutable: Literal[True]
    provenance: HumanSpeechProvenanceBindingRequest


class GovernedLocalVoiceActivationBindingRequest(StrictRequest):
    contract_version: Literal["1.0.0"]
    acknowledgement: RestrictedLocalAuditionAcknowledgementBindingRequest
    cast_assignment_id: str = Field(min_length=1, max_length=128)
    cast_assignment_revision: int = Field(ge=1)
    cast_assignment_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_cast_snapshot_id: str = Field(min_length=1, max_length=128)
    approved_cast_snapshot_revision: int = Field(ge=1)
    approved_cast_snapshot_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_profile_id: str = Field(min_length=1, max_length=128)
    runtime_profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    private_local_audition_only: Literal[True]
    production_export_eligible: Literal[False]
    binding_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class AuditionEvidenceBindingRequest(StrictRequest):
    project_id: str = Field(min_length=1, max_length=128)
    source_document_id: str = Field(min_length=1, max_length=128)
    source_revision: int = Field(ge=1)
    extraction_id: str = Field(min_length=1, max_length=128)
    extraction_revision: int = Field(ge=1)
    extracted_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    phase2_run_id: str = Field(min_length=1, max_length=128)
    phase2_snapshot_id: str = Field(min_length=1, max_length=128)
    phase2_snapshot_revision: int = Field(ge=1)
    phase2_snapshot_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    phase2_correction_set_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    casting_run_id: str = Field(min_length=1, max_length=128)
    approved_cast_snapshot_id: str = Field(min_length=1, max_length=128)
    approved_cast_snapshot_revision: int = Field(ge=1)
    approved_cast_snapshot_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    cast_assignment_id: str = Field(min_length=1, max_length=128)
    cast_assignment_revision: int = Field(ge=1)
    voice_profile_id: str = Field(min_length=1, max_length=128)
    voice_profile_version: str = Field(min_length=1, max_length=40)
    voice_runtime_binding_id: str = Field(min_length=1, max_length=128)
    voice_runtime_binding_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_voice_id: str = Field(min_length=1, max_length=120)
    provider_id: Literal["deterministic-pcm-wav-fixture", "kokoro-local-onnx"]
    provider_version: str = Field(min_length=1, max_length=40)
    model_id: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=40)
    catalog_revision_id: str = Field(min_length=1, max_length=128)
    catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    rights_record_id: str = Field(min_length=1, max_length=128)
    rights_record_revision: int = Field(ge=1)
    rights_record_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    pronunciation_dictionary_id: str = Field(min_length=1, max_length=128)
    pronunciation_dictionary_revision: int = Field(ge=1)
    pronunciation_dictionary_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_profile_id: str = Field(min_length=1, max_length=128)
    runtime_profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_package_id: str = Field(min_length=1, max_length=128)
    model_package_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    governed_local_voice_activation: GovernedLocalVoiceActivationBindingRequest | None = None
    producer_version: str = Field(min_length=1, max_length=40)


class RestrictedLocalAuditionActivationRequest(StrictRequest):
    expected_inventory_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_warning_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def validate_activation_reason(cls, value: str) -> str:
        return AppendAnalysisCorrectionRequest.validate_correction_reason(value)


class CreateAuditionSessionRequest(StrictRequest):
    role_id: str = Field(min_length=1, max_length=128)
    evidence: AuditionEvidenceBindingRequest
    restricted_local_audition_activation: RestrictedLocalAuditionActivationRequest | None = None
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def validate_audition_session_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)

    @model_validator(mode="after")
    def validate_role_binding(self) -> CreateAuditionSessionRequest:
        if self.evidence.project_id == self.role_id:
            raise ValueError("The audition role and project identities cannot alias.")
        return self


class CreateAuditionScriptRequest(StrictRequest):
    audition_session_id: str = Field(min_length=1, max_length=128)
    expected_session_revision: int = Field(ge=1)
    kind: AuditionScriptKindRequest
    text: str = Field(min_length=1, max_length=4000)
    source_document_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_revision: int | None = Field(default=None, ge=1)
    source_span: TextCodePointSpanRequest | None = None
    source_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted_optional_normalization_ids: list[str] = Field(
        default_factory=list,
        max_length=2000,
    )
    custom_pronunciation_scope_ids: list[str] = Field(
        default_factory=list,
        max_length=50,
    )
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("text")
    @classmethod
    def validate_script_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("The audition script text is invalid.")
        return value

    @field_validator("accepted_optional_normalization_ids")
    @classmethod
    def validate_normalization_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item or len(item) > 128 for item in value):
            raise ValueError("Accepted normalization IDs must be bounded and unique.")
        return value

    @field_validator("custom_pronunciation_scope_ids")
    @classmethod
    def validate_custom_pronunciation_scope_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item or len(item) > 128 for item in value):
            raise ValueError("Custom pronunciation scope IDs must be bounded and unique.")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_audition_script_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)

    @model_validator(mode="after")
    def validate_script_source(self) -> CreateAuditionScriptRequest:
        source_values = (self.source_document_id, self.source_revision, self.source_span)
        if self.kind in {
            "approved_manuscript_excerpt",
            "role_dialogue_excerpt",
            "narrator_excerpt",
        } and any(value is None for value in source_values):
            raise ValueError("Manuscript audition scripts require an exact source binding.")
        if self.kind in {
            "standardized_synthetic",
            "pronunciation_test",
            "synthetic_fallback",
        } and any(value is not None for value in source_values):
            raise ValueError("Synthetic audition scripts cannot claim a manuscript source span.")
        return self


class PreviewNormalizationRequest(StrictRequest):
    audition_session_id: str = Field(min_length=1, max_length=128)
    expected_session_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)
    source_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted_optional_normalization_ids: list[str] = Field(
        default_factory=list,
        max_length=2000,
    )
    custom_pronunciation_scope_ids: list[str] = Field(
        default_factory=list,
        max_length=50,
    )

    @field_validator("accepted_optional_normalization_ids")
    @classmethod
    def validate_preview_normalization_ids(cls, value: list[str]) -> list[str]:
        return CreateAuditionScriptRequest.validate_normalization_ids(value)

    @field_validator("custom_pronunciation_scope_ids")
    @classmethod
    def validate_preview_custom_pronunciation_scope_ids(
        cls,
        value: list[str],
    ) -> list[str]:
        return CreateAuditionScriptRequest.validate_custom_pronunciation_scope_ids(value)


class SpeechProviderControlsRequest(StrictRequest):
    speaking_rate: float = Field(ge=0.5, le=2.0)
    pitch: float | None = Field(default=None, ge=-1.0, le=1.0)
    style: str | None = Field(default=None, min_length=1, max_length=80)
    energy: float | None = Field(default=None, ge=0.0, le=1.0)
    controls_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SpeechPreviewRequestBody(StrictRequest):
    contract_version: Literal["1.0.0"] = "1.0.0"
    request_id: str = Field(min_length=1, max_length=128)
    audition_session_id: str = Field(min_length=1, max_length=128)
    audition_session_revision: int = Field(ge=1)
    audition_script_id: str = Field(min_length=1, max_length=128)
    audition_script_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: AuditionEvidenceBindingRequest
    normalized_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalization_plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    pronunciation_plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_controls: SpeechProviderControlsRequest
    output_format: Literal["pcm_s16le_wav"] = "pcm_s16le_wav"
    sample_rate_hz: Literal[24000] = 24000
    channels: Literal[1] = 1
    idempotency_key: str = Field(min_length=1, max_length=160)
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("idempotency_key")
    @classmethod
    def validate_preview_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class GenerateAuditionRequest(StrictRequest):
    preview: SpeechPreviewRequestBody


AuditionReviewGateId = Literal[
    "per_role_audition_review",
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review",
]


class ListAuditionReviewDecisionsQuery(StrictRequest):
    gate_id: AuditionReviewGateId
    role_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("role_id")
    @classmethod
    def validate_review_history_role_id(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or any(ord(character) < 33 for character in value)
        ):
            raise ValueError("The audition review history role is invalid.")
        return value

    @model_validator(mode="after")
    def validate_review_history_scope(self) -> ListAuditionReviewDecisionsQuery:
        if (self.gate_id == "per_role_audition_review") != (self.role_id is not None):
            raise ValueError(
                "Per-role history requires roleId and aggregate history forbids roleId."
            )
        return self


class HumanListeningAttestationRequest(StrictRequest):
    audition_clip_id: str = Field(min_length=1, max_length=128)
    audition_clip_revision: int = Field(ge=1)
    audition_clip_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    audio_artifact_id: str = Field(min_length=1, max_length=128)
    audio_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    listened: Literal[True]
    disposition: Literal["acceptable", "unacceptable", "needs_changes", "undecided"]


class DecideAuditionReviewRequest(StrictRequest):
    expected_review_revision: int = Field(ge=1)
    expected_evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "request_changes", "reject"]
    rationale: str = Field(min_length=1, max_length=4000)
    supersedes_decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    listening_attestation: HumanListeningAttestationRequest | None = None
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("rationale")
    @classmethod
    def validate_audition_rationale(cls, value: str) -> str:
        return DecideAnalysisReviewRequest.validate_analysis_rationale(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_audition_review_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class ModelInstallationOperationRequest(StrictRequest):
    model_package_id: str = Field(min_length=1, max_length=128)
    expected_manifest_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_installation_revision: int | None = Field(default=None, ge=1)
    action: Literal["verify", "activate", "deactivate", "repair", "remove"]
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("reason")
    @classmethod
    def validate_model_operation_reason(cls, value: str) -> str:
        return AppendAnalysisCorrectionRequest.validate_correction_reason(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_model_operation_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class InstallModelPackageRequest(StrictRequest):
    expected_manifest_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_installation_revision: int | None = Field(default=None, ge=1)
    acknowledge_restricted_local_use: bool
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("reason")
    @classmethod
    def validate_model_install_reason(cls, value: str) -> str:
        return AppendAnalysisCorrectionRequest.validate_correction_reason(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_model_install_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)


class ClearAuditionCacheRequest(StrictRequest):
    expected_project_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("reason")
    @classmethod
    def validate_cache_clear_reason(cls, value: str) -> str:
        return AppendAnalysisCorrectionRequest.validate_correction_reason(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_cache_clear_idempotency_key(cls, value: str) -> str:
        return CreateJobRequest.validate_idempotency_key(value)
