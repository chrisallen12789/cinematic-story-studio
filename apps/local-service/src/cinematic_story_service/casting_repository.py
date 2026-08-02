from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from threading import RLock
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .casting import (
    CASTING_CONTRACT_VERSION,
    CASTING_GATE_IDS,
    CASTING_JOB_STAGES,
    CASTING_PRODUCER_ID,
    CASTING_PRODUCER_VERSION,
    CASTING_PROFILE_FINGERPRINT,
    CASTING_PROFILE_ID,
    CASTING_PROFILE_VALUES,
    CASTING_PROFILE_VERSION,
    DEFAULT_CASTING_PAGE_SIZE,
    LEGACY_CASTING_PROFILE_FINGERPRINT,
    LEGACY_CASTING_PROFILE_ID,
    MAX_CASTING_CONFLICTS,
    MAX_CASTING_CORRECTIONS_PER_RUN,
    MAX_CASTING_PAGE_SIZE,
    MAX_CASTING_WARNINGS_PER_ENTITY,
    MAX_PRODUCTION_ROLES,
    RIGHTS_POLICY_VERSION,
    casting_profile,
    compatibility_assessment,
    generate_candidates,
    generate_pairwise_candidate_conflicts,
    governed_private_audition_binding_is_exact,
    load_governed_voice_catalog,
    rights_record_is_current,
)
from .database import Database
from .errors import ServiceError, not_found
from .models import (
    AnalysisEntityRow,
    AnalysisExecutionRow,
    AnalysisRunRow,
    AnalysisSnapshotRow,
    ApprovedCastSnapshotRow,
    CastAssignmentInvalidationRow,
    CastAssignmentRow,
    CastingCandidateRow,
    CastingConflictRow,
    CastingCorrectionRow,
    CastingGateDecisionRow,
    CastingGateReviewRow,
    CastingProfileRow,
    CastingRunRow,
    IdempotencyRow,
    JobAttemptRow,
    JobCheckpointRow,
    JobEventRow,
    JobRow,
    ProductionRoleRow,
    VoiceCatalogRevisionRow,
    VoiceModelDescriptorRow,
    VoiceProfileRow,
    VoiceProviderDescriptorRow,
    VoiceRightsRecordRow,
)
from .projects import ProjectRepository
from .story_intelligence import StoryIntelligenceRepository
from .util import canonical_json, new_id, parse_json, request_fingerprint, stable_id, utc_now

_CASTING_JOB_TYPE = "analyze_casting"
_CASTING_TARGET_TYPE = "casting_run"
_CASTING_CHECKPOINT_SCHEMA_VERSION = 1
_ACTIVE_JOB_SCOPE = "active_job"

_ASSIGNMENT_CORRECTION_KINDS = {
    "select_voice",
    "clear_assignment",
    "lock_assignment",
    "unlock_assignment",
    "mark_intentionally_uncast",
}
_REUSABLE_CONFLICT_CATEGORIES = {
    "incompatible_voice_reuse",
    "narrator_major_character_reuse",
    "metadata_similarity_risk",
    "voice_reuse_threshold_exceeded",
}
_SAME_DOMAIN_CORRECTION_KINDS = {
    "change_role_label",
    "change_casting_requirement",
    "acknowledge_restricted_rights",
}

_ASSIGNMENT_CONFLICT_OVERFLOW_CODE = "CASTING_ASSIGNMENT_CONFLICT_EVIDENCE_OVERFLOW"
_STATIC_CONFLICT_OVERFLOW_CODE = "CASTING_STATIC_CONFLICT_EVIDENCE_OVERFLOW"
_ASSIGNMENT_INVALIDATION_ACTOR_ID = "voice-casting-governance@1.0.0"
_ASSIGNMENT_INVALIDATION_WARNING_CODE = "CAST_ASSIGNMENT_EVIDENCE_INVALIDATED"
_WARNING_RELATED_ENTITY_LIMIT = 16
_WARNING_RELATED_ENTITY_OVERFLOW_ID = "casting-related-role-overflow"

_PHASE_2_GATE_KEYS = {
    "story_structure_review": "storyStructureReview",
    "character_registry_review": "characterRegistryReview",
    "dialogue_attribution_review": "dialogueAttributionReview",
    "whole_book_analysis_review": "wholeBookAnalysisReview",
}


def _page_cursor(binding: str, offset: int) -> str:
    raw = canonical_json({"binding": binding, "offset": offset, "version": "v1"})
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _page_offset(binding: str, cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("version") != "v1"
            or value.get("binding") != binding
            or not isinstance(value.get("offset"), int)
            or value["offset"] < 0
        ):
            raise ValueError
        return int(value["offset"])
    except Exception as exc:
        raise ServiceError(
            400,
            "INVALID_CURSOR",
            "The casting cursor is invalid.",
        ) from exc


def _bounded_page(
    values: Sequence[Any],
    *,
    binding: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[Any], str | None, int]:
    if not 1 <= limit <= MAX_CASTING_PAGE_SIZE:
        raise ServiceError(
            422,
            "CASTING_PAGE_LIMIT_INVALID",
            "The casting page size is invalid.",
        )
    offset = _page_offset(binding, cursor)
    total = len(values)
    if offset > total:
        raise ServiceError(400, "INVALID_CURSOR", "The casting cursor is invalid.")
    page = list(values[offset : offset + limit])
    next_offset = offset + len(page)
    next_cursor = _page_cursor(binding, next_offset) if next_offset < total else None
    return page, next_cursor, total


def _custom_role_evidence(
    *,
    project_id: str,
    run_id: str,
    definition_id: str,
    definition_fingerprint: str,
    label: str,
    performance_requirements: dict[str, Any],
    reason: str,
    analysis_run_id: str,
    analysis_snapshot_id: str,
    analysis_snapshot_fingerprint: str,
    casting_run_input_fingerprint: str,
    catalog_fingerprint: str,
    casting_profile_fingerprint: str,
    recorded_at: str,
) -> tuple[dict[str, Any], str]:
    """Build custom-role evidence without making wall-clock time an identity input."""

    role_id = stable_id(
        "phase3a-custom-production-role",
        project_id,
        run_id,
        definition_id,
    )
    warning = {
        "code": "CUSTOM_ROLE_CONTENT_FREE",
        "severity": "warning",
        "message": (
            "This explicit custom role has no implicit manuscript source "
            "material; workload counts and story ranges are intentionally empty."
        ),
        "requiresHumanReview": True,
        "relatedEntityIds": [],
        "evidence": [],
    }
    stable_provenance = {
        "origin": "human",
        "producerId": "local_user",
        "producerVersion": "1.0.0",
        "inputFingerprint": definition_fingerprint,
        "sourceRevisionId": definition_id,
        "reason": reason,
    }
    stable_role_values: dict[str, Any] = {
        "contractVersion": CASTING_CONTRACT_VERSION,
        "roleId": role_id,
        "projectId": project_id,
        "roleType": "custom",
        "analysisEntityId": None,
        "characterId": None,
        "roleImportance": "supporting",
        "unresolvedMaterialExplicitlyRepresented": False,
        "effectiveDisplayLabel": label,
        "analysisRunId": analysis_run_id,
        "analysisSnapshotId": analysis_snapshot_id,
        "analysisSnapshotFingerprint": analysis_snapshot_fingerprint,
        "dialogueLineCount": 0,
        "narrationSpanCount": 0,
        "approximateWordCount": 0,
        "chapterRange": {"firstOrdinal": None, "lastOrdinal": None},
        "sceneRange": {"firstOrdinal": None, "lastOrdinal": None},
        "languageRequirements": [str(performance_requirements["language"])],
        "performanceRequirements": performance_requirements,
        "warnings": [warning],
        "provenance": stable_provenance,
        "status": "active",
        "revision": 1,
    }
    role_fingerprint = request_fingerprint(stable_role_values)
    role_values = {
        **stable_role_values,
        "provenance": {
            **stable_provenance,
            "recordedAt": recorded_at,
        },
        "roleFingerprint": role_fingerprint,
    }
    candidate_input_fingerprint = request_fingerprint(
        {
            "castingRunInputFingerprint": casting_run_input_fingerprint,
            "customRoleDefinitionFingerprint": definition_fingerprint,
            "customRoleFingerprint": role_fingerprint,
            "catalogFingerprint": catalog_fingerprint,
            "castingProfileFingerprint": casting_profile_fingerprint,
        }
    )
    return role_values, candidate_input_fingerprint


def _deterministic_machine_fingerprint(
    value: dict[str, Any],
) -> str:
    fingerprint_values = {
        **value,
        "provenance": {
            key: item
            for key, item in dict(value.get("provenance", {})).items()
            if key != "recordedAt"
        },
    }
    fingerprint_values.pop("outputFingerprint", None)
    return request_fingerprint(fingerprint_values)


def _job_wire(row: JobRow) -> dict[str, Any]:
    result: dict[str, Any] = {
        "jobId": row.id,
        "projectId": row.project_id,
        "type": row.type,
        "state": row.state,
        "target": {"type": row.target_type, "id": row.target_id},
        "inputRevision": row.input_revision,
        "inputFingerprint": row.input_fingerprint,
        "attempt": row.current_attempt,
        "stage": row.stage,
        "progress": row.progress / 1_000_000,
        "checkpointAvailable": row.checkpoint_available,
        "cancellationRequested": row.cancellation_requested,
        "warnings": parse_json(row.warnings_json, []),
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }
    if row.error_code is not None:
        result["error"] = {
            "code": row.error_code,
            "message": row.error_message or "Casting could not be completed.",
            "retryable": bool(row.error_retryable),
        }
    if row.terminal_at is not None:
        result["terminalAt"] = row.terminal_at
    return result


