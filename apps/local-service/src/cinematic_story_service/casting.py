from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any, Final, Literal

from .errors import ServiceError
from .util import canonical_json, request_fingerprint, stable_id

CASTING_CONTRACT_VERSION: Final = "3.0.0"
CASTING_PROFILE_ID: Final = "governed-voice-casting-v1@1.0.0"
CASTING_PROFILE_VERSION: Final = "1.0.0"
CASTING_PRODUCER_ID: Final = "voice-casting-orchestrator@1.0.0"
CASTING_PRODUCER_VERSION: Final = "1.0.0"
CASTING_PRODUCER_RECORDED_AT: Final = "2026-01-01T00:00:00Z"
RIGHTS_POLICY_VERSION: Final = "voice-rights-policy-v1"
DEFAULT_CASTING_PAGE_SIZE: Final = 50
MAX_CASTING_PAGE_SIZE: Final = 200
MAX_PRODUCTION_ROLES: Final = 300
MAX_VOICE_PROFILES: Final = 5_000
MAX_PRE_REDUCTION_CANDIDATES: Final = 50
MAX_FINAL_CANDIDATES: Final = 12
MAX_CASTING_CONFLICTS: Final = 10_000
MAX_CASTING_CORRECTIONS_PER_RUN: Final = 200
MAX_CASTING_EXPLANATION_CODE_POINTS: Final = 2_000
MAX_CASTING_RULE_RESULTS: Final = 32
MAX_CASTING_WARNINGS_PER_ENTITY: Final = 32
MAX_CASTING_CHECKPOINT_BYTES: Final = 64 * 1024 * 1024

CASTING_JOB_STAGES: Final = (
    "validate_phase_2_approvals",
    "freeze_source_analysis_evidence",
    "load_voice_catalog_revision",
    "create_production_roles",
    "evaluate_role_constraints",
    "generate_bounded_candidates",
    "evaluate_differentiation_conflicts",
    "publish_casting_run",
    "publish_reviewable_cast_snapshot",
)

CASTING_GATE_IDS: Final = (
    "narrator_casting_review",
    "character_casting_review",
    "complete_cast_review",
)

PRODUCTION_ROLE_TYPES: Final = (
    "primary_narrator",
    "secondary_narrator",
    "named_character",
    "unresolved_speaker",
    "group_or_crowd",
    "quoted_document_or_announcement",
    "internal_thought",
    "custom",
)

CASTING_CORRECTION_OPERATIONS: Final = (
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
)

HARD_CONSTRAINT_IDS: Final = (
    "language_support",
    "provider_available",
    "model_available",
    "rights_not_prohibited",
    "rights_known",
    "required_consent",
    "voice_not_blocked",
    "declared_capabilities",
    "role_length_suitability",
)

SOFT_PREFERENCE_IDS: Final = (
    "locale_match",
    "narration_suitability",
    "dialogue_suitability",
    "expressive_range",
    "age_presentation",
    "vocal_presentation",
    "vocal_texture",
    "speaking_rate",
    "emotional_range",
    "long_form_preference",
)

CASTING_CONFLICT_CATEGORIES: Final = (
    "incompatible_voice_reuse",
    "narrator_major_character_reuse",
    "metadata_similarity_risk",
    "accent_or_locale_mismatch",
    "insufficient_expressive_range",
    "rights_conflict",
    "provider_or_model_unavailable",
    "deprecated_voice",
    "role_length_suitability",
    "unresolved_role_assignment",
    "voice_reuse_threshold_exceeded",
)

COMPATIBILITY_RULE_IDS: Final = (
    "hard_constraints_fail_closed",
    "soft_preferences_score_separately",
    "unknown_remains_unknown",
    "no_automatic_assignment",
    "declared_metadata_only",
)

RIGHTS_ELIGIBILITY_RULE_IDS: Final = (
    "verified_eligible",
    "restricted_requires_acknowledgement",
    "unknown_ineligible",
    "prohibited_ineligible",
)

CompatibilityStatus = Literal["eligible", "conditional", "ineligible", "unknown"]


def _profile_values() -> dict[str, Any]:
    return {
        "profileId": CASTING_PROFILE_ID,
        "castingContractVersion": CASTING_CONTRACT_VERSION,
        "producerId": CASTING_PRODUCER_ID,
        "rightsPolicyId": RIGHTS_POLICY_VERSION,
        "deterministic": True,
        "providerNeutral": True,
        "externalSemanticDependency": False,
        "hardConstraints": list(HARD_CONSTRAINT_IDS),
        "softPreferences": list(SOFT_PREFERENCE_IDS),
        "compatibilityRules": list(COMPATIBILITY_RULE_IDS),
        "conflictRules": list(CASTING_CONFLICT_CATEGORIES),
        "rightsEligibilityRules": list(RIGHTS_ELIGIBILITY_RULE_IDS),
        "explanationRequired": True,
        "limits": {
            "defaultPageSize": DEFAULT_CASTING_PAGE_SIZE,
            "maximumPageSize": MAX_CASTING_PAGE_SIZE,
            "maximumProductionRoles": MAX_PRODUCTION_ROLES,
            "maximumVoiceProfiles": MAX_VOICE_PROFILES,
            "maximumPreReductionCandidatesPerRole": MAX_PRE_REDUCTION_CANDIDATES,
            "maximumFinalCandidatesPerRole": MAX_FINAL_CANDIDATES,
            "maximumExplanationCodePoints": MAX_CASTING_EXPLANATION_CODE_POINTS,
            "maximumWarningsPerEntity": MAX_CASTING_WARNINGS_PER_ENTITY,
            "maximumHardConstraintResults": 16,
            "maximumSoftPreferenceResults": 16,
            "maximumConflictsPerRun": MAX_CASTING_CONFLICTS,
            "maximumVoiceReusePerProfile": 2,
        },
    }


CASTING_PROFILE_VALUES: Final = _profile_values()
CASTING_PROFILE_FINGERPRINT: Final = request_fingerprint(CASTING_PROFILE_VALUES)


def casting_profile() -> dict[str, Any]:
    return {
        "values": CASTING_PROFILE_VALUES,
        "canonicalJson": canonical_json(CASTING_PROFILE_VALUES),
        "fingerprint": CASTING_PROFILE_FINGERPRINT,
    }


def _invalid_catalog() -> ServiceError:
    return ServiceError(
        503,
        "VOICE_CATALOG_INVALID",
        "The deterministic voice catalog failed validation.",
        retryable=False,
    )


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid_catalog()
    return value


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _invalid_catalog()
    return value


def _identifier(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(not (character.isalnum() or character in "._-@") for character in value)
    ):
        raise _invalid_catalog()
    return value


def _text(value: Any, *, maximum: int = 2_000) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 and character != "\t" for character in value)
    ):
        raise _invalid_catalog()
    return value.strip()


def _state(value: Any, allowed: frozenset[str]) -> str:
    result = _text(value, maximum=80)
    if result not in allowed:
        raise _invalid_catalog()
    return result


def _exact_fields(
    value: dict[str, Any],
    required: frozenset[str],
    *,
    optional: frozenset[str] = frozenset(),
) -> None:
    if not required <= value.keys() or not value.keys() <= required | optional:
        raise _invalid_catalog()


def _catalog_strings(
    value: Any,
    *,
    maximum_items: int,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise _invalid_catalog()
    result = tuple(_text(item, maximum=128) for item in value)
    if len(result) != len(set(result)):
        raise _invalid_catalog()
    if allowed is not None and any(item not in allowed for item in result):
        raise _invalid_catalog()
    return result


def _rights_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 40:
        raise _invalid_catalog()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _invalid_catalog() from exc
    if parsed.tzinfo is None:
        raise _invalid_catalog()
    return parsed.astimezone(UTC)


def rights_record_is_current(
    rights: dict[str, Any],
    *,
    reference_time: datetime | None = None,
) -> bool:
    """Fail closed when governed rights are not yet effective or have expired."""

    try:
        effective = _rights_timestamp(rights.get("effectiveDate"))
        expires = _rights_timestamp(rights.get("expiresAt"))
    except ServiceError:
        return False
    if effective is not None and expires is not None and expires <= effective:
        return False
    evaluated_at = (reference_time or datetime.now(UTC)).astimezone(UTC)
    if effective is not None and evaluated_at < effective:
        return False
    return expires is None or evaluated_at < expires


def _catalog_range(
    value: Any,
    *,
    minimum: float,
    maximum: float,
    unit: str | None = None,
) -> tuple[float, float]:
    result = _object(value)
    expected = {"minimum", "maximum"} | ({"unit"} if unit is not None else set())
    if set(result) != expected or (unit is not None and result.get("unit") != unit):
        raise _invalid_catalog()
    lower = _number(result.get("minimum"))
    upper = _number(result.get("maximum"))
    if lower is None or upper is None or not minimum <= lower <= upper <= maximum:
        raise _invalid_catalog()
    return lower, upper


def _catalog_output_capability(value: Any) -> None:
    output = _object(value)
    _exact_fields(output, frozenset({"formats", "sampleRatesHz"}))
    _catalog_strings(
        output.get("formats"),
        maximum_items=8,
        allowed=frozenset({"pcm_s16le", "wav", "mp3", "unknown"}),
    )
    sample_rates = output.get("sampleRatesHz")
    if (
        not isinstance(sample_rates, list)
        or len(sample_rates) > 16
        or any(
            not isinstance(item, int) or isinstance(item, bool) or not 1 <= item <= 768_000
            for item in sample_rates
        )
        or len(sample_rates) != len(set(sample_rates))
    ):
        raise _invalid_catalog()


def _catalog_provenance(value: Any) -> None:
    provenance = _object(value)
    _exact_fields(
        provenance,
        frozenset({"origin", "producerId", "producerVersion", "recordedAt"}),
        optional=frozenset({"inputFingerprint", "sourceRevisionId"}),
    )
    _state(
        provenance.get("origin"),
        frozenset(
            {
                "development_fixture",
                "local_catalog",
                "runtime_agent",
                "human",
                "system",
            }
        ),
    )
    _identifier(provenance.get("producerId"))
    _text(provenance.get("producerVersion"), maximum=80)
    _text(provenance.get("recordedAt"), maximum=40)
    input_fingerprint = provenance.get("inputFingerprint")
    if input_fingerprint is not None and (
        not isinstance(input_fingerprint, str)
        or len(input_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in input_fingerprint)
    ):
        raise _invalid_catalog()
    source_revision_id = provenance.get("sourceRevisionId")
    if source_revision_id is not None:
        _identifier(source_revision_id)


def _catalog_contract(value: dict[str, Any]) -> None:
    if value.get("contractVersion") != CASTING_CONTRACT_VERSION:
        raise _invalid_catalog()
    _catalog_provenance(value.get("provenance"))


def _catalog_without_reported_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(canonical_json(payload))
    if not isinstance(result, dict):
        raise _invalid_catalog()
    result.pop("fingerprint", None)
    result.pop("catalogFingerprint", None)
    revision = result.get("catalogRevision")
    if isinstance(revision, dict):
        revision.pop("fingerprint", None)
        revision.pop("catalogFingerprint", None)
    return result


@dataclass(frozen=True, slots=True)
class VoiceCatalog:
    revision: dict[str, Any]
    providers: tuple[dict[str, Any], ...]
    models: tuple[dict[str, Any], ...]
    voices: tuple[dict[str, Any], ...]
    rights: tuple[dict[str, Any], ...]
    fingerprint: str

    @property
    def revision_id(self) -> str:
        return _identifier(
            self.revision.get("catalogRevisionId") or self.revision.get("revisionId")
        )

    @property
    def rights_evaluation_time(self) -> datetime:
        """Freeze temporal rights eligibility to the immutable catalog publication."""

        value = _rights_timestamp(self.revision.get("createdAt"))
        if value is None:
            raise _invalid_catalog()
        return value

    def to_wire(self) -> dict[str, Any]:
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "catalogRevision": self.revision,
            "providers": list(self.providers),
            "models": list(self.models),
            "voices": list(self.voices),
            "rights": list(self.rights),
            "fingerprint": self.fingerprint,
        }