class CastingRepository:
    """Governed Phase 3A persistence and evidence boundary."""

    def __init__(
        self,
        database: Database,
        projects: ProjectRepository,
        story_intelligence: StoryIntelligenceRepository,
    ) -> None:
        self.database = database
        self.projects = projects
        self.story_intelligence = story_intelligence
        self.catalog = load_governed_voice_catalog()
        self._review_refresh_lock = RLock()

    @staticmethod
    def require_run(
        session: Session,
        *,
        project_id: str,
        run_id: str,
    ) -> CastingRunRow:
        run = session.get(CastingRunRow, run_id)
        if run is None or run.project_id != project_id:
            raise not_found("casting run")
        return run

    @staticmethod
    def _latest_snapshot(
        session: Session,
        run_id: str,
    ) -> ApprovedCastSnapshotRow | None:
        return session.scalar(
            select(ApprovedCastSnapshotRow)
            .where(ApprovedCastSnapshotRow.casting_run_id == run_id)
            .order_by(
                ApprovedCastSnapshotRow.revision.desc(),
                ApprovedCastSnapshotRow.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _latest_review(
        session: Session,
        *,
        run_id: str,
        gate_id: str,
    ) -> CastingGateReviewRow | None:
        return session.scalar(
            select(CastingGateReviewRow)
            .where(
                CastingGateReviewRow.casting_run_id == run_id,
                CastingGateReviewRow.gate_id == gate_id,
            )
            .order_by(
                CastingGateReviewRow.revision.desc(),
                CastingGateReviewRow.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _latest_decision(
        session: Session,
        *,
        run_id: str,
        gate_id: str,
    ) -> CastingGateDecisionRow | None:
        return session.scalar(
            select(CastingGateDecisionRow)
            .where(
                CastingGateDecisionRow.casting_run_id == run_id,
                CastingGateDecisionRow.gate_id == gate_id,
                CastingGateDecisionRow.decision.in_(
                    ("approved", "changes_requested", "rejected", "invalidated")
                ),
            )
            .order_by(
                CastingGateDecisionRow.revision.desc(),
                CastingGateDecisionRow.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _latest_assignment(
        session: Session,
        *,
        role_id: str,
        include_proposal: bool = True,
    ) -> CastAssignmentRow | None:
        statement = select(CastAssignmentRow).where(CastAssignmentRow.role_id == role_id)
        if not include_proposal:
            statement = statement.where(
                CastAssignmentRow.authority.in_(("human_selection", "human_locked"))
            )
        return session.scalar(
            statement.order_by(
                CastAssignmentRow.revision.desc(),
                CastAssignmentRow.id.desc(),
            ).limit(1)
        )

    @staticmethod
    def _snapshot_revision(session: Session, snapshot_id: str) -> int:
        snapshot = session.get(AnalysisSnapshotRow, snapshot_id)
        if snapshot is None:
            raise not_found("analysis snapshot")
        execution = session.get(AnalysisExecutionRow, snapshot.execution_id)
        return execution.attempt if execution is not None else 1

    def _phase_2_evidence(
        self,
        session: Session,
        *,
        project_id: str,
        expected_analysis_run_id: str,
        expected_snapshot_id: str,
        expected_snapshot_revision: int,
        expected_snapshot_fingerprint: str,
        expected_correction_set_fingerprint: str,
        expected_import_review_decision_id: str,
        expected_analysis_gate_decision_ids: dict[str, str],
    ) -> tuple[AnalysisRunRow, Any, dict[str, str], str, str]:
        analysis_run = session.get(AnalysisRunRow, expected_analysis_run_id)
        if analysis_run is None or analysis_run.project_id != project_id:
            raise not_found("approved analysis run")
        self.story_intelligence._assert_run_approval_current(
            session,
            run=analysis_run,
            check_correction_set=False,
        )
        _project, _source, _extraction, _story, import_review = (
            self.story_intelligence.current_approved_input(
                session,
                project_id=project_id,
            )
        )
        snapshot = self.story_intelligence._latest_snapshot(session, analysis_run.id)
        current_correction_fingerprint = (
            self.story_intelligence.effective_correction_set_fingerprint(
                session,
                run=analysis_run,
            )
        )
        if snapshot is None:
            raise ServiceError(
                409,
                "CASTING_PHASE_2_SNAPSHOT_REQUIRED",
                "A published Phase 2 snapshot is required before casting.",
            )
        snapshot_revision = self._snapshot_revision(session, snapshot.id)
        if (
            snapshot.id != expected_snapshot_id
            or snapshot_revision != expected_snapshot_revision
            or snapshot.fingerprint != expected_snapshot_fingerprint
            or current_correction_fingerprint != expected_correction_set_fingerprint
            or import_review.decision_id != expected_import_review_decision_id
        ):
            raise ServiceError(
                409,
                "CASTING_PHASE_2_EVIDENCE_STALE",
                "The approved Phase 2 evidence changed; refresh before casting.",
                retryable=False,
            )
        decision_ids: dict[str, str] = {}
        for gate_id, wire_key in _PHASE_2_GATE_KEYS.items():
            gate = self.story_intelligence._latest_gate(
                session,
                run_id=analysis_run.id,
                gate_id=gate_id,
            )
            expected = expected_analysis_gate_decision_ids.get(gate_id)
            if expected is None:
                expected = expected_analysis_gate_decision_ids.get(wire_key)
            if (
                gate is None
                or gate.state != "approved"
                or gate.snapshot_id != snapshot.id
                or gate.id != expected
            ):
                raise ServiceError(
                    409,
                    "CASTING_PHASE_2_APPROVAL_REQUIRED",
                    "Every current Phase 2 review gate must approve the exact snapshot.",
                    retryable=False,
                )
            decision_ids[gate_id] = gate.id
        character_rows = list(
            session.scalars(
                select(AnalysisEntityRow)
                .where(
                    AnalysisEntityRow.run_id == analysis_run.id,
                    AnalysisEntityRow.snapshot_id == snapshot.id,
                    AnalysisEntityRow.collection == "characters",
                )
                .order_by(AnalysisEntityRow.ordinal, AnalysisEntityRow.id)
            )
        )
        character_registry_fingerprint = request_fingerprint(
            {
                "snapshotFingerprint": snapshot.fingerprint,
                "correctionSetFingerprint": current_correction_fingerprint,
                "characters": [
                    {
                        "id": row.id,
                        "revision": row.revision,
                        "fingerprint": row.fingerprint,
                    }
                    for row in character_rows
                ],
            }
        )
        phase_2_gate_evidence_fingerprint = request_fingerprint(
            {
                "analysisRunId": analysis_run.id,
                "snapshotId": snapshot.id,
                "snapshotRevision": snapshot_revision,
                "snapshotFingerprint": snapshot.fingerprint,
                "correctionSetFingerprint": current_correction_fingerprint,
                "characterRegistryFingerprint": character_registry_fingerprint,
                "gateDecisionIds": decision_ids,
            }
        )
        return (
            analysis_run,
            snapshot,
            decision_ids,
            character_registry_fingerprint,
            phase_2_gate_evidence_fingerprint,
        )

    def _ensure_catalog_rows(
        self,
        session: Session,
    ) -> tuple[VoiceCatalogRevisionRow, CastingProfileRow]:
        catalog_row = session.scalar(
            select(VoiceCatalogRevisionRow).where(
                VoiceCatalogRevisionRow.catalog_fingerprint == self.catalog.fingerprint
            )
        )
        now = utc_now()
        if catalog_row is None:
            revision = self.catalog.revision
            catalog_row = VoiceCatalogRevisionRow(
                id=stable_id("voice-catalog-revision", self.catalog.fingerprint),
                catalog_id=self.catalog.revision_id,
                revision=int(revision["revision"]),
                semantic_version=str(revision["semanticVersion"]),
                catalog_fingerprint=self.catalog.fingerprint,
                provider_set_fingerprint=request_fingerprint(list(self.catalog.providers)),
                rights_policy_version=RIGHTS_POLICY_VERSION,
                source_kind=(
                    "local_static"
                    if revision.get("provenance", {}).get("origin") == "local_catalog"
                    else "development_fixture"
                ),
                active=True,
                provenance_json=canonical_json(revision["provenance"]),
                created_at=str(revision["createdAt"]),
            )
            session.add(catalog_row)
            session.flush()
            providers: dict[str, VoiceProviderDescriptorRow] = {}
            for provider in self.catalog.providers:
                provider_id = str(provider["providerId"])
                output = provider.get("outputCapability", {})
                provider_row = VoiceProviderDescriptorRow(
                    id=stable_id(
                        "voice-provider",
                        self.catalog.fingerprint,
                        provider_id,
                    ),
                    catalog_revision_id=catalog_row.id,
                    provider_id=provider_id,
                    provider_version=str(provider["providerVersion"]),
                    provider_type=str(provider["providerType"]),
                    runtime_availability=str(provider["runtimeAvailability"]),
                    catalog_availability=str(provider["catalogAvailability"]),
                    synthesis_implemented=bool(provider["synthesisImplemented"]),
                    network_required=bool(provider["networkUseRequired"]),
                    credentials_required=bool(provider["credentialsRequired"]),
                    supported_operating_systems_json=canonical_json(
                        provider["supportedOperatingSystems"]
                    ),
                    supported_languages_json=canonical_json(provider["supportedLanguages"]),
                    output_capabilities_json=canonical_json(output),
                    rights_metadata_capabilities_json=canonical_json(
                        provider["rightsMetadataCapabilities"]
                    ),
                    health_status=str(provider["healthStatus"]),
                    descriptor_fingerprint=request_fingerprint(provider),
                    provenance_json=canonical_json(provider["provenance"]),
                    created_at=str(provider["provenance"]["recordedAt"]),
                )
                providers[provider_id] = provider_row
                session.add(provider_row)
            session.flush()
            models: dict[tuple[str, str], VoiceModelDescriptorRow] = {}
            for model in self.catalog.models:
                provider_id = str(model["providerId"])
                model_id = str(model["modelId"])
                capability = dict(model["capability"])
                model_row = VoiceModelDescriptorRow(
                    id=stable_id(
                        "voice-model",
                        self.catalog.fingerprint,
                        provider_id,
                        model_id,
                    ),
                    catalog_revision_id=catalog_row.id,
                    provider_descriptor_id=providers[provider_id].id,
                    model_id=model_id,
                    model_name=str(model["modelName"]),
                    model_version=str(model["modelVersion"]),
                    supported_languages_json=canonical_json(capability["supportedLanguages"]),
                    supported_locales_json=canonical_json(capability["supportedLocales"]),
                    expressive_controls_json=canonical_json(capability["expressiveControls"]),
                    speaking_rate_controls_json=canonical_json(capability["speakingRateRange"]),
                    pitch_style_controls_json=canonical_json(
                        {
                            "pitchControl": capability["pitchControl"],
                            "styleControl": capability["styleControl"],
                        }
                    ),
                    output_capabilities_json=canonical_json(capability["outputCapability"]),
                    execution_classification=(
                        "fixture" if model["executionLocation"] == "local" else "remote_disabled"
                    ),
                    rights_classification=str(model["licenseClassification"]),
                    availability=str(model["availability"]),
                    deprecated=bool(model["deprecated"]),
                    descriptor_fingerprint=request_fingerprint(model),
                    provenance_json=canonical_json(model["provenance"]),
                    created_at=str(model["provenance"]["recordedAt"]),
                )
                models[(provider_id, model_id)] = model_row
                session.add(model_row)
            session.flush()
            voices: dict[str, VoiceProfileRow] = {}
            for voice in self.catalog.voices:
                voice_id = str(voice["voiceProfileId"])
                provider_id = str(voice["providerId"])
                model_id = str(voice["modelId"])
                profile_voice_row = VoiceProfileRow(
                    id=stable_id(
                        "voice-profile-record",
                        self.catalog.fingerprint,
                        voice_id,
                    ),
                    profile_id=voice_id,
                    revision=1,
                    profile_version=str(voice["version"]),
                    catalog_revision_id=catalog_row.id,
                    provider_descriptor_id=providers[provider_id].id,
                    model_descriptor_id=models[(provider_id, model_id)].id,
                    provider_voice_id=str(voice["providerVoiceId"]),
                    display_label=str(voice["displayLabel"]),
                    language=str(voice["language"]),
                    locale=str(voice["locale"]),
                    declared_accent_dialect=str(voice["accentOrDialect"]),
                    declared_age_presentation_json=canonical_json(voice["agePresentationRange"]),
                    declared_vocal_presentation=str(voice["vocalPresentation"]),
                    vocal_weight_texture_json=canonical_json(
                        {
                            "texture": voice["vocalTexture"],
                            "metadataSimilarityGroup": voice["metadataSimilarityGroup"],
                            "reuseRiskGroup": voice["reuseRiskGroup"],
                        }
                    ),
                    pitch_range_classification=str(voice["pitchRange"]),
                    speaking_rate_range_json=canonical_json(voice["speakingRateRange"]),
                    energy_range_json=canonical_json(voice["energyRange"]),
                    expressive_range_json=canonical_json(voice["expressiveRange"]),
                    narration_suitability=str(voice["narrationSuitability"]),
                    dialogue_suitability=str(voice["dialogueSuitability"]),
                    long_form_suitability=str(voice["longFormSuitability"]),
                    character_role_suitability_json=canonical_json(
                        {
                            "roles": voice["characterRoleSuitability"],
                            "maximumRecommendedWords": voice["maximumRecommendedWords"],
                        }
                    ),
                    known_limitations_json=canonical_json(voice["knownLimitations"]),
                    rights_state=str(voice["rightsState"]),
                    consent_status=str(voice["consentStatus"]),
                    license_scope=str(voice["licenseScope"]),
                    commercial_use_status=str(voice["commercialUse"]),
                    attribution_required=bool(voice["attributionRequired"]),
                    voice_cloning_classification=str(voice["voiceCloningClassification"]),
                    state=str(voice["state"]),
                    profile_fingerprint=request_fingerprint(voice),
                    provenance_json=canonical_json(voice["provenance"]),
                    created_at=str(voice["provenance"]["recordedAt"]),
                )
                voices[voice_id] = profile_voice_row
                session.add(profile_voice_row)
            session.flush()
            for rights in self.catalog.rights:
                voice_id = str(rights["voiceProfileId"])
                provider_id = str(rights["providerId"])
                rights_row = VoiceRightsRecordRow(
                    id=stable_id(
                        "voice-rights-record",
                        self.catalog.fingerprint,
                        str(rights["rightsRecordId"]),
                    ),
                    rights_record_id=str(rights["rightsRecordId"]),
                    voice_profile_record_id=voices[voice_id].id,
                    provider_descriptor_id=providers[provider_id].id,
                    revision=int(rights["revision"]),
                    rights_state=str(rights["state"]),
                    license_identifier=str(rights["licenseIdentifier"]),
                    rights_basis=str(rights["rightsBasis"]),
                    license_scope=str(
                        next(
                            voice["licenseScope"]
                            for voice in self.catalog.voices
                            if voice["voiceProfileId"] == voice_id
                        )
                    ),
                    commercial_use_status=str(rights["commercialUsePermission"]),
                    attribution_required=(rights["attributionRequirement"] == "required"),
                    distribution_limitations_json=canonical_json(
                        {
                            "geographic": rights["geographicLimitations"],
                            "distribution": rights["distributionLimitations"],
                        }
                    ),
                    voice_cloning_status=str(rights["voiceCloningStatus"]),
                    consent_status=str(rights["consentStatus"]),
                    effective_date=rights["effectiveDate"],
                    expiration_date=rights["expiresAt"],
                    evidence_reference=str(rights["evidenceReference"]),
                    human_verification_status=str(rights["humanVerificationStatus"]),
                    rights_fingerprint=request_fingerprint(rights),
                    provenance_json=canonical_json(rights["provenance"]),
                    created_at=str(rights["provenance"]["recordedAt"]),
                )
                session.add(rights_row)

        profile_row = session.scalar(
            select(CastingProfileRow).where(
                CastingProfileRow.profile_fingerprint == CASTING_PROFILE_FINGERPRINT
            )
        )
        if profile_row is None:
            profile = casting_profile()
            profile_row = CastingProfileRow(
                id=stable_id("casting-profile", CASTING_PROFILE_FINGERPRINT),
                profile_id=CASTING_PROFILE_ID,
                semantic_version=CASTING_PROFILE_VERSION,
                producer_id=CASTING_PRODUCER_ID,
                producer_version=CASTING_PRODUCER_VERSION,
                compatibility_rules_json=canonical_json(
                    CASTING_PROFILE_VALUES["compatibilityRules"]
                ),
                hard_constraints_json=canonical_json(CASTING_PROFILE_VALUES["hardConstraints"]),
                soft_preferences_json=canonical_json(CASTING_PROFILE_VALUES["softPreferences"]),
                conflict_rules_json=canonical_json(CASTING_PROFILE_VALUES["conflictRules"]),
                rights_eligibility_rules_json=canonical_json(
                    CASTING_PROFILE_VALUES["rightsEligibilityRules"]
                ),
                pre_reduction_candidate_limit=50,
                candidate_limit=12,
                explanation_requirements_json=canonical_json(
                    {
                        "required": True,
                        "maximumCodePoints": 2_000,
                        "noManuscriptText": True,
                    }
                ),
                profile_fingerprint=str(profile["fingerprint"]),
                provenance_json=canonical_json(
                    {
                        "origin": "system",
                        "producerId": CASTING_PRODUCER_ID,
                        "producerVersion": CASTING_PRODUCER_VERSION,
                        "recordedAt": now,
                    }
                ),
                created_at=now,
            )
            session.add(profile_row)
            session.flush()
        return catalog_row, profile_row

    def catalog_page(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int = DEFAULT_CASTING_PAGE_SIZE,
        expected_revision_id: str | None = None,
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            self.projects.require_project(session, project_id)
            self._ensure_catalog_rows(session)
        if (
            expected_revision_id is not None and expected_revision_id != self.catalog.revision_id
        ) or (
            expected_fingerprint is not None and expected_fingerprint != self.catalog.fingerprint
        ):
            raise ServiceError(
                409,
                "VOICE_CATALOG_EVIDENCE_STALE",
                "The voice catalog changed; refresh before continuing.",
            )
        page, next_cursor, total = _bounded_page(
            self.catalog.voices,
            binding=request_fingerprint(
                {
                    "type": "voice-catalog",
                    "revision": self.catalog.revision_id,
                    "fingerprint": self.catalog.fingerprint,
                }
            ),
            cursor=cursor,
            limit=limit,
        )
        rights_by_voice = {str(value["voiceProfileId"]): value for value in self.catalog.rights}
        result: dict[str, Any] = {
            "catalogRevision": self.catalog.revision,
            "providers": list(self.catalog.providers),
            "models": list(self.catalog.models),
            "items": list(page),
            "rights": [rights_by_voice[str(value["voiceProfileId"])] for value in page],
            "total": total,
            "pageSize": len(page),
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    def create_run(
        self,
        *,
        project_id: str,
        expected_analysis_run_id: str,
        expected_snapshot_id: str,
        expected_snapshot_revision: int,
        expected_snapshot_fingerprint: str,
        expected_correction_set_fingerprint: str,
        expected_import_review_decision_id: str,
        expected_analysis_gate_decision_ids: dict[str, str],
        expected_catalog_revision_id: str,
        expected_catalog_fingerprint: str,
        expected_casting_profile_fingerprint: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if (
            expected_catalog_revision_id != self.catalog.revision_id
            or expected_catalog_fingerprint != self.catalog.fingerprint
            or expected_casting_profile_fingerprint != CASTING_PROFILE_FINGERPRINT
        ):
            raise ServiceError(
                409,
                "CASTING_CONFIGURATION_STALE",
                "The governed catalog or casting profile changed; refresh first.",
            )
        scope = f"create_casting_run:{project_id}"
        request_hash = request_fingerprint(
            {
                "projectId": project_id,
                "analysisRunId": expected_analysis_run_id,
                "snapshotId": expected_snapshot_id,
                "snapshotRevision": expected_snapshot_revision,
                "snapshotFingerprint": expected_snapshot_fingerprint,
                "correctionSetFingerprint": expected_correction_set_fingerprint,
                "importReviewDecisionId": expected_import_review_decision_id,
                "phase2GateDecisionIds": expected_analysis_gate_decision_ids,
                "catalogRevisionId": expected_catalog_revision_id,
                "catalogFingerprint": expected_catalog_fingerprint,
                "castingProfileFingerprint": (expected_casting_profile_fingerprint),
            }
        )
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = session.get(
                IdempotencyRow,
                {"scope": scope, "key": idempotency_key},
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was used for another casting run.",
                    )
                run = session.get(CastingRunRow, existing.resource_id)
                if run is None:
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved casting run is unavailable.",
                    )
                job = session.get(JobRow, run.job_id)
                if job is None:
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved casting job is unavailable.",
                    )
                return self.run_dict(session, run), _job_wire(job)
            (
                analysis_run,
                snapshot,
                decision_ids,
                character_registry_fingerprint,
                phase_2_gate_evidence_fingerprint,
            ) = self._phase_2_evidence(
                session,
                project_id=project_id,
                expected_analysis_run_id=expected_analysis_run_id,
                expected_snapshot_id=expected_snapshot_id,
                expected_snapshot_revision=expected_snapshot_revision,
                expected_snapshot_fingerprint=expected_snapshot_fingerprint,
                expected_correction_set_fingerprint=(expected_correction_set_fingerprint),
                expected_import_review_decision_id=(expected_import_review_decision_id),
                expected_analysis_gate_decision_ids=(expected_analysis_gate_decision_ids),
            )
            _project, source, extraction, story, import_review = (
                self.story_intelligence.current_approved_input(
                    session,
                    project_id=project_id,
                )
            )
            catalog_row, profile_row = self._ensure_catalog_rows(session)
            input_fingerprint = request_fingerprint(
                {
                    "projectId": project_id,
                    "sourceDocumentId": source.id,
                    "sourceRevision": source.source_revision,
                    "extractionId": extraction.id,
                    "extractionRevision": extraction.revision,
                    "extractedTextSha256": extraction.text_sha256,
                    "importReviewDecisionId": import_review.decision_id,
                    "analysisRunId": analysis_run.id,
                    "analysisSnapshotId": snapshot.id,
                    "analysisSnapshotRevision": expected_snapshot_revision,
                    "analysisSnapshotFingerprint": snapshot.fingerprint,
                    "analysisCorrectionSetFingerprint": (expected_correction_set_fingerprint),
                    "characterRegistryFingerprint": (character_registry_fingerprint),
                    "phase2GateDecisionIds": decision_ids,
                    "catalogFingerprint": self.catalog.fingerprint,
                    "castingProfileFingerprint": CASTING_PROFILE_FINGERPRINT,
                }
            )
            reusable_run = session.scalar(
                select(CastingRunRow)
                .where(
                    CastingRunRow.project_id == project_id,
                    CastingRunRow.input_fingerprint == input_fingerprint,
                    CastingRunRow.catalog_fingerprint == self.catalog.fingerprint,
                    CastingRunRow.casting_profile_fingerprint == CASTING_PROFILE_FINGERPRINT,
                    CastingRunRow.state.in_(("pending", "running", "succeeded")),
                )
                .order_by(
                    CastingRunRow.created_at.desc(),
                    CastingRunRow.id.desc(),
                )
                .limit(1)
            )
            if reusable_run is not None:
                reusable_job = session.get(JobRow, reusable_run.job_id)
                if reusable_job is None:
                    raise ServiceError(
                        500,
                        "CASTING_JOB_UNAVAILABLE",
                        "The reusable casting job is unavailable.",
                    )
                session.add(
                    IdempotencyRow(
                        scope=scope,
                        key=idempotency_key,
                        request_hash=request_hash,
                        resource_id=reusable_run.id,
                        created_at=utc_now(),
                    )
                )
                session.flush()
                return self.run_dict(session, reusable_run), _job_wire(reusable_job)

            run_id = new_id()
            job_id = new_id()
            now = utc_now()
            run_fingerprint = request_fingerprint(
                {
                    "runId": run_id,
                    "inputFingerprint": input_fingerprint,
                    "producerId": CASTING_PRODUCER_ID,
                }
            )
            job = JobRow(
                id=job_id,
                project_id=project_id,
                type=_CASTING_JOB_TYPE,
                state="queued",
                input_revision=expected_snapshot_revision,
                input_fingerprint=input_fingerprint,
                target_type=_CASTING_TARGET_TYPE,
                target_id=run_id,
                payload_json=canonical_json(
                    {
                        "schemaVersion": _CASTING_CHECKPOINT_SCHEMA_VERSION,
                        "castingRunId": run_id,
                        "analysisRunId": analysis_run.id,
                        "analysisSnapshotId": snapshot.id,
                        "catalogRevisionId": self.catalog.revision_id,
                        "catalogFingerprint": self.catalog.fingerprint,
                        "castingProfileFingerprint": (CASTING_PROFILE_FINGERPRINT),
                    }
                ),
                current_attempt=1,
                stage="queued",
                progress=0,
                checkpoint_available=False,
                cancellation_requested=False,
                resume_requested=False,
                warnings_json="[]",
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.flush()
            run = CastingRunRow(
                id=run_id,
                project_id=project_id,
                source_document_id=source.id,
                source_revision=source.source_revision,
                extraction_id=extraction.id,
                extraction_revision=extraction.revision,
                extracted_text_sha256=extraction.text_sha256,
                import_review_decision_id=str(import_review.decision_id),
                analysis_run_id=analysis_run.id,
                analysis_snapshot_id=snapshot.id,
                analysis_snapshot_revision=expected_snapshot_revision,
                analysis_snapshot_fingerprint=snapshot.fingerprint,
                analysis_correction_set_fingerprint=(expected_correction_set_fingerprint),
                character_registry_fingerprint=character_registry_fingerprint,
                phase2_gate_decision_ids_json=canonical_json(decision_ids),
                phase2_gate_evidence_fingerprint=(phase_2_gate_evidence_fingerprint),
                casting_profile_id=profile_row.id,
                casting_profile_fingerprint=CASTING_PROFILE_FINGERPRINT,
                catalog_revision_id=catalog_row.id,
                catalog_fingerprint=self.catalog.fingerprint,
                effective_correction_set_fingerprint=request_fingerprint([]),
                producer_id=CASTING_PRODUCER_ID,
                producer_version=CASTING_PRODUCER_VERSION,
                input_fingerprint=input_fingerprint,
                run_fingerprint=run_fingerprint,
                job_id=job.id,
                state="pending",
                warnings_json="[]",
                created_at=now,
                published_at=None,
            )
            session.add(run)
            active_key = request_fingerprint(
                {
                    "projectId": project_id,
                    "type": _CASTING_JOB_TYPE,
                    "targetType": _CASTING_TARGET_TYPE,
                    "targetId": run_id,
                    "inputRevision": expected_snapshot_revision,
                    "inputFingerprint": input_fingerprint,
                }
            )
            session.add(
                IdempotencyRow(
                    scope=_ACTIVE_JOB_SCOPE,
                    key=active_key,
                    request_hash=active_key,
                    resource_id=job.id,
                    created_at=now,
                )
            )
            session.add(
                JobAttemptRow(
                    job_id=job.id,
                    number=1,
                    producer_version=CASTING_PRODUCER_ID,
                )
            )
            session.add(
                JobEventRow(
                    job_id=job.id,
                    sequence=1,
                    attempt=1,
                    type="created",
                    state="queued",
                    stage="queued",
                    progress=0,
                    created_at=now,
                )
            )
            session.add(
                IdempotencyRow(
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=run.id,
                    created_at=now,
                )
            )
            session.flush()
            return self.run_dict(session, run), _job_wire(job)

    def load_run_input(
        self,
        *,
        run_id: str,
        job_id: str,
    ) -> tuple[CastingRunRow, list[dict[str, Any]]]:
        with self.database.session() as session:
            run = session.get(CastingRunRow, run_id)
            job = session.get(JobRow, job_id)
            if (
                run is None
                or job is None
                or run.job_id != job.id
                or job.target_id != run.id
                or job.type != _CASTING_JOB_TYPE
            ):
                raise ServiceError(
                    409,
                    "CASTING_RUN_INPUT_INVALID",
                    "The frozen casting run input is unavailable.",
                )
            expected_ids = parse_json(run.phase2_gate_decision_ids_json, {})
            analysis_run, _snapshot, _gate_ids, _registry_fingerprint, _gate_fingerprint = (
                self._phase_2_evidence(
                    session,
                    project_id=run.project_id,
                    expected_analysis_run_id=run.analysis_run_id,
                    expected_snapshot_id=run.analysis_snapshot_id,
                    expected_snapshot_revision=run.analysis_snapshot_revision,
                    expected_snapshot_fingerprint=run.analysis_snapshot_fingerprint,
                    expected_correction_set_fingerprint=(run.analysis_correction_set_fingerprint),
                    expected_import_review_decision_id=(run.import_review_decision_id),
                    expected_analysis_gate_decision_ids=expected_ids,
                )
            )
            story = self.story_intelligence._assert_run_approval_current(
                session,
                run=analysis_run,
                check_correction_set=False,
            )
            rows = list(
                session.scalars(
                    select(AnalysisEntityRow)
                    .where(
                        AnalysisEntityRow.run_id == run.analysis_run_id,
                        AnalysisEntityRow.snapshot_id == run.analysis_snapshot_id,
                    )
                    .order_by(
                        AnalysisEntityRow.collection,
                        AnalysisEntityRow.ordinal,
                        AnalysisEntityRow.id,
                    )
                )
            )
            rows_by_collection: dict[str, list[AnalysisEntityRow]] = {}
            for row in rows:
                rows_by_collection.setdefault(row.collection, []).append(row)
            entities: list[dict[str, Any]] = []
            for collection, collection_rows in sorted(rows_by_collection.items()):
                projected = self.story_intelligence._effective_collection_projection(
                    session,
                    run=analysis_run,
                    story=story,
                    collection=collection,
                    rows=collection_rows,
                )
                for item in projected:
                    payload = self.story_intelligence._wire_payload_from_entity_result(
                        collection,
                        item,
                    )
                    payload.pop("exactText", None)
                    entities.append(
                        {
                            "id": item["entityId"],
                            "collection": collection,
                            "ordinal": item["ordinal"],
                            "revision": item["effectiveRevision"],
                            "payload": payload,
                            "fingerprint": item["effectiveValueFingerprint"],
                            "warnings": item["warnings"],
                        }
                    )
            session.expunge(run)
            return run, entities

    def _voice_rows(
        self,
        session: Session,
        catalog_revision_id: str,
    ) -> dict[str, VoiceProfileRow]:
        return {
            row.profile_id: row
            for row in session.scalars(
                select(VoiceProfileRow)
                .where(VoiceProfileRow.catalog_revision_id == catalog_revision_id)
                .order_by(VoiceProfileRow.profile_id)
            )
        }

    @staticmethod
    def _catalog_identity(
        session: Session,
        catalog_revision_id: str,
    ) -> VoiceCatalogRevisionRow:
        catalog = session.get(VoiceCatalogRevisionRow, catalog_revision_id)
        if catalog is None:
            raise ServiceError(
                500,
                "VOICE_CATALOG_EVIDENCE_UNAVAILABLE",
                "The persisted voice-catalog evidence is unavailable.",
            )
        return catalog

    @staticmethod
    def _catalog_voice_count(
        session: Session,
        catalog_revision_id: str,
    ) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(VoiceProfileRow)
                .where(VoiceProfileRow.catalog_revision_id == catalog_revision_id)
            )
            or 0
        )

    @staticmethod
    def _stored_provider_material(row: VoiceProviderDescriptorRow) -> dict[str, Any]:
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "providerId": row.provider_id,
            "providerVersion": row.provider_version,
            "providerType": row.provider_type,
            "runtimeAvailability": row.runtime_availability,
            "catalogAvailability": row.catalog_availability,
            "synthesisImplemented": row.synthesis_implemented,
            "networkUseRequired": row.network_required,
            "credentialsRequired": row.credentials_required,
            "supportedOperatingSystems": parse_json(
                row.supported_operating_systems_json,
                None,
            ),
            "supportedLanguages": parse_json(row.supported_languages_json, None),
            "outputCapability": parse_json(row.output_capabilities_json, None),
            "rightsMetadataCapabilities": parse_json(
                row.rights_metadata_capabilities_json,
                None,
            ),
            "healthStatus": row.health_status,
            "provenance": parse_json(row.provenance_json, None),
        }

    @staticmethod
    def _stored_model_material(
        row: VoiceModelDescriptorRow,
        provider: VoiceProviderDescriptorRow,
    ) -> dict[str, Any]:
        pitch_style = parse_json(row.pitch_style_controls_json, {})
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "providerId": provider.provider_id,
            "modelId": row.model_id,
            "modelName": row.model_name,
            "modelVersion": row.model_version,
            "capability": {
                "supportedLanguages": parse_json(row.supported_languages_json, None),
                "supportedLocales": parse_json(row.supported_locales_json, None),
                "expressiveControls": parse_json(row.expressive_controls_json, None),
                "speakingRateRange": parse_json(row.speaking_rate_controls_json, None),
                "pitchControl": pitch_style.get("pitchControl"),
                "styleControl": pitch_style.get("styleControl"),
                "outputCapability": parse_json(row.output_capabilities_json, None),
            },
            "executionLocation": (
                "local" if row.execution_classification == "fixture" else "remote"
            ),
            "licenseClassification": row.rights_classification,
            "availability": row.availability,
            "deprecated": row.deprecated,
            "provenance": parse_json(row.provenance_json, None),
        }

    @staticmethod
    def _stored_voice_material(voice: VoiceProfileRow) -> dict[str, Any]:
        vocal_metadata = parse_json(voice.vocal_weight_texture_json, {})
        role_suitability = parse_json(voice.character_role_suitability_json, {})
        return {
            "providerVoiceId": voice.provider_voice_id,
            "displayLabel": voice.display_label,
            "language": voice.language,
            "locale": voice.locale,
            "accentOrDialect": voice.declared_accent_dialect,
            "agePresentationRange": parse_json(
                voice.declared_age_presentation_json,
                {},
            ),
            "vocalPresentation": voice.declared_vocal_presentation,
            "vocalTexture": vocal_metadata.get("texture"),
            "pitchRange": voice.pitch_range_classification,
            "speakingRateRange": parse_json(voice.speaking_rate_range_json, {}),
            "energyRange": parse_json(voice.energy_range_json, {}),
            "expressiveRange": parse_json(voice.expressive_range_json, []),
            "narrationSuitability": voice.narration_suitability,
            "dialogueSuitability": voice.dialogue_suitability,
            "longFormSuitability": voice.long_form_suitability,
            "characterRoleSuitability": role_suitability.get("roles", []),
            "maximumRecommendedWords": role_suitability.get("maximumRecommendedWords"),
            "knownLimitations": parse_json(voice.known_limitations_json, []),
            "rightsState": voice.rights_state,
            "licenseScope": voice.license_scope,
            "commercialUse": voice.commercial_use_status,
            "attributionRequired": voice.attribution_required,
            "voiceCloningClassification": voice.voice_cloning_classification,
            "consentStatus": voice.consent_status,
            "metadataSimilarityGroup": vocal_metadata.get("metadataSimilarityGroup"),
            "reuseRiskGroup": vocal_metadata.get("reuseRiskGroup"),
            "version": voice.profile_version,
            "state": voice.state,
        }

    @staticmethod
    def _catalog_voice_material(voice: dict[str, Any]) -> dict[str, Any]:
        return {
            key: voice.get(key)
            for key in (
                "providerVoiceId",
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
            )
        }

    @staticmethod
    def _stored_rights_material(rights: VoiceRightsRecordRow) -> dict[str, Any]:
        limitations = parse_json(rights.distribution_limitations_json, {})
        return {
            "state": rights.rights_state,
            "licenseIdentifier": rights.license_identifier,
            "rightsBasis": rights.rights_basis,
            "commercialUsePermission": rights.commercial_use_status,
            "attributionRequirement": ("required" if rights.attribution_required else "none"),
            "geographicLimitations": limitations.get("geographic", []),
            "distributionLimitations": limitations.get("distribution", []),
            "voiceCloningStatus": rights.voice_cloning_status,
            "consentStatus": rights.consent_status,
            "effectiveDate": rights.effective_date,
            "expiresAt": rights.expiration_date,
            "evidenceReference": rights.evidence_reference,
            "humanVerificationStatus": rights.human_verification_status,
        }

    @staticmethod
    def _catalog_rights_material(rights: dict[str, Any]) -> dict[str, Any]:
        return {
            key: rights.get(key)
            for key in (
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
            )
        }

    @staticmethod
    def _assignment_invalidation(
        session: Session,
        assignment_id: str,
    ) -> CastAssignmentInvalidationRow | None:
        return session.scalar(
            select(CastAssignmentInvalidationRow)
            .where(CastAssignmentInvalidationRow.assignment_id == assignment_id)
            .limit(1)
        )

    def _assignment_drift_evidence(
        self,
        session: Session,
        *,
        assignment: CastAssignmentRow,
    ) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None]:
        """Compare only the selected voice's current governed catalog leaves."""

        if assignment.voice_profile_record_id is None:
            return [], None, None
        voice = session.get(VoiceProfileRow, assignment.voice_profile_record_id)
        if voice is None:
            return ["selected_voice_missing"], None, None
        current_voice = next(
            (
                value
                for value in self.catalog.voices
                if value.get("voiceProfileId") == voice.profile_id
            ),
            None,
        )
        current_rights = next(
            (
                value
                for value in self.catalog.rights
                if value.get("voiceProfileId") == voice.profile_id
            ),
            None,
        )
        reasons: list[str] = []
        if current_voice is None:
            reasons.append("selected_voice_missing")
            return reasons, None, current_rights
        if request_fingerprint(self._stored_voice_material(voice)) != request_fingerprint(
            self._catalog_voice_material(current_voice)
        ):
            reasons.append("selected_voice_changed")

        stored_rights = session.scalar(
            select(VoiceRightsRecordRow)
            .where(VoiceRightsRecordRow.voice_profile_record_id == voice.id)
            .order_by(
                VoiceRightsRecordRow.revision.desc(),
                VoiceRightsRecordRow.id.desc(),
            )
            .limit(1)
        )
        if current_rights is None:
            reasons.append("selected_rights_missing")
        elif stored_rights is None or stored_rights.rights_fingerprint != request_fingerprint(
            current_rights
        ):
            reasons.append("selected_rights_changed")
        if current_rights is not None and not rights_record_is_current(current_rights):
            reasons.append("selected_rights_not_current")

        stored_provider = session.get(
            VoiceProviderDescriptorRow,
            voice.provider_descriptor_id,
        )
        current_provider = next(
            (
                value
                for value in self.catalog.providers
                if value.get("providerId") == current_voice.get("providerId")
            ),
            None,
        )
        if current_provider is None:
            reasons.append("selected_provider_missing")
        elif (
            stored_provider is None
            or stored_provider.descriptor_fingerprint != request_fingerprint(current_provider)
        ):
            reasons.append("selected_provider_changed")

        stored_model = session.get(
            VoiceModelDescriptorRow,
            voice.model_descriptor_id,
        )
        current_model = next(
            (
                value
                for value in self.catalog.models
                if value.get("providerId") == current_voice.get("providerId")
                and value.get("modelId") == current_voice.get("modelId")
            ),
            None,
        )
        if current_model is None:
            reasons.append("selected_model_missing")
        elif stored_model is None or stored_model.descriptor_fingerprint != request_fingerprint(
            current_model
        ):
            reasons.append("selected_model_changed")

        if (
            reasons
            and current_rights is not None
            and current_provider is not None
            and current_model is not None
        ):
            role = session.get(ProductionRoleRow, assignment.role_id)
            if role is None:
                reasons.append("selected_role_missing")
            else:
                current_assessment = compatibility_assessment(
                    role=self._role_wire(session, role),
                    voice=current_voice,
                    rights=current_rights,
                    provider=current_provider,
                    model=current_model,
                    input_fingerprint=self._effective_role_fingerprint(
                        session,
                        role,
                    ),
                )
                if current_assessment["compatibilityStatus"] in {
                    "ineligible",
                    "unknown",
                }:
                    reasons.append("selected_voice_no_longer_eligible")
        return sorted(set(reasons)), current_voice, current_rights

    def _external_assignment_drift_candidate_ids(
        self,
        session: Session,
        *,
        run: CastingRunRow,
    ) -> tuple[str, ...]:
        """Find unlatched selected assignments without taking a write lock."""

        candidates: list[str] = []
        for assignment in self._effective_assignment_rows(session, run.id):
            if (
                assignment.assignment_state not in {"selected", "locked"}
                or assignment.voice_profile_record_id is None
                or self._assignment_invalidation(session, assignment.id) is not None
            ):
                continue
            reasons, _current_voice, _current_rights = self._assignment_drift_evidence(
                session,
                assignment=assignment,
            )
            if reasons:
                candidates.append(assignment.id)
        return tuple(candidates)

    def _sync_external_assignment_invalidations(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        candidate_assignment_ids: frozenset[str] | None = None,
    ) -> None:
        """Latch selected-evidence drift and append system invalidation decisions."""

        now = utc_now()
        new_rows: list[CastAssignmentInvalidationRow] = []
        affected_gates: set[str] = set()
        narrator_types = {"primary_narrator", "secondary_narrator"}
        assignments = [
            value
            for value in self._effective_assignment_rows(session, run.id)
            if value.assignment_state in {"selected", "locked"}
            and value.voice_profile_record_id is not None
        ]
        for assignment in assignments:
            if (
                candidate_assignment_ids is not None
                and assignment.id not in candidate_assignment_ids
            ):
                continue
            if self._assignment_invalidation(session, assignment.id) is not None:
                continue
            reasons, current_voice, current_rights = self._assignment_drift_evidence(
                session,
                assignment=assignment,
            )
            if not reasons:
                continue
            evidence_material = {
                "castingRunId": run.id,
                "assignmentId": assignment.id,
                "roleId": assignment.role_id,
                "pinnedCatalogFingerprint": run.catalog_fingerprint,
                "currentCatalogFingerprint": self.catalog.fingerprint,
                "reasonCodes": reasons,
                "currentVoiceFingerprint": (
                    request_fingerprint(current_voice) if current_voice is not None else None
                ),
                "currentRightsFingerprint": (
                    request_fingerprint(current_rights) if current_rights is not None else None
                ),
            }
            evidence_fingerprint = request_fingerprint(evidence_material)
            invalidation = CastAssignmentInvalidationRow(
                id=stable_id(
                    "cast-assignment-invalidation",
                    assignment.id,
                    evidence_fingerprint,
                ),
                project_id=run.project_id,
                casting_run_id=run.id,
                role_id=assignment.role_id,
                assignment_id=assignment.id,
                reason_codes_json=canonical_json(reasons),
                evidence_fingerprint=evidence_fingerprint,
                provenance_json=canonical_json(
                    {
                        "origin": "system",
                        "producerId": _ASSIGNMENT_INVALIDATION_ACTOR_ID,
                        "producerVersion": "1.0.0",
                        "recordedAt": now,
                        "inputFingerprint": evidence_fingerprint,
                    }
                ),
                created_at=now,
            )
            session.add(invalidation)
            new_rows.append(invalidation)
            role = session.get(ProductionRoleRow, assignment.role_id)
            if role is not None:
                affected_gates.add(
                    "narrator_casting_review"
                    if role.role_type in narrator_types
                    else "character_casting_review"
                )
        if not new_rows:
            return
        session.flush()
        affected_gates.add("complete_cast_review")
        for gate_id in CASTING_GATE_IDS:
            if gate_id not in affected_gates:
                continue
            latest = self._latest_decision(
                session,
                run_id=run.id,
                gate_id=gate_id,
            )
            review = self._latest_review(
                session,
                run_id=run.id,
                gate_id=gate_id,
            )
            if (
                latest is None
                or latest.decision != "approved"
                or review is None
                or latest.cast_snapshot_id != review.cast_snapshot_id
            ):
                continue
            relevant_rows: list[CastAssignmentInvalidationRow] = []
            for value in new_rows:
                if gate_id == "complete_cast_review":
                    relevant_rows.append(value)
                    continue
                role = session.get(ProductionRoleRow, value.role_id)
                if role is not None and (
                    (role.role_type in narrator_types) == (gate_id == "narrator_casting_review")
                ):
                    relevant_rows.append(value)
            invalidation_evidence = request_fingerprint(
                {
                    "reviewEvidenceFingerprint": review.evidence_fingerprint,
                    "assignmentInvalidations": [
                        value.evidence_fingerprint
                        for value in sorted(
                            relevant_rows,
                            key=lambda item: item.id,
                        )
                    ],
                }
            )
            decision_id = stable_id(
                "casting-gate-system-invalidation",
                latest.id,
                invalidation_evidence,
            )
            session.add(
                CastingGateDecisionRow(
                    id=decision_id,
                    project_id=run.project_id,
                    casting_run_id=run.id,
                    cast_snapshot_id=review.cast_snapshot_id,
                    gate_review_id=review.id,
                    gate_id=gate_id,
                    revision=latest.revision + 1,
                    decision="invalidated",
                    evidence_fingerprint=invalidation_evidence,
                    actor_id=_ASSIGNMENT_INVALIDATION_ACTOR_ID,
                    warning_acknowledgements_json="[]",
                    rationale=(
                        "Selected voice catalog or rights evidence changed; "
                        "explicit human reselection and reapproval are required."
                    ),
                    provenance_json=canonical_json(
                        {
                            "origin": "system",
                            "producerId": _ASSIGNMENT_INVALIDATION_ACTOR_ID,
                            "producerVersion": "1.0.0",
                            "recordedAt": now,
                            "inputFingerprint": invalidation_evidence,
                        }
                    ),
                    supersedes_decision_id=latest.id,
                    idempotency_key=stable_id(
                        "casting-gate-system-invalidation-key",
                        decision_id,
                    ),
                    decided_at=now,
                    created_at=now,
                )
            )
        session.flush()

    def _require_assignment_read_run(
        self,
        session: Session,
        *,
        project_id: str,
        run_id: str,
        evidence: dict[str, Any],
        expected_cast_snapshot_id: str | None = None,
        expected_cast_snapshot_revision: int | None = None,
    ) -> CastingRunRow:
        run = self.require_run(
            session,
            project_id=project_id,
            run_id=run_id,
        )
        self.assert_evidence(session, run=run, **evidence)
        if expected_cast_snapshot_id is None:
            return run
        snapshot = self._latest_snapshot(session, run.id)
        if (
            expected_cast_snapshot_revision is None
            or snapshot is None
            or snapshot.id != expected_cast_snapshot_id
            or snapshot.revision != expected_cast_snapshot_revision
        ):
            raise ServiceError(
                409,
                "CASTING_SNAPSHOT_STALE",
                "The reviewable cast snapshot changed; refresh first.",
            )
        return run

    def _latch_external_assignment_invalidations_for_read(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence: dict[str, Any],
        expected_cast_snapshot_id: str | None = None,
        expected_cast_snapshot_revision: int | None = None,
    ) -> None:
        """Preflight drift normally, then serialize only the append transaction."""

        with self.database.session() as session:
            run = self._require_assignment_read_run(
                session,
                project_id=project_id,
                run_id=run_id,
                evidence=evidence,
                expected_cast_snapshot_id=expected_cast_snapshot_id,
                expected_cast_snapshot_revision=expected_cast_snapshot_revision,
            )
            candidate_ids = self._external_assignment_drift_candidate_ids(
                session,
                run=run,
            )
        if not candidate_ids:
            return
        with self.database.immediate_session() as session:
            run = self._require_assignment_read_run(
                session,
                project_id=project_id,
                run_id=run_id,
                evidence=evidence,
                expected_cast_snapshot_id=expected_cast_snapshot_id,
                expected_cast_snapshot_revision=expected_cast_snapshot_revision,
            )
            self._sync_external_assignment_invalidations(
                session,
                run=run,
                candidate_assignment_ids=frozenset(candidate_ids),
            )

    def _assert_current_catalog_write_evidence(
        self,
        run: CastingRunRow,
    ) -> None:
        if self.catalog.fingerprint != run.catalog_fingerprint:
            raise ServiceError(
                409,
                "CASTING_CATALOG_EVIDENCE_STALE",
                "The voice catalog changed; refresh before recording a decision.",
            )

    def publish_result(
        self,
        *,
        session: Session,
        job: JobRow,
        result: dict[str, Any],
    ) -> None:
        run = session.get(CastingRunRow, job.target_id)
        if run is None or run.job_id != job.id:
            raise ServiceError(
                409,
                "CASTING_RUN_INPUT_INVALID",
                "The frozen casting run is unavailable.",
            )
        expected_gate_ids = parse_json(run.phase2_gate_decision_ids_json, {})
        (
            _analysis_run,
            _analysis_snapshot,
            current_gate_ids,
            current_character_registry_fingerprint,
            current_gate_evidence_fingerprint,
        ) = self._phase_2_evidence(
            session,
            project_id=run.project_id,
            expected_analysis_run_id=run.analysis_run_id,
            expected_snapshot_id=run.analysis_snapshot_id,
            expected_snapshot_revision=run.analysis_snapshot_revision,
            expected_snapshot_fingerprint=run.analysis_snapshot_fingerprint,
            expected_correction_set_fingerprint=run.analysis_correction_set_fingerprint,
            expected_import_review_decision_id=run.import_review_decision_id,
            expected_analysis_gate_decision_ids=expected_gate_ids,
        )
        if (
            current_gate_ids != expected_gate_ids
            or current_character_registry_fingerprint != run.character_registry_fingerprint
            or current_gate_evidence_fingerprint != run.phase2_gate_evidence_fingerprint
        ):
            raise ServiceError(
                409,
                "CASTING_PHASE_2_EVIDENCE_STALE",
                "The approved Phase 2 evidence changed before casting publication.",
                retryable=False,
            )
        if session.scalar(
            select(func.count())
            .select_from(ProductionRoleRow)
            .where(ProductionRoleRow.casting_run_id == run.id)
        ):
            return
        now = utc_now()
        voice_rows = self._voice_rows(session, run.catalog_revision_id)
        role_rows: dict[str, ProductionRoleRow] = {}
        roles = list(result["roles"])
        for ordinal, role in enumerate(roles):
            role_row = ProductionRoleRow(
                id=str(role["roleId"]),
                project_id=run.project_id,
                casting_run_id=run.id,
                ordinal=ordinal,
                role_type=str(role["roleType"]),
                phase2_entity_id=role.get("analysisEntityId"),
                character_id=role.get("characterId"),
                role_importance=role.get("roleImportance"),
                effective_display_label=str(role["effectiveDisplayLabel"]),
                analysis_run_id=run.analysis_run_id,
                analysis_snapshot_id=run.analysis_snapshot_id,
                dialogue_line_count=int(role["dialogueLineCount"]),
                narration_span_count=int(role["narrationSpanCount"]),
                approximate_word_count=int(role["approximateWordCount"]),
                chapter_range_json=canonical_json(role["chapterRange"]),
                scene_range_json=canonical_json(role["sceneRange"]),
                language_requirements_json=canonical_json(role["languageRequirements"]),
                performance_requirements_json=canonical_json(role["performanceRequirements"]),
                warnings_json=canonical_json(role["warnings"]),
                provenance_json=canonical_json(role["provenance"]),
                status=("unresolved" if role["roleType"] == "unresolved_speaker" else "active"),
                role_fingerprint=str(role["roleFingerprint"]),
                created_at=now,
            )
            role_rows[role_row.id] = role_row
            session.add(role_row)
        session.flush()
        candidates = list(result["candidates"])
        conflicts = list(result["conflicts"])
        conflict_ids_by_candidate: dict[str, list[str]] = {}
        for candidate in candidates:
            role_id = str(candidate["roleId"])
            voice_id = str(candidate["voiceProfileId"])
            conflict_ids_by_candidate[str(candidate["candidateId"])] = [
                str(conflict["conflictId"])
                for conflict in conflicts
                if role_id
                in {
                    str(conflict["primaryRoleId"]),
                    *(str(value) for value in conflict["relatedRoleIds"]),
                }
                and (
                    not conflict["voiceProfileIds"]
                    or voice_id in {str(value) for value in conflict["voiceProfileIds"]}
                )
            ]
        candidate_rows: dict[str, CastingCandidateRow] = {}
        for candidate in candidates:
            voice_id = str(candidate["voiceProfileId"])
            raw_status = str(candidate["compatibilityStatus"])
            db_status = raw_status
            raw_rights = str(candidate["rightsEligibility"])
            db_rights = {
                "verified": "eligible",
                "restricted": "restricted",
                "unknown": "unknown",
                "prohibited": "ineligible",
            }.get(raw_rights, raw_rights)
            candidate_row = CastingCandidateRow(
                id=str(candidate["candidateId"]),
                project_id=run.project_id,
                casting_run_id=run.id,
                role_id=str(candidate["roleId"]),
                voice_profile_record_id=voice_rows[voice_id].id,
                role_revision=1,
                ordinal=int(candidate["ordinal"]),
                compatibility_status=db_status,
                compatibility_score=int(round(float(candidate["compatibilityScore"]) * 1_000_000)),
                confidence_class=str(candidate["confidenceClassification"]),
                hard_constraint_results_json=canonical_json(candidate["hardConstraintResults"]),
                soft_preference_results_json=canonical_json(candidate["softPreferenceResults"]),
                rights_eligibility=db_rights,
                language_eligibility=str(candidate["languageEligibility"]),
                provider_availability=(
                    "available" if candidate["providerAvailability"] else "unavailable"
                ),
                model_availability=(
                    "available" if candidate["modelAvailability"] else "unavailable"
                ),
                long_form_suitability=(
                    "suitable"
                    if candidate["longFormSuitability"] is True
                    else "unsuitable"
                    if candidate["longFormSuitability"] is False
                    else "unknown"
                ),
                conflict_warnings_json=canonical_json(
                    conflict_ids_by_candidate[str(candidate["candidateId"])]
                ),
                explanation_json=canonical_json(
                    {
                        "text": candidate["explanation"],
                        "preReductionRank": candidate["preReductionRank"],
                    }
                ),
                provenance_json=canonical_json(candidate["provenance"]),
                input_fingerprint=str(candidate["inputFingerprint"]),
                output_fingerprint=_deterministic_machine_fingerprint(candidate),
                created_at=now,
            )
            candidate_rows[candidate_row.id] = candidate_row
            session.add(candidate_row)
        session.flush()
        for conflict in conflicts:
            related = list(conflict["relatedRoleIds"])
            voice_ids = list(conflict["voiceProfileIds"])
            category = str(conflict["category"])
            session.add(
                CastingConflictRow(
                    id=str(conflict["conflictId"]),
                    project_id=run.project_id,
                    casting_run_id=run.id,
                    primary_role_id=str(conflict["primaryRoleId"]),
                    secondary_role_id=(str(related[0]) if related else None),
                    voice_profile_record_id=(
                        voice_rows[str(voice_ids[0])].id if voice_ids else None
                    ),
                    category=category,
                    severity=str(conflict["severity"]),
                    status="open",
                    details_json=canonical_json(conflict),
                    evidence_fingerprint=str(conflict["conflictFingerprint"]),
                    provenance_json=canonical_json(
                        {
                            "origin": "runtime_agent",
                            "producerId": CASTING_PRODUCER_ID,
                            "producerVersion": CASTING_PRODUCER_VERSION,
                            "recordedAt": now,
                            "inputFingerprint": run.input_fingerprint,
                        }
                    ),
                    created_at=now,
                )
            )
        session.flush()
        for role in role_rows.values():
            role_candidates = [
                value
                for value in candidates
                if value["roleId"] == role.id
                and value["compatibilityStatus"] in {"eligible", "conditional"}
            ]
            if not role_candidates:
                continue
            proposal = role_candidates[0]
            voice = voice_rows[str(proposal["voiceProfileId"])]
            session.add(
                CastAssignmentRow(
                    id=stable_id(
                        "machine-cast-proposal",
                        run.id,
                        role.id,
                        proposal["candidateId"],
                    ),
                    project_id=run.project_id,
                    casting_run_id=run.id,
                    role_id=role.id,
                    correction_id=None,
                    voice_profile_record_id=voice.id,
                    catalog_revision_id=run.catalog_revision_id,
                    casting_profile_fingerprint=(run.casting_profile_fingerprint),
                    phase2_snapshot_fingerprint=(run.analysis_snapshot_fingerprint),
                    effective_correction_set_fingerprint=(run.effective_correction_set_fingerprint),
                    authority="machine_proposal",
                    assignment_state="proposed",
                    rationale=(
                        "Deterministic declared-metadata proposal for human review; "
                        "not an automatic selection or artistic-correctness claim."
                    ),
                    warnings_json="[]",
                    rights_state=voice.rights_state,
                    revision=1,
                    provenance_json=canonical_json(
                        {
                            "origin": "runtime_agent",
                            "producerId": CASTING_PRODUCER_ID,
                            "producerVersion": CASTING_PRODUCER_VERSION,
                            "recordedAt": now,
                            "inputFingerprint": run.input_fingerprint,
                        }
                    ),
                    supersedes_assignment_id=None,
                    created_at=now,
                )
            )
        session.flush()
        run.state = "succeeded"
        run.published_at = now
        self._publish_cast_snapshot(session, run=run, now=now)

    @staticmethod
    def _correction_material(row: CastingCorrectionRow) -> dict[str, Any]:
        corrected_value = parse_json(row.corrected_value_json, None)
        provenance = parse_json(row.provenance_json, None)
        if not isinstance(corrected_value, dict) or not isinstance(provenance, dict):
            raise ServiceError(
                500,
                "CASTING_CORRECTION_EVIDENCE_INVALID",
                "Casting correction evidence failed integrity validation.",
            )
        return {
            "correctionId": row.id,
            "projectId": row.project_id,
            "castingRunId": row.casting_run_id,
            "roleId": row.role_id,
            "kind": row.kind,
            "revision": row.revision,
            "priorEffectiveFingerprint": row.prior_effective_fingerprint,
            "correctedValue": corrected_value,
            "actorId": row.actor_id,
            "reason": row.reason,
            "provenance": provenance,
            "supersedesCorrectionId": row.supersedes_correction_id,
            "idempotencyKey": row.idempotency_key,
            "recordedAt": row.recorded_at,
        }

    @staticmethod
    def _public_provenance(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value[key]
            for key in (
                "origin",
                "producerId",
                "producerVersion",
                "recordedAt",
                "inputFingerprint",
                "sourceRevisionId",
                "reason",
            )
            if key in value
        }

    @classmethod
    def _validated_correction_fingerprint(
        cls,
        row: CastingCorrectionRow,
    ) -> str:
        fingerprint = request_fingerprint(cls._correction_material(row))
        if row.correction_fingerprint != fingerprint:
            raise ServiceError(
                500,
                "CASTING_CORRECTION_EVIDENCE_INVALID",
                "Casting correction evidence failed integrity validation.",
            )
        return fingerprint

    @classmethod
    def _validated_correction_value(
        cls,
        row: CastingCorrectionRow,
    ) -> dict[str, Any]:
        cls._validated_correction_fingerprint(row)
        value = cls._correction_material(row)["correctedValue"]
        assert isinstance(value, dict)
        return value

    def _correction_fingerprint(
        self,
        session: Session,
        run_id: str,
    ) -> str:
        rows = list(
            session.scalars(
                select(CastingCorrectionRow)
                .where(CastingCorrectionRow.casting_run_id == run_id)
                .order_by(
                    CastingCorrectionRow.recorded_at,
                    CastingCorrectionRow.id,
                )
            )
        )
        return request_fingerprint(
            [
                {
                    "correctionId": row.id,
                    "kind": row.kind,
                    "revision": row.revision,
                    "correctionFingerprint": (self._validated_correction_fingerprint(row)),
                    "supersedesCorrectionId": row.supersedes_correction_id,
                }
                for row in rows
            ]
        )

    def _effective_assignment_rows(
        self,
        session: Session,
        run_id: str,
    ) -> list[CastAssignmentRow]:
        latest_revisions = (
            select(
                CastAssignmentRow.role_id.label("role_id"),
                func.max(CastAssignmentRow.revision).label("revision"),
            )
            .where(
                CastAssignmentRow.casting_run_id == run_id,
                CastAssignmentRow.authority.in_(("human_selection", "human_locked")),
            )
            .group_by(CastAssignmentRow.role_id)
            .subquery()
        )
        return list(
            session.scalars(
                select(CastAssignmentRow)
                .join(
                    latest_revisions,
                    (CastAssignmentRow.role_id == latest_revisions.c.role_id)
                    & (CastAssignmentRow.revision == latest_revisions.c.revision),
                )
                .join(ProductionRoleRow, ProductionRoleRow.id == CastAssignmentRow.role_id)
                .where(
                    CastAssignmentRow.casting_run_id == run_id,
                    ProductionRoleRow.casting_run_id == run_id,
                )
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        )

    @staticmethod
    def _set_conflict_overflow_warning(
        run: CastingRunRow,
        *,
        code: str,
        overflowed: bool,
    ) -> None:
        warnings = [
            value
            for value in parse_json(run.warnings_json, [])
            if isinstance(value, dict) and value.get("code") != code
        ]
        if overflowed:
            warnings.append(
                {
                    "code": code,
                    "severity": "blocker",
                    "message": (
                        "The bounded conflict ledger could not represent every "
                        "current metadata fact; casting approval fails closed."
                    ),
                    "requiresHumanReview": True,
                    "relatedEntityIds": [],
                    "evidence": [],
                }
            )
        run.warnings_json = canonical_json(warnings)

    @staticmethod
    def _conflict_role_ids(conflict: CastingConflictRow) -> list[str]:
        details = parse_json(conflict.details_json, {})
        values = {
            conflict.primary_role_id,
            *([conflict.secondary_role_id] if conflict.secondary_role_id is not None else []),
            *[value for value in details.get("relatedRoleIds", []) if isinstance(value, str)],
        }
        return sorted(values)

    def _current_restricted_rights_acknowledgement(
        self,
        session: Session,
        *,
        run_id: str,
        role_id: str,
        voice_profile_id: str,
        rights_record_id: str,
        rights_record_revision: int,
    ) -> CastingCorrectionRow | None:
        acknowledgement = session.scalar(
            select(CastingCorrectionRow)
            .where(
                CastingCorrectionRow.casting_run_id == run_id,
                CastingCorrectionRow.role_id == role_id,
                CastingCorrectionRow.kind == "acknowledge_restricted_rights",
            )
            .order_by(
                CastingCorrectionRow.revision.desc(),
                CastingCorrectionRow.id.desc(),
            )
            .limit(1)
        )
        if acknowledgement is None:
            return None
        successor = session.scalar(
            select(CastingCorrectionRow.id)
            .where(
                CastingCorrectionRow.casting_run_id == run_id,
                CastingCorrectionRow.supersedes_correction_id == acknowledgement.id,
            )
            .limit(1)
        )
        if successor is not None:
            return None
        value = self._validated_correction_value(acknowledgement)
        if value != {
            "rightsRecordId": rights_record_id,
            "rightsRecordRevision": rights_record_revision,
        }:
            return None
        rights_voice = session.scalar(
            select(VoiceProfileRow.profile_id)
            .join(
                VoiceRightsRecordRow,
                VoiceRightsRecordRow.voice_profile_record_id == VoiceProfileRow.id,
            )
            .where(
                VoiceRightsRecordRow.rights_record_id == rights_record_id,
                VoiceRightsRecordRow.revision == rights_record_revision,
            )
            .limit(1)
        )
        return acknowledgement if rights_voice == voice_profile_id else None

    def _current_conflict_disposition(
        self,
        session: Session,
        conflict: CastingConflictRow,
    ) -> CastingCorrectionRow | None:
        expected_role_ids = self._conflict_role_ids(conflict)
        rows = list(
            session.scalars(
                select(CastingCorrectionRow)
                .where(
                    CastingCorrectionRow.casting_run_id == conflict.casting_run_id,
                    CastingCorrectionRow.kind == "approve_voice_reuse",
                    CastingCorrectionRow.corrected_value_json.contains(conflict.id),
                )
                .order_by(
                    CastingCorrectionRow.revision.desc(),
                    CastingCorrectionRow.id.desc(),
                )
            )
        )
        return next(
            (
                row
                for row in rows
                if self._validated_correction_value(row)
                == {
                    "conflictId": conflict.id,
                    "approvedRoleIds": expected_role_ids,
                }
            ),
            None,
        )

    def _effective_role_assessment_values(
        self,
        session: Session,
        role: ProductionRoleRow,
    ) -> dict[str, Any]:
        role_values = self._role_wire(session, role)
        return {
            "roleId": role.id,
            "roleType": role.role_type,
            "roleImportance": role_values.get("roleImportance"),
            "languageRequirements": role_values["languageRequirements"],
            "performanceRequirements": role_values["performanceRequirements"],
            "approximateWordCount": role.approximate_word_count,
        }

    @staticmethod
    def _current_candidate_rows(
        session: Session,
        *,
        run_id: str,
        role_id: str | None = None,
    ) -> list[CastingCandidateRow]:
        """Return only the newest immutable machine-evidence generation per role."""

        latest_revisions = (
            select(
                CastingCandidateRow.role_id.label("role_id"),
                func.max(CastingCandidateRow.role_revision).label("role_revision"),
            )
            .where(CastingCandidateRow.casting_run_id == run_id)
            .group_by(CastingCandidateRow.role_id)
            .subquery()
        )
        statement = (
            select(CastingCandidateRow)
            .join(
                latest_revisions,
                (CastingCandidateRow.role_id == latest_revisions.c.role_id)
                & (CastingCandidateRow.role_revision == latest_revisions.c.role_revision),
            )
            .where(CastingCandidateRow.casting_run_id == run_id)
        )
        if role_id is not None:
            statement = statement.where(CastingCandidateRow.role_id == role_id)
        return list(
            session.scalars(
                statement.order_by(
                    CastingCandidateRow.role_id,
                    CastingCandidateRow.ordinal,
                    CastingCandidateRow.id,
                )
            )
        )

    @staticmethod
    def _current_candidate_revision(
        session: Session,
        *,
        role_id: str,
    ) -> int:
        return int(
            session.scalar(
                select(func.max(CastingCandidateRow.role_revision)).where(
                    CastingCandidateRow.role_id == role_id
                )
            )
            or 0
        )

    def _replace_role_machine_evidence(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        role: ProductionRoleRow,
        now: str,
        candidate_input_override: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Append a new current generation while retaining prior machine evidence."""

        generation_revision = self._role_revision(session, role.id)
        prior_conflicts = list(
            session.scalars(
                select(CastingConflictRow).where(CastingConflictRow.casting_run_id == run.id)
            )
        )
        for prior_conflict_row in prior_conflicts:
            details = parse_json(prior_conflict_row.details_json, {})
            if details.get("source") != "human_assignments" and role.id in self._conflict_role_ids(
                prior_conflict_row
            ):
                prior_conflict_row.status = "superseded"
        session.flush()

        assessment_values = self._effective_role_assessment_values(
            session,
            role,
        )
        candidate_input_fingerprint = candidate_input_override or request_fingerprint(
            {
                "castingRunInputFingerprint": run.input_fingerprint,
                "roleAssessment": assessment_values,
                "catalogFingerprint": run.catalog_fingerprint,
                "castingProfileFingerprint": (run.casting_profile_fingerprint),
                "effectiveCorrectionSetFingerprint": (run.effective_correction_set_fingerprint),
                "roleRevision": generation_revision,
            }
        )
        candidates, role_conflicts = generate_candidates(
            roles=[assessment_values],
            catalog=self.catalog,
            input_fingerprint=candidate_input_fingerprint,
        )

        all_roles = list(
            session.scalars(
                select(ProductionRoleRow)
                .where(ProductionRoleRow.casting_run_id == run.id)
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        )
        role_assessments = [
            assessment_values,
            *[
                self._effective_role_assessment_values(session, value)
                for value in all_roles
                if value.id != role.id
            ],
        ]
        voice_rows = self._voice_rows(session, run.catalog_revision_id)
        voice_profile_ids_by_record = {value.id: value.profile_id for value in voice_rows.values()}
        other_current_candidates = [
            {
                "roleId": value.role_id,
                "voiceProfileId": voice_profile_ids_by_record[value.voice_profile_record_id],
                "ordinal": value.ordinal,
            }
            for value in self._current_candidate_rows(
                session,
                run_id=run.id,
            )
            if value.role_id != role.id
        ]
        pairwise_conflicts = [
            value
            for value in generate_pairwise_candidate_conflicts(
                roles=role_assessments,
                candidates=[*other_current_candidates, *candidates],
                catalog=self.catalog,
                input_fingerprint=candidate_input_fingerprint,
            )
            if role.id
            in {
                str(value["primaryRoleId"]),
                *(str(candidate) for candidate in value["relatedRoleIds"]),
            }
        ]
        generated_conflicts = list(
            {
                str(value["conflictId"]): value for value in [*role_conflicts, *pairwise_conflicts]
            }.values()
        )

        current_conflict_count = int(
            session.scalar(
                select(func.count())
                .select_from(CastingConflictRow)
                .where(
                    CastingConflictRow.casting_run_id == run.id,
                    CastingConflictRow.status == "open",
                )
            )
            or 0
        )
        available_conflicts = max(
            0,
            MAX_CASTING_CONFLICTS - current_conflict_count,
        )
        conflicts = sorted(
            generated_conflicts,
            key=lambda value: str(value["conflictId"]),
        )[:available_conflicts]
        self._set_conflict_overflow_warning(
            run,
            code=_STATIC_CONFLICT_OVERFLOW_CODE,
            overflowed=len(conflicts) != len(generated_conflicts),
        )
        conflict_ids_by_candidate: dict[str, list[str]] = {}
        for generated_candidate in candidates:
            voice_id = str(generated_candidate["voiceProfileId"])
            conflict_ids_by_candidate[str(generated_candidate["candidateId"])] = [
                str(generated_conflict["conflictId"])
                for generated_conflict in conflicts
                if (
                    not generated_conflict["voiceProfileIds"]
                    or voice_id in {str(value) for value in generated_conflict["voiceProfileIds"]}
                )
            ]

        for generated_candidate in candidates:
            voice_id = str(generated_candidate["voiceProfileId"])
            raw_rights = str(generated_candidate["rightsEligibility"])
            provenance = {
                **dict(generated_candidate["provenance"]),
                "recordedAt": now,
            }
            output_fingerprint = _deterministic_machine_fingerprint(
                {
                    **generated_candidate,
                    "provenance": provenance,
                },
            )
            session.add(
                CastingCandidateRow(
                    id=str(generated_candidate["candidateId"]),
                    project_id=run.project_id,
                    casting_run_id=run.id,
                    role_id=role.id,
                    voice_profile_record_id=voice_rows[voice_id].id,
                    role_revision=generation_revision,
                    ordinal=int(generated_candidate["ordinal"]),
                    compatibility_status=str(generated_candidate["compatibilityStatus"]),
                    compatibility_score=int(
                        round(float(generated_candidate["compatibilityScore"]) * 1_000_000)
                    ),
                    confidence_class=str(generated_candidate["confidenceClassification"]),
                    hard_constraint_results_json=canonical_json(
                        generated_candidate["hardConstraintResults"]
                    ),
                    soft_preference_results_json=canonical_json(
                        generated_candidate["softPreferenceResults"]
                    ),
                    rights_eligibility={
                        "verified": "eligible",
                        "restricted": "restricted",
                        "unknown": "unknown",
                        "prohibited": "ineligible",
                    }.get(raw_rights, raw_rights),
                    language_eligibility=str(generated_candidate["languageEligibility"]),
                    provider_availability=(
                        "available"
                        if generated_candidate["providerAvailability"]
                        else "unavailable"
                    ),
                    model_availability=(
                        "available" if generated_candidate["modelAvailability"] else "unavailable"
                    ),
                    long_form_suitability=(
                        "suitable"
                        if generated_candidate["longFormSuitability"] is True
                        else "unsuitable"
                        if generated_candidate["longFormSuitability"] is False
                        else "unknown"
                    ),
                    conflict_warnings_json=canonical_json(
                        conflict_ids_by_candidate[str(generated_candidate["candidateId"])]
                    ),
                    explanation_json=canonical_json(
                        {
                            "text": generated_candidate["explanation"],
                            "preReductionRank": generated_candidate["preReductionRank"],
                        }
                    ),
                    provenance_json=canonical_json(provenance),
                    input_fingerprint=str(generated_candidate["inputFingerprint"]),
                    output_fingerprint=output_fingerprint,
                    created_at=now,
                )
            )
        session.flush()
        for generated_conflict in conflicts:
            voice_ids = [str(value) for value in generated_conflict["voiceProfileIds"]]
            related_role_ids = [str(value) for value in generated_conflict["relatedRoleIds"]]
            session.add(
                CastingConflictRow(
                    id=str(generated_conflict["conflictId"]),
                    project_id=run.project_id,
                    casting_run_id=run.id,
                    primary_role_id=str(generated_conflict["primaryRoleId"]),
                    secondary_role_id=(related_role_ids[0] if related_role_ids else None),
                    voice_profile_record_id=(voice_rows[voice_ids[0]].id if voice_ids else None),
                    category=str(generated_conflict["category"]),
                    severity=str(generated_conflict["severity"]),
                    status="open",
                    details_json=canonical_json(generated_conflict),
                    evidence_fingerprint=str(generated_conflict["conflictFingerprint"]),
                    provenance_json=canonical_json(
                        {
                            "origin": "runtime_agent",
                            "producerId": CASTING_PRODUCER_ID,
                            "producerVersion": (CASTING_PRODUCER_VERSION),
                            "recordedAt": now,
                            "inputFingerprint": (candidate_input_fingerprint),
                        }
                    ),
                    created_at=now,
                )
            )
        return candidates, candidate_input_fingerprint

    def _refresh_assignment_conflicts(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        now: str,
    ) -> None:
        roles = {
            row.id: row
            for row in session.scalars(
                select(ProductionRoleRow).where(ProductionRoleRow.casting_run_id == run.id)
            )
        }
        assignments = sorted(
            [
                row
                for row in self._effective_assignment_rows(session, run.id)
                if row.assignment_state in {"selected", "locked"}
                and row.voice_profile_record_id is not None
            ],
            key=lambda value: (value.role_id, value.id),
        )
        voice_rows = {
            row.id: row
            for row in session.scalars(
                select(VoiceProfileRow).where(
                    VoiceProfileRow.catalog_revision_id == run.catalog_revision_id
                )
            )
        }
        voice_rows_by_profile = {row.profile_id: row for row in voice_rows.values()}
        assignments_by_voice: dict[str, list[CastAssignmentRow]] = {}
        for assignment in assignments:
            assert assignment.voice_profile_record_id is not None
            assignments_by_voice.setdefault(
                assignment.voice_profile_record_id,
                [],
            ).append(assignment)

        assignment_generation_fingerprint = request_fingerprint(
            {
                "contractVersion": CASTING_CONTRACT_VERSION,
                "castingRunId": run.id,
                "catalogRevisionId": run.catalog_revision_id,
                "castingProfileFingerprint": run.casting_profile_fingerprint,
                "assignments": [
                    {
                        "assignmentId": value.id,
                        "roleId": value.role_id,
                        "voiceProfileRecordId": value.voice_profile_record_id,
                        "assignmentState": value.assignment_state,
                        "revision": value.revision,
                        "effectiveCorrectionSetFingerprint": (
                            value.effective_correction_set_fingerprint
                        ),
                    }
                    for value in assignments
                ],
            }
        )
        conflict_specs: list[dict[str, Any]] = []

        def append_conflict_spec(
            *,
            category: str,
            selected_assignments: list[CastAssignmentRow],
            selected_voice_ids: list[str],
            explanation: str,
        ) -> None:
            ordered_assignments = sorted(
                selected_assignments,
                key=lambda value: (value.role_id, value.id),
            )
            role_ids = sorted({value.role_id for value in ordered_assignments})
            voice_ids = sorted(set(selected_voice_ids))
            if len(role_ids) < 2 or not voice_ids:
                return
            conflict_id = stable_id(
                "phase3a-human-assignment-conflict",
                category,
                run.id,
                assignment_generation_fingerprint,
                *(value.id for value in ordered_assignments),
            )
            details = {
                "contractVersion": CASTING_CONTRACT_VERSION,
                "conflictId": conflict_id,
                "category": category,
                "severity": "warning",
                "primaryRoleId": role_ids[0],
                "relatedRoleIds": role_ids[1:MAX_PRODUCTION_ROLES],
                "voiceProfileIds": voice_ids[:MAX_PRODUCTION_ROLES],
                "status": "open",
                "explanation": explanation,
                "metadataBased": True,
                "acousticSimilarityClaimed": False,
                "inputFingerprint": assignment_generation_fingerprint,
                "source": "human_assignments",
                "assignmentIds": [value.id for value in ordered_assignments[:MAX_PRODUCTION_ROLES]],
            }
            conflict_specs.append(
                {
                    "id": conflict_id,
                    "roleIds": role_ids,
                    "voiceIds": voice_ids,
                    "assignmentIds": [value.id for value in ordered_assignments],
                    "category": category,
                    "details": details,
                    "evidenceFingerprint": request_fingerprint(details),
                }
            )

        narrator_types = {"primary_narrator", "secondary_narrator"}
        major_types = {*narrator_types, "named_character"}
        for voice_record_id, voice_assignments in sorted(assignments_by_voice.items()):
            ordered = sorted(voice_assignments, key=lambda value: value.role_id)
            voice = voice_rows.get(voice_record_id)
            if voice is None:
                continue
            major_assignments = [
                value
                for value in ordered
                if (
                    roles.get(value.role_id) is not None
                    and roles[value.role_id].role_type in major_types
                )
            ]
            major_role_types = {roles[value.role_id].role_type for value in major_assignments}
            if len(major_assignments) >= 2:
                narrator_character_reuse = (
                    bool(major_role_types & narrator_types)
                    and "named_character" in major_role_types
                )
                append_conflict_spec(
                    category=(
                        "narrator_major_character_reuse"
                        if narrator_character_reuse
                        else "incompatible_voice_reuse"
                    ),
                    selected_assignments=major_assignments,
                    selected_voice_ids=[voice.profile_id],
                    explanation=(
                        "The current human assignments reuse one declared "
                        "voice across differentiation-sensitive narrator and "
                        "character roles."
                        if narrator_character_reuse
                        else "The current human assignments reuse one declared "
                        "voice across differentiation-sensitive production roles."
                    ),
                )
            if len(ordered) > 2:
                append_conflict_spec(
                    category="voice_reuse_threshold_exceeded",
                    selected_assignments=ordered,
                    selected_voice_ids=[voice.profile_id],
                    explanation=(
                        "The current human assignments reuse one declared voice "
                        "beyond the configured maximum of two roles."
                    ),
                )

        similarity_group_by_voice = {
            str(value["voiceProfileId"]): str(group)
            for value in self.catalog.voices
            if isinstance(
                group := value.get("metadataSimilarityGroup"),
                str,
            )
            and group
        }
        assignments_by_similarity_group: dict[
            str,
            list[CastAssignmentRow],
        ] = {}
        for assignment in assignments:
            assert assignment.voice_profile_record_id is not None
            voice = voice_rows.get(assignment.voice_profile_record_id)
            if voice is None:
                continue
            group = similarity_group_by_voice.get(voice.profile_id)
            if group is not None:
                assignments_by_similarity_group.setdefault(
                    group,
                    [],
                ).append(assignment)
        for group, group_assignments in sorted(assignments_by_similarity_group.items()):
            selected_voice_ids = sorted(
                {
                    voice_rows[value.voice_profile_record_id].profile_id
                    for value in group_assignments
                    if value.voice_profile_record_id in voice_rows
                }
            )
            if len(selected_voice_ids) < 2:
                continue
            append_conflict_spec(
                category="metadata_similarity_risk",
                selected_assignments=group_assignments,
                selected_voice_ids=selected_voice_ids,
                explanation=(
                    "Current human selections use distinct catalog voices "
                    f"in declared metadata-similarity group {group}. This is "
                    "metadata-only differentiation risk."
                ),
            )

        conflict_specs.sort(
            key=lambda value: (
                value["category"],
                value["roleIds"],
                value["voiceIds"],
                value["assignmentIds"],
                value["id"],
            )
        )
        all_conflicts = list(
            session.scalars(
                select(CastingConflictRow)
                .where(CastingConflictRow.casting_run_id == run.id)
                .order_by(
                    CastingConflictRow.created_at,
                    CastingConflictRow.id,
                )
            )
        )
        static_rows: list[CastingConflictRow] = []
        dynamic_rows: list[CastingConflictRow] = []
        for conflict in all_conflicts:
            details = parse_json(conflict.details_json, {})
            if details.get("source") == "human_assignments":
                dynamic_rows.append(conflict)
            else:
                static_rows.append(conflict)

        active_static_count = sum(value.status in {"open", "acknowledged"} for value in static_rows)
        active_dynamic_capacity = max(
            0,
            MAX_CASTING_CONFLICTS - active_static_count,
        )
        append_capacity = max(
            0,
            MAX_CASTING_CONFLICTS - len(all_conflicts),
        )
        existing_by_id = {value.id: value for value in dynamic_rows}
        selected_specs: list[dict[str, Any]] = []
        for spec in conflict_specs:
            if len(selected_specs) >= active_dynamic_capacity:
                continue
            if str(spec["id"]) not in existing_by_id:
                if append_capacity == 0:
                    continue
                append_capacity -= 1
            selected_specs.append(spec)
        overflowed = len(selected_specs) != len(conflict_specs)
        active_ids = {str(value["id"]) for value in selected_specs}
        for spec in selected_specs:
            conflict_id = str(spec["id"])
            role_ids = list(spec["roleIds"])
            voice_ids = list(spec["voiceIds"])
            voice = voice_rows_by_profile.get(voice_ids[0])
            if voice is None:
                overflowed = True
                active_ids.discard(conflict_id)
                continue
            provenance_json = canonical_json(
                {
                    "origin": "system",
                    "producerId": CASTING_PRODUCER_ID,
                    "producerVersion": CASTING_PRODUCER_VERSION,
                    "recordedAt": now,
                    "inputFingerprint": assignment_generation_fingerprint,
                }
            )
            existing = existing_by_id.get(conflict_id)
            if existing is None:
                session.add(
                    CastingConflictRow(
                        id=conflict_id,
                        project_id=run.project_id,
                        casting_run_id=run.id,
                        primary_role_id=role_ids[0],
                        secondary_role_id=(role_ids[1] if len(role_ids) > 1 else None),
                        voice_profile_record_id=voice.id,
                        category=str(spec["category"]),
                        severity="warning",
                        status="open",
                        details_json=canonical_json(spec["details"]),
                        evidence_fingerprint=str(spec["evidenceFingerprint"]),
                        provenance_json=provenance_json,
                        created_at=now,
                    )
                )
            else:
                expected_details_json = canonical_json(spec["details"])
                if (
                    existing.primary_role_id != role_ids[0]
                    or existing.secondary_role_id != (role_ids[1] if len(role_ids) > 1 else None)
                    or existing.voice_profile_record_id != voice.id
                    or existing.category != str(spec["category"])
                    or existing.severity != "warning"
                    or existing.status != "open"
                    or existing.details_json != expected_details_json
                    or existing.evidence_fingerprint != str(spec["evidenceFingerprint"])
                ):
                    raise ServiceError(
                        500,
                        "CASTING_CONFLICT_ID_COLLISION",
                        "Immutable assignment-conflict evidence collided with a prior row.",
                    )

        for conflict in dynamic_rows:
            if conflict.id not in active_ids and conflict.status in {"open", "acknowledged"}:
                conflict.status = "superseded"
        self._set_conflict_overflow_warning(
            run,
            code=_ASSIGNMENT_CONFLICT_OVERFLOW_CODE,
            overflowed=overflowed,
        )

    def _review_blockers(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        gate_id: str,
        include_gate_prerequisites: bool = True,
        validate_current_cast_snapshot: bool = True,
    ) -> tuple[list[str], list[str]]:
        roles = list(
            session.scalars(
                select(ProductionRoleRow)
                .where(ProductionRoleRow.casting_run_id == run.id)
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        )
        narrator_types = {"primary_narrator", "secondary_narrator"}
        selected_roles = (
            [role for role in roles if role.role_type in narrator_types]
            if gate_id == "narrator_casting_review"
            else [role for role in roles if role.role_type not in narrator_types]
        )
        blockers: list[str] = []
        warnings: list[str] = []
        try:
            self.assert_evidence(
                session,
                run=run,
                validate_current_cast_snapshot=validate_current_cast_snapshot,
            )
        except ServiceError as exc:
            blockers.append(f"phase-2-evidence-stale:{exc.code}")
        if gate_id == "complete_cast_review":
            selected_roles = roles
            if include_gate_prerequisites:
                snapshot = self._latest_snapshot(session, run.id)
                for upstream in CASTING_GATE_IDS[:2]:
                    upstream_review = self._latest_review(
                        session,
                        run_id=run.id,
                        gate_id=upstream,
                    )
                    decision = self._latest_decision(
                        session,
                        run_id=run.id,
                        gate_id=upstream,
                    )
                    if (
                        snapshot is None
                        or upstream_review is None
                        or upstream_review.cast_snapshot_id != snapshot.id
                        or decision is None
                        or decision.gate_review_id != upstream_review.id
                        or decision.cast_snapshot_id != snapshot.id
                        or decision.decision != "approved"
                    ):
                        blockers.append(f"upstream-gate:{upstream}")
        selected_role_ids = {role.id for role in selected_roles}
        selected_voice_by_role: dict[str, str] = {}
        rights_by_voice = {
            row.voice_profile_record_id: row
            for row in session.scalars(
                select(VoiceRightsRecordRow).where(
                    VoiceRightsRecordRow.voice_profile_record_id.in_(
                        select(VoiceProfileRow.id).where(
                            VoiceProfileRow.catalog_revision_id == run.catalog_revision_id
                        )
                    )
                )
            )
        }
        current_voices = {str(value["voiceProfileId"]): value for value in self.catalog.voices}
        current_rights = {str(value["voiceProfileId"]): value for value in self.catalog.rights}
        current_providers = {str(value["providerId"]): value for value in self.catalog.providers}
        current_models = {
            (str(value["providerId"]), str(value["modelId"])): value
            for value in self.catalog.models
        }
        for role in selected_roles:
            assignment = self._latest_assignment(
                session,
                role_id=role.id,
                include_proposal=False,
            )
            if assignment is None or assignment.assignment_state in {
                "cleared",
                "proposed",
            }:
                blockers.append(f"unassigned-role:{role.id}")
                continue
            if assignment.assignment_state == "intentionally_uncast":
                if role.role_type in narrator_types or role.role_type == "named_character":
                    blockers.append(f"required-role-uncast:{role.id}")
                continue
            if assignment.voice_profile_record_id is None:
                blockers.append(f"unassigned-role:{role.id}")
                continue
            if self._assignment_invalidation(session, assignment.id) is not None:
                blockers.append(f"assignment-evidence-invalidated:{role.id}")
            voice = session.get(
                VoiceProfileRow,
                assignment.voice_profile_record_id,
            )
            if voice is None:
                blockers.append(f"selected-voice-missing:{role.id}")
                continue
            selected_voice_by_role[role.id] = voice.profile_id
            candidate_revision = self._current_candidate_revision(
                session,
                role_id=role.id,
            )
            candidate = session.scalar(
                select(CastingCandidateRow)
                .where(
                    CastingCandidateRow.role_id == role.id,
                    CastingCandidateRow.role_revision == candidate_revision,
                    CastingCandidateRow.voice_profile_record_id == voice.id,
                )
                .limit(1)
            )
            if (
                candidate is None
                or candidate.compatibility_status in {"ineligible", "unknown"}
                or candidate.language_eligibility != "eligible"
                or candidate.provider_availability != "available"
                or candidate.model_availability != "available"
            ):
                blockers.append(f"ineligible-candidate:{role.id}:{voice.profile_id}")
            elif (
                self._active_candidate_rejection(
                    session,
                    casting_run_id=run.id,
                    candidate_id=candidate.id,
                )
                is not None
            ):
                blockers.append(f"rejected-candidate:{role.id}:{voice.profile_id}")
            if voice.state in {"deprecated", "unavailable", "blocked"}:
                blockers.append(f"selected-voice-{voice.state}:{role.id}:{voice.profile_id}")
            rights = rights_by_voice.get(assignment.voice_profile_record_id)
            if rights is None or rights.rights_state in {"unknown", "prohibited"}:
                blockers.append(f"ineligible-rights:{role.id}")
            elif not rights_record_is_current(self._stored_rights_material(rights)):
                blockers.append(f"rights-not-current:{role.id}")
            elif rights.rights_state == "restricted":
                acknowledged = self._current_restricted_rights_acknowledgement(
                    session,
                    run_id=run.id,
                    role_id=role.id,
                    voice_profile_id=voice.profile_id,
                    rights_record_id=rights.rights_record_id,
                    rights_record_revision=rights.revision,
                )
                warning_id = f"restricted-rights:{role.id}:{rights.rights_record_id}"
                if acknowledged is None:
                    blockers.append(warning_id)
                warnings.append(warning_id)
            current_voice = current_voices.get(voice.profile_id)
            current_rights_record = current_rights.get(voice.profile_id)
            if current_voice is None:
                blockers.append(f"catalog-voice-missing:{role.id}:{voice.profile_id}")
                continue
            if request_fingerprint(self._stored_voice_material(voice)) != request_fingerprint(
                self._catalog_voice_material(current_voice)
            ):
                blockers.append(f"catalog-voice-changed:{role.id}:{voice.profile_id}")
            if (
                rights is None
                or current_rights_record is None
                or request_fingerprint(self._stored_rights_material(rights))
                != request_fingerprint(self._catalog_rights_material(current_rights_record))
            ):
                blockers.append(f"voice-rights-changed:{role.id}:{voice.profile_id}")
            provider_id = str(current_voice["providerId"])
            model_id = str(current_voice["modelId"])
            current_provider = current_providers.get(provider_id)
            current_model = current_models.get((provider_id, model_id))
            if (
                current_provider is None
                or current_provider.get("runtimeAvailability") != "available"
                or current_provider.get("catalogAvailability") != "available"
                or current_model is None
                or current_model.get("availability") != "available"
                or current_model.get("deprecated") is True
            ):
                blockers.append(f"provider-model-changed:{role.id}:{voice.profile_id}")
            elif current_rights_record is not None:
                governed_private_audition = governed_private_audition_binding_is_exact(
                    voice=current_voice,
                    rights=current_rights_record,
                    provider=current_provider,
                    model=current_model,
                )
                if (
                    candidate is not None
                    and candidate.long_form_suitability in {"unsuitable", "unknown"}
                    and not (
                        candidate.long_form_suitability == "unknown" and governed_private_audition
                    )
                ):
                    blocker = f"ineligible-candidate:{role.id}:{voice.profile_id}"
                    if blocker not in blockers:
                        blockers.append(blocker)
                current_assessment = compatibility_assessment(
                    role=self._role_wire(session, role),
                    voice=current_voice,
                    rights=current_rights_record,
                    provider=current_provider,
                    model=current_model,
                    input_fingerprint=self._effective_role_fingerprint(
                        session,
                        role,
                    ),
                )
                if current_assessment["compatibilityStatus"] in {
                    "ineligible",
                    "unknown",
                }:
                    blockers.append(
                        f"effective-requirement-ineligible:{role.id}:{voice.profile_id}"
                    )
        conflicts = list(
            session.scalars(
                select(CastingConflictRow)
                .where(
                    CastingConflictRow.casting_run_id == run.id,
                    CastingConflictRow.status == "open",
                )
                .order_by(CastingConflictRow.id)
            )
        )
        run_warning_codes = {
            str(value.get("code"))
            for value in parse_json(run.warnings_json, [])
            if isinstance(value, dict)
        }
        for overflow_code in (
            _ASSIGNMENT_CONFLICT_OVERFLOW_CODE,
            _STATIC_CONFLICT_OVERFLOW_CODE,
        ):
            if overflow_code in run_warning_codes:
                marker = overflow_code.casefold().replace("_", "-")
                blockers.append(marker)
                warnings.append(marker)
        for conflict in conflicts:
            conflict_role_ids = set(self._conflict_role_ids(conflict))
            if not conflict_role_ids & selected_role_ids:
                continue
            details = parse_json(conflict.details_json, {})
            conflict_voice_ids = {
                value for value in details.get("voiceProfileIds", []) if isinstance(value, str)
            }
            if (
                details.get("source") != "human_assignments"
                and conflict_voice_ids
                and not conflict_voice_ids
                & {
                    selected_voice_by_role[role_id]
                    for role_id in conflict_role_ids & selected_role_ids
                    if role_id in selected_voice_by_role
                }
            ):
                continue
            disposition = self._current_conflict_disposition(
                session,
                conflict,
            )
            warning_id = f"casting-conflict:{conflict.id}"
            if disposition is None:
                warnings.append(warning_id)
        bounded_blockers = sorted(set(blockers))
        bounded_warnings = sorted(set(warnings))
        if len(bounded_blockers) > MAX_CASTING_WARNINGS_PER_ENTITY:
            bounded_blockers = [
                *bounded_blockers[: MAX_CASTING_WARNINGS_PER_ENTITY - 1],
                "casting-blocker-limit-exceeded",
            ]
        if len(bounded_warnings) > MAX_CASTING_WARNINGS_PER_ENTITY:
            bounded_warnings = [
                *bounded_warnings[: MAX_CASTING_WARNINGS_PER_ENTITY - 1],
                "casting-warning-limit-exceeded",
            ]
            if "casting-warning-limit-exceeded" not in bounded_blockers:
                overflow_markers = [
                    value
                    for value in (
                        "casting-blocker-limit-exceeded",
                        "casting-warning-limit-exceeded",
                    )
                    if value == "casting-warning-limit-exceeded" or value in bounded_blockers
                ]
                bounded_blockers = [
                    *[
                        value
                        for value in bounded_blockers
                        if value != "casting-blocker-limit-exceeded"
                    ][: MAX_CASTING_WARNINGS_PER_ENTITY - len(overflow_markers)],
                    *overflow_markers,
                ]
        return bounded_blockers, bounded_warnings

    @staticmethod
    def _snapshot_manifest_error() -> ServiceError:
        return ServiceError(
            500,
            "CASTING_SNAPSHOT_MANIFEST_INVALID",
            "The approved-cast snapshot manifest failed integrity validation.",
        )

    def _assignment_snapshot_evidence(
        self,
        session: Session,
        *,
        assignment: CastAssignmentRow,
        include_current_invalidation: bool,
        rights_record_id: str | None = None,
        rights_record_revision: int | None = None,
    ) -> dict[str, Any]:
        catalog = self._catalog_identity(session, assignment.catalog_revision_id)
        voice = (
            session.get(VoiceProfileRow, assignment.voice_profile_record_id)
            if assignment.voice_profile_record_id is not None
            else None
        )
        rights: VoiceRightsRecordRow | None = None
        if voice is not None:
            rights_statement = select(VoiceRightsRecordRow).where(
                VoiceRightsRecordRow.voice_profile_record_id == voice.id
            )
            if rights_record_id is not None and rights_record_revision is not None:
                rights_statement = rights_statement.where(
                    VoiceRightsRecordRow.rights_record_id == rights_record_id,
                    VoiceRightsRecordRow.revision == rights_record_revision,
                )
            rights = session.scalar(
                rights_statement.order_by(
                    VoiceRightsRecordRow.revision.desc(),
                    VoiceRightsRecordRow.id.desc(),
                ).limit(1)
            )
            if rights is None:
                raise self._snapshot_manifest_error()
        elif rights_record_id is not None or rights_record_revision is not None:
            raise self._snapshot_manifest_error()
        frozen_invalidation = (
            self._assignment_invalidation(session, assignment.id)
            if include_current_invalidation
            else None
        )
        provenance = parse_json(assignment.provenance_json, {})
        warnings = parse_json(assignment.warnings_json, [])
        if not isinstance(provenance, dict) or not isinstance(warnings, list):
            raise self._snapshot_manifest_error()
        return {
            "assignmentId": assignment.id,
            "projectId": assignment.project_id,
            "castingRunId": assignment.casting_run_id,
            "roleId": assignment.role_id,
            "correctionId": assignment.correction_id,
            "voiceProfileId": voice.profile_id if voice is not None else None,
            "voiceProfileVersion": voice.profile_version if voice is not None else None,
            "voiceEvidenceFingerprint": (voice.profile_fingerprint if voice is not None else None),
            "rightsRecordId": rights.rights_record_id if rights is not None else None,
            "rightsRecordRevision": rights.revision if rights is not None else None,
            "rightsEvidenceFingerprint": (
                rights.rights_fingerprint if rights is not None else None
            ),
            "catalogRevisionId": catalog.catalog_id,
            "castingProfileFingerprint": assignment.casting_profile_fingerprint,
            "phase2SnapshotFingerprint": assignment.phase2_snapshot_fingerprint,
            "effectiveCorrectionSetFingerprint": (assignment.effective_correction_set_fingerprint),
            "authority": assignment.authority,
            "assignmentState": assignment.assignment_state,
            "rationale": assignment.rationale,
            "warnings": warnings,
            "rightsState": assignment.rights_state,
            "revision": assignment.revision,
            "provenance": provenance,
            "supersedesAssignmentId": assignment.supersedes_assignment_id,
            "createdAt": assignment.created_at,
            "invalidation": (
                {
                    "evidenceFingerprint": frozen_invalidation.evidence_fingerprint,
                    "reasonCodes": parse_json(
                        frozen_invalidation.reason_codes_json,
                        [],
                    ),
                    "createdAt": frozen_invalidation.created_at,
                }
                if frozen_invalidation is not None
                else None
            ),
        }

    @staticmethod
    def _snapshot_fingerprint(
        snapshot: ApprovedCastSnapshotRow,
        manifest: dict[str, Any],
    ) -> str:
        return request_fingerprint(
            {
                "castingRunId": snapshot.casting_run_id,
                "revision": snapshot.revision,
                "phase2SnapshotFingerprint": snapshot.phase2_snapshot_fingerprint,
                "catalogFingerprint": snapshot.catalog_fingerprint,
                "castingProfileFingerprint": snapshot.casting_profile_fingerprint,
                "effectiveCorrectionSetFingerprint": (
                    snapshot.effective_correction_set_fingerprint
                ),
                "manifest": manifest,
            }
        )

    @staticmethod
    def _manifest_evidence_by_id(
        manifest: dict[str, Any],
        *,
        field: str,
        id_field: str,
        exact_fields: frozenset[str],
    ) -> dict[str, dict[str, Any]]:
        values = manifest.get(field)
        if not isinstance(values, list):
            raise CastingRepository._snapshot_manifest_error()
        result: dict[str, dict[str, Any]] = {}
        for value in values:
            if (
                not isinstance(value, dict)
                or frozenset(value) != exact_fields
                or not isinstance(value.get(id_field), str)
                or not value[id_field]
                or value[id_field] in result
            ):
                raise CastingRepository._snapshot_manifest_error()
            result[value[id_field]] = value
        return result

    def _validate_snapshot_manifest(
        self,
        session: Session,
        *,
        snapshot: ApprovedCastSnapshotRow,
        run: CastingRunRow,
    ) -> dict[str, Any]:
        manifest = parse_json(snapshot.manifest_json, {})
        expected_manifest_fields = frozenset(
            {
                "productionRoleEvidence",
                "candidateEvidence",
                "assignmentEvidence",
                "correctionEvidence",
                "unresolvedConflictEvidence",
                "assignmentIds",
                "intentionallyUncastRoleIds",
                "unresolvedConflictIds",
                "counts",
                "reviewEligibleAtPublication",
            }
        )
        if (
            not isinstance(manifest, dict)
            or frozenset(manifest) != expected_manifest_fields
            or self._snapshot_fingerprint(snapshot, manifest) != snapshot.snapshot_fingerprint
            or snapshot.project_id != run.project_id
            or snapshot.catalog_revision_id != run.catalog_revision_id
            or snapshot.phase2_snapshot_fingerprint != run.analysis_snapshot_fingerprint
            or snapshot.catalog_fingerprint != run.catalog_fingerprint
            or snapshot.casting_profile_fingerprint != run.casting_profile_fingerprint
            or not isinstance(manifest.get("reviewEligibleAtPublication"), bool)
        ):
            raise self._snapshot_manifest_error()

        roles = self._manifest_evidence_by_id(
            manifest,
            field="productionRoleEvidence",
            id_field="roleId",
            exact_fields=frozenset({"roleId", "effectiveFingerprint"}),
        )
        candidates = self._manifest_evidence_by_id(
            manifest,
            field="candidateEvidence",
            id_field="candidateId",
            exact_fields=frozenset({"candidateId", "roleId", "outputFingerprint"}),
        )
        assignments = self._manifest_evidence_by_id(
            manifest,
            field="assignmentEvidence",
            id_field="assignmentId",
            exact_fields=frozenset(
                {
                    "assignmentId",
                    "projectId",
                    "castingRunId",
                    "roleId",
                    "correctionId",
                    "voiceProfileId",
                    "voiceProfileVersion",
                    "voiceEvidenceFingerprint",
                    "rightsRecordId",
                    "rightsRecordRevision",
                    "rightsEvidenceFingerprint",
                    "catalogRevisionId",
                    "castingProfileFingerprint",
                    "phase2SnapshotFingerprint",
                    "effectiveCorrectionSetFingerprint",
                    "authority",
                    "assignmentState",
                    "rationale",
                    "warnings",
                    "rightsState",
                    "revision",
                    "provenance",
                    "supersedesAssignmentId",
                    "createdAt",
                    "invalidation",
                }
            ),
        )
        corrections = self._manifest_evidence_by_id(
            manifest,
            field="correctionEvidence",
            id_field="correctionId",
            exact_fields=frozenset({"correctionId", "correctionFingerprint"}),
        )
        conflicts = self._manifest_evidence_by_id(
            manifest,
            field="unresolvedConflictEvidence",
            id_field="conflictId",
            exact_fields=frozenset({"conflictId", "evidenceFingerprint"}),
        )

        validated_role_rows: dict[str, ProductionRoleRow] = {}
        for role_id, evidence in roles.items():
            role_row = session.get(ProductionRoleRow, role_id)
            if (
                role_row is None
                or role_row.casting_run_id != run.id
                or not isinstance(evidence.get("effectiveFingerprint"), str)
                or len(evidence["effectiveFingerprint"]) != 64
            ):
                raise self._snapshot_manifest_error()
            self._validate_role_base_fingerprint(
                session,
                role_row,
            )
            validated_role_rows[role_id] = role_row
        for candidate_id, evidence in candidates.items():
            candidate_row = session.get(CastingCandidateRow, candidate_id)
            candidate_voice = (
                session.get(
                    VoiceProfileRow,
                    candidate_row.voice_profile_record_id,
                )
                if candidate_row is not None
                else None
            )
            if (
                candidate_row is None
                or candidate_voice is None
                or candidate_row.casting_run_id != run.id
                or evidence.get("roleId") != candidate_row.role_id
                or evidence.get("outputFingerprint") != candidate_row.output_fingerprint
            ):
                raise self._snapshot_manifest_error()
            self._candidate_machine_evidence(
                candidate_row,
                candidate_voice,
            )
        for assignment_id, evidence in assignments.items():
            assignment_row = session.get(CastAssignmentRow, assignment_id)
            if assignment_row is None or assignment_row.casting_run_id != run.id:
                raise self._snapshot_manifest_error()
            expected = self._assignment_snapshot_evidence(
                session,
                assignment=assignment_row,
                include_current_invalidation=False,
                rights_record_id=evidence.get("rightsRecordId"),
                rights_record_revision=evidence.get("rightsRecordRevision"),
            )
            if evidence.get("invalidation") is not None:
                invalidation = self._assignment_invalidation(
                    session,
                    assignment_row.id,
                )
                if invalidation is None:
                    raise self._snapshot_manifest_error()
                expected["invalidation"] = {
                    "evidenceFingerprint": invalidation.evidence_fingerprint,
                    "reasonCodes": parse_json(
                        invalidation.reason_codes_json,
                        [],
                    ),
                    "createdAt": invalidation.created_at,
                }
            if evidence != expected:
                raise self._snapshot_manifest_error()
        validated_correction_rows: dict[str, CastingCorrectionRow] = {}
        for correction_id, evidence in corrections.items():
            correction_row = session.get(CastingCorrectionRow, correction_id)
            if (
                correction_row is None
                or correction_row.casting_run_id != run.id
                or evidence.get("correctionFingerprint")
                != self._validated_correction_fingerprint(correction_row)
            ):
                raise self._snapshot_manifest_error()
            validated_correction_rows[correction_id] = correction_row
        for conflict_id, evidence in conflicts.items():
            conflict_row = session.get(CastingConflictRow, conflict_id)
            if (
                conflict_row is None
                or conflict_row.casting_run_id != run.id
                or evidence.get("evidenceFingerprint") != conflict_row.evidence_fingerprint
            ):
                raise self._snapshot_manifest_error()
            self._conflict_wire(
                session,
                conflict_row,
            )

        assignment_evidence_by_role: dict[str, dict[str, Any]] = {}
        for assignment_evidence in assignments.values():
            assignment_role_id = str(assignment_evidence["roleId"])
            if assignment_role_id in assignment_evidence_by_role:
                raise self._snapshot_manifest_error()
            assignment_evidence_by_role[assignment_role_id] = assignment_evidence
        correction_rows_by_role: dict[str, list[CastingCorrectionRow]] = {}
        for correction_row in validated_correction_rows.values():
            correction_rows_by_role.setdefault(correction_row.role_id, []).append(correction_row)
        for role_corrections in correction_rows_by_role.values():
            role_corrections.sort(key=lambda value: (value.revision, value.id))

        for role_id, evidence in roles.items():
            persisted_role = validated_role_rows[role_id]
            selected_assignment_evidence = assignment_evidence_by_role.get(role_id)
            role_corrections = correction_rows_by_role.get(role_id, [])
            historical_effective_fingerprint = request_fingerprint(
                {
                    "roleFingerprint": (persisted_role.role_fingerprint),
                    "assignmentId": (
                        selected_assignment_evidence["assignmentId"]
                        if selected_assignment_evidence is not None
                        else None
                    ),
                    "assignmentState": (
                        selected_assignment_evidence["assignmentState"]
                        if selected_assignment_evidence is not None
                        else None
                    ),
                    "correctionFingerprints": [
                        self._validated_correction_fingerprint(value) for value in role_corrections
                    ],
                }
            )
            if evidence["effectiveFingerprint"] != historical_effective_fingerprint:
                raise self._snapshot_manifest_error()

        assignment_ids = manifest.get("assignmentIds")
        intentionally_uncast_ids = manifest.get("intentionallyUncastRoleIds")
        unresolved_conflict_ids = manifest.get("unresolvedConflictIds")
        counts = manifest.get("counts")
        expected_count_fields = frozenset(
            {
                "productionRoles",
                "narratorRoles",
                "characterRoles",
                "preReductionCandidates",
                "finalCandidates",
                "conflicts",
                "assignments",
                "corrections",
            }
        )
        if (
            not isinstance(assignment_ids, list)
            or assignment_ids != sorted(set(assignment_ids))
            or not all(isinstance(value, str) for value in assignment_ids)
            or not isinstance(intentionally_uncast_ids, list)
            or intentionally_uncast_ids != sorted(set(intentionally_uncast_ids))
            or not all(isinstance(value, str) for value in intentionally_uncast_ids)
            or not isinstance(unresolved_conflict_ids, list)
            or unresolved_conflict_ids != sorted(set(unresolved_conflict_ids))
            or set(unresolved_conflict_ids) != set(conflicts)
            or not isinstance(counts, dict)
            or frozenset(counts) != expected_count_fields
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in counts.values()
            )
        ):
            raise self._snapshot_manifest_error()

        selected_assignment_ids = sorted(
            assignment_id
            for assignment_id, evidence in assignments.items()
            if evidence["voiceProfileId"] is not None
            and evidence["assignmentState"] in {"selected", "locked"}
        )
        expected_intentionally_uncast = sorted(
            str(evidence["roleId"])
            for evidence in assignments.values()
            if evidence["assignmentState"] == "intentionally_uncast"
        )
        restricted_count = sum(
            evidence["rightsState"] == "restricted"
            for evidence in assignments.values()
            if evidence["assignmentId"] in selected_assignment_ids
        )
        ineligible_count = sum(
            evidence["rightsState"] in {"unknown", "prohibited"}
            for evidence in assignments.values()
            if evidence["assignmentId"] in selected_assignment_ids
        )
        unresolved_count = len(roles) - len(
            {str(assignments[assignment_id]["roleId"]) for assignment_id in selected_assignment_ids}
            | set(expected_intentionally_uncast)
        )
        if (
            assignment_ids != selected_assignment_ids
            or intentionally_uncast_ids != expected_intentionally_uncast
            or counts["productionRoles"] != len(roles)
            or counts["narratorRoles"] + counts["characterRoles"] != len(roles)
            or counts["finalCandidates"] != len(candidates)
            or counts["assignments"] != len(selected_assignment_ids)
            or counts["corrections"] != len(corrections)
            or snapshot.role_count != len(roles)
            or snapshot.assignment_count != len(selected_assignment_ids)
            or snapshot.unresolved_role_count != unresolved_count
            or snapshot.restricted_rights_count != restricted_count
            or snapshot.ineligible_rights_count != ineligible_count
        ):
            raise self._snapshot_manifest_error()

        latest = self._latest_snapshot(session, run.id)
        if latest is not None and latest.id == snapshot.id:
            if (
                set(roles)
                != set(
                    session.scalars(
                        select(ProductionRoleRow.id).where(
                            ProductionRoleRow.casting_run_id == run.id
                        )
                    )
                )
                or set(candidates)
                != {
                    value.id
                    for value in self._current_candidate_rows(
                        session,
                        run_id=run.id,
                    )
                }
                or set(assignments)
                != {
                    value.id
                    for value in self._effective_assignment_rows(
                        session,
                        run.id,
                    )
                }
                or set(corrections)
                != set(
                    session.scalars(
                        select(CastingCorrectionRow.id).where(
                            CastingCorrectionRow.casting_run_id == run.id
                        )
                    )
                )
            ):
                raise self._snapshot_manifest_error()
        return manifest

    def validated_snapshot_manifest(
        self,
        session: Session,
        *,
        snapshot: ApprovedCastSnapshotRow,
        run: CastingRunRow,
    ) -> dict[str, Any]:
        """Return an exact relationally verified snapshot manifest."""

        return self._validate_snapshot_manifest(
            session,
            snapshot=snapshot,
            run=run,
        )

    def validated_phase3a_gate_decisions(
        self,
        session: Session,
        *,
        snapshot: ApprovedCastSnapshotRow,
        run: CastingRunRow,
    ) -> dict[str, CastingGateDecisionRow] | None:
        """Return the exact current approved gate decisions, or fail closed.

        Phase 3B must not infer casting authority from mutable decision rows alone.
        This verifier binds every approval to the latest immutable eligible gate
        review, verifies its frozen blockers and warning acknowledgements, and
        rechecks the current Phase 2 and catalog identities. The caller separately
        validates the exact snapshot manifest and current assignment rights.
        """

        latest_snapshot = self._latest_snapshot(session, run.id)
        if (
            run.state != "succeeded"
            or snapshot.project_id != run.project_id
            or snapshot.casting_run_id != run.id
            or latest_snapshot is None
            or latest_snapshot.id != snapshot.id
        ):
            return None
        try:
            self.assert_evidence(session, run=run)
            self._assert_current_catalog_write_evidence(run)
        except ServiceError:
            return None
        validated: dict[str, CastingGateDecisionRow] = {}
        for gate_id in CASTING_GATE_IDS:
            review = self._latest_review(
                session,
                run_id=run.id,
                gate_id=gate_id,
            )
            decision = self._latest_decision(
                session,
                run_id=run.id,
                gate_id=gate_id,
            )
            if review is None or decision is None:
                return None
            warning_evidence = parse_json(review.warnings_json, {})
            warning_ids = warning_evidence.get("warningIds")
            frozen_blockers = warning_evidence.get("blockingReasonCodes")
            acknowledgements = parse_json(
                decision.warning_acknowledgements_json,
                None,
            )
            review_provenance = parse_json(review.provenance_json, None)
            decision_provenance = parse_json(decision.provenance_json, None)
            if (
                not isinstance(warning_ids, list)
                or not all(isinstance(value, str) for value in warning_ids)
                or not isinstance(frozen_blockers, list)
                or not all(isinstance(value, str) for value in frozen_blockers)
                or not isinstance(acknowledgements, list)
                or not all(isinstance(value, str) for value in acknowledgements)
            ):
                return None
            expected_required_gates = (
                list(CASTING_GATE_IDS[:2]) if gate_id == "complete_cast_review" else []
            )
            expected_review_fingerprint = request_fingerprint(
                {
                    "castingRunId": run.id,
                    "gateId": gate_id,
                    "snapshotId": snapshot.id,
                    "snapshotRevision": snapshot.revision,
                    "snapshotFingerprint": snapshot.snapshot_fingerprint,
                    "blockers": frozen_blockers,
                    "warnings": warning_ids,
                }
            )
            previous_decision = (
                session.get(CastingGateDecisionRow, decision.supersedes_decision_id)
                if decision.supersedes_decision_id is not None
                else None
            )
            request_supersedes_decision_id = (
                previous_decision.id
                if previous_decision is not None and previous_decision.gate_review_id == review.id
                else None
            )
            expected_request_fingerprint = request_fingerprint(
                {
                    "projectId": run.project_id,
                    "castingRunId": run.id,
                    "gateId": gate_id,
                    "decision": "approved",
                    "expectedRevision": review.revision,
                    "expectedEvidenceFingerprint": review.evidence_fingerprint,
                    "expectedRunFingerprint": snapshot.snapshot_fingerprint,
                    "expectedApprovedCastSnapshotId": snapshot.id,
                    "expectedApprovedCastSnapshotRevision": snapshot.revision,
                    "warningAcknowledgementIds": acknowledgements,
                    "rationale": decision.rationale,
                    "supersedesDecisionId": request_supersedes_decision_id,
                }
            )
            expected_review_provenance = {
                "origin": "system",
                "producerId": CASTING_PRODUCER_ID,
                "producerVersion": CASTING_PRODUCER_VERSION,
                "recordedAt": review.created_at,
                "inputFingerprint": snapshot.snapshot_fingerprint,
            }
            expected_decision_provenance = {
                "origin": "human",
                "producerId": "local_user",
                "producerVersion": "1.0.0",
                "recordedAt": decision.decided_at,
                "inputFingerprint": review.evidence_fingerprint,
                "requestFingerprint": expected_request_fingerprint,
            }
            if (
                review.project_id != run.project_id
                or review.casting_run_id != run.id
                or review.cast_snapshot_id != snapshot.id
                or review.gate_id != gate_id
                or not review.eligible
                or frozen_blockers
                or review.evidence_fingerprint != expected_review_fingerprint
                or parse_json(review.required_gate_decision_ids_json, None)
                != expected_required_gates
                or review_provenance != expected_review_provenance
                or decision.project_id != run.project_id
                or decision.casting_run_id != run.id
                or decision.cast_snapshot_id != snapshot.id
                or decision.gate_review_id != review.id
                or decision.gate_id != gate_id
                or decision.decision != "approved"
                or decision.evidence_fingerprint != review.evidence_fingerprint
                or decision.actor_id != "local_user"
                or decision.decided_at is None
                or decision.created_at != decision.decided_at
                or not decision.idempotency_key
                or decision_provenance != expected_decision_provenance
                or (
                    previous_decision is None
                    and (decision.revision != 1 or decision.supersedes_decision_id is not None)
                )
                or (
                    previous_decision is not None
                    and (
                        previous_decision.project_id != run.project_id
                        or previous_decision.casting_run_id != run.id
                        or previous_decision.gate_id != gate_id
                        or decision.revision != previous_decision.revision + 1
                        or decision.supersedes_decision_id != previous_decision.id
                    )
                )
                or not set(acknowledgements).issubset(warning_ids)
                or bool(set(warning_ids) - set(acknowledgements))
            ):
                return None
            validated[gate_id] = decision
        return validated

    def snapshot_assignment_evidence_is_current(
        self,
        session: Session,
        *,
        snapshot: ApprovedCastSnapshotRow,
        assignments: Sequence[CastAssignmentRow],
    ) -> bool:
        """Verify selected catalog leaves and temporal rights with bounded queries."""

        manifest = parse_json(snapshot.manifest_json, {})
        raw_evidence = manifest.get("assignmentEvidence")
        if not isinstance(raw_evidence, list):
            return False
        evidence_by_assignment = {
            value.get("assignmentId"): value
            for value in raw_evidence
            if isinstance(value, dict) and isinstance(value.get("assignmentId"), str)
        }
        voice_ids = {
            assignment.voice_profile_record_id
            for assignment in assignments
            if assignment.voice_profile_record_id is not None
        }
        voice_rows = list(
            session.scalars(select(VoiceProfileRow).where(VoiceProfileRow.id.in_(voice_ids)))
        )
        voices_by_id = {voice.id: voice for voice in voice_rows}
        provider_record_ids = {voice.provider_descriptor_id for voice in voice_rows}
        model_record_ids = {voice.model_descriptor_id for voice in voice_rows}
        providers_by_id = {
            provider.id: provider
            for provider in session.scalars(
                select(VoiceProviderDescriptorRow).where(
                    VoiceProviderDescriptorRow.id.in_(provider_record_ids)
                )
            )
        }
        models_by_id = {
            model.id: model
            for model in session.scalars(
                select(VoiceModelDescriptorRow).where(
                    VoiceModelDescriptorRow.id.in_(model_record_ids)
                )
            )
        }
        rights_rows = list(
            session.scalars(
                select(VoiceRightsRecordRow)
                .where(VoiceRightsRecordRow.voice_profile_record_id.in_(voice_ids))
                .order_by(
                    VoiceRightsRecordRow.voice_profile_record_id,
                    VoiceRightsRecordRow.revision.desc(),
                    VoiceRightsRecordRow.id.desc(),
                )
            )
        )
        latest_by_voice: dict[str, VoiceRightsRecordRow] = {}
        for rights in rights_rows:
            latest_by_voice.setdefault(rights.voice_profile_record_id, rights)
        if len(evidence_by_assignment) < len(assignments):
            return False
        for assignment in assignments:
            if assignment.voice_profile_record_id is None:
                return False
            voice = voices_by_id.get(assignment.voice_profile_record_id)
            current_rights = latest_by_voice.get(assignment.voice_profile_record_id)
            evidence = evidence_by_assignment.get(assignment.id)
            catalog_voice = next(
                (
                    value
                    for value in self.catalog.voices
                    if voice is not None and value.get("voiceProfileId") == voice.profile_id
                ),
                None,
            )
            catalog_rights = next(
                (
                    value
                    for value in self.catalog.rights
                    if voice is not None and value.get("voiceProfileId") == voice.profile_id
                ),
                None,
            )
            catalog_provider = next(
                (
                    value
                    for value in self.catalog.providers
                    if catalog_voice is not None
                    and value.get("providerId") == catalog_voice.get("providerId")
                ),
                None,
            )
            catalog_model = next(
                (
                    value
                    for value in self.catalog.models
                    if catalog_voice is not None
                    and value.get("providerId") == catalog_voice.get("providerId")
                    and value.get("modelId") == catalog_voice.get("modelId")
                ),
                None,
            )
            provider = (
                providers_by_id.get(voice.provider_descriptor_id) if voice is not None else None
            )
            model = models_by_id.get(voice.model_descriptor_id) if voice is not None else None
            governed_private_audition = (
                catalog_voice is not None
                and catalog_rights is not None
                and catalog_provider is not None
                and catalog_model is not None
                and governed_private_audition_binding_is_exact(
                    voice=catalog_voice,
                    rights=catalog_rights,
                    provider=catalog_provider,
                    model=catalog_model,
                )
            )
            if (
                voice is None
                or current_rights is None
                or evidence is None
                or catalog_voice is None
                or catalog_rights is None
                or catalog_provider is None
                or catalog_model is None
                or provider is None
                or model is None
                or evidence.get("voiceProfileId") != voice.profile_id
                or evidence.get("voiceProfileVersion") != voice.profile_version
                or evidence.get("voiceEvidenceFingerprint") != voice.profile_fingerprint
                or voice.profile_fingerprint != request_fingerprint(catalog_voice)
                or request_fingerprint(self._stored_voice_material(voice))
                != request_fingerprint(self._catalog_voice_material(catalog_voice))
                or self._stored_provider_material(provider) != catalog_provider
                or provider.descriptor_fingerprint
                != request_fingerprint(self._stored_provider_material(provider))
                or provider.runtime_availability != "available"
                or provider.catalog_availability != "available"
                or self._stored_model_material(model, provider) != catalog_model
                or model.descriptor_fingerprint
                != request_fingerprint(self._stored_model_material(model, provider))
                or model.availability != "available"
                or model.deprecated
                or evidence.get("rightsRecordId") != current_rights.rights_record_id
                or evidence.get("rightsRecordRevision") != current_rights.revision
                or evidence.get("rightsEvidenceFingerprint") != current_rights.rights_fingerprint
                or evidence.get("rightsState") != current_rights.rights_state
                or assignment.rights_state != current_rights.rights_state
                or current_rights.rights_fingerprint != request_fingerprint(catalog_rights)
                or request_fingerprint(self._stored_rights_material(current_rights))
                != request_fingerprint(self._catalog_rights_material(catalog_rights))
                or current_rights.rights_state not in {"verified", "restricted"}
                or current_rights.commercial_use_status in {"unknown", "prohibited"}
                or (
                    current_rights.consent_status in {"missing", "unknown", "prohibited"}
                    and not governed_private_audition
                )
                or (
                    current_rights.human_verification_status
                    not in {"verified", "not_required_fixture"}
                    and not governed_private_audition
                )
                or not rights_record_is_current(self._stored_rights_material(current_rights))
            ):
                return False
        return True

    def _append_gate_review(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        snapshot: ApprovedCastSnapshotRow,
        gate_id: str,
        blockers: Sequence[str],
        warnings: Sequence[str],
        now: str,
    ) -> CastingGateReviewRow:
        prior_review = self._latest_review(
            session,
            run_id=run.id,
            gate_id=gate_id,
        )
        review_revision = 1 if prior_review is None else prior_review.revision + 1
        evidence_fingerprint = request_fingerprint(
            {
                "castingRunId": run.id,
                "gateId": gate_id,
                "snapshotId": snapshot.id,
                "snapshotRevision": snapshot.revision,
                "snapshotFingerprint": snapshot.snapshot_fingerprint,
                "blockers": list(blockers),
                "warnings": list(warnings),
            }
        )
        row = CastingGateReviewRow(
            id=new_id(),
            project_id=run.project_id,
            casting_run_id=run.id,
            cast_snapshot_id=snapshot.id,
            gate_id=gate_id,
            revision=review_revision,
            eligible=not blockers,
            evidence_fingerprint=evidence_fingerprint,
            required_gate_decision_ids_json=canonical_json(
                list(CASTING_GATE_IDS[:2]) if gate_id == "complete_cast_review" else []
            ),
            warnings_json=canonical_json(
                {
                    "warningIds": list(warnings),
                    "blockingReasonCodes": list(blockers),
                }
            ),
            provenance_json=canonical_json(
                {
                    "origin": "system",
                    "producerId": CASTING_PRODUCER_ID,
                    "producerVersion": CASTING_PRODUCER_VERSION,
                    "recordedAt": now,
                    "inputFingerprint": snapshot.snapshot_fingerprint,
                }
            ),
            created_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def _refresh_gate_review_evidence(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        gate_id: str,
    ) -> CastingGateReviewRow | None:
        snapshot = self._latest_snapshot(session, run.id)
        if snapshot is None:
            return None
        current = self._latest_review(
            session,
            run_id=run.id,
            gate_id=gate_id,
        )
        if (
            current is not None
            and current.project_id == run.project_id
            and current.cast_snapshot_id == snapshot.id
        ):
            return current
        blockers, warnings = self._review_blockers(
            session,
            run=run,
            gate_id=gate_id,
            include_gate_prerequisites=False,
        )
        return self._append_gate_review(
            session,
            run=run,
            snapshot=snapshot,
            gate_id=gate_id,
            blockers=blockers,
            warnings=warnings,
            now=utc_now(),
        )

    def _publish_cast_snapshot(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        now: str,
    ) -> ApprovedCastSnapshotRow:
        latest = self._latest_snapshot(session, run.id)
        revision = 1 if latest is None else latest.revision + 1
        assignments = self._effective_assignment_rows(session, run.id)
        actual_assignments = [
            value
            for value in assignments
            if value.voice_profile_record_id is not None
            and value.assignment_state in {"selected", "locked"}
        ]
        roles = list(
            session.scalars(
                select(ProductionRoleRow)
                .where(ProductionRoleRow.casting_run_id == run.id)
                .order_by(
                    ProductionRoleRow.ordinal,
                    ProductionRoleRow.id,
                )
            )
        )
        role_count = len(roles)
        intentionally_uncast_role_ids = {
            value.role_id
            for value in assignments
            if value.assignment_state == "intentionally_uncast"
        }
        resolved_role_ids = {
            value.role_id for value in actual_assignments
        } | intentionally_uncast_role_ids
        unresolved_role_count = role_count - len(resolved_role_ids)
        restricted = [value for value in actual_assignments if value.rights_state == "restricted"]
        ineligible = [
            value for value in actual_assignments if value.rights_state in {"unknown", "prohibited"}
        ]
        conflicts = list(
            session.scalars(
                select(CastingConflictRow)
                .where(
                    CastingConflictRow.casting_run_id == run.id,
                    CastingConflictRow.status == "open",
                )
                .order_by(CastingConflictRow.id)
            )
        )
        unresolved_conflicts = [
            value
            for value in conflicts
            if self._current_conflict_disposition(session, value) is None
        ]
        candidates = self._current_candidate_rows(
            session,
            run_id=run.id,
        )
        corrections = list(
            session.scalars(
                select(CastingCorrectionRow)
                .where(CastingCorrectionRow.casting_run_id == run.id)
                .order_by(
                    CastingCorrectionRow.revision,
                    CastingCorrectionRow.id,
                )
            )
        )
        narrator_count = sum(
            value.role_type in {"primary_narrator", "secondary_narrator"} for value in roles
        )
        conflict_count = len(conflicts)
        counts = {
            "productionRoles": role_count,
            "narratorRoles": narrator_count,
            "characterRoles": role_count - narrator_count,
            "preReductionCandidates": role_count
            * min(
                50,
                self._catalog_voice_count(
                    session,
                    run.catalog_revision_id,
                ),
            ),
            "finalCandidates": len(candidates),
            "conflicts": conflict_count,
            "assignments": len(actual_assignments),
            "corrections": len(corrections),
        }
        review_evidence = {
            gate_id: self._review_blockers(
                session,
                run=run,
                gate_id=gate_id,
                include_gate_prerequisites=False,
                validate_current_cast_snapshot=False,
            )
            for gate_id in CASTING_GATE_IDS
        }
        manifest = {
            "productionRoleEvidence": [
                {
                    "roleId": value.id,
                    "effectiveFingerprint": (
                        self._effective_role_fingerprint(
                            session,
                            value,
                        )
                    ),
                }
                for value in roles
            ],
            "candidateEvidence": [
                {
                    "candidateId": value.id,
                    "roleId": value.role_id,
                    "outputFingerprint": value.output_fingerprint,
                }
                for value in candidates
            ],
            "assignmentEvidence": [
                self._assignment_snapshot_evidence(
                    session,
                    assignment=value,
                    include_current_invalidation=True,
                )
                for value in assignments
            ],
            "correctionEvidence": [
                {
                    "correctionId": value.id,
                    "correctionFingerprint": (self._validated_correction_fingerprint(value)),
                }
                for value in corrections
            ],
            "unresolvedConflictEvidence": [
                {
                    "conflictId": value.id,
                    "evidenceFingerprint": (value.evidence_fingerprint),
                }
                for value in unresolved_conflicts
            ],
            "assignmentIds": sorted(value.id for value in actual_assignments),
            "intentionallyUncastRoleIds": sorted(intentionally_uncast_role_ids),
            "unresolvedConflictIds": [value.id for value in unresolved_conflicts],
            "counts": counts,
            "reviewEligibleAtPublication": all(
                not blockers for blockers, _warnings in review_evidence.values()
            ),
        }
        fingerprint = request_fingerprint(
            {
                "castingRunId": run.id,
                "revision": revision,
                "phase2SnapshotFingerprint": (run.analysis_snapshot_fingerprint),
                "catalogFingerprint": run.catalog_fingerprint,
                "castingProfileFingerprint": (run.casting_profile_fingerprint),
                "effectiveCorrectionSetFingerprint": (run.effective_correction_set_fingerprint),
                "manifest": manifest,
            }
        )
        snapshot = ApprovedCastSnapshotRow(
            id=new_id(),
            project_id=run.project_id,
            casting_run_id=run.id,
            revision=revision,
            phase2_snapshot_fingerprint=run.analysis_snapshot_fingerprint,
            catalog_revision_id=run.catalog_revision_id,
            catalog_fingerprint=run.catalog_fingerprint,
            casting_profile_fingerprint=run.casting_profile_fingerprint,
            effective_correction_set_fingerprint=(run.effective_correction_set_fingerprint),
            role_count=role_count,
            assignment_count=len(actual_assignments),
            unresolved_role_count=unresolved_role_count,
            restricted_rights_count=len(restricted),
            ineligible_rights_count=len(ineligible),
            snapshot_fingerprint=fingerprint,
            manifest_json=canonical_json(manifest),
            created_at=now,
        )
        session.add(snapshot)
        session.flush()
        for gate_id in CASTING_GATE_IDS:
            blockers, warnings = review_evidence[gate_id]
            self._append_gate_review(
                session,
                run=run,
                snapshot=snapshot,
                gate_id=gate_id,
                blockers=blockers,
                warnings=warnings,
                now=now,
            )
        return snapshot

    def assert_evidence(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        expected_run_fingerprint: str | None = None,
        expected_catalog_revision_id: str | None = None,
        expected_catalog_fingerprint: str | None = None,
        expected_snapshot_id: str | None = None,
        expected_snapshot_revision: int | None = None,
        expected_snapshot_fingerprint: str | None = None,
        validate_current_cast_snapshot: bool = True,
    ) -> None:
        catalog_row = session.get(
            VoiceCatalogRevisionRow,
            run.catalog_revision_id,
        )
        expected_gate_ids = parse_json(run.phase2_gate_decision_ids_json, {})
        (
            _analysis_run,
            current_snapshot,
            current_gate_ids,
            current_character_registry_fingerprint,
            current_gate_evidence_fingerprint,
        ) = self._phase_2_evidence(
            session,
            project_id=run.project_id,
            expected_analysis_run_id=run.analysis_run_id,
            expected_snapshot_id=run.analysis_snapshot_id,
            expected_snapshot_revision=run.analysis_snapshot_revision,
            expected_snapshot_fingerprint=run.analysis_snapshot_fingerprint,
            expected_correction_set_fingerprint=run.analysis_correction_set_fingerprint,
            expected_import_review_decision_id=run.import_review_decision_id,
            expected_analysis_gate_decision_ids=expected_gate_ids,
        )
        current_cast_snapshot = self._latest_snapshot(session, run.id)
        if current_cast_snapshot is not None and validate_current_cast_snapshot:
            self._validate_snapshot_manifest(
                session,
                snapshot=current_cast_snapshot,
                run=run,
            )
        effective_run_fingerprint = (
            current_cast_snapshot.snapshot_fingerprint
            if current_cast_snapshot is not None
            else run.input_fingerprint
        )
        if (
            catalog_row is None
            or current_gate_ids != expected_gate_ids
            or current_character_registry_fingerprint != run.character_registry_fingerprint
            or current_gate_evidence_fingerprint != run.phase2_gate_evidence_fingerprint
            or (
                expected_run_fingerprint is not None
                and effective_run_fingerprint != expected_run_fingerprint
            )
            or (
                expected_catalog_revision_id is not None
                and catalog_row.catalog_id != expected_catalog_revision_id
            )
            or (
                expected_catalog_fingerprint is not None
                and run.catalog_fingerprint != expected_catalog_fingerprint
            )
            or (
                expected_snapshot_id is not None
                and run.analysis_snapshot_id != expected_snapshot_id
            )
            or (
                expected_snapshot_revision is not None
                and run.analysis_snapshot_revision != expected_snapshot_revision
            )
            or (
                expected_snapshot_fingerprint is not None
                and run.analysis_snapshot_fingerprint != expected_snapshot_fingerprint
            )
            or current_snapshot.id != run.analysis_snapshot_id
            or current_snapshot.fingerprint != run.analysis_snapshot_fingerprint
        ):
            raise ServiceError(
                409,
                "CASTING_EVIDENCE_STALE",
                "Casting evidence changed; refresh before continuing.",
            )

    def _prerequisites_wire(
        self,
        run: CastingRunRow,
    ) -> dict[str, Any]:
        ids = parse_json(run.phase2_gate_decision_ids_json, {})
        return {
            "projectId": run.project_id,
            "sourceDocumentId": run.source_document_id,
            "sourceRevision": run.source_revision,
            "extractionId": run.extraction_id,
            "extractionRevision": run.extraction_revision,
            "extractedTextSha256": run.extracted_text_sha256,
            "importReviewDecisionId": run.import_review_decision_id,
            "analysisRunId": run.analysis_run_id,
            "analysisSnapshotId": run.analysis_snapshot_id,
            "analysisSnapshotRevision": run.analysis_snapshot_revision,
            "analysisSnapshotFingerprint": (run.analysis_snapshot_fingerprint),
            "analysisCorrectionSetFingerprint": (run.analysis_correction_set_fingerprint),
            "characterRegistryFingerprint": (run.character_registry_fingerprint),
            "phase2GateDecisionIds": {
                wire_key: ids[gate_id] for gate_id, wire_key in _PHASE_2_GATE_KEYS.items()
            },
            "evidenceFingerprint": run.phase2_gate_evidence_fingerprint,
        }

    def _snapshot_wire(
        self,
        session: Session,
        snapshot: ApprovedCastSnapshotRow,
    ) -> dict[str, Any]:
        run = session.get(CastingRunRow, snapshot.casting_run_id)
        if run is None:
            raise ServiceError(
                500,
                "CASTING_SNAPSHOT_INVALID",
                "The casting snapshot evidence is unavailable.",
            )
        catalog = self._catalog_identity(session, run.catalog_revision_id)
        manifest = self._validate_snapshot_manifest(
            session,
            snapshot=snapshot,
            run=run,
        )
        counts = manifest["counts"]
        review_eligible = bool(
            manifest.get(
                "reviewEligibleAtPublication",
                False,
            )
        )
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "snapshotId": snapshot.id,
            "castingRunId": snapshot.casting_run_id,
            "projectId": snapshot.project_id,
            "revision": snapshot.revision,
            "phase2SnapshotFingerprint": (snapshot.phase2_snapshot_fingerprint),
            "catalogRevisionId": catalog.catalog_id,
            "catalogFingerprint": snapshot.catalog_fingerprint,
            "castingProfileFingerprint": (snapshot.casting_profile_fingerprint),
            "effectiveCorrectionSetFingerprint": (snapshot.effective_correction_set_fingerprint),
            "assignmentIds": manifest.get("assignmentIds", []),
            "intentionallyUncastRoleIds": manifest.get(
                "intentionallyUncastRoleIds",
                [],
            ),
            "unresolvedConflictIds": manifest.get(
                "unresolvedConflictIds",
                [],
            ),
            "counts": counts,
            "snapshotFingerprint": snapshot.snapshot_fingerprint,
            "reviewEligible": review_eligible,
            "createdAt": snapshot.created_at,
            "immutable": True,
        }

    def run_dict(
        self,
        session: Session,
        run: CastingRunRow,
    ) -> dict[str, Any]:
        job = session.get(JobRow, run.job_id)
        if job is None:
            raise ServiceError(
                500,
                "CASTING_JOB_UNAVAILABLE",
                "The casting job is unavailable.",
            )
        catalog = self._catalog_identity(session, run.catalog_revision_id)
        profile_id = {
            CASTING_PROFILE_FINGERPRINT: CASTING_PROFILE_ID,
            LEGACY_CASTING_PROFILE_FINGERPRINT: LEGACY_CASTING_PROFILE_ID,
        }.get(run.casting_profile_fingerprint)
        if profile_id is None:
            raise ServiceError(
                500,
                "CASTING_PROFILE_EVIDENCE_INVALID",
                "The persisted casting profile is not recognized.",
            )
        snapshot = self._latest_snapshot(session, run.id)
        checkpoint_row = session.scalar(
            select(JobCheckpointRow)
            .where(JobCheckpointRow.job_id == job.id)
            .order_by(
                JobCheckpointRow.attempt.desc(),
                JobCheckpointRow.sequence.desc(),
            )
            .limit(1)
        )
        role_count = int(
            session.scalar(
                select(func.count())
                .select_from(ProductionRoleRow)
                .where(ProductionRoleRow.casting_run_id == run.id)
            )
            or 0
        )
        narrator_count = int(
            session.scalar(
                select(func.count())
                .select_from(ProductionRoleRow)
                .where(
                    ProductionRoleRow.casting_run_id == run.id,
                    ProductionRoleRow.role_type.in_(("primary_narrator", "secondary_narrator")),
                )
            )
            or 0
        )
        candidate_count = len(
            self._current_candidate_rows(
                session,
                run_id=run.id,
            )
        )
        conflict_count = int(
            session.scalar(
                select(func.count())
                .select_from(CastingConflictRow)
                .where(
                    CastingConflictRow.casting_run_id == run.id,
                    CastingConflictRow.status == "open",
                )
            )
            or 0
        )
        assignment_count = len(
            [
                assignment
                for assignment in self._effective_assignment_rows(
                    session,
                    run.id,
                )
                if assignment.voice_profile_record_id is not None
                and assignment.assignment_state in {"selected", "locked"}
            ]
        )
        correction_count = int(
            session.scalar(
                select(func.count())
                .select_from(CastingCorrectionRow)
                .where(CastingCorrectionRow.casting_run_id == run.id)
            )
            or 0
        )
        status = job.state
        if status == "cancel_requested":
            status = "running"
        if status not in {
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
        }:
            status = "interrupted"
        summary = (
            {
                "productionRoles": role_count,
                "narratorRoles": narrator_count,
                "characterRoles": role_count - narrator_count,
                "preReductionCandidates": role_count
                * min(50, self._catalog_voice_count(session, run.catalog_revision_id)),
                "finalCandidates": candidate_count,
                "conflicts": conflict_count,
                "assignments": assignment_count,
                "corrections": correction_count,
            }
            if snapshot is not None
            else None
        )
        retry_classification = (
            "retry_exhausted"
            if job.state == "failed" and job.current_attempt >= 3
            else "retryable"
            if bool(job.error_retryable) or job.state == "interrupted"
            else "not_retryable"
        )
        latest_stage = session.scalar(
            select(JobEventRow.stage)
            .where(
                JobEventRow.job_id == job.id,
                JobEventRow.stage.in_(CASTING_JOB_STAGES),
            )
            .order_by(JobEventRow.sequence.desc())
            .limit(1)
        )
        current_stage = (
            "complete"
            if status == "succeeded"
            else "queued"
            if latest_stage is None
            else latest_stage
        )
        result = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "castingRunId": run.id,
            "projectId": run.project_id,
            "prerequisites": self._prerequisites_wire(run),
            "profile": {
                "profileId": profile_id,
                "fingerprint": run.casting_profile_fingerprint,
            },
            "producerId": run.producer_id,
            "catalogRevisionId": catalog.catalog_id,
            "catalogFingerprint": run.catalog_fingerprint,
            "effectiveCorrectionSetFingerprint": (
                snapshot.effective_correction_set_fingerprint
                if snapshot is not None
                else run.effective_correction_set_fingerprint
            ),
            "inputFingerprint": run.input_fingerprint,
            "outputFingerprint": (snapshot.snapshot_fingerprint if snapshot is not None else None),
            "idempotencyFingerprint": run.run_fingerprint,
            "jobId": run.job_id,
            "status": status,
            "currentStage": current_stage,
            "progress": job.progress / 1_000_000,
            "checkpoint": (
                {
                    "checkpointId": stable_id(
                        "casting-job-checkpoint",
                        checkpoint_row.job_id,
                        checkpoint_row.attempt,
                        checkpoint_row.sequence,
                    ),
                    "stage": CASTING_JOB_STAGES[6],
                    "fingerprint": checkpoint_row.payload_sha256,
                    "recordedAt": checkpoint_row.created_at,
                }
                if checkpoint_row is not None
                else None
            ),
            "attempt": job.current_attempt,
            "retryPolicy": {
                "maxAttempts": 3,
                "retryableFailureCodes": ["CASTING_FAILED"],
            },
            "failurePolicy": "fail_closed_preserve_effective_cast_snapshot",
            "resumeOfCastingRunId": None,
            "retryOfCastingRunId": None,
            "retryClassification": retry_classification,
            "cancellationRequested": job.cancellation_requested,
            "warnings": parse_json(run.warnings_json, []),
            "summary": summary,
            "approvedCastSnapshot": (
                self._snapshot_wire(session, snapshot) if snapshot is not None else None
            ),
            "createdAt": run.created_at,
            "updatedAt": job.updated_at,
            "completedAt": run.published_at,
            "failure": (
                {
                    "code": job.error_code,
                    "redactedMessage": job.error_message,
                    "retryable": bool(job.error_retryable),
                    "redacted": True,
                }
                if job.error_code is not None
                else None
            ),
        }
        return result

    def get_run(
        self,
        *,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            return self.run_dict(session, run)

    def list_runs(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        with self.database.session() as session:
            self.projects.require_project(session, project_id)
            rows = list(
                session.scalars(
                    select(CastingRunRow)
                    .where(CastingRunRow.project_id == project_id)
                    .order_by(
                        CastingRunRow.created_at.desc(),
                        CastingRunRow.id.desc(),
                    )
                )
            )
            page, next_cursor, total = _bounded_page(
                rows,
                binding=request_fingerprint(
                    {
                        "type": "casting-runs",
                        "projectId": project_id,
                        "rowIds": [value.id for value in rows],
                    }
                ),
                cursor=cursor,
                limit=limit,
            )
            return (
                [self.run_dict(session, row) for row in page],
                next_cursor,
                total,
            )

    def _voice_wire(
        self,
        profile_id: str,
    ) -> dict[str, Any]:
        voice = next(
            value for value in self.catalog.voices if value["voiceProfileId"] == profile_id
        )
        rights = next(
            value for value in self.catalog.rights if value["voiceProfileId"] == profile_id
        )
        return voice | {"rights": rights}

    def _role_revision(
        self,
        session: Session,
        role_id: str,
    ) -> int:
        return 1 + int(
            session.scalar(
                select(func.count())
                .select_from(CastingCorrectionRow)
                .where(CastingCorrectionRow.role_id == role_id)
            )
            or 0
        )

    def _effective_role_fingerprint(
        self,
        session: Session,
        role: ProductionRoleRow,
    ) -> str:
        self._validate_role_base_fingerprint(session, role)
        assignment = self._latest_assignment(
            session,
            role_id=role.id,
            include_proposal=False,
        )
        corrections = list(
            session.scalars(
                select(CastingCorrectionRow)
                .where(CastingCorrectionRow.role_id == role.id)
                .order_by(CastingCorrectionRow.revision, CastingCorrectionRow.id)
            )
        )
        return request_fingerprint(
            {
                "roleFingerprint": role.role_fingerprint,
                "assignmentId": assignment.id if assignment is not None else None,
                "assignmentState": (
                    assignment.assignment_state if assignment is not None else None
                ),
                "correctionFingerprints": [
                    self._validated_correction_fingerprint(value) for value in corrections
                ],
            }
        )

    def _validate_role_base_fingerprint(
        self,
        session: Session,
        role: ProductionRoleRow,
    ) -> None:
        run = session.get(CastingRunRow, role.casting_run_id)
        if run is None or run.project_id != role.project_id:
            raise ServiceError(
                500,
                "CASTING_ROLE_EVIDENCE_INVALID",
                "Production-role evidence failed integrity validation.",
            )
        provenance = parse_json(role.provenance_json, None)
        warnings = parse_json(role.warnings_json, None)
        chapter_range = parse_json(role.chapter_range_json, None)
        scene_range = parse_json(role.scene_range_json, None)
        languages = parse_json(role.language_requirements_json, None)
        requirements = parse_json(role.performance_requirements_json, None)
        if (
            not isinstance(provenance, dict)
            or not isinstance(warnings, list)
            or not isinstance(chapter_range, dict)
            or not isinstance(scene_range, dict)
            or not isinstance(languages, list)
            or not isinstance(requirements, dict)
        ):
            raise ServiceError(
                500,
                "CASTING_ROLE_EVIDENCE_INVALID",
                "Production-role evidence failed integrity validation.",
            )
        fingerprint_provenance = (
            {key: value for key, value in provenance.items() if key != "recordedAt"}
            if role.role_type == "custom"
            else provenance
        )
        values: dict[str, Any] = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "roleId": role.id,
            "projectId": role.project_id,
            "roleType": role.role_type,
            "analysisEntityId": role.phase2_entity_id,
            "effectiveDisplayLabel": role.effective_display_label,
            "analysisRunId": role.analysis_run_id,
            "analysisSnapshotId": role.analysis_snapshot_id,
            "analysisSnapshotFingerprint": run.analysis_snapshot_fingerprint,
            "dialogueLineCount": role.dialogue_line_count,
            "narrationSpanCount": role.narration_span_count,
            "approximateWordCount": role.approximate_word_count,
            "chapterRange": chapter_range,
            "sceneRange": scene_range,
            "languageRequirements": languages,
            "performanceRequirements": requirements,
            "warnings": warnings,
            "provenance": fingerprint_provenance,
            "status": "active",
            "revision": 1,
        }
        if role.role_type not in {
            "primary_narrator",
            "secondary_narrator",
        }:
            values.update(
                {
                    "characterId": role.character_id,
                    "roleImportance": role.role_importance,
                    "unresolvedMaterialExplicitlyRepresented": (
                        role.role_type == "unresolved_speaker"
                    ),
                }
            )
        expected_database_status = (
            "unresolved" if role.role_type == "unresolved_speaker" else "active"
        )
        if (
            role.status != expected_database_status
            or request_fingerprint(values) != role.role_fingerprint
        ):
            raise ServiceError(
                500,
                "CASTING_ROLE_EVIDENCE_INVALID",
                "Production-role evidence failed integrity validation.",
            )

    def _role_wire(
        self,
        session: Session,
        role: ProductionRoleRow,
    ) -> dict[str, Any]:
        self._validate_role_base_fingerprint(session, role)
        assignment = self._latest_assignment(
            session,
            role_id=role.id,
            include_proposal=False,
        )
        label = role.effective_display_label
        requirements = parse_json(role.performance_requirements_json, {})
        corrections = list(
            session.scalars(
                select(CastingCorrectionRow)
                .where(CastingCorrectionRow.role_id == role.id)
                .order_by(CastingCorrectionRow.revision, CastingCorrectionRow.id)
            )
        )
        for correction in corrections:
            value = self._validated_correction_value(correction)
            if correction.kind == "change_role_label":
                label = str(value.get("effectiveDisplayLabel", value.get("label", label)))
            elif correction.kind in {
                "change_requirement",
                "change_casting_requirement",
            }:
                requirements = value.get("requirement", value)
        language_requirements = parse_json(
            role.language_requirements_json,
            [],
        )
        effective_language = requirements.get("language")
        if isinstance(effective_language, str) and effective_language:
            language_requirements = [effective_language]
        status = role.status
        if assignment is not None and assignment.assignment_state == "intentionally_uncast":
            status = "intentionally_uncast"
        chapter_range = parse_json(role.chapter_range_json, {})
        scene_range = parse_json(role.scene_range_json, {})
        range_value = {
            "firstChapterOrdinal": chapter_range.get("firstOrdinal"),
            "lastChapterOrdinal": chapter_range.get("lastOrdinal"),
            "firstSceneOrdinal": scene_range.get("firstOrdinal"),
            "lastSceneOrdinal": scene_range.get("lastOrdinal"),
        }
        result: dict[str, Any] = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "roleId": role.id,
            "projectId": role.project_id,
            "revision": self._role_revision(session, role.id),
            "roleType": role.role_type,
            "phase2EntityId": role.phase2_entity_id,
            "effectiveDisplayLabel": label,
            "analysisRunId": role.analysis_run_id,
            "analysisSnapshotId": role.analysis_snapshot_id,
            "analysisSnapshotRevision": self.require_run(
                session,
                project_id=role.project_id,
                run_id=role.casting_run_id,
            ).analysis_snapshot_revision,
            "analysisSnapshotFingerprint": self.require_run(
                session,
                project_id=role.project_id,
                run_id=role.casting_run_id,
            ).analysis_snapshot_fingerprint,
            "dialogueLineCount": role.dialogue_line_count,
            "narrationSpanCount": role.narration_span_count,
            "approximateWordCount": role.approximate_word_count,
            "range": range_value,
            "languageRequirements": language_requirements,
            "performanceRequirements": requirements,
            "warnings": parse_json(role.warnings_json, []),
            "provenance": parse_json(role.provenance_json, {}),
            "status": status,
            "effectiveFingerprint": self._effective_role_fingerprint(
                session,
                role,
            ),
        }
        if role.role_type in {"primary_narrator", "secondary_narrator"}:
            result["narratorKind"] = (
                "primary" if role.role_type == "primary_narrator" else "secondary"
            )
        else:
            result.update(
                {
                    "characterId": role.character_id,
                    "roleImportance": (
                        role.role_importance
                        or (
                            "unresolved" if role.role_type == "unresolved_speaker" else "supporting"
                        )
                    ),
                    "unresolvedMaterialExplicitlyRepresented": (
                        role.role_type == "unresolved_speaker"
                    ),
                }
            )
        return result

    def list_roles(
        self,
        *,
        project_id: str,
        run_id: str,
        cursor: str | None,
        limit: int,
        evidence: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        self._latch_external_assignment_invalidations_for_read(
            project_id=project_id,
            run_id=run_id,
            evidence=evidence,
        )
        with self.database.session() as session:
            run = self._require_assignment_read_run(
                session,
                project_id=project_id,
                run_id=run_id,
                evidence=evidence,
            )
            rows = list(
                session.scalars(
                    select(ProductionRoleRow)
                    .where(ProductionRoleRow.casting_run_id == run.id)
                    .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
                )
            )
            page, next_cursor, total = _bounded_page(
                rows,
                binding=request_fingerprint(
                    {
                        "type": "casting-roles",
                        "runId": run.id,
                        "rowIds": [value.id for value in rows],
                    }
                ),
                cursor=cursor,
                limit=limit,
            )
            return (
                [self._role_wire(session, row) for row in page],
                next_cursor,
                total,
            )

    def create_custom_role(
        self,
        *,
        project_id: str,
        run_id: str,
        definition_id: str,
        label: str,
        performance_requirements: dict[str, Any],
        reason: str,
        expected_run_fingerprint: str,
        expected_catalog_revision_id: str,
        expected_catalog_fingerprint: str,
        expected_snapshot_id: str,
        expected_snapshot_revision: int,
        expected_snapshot_fingerprint: str,
        expected_correction_set_fingerprint: str,
        expected_casting_profile_fingerprint: str,
        idempotency_key: str,
    ) -> tuple[
        dict[str, Any],
        list[str],
        dict[str, Any],
        list[dict[str, Any]],
    ]:
        definition_material = {
            "projectId": project_id,
            "castingRunId": run_id,
            "definitionId": definition_id,
            "label": label,
            "performanceRequirements": performance_requirements,
            "reason": reason,
            "contentSource": "explicit_content_free",
        }
        definition_fingerprint = request_fingerprint(definition_material)
        request_hash = request_fingerprint(
            definition_material
            | {
                "expectedRunFingerprint": expected_run_fingerprint,
                "expectedCatalogRevisionId": expected_catalog_revision_id,
                "expectedCatalogFingerprint": expected_catalog_fingerprint,
                "expectedSnapshotId": expected_snapshot_id,
                "expectedSnapshotRevision": expected_snapshot_revision,
                "expectedSnapshotFingerprint": expected_snapshot_fingerprint,
                "expectedCorrectionSetFingerprint": (expected_correction_set_fingerprint),
                "expectedCastingProfileFingerprint": (expected_casting_profile_fingerprint),
            }
        )
        scope = f"create_custom_production_role:{project_id}:{run_id}"
        role_id = stable_id(
            "phase3a-custom-production-role",
            project_id,
            run_id,
            definition_id,
        )
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            replay = session.get(
                IdempotencyRow,
                {"scope": scope, "key": idempotency_key},
            )
            if replay is not None:
                if replay.request_hash != request_hash or replay.resource_id != role_id:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was used for another custom role.",
                    )
                replayed_role = session.get(ProductionRoleRow, replay.resource_id)
                if replayed_role is None or replayed_role.casting_run_id != run.id:
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved custom production role is unavailable.",
                    )
                return (
                    self._role_wire(session, replayed_role),
                    list(CASTING_GATE_IDS),
                    self.run_dict(session, run),
                    self._reviews_wire(session, run),
                )
            if run.state != "succeeded":
                raise ServiceError(
                    409,
                    "CASTING_RUN_NOT_SUCCEEDED",
                    "Custom roles can be added only to a succeeded casting run.",
                )
            self.assert_evidence(
                session,
                run=run,
                expected_run_fingerprint=expected_run_fingerprint,
                expected_catalog_revision_id=expected_catalog_revision_id,
                expected_catalog_fingerprint=expected_catalog_fingerprint,
                expected_snapshot_id=expected_snapshot_id,
                expected_snapshot_revision=expected_snapshot_revision,
                expected_snapshot_fingerprint=expected_snapshot_fingerprint,
            )
            if (
                run.effective_correction_set_fingerprint != expected_correction_set_fingerprint
                or run.casting_profile_fingerprint != expected_casting_profile_fingerprint
                or self.catalog.revision_id != expected_catalog_revision_id
                or self.catalog.fingerprint != expected_catalog_fingerprint
            ):
                raise ServiceError(
                    409,
                    "CASTING_CUSTOM_ROLE_EVIDENCE_STALE",
                    "Casting evidence changed; refresh before adding the custom role.",
                )
            existing_role = session.get(ProductionRoleRow, role_id)
            if existing_role is not None:
                provenance = parse_json(existing_role.provenance_json, {})
                if (
                    existing_role.casting_run_id != run.id
                    or existing_role.role_type != "custom"
                    or provenance.get("sourceRevisionId") != definition_id
                    or provenance.get("inputFingerprint") != definition_fingerprint
                ):
                    raise ServiceError(
                        409,
                        "CASTING_CUSTOM_ROLE_DEFINITION_CONFLICT",
                        "That custom-role definition ID already identifies another role.",
                    )
                session.add(
                    IdempotencyRow(
                        scope=scope,
                        key=idempotency_key,
                        request_hash=request_hash,
                        resource_id=existing_role.id,
                        created_at=utc_now(),
                    )
                )
                session.flush()
                return (
                    self._role_wire(session, existing_role),
                    list(CASTING_GATE_IDS),
                    self.run_dict(session, run),
                    self._reviews_wire(session, run),
                )
            role_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(ProductionRoleRow)
                    .where(ProductionRoleRow.casting_run_id == run.id)
                )
                or 0
            )
            if role_count >= MAX_PRODUCTION_ROLES:
                raise ServiceError(
                    422,
                    "CASTING_ROLE_LIMIT_EXCEEDED",
                    "The casting run has reached its production-role limit.",
                )
            max_ordinal = session.scalar(
                select(func.max(ProductionRoleRow.ordinal)).where(
                    ProductionRoleRow.casting_run_id == run.id
                )
            )
            ordinal = int(max_ordinal if max_ordinal is not None else -1) + 1
            now = utc_now()
            role_values, custom_candidate_input_fingerprint = _custom_role_evidence(
                project_id=project_id,
                run_id=run.id,
                definition_id=definition_id,
                definition_fingerprint=definition_fingerprint,
                label=label,
                performance_requirements=(performance_requirements),
                reason=reason,
                analysis_run_id=run.analysis_run_id,
                analysis_snapshot_id=run.analysis_snapshot_id,
                analysis_snapshot_fingerprint=(run.analysis_snapshot_fingerprint),
                casting_run_input_fingerprint=(run.input_fingerprint),
                catalog_fingerprint=run.catalog_fingerprint,
                casting_profile_fingerprint=(run.casting_profile_fingerprint),
                recorded_at=now,
            )
            provenance = dict(role_values["provenance"])
            warnings = list(role_values["warnings"])
            role_row = ProductionRoleRow(
                id=role_id,
                project_id=project_id,
                casting_run_id=run.id,
                ordinal=ordinal,
                role_type="custom",
                phase2_entity_id=None,
                character_id=None,
                role_importance="supporting",
                effective_display_label=label,
                analysis_run_id=run.analysis_run_id,
                analysis_snapshot_id=run.analysis_snapshot_id,
                dialogue_line_count=0,
                narration_span_count=0,
                approximate_word_count=0,
                chapter_range_json=canonical_json(role_values["chapterRange"]),
                scene_range_json=canonical_json(role_values["sceneRange"]),
                language_requirements_json=canonical_json(role_values["languageRequirements"]),
                performance_requirements_json=canonical_json(performance_requirements),
                warnings_json=canonical_json(warnings),
                provenance_json=canonical_json(provenance),
                status="active",
                role_fingerprint=str(role_values["roleFingerprint"]),
                created_at=now,
            )
            session.add(role_row)
            session.flush()
            candidates, candidate_input_fingerprint = self._replace_role_machine_evidence(
                session,
                run=run,
                role=role_row,
                now=now,
                candidate_input_override=(custom_candidate_input_fingerprint),
            )
            voice_rows = self._voice_rows(session, run.catalog_revision_id)
            eligible = [
                candidate
                for candidate in candidates
                if candidate["compatibilityStatus"] in {"eligible", "conditional"}
            ]
            if eligible:
                proposal = eligible[0]
                voice = voice_rows[str(proposal["voiceProfileId"])]
                session.add(
                    CastAssignmentRow(
                        id=stable_id(
                            "machine-cast-proposal",
                            run.id,
                            role_id,
                            proposal["candidateId"],
                        ),
                        project_id=project_id,
                        casting_run_id=run.id,
                        role_id=role_id,
                        correction_id=None,
                        voice_profile_record_id=voice.id,
                        catalog_revision_id=run.catalog_revision_id,
                        casting_profile_fingerprint=run.casting_profile_fingerprint,
                        phase2_snapshot_fingerprint=run.analysis_snapshot_fingerprint,
                        effective_correction_set_fingerprint=(
                            run.effective_correction_set_fingerprint
                        ),
                        authority="machine_proposal",
                        assignment_state="proposed",
                        rationale=(
                            "Deterministic declared-metadata proposal for an explicit "
                            "content-free custom role; human selection remains required."
                        ),
                        warnings_json=canonical_json(warnings),
                        rights_state=voice.rights_state,
                        revision=1,
                        provenance_json=canonical_json(
                            {
                                "origin": "runtime_agent",
                                "producerId": CASTING_PRODUCER_ID,
                                "producerVersion": CASTING_PRODUCER_VERSION,
                                "recordedAt": now,
                                "inputFingerprint": candidate_input_fingerprint,
                            }
                        ),
                        supersedes_assignment_id=None,
                        created_at=now,
                    )
                )
            session.flush()
            self._publish_cast_snapshot(session, run=run, now=now)
            session.add(
                IdempotencyRow(
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=role_id,
                    created_at=now,
                )
            )
            session.flush()
            return (
                self._role_wire(session, role_row),
                list(CASTING_GATE_IDS),
                self.run_dict(session, run),
                self._reviews_wire(session, run),
            )

    @staticmethod
    def _candidate_machine_evidence(
        row: CastingCandidateRow,
        voice: VoiceProfileRow,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        explanation = parse_json(row.explanation_json, {})
        provenance = parse_json(row.provenance_json, {})
        hard_constraints = parse_json(row.hard_constraint_results_json, [])
        soft_preferences = parse_json(row.soft_preference_results_json, [])
        rights_eligibility = {
            "eligible": "verified",
            "restricted": "restricted",
            "unknown": "unknown",
            "ineligible": "prohibited",
        }.get(row.rights_eligibility)
        provider_available = {
            "available": True,
            "unavailable": False,
        }.get(row.provider_availability)
        model_available = {
            "available": True,
            "unavailable": False,
        }.get(row.model_availability)
        long_form_suitable = {
            "suitable": True,
            "unsuitable": False,
        }.get(row.long_form_suitability)
        if (
            row.compatibility_score is None
            or not isinstance(explanation, dict)
            or set(explanation) != {"text", "preReductionRank"}
            or not isinstance(explanation.get("text"), str)
            or not isinstance(explanation.get("preReductionRank"), int)
            or isinstance(explanation.get("preReductionRank"), bool)
            or not isinstance(provenance, dict)
            or not isinstance(hard_constraints, list)
            or not isinstance(soft_preferences, list)
            or rights_eligibility is None
            or provider_available is None
            or model_available is None
            or row.long_form_suitability not in {"suitable", "unsuitable", "unknown"}
        ):
            raise ServiceError(
                500,
                "CASTING_CANDIDATE_INVALID",
                "Candidate machine evidence is structurally invalid.",
            )
        assessment_evidence = {
            "compatibilityStatus": row.compatibility_status,
            "compatibilityScore": row.compatibility_score / 1_000_000,
            "confidenceClassification": row.confidence_class,
            "hardConstraintResults": hard_constraints,
            "softPreferenceResults": soft_preferences,
            "rightsEligibility": rights_eligibility,
            "languageEligibility": row.language_eligibility,
            "providerAvailability": provider_available,
            "modelAvailability": model_available,
            "longFormSuitability": long_form_suitable,
            "explanation": explanation["text"],
            "inputFingerprint": row.input_fingerprint,
        }
        assessment_evidence_fingerprint = request_fingerprint(assessment_evidence)
        candidate_evidence = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "candidateId": row.id,
            "roleId": row.role_id,
            "voiceProfileId": voice.profile_id,
            **assessment_evidence,
            "assessmentFingerprint": assessment_evidence_fingerprint,
            "conflictWarnings": [],
            "provenance": provenance,
            "preReductionRank": explanation["preReductionRank"],
            "ordinal": row.ordinal,
        }
        if _deterministic_machine_fingerprint(candidate_evidence) != row.output_fingerprint:
            raise ServiceError(
                500,
                "CASTING_CANDIDATE_INVALID",
                "Candidate machine evidence failed fingerprint verification.",
            )
        return assessment_evidence, candidate_evidence, assessment_evidence_fingerprint

    def _candidate_wire(
        self,
        session: Session,
        row: CastingCandidateRow,
        current_conflicts: Sequence[CastingConflictRow] = (),
    ) -> dict[str, Any]:
        voice = session.get(VoiceProfileRow, row.voice_profile_record_id)
        if voice is None:
            raise ServiceError(
                500,
                "CASTING_CANDIDATE_INVALID",
                "Candidate metadata is unavailable.",
            )
        (
            machine_assessment,
            machine_candidate,
            assessment_evidence_fingerprint,
        ) = self._candidate_machine_evidence(row, voice)
        rejected = self._active_candidate_rejection(
            session,
            casting_run_id=row.casting_run_id,
            candidate_id=row.id,
        )
        status = {
            "eligible": (
                "compatible_with_warnings"
                if row.rights_eligibility == "restricted" or voice.state == "deprecated"
                else "compatible"
            ),
            "conditional": "compatible_with_warnings",
            "ineligible": "incompatible",
            "unknown": "unknown",
        }[row.compatibility_status]
        rights_eligibility = {
            "eligible": "eligible",
            "restricted": "restricted_requires_acknowledgement",
            "unknown": "ineligible_unknown",
            "ineligible": "ineligible_prohibited",
        }[row.rights_eligibility]
        confidence_score = row.compatibility_score
        assert confidence_score is not None
        public_confidence_score: int | float = (
            confidence_score // 1_000_000
            if confidence_score % 1_000_000 == 0
            else confidence_score / 1_000_000
        )
        hard_constraints = [
            {
                "constraintId": value.get("constraintId", value.get("ruleId")),
                "result": value.get("result", "unknown"),
                "explanation": value.get("explanation", ""),
            }
            for value in machine_assessment["hardConstraintResults"]
            if isinstance(value, dict)
        ]
        soft_preferences = [
            {
                "preferenceId": value.get(
                    "preferenceId",
                    value.get("ruleId"),
                ),
                "score": (
                    float(value.get("weight", 0)) / 10 if value.get("result") == "pass" else 0
                ),
                "explanation": value.get("explanation", ""),
            }
            for value in machine_assessment["softPreferenceResults"]
            if isinstance(value, dict)
        ]
        conflict_rows = [
            value
            for value in sorted(current_conflicts, key=lambda candidate: candidate.id)
            if (
                not (
                    voice_ids := [
                        candidate
                        for candidate in parse_json(value.details_json, {}).get(
                            "voiceProfileIds",
                            [],
                        )
                        if isinstance(candidate, str)
                    ]
                )
                or voice.profile_id in voice_ids
            )
        ]
        conflict_ids = [value.id for value in conflict_rows]
        conflict_warnings = [
            {
                "code": (f"CASTING_CONFLICT_{value.category.upper()}"),
                "severity": ("blocker" if value.severity == "blocking" else value.severity),
                "message": (
                    "Current declared-metadata casting evidence records "
                    f"a {value.category.replace('_', ' ')} conflict."
                ),
                "requiresHumanReview": True,
                "relatedEntityIds": (
                    role_ids
                    if len(role_ids := self._conflict_role_ids(value))
                    <= _WARNING_RELATED_ENTITY_LIMIT
                    else [
                        *role_ids[: _WARNING_RELATED_ENTITY_LIMIT - 1],
                        _WARNING_RELATED_ENTITY_OVERFLOW_ID,
                    ]
                ),
                "evidence": [],
            }
            for value in conflict_rows[:MAX_CASTING_WARNINGS_PER_ENTITY]
        ]
        assessment_values = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "assessmentId": stable_id("casting-assessment", row.id),
            "roleId": row.role_id,
            "voiceProfileId": voice.profile_id,
            "compatibilityStatus": status,
            "compatibilityScore": public_confidence_score,
            "confidence": {
                "score": public_confidence_score,
                "classification": row.confidence_class,
                "basis": ("Deterministic declared catalog metadata and governed rules."),
                "calibrationId": "governed-casting-rules-v1",
            },
            "hardConstraints": hard_constraints,
            "softPreferences": soft_preferences,
            "rightsEligibility": rights_eligibility,
            "languageEligibility": {
                "eligible": "pass",
                "ineligible": "fail",
                "unknown": "unknown",
            }.get(row.language_eligibility, "unknown"),
            "providerAvailability": row.provider_availability,
            "modelAvailability": row.model_availability,
            "longFormSuitability": row.long_form_suitability,
            "explanation": machine_assessment["explanation"],
            "provenance": machine_candidate["provenance"],
            "inputFingerprint": row.input_fingerprint,
            "baseEvidenceFingerprint": assessment_evidence_fingerprint,
        }
        # The public assessment reshapes the immutable engine evidence; its
        # output fingerprint therefore binds the exact public representation.
        assessment = assessment_values | {
            "outputFingerprint": request_fingerprint(assessment_values)
        }
        candidate_values = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "candidateId": row.id,
            "castingRunId": row.casting_run_id,
            "roleId": row.role_id,
            "voiceProfileId": voice.profile_id,
            "rank": row.ordinal + 1,
            "preReductionRank": int(machine_candidate["preReductionRank"]),
            "assessment": assessment,
            "conflictIds": conflict_ids,
            "conflictWarnings": conflict_warnings,
            "rejectedByCorrectionId": rejected.id if rejected is not None else None,
            "provenance": assessment["provenance"],
            "inputFingerprint": row.input_fingerprint,
            "baseEvidenceFingerprint": row.output_fingerprint,
        }
        return candidate_values | {"outputFingerprint": request_fingerprint(candidate_values)}

    def _active_candidate_rejection(
        self,
        session: Session,
        *,
        casting_run_id: str,
        candidate_id: str,
    ) -> CastingCorrectionRow | None:
        rejected = session.scalar(
            select(CastingCorrectionRow.id)
            .where(
                CastingCorrectionRow.casting_run_id == casting_run_id,
                CastingCorrectionRow.kind == "reject_candidate",
                CastingCorrectionRow.corrected_value_json.contains(candidate_id),
            )
            .order_by(CastingCorrectionRow.revision.desc())
            .limit(1)
        )
        if rejected is None:
            return None
        rejection = session.get(CastingCorrectionRow, rejected)
        if rejection is None:
            return None
        self._validated_correction_fingerprint(rejection)
        superseding = session.scalar(
            select(CastingCorrectionRow.id)
            .where(
                CastingCorrectionRow.casting_run_id == casting_run_id,
                CastingCorrectionRow.supersedes_correction_id == rejection.id,
                CastingCorrectionRow.kind == "select_voice",
            )
            .limit(1)
        )
        return None if superseding is not None else rejection

    def list_candidates(
        self,
        *,
        project_id: str,
        run_id: str,
        role_id: str,
        expected_role_revision: int,
        cursor: str | None,
        limit: int,
        evidence: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        with self.database.session() as session:
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            self.assert_evidence(session, run=run, **evidence)
            role = session.get(ProductionRoleRow, role_id)
            if role is None or role.casting_run_id != run.id:
                raise not_found("production role")
            if self._role_revision(session, role.id) != expected_role_revision:
                raise ServiceError(
                    409,
                    "CASTING_ROLE_STALE",
                    "The production role changed; refresh before continuing.",
                )
            rows = self._current_candidate_rows(
                session,
                run_id=run.id,
                role_id=role.id,
            )
            page, next_cursor, total = _bounded_page(
                rows,
                binding=request_fingerprint(
                    {
                        "type": "casting-candidates",
                        "runId": run.id,
                        "roleId": role.id,
                        "rowIds": [value.id for value in rows],
                    }
                ),
                cursor=cursor,
                limit=limit,
            )
            current_conflicts = list(
                session.scalars(
                    select(CastingConflictRow)
                    .where(
                        CastingConflictRow.casting_run_id == run.id,
                        CastingConflictRow.status == "open",
                        (
                            (CastingConflictRow.primary_role_id == role.id)
                            | (CastingConflictRow.secondary_role_id == role.id)
                        ),
                    )
                    .order_by(CastingConflictRow.id)
                )
            )
            return (
                [
                    self._candidate_wire(
                        session,
                        row,
                        current_conflicts,
                    )
                    for row in page
                ],
                next_cursor,
                total,
            )

    def _conflict_wire(
        self,
        session: Session,
        row: CastingConflictRow,
    ) -> dict[str, Any]:
        details = parse_json(row.details_json, {})
        if not isinstance(details, dict):
            raise ServiceError(
                500,
                "CASTING_CONFLICT_INVALID",
                "Conflict machine evidence is structurally invalid.",
            )
        base_values = dict(details)
        embedded_fingerprint = base_values.pop("conflictFingerprint", None)
        expected_base_fingerprint = request_fingerprint(base_values)
        related_role_ids = details.get("relatedRoleIds")
        voice_profile_ids = details.get("voiceProfileIds")
        provenance = parse_json(row.provenance_json, {})
        expected_provenance = {
            "origin": (
                "system" if details.get("source") == "human_assignments" else "runtime_agent"
            ),
            "producerId": CASTING_PRODUCER_ID,
            "producerVersion": CASTING_PRODUCER_VERSION,
            "recordedAt": row.created_at,
            "inputFingerprint": details.get("inputFingerprint"),
        }
        if (
            expected_base_fingerprint != row.evidence_fingerprint
            or (
                embedded_fingerprint is not None
                and embedded_fingerprint != row.evidence_fingerprint
            )
            or details.get("contractVersion") != CASTING_CONTRACT_VERSION
            or details.get("conflictId") != row.id
            or details.get("category") != row.category
            or details.get("severity") != row.severity
            or details.get("primaryRoleId") != row.primary_role_id
            or not isinstance(related_role_ids, list)
            or any(not isinstance(value, str) for value in related_role_ids)
            or row.secondary_role_id != (related_role_ids[0] if related_role_ids else None)
            or not isinstance(voice_profile_ids, list)
            or any(not isinstance(value, str) for value in voice_profile_ids)
            or details.get("metadataBased") is not True
            or details.get("acousticSimilarityClaimed") is not False
            or provenance != expected_provenance
        ):
            raise ServiceError(
                500,
                "CASTING_CONFLICT_INVALID",
                "Conflict machine evidence failed fingerprint verification.",
            )
        if voice_profile_ids:
            if row.voice_profile_record_id is None:
                raise ServiceError(
                    500,
                    "CASTING_CONFLICT_INVALID",
                    "Conflict voice evidence is unavailable.",
                )
            voice = session.get(VoiceProfileRow, row.voice_profile_record_id)
            if voice is None or voice.profile_id != voice_profile_ids[0]:
                raise ServiceError(
                    500,
                    "CASTING_CONFLICT_INVALID",
                    "Conflict voice evidence does not match its immutable base.",
                )
        elif row.voice_profile_record_id is not None:
            raise ServiceError(
                500,
                "CASTING_CONFLICT_INVALID",
                "Conflict voice evidence does not match its immutable base.",
            )
        disposition = self._current_conflict_disposition(
            session,
            row,
        )
        resolution = (
            "superseded"
            if row.status == "superseded"
            else "approved_reuse"
            if disposition is not None
            else row.status
            if row.status != "open"
            else "open"
        )
        role_ids = self._conflict_role_ids(row)
        voice_ids = list(voice_profile_ids)
        conflict_values = {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "conflictId": row.id,
            "castingRunId": row.casting_run_id,
            "category": row.category,
            "severity": ("blocker" if row.severity == "blocking" else row.severity),
            "resolutionState": resolution,
            "roleIds": role_ids,
            "voiceProfileIds": voice_ids,
            "explanation": details.get("explanation", ""),
            "metadataOnly": True,
            "acousticSimilarityClaimed": False,
            "dispositionCorrectionId": (disposition.id if disposition is not None else None),
            "provenance": provenance,
            "inputFingerprint": details.get(
                "inputFingerprint",
                row.evidence_fingerprint,
            ),
            "baseEvidenceFingerprint": row.evidence_fingerprint,
        }
        return conflict_values | {"outputFingerprint": request_fingerprint(conflict_values)}

    def list_conflicts(
        self,
        *,
        project_id: str,
        run_id: str,
        cursor: str | None,
        limit: int,
        evidence: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        self._latch_external_assignment_invalidations_for_read(
            project_id=project_id,
            run_id=run_id,
            evidence=evidence,
        )
        with self.database.session() as session:
            run = self._require_assignment_read_run(
                session,
                project_id=project_id,
                run_id=run_id,
                evidence=evidence,
            )
            rows = list(
                session.scalars(
                    select(CastingConflictRow)
                    .where(CastingConflictRow.casting_run_id == run.id)
                    .order_by(CastingConflictRow.created_at, CastingConflictRow.id)
                )
            )
            page, next_cursor, total = _bounded_page(
                rows,
                binding=request_fingerprint(
                    {
                        "type": "casting-conflicts",
                        "runId": run.id,
                        "rowIds": [value.id for value in rows],
                    }
                ),
                cursor=cursor,
                limit=limit,
            )
            return (
                [self._conflict_wire(session, row) for row in page],
                next_cursor,
                total,
            )

    def _assignment_wire(
        self,
        session: Session,
        row: CastAssignmentRow,
    ) -> dict[str, Any]:
        voice = (
            session.get(VoiceProfileRow, row.voice_profile_record_id)
            if row.voice_profile_record_id is not None
            else None
        )
        if voice is None:
            raise ServiceError(
                500,
                "CAST_ASSIGNMENT_INVALID",
                "The cast assignment has no governed voice evidence.",
            )
        rights = session.scalar(
            select(VoiceRightsRecordRow)
            .where(VoiceRightsRecordRow.voice_profile_record_id == voice.id)
            .order_by(
                VoiceRightsRecordRow.revision.desc(),
                VoiceRightsRecordRow.id.desc(),
            )
            .limit(1)
        )
        if rights is None:
            raise ServiceError(
                500,
                "CAST_ASSIGNMENT_INVALID",
                "The cast assignment has no governed rights evidence.",
            )
        catalog = self._catalog_identity(session, row.catalog_revision_id)
        latest = self._latest_assignment(
            session,
            role_id=row.role_id,
            include_proposal=row.authority == "machine_proposal",
        )
        invalidation = self._assignment_invalidation(session, row.id)
        effective = latest is not None and latest.id == row.id and invalidation is None
        warnings = [value for value in parse_json(row.warnings_json, []) if isinstance(value, dict)]
        if invalidation is not None:
            warnings = [
                *warnings[: MAX_CASTING_WARNINGS_PER_ENTITY - 1],
                {
                    "code": _ASSIGNMENT_INVALIDATION_WARNING_CODE,
                    "severity": "blocker",
                    "message": (
                        "The selected voice's governed catalog or rights "
                        "evidence changed; explicit reselection is required."
                    ),
                    "requiresHumanReview": True,
                    "relatedEntityIds": [row.role_id, voice.profile_id],
                    "evidence": [],
                },
            ]
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "assignmentId": row.id,
            "projectId": row.project_id,
            "roleId": row.role_id,
            "voiceProfileId": voice.profile_id,
            "voiceProfileVersion": voice.profile_version,
            "voiceEvidenceFingerprint": voice.profile_fingerprint,
            "rightsRecordId": rights.rights_record_id,
            "rightsRecordRevision": rights.revision,
            "rightsEvidenceFingerprint": rights.rights_fingerprint,
            "catalogRevisionId": catalog.catalog_id,
            "castingRunId": row.casting_run_id,
            "castingProfileFingerprint": row.casting_profile_fingerprint,
            "phase2SnapshotFingerprint": row.phase2_snapshot_fingerprint,
            "effectiveCorrectionSetFingerprint": (row.effective_correction_set_fingerprint),
            "authority": row.authority,
            "rationale": row.rationale,
            "warnings": warnings,
            "rightsState": row.rights_state,
            "revision": row.revision,
            "provenance": parse_json(row.provenance_json, {}),
            "supersedesAssignmentId": row.supersedes_assignment_id,
            "effective": effective,
        }

    def list_assignments(
        self,
        *,
        project_id: str,
        run_id: str,
        cursor: str | None,
        limit: int,
        evidence: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        self._latch_external_assignment_invalidations_for_read(
            project_id=project_id,
            run_id=run_id,
            evidence=evidence,
        )
        with self.database.session() as session:
            run = self._require_assignment_read_run(
                session,
                project_id=project_id,
                run_id=run_id,
                evidence=evidence,
            )
            rows = list(
                session.scalars(
                    select(CastAssignmentRow)
                    .where(
                        CastAssignmentRow.casting_run_id == run.id,
                        CastAssignmentRow.voice_profile_record_id.is_not(None),
                    )
                    .order_by(
                        CastAssignmentRow.role_id,
                        CastAssignmentRow.revision,
                        CastAssignmentRow.id,
                    )
                )
            )
            page, next_cursor, total = _bounded_page(
                rows,
                binding=request_fingerprint(
                    {
                        "type": "cast-assignments",
                        "runId": run.id,
                        "rowIds": [value.id for value in rows],
                    }
                ),
                cursor=cursor,
                limit=limit,
            )
            return (
                [self._assignment_wire(session, row) for row in page],
                next_cursor,
                total,
            )

    def _correction_wire(
        self,
        row: CastingCorrectionRow,
    ) -> dict[str, Any]:
        evidence_fingerprint = self._validated_correction_fingerprint(row)
        material = self._correction_material(row)
        value = material["correctedValue"]
        provenance = self._public_provenance(material["provenance"])
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "correctionId": row.id,
            "projectId": row.project_id,
            "castingRunId": row.casting_run_id,
            "targetRoleId": row.role_id,
            "category": row.kind,
            "priorEffectiveFingerprint": row.prior_effective_fingerprint,
            "correctedValue": value,
            "correctedValueFingerprint": request_fingerprint(value),
            "actor": {
                "classification": "human",
                "actorId": row.actor_id,
            },
            "reason": row.reason,
            "recordedAt": row.recorded_at,
            "provenance": provenance,
            "immutable": True,
            "lockedAgainstAutomation": True,
            "supersedesCorrectionId": row.supersedes_correction_id,
            "idempotencyFingerprint": evidence_fingerprint,
        }

    def _assignment_for_correction(
        self,
        session: Session,
        correction: CastingCorrectionRow,
    ) -> CastAssignmentRow | None:
        return session.scalar(
            select(CastAssignmentRow)
            .where(
                CastAssignmentRow.correction_id == correction.id,
                CastAssignmentRow.voice_profile_record_id.is_not(None),
            )
            .limit(1)
        )

    def list_corrections(
        self,
        *,
        project_id: str,
        run_id: str,
        cursor: str | None,
        limit: int,
        evidence: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        with self.database.session() as session:
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            self.assert_evidence(session, run=run, **evidence)
            rows = list(
                session.scalars(
                    select(CastingCorrectionRow)
                    .where(CastingCorrectionRow.casting_run_id == run.id)
                    .order_by(
                        CastingCorrectionRow.recorded_at,
                        CastingCorrectionRow.id,
                    )
                )
            )
            page, next_cursor, total = _bounded_page(
                rows,
                binding=request_fingerprint(
                    {
                        "type": "casting-corrections",
                        "runId": run.id,
                        "rowIds": [value.id for value in rows],
                    }
                ),
                cursor=cursor,
                limit=limit,
            )
            return (
                [self._correction_wire(row) for row in page],
                next_cursor,
                total,
            )

    def append_correction(
        self,
        *,
        project_id: str,
        run_id: str,
        operation: str,
        target_role_id: str,
        expected_role_revision: int,
        expected_run_fingerprint: str,
        expected_catalog_fingerprint: str,
        expected_snapshot_fingerprint: str,
        expected_correction_set_fingerprint: str,
        previous_effective_fingerprint: str,
        voice_profile_id: str | None,
        corrected_value: dict[str, Any] | None,
        reason: str,
        supersedes_correction_id: str | None,
        idempotency_key: str,
    ) -> tuple[
        dict[str, Any],
        dict[str, Any] | None,
        list[str],
        dict[str, Any],
        list[dict[str, Any]],
    ]:
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            submitted_value = dict(corrected_value or {})
            request_value = dict(submitted_value)
            if voice_profile_id is not None:
                request_value["voiceProfileId"] = voice_profile_id
            request_hash = request_fingerprint(
                {
                    "operation": operation,
                    "projectId": project_id,
                    "castingRunId": run.id,
                    "roleId": target_role_id,
                    "expectedRoleRevision": expected_role_revision,
                    "expectedRunFingerprint": expected_run_fingerprint,
                    "expectedCatalogFingerprint": expected_catalog_fingerprint,
                    "expectedSnapshotFingerprint": expected_snapshot_fingerprint,
                    "expectedCorrectionSetFingerprint": (expected_correction_set_fingerprint),
                    "previousEffectiveFingerprint": previous_effective_fingerprint,
                    "value": request_value,
                    "reason": reason,
                    "supersedesCorrectionId": supersedes_correction_id,
                }
            )
            idempotent = session.scalar(
                select(CastingCorrectionRow).where(
                    CastingCorrectionRow.casting_run_id == run.id,
                    CastingCorrectionRow.idempotency_key == idempotency_key,
                )
            )
            if idempotent is not None:
                idempotent_provenance = self._correction_material(idempotent)["provenance"]
                self._validated_correction_fingerprint(idempotent)
                if idempotent_provenance.get("requestFingerprint") != request_hash:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was used for another correction.",
                    )
                assignment = self._assignment_for_correction(
                    session,
                    idempotent,
                )
                self._sync_external_assignment_invalidations(
                    session,
                    run=run,
                )
                return (
                    self._correction_wire(idempotent),
                    (
                        self._assignment_wire(session, assignment)
                        if assignment is not None
                        else None
                    ),
                    list(CASTING_GATE_IDS),
                    self.run_dict(session, run),
                    self._reviews_wire(session, run),
                )
            correction_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(CastingCorrectionRow)
                    .where(CastingCorrectionRow.casting_run_id == run.id)
                )
                or 0
            )
            if correction_count >= MAX_CASTING_CORRECTIONS_PER_RUN:
                raise ServiceError(
                    409,
                    "CASTING_CORRECTION_LIMIT_EXCEEDED",
                    "This casting run reached its immutable correction limit.",
                )
            self.assert_evidence(
                session,
                run=run,
                expected_run_fingerprint=expected_run_fingerprint,
                expected_catalog_fingerprint=expected_catalog_fingerprint,
                expected_snapshot_id=run.analysis_snapshot_id,
                expected_snapshot_revision=run.analysis_snapshot_revision,
                expected_snapshot_fingerprint=expected_snapshot_fingerprint,
            )
            self._assert_current_catalog_write_evidence(run)
            role = session.get(ProductionRoleRow, target_role_id)
            if role is None or role.casting_run_id != run.id:
                raise not_found("production role")
            if (
                self._role_revision(session, role.id) != expected_role_revision
                or run.effective_correction_set_fingerprint != expected_correction_set_fingerprint
                or self._effective_role_fingerprint(session, role) != previous_effective_fingerprint
            ):
                raise ServiceError(
                    409,
                    "CASTING_CORRECTION_STALE",
                    "Casting evidence changed; refresh before correcting.",
                )
            prior_assignment = self._latest_assignment(
                session,
                role_id=role.id,
                include_proposal=False,
            )
            latest_assignment_revision = self._latest_assignment(
                session,
                role_id=role.id,
                include_proposal=True,
            )
            voice_row = None
            if voice_profile_id is not None:
                voice_row = session.scalar(
                    select(VoiceProfileRow).where(
                        VoiceProfileRow.catalog_revision_id == run.catalog_revision_id,
                        VoiceProfileRow.profile_id == voice_profile_id,
                    )
                )
                if voice_row is None:
                    raise not_found("voice profile")
            if (
                operation
                in {
                    "select_voice",
                    "reject_candidate",
                }
                and voice_row is None
            ):
                raise ServiceError(
                    422,
                    "VOICE_PROFILE_REQUIRED",
                    "This correction requires a catalog voice profile.",
                )
            candidate: CastingCandidateRow | None = None
            rejection: CastingCorrectionRow | None = None
            if operation in {"select_voice", "reject_candidate"}:
                if voice_row is None:
                    raise ServiceError(
                        422,
                        "VOICE_PROFILE_REQUIRED",
                        "This correction requires a catalog voice profile.",
                    )
                candidate_revision = self._current_candidate_revision(
                    session,
                    role_id=role.id,
                )
                candidate = session.scalar(
                    select(CastingCandidateRow).where(
                        CastingCandidateRow.role_id == role.id,
                        CastingCandidateRow.role_revision == candidate_revision,
                        CastingCandidateRow.voice_profile_record_id == voice_row.id,
                    )
                )
                if candidate is None:
                    raise ServiceError(
                        409,
                        "CASTING_CANDIDATE_STALE",
                        "The selected candidate is not current for this role.",
                    )
                rejection = self._active_candidate_rejection(
                    session,
                    casting_run_id=run.id,
                    candidate_id=candidate.id,
                )
                if (
                    operation == "select_voice"
                    and rejection is not None
                    and supersedes_correction_id != rejection.id
                ):
                    raise ServiceError(
                        409,
                        "CASTING_CANDIDATE_REJECTED",
                        "The candidate remains rejected until that decision is "
                        "explicitly superseded.",
                    )
                if operation == "reject_candidate" and rejection is not None:
                    raise ServiceError(
                        409,
                        "CASTING_CANDIDATE_ALREADY_REJECTED",
                        "The candidate already has a current rejection.",
                    )
            rights: VoiceRightsRecordRow | None = None
            if operation == "acknowledge_restricted_rights":
                if voice_row is None or voice_row.rights_state != "restricted":
                    raise ServiceError(
                        409,
                        "RESTRICTED_RIGHTS_ACKNOWLEDGEMENT_INVALID",
                        "Only a current restricted-rights assignment can be acknowledged.",
                    )
                if (
                    prior_assignment is None
                    or prior_assignment.voice_profile_record_id != voice_row.id
                ):
                    raise ServiceError(
                        409,
                        "RESTRICTED_RIGHTS_ACKNOWLEDGEMENT_INVALID",
                        "The restricted voice is not the current human selection.",
                    )
                rights = session.scalar(
                    select(VoiceRightsRecordRow)
                    .where(VoiceRightsRecordRow.voice_profile_record_id == voice_row.id)
                    .order_by(VoiceRightsRecordRow.revision.desc())
                    .limit(1)
                )
                if rights is None:
                    raise ServiceError(
                        409,
                        "VOICE_RIGHTS_UNAVAILABLE",
                        "The current rights record is unavailable.",
                    )
            if operation == "select_voice":
                selected_rights = (
                    session.scalar(
                        select(VoiceRightsRecordRow)
                        .where(VoiceRightsRecordRow.voice_profile_record_id == voice_row.id)
                        .order_by(
                            VoiceRightsRecordRow.revision.desc(),
                            VoiceRightsRecordRow.id.desc(),
                        )
                        .limit(1)
                    )
                    if voice_row is not None
                    else None
                )
                if selected_rights is None or not rights_record_is_current(
                    self._stored_rights_material(selected_rights)
                ):
                    raise ServiceError(
                        409,
                        "VOICE_RIGHTS_NOT_CURRENT",
                        "The selected voice rights are not currently effective.",
                    )
                request_value = {"voiceProfileId": voice_profile_id}
            elif operation == "clear_assignment":
                if prior_assignment is None:
                    raise ServiceError(
                        409,
                        "CASTING_ASSIGNMENT_REQUIRED",
                        "The role has no current human assignment to clear.",
                    )
                expected_assignment_value = {"expectedAssignmentId": prior_assignment.id}
                if submitted_value != expected_assignment_value:
                    raise ServiceError(
                        409,
                        "CASTING_ASSIGNMENT_STALE",
                        "The assignment-clear evidence is not current.",
                    )
                request_value = expected_assignment_value
            elif operation == "lock_assignment":
                if prior_assignment is None or prior_assignment.voice_profile_record_id is None:
                    raise ServiceError(
                        409,
                        "CASTING_ASSIGNMENT_REQUIRED",
                        "Select a voice before locking this role.",
                    )
                expected_assignment_value = {"assignmentId": prior_assignment.id}
                if submitted_value != expected_assignment_value:
                    raise ServiceError(
                        409,
                        "CASTING_ASSIGNMENT_STALE",
                        "The assignment-lock evidence is not current.",
                    )
                request_value = expected_assignment_value
            elif operation == "unlock_assignment":
                if (
                    prior_assignment is None
                    or prior_assignment.assignment_state != "locked"
                    or prior_assignment.voice_profile_record_id is None
                ):
                    raise ServiceError(
                        409,
                        "CASTING_LOCK_REQUIRED",
                        "The role has no current locked assignment.",
                    )
                expected_assignment_value = {"lockedAssignmentId": prior_assignment.id}
                if submitted_value != expected_assignment_value:
                    raise ServiceError(
                        409,
                        "CASTING_ASSIGNMENT_STALE",
                        "The assignment-unlock evidence is not current.",
                    )
                request_value = expected_assignment_value
            elif operation == "mark_intentionally_uncast":
                request_value = {"intentionallyUncast": True}
            elif operation == "change_role_label":
                label = request_value.get("effectiveDisplayLabel")
                if not isinstance(label, str) or not label.strip() or len(label) > 200:
                    raise ServiceError(
                        422,
                        "CASTING_ROLE_LABEL_INVALID",
                        "The casting role label is invalid.",
                    )
                request_value = {"effectiveDisplayLabel": label.strip()}
            elif operation == "change_casting_requirement":
                requirement = request_value.get("requirement")
                if not isinstance(requirement, dict):
                    raise ServiceError(
                        422,
                        "CASTING_REQUIREMENT_INVALID",
                        "The casting requirement is invalid.",
                    )
                request_value = {"requirement": requirement}
            elif operation == "acknowledge_restricted_rights":
                if rights is None:
                    raise ServiceError(
                        409,
                        "VOICE_RIGHTS_UNAVAILABLE",
                        "The current rights record is unavailable.",
                    )
                expected_rights_value = {
                    "rightsRecordId": rights.rights_record_id,
                    "rightsRecordRevision": rights.revision,
                }
                if submitted_value != expected_rights_value:
                    raise ServiceError(
                        409,
                        "VOICE_RIGHTS_STALE",
                        "The restricted-rights evidence is not current.",
                    )
                request_value = expected_rights_value
            elif operation == "approve_voice_reuse":
                conflict_id = request_value.get("conflictId")
                approved_role_ids = request_value.get("approvedRoleIds")
                conflict = (
                    session.get(CastingConflictRow, conflict_id)
                    if isinstance(conflict_id, str)
                    else None
                )
                expected_role_ids = (
                    set(self._conflict_role_ids(conflict)) if conflict is not None else set()
                )
                if (
                    conflict is None
                    or conflict.casting_run_id != run.id
                    or conflict.status != "open"
                    or conflict.category not in _REUSABLE_CONFLICT_CATEGORIES
                    or not isinstance(approved_role_ids, list)
                    or {value for value in approved_role_ids if isinstance(value, str)}
                    != expected_role_ids
                ):
                    raise ServiceError(
                        409,
                        "CASTING_CONFLICT_STALE",
                        "The voice-reuse conflict is not current.",
                    )
                existing_disposition = self._current_conflict_disposition(
                    session,
                    conflict,
                )
                if existing_disposition is not None:
                    raise ServiceError(
                        409,
                        "CASTING_CONFLICT_ALREADY_DISPOSED",
                        "The voice-reuse conflict already has a current disposition.",
                    )
                request_value = {
                    "conflictId": conflict.id,
                    "approvedRoleIds": sorted(expected_role_ids),
                }
            elif operation == "reject_candidate":
                if candidate is None:
                    raise ServiceError(
                        409,
                        "CASTING_CANDIDATE_STALE",
                        "The rejected candidate is not current for this role.",
                    )
                expected_candidate_value = {"candidateId": candidate.id}
                if submitted_value != expected_candidate_value:
                    raise ServiceError(
                        409,
                        "CASTING_CANDIDATE_STALE",
                        "The rejected candidate evidence is not current for this role.",
                    )
                request_value = expected_candidate_value
            elif operation == "record_custom_rationale":
                custom_rationale = request_value.get("rationale")
                if (
                    not isinstance(custom_rationale, str)
                    or not custom_rationale.strip()
                    or len(custom_rationale) > 2_000
                ):
                    raise ServiceError(
                        422,
                        "CASTING_RATIONALE_INVALID",
                        "The custom casting rationale is invalid.",
                    )
                request_value = {"rationale": custom_rationale.strip()}

            semantic_supersession: CastingCorrectionRow | None = None
            if operation == "select_voice" and rejection is not None:
                semantic_supersession = rejection
            elif operation in _ASSIGNMENT_CORRECTION_KINDS:
                semantic_supersession = session.scalar(
                    select(CastingCorrectionRow)
                    .where(
                        CastingCorrectionRow.role_id == role.id,
                        CastingCorrectionRow.kind.in_(_ASSIGNMENT_CORRECTION_KINDS),
                    )
                    .order_by(
                        CastingCorrectionRow.revision.desc(),
                        CastingCorrectionRow.id.desc(),
                    )
                    .limit(1)
                )
            elif operation in _SAME_DOMAIN_CORRECTION_KINDS:
                semantic_supersession = session.scalar(
                    select(CastingCorrectionRow)
                    .where(
                        CastingCorrectionRow.role_id == role.id,
                        CastingCorrectionRow.kind == operation,
                    )
                    .order_by(
                        CastingCorrectionRow.revision.desc(),
                        CastingCorrectionRow.id.desc(),
                    )
                    .limit(1)
                )
            expected_supersession_id = (
                semantic_supersession.id if semantic_supersession is not None else None
            )
            if (
                supersedes_correction_id is not None
                and supersedes_correction_id != expected_supersession_id
            ):
                raise ServiceError(
                    409,
                    "CASTING_SUPERSESSION_INVALID",
                    "The correction does not supersede the current semantic leaf.",
                )
            if operation == "unlock_assignment" and (
                semantic_supersession is None or semantic_supersession.kind != "lock_assignment"
            ):
                raise ServiceError(
                    409,
                    "CASTING_LOCK_SUPERSESSION_REQUIRED",
                    "Unlocking must supersede the correction that created the current lock.",
                )
            if semantic_supersession is not None:
                existing_successor = session.scalar(
                    select(CastingCorrectionRow.id)
                    .where(
                        CastingCorrectionRow.supersedes_correction_id == semantic_supersession.id
                    )
                    .limit(1)
                )
                if existing_successor is not None:
                    raise ServiceError(
                        409,
                        "CASTING_SUPERSESSION_STALE",
                        "The correction to supersede already has a successor.",
                    )
            supersedes_correction_id = expected_supersession_id

            latest_role_correction = session.scalar(
                select(CastingCorrectionRow)
                .where(CastingCorrectionRow.role_id == role.id)
                .order_by(
                    CastingCorrectionRow.revision.desc(),
                    CastingCorrectionRow.id.desc(),
                )
                .limit(1)
            )
            revision = 1 if latest_role_correction is None else latest_role_correction.revision + 1
            now = utc_now()
            correction_id = new_id()
            correction_provenance = {
                "origin": "human",
                "producerId": "local_user",
                "producerVersion": "1.0.0",
                "recordedAt": now,
                "inputFingerprint": previous_effective_fingerprint,
                "requestFingerprint": request_hash,
            }
            correction = CastingCorrectionRow(
                id=correction_id,
                project_id=project_id,
                casting_run_id=run.id,
                role_id=role.id,
                kind=operation,
                revision=revision,
                prior_effective_fingerprint=(previous_effective_fingerprint),
                corrected_value_json=canonical_json(request_value),
                correction_fingerprint="",
                actor_id="local_user",
                reason=reason,
                provenance_json=canonical_json(correction_provenance),
                supersedes_correction_id=supersedes_correction_id,
                idempotency_key=idempotency_key,
                recorded_at=now,
            )
            correction.correction_fingerprint = request_fingerprint(
                self._correction_material(correction)
            )
            session.add(correction)
            session.flush()
            run.effective_correction_set_fingerprint = self._correction_fingerprint(
                session,
                run.id,
            )
            session.flush()
            assignment_state: str | None = None
            authority = "human_selection"
            selected_voice = voice_row
            if operation == "select_voice":
                assignment_state = "selected"
            elif operation == "clear_assignment":
                assignment_state = "cleared"
                selected_voice = None
            elif operation == "mark_intentionally_uncast":
                assignment_state = "intentionally_uncast"
                selected_voice = None
            elif operation == "lock_assignment":
                assert prior_assignment is not None
                assert prior_assignment.voice_profile_record_id is not None
                assignment_state = "locked"
                authority = "human_locked"
                selected_voice = session.get(
                    VoiceProfileRow,
                    prior_assignment.voice_profile_record_id,
                )
            elif operation == "unlock_assignment":
                assert prior_assignment is not None
                assert prior_assignment.voice_profile_record_id is not None
                assignment_state = "selected"
                selected_voice = session.get(
                    VoiceProfileRow,
                    prior_assignment.voice_profile_record_id,
                )
            if assignment_state is not None:
                assignment_revision = (
                    1
                    if latest_assignment_revision is None
                    else latest_assignment_revision.revision + 1
                )
                session.add(
                    CastAssignmentRow(
                        id=new_id(),
                        project_id=project_id,
                        casting_run_id=run.id,
                        role_id=role.id,
                        correction_id=correction.id,
                        voice_profile_record_id=(
                            selected_voice.id if selected_voice is not None else None
                        ),
                        catalog_revision_id=run.catalog_revision_id,
                        casting_profile_fingerprint=(run.casting_profile_fingerprint),
                        phase2_snapshot_fingerprint=(run.analysis_snapshot_fingerprint),
                        effective_correction_set_fingerprint=(
                            run.effective_correction_set_fingerprint
                        ),
                        authority=authority,
                        assignment_state=assignment_state,
                        rationale=reason,
                        warnings_json="[]",
                        rights_state=(
                            selected_voice.rights_state if selected_voice is not None else "unknown"
                        ),
                        revision=assignment_revision,
                        provenance_json=canonical_json(
                            {
                                "origin": "human",
                                "producerId": "local_user",
                                "producerVersion": "1.0.0",
                                "recordedAt": now,
                                "inputFingerprint": request_hash,
                            }
                        ),
                        supersedes_assignment_id=(
                            latest_assignment_revision.id
                            if latest_assignment_revision is not None
                            else None
                        ),
                        created_at=now,
                    )
                )
            session.flush()
            if operation == "change_casting_requirement":
                self._replace_role_machine_evidence(
                    session,
                    run=run,
                    role=role,
                    now=now,
                )
                session.flush()
            self._refresh_assignment_conflicts(
                session,
                run=run,
                now=now,
            )
            session.flush()
            self._publish_cast_snapshot(session, run=run, now=now)
            session.flush()
            assignment = self._assignment_for_correction(
                session,
                correction,
            )
            return (
                self._correction_wire(correction),
                (self._assignment_wire(session, assignment) if assignment is not None else None),
                list(CASTING_GATE_IDS),
                self.run_dict(session, run),
                self._reviews_wire(session, run),
            )

    def _review_evidence_wire(
        self,
        session: Session,
        review: CastingGateReviewRow,
    ) -> dict[str, Any]:
        snapshot = session.get(
            ApprovedCastSnapshotRow,
            review.cast_snapshot_id,
        )
        run = session.get(CastingRunRow, review.casting_run_id)
        if snapshot is None or run is None:
            raise ServiceError(
                500,
                "CASTING_REVIEW_INVALID",
                "The casting review evidence is unavailable.",
            )
        catalog = self._catalog_identity(session, run.catalog_revision_id)
        return {
            "projectId": run.project_id,
            "castingRunId": run.id,
            "approvedCastSnapshotId": snapshot.id,
            "approvedCastSnapshotRevision": snapshot.revision,
            "approvedCastSnapshotFingerprint": (snapshot.snapshot_fingerprint),
            "phase2SnapshotFingerprint": (run.analysis_snapshot_fingerprint),
            "catalogRevisionId": catalog.catalog_id,
            "catalogFingerprint": run.catalog_fingerprint,
            "castingProfileFingerprint": (run.casting_profile_fingerprint),
            "effectiveCorrectionSetFingerprint": (snapshot.effective_correction_set_fingerprint),
            "evidenceFingerprint": review.evidence_fingerprint,
        }

    def _decision_wire(
        self,
        session: Session,
        row: CastingGateDecisionRow,
    ) -> dict[str, Any]:
        snapshot = session.get(
            ApprovedCastSnapshotRow,
            row.cast_snapshot_id,
        )
        if snapshot is None:
            raise ServiceError(
                500,
                "CASTING_DECISION_INVALID",
                "The casting decision snapshot is unavailable.",
            )
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "decisionId": row.id,
            "reviewId": stable_id(
                "casting-gate-review",
                row.casting_run_id,
                row.gate_id,
            ),
            "gateId": row.gate_id,
            "projectId": row.project_id,
            "castingRunId": row.casting_run_id,
            "approvedCastSnapshotId": row.cast_snapshot_id,
            "approvedCastSnapshotRevision": snapshot.revision,
            "evidenceFingerprint": row.evidence_fingerprint,
            "decision": row.decision,
            "actor": {
                "classification": ("system" if row.decision == "invalidated" else "human"),
                "actorId": (
                    row.actor_id
                    or (
                        _ASSIGNMENT_INVALIDATION_ACTOR_ID
                        if row.decision == "invalidated"
                        else "local_user"
                    )
                ),
            },
            "acknowledgedWarningIds": parse_json(
                row.warning_acknowledgements_json,
                [],
            ),
            "rationale": row.rationale,
            "decidedAt": row.decided_at or row.created_at,
            "provenance": self._public_provenance(parse_json(row.provenance_json, {})),
            "immutable": True,
            "supersedesDecisionId": row.supersedes_decision_id,
        }

    def _review_wire(
        self,
        session: Session,
        row: CastingGateReviewRow,
        *,
        decision_override: CastingGateDecisionRow | None = None,
    ) -> dict[str, Any]:
        if decision_override is not None and decision_override.gate_review_id != row.id:
            raise ServiceError(
                500,
                "CASTING_DECISION_INVALID",
                "The casting decision does not belong to its frozen review.",
            )
        latest_for_gate = decision_override or self._latest_decision(
            session,
            run_id=row.casting_run_id,
            gate_id=row.gate_id,
        )
        latest = (
            latest_for_gate
            if latest_for_gate is not None and latest_for_gate.gate_review_id == row.id
            else None
        )
        warning_values = parse_json(row.warnings_json, {})
        run = session.get(CastingRunRow, row.casting_run_id)
        if run is None:
            raise ServiceError(
                500,
                "CASTING_REVIEW_INVALID",
                "The casting review run is unavailable.",
            )
        dynamic_blockers = (
            []
            if decision_override is not None
            else self._review_blockers(
                session,
                run=run,
                gate_id=row.gate_id,
            )[0]
        )
        dynamically_invalidated = (
            latest is not None
            and latest.cast_snapshot_id == row.cast_snapshot_id
            and latest.decision == "approved"
            and bool(dynamic_blockers)
        )
        state = (
            "invalidated"
            if dynamically_invalidated
            else latest.decision
            if latest is not None and latest.cast_snapshot_id == row.cast_snapshot_id
            else "pending"
        )
        evidence = self._review_evidence_wire(session, row)
        open_warning_ids = sorted(
            {
                *warning_values.get("warningIds", []),
                *(dynamic_blockers if state == "invalidated" else []),
            }
        )
        if len(open_warning_ids) > MAX_CASTING_WARNINGS_PER_ENTITY:
            open_warning_ids = [
                *open_warning_ids[: MAX_CASTING_WARNINGS_PER_ENTITY - 1],
                "casting-warning-limit-exceeded",
            ]
        return {
            "contractVersion": CASTING_CONTRACT_VERSION,
            "reviewId": stable_id(
                "casting-gate-review",
                row.casting_run_id,
                row.gate_id,
            ),
            "gateId": row.gate_id,
            "projectId": row.project_id,
            "castingRunId": row.casting_run_id,
            "state": state,
            "revision": row.revision,
            "evidence": evidence,
            "prerequisiteGateIds": parse_json(
                row.required_gate_decision_ids_json,
                [],
            ),
            "openWarningIds": open_warning_ids,
            "acknowledgedWarningIds": (
                parse_json(latest.warning_acknowledgements_json, [])
                if latest is not None and state != "invalidated"
                else []
            ),
            "latestDecision": (
                self._decision_wire(session, latest)
                if latest is not None and not dynamically_invalidated
                else None
            ),
            "provenance": parse_json(row.provenance_json, {}),
            "updatedAt": (
                latest.decided_at
                if latest is not None and latest.decided_at is not None
                else row.created_at
            ),
        }

    def _run_dict_for_snapshot(
        self,
        session: Session,
        *,
        run: CastingRunRow,
        snapshot: ApprovedCastSnapshotRow,
    ) -> dict[str, Any]:
        result = self.run_dict(session, run)
        manifest = self._validate_snapshot_manifest(
            session,
            snapshot=snapshot,
            run=run,
        )
        return {
            **result,
            "effectiveCorrectionSetFingerprint": (snapshot.effective_correction_set_fingerprint),
            "outputFingerprint": snapshot.snapshot_fingerprint,
            "summary": manifest["counts"],
            "approvedCastSnapshot": self._snapshot_wire(
                session,
                snapshot,
            ),
        }

    def _reviews_wire(
        self,
        session: Session,
        run: CastingRunRow,
        *,
        synchronize_invalidations: bool = True,
    ) -> list[dict[str, Any]]:
        if synchronize_invalidations:
            self._sync_external_assignment_invalidations(
                session,
                run=run,
            )
        result: list[dict[str, Any]] = []
        for gate_id in CASTING_GATE_IDS:
            row = self._refresh_gate_review_evidence(
                session,
                run=run,
                gate_id=gate_id,
            )
            if row is not None:
                result.append(self._review_wire(session, row))
        return result

    def list_reviews(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence: dict[str, Any],
        expected_cast_snapshot_id: str,
        expected_cast_snapshot_revision: int,
    ) -> list[dict[str, Any]]:
        with self._review_refresh_lock:
            self._latch_external_assignment_invalidations_for_read(
                project_id=project_id,
                run_id=run_id,
                evidence=evidence,
                expected_cast_snapshot_id=expected_cast_snapshot_id,
                expected_cast_snapshot_revision=expected_cast_snapshot_revision,
            )
            with self.database.immediate_session() as session:
                run = self._require_assignment_read_run(
                    session,
                    project_id=project_id,
                    run_id=run_id,
                    evidence=evidence,
                    expected_cast_snapshot_id=expected_cast_snapshot_id,
                    expected_cast_snapshot_revision=expected_cast_snapshot_revision,
                )
                return self._reviews_wire(
                    session,
                    run,
                    synchronize_invalidations=False,
                )

    def decide_review(
        self,
        *,
        project_id: str,
        run_id: str,
        gate_id: str,
        decision: str,
        expected_revision: int,
        expected_evidence_fingerprint: str,
        expected_run_fingerprint: str,
        expected_approved_cast_snapshot_id: str,
        expected_approved_cast_snapshot_revision: int,
        warning_acknowledgement_ids: list[str],
        rationale: str,
        supersedes_decision_id: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        states = {
            "approve": "approved",
            "request_changes": "changes_requested",
            "reject": "rejected",
        }
        state = states.get(decision)
        if gate_id not in CASTING_GATE_IDS or state is None:
            raise not_found("casting review gate")
        decision_request_fingerprint = request_fingerprint(
            {
                "projectId": project_id,
                "castingRunId": run_id,
                "gateId": gate_id,
                "decision": state,
                "expectedRevision": expected_revision,
                "expectedEvidenceFingerprint": expected_evidence_fingerprint,
                "expectedRunFingerprint": expected_run_fingerprint,
                "expectedApprovedCastSnapshotId": expected_approved_cast_snapshot_id,
                "expectedApprovedCastSnapshotRevision": (expected_approved_cast_snapshot_revision),
                "warningAcknowledgementIds": warning_acknowledgement_ids,
                "rationale": rationale,
                "supersedesDecisionId": supersedes_decision_id,
            }
        )
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = self.require_run(
                session,
                project_id=project_id,
                run_id=run_id,
            )
            idempotent = session.scalar(
                select(CastingGateDecisionRow).where(
                    CastingGateDecisionRow.casting_run_id == run.id,
                    CastingGateDecisionRow.gate_id == gate_id,
                    CastingGateDecisionRow.idempotency_key == idempotency_key,
                )
            )
            if idempotent is not None:
                idempotent_snapshot = session.get(
                    ApprovedCastSnapshotRow,
                    idempotent.cast_snapshot_id,
                )
                idempotent_review = session.get(
                    CastingGateReviewRow,
                    idempotent.gate_review_id,
                )
                if (
                    idempotent_snapshot is None
                    or idempotent_review is None
                    or idempotent.decision != state
                    or idempotent.evidence_fingerprint != expected_evidence_fingerprint
                    or idempotent.rationale != rationale
                    or parse_json(idempotent.warning_acknowledgements_json, [])
                    != warning_acknowledgement_ids
                    or idempotent_review.revision != expected_revision
                    or idempotent_snapshot.id != expected_approved_cast_snapshot_id
                    or idempotent_snapshot.revision != expected_approved_cast_snapshot_revision
                    or parse_json(idempotent.provenance_json, {}).get("requestFingerprint")
                    != decision_request_fingerprint
                ):
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was used for another decision.",
                    )
                self._sync_external_assignment_invalidations(
                    session,
                    run=run,
                )
                return (
                    self._review_wire(
                        session,
                        idempotent_review,
                        decision_override=idempotent,
                    ),
                    self._decision_wire(session, idempotent),
                    self._snapshot_wire(session, idempotent_snapshot),
                    self._run_dict_for_snapshot(
                        session,
                        run=run,
                        snapshot=idempotent_snapshot,
                    ),
                )
            self.assert_evidence(
                session,
                run=run,
                expected_run_fingerprint=expected_run_fingerprint,
                expected_catalog_fingerprint=run.catalog_fingerprint,
                expected_snapshot_id=run.analysis_snapshot_id,
                expected_snapshot_revision=run.analysis_snapshot_revision,
                expected_snapshot_fingerprint=(run.analysis_snapshot_fingerprint),
            )
            self._assert_current_catalog_write_evidence(run)
            self._sync_external_assignment_invalidations(
                session,
                run=run,
            )
            current = self._refresh_gate_review_evidence(
                session,
                run=run,
                gate_id=gate_id,
            )
            if current is None:
                raise not_found("casting review gate")
            snapshot = session.get(
                ApprovedCastSnapshotRow,
                current.cast_snapshot_id,
            )
            if (
                snapshot is None
                or current.revision != expected_revision
                or current.evidence_fingerprint != expected_evidence_fingerprint
                or snapshot.id != expected_approved_cast_snapshot_id
                or snapshot.revision != expected_approved_cast_snapshot_revision
            ):
                raise ServiceError(
                    409,
                    "CASTING_REVIEW_EVIDENCE_STALE",
                    "The casting review evidence changed; refresh first.",
                )
            latest = self._latest_decision(
                session,
                run_id=run.id,
                gate_id=gate_id,
            )
            current_decision = (
                latest if latest is not None and latest.gate_review_id == current.id else None
            )
            if (current_decision is None and supersedes_decision_id is not None) or (
                current_decision is not None and current_decision.id != supersedes_decision_id
            ):
                raise ServiceError(
                    409,
                    "CASTING_DECISION_SUPERSESSION_STALE",
                    "The decision to supersede is no longer current.",
                )
            warning_values = parse_json(current.warnings_json, {})
            known_warnings = set(warning_values.get("warningIds", []))
            supplied = set(warning_acknowledgement_ids)
            if not supplied.issubset(known_warnings):
                raise ServiceError(
                    409,
                    "CASTING_WARNING_STALE",
                    "A warning acknowledgement is not current.",
                )
            blockers, current_warnings = self._review_blockers(
                session,
                run=run,
                gate_id=gate_id,
            )
            if state == "approved":
                if blockers:
                    raise ServiceError(
                        409,
                        "CASTING_REVIEW_NOT_ELIGIBLE",
                        "The cast is not eligible for this approval gate.",
                        details={
                            "blockingReasonCodes": ",".join(blockers),
                        },
                    )
                if set(current_warnings) - supplied:
                    raise ServiceError(
                        409,
                        "CASTING_WARNINGS_UNACKNOWLEDGED",
                        "Acknowledge current casting warnings before approval.",
                    )
                if gate_id == "complete_cast_review":
                    for upstream in CASTING_GATE_IDS[:2]:
                        upstream_review = self._latest_review(
                            session,
                            run_id=run.id,
                            gate_id=upstream,
                        )
                        upstream_decision = self._latest_decision(
                            session,
                            run_id=run.id,
                            gate_id=upstream,
                        )
                        if (
                            upstream_review is None
                            or upstream_review.cast_snapshot_id != snapshot.id
                            or upstream_decision is None
                            or upstream_decision.decision != "approved"
                            or upstream_decision.cast_snapshot_id != snapshot.id
                            or upstream_decision.gate_review_id != upstream_review.id
                        ):
                            raise ServiceError(
                                409,
                                "CASTING_UPSTREAM_APPROVAL_REQUIRED",
                                "Current narrator and character approvals are required.",
                            )
            now = utc_now()
            revision = 1 if latest is None else latest.revision + 1
            row = CastingGateDecisionRow(
                id=new_id(),
                project_id=project_id,
                casting_run_id=run.id,
                cast_snapshot_id=snapshot.id,
                gate_review_id=current.id,
                gate_id=gate_id,
                revision=revision,
                decision=state,
                evidence_fingerprint=current.evidence_fingerprint,
                actor_id="local_user",
                warning_acknowledgements_json=canonical_json(warning_acknowledgement_ids),
                rationale=rationale,
                provenance_json=canonical_json(
                    {
                        "origin": "human",
                        "producerId": "local_user",
                        "producerVersion": "1.0.0",
                        "recordedAt": now,
                        "inputFingerprint": (current.evidence_fingerprint),
                        "requestFingerprint": decision_request_fingerprint,
                    }
                ),
                supersedes_decision_id=(latest.id if latest is not None else None),
                idempotency_key=idempotency_key,
                decided_at=now,
                created_at=now,
            )
            session.add(row)
            session.flush()
            return (
                self._review_wire(session, current),
                self._decision_wire(session, row),
                self._snapshot_wire(session, snapshot),
                self.run_dict(session, run),
            )

    def mark_job_terminal(
        self,
        session: Session,
        *,
        job: JobRow,
        state: str,
    ) -> None:
        if job.type != _CASTING_JOB_TYPE or job.target_id is None:
            return
        run = session.get(CastingRunRow, job.target_id)
        if run is not None:
            run.state = state

    def _project_summary_run(
        self,
        session: Session,
        *,
        project_id: str,
    ) -> CastingRunRow | None:
        latest_run = session.scalar(
            select(CastingRunRow)
            .where(CastingRunRow.project_id == project_id)
            .order_by(
                CastingRunRow.created_at.desc(),
                CastingRunRow.id.desc(),
            )
            .limit(1)
        )
        if latest_run is None:
            return None
        if latest_run.state not in {"failed", "cancelled", "interrupted"}:
            return latest_run
        effective_run = session.scalar(
            select(CastingRunRow)
            .where(
                CastingRunRow.project_id == project_id,
                CastingRunRow.state == "succeeded",
            )
            .order_by(
                CastingRunRow.published_at.desc(),
                CastingRunRow.id.desc(),
            )
            .limit(1)
        )
        if (
            effective_run is not None
            and self._latest_snapshot(session, effective_run.id) is not None
        ):
            return effective_run
        return latest_run

    def _latch_project_summary_assignment_invalidations(
        self,
        *,
        project_id: str,
    ) -> None:
        with self.database.session() as session:
            self.projects.require_project(session, project_id)
            run = self._project_summary_run(
                session,
                project_id=project_id,
            )
            if run is None:
                return
            preflight_run_id = run.id
            candidate_ids = self._external_assignment_drift_candidate_ids(
                session,
                run=run,
            )
        if not candidate_ids:
            return
        with self.database.immediate_session() as session:
            self.projects.require_project(session, project_id)
            run = self._project_summary_run(
                session,
                project_id=project_id,
            )
            if run is None:
                return
            self._sync_external_assignment_invalidations(
                session,
                run=run,
                candidate_assignment_ids=(
                    frozenset(candidate_ids) if run.id == preflight_run_id else None
                ),
            )

    def project_summary(self, project_id: str) -> dict[str, Any]:
        with self._review_refresh_lock:
            self._latch_project_summary_assignment_invalidations(
                project_id=project_id,
            )
            with self.database.immediate_session() as session:
                self.projects.require_project(session, project_id)
                run = self._project_summary_run(
                    session,
                    project_id=project_id,
                )
                if run is None:
                    catalog = self.catalog
                    return {
                        "contractVersion": CASTING_CONTRACT_VERSION,
                        "currentRun": None,
                        "catalogRevision": catalog.revision,
                        "catalogFingerprint": catalog.fingerprint,
                        "profile": casting_profile(),
                        "gateReviews": [],
                    }
                return {
                    "contractVersion": CASTING_CONTRACT_VERSION,
                    "currentRun": self.run_dict(session, run),
                    "catalogRevision": self.catalog.revision,
                    "catalogFingerprint": self.catalog.fingerprint,
                    "profile": casting_profile(),
                    "gateReviews": self._reviews_wire(
                        session,
                        run,
                        synchronize_invalidations=False,
                    ),
                }