def validate_catalog(payload: Any) -> VoiceCatalog:
    root = _object(payload)
    _exact_fields(
        root,
        frozenset(
            {
                "contractVersion",
                "catalogRevision",
                "providers",
                "models",
                "voices",
                "rights",
                "fingerprint",
            }
        ),
    )
    if root.get("contractVersion") != CASTING_CONTRACT_VERSION:
        raise _invalid_catalog()
    revision = _object(root.get("catalogRevision"))
    providers = _objects(root.get("providers"))
    models = _objects(root.get("models"))
    voices = _objects(root.get("voices", root.get("voiceProfiles")))
    rights = _objects(root.get("rights", root.get("rightsRecords")))
    if (
        not 1 <= len(providers) <= 32
        or not 1 <= len(models) <= 128
        or not 14 <= len(voices) <= MAX_VOICE_PROFILES
        or len(rights) != len(voices)
    ):
        raise _invalid_catalog()

    _exact_fields(
        revision,
        frozenset(
            {
                "contractVersion",
                "catalogRevisionId",
                "revision",
                "semanticVersion",
                "rightsPolicyId",
                "providerDescriptorIds",
                "modelDescriptorIds",
                "voiceProfileIds",
                "createdAt",
                "immutable",
                "provenance",
                "catalogFingerprint",
            }
        ),
    )
    _catalog_contract(revision)
    revision_id = _identifier(revision.get("catalogRevisionId"))
    revision_number = revision.get("revision")
    if (
        not isinstance(revision_number, int)
        or isinstance(revision_number, bool)
        or revision_number < 1
        or revision.get("immutable") is not True
        or revision.get("rightsPolicyId") != RIGHTS_POLICY_VERSION
    ):
        raise _invalid_catalog()
    _text(revision.get("semanticVersion"), maximum=80)
    if _rights_timestamp(revision.get("createdAt")) is None:
        raise _invalid_catalog()
    revision_provider_ids = _catalog_strings(
        revision.get("providerDescriptorIds"),
        maximum_items=32,
    )
    revision_model_ids = _catalog_strings(
        revision.get("modelDescriptorIds"),
        maximum_items=128,
    )
    revision_voice_ids = _catalog_strings(
        revision.get("voiceProfileIds"),
        maximum_items=MAX_VOICE_PROFILES,
    )

    provider_ids: set[str] = set()
    for provider in providers:
        _exact_fields(
            provider,
            frozenset(
                {
                    "contractVersion",
                    "providerId",
                    "providerVersion",
                    "providerType",
                    "runtimeAvailability",
                    "catalogAvailability",
                    "synthesisImplemented",
                    "networkUseRequired",
                    "credentialsRequired",
                    "supportedOperatingSystems",
                    "supportedLanguages",
                    "outputCapability",
                    "rightsMetadataCapabilities",
                    "healthStatus",
                    "provenance",
                }
            ),
        )
        _catalog_contract(provider)
        provider_id = _identifier(provider.get("providerId"))
        if provider_id in provider_ids:
            raise _invalid_catalog()
        provider_ids.add(provider_id)
        _text(provider.get("providerVersion"), maximum=80)
        _state(
            provider.get("providerType"),
            frozenset({"local", "cloud_capable_disabled", "development_fixture"}),
        )
        _state(
            provider.get("runtimeAvailability"),
            frozenset({"available", "unavailable", "disabled"}),
        )
        _state(
            provider.get("catalogAvailability"),
            frozenset({"available", "unavailable", "disabled"}),
        )
        if any(
            not isinstance(provider.get(field), bool)
            for field in (
                "synthesisImplemented",
                "networkUseRequired",
                "credentialsRequired",
            )
        ):
            raise _invalid_catalog()
        _catalog_strings(
            provider.get("supportedOperatingSystems"),
            maximum_items=3,
            allowed=frozenset({"windows", "macos", "linux"}),
        )
        if not _catalog_strings(
            provider.get("supportedLanguages"),
            maximum_items=64,
        ):
            raise _invalid_catalog()
        _catalog_output_capability(provider.get("outputCapability"))
        _catalog_strings(
            provider.get("rightsMetadataCapabilities"),
            maximum_items=16,
            allowed=frozenset(
                {
                    "license_identifier",
                    "commercial_use",
                    "attribution",
                    "distribution_limits",
                    "consent",
                    "effective_dates",
                    "evidence_reference",
                }
            ),
        )
        _state(
            provider.get("healthStatus"),
            frozenset({"healthy", "degraded", "unavailable", "disabled"}),
        )

    model_pairs: set[tuple[str, str]] = set()
    model_ids: set[str] = set()
    for model in models:
        _exact_fields(
            model,
            frozenset(
                {
                    "contractVersion",
                    "modelId",
                    "providerId",
                    "modelName",
                    "modelVersion",
                    "capability",
                    "executionLocation",
                    "licenseClassification",
                    "availability",
                    "deprecated",
                    "provenance",
                }
            ),
        )
        _catalog_contract(model)
        provider_id = _identifier(model.get("providerId"))
        model_id = _identifier(model.get("modelId"))
        pair = (provider_id, model_id)
        if provider_id not in provider_ids or pair in model_pairs or model_id in model_ids:
            raise _invalid_catalog()
        model_pairs.add(pair)
        model_ids.add(model_id)
        _text(model.get("modelName"), maximum=200)
        _text(model.get("modelVersion"), maximum=80)
        _state(model.get("executionLocation"), frozenset({"local", "remote"}))
        _state(
            model.get("licenseClassification"),
            frozenset({"fixture_only", "commercial", "restricted", "unknown", "prohibited"}),
        )
        _state(
            model.get("availability"),
            frozenset({"available", "unavailable", "disabled"}),
        )
        if not isinstance(model.get("deprecated"), bool):
            raise _invalid_catalog()
        capability = _object(model.get("capability"))
        _exact_fields(
            capability,
            frozenset(
                {
                    "supportedLanguages",
                    "supportedLocales",
                    "expressiveControls",
                    "speakingRateRange",
                    "pitchControl",
                    "styleControl",
                    "outputCapability",
                }
            ),
        )
        if not _catalog_strings(
            capability.get("supportedLanguages"),
            maximum_items=64,
        ):
            raise _invalid_catalog()
        _catalog_strings(capability.get("supportedLocales"), maximum_items=128)
        _catalog_strings(
            capability.get("expressiveControls"),
            maximum_items=5,
            allowed=frozenset({"energy", "emotion", "pace", "pitch", "style"}),
        )
        _catalog_range(
            capability.get("speakingRateRange"),
            minimum=0.25,
            maximum=4.0,
            unit="multiplier",
        )
        _state(
            capability.get("pitchControl"),
            frozenset({"none", "categorical", "continuous"}),
        )
        _state(
            capability.get("styleControl"),
            frozenset({"none", "categorical", "continuous"}),
        )
        _catalog_output_capability(capability.get("outputCapability"))

    voice_ids: set[str] = set()
    voices_by_id: dict[str, dict[str, Any]] = {}
    for voice in voices:
        _exact_fields(
            voice,
            frozenset(
                {
                    "contractVersion",
                    "voiceProfileId",
                    "providerId",
                    "modelId",
                    "providerVoiceId",
                    "catalogRevisionId",
                    "displayLabel",
                    "language",
                    "locale",
                    "accentOrDialect",
                    "agePresentationRange",
                    "vocalPresentation",
                    "vocalTexture",
                    "pitchRange",
                    "speakingRateRange",
                    "energyRange",
                    "expressiveRange",
                    "narrationSuitability",
                    "dialogueSuitability",
                    "longFormSuitability",
                    "characterRoleSuitability",
                    "maximumRecommendedWords",
                    "knownLimitations",
                    "rightsRecordId",
                    "rightsState",
                    "licenseScope",
                    "commercialUse",
                    "attributionRequired",
                    "voiceCloningClassification",
                    "consentStatus",
                    "metadataSimilarityGroup",
                    "reuseRiskGroup",
                    "version",
                    "state",
                    "provenance",
                }
            ),
        )
        _catalog_contract(voice)
        voice_id = _identifier(voice.get("voiceProfileId") or voice.get("profileId"))
        if voice_id in voice_ids:
            raise _invalid_catalog()
        voice_ids.add(voice_id)
        voices_by_id[voice_id] = voice
        provider_id = _identifier(voice.get("providerId"))
        model_id = _identifier(voice.get("modelId"))
        if provider_id not in provider_ids or (provider_id, model_id) not in model_pairs:
            raise _invalid_catalog()
        _identifier(voice.get("providerVoiceId"))
        if _identifier(voice.get("catalogRevisionId")) != revision_id:
            raise _invalid_catalog()
        for field, maximum in (
            ("displayLabel", 200),
            ("language", 35),
            ("locale", 35),
            ("accentOrDialect", 200),
            ("licenseScope", 500),
            ("version", 80),
        ):
            _text(voice.get(field), maximum=maximum)
        _catalog_range(
            voice.get("agePresentationRange"),
            minimum=0,
            maximum=120,
        )
        _catalog_range(
            voice.get("speakingRateRange"),
            minimum=0.25,
            maximum=4.0,
            unit="multiplier",
        )
        _catalog_range(voice.get("energyRange"), minimum=0, maximum=1)
        _state(
            voice.get("vocalPresentation"),
            frozenset(
                {
                    "feminine",
                    "masculine",
                    "androgynous",
                    "neutral",
                    "varied",
                    "unspecified",
                }
            ),
        )
        _state(
            voice.get("vocalTexture"),
            frozenset(
                {
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
            ),
        )
        _state(
            voice.get("pitchRange"),
            frozenset(
                {
                    "low",
                    "low_mid",
                    "mid",
                    "mid_high",
                    "high",
                    "wide",
                    "unspecified",
                }
            ),
        )
        _catalog_strings(voice.get("expressiveRange"), maximum_items=32)
        suitability_states = frozenset(
            {"preferred", "suitable", "limited", "unsuitable", "unknown"}
        )
        for field in (
            "narrationSuitability",
            "dialogueSuitability",
            "longFormSuitability",
        ):
            _state(voice.get(field), suitability_states)
        _catalog_strings(
            voice.get("characterRoleSuitability"),
            maximum_items=6,
            allowed=frozenset(
                {
                    "lead",
                    "supporting",
                    "minor",
                    "group",
                    "announcement",
                    "internal_thought",
                }
            ),
        )
        maximum_words = voice.get("maximumRecommendedWords")
        if maximum_words is not None and (
            not isinstance(maximum_words, int)
            or isinstance(maximum_words, bool)
            or maximum_words < 1
        ):
            raise _invalid_catalog()
        _catalog_strings(voice.get("knownLimitations"), maximum_items=32)
        _identifier(voice.get("rightsRecordId"))
        _state(
            voice.get("rightsState"),
            frozenset({"verified", "restricted", "unknown", "prohibited"}),
        )
        _state(
            voice.get("commercialUse"),
            frozenset({"permitted", "restricted", "unknown", "prohibited"}),
        )
        if not isinstance(voice.get("attributionRequired"), bool):
            raise _invalid_catalog()
        _state(
            voice.get("voiceCloningClassification"),
            frozenset(
                {
                    "not_cloned_synthetic_fixture",
                    "provider_declared_non_cloned",
                    "unknown",
                    "prohibited",
                }
            ),
        )
        _state(
            voice.get("consentStatus"),
            frozenset(
                {
                    "not_applicable_synthetic_fixture",
                    "verified",
                    "restricted",
                    "missing",
                    "unknown",
                    "prohibited",
                }
            ),
        )
        for field in ("metadataSimilarityGroup", "reuseRiskGroup"):
            if voice.get(field) is not None:
                _identifier(voice.get(field))
        _state(
            voice.get("state"),
            frozenset({"active", "unavailable", "deprecated", "blocked"}),
        )

    rights_voice_ids: set[str] = set()
    rights_record_ids: set[str] = set()
    for record in rights:
        _exact_fields(
            record,
            frozenset(
                {
                    "contractVersion",
                    "rightsRecordId",
                    "voiceProfileId",
                    "providerId",
                    "revision",
                    "state",
                    "licenseIdentifier",
                    "rightsBasis",
                    "commercialUsePermission",
                    "attributionRequirement",
                    "geographicLimitations",
                    "distributionLimitations",
                    "voiceCloningStatus",
                    "consentStatus",
                    "effectiveDate",
                    "expiresAt",
                    "evidenceReference",
                    "humanVerificationStatus",
                    "provenance",
                }
            ),
        )
        _catalog_contract(record)
        voice_id = _identifier(record.get("voiceProfileId") or record.get("profileId"))
        if voice_id not in voice_ids or voice_id in rights_voice_ids:
            raise _invalid_catalog()
        rights_voice_ids.add(voice_id)
        record_id = _identifier(record.get("rightsRecordId"))
        if record_id in rights_record_ids:
            raise _invalid_catalog()
        rights_record_ids.add(record_id)
        rights_state = _state(
            record.get("state") or record.get("rightsState"),
            frozenset({"verified", "restricted", "unknown", "prohibited"}),
        )
        voice = voices_by_id[voice_id]
        if (
            record_id != voice.get("rightsRecordId")
            or record.get("providerId") != voice.get("providerId")
            or rights_state != voice.get("rightsState")
        ):
            raise _invalid_catalog()
        record_revision = record.get("revision")
        if (
            not isinstance(record_revision, int)
            or isinstance(record_revision, bool)
            or record_revision < 1
        ):
            raise _invalid_catalog()
        for field, maximum in (
            ("licenseIdentifier", 200),
            ("rightsBasis", 1_000),
            ("evidenceReference", 500),
        ):
            _text(record.get(field), maximum=maximum)
        commercial_use = _state(
            record.get("commercialUsePermission"),
            frozenset({"permitted", "restricted", "unknown", "prohibited"}),
        )
        if commercial_use != voice.get("commercialUse"):
            raise _invalid_catalog()
        _state(
            record.get("attributionRequirement"),
            frozenset({"none", "required", "unknown", "prohibited"}),
        )
        _catalog_strings(record.get("geographicLimitations"), maximum_items=32)
        _catalog_strings(record.get("distributionLimitations"), maximum_items=32)
        _state(
            record.get("voiceCloningStatus"),
            frozenset(
                {
                    "not_applicable_synthetic_fixture",
                    "not_permitted",
                    "permitted_with_consent",
                    "unknown",
                    "prohibited",
                }
            ),
        )
        consent_status = _state(
            record.get("consentStatus"),
            frozenset(
                {
                    "not_applicable_synthetic_fixture",
                    "verified",
                    "restricted",
                    "missing",
                    "unknown",
                    "prohibited",
                }
            ),
        )
        if consent_status != voice.get("consentStatus"):
            raise _invalid_catalog()
        effective_date = _rights_timestamp(record.get("effectiveDate"))
        expiration_date = _rights_timestamp(record.get("expiresAt"))
        if (
            effective_date is not None
            and expiration_date is not None
            and expiration_date <= effective_date
        ):
            raise _invalid_catalog()
        _state(
            record.get("humanVerificationStatus"),
            frozenset({"verified", "not_required_fixture", "pending", "rejected"}),
        )
    if rights_voice_ids != voice_ids:
        raise _invalid_catalog()
    if (
        set(revision_provider_ids) != provider_ids
        or set(revision_model_ids) != model_ids
        or set(revision_voice_ids) != voice_ids
    ):
        raise _invalid_catalog()

    computed = request_fingerprint(_catalog_without_reported_fingerprint(root))
    reported = root.get("fingerprint")
    revision_fingerprint = revision.get("catalogFingerprint")
    if (
        not isinstance(reported, str)
        or not isinstance(revision_fingerprint, str)
        or reported != computed
        or revision_fingerprint != computed
    ):
        raise _invalid_catalog()
    return VoiceCatalog(
        revision=revision,
        providers=tuple(providers),
        models=tuple(models),
        voices=tuple(voices),
        rights=tuple(rights),
        fingerprint=computed,
    )


def load_synthetic_catalog() -> VoiceCatalog:
    try:
        resource = files("cinematic_story_service").joinpath(
            "catalogs",
            "synthetic_voice_catalog.v1.json",
        )
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _invalid_catalog() from exc
    return validate_catalog(payload)


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("payload")
    return value if isinstance(value, dict) else record


def _payload_identifier(value: dict[str, Any], *names: str) -> str | None:
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _payload_text(value: dict[str, Any], *names: str) -> str | None:
    result = _payload_identifier(value, *names)
    return result.strip()[:200] if result is not None else None


def _span_units(value: dict[str, Any]) -> int:
    start = value.get("startOffset")
    end = value.get("endOffset")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
        return max(1, (end - start) // 6)
    source_span = value.get("sourceSpan")
    return _span_units(source_span) if isinstance(source_span, dict) else 1


def _evidence_strings(value: dict[str, Any], *names: str) -> list[str]:
    result: list[str] = []
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, str) and candidate.strip():
            result.append(candidate.strip()[:200])
        elif isinstance(candidate, list):
            result.extend(
                item.strip()[:200] for item in candidate if isinstance(item, str) and item.strip()
            )
    return result


def _project_language_requirements(
    evidence_entities: list[dict[str, Any]],
    *,
    fallback_entities: list[dict[str, Any]],
) -> tuple[list[str], str, list[str]]:
    def collect(
        entities: list[dict[str, Any]],
    ) -> tuple[set[str], dict[str, str], bool]:
        languages: set[str] = set()
        locales: dict[str, str] = {}
        declaration_seen = False
        language_fields = (
            "languageRequirements",
            "language",
            "languageCode",
            "languageTag",
            "detectedLanguage",
            "sourceLanguage",
        )
        locale_fields = (
            "locales",
            "locale",
            "languageLocale",
            "sourceLocale",
        )
        for entity in entities:
            value = _payload(entity)
            declared_requirements = value.get("performanceRequirements")
            requirement_values = (
                declared_requirements if isinstance(declared_requirements, dict) else {}
            )
            declaration_seen = (
                declaration_seen
                or any(field in value for field in (*language_fields, *locale_fields))
                or any(field in requirement_values for field in ("language", "locales"))
            )
            for language in [
                *_evidence_strings(
                    value,
                    *language_fields,
                ),
                *_evidence_strings(
                    requirement_values,
                    "language",
                ),
            ]:
                languages.add(language.casefold())
            for locale in [
                *_evidence_strings(
                    value,
                    *locale_fields,
                ),
                *_evidence_strings(
                    requirement_values,
                    "locales",
                ),
            ]:
                locales.setdefault(locale.casefold(), locale)
        return languages, locales, declaration_seen

    languages, locales_by_key, declaration_seen = collect(evidence_entities)
    if not declaration_seen:
        languages, locales_by_key, _ = collect(fallback_entities)
    if not languages:
        languages.update(
            locale_key.split("-", 1)[0]
            for locale_key in locales_by_key
            if locale_key.split("-", 1)[0]
        )
    ordered_languages = sorted(languages) or ["und"]
    performance_language = ordered_languages[0] if len(ordered_languages) == 1 else "und"
    ordered_locales = [locales_by_key[key] for key in sorted(locales_by_key)][:20]
    return ordered_languages[:20], performance_language, ordered_locales


def production_roles(
    *,
    project_id: str,
    casting_evidence_fingerprint: str,
    analysis_run_id: str,
    snapshot_id: str,
    snapshot_fingerprint: str,
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    collections: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        collection = entity.get("collection")
        if isinstance(collection, str):
            collections.setdefault(collection, []).append(entity)
    characters = collections.get("characters", [])
    dialogue = collections.get("dialogue-lines", [])
    narration = collections.get("narration-spans", [])
    global_spoken_evidence = [*dialogue, *narration]

    chapter_ordinals = {
        identifier: int(entity.get("ordinal", 0)) + 1
        for entity in collections.get("chapters", [])
        if (
            identifier := _payload_identifier(
                _payload(entity),
                "chapterId",
                "entityId",
            )
        )
    }
    scene_ordinals = {
        identifier: int(entity.get("ordinal", 0)) + 1
        for entity in collections.get("scenes", [])
        if (
            identifier := _payload_identifier(
                _payload(entity),
                "sceneId",
                "entityId",
            )
        )
    }

    def evidence_range(
        evidence_entities: list[dict[str, Any]],
    ) -> tuple[dict[str, int | None], dict[str, int | None]]:
        chapter_values = sorted(
            {
                chapter_ordinals[chapter_id]
                for entity in evidence_entities
                if (
                    chapter_id := _payload_identifier(
                        _payload(entity),
                        "chapterId",
                    )
                )
                in chapter_ordinals
            }
        )
        scene_values = sorted(
            {
                scene_ordinals[scene_id]
                for entity in evidence_entities
                if (
                    scene_id := _payload_identifier(
                        _payload(entity),
                        "sceneId",
                    )
                )
                in scene_ordinals
            }
        )
        return (
            {
                "firstOrdinal": chapter_values[0] if chapter_values else None,
                "lastOrdinal": chapter_values[-1] if chapter_values else None,
            },
            {
                "firstOrdinal": scene_values[0] if scene_values else None,
                "lastOrdinal": scene_values[-1] if scene_values else None,
            },
        )

    def role(
        *,
        role_type: str,
        identity: str,
        label: str,
        entity_id: str | None,
        character_id: str | None = None,
        role_importance: str | None = None,
        dialogue_count: int,
        narration_count: int,
        approximate_words: int,
        evidence_entities: list[dict[str, Any]],
        requirement_entities: list[dict[str, Any]] | None = None,
        warnings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        role_id = stable_id(
            "phase3a-production-role",
            project_id,
            casting_evidence_fingerprint,
            analysis_run_id,
            snapshot_id,
            role_type,
            identity,
        )
        chapter_range, scene_range = evidence_range(evidence_entities)
        (
            language_requirements,
            performance_language,
            locales,
        ) = _project_language_requirements(
            requirement_entities or evidence_entities,
            fallback_entities=global_spoken_evidence,
        )
        values = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "roleId": role_id,
            "projectId": project_id,
            "roleType": role_type,
            "analysisEntityId": entity_id,
            "effectiveDisplayLabel": label[:200],
            "analysisRunId": analysis_run_id,
            "analysisSnapshotId": snapshot_id,
            "analysisSnapshotFingerprint": snapshot_fingerprint,
            "dialogueLineCount": dialogue_count,
            "narrationSpanCount": narration_count,
            "approximateWordCount": approximate_words,
            "chapterRange": chapter_range,
            "sceneRange": scene_range,
            "languageRequirements": language_requirements,
            "performanceRequirements": {
                "language": performance_language,
                "locales": locales,
                "agePresentationRange": None,
                "vocalPresentations": [],
                "preferredTextures": [],
                "speakingRateRange": None,
                "requiredExpressiveRange": [],
                "longFormRequired": approximate_words >= 1_000,
            },
            "warnings": warnings or [],
            "provenance": {
                "origin": "runtime_agent",
                "producerId": CASTING_PRODUCER_ID,
                "producerVersion": CASTING_PRODUCER_VERSION,
                "recordedAt": CASTING_PRODUCER_RECORDED_AT,
                "inputFingerprint": snapshot_fingerprint,
            },
            "status": "active",
            "revision": 1,
        }
        if role_type not in {"primary_narrator", "secondary_narrator"}:
            values.update(
                {
                    "characterId": character_id,
                    "roleImportance": (
                        "unresolved"
                        if role_type == "unresolved_speaker"
                        else role_importance or "supporting"
                    ),
                    "unresolvedMaterialExplicitlyRepresented": (role_type == "unresolved_speaker"),
                }
            )
        return values | {"roleFingerprint": request_fingerprint(values)}

    character_by_id: dict[
        str,
        tuple[dict[str, Any], dict[str, Any], str],
    ] = {}
    ambiguous_character_ids: set[str] = set()
    for entity in characters:
        value = _payload(entity)
        registry_character_id = _payload_identifier(
            value,
            "registryCharacterId",
            "characterId",
            "entityId",
        ) or (str(entity["id"]) if isinstance(entity.get("id"), str) and entity["id"] else None)
        if registry_character_id is None:
            continue
        aliases = {
            candidate
            for candidate in (
                registry_character_id,
                _payload_identifier(value, "characterId"),
                _payload_identifier(value, "entityId"),
                str(entity["id"]) if isinstance(entity.get("id"), str) and entity["id"] else None,
            )
            if candidate is not None
        }
        for alias in aliases:
            if alias in ambiguous_character_ids:
                continue
            prior = character_by_id.get(alias)
            if prior is not None and prior[2] != registry_character_id:
                character_by_id.pop(alias, None)
                ambiguous_character_ids.add(alias)
                continue
            character_by_id[alias] = (
                entity,
                value,
                registry_character_id,
            )

    dialogue_by_character: dict[str, list[dict[str, Any]]] = {}
    unresolved_dialogue: list[dict[str, Any]] = []
    quoted_dialogue: list[dict[str, Any]] = []
    internal_dialogue: list[dict[str, Any]] = []
    for entity in dialogue:
        value = _payload(entity)
        distinction = _payload_text(value, "distinction", "classification")
        if distinction in {"quoted_material", "epigraph_or_document"}:
            quoted_dialogue.append(entity)
            continue
        if distinction == "internal_thought":
            internal_dialogue.append(entity)
            continue
        attribution = value.get("effectiveAttribution")
        nested_speaker = (
            attribution.get("speakerCharacterId") if isinstance(attribution, dict) else None
        )
        speaker_state = _payload_text(value, "speakerState")
        speaker_id = (
            None
            if speaker_state in {"unknown", "ambiguous"}
            else _payload_identifier(
                value,
                "effectiveSpeakerId",
                "speakerCharacterId",
                "proposedSpeakerId",
            )
            or (nested_speaker if isinstance(nested_speaker, str) else None)
        )
        character = character_by_id.get(speaker_id) if speaker_id is not None else None
        if (
            speaker_id is None
            or character is None
            or distinction in {"unresolved_speech", "ambiguous"}
        ):
            unresolved_dialogue.append(entity)
        else:
            dialogue_by_character.setdefault(character[2], []).append(entity)

    direct_narration = [
        entity
        for entity in narration
        if _payload_text(_payload(entity), "classification")
        in {
            "direct_narration",
            "unresolved",
            None,
        }
    ]
    primary_narration = [
        entity
        for entity in direct_narration
        if _payload_identifier(_payload(entity), "narratorCharacterId") is None
    ]
    narration_by_character: dict[str, list[dict[str, Any]]] = {}
    for entity in direct_narration:
        narrator_id = _payload_identifier(
            _payload(entity),
            "narratorCharacterId",
        )
        if narrator_id is not None:
            character = character_by_id.get(narrator_id)
            stable_narrator_id = character[2] if character is not None else narrator_id
            narration_by_character.setdefault(stable_narrator_id, []).append(entity)

    roles = [
        role(
            role_type="primary_narrator",
            identity="primary",
            label="Primary narrator",
            entity_id=None,
            dialogue_count=0,
            narration_count=len(primary_narration),
            approximate_words=sum(_span_units(_payload(value)) for value in primary_narration),
            evidence_entities=primary_narration,
        )
    ]
    for narrator_id, selected in sorted(narration_by_character.items()):
        character = character_by_id.get(narrator_id)
        roles.append(
            role(
                role_type="secondary_narrator",
                identity=narrator_id,
                label=(
                    f"Secondary narrator — {_payload_text(character[1], 'canonicalName')}"
                    if character is not None
                    else "Secondary narrator"
                ),
                entity_id=(
                    str(character[0].get("id") or narrator_id) if character is not None else None
                ),
                dialogue_count=0,
                narration_count=len(selected),
                approximate_words=sum(_span_units(_payload(value)) for value in selected),
                evidence_entities=selected,
                requirement_entities=(
                    [character[0], *selected] if character is not None else selected
                ),
            )
        )

    emitted_character_ids: set[str] = set()
    for entity in sorted(characters, key=lambda value: int(value.get("ordinal", 0))):
        value = _payload(entity)
        character_id = _payload_identifier(
            value,
            "registryCharacterId",
            "characterId",
            "entityId",
        ) or str(entity.get("id", "unknown"))
        if character_id in emitted_character_ids:
            continue
        emitted_character_ids.add(character_id)
        label = (
            _payload_text(
                value,
                "effectiveDisplayLabel",
                "canonicalName",
                "displayName",
            )
            or "Named character"
        )
        selected_dialogue = dialogue_by_character.get(character_id, [])
        if not selected_dialogue:
            continue
        character_kind = _payload_text(value, "kind")
        role_type = "group_or_crowd" if character_kind == "group" else "named_character"
        declared_importance = (
            _payload_text(
                value,
                "roleImportance",
                "narrativeImportance",
                "importance",
            )
            or ""
        ).casefold()
        role_importance = (
            declared_importance
            if declared_importance in {"major", "supporting", "minor"}
            else "supporting"
        )
        roles.append(
            role(
                role_type=role_type,
                identity=character_id,
                label=label,
                entity_id=str(entity.get("id") or character_id),
                character_id=character_id,
                role_importance=role_importance,
                dialogue_count=len(selected_dialogue),
                narration_count=0,
                approximate_words=sum(_span_units(_payload(item)) for item in selected_dialogue),
                evidence_entities=selected_dialogue,
                requirement_entities=[entity, *selected_dialogue],
            )
        )
    if unresolved_dialogue:
        roles.append(
            role(
                role_type="unresolved_speaker",
                identity="unresolved",
                label="Unresolved speaker",
                entity_id=None,
                dialogue_count=len(unresolved_dialogue),
                narration_count=0,
                approximate_words=sum(_span_units(_payload(item)) for item in unresolved_dialogue),
                evidence_entities=unresolved_dialogue,
                warnings=[
                    {
                        "code": "UNRESOLVED_SPEAKER_ROLE",
                        "severity": "warning",
                        "message": "Approved analysis retains unresolved spoken material.",
                        "requiresHumanReview": True,
                        "relatedEntityIds": [],
                        "evidence": [],
                    }
                ],
            )
        )
    for role_type, narration_kinds, selected_dialogue, label in (
        (
            "quoted_document_or_announcement",
            {"quoted_material", "epigraph_or_document"},
            quoted_dialogue,
            "Quoted document or announcement",
        ),
        (
            "internal_thought",
            {"internal_thought"},
            internal_dialogue,
            "Internal thought",
        ),
    ):
        selected_narration = [
            item
            for item in narration
            if _payload_text(_payload(item), "classification") in narration_kinds
        ]
        selected = [*selected_narration, *selected_dialogue]
        if selected:
            roles.append(
                role(
                    role_type=role_type,
                    identity=role_type,
                    label=label,
                    entity_id=None,
                    dialogue_count=len(selected_dialogue),
                    narration_count=len(selected_narration),
                    approximate_words=sum(_span_units(_payload(value)) for value in selected),
                    evidence_entities=selected,
                )
            )
    if len(roles) > MAX_PRODUCTION_ROLES:
        raise ServiceError(
            422,
            "CASTING_ROLE_LIMIT_EXCEEDED",
            "The approved analysis exceeds the casting-role limit.",
        )
    return roles


def _boolish(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.casefold() in {
            "available",
            "enabled",
            "preferred",
            "suitable",
            "supported",
            "true",
            "yes",
        }
    return default


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.casefold()}
    if isinstance(value, list):
        return {item.casefold() for item in value if isinstance(item, str)}
    return set()


def _rule(
    rule_id: str,
    *,
    passed: bool | None,
    kind: Literal["hard", "soft"],
    explanation: str,
    weight: int = 0,
) -> dict[str, Any]:
    return {
        "ruleId": rule_id,
        "kind": kind,
        "result": "unknown" if passed is None else ("pass" if passed else "fail"),
        "weight": weight,
        "explanation": explanation[:MAX_CASTING_EXPLANATION_CODE_POINTS],
    }


@dataclass(frozen=True, slots=True)
class _HardConstraintFacts:
    language_ok: bool | None
    provider_available: bool
    model_available: bool
    voice_state: str
    voice_available: bool
    rights_state: str
    rights_not_prohibited: bool
    consent_ok: bool | None
    commercial_ok: bool
    rights_known: bool | None
    narration_ok: bool
    dialogue_ok: bool
    role_capability_ok: bool
    long_form_ok: bool
    word_count_ok: bool

    @property
    def hard_results(self) -> tuple[bool | None, ...]:
        return (
            self.language_ok,
            self.provider_available,
            self.model_available,
            self.rights_not_prohibited,
            self.rights_known,
            self.consent_ok,
            self.voice_available,
            (
                self.narration_ok
                and self.dialogue_ok
                and self.commercial_ok
                and self.role_capability_ok
            ),
            self.long_form_ok and self.word_count_ok,
        )

    @property
    def failure_count(self) -> int:
        return sum(value is False for value in self.hard_results)

    @property
    def unknown_count(self) -> int:
        return sum(value is None for value in self.hard_results)


def _hard_constraint_facts(
    *,
    role: dict[str, Any],
    voice: dict[str, Any],
    rights: dict[str, Any],
    provider: dict[str, Any],
    model: dict[str, Any],
    rights_reference_time: datetime | None = None,
) -> _HardConstraintFacts:
    role_type = str(role["roleType"])
    requirements = _object(role["performanceRequirements"])
    languages = _string_values(
        voice.get("languages") or voice.get("languageSupport") or voice.get("language")
    )
    role_languages = _string_values(role.get("languageRequirements"))
    language_ok: bool | None = (
        None
        if not role_languages or "und" in role_languages
        else role_languages <= languages or "*" in languages
    )
    provider_type = str(provider.get("providerType", provider.get("type", "")))
    provider_available = (
        _boolish(
            provider.get("runtimeAvailability", provider.get("available")),
        )
        and _boolish(
            provider.get("catalogAvailability", provider.get("available")),
        )
        and str(provider.get("healthStatus", "unknown")) in {"healthy", "degraded"}
        and provider_type != "cloud_capable_disabled"
    )
    model_available = _boolish(model.get("availability", model.get("available"))) and not bool(
        model.get("deprecated", False)
    )
    voice_state = str(voice.get("state", "blocked"))
    voice_available = voice_state in {"active", "deprecated"}
    rights_state = str(rights.get("state", rights.get("rightsState", "unknown")))
    rights_not_prohibited = rights_state != "prohibited"
    consent = str(rights.get("consentStatus", "unknown"))
    cloning = str(rights.get("voiceCloningStatus", "unknown"))
    if cloning == "permitted_with_consent":
        consent_ok: bool | None = (
            True if consent == "verified" else None if consent == "unknown" else False
        )
    elif cloning in {"unknown"} or consent == "unknown":
        consent_ok = None
    elif cloning == "prohibited" or consent in {"missing", "prohibited"}:
        consent_ok = False
    else:
        consent_ok = consent in {
            "verified",
            "restricted",
            "not_applicable_synthetic_fixture",
        }
    commercial = str(
        rights.get("commercialUsePermission", rights.get("commercialUseStatus", "unknown"))
    )
    commercial_ok = commercial not in {"denied", "prohibited", "false"}
    verification = str(rights.get("humanVerificationStatus", "pending"))
    temporally_current = rights_record_is_current(
        rights,
        reference_time=rights_reference_time,
    )
    rights_known: bool | None = (
        None
        if (rights_state == "unknown" or commercial == "unknown" or verification == "pending")
        else False
        if (
            rights_state == "prohibited"
            or commercial in {"denied", "prohibited", "false"}
            or verification == "rejected"
            or not temporally_current
        )
        else True
    )
    narration = role_type in {
        "primary_narrator",
        "secondary_narrator",
        "quoted_document_or_announcement",
    }
    dialogue = role_type in {
        "named_character",
        "unresolved_speaker",
        "group_or_crowd",
        "internal_thought",
        "custom",
    }
    narration_ok = not narration or _boolish(voice.get("narrationSuitability"))
    dialogue_ok = not dialogue or _boolish(voice.get("dialogueSuitability"))
    required_role_capability = {
        "named_character": (
            "lead"
            if role.get("roleImportance") == "major"
            else "minor"
            if role.get("roleImportance") == "minor"
            else "supporting"
        ),
        "group_or_crowd": "group",
        "quoted_document_or_announcement": "announcement",
        "internal_thought": "internal_thought",
    }.get(role_type)
    declared_role_capabilities = _string_values(voice.get("characterRoleSuitability"))
    role_capability_ok = (
        required_role_capability is None or required_role_capability in declared_role_capabilities
    )
    long_form_required = bool(requirements.get("longFormRequired"))
    long_form_ok = not long_form_required or _boolish(voice.get("longFormSuitability"))
    approximate_words = role.get("approximateWordCount", 0)
    maximum_words = voice.get("maximumRecommendedWords")
    word_count_ok = (
        not isinstance(approximate_words, int)
        or not isinstance(maximum_words, int)
        or approximate_words <= maximum_words
    )
    return _HardConstraintFacts(
        language_ok=language_ok,
        provider_available=provider_available,
        model_available=model_available,
        voice_state=voice_state,
        voice_available=voice_available,
        rights_state=rights_state,
        rights_not_prohibited=rights_not_prohibited,
        consent_ok=consent_ok,
        commercial_ok=commercial_ok,
        rights_known=rights_known,
        narration_ok=narration_ok,
        dialogue_ok=dialogue_ok,
        role_capability_ok=role_capability_ok,
        long_form_ok=long_form_ok,
        word_count_ok=word_count_ok,
    )


def compatibility_assessment(
    *,
    role: dict[str, Any],
    voice: dict[str, Any],
    rights: dict[str, Any],
    provider: dict[str, Any],
    model: dict[str, Any],
    input_fingerprint: str,
    rights_reference_time: datetime | None = None,
) -> dict[str, Any]:
    requirements = _object(role["performanceRequirements"])
    facts = _hard_constraint_facts(
        role=role,
        voice=voice,
        rights=rights,
        provider=provider,
        model=model,
        rights_reference_time=rights_reference_time,
    )
    language_ok = facts.language_ok
    provider_available = facts.provider_available
    model_available = facts.model_available
    voice_state = facts.voice_state
    voice_available = facts.voice_available
    rights_state = facts.rights_state
    rights_not_prohibited = facts.rights_not_prohibited
    consent_ok = facts.consent_ok
    commercial_ok = facts.commercial_ok
    rights_known = facts.rights_known
    narration_ok = facts.narration_ok
    dialogue_ok = facts.dialogue_ok
    role_capability_ok = facts.role_capability_ok
    long_form_ok = facts.long_form_ok
    word_count_ok = facts.word_count_ok

    hard_results = [
        _rule(
            "language_support",
            passed=language_ok,
            kind="hard",
            explanation=(
                "Declared language support matches the role requirement."
                if language_ok is True
                else "The role language is unresolved in approved analysis evidence."
                if language_ok is None
                else "The voice does not declare the required language."
            ),
        ),
        _rule(
            "provider_available",
            passed=provider_available,
            kind="hard",
            explanation="The provider is locally available for catalog use."
            if provider_available
            else "The provider is unavailable or disabled.",
        ),
        _rule(
            "model_available",
            passed=model_available,
            kind="hard",
            explanation="The declared model is available."
            if model_available
            else "The declared model is unavailable.",
        ),
        _rule(
            "rights_not_prohibited",
            passed=rights_not_prohibited,
            kind="hard",
            explanation="Recorded rights are not prohibited."
            if rights_not_prohibited
            else "Recorded rights prohibit use.",
        ),
        _rule(
            "rights_known",
            passed=rights_known,
            kind="hard",
            explanation=(
                "The rights and commercial-use states are declared."
                if rights_known is True
                else "Rights or commercial-use eligibility remains unknown."
                if rights_known is None
                else "The declared rights or commercial-use state is ineligible."
            ),
        ),
        _rule(
            "required_consent",
            passed=consent_ok,
            kind="hard",
            explanation=(
                "The recorded consent state meets declared requirements."
                if consent_ok is True
                else "Consent eligibility remains unknown."
                if consent_ok is None
                else "Required consent is missing, prohibited, or incompatible "
                "with the cloning classification."
            ),
        ),
        _rule(
            "voice_not_blocked",
            passed=voice_available,
            kind="hard",
            explanation="The catalog voice is active or explicitly deprecated."
            if voice_available
            else "The catalog voice is unavailable or blocked.",
        ),
        _rule(
            "declared_capabilities",
            passed=(narration_ok and dialogue_ok and commercial_ok and role_capability_ok),
            kind="hard",
            explanation="Declared role capabilities and commercial scope are compatible."
            if narration_ok and dialogue_ok and commercial_ok and role_capability_ok
            else "The role requires a capability or scope the voice does not declare.",
        ),
        _rule(
            "role_length_suitability",
            passed=long_form_ok and word_count_ok,
            kind="hard",
            explanation="Declared long-form suitability matches the workload."
            if long_form_ok and word_count_ok
            else "The role workload requires long-form suitability.",
        ),
    ]
    locale = str(voice.get("locale", "")).casefold()
    role_locales = _string_values(requirements.get("locales"))
    expression = _string_values(
        voice.get("expressiveRange") or voice.get("emotionalOrExpressiveRange")
    )
    required_expression = _string_values(requirements.get("requiredExpressiveRange"))
    age_requirement = requirements.get("agePresentationRange")
    voice_age = voice.get("agePresentationRange")
    age_match: bool | None = None
    if isinstance(age_requirement, dict) and isinstance(voice_age, dict):
        required_minimum = _number(age_requirement.get("minimum"))
        required_maximum = _number(age_requirement.get("maximum"))
        voice_minimum = _number(voice_age.get("minimum"))
        voice_maximum = _number(voice_age.get("maximum"))
        if None not in (
            required_minimum,
            required_maximum,
            voice_minimum,
            voice_maximum,
        ):
            assert required_minimum is not None
            assert required_maximum is not None
            assert voice_minimum is not None
            assert voice_maximum is not None
            age_match = bool(
                required_minimum <= voice_maximum and voice_minimum <= required_maximum
            )
    required_presentations = _string_values(requirements.get("vocalPresentations"))
    required_textures = _string_values(requirements.get("preferredTextures"))
    voice_rate = voice.get("speakingRateRange")
    required_rate = requirements.get("speakingRateRange")
    rate_match: bool | None = None
    if isinstance(voice_rate, dict) and isinstance(required_rate, dict):
        voice_minimum = _number(voice_rate.get("minimum"))
        voice_maximum = _number(voice_rate.get("maximum"))
        required_minimum = _number(required_rate.get("minimum"))
        required_maximum = _number(required_rate.get("maximum"))
        if None not in (
            voice_minimum,
            voice_maximum,
            required_minimum,
            required_maximum,
        ):
            assert voice_minimum is not None
            assert voice_maximum is not None
            assert required_minimum is not None
            assert required_maximum is not None
            rate_match = bool(
                required_minimum <= voice_maximum and voice_minimum <= required_maximum
            )
    soft_results = [
        _rule(
            "locale_match",
            passed=(locale in role_locales if role_locales else None),
            kind="soft",
            weight=8,
            explanation="Locale metadata matches the configured production locale."
            if role_locales and locale in role_locales
            else "Locale metadata differs from the configured production locale."
            if role_locales
            else "No role locale preference is declared.",
        ),
        _rule(
            "narration_suitability",
            passed=narration_ok,
            kind="soft",
            weight=7,
            explanation="Declared narration suitability is visible as a preference.",
        ),
        _rule(
            "dialogue_suitability",
            passed=dialogue_ok,
            kind="soft",
            weight=7,
            explanation="Declared dialogue suitability is visible as a preference.",
        ),
        _rule(
            "expressive_range",
            passed=(required_expression <= expression if required_expression else None),
            kind="soft",
            weight=8,
            explanation="Declared expressive range covers the role preference."
            if required_expression and required_expression <= expression
            else "Declared expressive range does not cover the role preference."
            if required_expression
            else "No role expressive-range preference is declared.",
        ),
        _rule(
            "age_presentation",
            passed=age_match,
            kind="soft",
            weight=5,
            explanation="Declared age-presentation ranges overlap."
            if age_match is True
            else "Declared age-presentation ranges do not overlap."
            if age_match is False
            else "No role age-presentation preference is declared.",
        ),
        _rule(
            "vocal_presentation",
            passed=(
                str(voice.get("vocalPresentation", "")).casefold() in required_presentations
                if required_presentations
                else None
            ),
            kind="soft",
            weight=5,
            explanation="Declared vocal presentation matches the role preference."
            if required_presentations
            and str(voice.get("vocalPresentation", "")).casefold() in required_presentations
            else "Declared vocal presentation differs from the role preference."
            if required_presentations
            else "No role vocal-presentation preference is declared.",
        ),
        _rule(
            "vocal_texture",
            passed=(
                str(
                    voice.get(
                        "vocalTexture",
                        voice.get("vocalWeightOrTexture", ""),
                    )
                ).casefold()
                in required_textures
                if required_textures
                else None
            ),
            kind="soft",
            weight=5,
            explanation="Declared vocal texture matches the role preference."
            if required_textures
            and str(
                voice.get(
                    "vocalTexture",
                    voice.get("vocalWeightOrTexture", ""),
                )
            ).casefold()
            in required_textures
            else "Declared vocal texture differs from the role preference."
            if required_textures
            else "No role vocal-texture preference is declared.",
        ),
        _rule(
            "speaking_rate",
            passed=rate_match,
            kind="soft",
            weight=4,
            explanation="Declared speaking-rate ranges overlap."
            if rate_match is True
            else "Declared speaking-rate ranges do not overlap."
            if rate_match is False
            else "No role speaking-rate preference is declared.",
        ),
        _rule(
            "emotional_range",
            passed=(required_expression <= expression if required_expression else None),
            kind="soft",
            weight=5,
            explanation="Declared emotional range covers the role preference."
            if required_expression and required_expression <= expression
            else "Declared emotional range does not cover the role preference."
            if required_expression
            else "No role emotional-range preference is declared.",
        ),
        _rule(
            "long_form_preference",
            passed=long_form_ok and word_count_ok,
            kind="soft",
            weight=6,
            explanation="Declared long-form suitability matches the workload."
            if long_form_ok and word_count_ok
            else "Long-form suitability is limited for this workload.",
        ),
    ]
    hard_failed = any(value["result"] == "fail" for value in hard_results)
    hard_unknown = any(value["result"] == "unknown" for value in hard_results)
    if hard_failed:
        status: CompatibilityStatus = "ineligible"
    elif hard_unknown:
        status = "unknown"
    elif rights_state == "restricted" or voice_state == "deprecated":
        status = "conditional"
    else:
        status = "eligible"
    base_score = 50
    score = min(
        100,
        base_score + sum(value["weight"] for value in soft_results if value["result"] == "pass"),
    )
    if status == "ineligible":
        score = min(score, 39)
    elif status == "unknown":
        score = min(score, 49)
    explanation = (
        "Deterministic declared-metadata assessment; it is not an artistic "
        "correctness or acoustic-similarity claim."
    )
    values = {
        "compatibilityStatus": status,
        "compatibilityScore": score / 100,
        "confidenceClassification": "high" if status in {"eligible", "ineligible"} else "unknown",
        "hardConstraintResults": hard_results,
        "softPreferenceResults": soft_results,
        "rightsEligibility": rights_state,
        "languageEligibility": (
            "eligible"
            if language_ok is True
            else "unknown"
            if language_ok is None
            else "ineligible"
        ),
        "providerAvailability": provider_available,
        "modelAvailability": model_available,
        "longFormSuitability": long_form_ok,
        "explanation": explanation,
        "inputFingerprint": input_fingerprint,
    }
    return values | {"assessmentFingerprint": request_fingerprint(values)}


def generate_pairwise_candidate_conflicts(
    *,
    roles: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    catalog: VoiceCatalog,
    input_fingerprint: str,
) -> list[dict[str, Any]]:
    """Build deterministic cross-role conflicts from current bounded candidates."""

    voices_by_id = {
        str(value.get("voiceProfileId") or value.get("profileId")): value
        for value in catalog.voices
    }
    by_role: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        role_id = str(candidate["roleId"])
        by_role.setdefault(role_id, []).append(candidate)
    for values in by_role.values():
        values.sort(
            key=lambda value: (
                int(value.get("ordinal", MAX_FINAL_CANDIDATES)),
                str(value["voiceProfileId"]),
            )
        )

    conflicts: list[dict[str, Any]] = []
    major_roles = [
        role for role in roles if role["roleType"] in {"primary_narrator", "named_character"}
    ]
    for index, left in enumerate(major_roles):
        left_candidates = by_role.get(str(left["roleId"]), [])
        if not left_candidates:
            continue
        for right in major_roles[index + 1 :]:
            right_candidates = by_role.get(str(right["roleId"]), [])
            if not right_candidates:
                continue
            left_voice = str(left_candidates[0]["voiceProfileId"])
            right_voice = str(right_candidates[0]["voiceProfileId"])
            if left_voice == right_voice:
                category = (
                    "narrator_major_character_reuse"
                    if "primary_narrator" in {left["roleType"], right["roleType"]}
                    else "incompatible_voice_reuse"
                )
                conflict_values = {
                    "contractVersion": CASTING_CONTRACT_VERSION,
                    "conflictId": stable_id(
                        "phase3a-casting-conflict",
                        category,
                        left["roleId"],
                        right["roleId"],
                        left_voice,
                        input_fingerprint,
                    ),
                    "category": category,
                    "severity": "warning",
                    "primaryRoleId": left["roleId"],
                    "relatedRoleIds": [right["roleId"]],
                    "voiceProfileIds": [left_voice],
                    "status": "open",
                    "explanation": (
                        "The deterministic metadata proposal reuses one catalog voice "
                        "for roles configured as differentiation-sensitive."
                    ),
                    "metadataBased": True,
                    "acousticSimilarityClaimed": False,
                    "inputFingerprint": input_fingerprint,
                }
                conflicts.append(
                    conflict_values | {"conflictFingerprint": request_fingerprint(conflict_values)}
                )
                break
        if conflicts:
            break

    def metadata_signature(voice_id: str) -> tuple[str, str, str, str] | None:
        voice = voices_by_id[voice_id]
        signature = (
            str(voice.get("locale", "")).casefold(),
            str(voice.get("vocalPresentation", "")).casefold(),
            str(voice.get("vocalTexture", voice.get("vocalWeightOrTexture", ""))).casefold(),
            str(voice.get("pitchRange", voice.get("pitchRangeClassification", ""))).casefold(),
        )
        return signature if all(signature) else None

    metadata_conflict_added = False
    for index, left in enumerate(major_roles):
        left_by_signature: dict[tuple[str, str, str, str], list[str]] = {}
        for candidate in by_role.get(str(left["roleId"]), []):
            voice_id = str(candidate["voiceProfileId"])
            signature = metadata_signature(voice_id)
            if signature is not None:
                left_by_signature.setdefault(signature, []).append(voice_id)
        for right in major_roles[index + 1 :]:
            right_by_signature: dict[tuple[str, str, str, str], list[str]] = {}
            for candidate in by_role.get(str(right["roleId"]), []):
                voice_id = str(candidate["voiceProfileId"])
                signature = metadata_signature(voice_id)
                if signature is not None:
                    right_by_signature.setdefault(signature, []).append(voice_id)
            similar: tuple[str, str] | None = None
            for signature in sorted(left_by_signature.keys() & right_by_signature.keys()):
                for left_voice in sorted(left_by_signature[signature]):
                    matching_right_voice = next(
                        (
                            value
                            for value in sorted(right_by_signature[signature])
                            if value != left_voice
                        ),
                        None,
                    )
                    if matching_right_voice is not None:
                        similar = (left_voice, matching_right_voice)
                        break
                if similar is not None:
                    break
            if similar is None:
                continue
            conflict_values = {
                "contractVersion": CASTING_CONTRACT_VERSION,
                "conflictId": stable_id(
                    "phase3a-casting-conflict",
                    "metadata-differentiation",
                    left["roleId"],
                    right["roleId"],
                    *similar,
                    input_fingerprint,
                ),
                "category": "metadata_similarity_risk",
                "severity": "warning",
                "primaryRoleId": left["roleId"],
                "relatedRoleIds": [right["roleId"]],
                "voiceProfileIds": list(similar),
                "status": "open",
                "explanation": (
                    "Two role-specific candidate voices share declared locale, "
                    "vocal-presentation, texture, and pitch classifications. "
                    "This is metadata-based risk only."
                ),
                "metadataBased": True,
                "acousticSimilarityClaimed": False,
                "inputFingerprint": input_fingerprint,
            }
            conflicts.append(
                conflict_values | {"conflictFingerprint": request_fingerprint(conflict_values)}
            )
            metadata_conflict_added = True
            break
        if metadata_conflict_added:
            break
    return conflicts


def generate_candidates(
    *,
    roles: list[dict[str, Any]],
    catalog: VoiceCatalog,
    input_fingerprint: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rights_reference_time = catalog.rights_evaluation_time
    providers = {str(value["providerId"]): value for value in catalog.providers}
    models = {(str(value["providerId"]), str(value["modelId"])): value for value in catalog.models}
    rights = {
        str(value.get("voiceProfileId") or value.get("profileId")): value
        for value in catalog.rights
    }
    voices_by_id = {
        str(value.get("voiceProfileId") or value.get("profileId")): value
        for value in catalog.voices
    }
    voices_in_stable_order = tuple(
        sorted(
            catalog.voices,
            key=lambda value: str(value.get("voiceProfileId") or value.get("profileId")),
        )
    )
    candidates: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    pre_reduction_cache: dict[
        tuple[str, str, tuple[str, ...], bool, int | None],
        tuple[dict[str, Any], ...],
    ] = {}
    assessment_cache: dict[tuple[str, str], dict[str, Any]] = {}
    status_order = {"eligible": 0, "conditional": 1, "unknown": 2, "ineligible": 3}
    for role in roles:
        role_languages = _string_values(role.get("languageRequirements"))
        long_form_required = bool(_object(role["performanceRequirements"]).get("longFormRequired"))
        approximate_words = role.get("approximateWordCount")
        pre_reduction_key_values = (
            str(role.get("roleType", "")),
            str(role.get("roleImportance", "")),
            tuple(sorted(role_languages)),
            long_form_required,
            (
                approximate_words
                if isinstance(approximate_words, int) and not isinstance(approximate_words, bool)
                else None
            ),
        )

        def pre_reduction_key(
            voice: dict[str, Any],
            role: dict[str, Any] = role,
        ) -> tuple[int, int, int, int, int, str]:
            voice_id = str(voice.get("voiceProfileId") or voice.get("profileId"))
            provider = providers[str(voice["providerId"])]
            model = models[(str(voice["providerId"]), str(voice["modelId"]))]
            rights_record = rights[voice_id]
            facts = _hard_constraint_facts(
                role=role,
                voice=voice,
                rights=rights_record,
                provider=provider,
                model=model,
                rights_reference_time=rights_reference_time,
            )
            eligibility_order = (
                3
                if facts.failure_count
                else 2
                if facts.unknown_count
                else 1
                if facts.rights_state == "restricted" or facts.voice_state == "deprecated"
                else 0
            )
            return (
                eligibility_order,
                facts.failure_count,
                facts.unknown_count,
                {"verified": 0, "restricted": 1, "unknown": 2, "prohibited": 3}.get(
                    facts.rights_state,
                    4,
                ),
                {"active": 0, "deprecated": 1, "unavailable": 2, "blocked": 3}.get(
                    facts.voice_state,
                    4,
                ),
                voice_id,
            )

        cached_pre_reduction = pre_reduction_cache.get(pre_reduction_key_values)
        if cached_pre_reduction is None:
            ranked_voices = heapq.nsmallest(
                MAX_PRE_REDUCTION_CANDIDATES,
                voices_in_stable_order,
                key=pre_reduction_key,
            )
            representative_predicates = (
                lambda value: rights[str(value["voiceProfileId"])]["state"] == "restricted",
                lambda value: rights[str(value["voiceProfileId"])]["state"] == "unknown",
                lambda value: rights[str(value["voiceProfileId"])]["state"] == "prohibited",
                lambda value: (
                    providers[str(value["providerId"])]["runtimeAvailability"] != "available"
                ),
                lambda value: (
                    models[(str(value["providerId"]), str(value["modelId"]))]["availability"]
                    != "available"
                ),
                lambda value, role_languages=role_languages: (
                    not (
                        role_languages
                        <= _string_values(
                            value.get("languages")
                            or value.get("languageSupport")
                            or value.get("language")
                        )
                        or "*"
                        in _string_values(
                            value.get("languages")
                            or value.get("languageSupport")
                            or value.get("language")
                        )
                    )
                ),
                lambda value, long_form_required=long_form_required: (
                    long_form_required and not _boolish(value.get("longFormSuitability"))
                ),
                lambda value: value.get("state") == "deprecated",
                lambda value: value.get("state") in {"unavailable", "blocked"},
            )
            representative_voices: list[dict[str, Any]] = []
            for predicate in representative_predicates:
                representative = next(
                    (value for value in voices_in_stable_order if predicate(value)),
                    None,
                )
                if representative is not None and representative not in representative_voices:
                    representative_voices.append(representative)
            ordinary_voices = [
                value for value in ranked_voices if value not in representative_voices
            ][: MAX_PRE_REDUCTION_CANDIDATES - len(representative_voices)]
            cached_pre_reduction = tuple([*ordinary_voices, *representative_voices])
            pre_reduction_cache[pre_reduction_key_values] = cached_pre_reduction
        pre_reduction = cached_pre_reduction
        role_assessment_fingerprint = request_fingerprint(
            {
                "roleType": role.get("roleType"),
                "roleImportance": role.get("roleImportance"),
                "languageRequirements": role.get("languageRequirements"),
                "performanceRequirements": role.get("performanceRequirements"),
                "approximateWordCount": role.get("approximateWordCount"),
            }
        )
        values: list[dict[str, Any]] = []
        for voice in pre_reduction:
            voice_id = str(voice.get("voiceProfileId") or voice.get("profileId"))
            provider_id = str(voice["providerId"])
            model_id = str(voice["modelId"])
            assessment_cache_key = (role_assessment_fingerprint, voice_id)
            assessment = assessment_cache.get(assessment_cache_key)
            if assessment is None:
                assessment = compatibility_assessment(
                    role=role,
                    voice=voice,
                    rights=rights[voice_id],
                    provider=providers[provider_id],
                    model=models[(provider_id, model_id)],
                    input_fingerprint=input_fingerprint,
                    rights_reference_time=rights_reference_time,
                )
                assessment_cache[assessment_cache_key] = assessment
            candidate_values = {
                "contractVersion": CASTING_CONTRACT_VERSION,
                "candidateId": stable_id(
                    "phase3a-casting-candidate",
                    role["roleId"],
                    voice_id,
                    input_fingerprint,
                ),
                "roleId": role["roleId"],
                "voiceProfileId": voice_id,
                **assessment,
                "conflictWarnings": [],
                "provenance": {
                    "origin": "runtime_agent",
                    "producerId": CASTING_PRODUCER_ID,
                    "producerVersion": CASTING_PRODUCER_VERSION,
                    "recordedAt": CASTING_PRODUCER_RECORDED_AT,
                    "inputFingerprint": input_fingerprint,
                },
            }
            values.append(
                candidate_values | {"outputFingerprint": request_fingerprint(candidate_values)}
            )
        values.sort(
            key=lambda value: (
                status_order[str(value["compatibilityStatus"])],
                -float(value["compatibilityScore"]),
                str(value["voiceProfileId"]),
            )
        )
        for pre_reduction_rank, value in enumerate(values, start=1):
            value["preReductionRank"] = pre_reduction_rank
        required_candidates: list[dict[str, Any]] = []
        predicates = (
            lambda value: value["rightsEligibility"] == "restricted",
            lambda value: value["rightsEligibility"] == "unknown",
            lambda value: value["rightsEligibility"] == "prohibited",
            lambda value: value["providerAvailability"] is False,
            lambda value: value["modelAvailability"] is False,
            lambda value: value["languageEligibility"] == "ineligible",
            lambda value: value["longFormSuitability"] is False,
            lambda value, lookup=voices_by_id: (
                lookup[str(value["voiceProfileId"])].get("state") == "deprecated"
            ),
            lambda value, lookup=voices_by_id: (
                lookup[str(value["voiceProfileId"])].get("state") == "unavailable"
            ),
        )
        for predicate in predicates:
            match = next((value for value in values if predicate(value)), None)
            if match is not None and match not in required_candidates:
                required_candidates.append(match)
        ordinary = [value for value in values if value not in required_candidates][
            : MAX_FINAL_CANDIDATES - len(required_candidates)
        ]
        published = ordinary + required_candidates
        published.sort(
            key=lambda value: (
                status_order[str(value["compatibilityStatus"])],
                -float(value["compatibilityScore"]),
                str(value["voiceProfileId"]),
            )
        )
        for ordinal, value in enumerate(published):
            value["ordinal"] = ordinal
            fingerprint_values = dict(value)
            fingerprint_values.pop("outputFingerprint", None)
            value["outputFingerprint"] = request_fingerprint(fingerprint_values)
        by_role[str(role["roleId"])] = published
        candidates.extend(published)

    conflicts = generate_pairwise_candidate_conflicts(
        roles=roles,
        candidates=candidates,
        catalog=catalog,
        input_fingerprint=input_fingerprint,
    )

    def candidate_rule_failed(
        candidate: dict[str, Any],
        *,
        group: str,
        rule_id: str,
    ) -> bool:
        values = candidate.get(group)
        return isinstance(values, list) and any(
            isinstance(value, dict)
            and value.get("ruleId") == rule_id
            and value.get("result") == "fail"
            for value in values
        )

    def append_role_conflict(
        *,
        role: dict[str, Any],
        category: str,
        candidate: dict[str, Any] | None,
        explanation: str,
    ) -> None:
        if len(conflicts) >= MAX_CASTING_CONFLICTS:
            return
        voice_ids = [str(candidate["voiceProfileId"])] if candidate is not None else []
        conflict_values = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "conflictId": stable_id(
                "phase3a-role-metadata-conflict",
                category,
                role["roleId"],
                *voice_ids,
                input_fingerprint,
            ),
            "category": category,
            "severity": "warning",
            "primaryRoleId": role["roleId"],
            "relatedRoleIds": [],
            "voiceProfileIds": voice_ids,
            "status": "open",
            "explanation": explanation,
            "metadataBased": True,
            "acousticSimilarityClaimed": False,
            "inputFingerprint": input_fingerprint,
        }
        conflicts.append(
            conflict_values | {"conflictFingerprint": request_fingerprint(conflict_values)}
        )

    for role in roles:
        role_candidates = by_role.get(str(role["roleId"]), [])
        if role["roleType"] == "unresolved_speaker":
            append_role_conflict(
                role=role,
                category="unresolved_role_assignment",
                candidate=None,
                explanation=(
                    "The approved analysis explicitly retains an unresolved "
                    "speaker role that requires human casting disposition."
                ),
            )
        conflict_candidates: tuple[
            tuple[str, Any, str],
            ...,
        ] = (
            (
                "accent_or_locale_mismatch",
                lambda value: candidate_rule_failed(
                    value,
                    group="softPreferenceResults",
                    rule_id="locale_match",
                ),
                "The candidate locale differs from the role's declared locale "
                "preference; no accent inference is made.",
            ),
            (
                "insufficient_expressive_range",
                lambda value: (
                    candidate_rule_failed(
                        value,
                        group="softPreferenceResults",
                        rule_id="expressive_range",
                    )
                    or len(
                        _string_values(
                            voices_by_id[str(value["voiceProfileId"])].get("expressiveRange")
                        )
                    )
                    < 2
                ),
                "The declared expressive-range metadata is narrower than the "
                "role preference or fixture differentiation threshold.",
            ),
            (
                "rights_conflict",
                lambda value: value["rightsEligibility"] != "verified",
                "The candidate has restricted, unknown, or prohibited recorded "
                "rights and requires the applicable governance disposition.",
            ),
            (
                "provider_or_model_unavailable",
                lambda value: (
                    value["providerAvailability"] is False or value["modelAvailability"] is False
                ),
                "The candidate's declared provider or model is unavailable or disabled.",
            ),
            (
                "deprecated_voice",
                lambda value: (
                    voices_by_id[str(value["voiceProfileId"])].get("state") == "deprecated"
                ),
                "The candidate voice is declared deprecated in this catalog revision.",
            ),
            (
                "role_length_suitability",
                lambda value: value["longFormSuitability"] is False,
                "The candidate's declared role-length or long-form suitability "
                "does not meet this workload.",
            ),
        )
        for category, predicate, explanation in conflict_candidates:
            match = next(
                (value for value in role_candidates if predicate(value)),
                None,
            )
            if match is not None:
                append_role_conflict(
                    role=role,
                    category=category,
                    candidate=match,
                    explanation=explanation,
                )
    return candidates, conflicts


def validate_casting_result(
    result: dict[str, Any],
    *,
    expected_input_fingerprint: str,
    expected_catalog_fingerprint: str,
    expected_profile_fingerprint: str,
) -> None:
    def reject(message: str) -> ServiceError:
        return ServiceError(
            422,
            "CASTING_OUTPUT_INVALID",
            message,
        )

    def non_empty_text(
        value: Any,
        *,
        maximum: int = MAX_CASTING_EXPLANATION_CODE_POINTS,
    ) -> bool:
        return (
            isinstance(value, str)
            and bool(value.strip())
            and len(value) <= maximum
            and all(ord(character) >= 32 or character == "\t" for character in value)
        )

    def non_negative_integer(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def fingerprint_matches(value: dict[str, Any], field: str) -> bool:
        fingerprint = value.get(field)
        fingerprint_values = dict(value)
        fingerprint_values.pop(field, None)
        return isinstance(fingerprint, str) and fingerprint == request_fingerprint(
            fingerprint_values
        )

    expected_result_fields = {
        "contractVersion",
        "castingRunId",
        "inputFingerprint",
        "catalogRevisionId",
        "catalogFingerprint",
        "castingProfileFingerprint",
        "stages",
        "roles",
        "candidates",
        "conflicts",
        "outputFingerprint",
    }
    if (
        set(result) != expected_result_fields
        or result.get("contractVersion") != CASTING_CONTRACT_VERSION
        or not non_empty_text(result.get("castingRunId"), maximum=128)
        or result.get("inputFingerprint") != expected_input_fingerprint
        or not non_empty_text(result.get("catalogRevisionId"), maximum=128)
        or result.get("catalogFingerprint") != expected_catalog_fingerprint
        or result.get("castingProfileFingerprint") != expected_profile_fingerprint
        or result.get("stages") != list(CASTING_JOB_STAGES)
        or not fingerprint_matches(result, "outputFingerprint")
    ):
        raise reject("The deterministic casting output failed verification.")
    roles = result.get("roles")
    candidates = result.get("candidates")
    conflicts = result.get("conflicts")
    if (
        not isinstance(roles, list)
        or not 1 <= len(roles) <= MAX_PRODUCTION_ROLES
        or not isinstance(candidates, list)
        or not isinstance(conflicts, list)
        or len(candidates) > len(roles) * MAX_FINAL_CANDIDATES
        or len(conflicts) > MAX_CASTING_CONFLICTS
    ):
        raise reject("The deterministic casting output exceeded its bounds.")

    narrator_role_fields = {
        "contractVersion",
        "roleId",
        "projectId",
        "roleType",
        "analysisEntityId",
        "effectiveDisplayLabel",
        "analysisRunId",
        "analysisSnapshotId",
        "analysisSnapshotFingerprint",
        "dialogueLineCount",
        "narrationSpanCount",
        "approximateWordCount",
        "chapterRange",
        "sceneRange",
        "languageRequirements",
        "performanceRequirements",
        "warnings",
        "provenance",
        "status",
        "revision",
        "roleFingerprint",
    }
    character_role_fields = narrator_role_fields | {
        "characterId",
        "roleImportance",
        "unresolvedMaterialExplicitlyRepresented",
    }
    role_ids: set[str] = set()
    for role in roles:
        if not isinstance(role, dict):
            raise reject("A production role is not an object.")
        role_type = role.get("roleType")
        expected_fields = (
            narrator_role_fields
            if role_type in {"primary_narrator", "secondary_narrator"}
            else character_role_fields
        )
        role_id = role.get("roleId")
        if (
            set(role) != expected_fields
            or role.get("contractVersion") != CASTING_CONTRACT_VERSION
            or role_type not in PRODUCTION_ROLE_TYPES
            or not non_empty_text(role_id, maximum=128)
            or role_id in role_ids
            or not non_empty_text(role.get("projectId"), maximum=128)
            or not non_empty_text(role.get("effectiveDisplayLabel"), maximum=200)
            or not non_empty_text(role.get("analysisRunId"), maximum=128)
            or not non_empty_text(role.get("analysisSnapshotId"), maximum=128)
            or not non_empty_text(
                role.get("analysisSnapshotFingerprint"),
                maximum=64,
            )
            or role.get("status") != "active"
            or role.get("revision") != 1
            or not fingerprint_matches(role, "roleFingerprint")
        ):
            raise reject("A production role failed structural verification.")
        assert isinstance(role_id, str)
        role_ids.add(role_id)
        analysis_entity_id = role.get("analysisEntityId")
        if analysis_entity_id is not None and not non_empty_text(
            analysis_entity_id,
            maximum=128,
        ):
            raise reject("A production role has an invalid analysis entity reference.")
        counts = (
            role.get("dialogueLineCount"),
            role.get("narrationSpanCount"),
            role.get("approximateWordCount"),
        )
        if any(not non_negative_integer(value) for value in counts):
            raise reject("A production role has an invalid workload.")
        if role_type in {"named_character", "group_or_crowd"} and not (
            isinstance(role.get("dialogueLineCount"), int) and role["dialogueLineCount"] > 0
        ):
            raise reject("A character production role has no approved dialogue workload.")
        for range_field in ("chapterRange", "sceneRange"):
            role_range = role.get(range_field)
            if not isinstance(role_range, dict) or set(role_range) != {
                "firstOrdinal",
                "lastOrdinal",
            }:
                raise reject("A production role has an invalid evidence range.")
            first_ordinal = role_range.get("firstOrdinal")
            last_ordinal = role_range.get("lastOrdinal")
            if (
                first_ordinal is None
                and last_ordinal is not None
                or first_ordinal is not None
                and (
                    not non_negative_integer(first_ordinal)
                    or not non_negative_integer(last_ordinal)
                    or first_ordinal < 1
                    or last_ordinal < first_ordinal
                )
            ):
                raise reject("A production role has an invalid evidence range.")
        languages = role.get("languageRequirements")
        if (
            not isinstance(languages, list)
            or not 1 <= len(languages) <= 32
            or any(not non_empty_text(value, maximum=32) for value in languages)
            or len(languages) != len(set(languages))
            or not isinstance(role.get("performanceRequirements"), dict)
        ):
            raise reject("A production role has invalid performance requirements.")
        warnings = role.get("warnings")
        if (
            not isinstance(warnings, list)
            or len(warnings) > MAX_CASTING_WARNINGS_PER_ENTITY
            or any(not isinstance(value, dict) for value in warnings)
            or not isinstance(role.get("provenance"), dict)
        ):
            raise reject("A production role has invalid warning or provenance data.")
        if role_type in {"primary_narrator", "secondary_narrator"}:
            continue
        character_id = role.get("characterId")
        role_importance = role.get("roleImportance")
        unresolved = role_type == "unresolved_speaker"
        if (
            character_id is not None
            and not non_empty_text(character_id, maximum=128)
            or role_type in {"named_character", "group_or_crowd"}
            and not non_empty_text(character_id, maximum=128)
            or role_importance not in {"major", "supporting", "minor", "unresolved"}
            or unresolved
            and role_importance != "unresolved"
            or not unresolved
            and role_importance == "unresolved"
            or role.get("unresolvedMaterialExplicitlyRepresented") is not unresolved
        ):
            raise reject("A character production role lost its governed identity.")

    candidate_fields = {
        "contractVersion",
        "candidateId",
        "roleId",
        "voiceProfileId",
        "compatibilityStatus",
        "compatibilityScore",
        "confidenceClassification",
        "hardConstraintResults",
        "softPreferenceResults",
        "rightsEligibility",
        "languageEligibility",
        "providerAvailability",
        "modelAvailability",
        "longFormSuitability",
        "explanation",
        "inputFingerprint",
        "assessmentFingerprint",
        "conflictWarnings",
        "provenance",
        "preReductionRank",
        "ordinal",
        "outputFingerprint",
    }
    assessment_fields = (
        "compatibilityStatus",
        "compatibilityScore",
        "confidenceClassification",
        "hardConstraintResults",
        "softPreferenceResults",
        "rightsEligibility",
        "languageEligibility",
        "providerAvailability",
        "modelAvailability",
        "longFormSuitability",
        "explanation",
        "inputFingerprint",
    )

    def validate_rules(
        values: Any,
        *,
        expected_ids: tuple[str, ...],
        kind: Literal["hard", "soft"],
    ) -> bool:
        if (
            not isinstance(values, list)
            or len(values) > MAX_CASTING_RULE_RESULTS
            or len(values) != len(expected_ids)
        ):
            return False
        rule_ids: list[str] = []
        for value in values:
            if (
                not isinstance(value, dict)
                or set(value) != {"ruleId", "kind", "result", "weight", "explanation"}
                or value.get("kind") != kind
                or value.get("result") not in {"pass", "fail", "unknown"}
                or not isinstance(value.get("weight"), int)
                or isinstance(value.get("weight"), bool)
                or not 0 <= value["weight"] <= 100
                or not non_empty_text(value.get("explanation"))
            ):
                return False
            rule_id = value.get("ruleId")
            if not isinstance(rule_id, str):
                return False
            rule_ids.append(rule_id)
        return tuple(rule_ids) == expected_ids

    candidate_ids: set[str] = set()
    candidate_voice_pairs: set[tuple[str, str]] = set()
    candidates_by_role: dict[str, list[dict[str, Any]]] = {role_id: [] for role_id in role_ids}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise reject("A casting candidate is not an object.")
        candidate_id = candidate.get("candidateId")
        role_id = candidate.get("roleId")
        voice_id = candidate.get("voiceProfileId")
        score = candidate.get("compatibilityScore")
        rank = candidate.get("preReductionRank")
        ordinal = candidate.get("ordinal")
        if (
            set(candidate) != candidate_fields
            or candidate.get("contractVersion") != CASTING_CONTRACT_VERSION
            or not non_empty_text(candidate_id, maximum=128)
            or candidate_id in candidate_ids
            or role_id not in role_ids
            or not non_empty_text(voice_id, maximum=128)
            or (role_id, voice_id) in candidate_voice_pairs
            or candidate.get("compatibilityStatus")
            not in {"eligible", "conditional", "ineligible", "unknown"}
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= score <= 1
            or candidate.get("confidenceClassification") not in {"high", "unknown"}
            or candidate.get("rightsEligibility")
            not in {"verified", "restricted", "unknown", "prohibited"}
            or candidate.get("languageEligibility") not in {"eligible", "ineligible", "unknown"}
            or not isinstance(candidate.get("providerAvailability"), bool)
            or not isinstance(candidate.get("modelAvailability"), bool)
            or not isinstance(candidate.get("longFormSuitability"), bool)
            or not non_empty_text(candidate.get("explanation"))
            or candidate.get("inputFingerprint") != expected_input_fingerprint
            or not isinstance(rank, int)
            or isinstance(rank, bool)
            or not 1 <= rank <= MAX_PRE_REDUCTION_CANDIDATES
            or not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or not 0 <= ordinal < MAX_FINAL_CANDIDATES
            or not isinstance(candidate.get("conflictWarnings"), list)
            or len(candidate["conflictWarnings"]) > MAX_CASTING_WARNINGS_PER_ENTITY
            or any(not isinstance(value, dict) for value in candidate["conflictWarnings"])
            or not isinstance(candidate.get("provenance"), dict)
            or not validate_rules(
                candidate.get("hardConstraintResults"),
                expected_ids=HARD_CONSTRAINT_IDS,
                kind="hard",
            )
            or not validate_rules(
                candidate.get("softPreferenceResults"),
                expected_ids=SOFT_PREFERENCE_IDS,
                kind="soft",
            )
            or len(candidate["hardConstraintResults"]) + len(candidate["softPreferenceResults"])
            > MAX_CASTING_RULE_RESULTS
        ):
            raise reject("A casting candidate failed structural verification.")
        assert isinstance(candidate_id, str)
        assert isinstance(role_id, str)
        assert isinstance(voice_id, str)
        assessment_values = {field: candidate[field] for field in assessment_fields}
        if candidate.get("assessmentFingerprint") != request_fingerprint(
            assessment_values
        ) or not fingerprint_matches(candidate, "outputFingerprint"):
            raise reject("A casting candidate fingerprint is invalid.")
        candidate_ids.add(candidate_id)
        candidate_voice_pairs.add((role_id, voice_id))
        candidates_by_role[role_id].append(candidate)

    for role_id, role_candidates in candidates_by_role.items():
        ordinals = [value["ordinal"] for value in role_candidates]
        ranks = [value["preReductionRank"] for value in role_candidates]
        if (
            not 1 <= len(role_candidates) <= MAX_FINAL_CANDIDATES
            or sorted(ordinals) != list(range(len(role_candidates)))
            or len(ranks) != len(set(ranks))
        ):
            raise reject(f"Casting candidates for role {role_id} failed bounded ordering.")

    conflict_fields = {
        "contractVersion",
        "conflictId",
        "category",
        "severity",
        "primaryRoleId",
        "relatedRoleIds",
        "voiceProfileIds",
        "status",
        "explanation",
        "metadataBased",
        "acousticSimilarityClaimed",
        "inputFingerprint",
        "conflictFingerprint",
    }
    candidate_voice_ids_by_role = {
        role_id: {str(candidate["voiceProfileId"]) for candidate in role_candidates}
        for role_id, role_candidates in candidates_by_role.items()
    }
    conflict_ids: set[str] = set()
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            raise reject("A casting conflict is not an object.")
        conflict_id = conflict.get("conflictId")
        primary_role_id = conflict.get("primaryRoleId")
        related_role_ids = conflict.get("relatedRoleIds")
        voice_profile_ids = conflict.get("voiceProfileIds")
        if (
            set(conflict) != conflict_fields
            or conflict.get("contractVersion") != CASTING_CONTRACT_VERSION
            or not non_empty_text(conflict_id, maximum=128)
            or conflict_id in conflict_ids
            or conflict.get("category") not in CASTING_CONFLICT_CATEGORIES
            or conflict.get("severity") != "warning"
            or primary_role_id not in role_ids
            or not isinstance(related_role_ids, list)
            or len(related_role_ids) > MAX_PRODUCTION_ROLES - 1
            or any(value not in role_ids for value in related_role_ids)
            or primary_role_id in related_role_ids
            or len(related_role_ids) != len(set(related_role_ids))
            or not isinstance(voice_profile_ids, list)
            or len(voice_profile_ids) > MAX_PRODUCTION_ROLES
            or any(not non_empty_text(value, maximum=128) for value in voice_profile_ids)
            or len(voice_profile_ids) != len(set(voice_profile_ids))
            or conflict.get("status") != "open"
            or not non_empty_text(conflict.get("explanation"))
            or conflict.get("metadataBased") is not True
            or conflict.get("acousticSimilarityClaimed") is not False
            or conflict.get("inputFingerprint") != expected_input_fingerprint
            or not fingerprint_matches(conflict, "conflictFingerprint")
        ):
            raise reject("A casting conflict failed structural verification.")
        assert isinstance(conflict_id, str)
        assert isinstance(primary_role_id, str)
        assert isinstance(related_role_ids, list)
        assert isinstance(voice_profile_ids, list)
        referenced_role_ids = [primary_role_id, *related_role_ids]
        referenced_candidate_voice_ids = set().union(
            *(candidate_voice_ids_by_role[role_id] for role_id in referenced_role_ids)
        )
        if any(voice_id not in referenced_candidate_voice_ids for voice_id in voice_profile_ids):
            raise reject("A casting conflict references an unpublished candidate.")
        category = conflict.get("category")
        if category == "unresolved_role_assignment":
            if voice_profile_ids:
                raise reject("An unresolved-role conflict cannot claim a voice.")
        elif not voice_profile_ids:
            raise reject("A candidate-specific conflict has no candidate voice.")
        if category in {
            "incompatible_voice_reuse",
            "narrator_major_character_reuse",
        } and (
            len(referenced_role_ids) != 2
            or len(voice_profile_ids) != 1
            or any(
                voice_profile_ids[0] not in candidate_voice_ids_by_role[role_id]
                for role_id in referenced_role_ids
            )
        ):
            raise reject("A voice-reuse conflict is not bound to both roles.")
        if category == "metadata_similarity_risk" and (
            len(referenced_role_ids) != 2
            or len(voice_profile_ids) != 2
            or voice_profile_ids[0] not in candidate_voice_ids_by_role[referenced_role_ids[0]]
            or voice_profile_ids[1] not in candidate_voice_ids_by_role[referenced_role_ids[1]]
        ):
            raise reject("A metadata-similarity conflict is not bound to its role candidates.")
        conflict_ids.add(conflict_id)
