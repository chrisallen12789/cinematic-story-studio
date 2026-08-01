from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.audition_repository import _audio_quality_fingerprint
from cinematic_story_service.auditions import (
    AUDITION_PROFILE_FINGERPRINT,
    AuditionCacheIdentity,
)
from cinematic_story_service.casting import (
    CASTING_CONTRACT_VERSION,
    CASTING_GATE_IDS,
    generate_candidates,
)
from cinematic_story_service.casting_repository import (
    CastingRepository,
    _deterministic_machine_fingerprint,
)
from cinematic_story_service.database import Database
from cinematic_story_service.models import (
    AudioArtifactRow,
    AudioQualityRecordRow,
    AuditionCacheRecordRow,
    AuditionClipRow,
    AuditionScriptRow,
    AuditionSessionRow,
    CastAssignmentRow,
    CastingCandidateRow,
    CastingCorrectionRow,
    CastingGateDecisionRow,
    CastingRunRow,
    JobAttemptRow,
    JobRow,
    ModelPackageManifestRow,
    ProductionRoleRow,
    PronunciationDictionaryRow,
    PronunciationEntryRow,
    SpeechProviderRequestRow,
    SpeechRuntimeInstanceRow,
    SpeechRuntimeProfileRow,
    TextNormalizationPlanRow,
    VoiceProfileRow,
)
from cinematic_story_service.pronunciation import PRONUNCIATION_PROFILE_VERSION
from cinematic_story_service.util import (
    canonical_json,
    parse_json,
    request_fingerprint,
    sha256_text,
    stable_id,
    utc_now,
)
from tests.conftest import wait_for_job
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _create_session_and_script,
    _establish_approved_cast,
    _generate,
    _workspace,
)

_ROLE_COUNT = 300
_PRONUNCIATION_COUNT = 1_000
_AUDITION_METADATA_COUNT = 2_000
_CACHE_COUNT = 10_000
_MAX_PAGE_SIZE = 200
_READ_PAGE_SIZE = 2
_MAX_BOUNDARY_SECONDS = 30.0


@contextmanager
def _timed_client(
    settings: ServiceSettings,
) -> Iterator[tuple[TestClient, dict[str, float]]]:
    timings: dict[str, float] = {}
    started = time.perf_counter()
    client = TestClient(create_app(settings))
    with client:
        timings["startupSeconds"] = time.perf_counter() - started
        try:
            yield client, timings
        finally:
            shutdown_started = time.perf_counter()
    timings["shutdownSeconds"] = time.perf_counter() - shutdown_started


def _queue_generation(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    session_id: str,
    generation_request: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions/{session_id}/generate",
        headers=auth_headers,
        json={"preview": generation_request},
    )
    assert response.status_code == 202, response.text
    return cast(dict[str, Any], response.json())


def _row_mapping(row: Any) -> dict[str, Any]:
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


def _scale_timestamp(ordinal: int, *, year: int = 2099) -> str:
    return f"{year:04d}-01-01T00:00:00.{ordinal:06d}Z"


def _seed_roles_and_pronunciations(
    database: Database,
    casting: CastingRepository,
    *,
    project_id: str,
) -> tuple[str, int, int]:
    helper_started = time.perf_counter()
    assignment_seed_seconds = 0.0
    snapshot_publish_seconds = 0.0
    gate_refresh_decision_seconds = 0.0
    with database.immediate_session() as session:
        casting_run = session.scalar(
            select(CastingRunRow)
            .where(
                CastingRunRow.project_id == project_id,
                CastingRunRow.state == "succeeded",
            )
            .order_by(CastingRunRow.created_at.desc(), CastingRunRow.id.desc())
            .limit(1)
        )
        assert casting_run is not None
        roles = list(
            session.scalars(
                select(ProductionRoleRow)
                .where(ProductionRoleRow.project_id == project_id)
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        )
        assert 0 < len(roles) <= _ROLE_COUNT
        current_assignments: dict[str, CastAssignmentRow] = {}
        selected_assignments: dict[str, CastAssignmentRow] = {}
        intentionally_uncast_roles: list[ProductionRoleRow] = []
        for role in roles:
            assignment = session.scalar(
                select(CastAssignmentRow)
                .where(
                    CastAssignmentRow.role_id == role.id,
                    CastAssignmentRow.authority.in_(("human_selection", "human_locked")),
                )
                .order_by(CastAssignmentRow.revision.desc(), CastAssignmentRow.id.desc())
                .limit(1)
            )
            assert assignment is not None
            current_assignments[role.id] = assignment
            if assignment.assignment_state in {"selected", "locked"}:
                assert role.status == "active"
                assert assignment.voice_profile_record_id is not None
                selected_assignments[role.id] = assignment
            else:
                assert role.status == "unresolved"
                assert assignment.assignment_state == "intentionally_uncast"
                assert assignment.voice_profile_record_id is None
                intentionally_uncast_roles.append(role)
        assert selected_assignments
        template_role = next(
            (role for role in roles if role.role_type == "named_character"),
            roles[0],
        )
        template_assignment = selected_assignments[template_role.id]
        assert template_assignment is not None
        assert template_assignment.voice_profile_record_id is not None
        template_role_values = casting._role_wire(session, template_role)

        for role in intentionally_uncast_roles:
            latest_role_correction = session.scalar(
                select(CastingCorrectionRow)
                .where(CastingCorrectionRow.role_id == role.id)
                .order_by(
                    CastingCorrectionRow.revision.desc(),
                    CastingCorrectionRow.id.desc(),
                )
                .limit(1)
            )
            prior_requirement = session.scalar(
                select(CastingCorrectionRow)
                .where(
                    CastingCorrectionRow.role_id == role.id,
                    CastingCorrectionRow.kind == "change_casting_requirement",
                )
                .order_by(
                    CastingCorrectionRow.revision.desc(),
                    CastingCorrectionRow.id.desc(),
                )
                .limit(1)
            )
            recorded_at = _scale_timestamp(role.ordinal, year=2096)
            prior_effective_fingerprint = casting._effective_role_fingerprint(
                session,
                role,
            )
            corrected_value = {"requirement": template_role_values["performanceRequirements"]}
            requirement_correction = CastingCorrectionRow(
                id=f"scale-requirement-{role.ordinal:04d}",
                project_id=project_id,
                casting_run_id=casting_run.id,
                role_id=role.id,
                kind="change_casting_requirement",
                revision=(latest_role_correction.revision + 1 if latest_role_correction else 1),
                prior_effective_fingerprint=prior_effective_fingerprint,
                corrected_value_json=canonical_json(corrected_value),
                correction_fingerprint="",
                actor_id="local_user",
                reason="Apply governed synthetic scale language requirements.",
                provenance_json=canonical_json(
                    {
                        "origin": "human",
                        "producerId": "phase3b-scale-test",
                        "producerVersion": "1.0.0",
                        "recordedAt": recorded_at,
                        "inputFingerprint": prior_effective_fingerprint,
                        "requestFingerprint": request_fingerprint(
                            {
                                "operation": "change_casting_requirement",
                                "roleId": role.id,
                                "value": corrected_value,
                            }
                        ),
                    }
                ),
                supersedes_correction_id=(
                    prior_requirement.id if prior_requirement is not None else None
                ),
                idempotency_key=f"phase3b-scale-requirement-{role.ordinal:04d}",
                recorded_at=recorded_at,
            )
            requirement_correction.correction_fingerprint = request_fingerprint(
                casting._correction_material(requirement_correction)
            )
            session.add(requirement_correction)
        session.flush()
        casting_run.effective_correction_set_fingerprint = casting._correction_fingerprint(
            session,
            casting_run.id,
        )
        session.flush()

        role_mappings: list[dict[str, Any]] = []
        first_new_ordinal = max(role.ordinal for role in roles) + 1
        last_new_ordinal = first_new_ordinal + _ROLE_COUNT - len(roles) - 1
        for ordinal in range(first_new_ordinal, last_new_ordinal + 1):
            role_id = f"scale-role-{ordinal:04d}"
            role_mapping = _row_mapping(template_role)
            role_mapping.update(
                {
                    "id": role_id,
                    "ordinal": ordinal,
                    "role_type": "named_character",
                    "phase2_entity_id": None,
                    "character_id": f"scale-character-{ordinal:04d}",
                    "role_importance": "minor",
                    "effective_display_label": f"Scale role {ordinal:04d}",
                    "dialogue_line_count": 1,
                    "narration_span_count": 0,
                    "approximate_word_count": 4,
                    "language_requirements_json": canonical_json(
                        template_role_values["languageRequirements"]
                    ),
                    "performance_requirements_json": canonical_json(
                        template_role_values["performanceRequirements"]
                    ),
                    "status": "active",
                    "created_at": _scale_timestamp(ordinal, year=2097),
                }
            )
            role_mapping["role_fingerprint"] = request_fingerprint(
                {
                    "contractVersion": CASTING_CONTRACT_VERSION,
                    "roleId": role_mapping["id"],
                    "projectId": role_mapping["project_id"],
                    "roleType": role_mapping["role_type"],
                    "analysisEntityId": role_mapping["phase2_entity_id"],
                    "effectiveDisplayLabel": role_mapping["effective_display_label"],
                    "analysisRunId": role_mapping["analysis_run_id"],
                    "analysisSnapshotId": role_mapping["analysis_snapshot_id"],
                    "analysisSnapshotFingerprint": casting_run.analysis_snapshot_fingerprint,
                    "dialogueLineCount": role_mapping["dialogue_line_count"],
                    "narrationSpanCount": role_mapping["narration_span_count"],
                    "approximateWordCount": role_mapping["approximate_word_count"],
                    "chapterRange": parse_json(role_mapping["chapter_range_json"], {}),
                    "sceneRange": parse_json(role_mapping["scene_range_json"], {}),
                    "languageRequirements": parse_json(
                        role_mapping["language_requirements_json"],
                        [],
                    ),
                    "performanceRequirements": parse_json(
                        role_mapping["performance_requirements_json"],
                        {},
                    ),
                    "warnings": parse_json(role_mapping["warnings_json"], []),
                    "provenance": parse_json(role_mapping["provenance_json"], {}),
                    "status": "active",
                    "revision": 1,
                    "characterId": role_mapping["character_id"],
                    "roleImportance": role_mapping["role_importance"],
                    "unresolvedMaterialExplicitlyRepresented": False,
                }
            )
            role_mappings.append(role_mapping)

        if role_mappings:
            session.execute(insert(ProductionRoleRow), role_mappings)
        session.flush()

        new_role_ids = [str(value["id"]) for value in role_mappings]
        new_roles = list(
            session.scalars(
                select(ProductionRoleRow)
                .where(
                    ProductionRoleRow.casting_run_id == casting_run.id,
                    ProductionRoleRow.id.in_(new_role_ids),
                )
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        )
        assert len(new_roles) == _ROLE_COUNT - len(roles)
        voice_rows = casting._voice_rows(session, casting_run.catalog_revision_id)
        assignment_inputs: list[
            tuple[ProductionRoleRow, CastingCorrectionRow, VoiceProfileRow]
        ] = []
        for role in [*intentionally_uncast_roles, *new_roles]:
            role_revision = casting._role_revision(session, role.id)
            role_assessment = casting._effective_role_assessment_values(
                session,
                role,
            )
            candidate_input_fingerprint = request_fingerprint(
                {
                    "castingRunInputFingerprint": casting_run.input_fingerprint,
                    "roleAssessment": role_assessment,
                    "catalogFingerprint": casting_run.catalog_fingerprint,
                    "castingProfileFingerprint": (casting_run.casting_profile_fingerprint),
                    "effectiveCorrectionSetFingerprint": (
                        casting_run.effective_correction_set_fingerprint
                    ),
                    "roleRevision": role_revision,
                }
            )
            generated_candidates, _role_conflicts = generate_candidates(
                roles=[role_assessment],
                catalog=casting.catalog,
                input_fingerprint=candidate_input_fingerprint,
            )
            selected_candidate = next(
                value
                for value in generated_candidates
                if value["compatibilityStatus"] in {"eligible", "conditional"}
                and value["rightsEligibility"] == "verified"
                and value["languageEligibility"] == "eligible"
                and value["providerAvailability"] is True
                and value["modelAvailability"] is True
                and value["longFormSuitability"] is True
                and value["conflictWarnings"] == []
            )
            voice_profile_id = str(selected_candidate["voiceProfileId"])
            selected_voice = voice_rows[voice_profile_id]
            recorded_at = _scale_timestamp(role.ordinal, year=2097)
            candidate_provenance = {
                **dict(selected_candidate["provenance"]),
                "recordedAt": recorded_at,
            }
            candidate_output_fingerprint = _deterministic_machine_fingerprint(
                {
                    **selected_candidate,
                    "provenance": candidate_provenance,
                }
            )
            raw_rights = str(selected_candidate["rightsEligibility"])
            session.add(
                CastingCandidateRow(
                    id=str(selected_candidate["candidateId"]),
                    project_id=project_id,
                    casting_run_id=casting_run.id,
                    role_id=role.id,
                    voice_profile_record_id=selected_voice.id,
                    role_revision=role_revision,
                    ordinal=int(selected_candidate["ordinal"]),
                    compatibility_status=str(selected_candidate["compatibilityStatus"]),
                    compatibility_score=int(
                        round(float(selected_candidate["compatibilityScore"]) * 1_000_000)
                    ),
                    confidence_class=str(selected_candidate["confidenceClassification"]),
                    hard_constraint_results_json=canonical_json(
                        selected_candidate["hardConstraintResults"]
                    ),
                    soft_preference_results_json=canonical_json(
                        selected_candidate["softPreferenceResults"]
                    ),
                    rights_eligibility={
                        "verified": "eligible",
                        "restricted": "restricted",
                        "unknown": "unknown",
                        "prohibited": "ineligible",
                    }.get(raw_rights, raw_rights),
                    language_eligibility=str(selected_candidate["languageEligibility"]),
                    provider_availability="available",
                    model_availability="available",
                    long_form_suitability="suitable",
                    conflict_warnings_json="[]",
                    explanation_json=canonical_json(
                        {
                            "text": selected_candidate["explanation"],
                            "preReductionRank": selected_candidate["preReductionRank"],
                        }
                    ),
                    provenance_json=canonical_json(candidate_provenance),
                    input_fingerprint=candidate_input_fingerprint,
                    output_fingerprint=candidate_output_fingerprint,
                    created_at=recorded_at,
                )
            )

            prior_effective_fingerprint = casting._effective_role_fingerprint(
                session,
                role,
            )
            latest_role_correction = session.scalar(
                select(CastingCorrectionRow)
                .where(CastingCorrectionRow.role_id == role.id)
                .order_by(
                    CastingCorrectionRow.revision.desc(),
                    CastingCorrectionRow.id.desc(),
                )
                .limit(1)
            )
            prior_assignment = current_assignments.get(role.id)
            correction = CastingCorrectionRow(
                id=f"scale-correction-{role.ordinal:04d}",
                project_id=project_id,
                casting_run_id=casting_run.id,
                role_id=role.id,
                kind="select_voice",
                revision=(latest_role_correction.revision + 1 if latest_role_correction else 1),
                prior_effective_fingerprint=prior_effective_fingerprint,
                corrected_value_json=canonical_json({"voiceProfileId": voice_profile_id}),
                correction_fingerprint="",
                actor_id="local_user",
                reason="Select a repository-owned synthetic scale voice.",
                provenance_json=canonical_json(
                    {
                        "origin": "human",
                        "producerId": "phase3b-scale-test",
                        "producerVersion": "1.0.0",
                        "recordedAt": recorded_at,
                        "inputFingerprint": prior_effective_fingerprint,
                        "requestFingerprint": request_fingerprint(
                            {
                                "operation": "select_voice",
                                "roleId": role.id,
                                "voiceProfileId": voice_profile_id,
                            }
                        ),
                    }
                ),
                supersedes_correction_id=(
                    prior_assignment.correction_id if prior_assignment is not None else None
                ),
                idempotency_key=f"phase3b-scale-select-{role.ordinal:04d}",
                recorded_at=recorded_at,
            )
            correction.correction_fingerprint = request_fingerprint(
                casting._correction_material(correction)
            )
            session.add(correction)
            assignment_inputs.append((role, correction, selected_voice))
        session.flush()
        casting_run.effective_correction_set_fingerprint = casting._correction_fingerprint(
            session, casting_run.id
        )
        session.flush()
        for role, correction, selected_voice in assignment_inputs:
            recorded_at = _scale_timestamp(role.ordinal, year=2097)
            prior_assignment = current_assignments.get(role.id)
            session.add(
                CastAssignmentRow(
                    id=f"scale-assignment-{role.ordinal:04d}",
                    project_id=project_id,
                    casting_run_id=casting_run.id,
                    role_id=role.id,
                    correction_id=correction.id,
                    voice_profile_record_id=selected_voice.id,
                    catalog_revision_id=casting_run.catalog_revision_id,
                    casting_profile_fingerprint=(casting_run.casting_profile_fingerprint),
                    phase2_snapshot_fingerprint=(casting_run.analysis_snapshot_fingerprint),
                    effective_correction_set_fingerprint=(
                        casting_run.effective_correction_set_fingerprint
                    ),
                    authority="human_selection",
                    assignment_state="selected",
                    rationale="Select a repository-owned synthetic scale voice.",
                    warnings_json="[]",
                    rights_state=selected_voice.rights_state,
                    revision=(prior_assignment.revision + 1 if prior_assignment else 1),
                    provenance_json=canonical_json(
                        {
                            "origin": "human",
                            "producerId": "phase3b-scale-test",
                            "producerVersion": "1.0.0",
                            "recordedAt": recorded_at,
                            "inputFingerprint": correction.correction_fingerprint,
                        }
                    ),
                    supersedes_assignment_id=(
                        prior_assignment.id if prior_assignment is not None else None
                    ),
                    created_at=recorded_at,
                )
            )
        session.flush()
        assignment_seed_seconds = time.perf_counter() - helper_started

        # Direct fixture seeding makes the prior snapshot historically valid but
        # no longer current. Publish the new exact snapshot, then refresh each
        # gate's immutable review against that same now-current snapshot.
        snapshot_started = time.perf_counter()
        scale_snapshot = casting._publish_cast_snapshot(
            session,
            run=casting_run,
            now=_scale_timestamp(999_999, year=2097),
        )
        session.flush()
        snapshot_publish_seconds = time.perf_counter() - snapshot_started
        gate_refresh_started = time.perf_counter()
        for gate_ordinal, gate_id in enumerate(CASTING_GATE_IDS):
            review = casting._refresh_gate_review_evidence(
                session,
                run=casting_run,
                gate_id=gate_id,
            )
            assert review is not None
            assert review.cast_snapshot_id == scale_snapshot.id
            warning_evidence = parse_json(review.warnings_json, {})
            warning_ids = warning_evidence.get("warningIds")
            assert isinstance(warning_ids, list)
            assert warning_evidence.get("blockingReasonCodes") == []
            assert review.eligible is True
            previous_decision = session.scalar(
                select(CastingGateDecisionRow)
                .where(
                    CastingGateDecisionRow.casting_run_id == casting_run.id,
                    CastingGateDecisionRow.gate_id == gate_id,
                )
                .order_by(
                    CastingGateDecisionRow.revision.desc(),
                    CastingGateDecisionRow.id.desc(),
                )
                .limit(1)
            )
            decided_at = _scale_timestamp(gate_ordinal, year=2098)
            rationale = "Approve repository-owned synthetic scale evidence."
            request_supersedes_decision_id = (
                previous_decision.id
                if previous_decision is not None and previous_decision.gate_review_id == review.id
                else None
            )
            decision_request_fingerprint = request_fingerprint(
                {
                    "projectId": project_id,
                    "castingRunId": casting_run.id,
                    "gateId": gate_id,
                    "decision": "approved",
                    "expectedRevision": review.revision,
                    "expectedEvidenceFingerprint": review.evidence_fingerprint,
                    "expectedRunFingerprint": scale_snapshot.snapshot_fingerprint,
                    "expectedApprovedCastSnapshotId": scale_snapshot.id,
                    "expectedApprovedCastSnapshotRevision": scale_snapshot.revision,
                    "warningAcknowledgementIds": warning_ids,
                    "rationale": rationale,
                    "supersedesDecisionId": request_supersedes_decision_id,
                }
            )
            decision_id = stable_id(
                "phase3b-scale-casting-decision",
                scale_snapshot.id,
                gate_id,
            )
            session.add(
                CastingGateDecisionRow(
                    id=decision_id,
                    project_id=project_id,
                    casting_run_id=casting_run.id,
                    cast_snapshot_id=scale_snapshot.id,
                    gate_review_id=review.id,
                    gate_id=gate_id,
                    revision=(previous_decision.revision + 1 if previous_decision else 1),
                    decision="approved",
                    evidence_fingerprint=review.evidence_fingerprint,
                    actor_id="local_user",
                    warning_acknowledgements_json=canonical_json(warning_ids),
                    rationale=rationale,
                    provenance_json=canonical_json(
                        {
                            "origin": "human",
                            "producerId": "local_user",
                            "producerVersion": "1.0.0",
                            "recordedAt": decided_at,
                            "inputFingerprint": review.evidence_fingerprint,
                            "requestFingerprint": decision_request_fingerprint,
                        }
                    ),
                    supersedes_decision_id=(
                        previous_decision.id if previous_decision is not None else None
                    ),
                    idempotency_key=f"phase3b-scale-{gate_id}",
                    decided_at=decided_at,
                    created_at=decided_at,
                )
            )
            session.flush()
        gate_refresh_decision_seconds = time.perf_counter() - gate_refresh_started

        current_dictionary = session.scalar(
            select(PronunciationDictionaryRow)
            .where(PronunciationDictionaryRow.project_id == project_id)
            .order_by(
                PronunciationDictionaryRow.revision.desc(),
                PronunciationDictionaryRow.id.desc(),
            )
            .limit(1)
        )
        assert current_dictionary is not None
        assert current_dictionary.revision == 1
        dictionary_record_id = "scale-pronunciation-dictionary-v2"
        entry_mappings: list[dict[str, Any]] = []
        dictionary_material: list[dict[str, Any]] = []
        active_entry_ids: list[str] = []
        for ordinal in range(_PRONUNCIATION_COUNT):
            entry_id = f"scale-pronunciation-{ordinal:04d}"
            entry_fingerprint = request_fingerprint(
                {
                    "entryId": entry_id,
                    "pronunciation": f"scale-{ordinal:04d}",
                    "scope": "project",
                }
            )
            active_entry_ids.append(entry_id)
            dictionary_material.append(
                {
                    "caseSensitive": False,
                    "entryFingerprint": entry_fingerprint,
                    "entryId": entry_id,
                    "entryRevision": 1,
                    "matchRule": "whole_word",
                    "scope": "project",
                    "scopeId": None,
                    "verificationState": "approved",
                }
            )
            entry_mappings.append(
                {
                    "id": f"scale-pron-row-{ordinal:04d}",
                    "project_id": project_id,
                    "dictionary_record_id": dictionary_record_id,
                    "dictionary_id": current_dictionary.dictionary_id,
                    "dictionary_revision": 2,
                    "entry_id": entry_id,
                    "revision": 1,
                    "written_form": f"ScaleName{ordinal:04d}",
                    "normalized_lookup_form": f"scalename{ordinal:04d}",
                    "language": "en",
                    "locale": "en-US",
                    "scope_type": "project",
                    "scope_target_id": None,
                    "provider_neutral_value": f"scale-{ordinal:04d}",
                    "ipa_value": None,
                    "provider_specific_json": "{}",
                    "case_sensitive": False,
                    "whole_word": True,
                    "priority": 0,
                    "verification_state": "approved",
                    "entry_fingerprint": entry_fingerprint,
                    "actor_id": "scale_fixture",
                    "reason": "Repository-owned metadata-only pronunciation scale fixture.",
                    "supersedes_entry_record_id": None,
                    "provenance_json": canonical_json(
                        {
                            "origin": "fixture",
                            "producerId": "phase3b-scale-test",
                            "producerVersion": "1.0.0",
                        }
                    ),
                    "created_at": _scale_timestamp(ordinal, year=2098),
                }
            )
        dictionary_fingerprint = request_fingerprint(
            {
                "entries": dictionary_material,
                "profileVersion": PRONUNCIATION_PROFILE_VERSION,
                "revision": 2,
            }
        )
        session.add(
            PronunciationDictionaryRow(
                id=dictionary_record_id,
                project_id=project_id,
                dictionary_id=current_dictionary.dictionary_id,
                revision=2,
                default_language="en",
                default_locale="en-US",
                entry_count=_PRONUNCIATION_COUNT,
                active_entry_ids_json=canonical_json(sorted(active_entry_ids)),
                dictionary_fingerprint=dictionary_fingerprint,
                producer_id="phase3b-scale-test",
                producer_version="1.0.0",
                supersedes_dictionary_record_id=current_dictionary.id,
                provenance_json=canonical_json(
                    {
                        "origin": "fixture",
                        "producerId": "phase3b-scale-test",
                        "producerVersion": "1.0.0",
                    }
                ),
                created_at=_scale_timestamp(0, year=2098),
            )
        )
        session.flush()
        session.execute(insert(PronunciationEntryRow), entry_mappings)

        active_role_count = int(
            session.scalar(
                select(func.count())
                .select_from(ProductionRoleRow)
                .where(
                    ProductionRoleRow.project_id == project_id,
                    ProductionRoleRow.status.in_(("active", "unresolved")),
                )
            )
            or 0
        )
        assert active_role_count == _ROLE_COUNT
    print(
        "phase3b-scale-cast-authority "
        f"roleCandidateAssignmentSeedSeconds={assignment_seed_seconds:.3f} "
        f"snapshotPublishSeconds={snapshot_publish_seconds:.3f} "
        f"gateRefreshDecisionSeconds={gate_refresh_decision_seconds:.3f} "
        f"helperSeconds={time.perf_counter() - helper_started:.3f}"
    )
    return dictionary_fingerprint, 2, last_new_ordinal


def _cache_identity(project_id: str, ordinal: int) -> AuditionCacheIdentity:
    return AuditionCacheIdentity(
        project_id=project_id,
        provider_id="deterministic-pcm-wav-fixture",
        adapter_version="1.0.0",
        runtime_fingerprint=sha256_text("scale-runtime"),
        model_package_fingerprint=sha256_text("scale-model"),
        voice_profile_id="scale-voice",
        voice_runtime_binding_fingerprint=sha256_text("scale-voice-runtime-binding"),
        provider_voice_id="fixture-narrator-01",
        voice_assignment_id="scale-assignment",
        voice_assignment_revision=1,
        normalized_text_sha256=sha256_text(f"scale-text-{ordinal:05d}"),
        pronunciation_plan_fingerprint=sha256_text("scale-pronunciation-plan"),
        provider_control_fingerprint=sha256_text("scale-provider-controls"),
        output_profile_fingerprint=AUDITION_PROFILE_FINGERPRINT,
        producer_version="1.0.0",
    )


def _seed_audition_and_cache_metadata(
    database: Database,
    *,
    project_id: str,
    dictionary_fingerprint: str,
    dictionary_revision: int,
) -> dict[str, Any]:
    seed_started = time.perf_counter()
    with database.immediate_session() as session:
        template_clip = session.scalar(
            select(AuditionClipRow)
            .where(AuditionClipRow.project_id == project_id)
            .order_by(AuditionClipRow.created_at.desc(), AuditionClipRow.id.desc())
            .limit(1)
        )
        assert template_clip is not None
        template_session = session.get(AuditionSessionRow, template_clip.session_id)
        template_script = session.get(AuditionScriptRow, template_clip.script_id)
        template_request = session.get(
            SpeechProviderRequestRow,
            template_clip.provider_request_id,
        )
        template_artifact = session.get(AudioArtifactRow, template_clip.artifact_id)
        template_cache = (
            session.get(AuditionCacheRecordRow, template_clip.cache_record_id)
            if template_clip.cache_record_id is not None
            else None
        )
        template_quality = session.scalar(
            select(AudioQualityRecordRow)
            .where(AudioQualityRecordRow.clip_id == template_clip.id)
            .order_by(AudioQualityRecordRow.revision.desc(), AudioQualityRecordRow.id.desc())
            .limit(1)
        )
        template_plan = (
            session.get(TextNormalizationPlanRow, template_request.normalization_plan_id)
            if template_request is not None
            else None
        )
        assert template_session is not None
        assert template_script is not None
        assert template_request is not None
        assert template_artifact is not None
        assert template_cache is not None
        assert template_quality is not None
        assert template_plan is not None

        template_provenance = parse_json(template_request.provenance_json, None)
        template_findings = parse_json(template_quality.findings_json, None)
        assert isinstance(template_provenance, dict)
        assert isinstance(template_provenance.get("details"), dict)
        assert isinstance(template_findings, dict)
        blocking_finding_codes = template_findings.get("blockingFindingCodes")
        warning_codes = template_findings.get("warningCodes")
        assert isinstance(blocking_finding_codes, list)
        assert all(isinstance(value, str) for value in blocking_finding_codes)
        assert isinstance(warning_codes, list)
        assert all(isinstance(value, str) for value in warning_codes)

        def cache_lookup_provenance(
            provider_request_fingerprint: str,
            recorded_at: str,
        ) -> str:
            provenance = dict(template_provenance)
            details = dict(cast(dict[str, Any], provenance["details"]))
            details.update(
                {
                    "executionClassification": "verified_cache_lookup",
                    "providerDispatchCount": 0,
                    "sourceProviderRequestId": template_cache.provider_request_id,
                }
            )
            provenance.update(
                {
                    "inputFingerprint": provider_request_fingerprint,
                    "recordedAt": recorded_at,
                    "details": details,
                }
            )
            return cast(str, canonical_json(provenance))

        def quality_fingerprint(
            *,
            quality_record_id: str,
            clip_id: str,
            provider_request_id: str,
            revision: int,
        ) -> str:
            return cast(
                str,
                _audio_quality_fingerprint(
                    quality_record_id=quality_record_id,
                    project_id=project_id,
                    clip_id=clip_id,
                    artifact_id=template_artifact.id,
                    artifact_fingerprint=template_artifact.artifact_fingerprint,
                    provider_request_id=provider_request_id,
                    revision=revision,
                    policy_id=template_quality.policy_id,
                    policy_version=template_quality.policy_version,
                    policy_fingerprint=template_quality.policy_fingerprint,
                    outcome=template_quality.outcome,
                    peak_millidbfs=template_quality.peak_millidbfs,
                    rms_millidbfs=template_quality.rms_millidbfs,
                    silence_ratio_ppm=template_quality.silence_ratio_ppm,
                    clipped_sample_count=template_quality.clipped_sample_count,
                    warning_count=template_quality.warning_count,
                    blocking_finding_count=template_quality.blocking_finding_count,
                    blocking_finding_codes=cast(list[str], blocking_finding_codes),
                    warning_codes=cast(list[str], warning_codes),
                ),
            )

        current_dictionary = session.scalar(
            select(PronunciationDictionaryRow).where(
                PronunciationDictionaryRow.project_id == project_id,
                PronunciationDictionaryRow.revision == dictionary_revision,
                PronunciationDictionaryRow.dictionary_fingerprint == dictionary_fingerprint,
            )
        )
        assert current_dictionary is not None
        existing_session_count = int(
            session.scalar(
                select(func.count())
                .select_from(AuditionSessionRow)
                .where(AuditionSessionRow.project_id == project_id)
            )
            or 0
        )
        existing_clip_count = int(
            session.scalar(
                select(func.count())
                .select_from(AuditionClipRow)
                .where(AuditionClipRow.project_id == project_id)
            )
            or 0
        )
        assert existing_session_count < _AUDITION_METADATA_COUNT
        assert existing_clip_count < _AUDITION_METADATA_COUNT

        session_mappings: list[dict[str, Any]] = []
        script_mappings: list[dict[str, Any]] = []
        plan_mappings: list[dict[str, Any]] = []
        request_mappings: list[dict[str, Any]] = []
        clip_mappings: list[dict[str, Any]] = []
        quality_mappings: list[dict[str, Any]] = []
        maximum_quality_revision = int(
            session.scalar(
                select(func.max(AudioQualityRecordRow.revision)).where(
                    AudioQualityRecordRow.artifact_id == template_artifact.id
                )
            )
            or 0
        )

        paired_count = _AUDITION_METADATA_COUNT - existing_session_count
        for ordinal in range(paired_count):
            created_at = _scale_timestamp(ordinal)
            session_id = f"scale-session-{ordinal:05d}"
            script_record_id = f"scale-script-row-{ordinal:05d}"
            script_id = f"scale-script-{ordinal:05d}"
            plan_id = f"scale-plan-{ordinal:05d}"
            provider_request_id = f"scale-provider-request-{ordinal:05d}"
            clip_id = f"scale-clip-{ordinal:05d}"
            normalized_sha256 = template_request.normalized_text_sha256
            plan_fingerprint = template_request.pronunciation_plan_fingerprint
            cache_key = template_cache.cache_key
            provider_request_fingerprint = request_fingerprint(
                {"kind": "scale-provider-request", "ordinal": ordinal}
            )

            session_mapping = _row_mapping(template_session)
            session_mapping.update(
                {
                    "id": session_id,
                    "pronunciation_dictionary_record_id": current_dictionary.id,
                    "pronunciation_dictionary_revision": dictionary_revision,
                    "pronunciation_dictionary_fingerprint": dictionary_fingerprint,
                    "request_fingerprint": request_fingerprint(
                        {"kind": "scale-session", "ordinal": ordinal}
                    ),
                    "state": "reviewable",
                    "idempotency_key": f"scale-session-{ordinal:05d}",
                    "supersedes_session_id": None,
                    "created_at": created_at,
                    "published_at": created_at,
                }
            )
            session_mappings.append(session_mapping)

            script_mapping = _row_mapping(template_script)
            script_mapping.update(
                {
                    "id": script_record_id,
                    "session_id": session_id,
                    "script_id": script_id,
                    "revision": 1,
                    "exact_text_sha256": normalized_sha256,
                    "text_storage_key": None,
                    "synthetic_text_id": f"phase3b-scale-script-{ordinal:05d}",
                    "script_fingerprint": request_fingerprint(
                        {"kind": "scale-script", "ordinal": ordinal}
                    ),
                    "supersedes_script_record_id": None,
                    "created_at": created_at,
                }
            )
            script_mappings.append(script_mapping)

            plan_mapping = _row_mapping(template_plan)
            plan_mapping.update(
                {
                    "id": plan_id,
                    "session_id": session_id,
                    "script_id": script_record_id,
                    "revision": 1,
                    "original_text_sha256": normalized_sha256,
                    "normalized_text_sha256": normalized_sha256,
                    "pronunciation_dictionary_record_id": current_dictionary.id,
                    "pronunciation_dictionary_revision": dictionary_revision,
                    "pronunciation_dictionary_fingerprint": dictionary_fingerprint,
                    "pronunciation_entry_ids_json": "[]",
                    "pronunciation_plan_fingerprint": plan_fingerprint,
                    "plan_fingerprint": plan_fingerprint,
                    "created_at": created_at,
                }
            )
            plan_mappings.append(plan_mapping)

            request_mapping = _row_mapping(template_request)
            request_mapping.update(
                {
                    "id": provider_request_id,
                    "session_id": session_id,
                    "script_id": script_record_id,
                    "normalization_plan_id": plan_id,
                    "runtime_instance_id": None,
                    "provider_operation_id": f"scale-operation-{ordinal:05d}",
                    "normalized_text_sha256": normalized_sha256,
                    "pronunciation_plan_fingerprint": plan_fingerprint,
                    "cache_key": cache_key,
                    "request_fingerprint": provider_request_fingerprint,
                    "idempotency_key": f"scale-provider-request-{ordinal:05d}",
                    "outcome": "succeeded",
                    "retryable": False,
                    "provenance_json": cache_lookup_provenance(
                        provider_request_fingerprint,
                        created_at,
                    ),
                    "started_at": created_at,
                    "finished_at": created_at,
                }
            )
            request_mappings.append(request_mapping)

            clip_mapping = _row_mapping(template_clip)
            clip_mapping.update(
                {
                    "id": clip_id,
                    "session_id": session_id,
                    "script_id": script_record_id,
                    "provider_request_id": provider_request_id,
                    "cache_record_id": template_cache.id,
                    "revision": 1,
                    "request_fingerprint": provider_request_fingerprint,
                    "cache_key": cache_key,
                    "cache_hit": True,
                    "clip_fingerprint": request_fingerprint(
                        {"kind": "scale-clip", "ordinal": ordinal}
                    ),
                    "supersedes_clip_id": None,
                    "created_at": created_at,
                }
            )
            clip_mappings.append(clip_mapping)

            quality_record_id = f"scale-quality-{ordinal:05d}"
            quality_revision = maximum_quality_revision + ordinal + 1
            quality_mapping = _row_mapping(template_quality)
            quality_mapping.update(
                {
                    "id": quality_record_id,
                    "clip_id": clip_id,
                    "provider_request_id": provider_request_id,
                    "revision": quality_revision,
                    "quality_fingerprint": quality_fingerprint(
                        quality_record_id=quality_record_id,
                        clip_id=clip_id,
                        provider_request_id=provider_request_id,
                        revision=quality_revision,
                    ),
                    "created_at": created_at,
                }
            )
            quality_mappings.append(quality_mapping)

        remaining_clip_count = _AUDITION_METADATA_COUNT - existing_clip_count - paired_count
        assert remaining_clip_count >= 0
        maximum_template_clip_revision = int(
            session.scalar(
                select(func.max(AuditionClipRow.revision)).where(
                    AuditionClipRow.session_id == template_session.id,
                    AuditionClipRow.script_id == template_script.id,
                )
            )
            or 0
        )
        for offset in range(remaining_clip_count):
            ordinal = paired_count + offset
            created_at = _scale_timestamp(ordinal)
            provider_request_id = f"scale-provider-request-{ordinal:05d}"
            clip_id = f"scale-clip-{ordinal:05d}"
            request_fingerprint_value = request_fingerprint(
                {"kind": "scale-provider-request", "ordinal": ordinal}
            )
            cache_key = template_cache.cache_key
            request_mapping = _row_mapping(template_request)
            request_mapping.update(
                {
                    "id": provider_request_id,
                    "session_id": template_session.id,
                    "script_id": template_script.id,
                    "normalization_plan_id": template_plan.id,
                    "runtime_instance_id": None,
                    "provider_operation_id": f"scale-operation-{ordinal:05d}",
                    "normalized_text_sha256": template_request.normalized_text_sha256,
                    "pronunciation_plan_fingerprint": (
                        template_request.pronunciation_plan_fingerprint
                    ),
                    "cache_key": cache_key,
                    "request_fingerprint": request_fingerprint_value,
                    "idempotency_key": f"scale-provider-request-{ordinal:05d}",
                    "outcome": "succeeded",
                    "retryable": False,
                    "provenance_json": cache_lookup_provenance(
                        request_fingerprint_value,
                        created_at,
                    ),
                    "started_at": created_at,
                    "finished_at": created_at,
                }
            )
            request_mappings.append(request_mapping)
            clip_mapping = _row_mapping(template_clip)
            clip_mapping.update(
                {
                    "id": clip_id,
                    "session_id": template_session.id,
                    "script_id": template_script.id,
                    "provider_request_id": provider_request_id,
                    "cache_record_id": template_cache.id,
                    "revision": maximum_template_clip_revision + offset + 1,
                    "request_fingerprint": request_fingerprint_value,
                    "cache_key": cache_key,
                    "cache_hit": True,
                    "clip_fingerprint": request_fingerprint(
                        {"kind": "scale-clip", "ordinal": ordinal}
                    ),
                    "supersedes_clip_id": None,
                    "created_at": created_at,
                }
            )
            clip_mappings.append(clip_mapping)
            quality_record_id = f"scale-quality-{ordinal:05d}"
            quality_revision = maximum_quality_revision + ordinal + 1
            quality_mapping = _row_mapping(template_quality)
            quality_mapping.update(
                {
                    "id": quality_record_id,
                    "clip_id": clip_id,
                    "provider_request_id": provider_request_id,
                    "revision": quality_revision,
                    "quality_fingerprint": quality_fingerprint(
                        quality_record_id=quality_record_id,
                        clip_id=clip_id,
                        provider_request_id=provider_request_id,
                        revision=quality_revision,
                    ),
                    "created_at": created_at,
                }
            )
            quality_mappings.append(quality_mapping)

        session.execute(insert(AuditionSessionRow), session_mappings)
        session.execute(insert(AuditionScriptRow), script_mappings)
        session.execute(insert(TextNormalizationPlanRow), plan_mappings)
        session.execute(insert(SpeechProviderRequestRow), request_mappings)
        session.execute(insert(AuditionClipRow), clip_mappings)
        session.execute(insert(AudioQualityRecordRow), quality_mappings)

        generated_cache_keys = [
            _cache_identity(project_id, ordinal).key() for ordinal in range(_CACHE_COUNT)
        ]
        assert generated_cache_keys == [
            _cache_identity(project_id, ordinal).key() for ordinal in range(_CACHE_COUNT)
        ]
        assert len(set(generated_cache_keys)) == _CACHE_COUNT
        existing_cache_keys = set(
            session.scalars(
                select(AuditionCacheRecordRow.cache_key).where(
                    AuditionCacheRecordRow.project_id == project_id
                )
            )
        )
        cache_count = len(existing_cache_keys)
        assert cache_count < _CACHE_COUNT
        selected_cache_keys = [
            value for value in generated_cache_keys if value not in existing_cache_keys
        ][: _CACHE_COUNT - cache_count]
        assert len(selected_cache_keys) == _CACHE_COUNT - cache_count
        cache_mappings: list[dict[str, Any]] = []
        for ordinal, cache_key in enumerate(selected_cache_keys):
            created_at = _scale_timestamp(ordinal, year=2100)
            cache_mapping = _row_mapping(template_cache)
            cache_mapping.update(
                {
                    "id": f"scale-cache-{ordinal:05d}",
                    "cache_key": cache_key,
                    "verification_fingerprint": request_fingerprint(
                        {"cacheKey": cache_key, "ordinal": ordinal}
                    ),
                    "state": "verified",
                    "hit_count": 0,
                    "created_at": created_at,
                    "last_verified_at": created_at,
                    "last_hit_at": None,
                    "purged_at": None,
                }
            )
            cache_mappings.append(cache_mapping)
        session.execute(insert(AuditionCacheRecordRow), cache_mappings)

        counts = {
            "roles": int(
                session.scalar(
                    select(func.count())
                    .select_from(ProductionRoleRow)
                    .where(
                        ProductionRoleRow.project_id == project_id,
                        ProductionRoleRow.status.in_(("active", "unresolved")),
                    )
                )
                or 0
            ),
            "pronunciations": int(
                session.scalar(
                    select(func.count())
                    .select_from(PronunciationEntryRow)
                    .where(PronunciationEntryRow.project_id == project_id)
                )
                or 0
            ),
            "sessions": int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionSessionRow)
                    .where(AuditionSessionRow.project_id == project_id)
                )
                or 0
            ),
            "clips": int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionClipRow)
                    .where(AuditionClipRow.project_id == project_id)
                )
                or 0
            ),
            "cacheRecords": int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionCacheRecordRow)
                    .where(AuditionCacheRecordRow.project_id == project_id)
                )
                or 0
            ),
            "audioArtifacts": int(
                session.scalar(
                    select(func.count())
                    .select_from(AudioArtifactRow)
                    .where(AudioArtifactRow.project_id == project_id)
                )
                or 0
            ),
        }
    counts["seedSeconds"] = time.perf_counter() - seed_started
    return counts


def _assert_page_cap(
    client: TestClient,
    auth_headers: dict[str, str],
    path: str,
) -> None:
    response = client.get(path, headers=auth_headers, params={"limit": _MAX_PAGE_SIZE + 1})
    assert response.status_code == 422, response.text


def _first_two_pages(
    client: TestClient,
    auth_headers: dict[str, str],
    path: str,
    *,
    expected_total: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    first_response = client.get(
        path,
        headers=auth_headers,
        params={"limit": _READ_PAGE_SIZE},
    )
    assert first_response.status_code == 200, first_response.text
    first = cast(dict[str, Any], first_response.json())
    assert first["total"] == expected_total
    assert first["pageSize"] == _READ_PAGE_SIZE
    assert len(first["items"]) == _READ_PAGE_SIZE
    assert isinstance(first["nextCursor"], str)
    second_response = client.get(
        path,
        headers=auth_headers,
        params={"limit": _READ_PAGE_SIZE, "cursor": first["nextCursor"]},
    )
    assert second_response.status_code == 200, second_response.text
    second = cast(dict[str, Any], second_response.json())
    assert second["total"] == expected_total
    assert second["pageSize"] == _READ_PAGE_SIZE
    assert len(second["items"]) == _READ_PAGE_SIZE
    return first, second


def test_phase3b_maximum_scale_is_bounded_deterministic_and_restart_safe(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    project_id = ""
    other_project_id = ""
    cancelled_job_id = ""
    cancelled_session_id = ""
    initial_timings: dict[str, float]
    with _timed_client(settings) as (client, initial_timings):
        project_id, _run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-scale",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-scale",
        )
        other_project = client.post(
            "/api/v1/projects",
            headers={
                **auth_headers,
                "Idempotency-Key": "phase3b-scale-other-project",
            },
            json={"name": "Repository-owned scale cursor boundary"},
        )
        assert other_project.status_code == 200, other_project.text
        other_project_id = other_project.json()["project"]["projectId"]

        roles = _workspace(client, auth_headers, project_id)["roles"]["items"]
        role_id = roles[0]["roleId"]
        worker = client.app.state.worker
        worker.controls.execution_gate.clear()
        first_session, _first_script, first_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned concurrency cancellation boundary.",
            key="phase3b-scale-cancel",
        )
        second_session, _second_script, second_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned concurrency queued boundary.",
            key="phase3b-scale-queued",
        )
        try:
            first_queued = _queue_generation(
                client,
                auth_headers,
                project_id=project_id,
                session_id=first_session["auditionSessionId"],
                generation_request=first_request,
            )
            first_running = wait_for_job(
                client,
                auth_headers,
                first_queued["jobId"],
                {"running"},
                timeout=20,
            )
            assert first_running["state"] == "running"
            second_queued = _queue_generation(
                client,
                auth_headers,
                project_id=project_id,
                session_id=second_session["auditionSessionId"],
                generation_request=second_request,
            )
            queued_detail = client.get(
                f"/api/v1/jobs/{second_queued['jobId']}",
                headers=auth_headers,
            )
            assert queued_detail.status_code == 200, queued_detail.text
            assert queued_detail.json()["job"]["state"] == "queued"
            cancelled = client.post(
                f"/api/v1/jobs/{first_queued['jobId']}/cancel",
                headers=auth_headers,
            )
            assert cancelled.status_code == 200, cancelled.text
            assert (
                wait_for_job(
                    client,
                    auth_headers,
                    first_queued["jobId"],
                    {"cancelled"},
                    timeout=20,
                )["state"]
                == "cancelled"
            )
            cancelled_job_id = first_queued["jobId"]
            cancelled_session_id = first_session["auditionSessionId"]
        finally:
            worker.controls.execution_gate.set()
        assert (
            wait_for_job(
                client,
                auth_headers,
                second_queued["jobId"],
                {"succeeded", "failed"},
                timeout=30,
            )["state"]
            == "succeeded"
        )

        retry_session, _retry_script, retry_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned bounded retry boundary.",
            key="phase3b-scale-retry",
        )
        worker.fail_next_attempt()
        retry_queued, failed = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=retry_session["auditionSessionId"],
            generation_request=retry_request,
        )
        assert failed["state"] == "failed"
        assert failed["error"]["retryable"] is True
        retried = client.post(
            f"/api/v1/jobs/{retry_queued['jobId']}/retry",
            headers=auth_headers,
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["job"]["attempt"] == 2
        assert (
            wait_for_job(
                client,
                auth_headers,
                retry_queued["jobId"],
                {"succeeded", "failed"},
                timeout=30,
            )["state"]
            == "succeeded"
        )

        runtime_workspace = _workspace(client, auth_headers, project_id)
        fixture_profile = next(
            value
            for value in runtime_workspace["runtimeProfiles"]
            if "deterministic-pcm-wav-fixture" in value["providerIds"]
        )
        assert fixture_profile["maximumConcurrentRequests"] == 1
        assert fixture_profile["startupDeadlineMilliseconds"] == 10_000
        assert fixture_profile["requestDeadlineMilliseconds"] == 60_000
        active_instances = [
            value
            for value in runtime_workspace["runtimeInstances"]
            if value["state"] in {"ready", "busy", "idle"}
        ]
        assert len(active_instances) == 1
        assert active_instances[0]["state"] == "idle"

    assert initial_timings["startupSeconds"] < _MAX_BOUNDARY_SECONDS
    assert initial_timings["shutdownSeconds"] < _MAX_BOUNDARY_SECONDS

    database = Database(settings.database_path)
    try:
        with database.immediate_session() as session:
            interrupted_job = session.get(JobRow, cancelled_job_id)
            interrupted_session = session.get(AuditionSessionRow, cancelled_session_id)
            interrupted_request = session.scalar(
                select(SpeechProviderRequestRow).where(
                    SpeechProviderRequestRow.job_id == cancelled_job_id
                )
            )
            assert interrupted_job is not None
            assert interrupted_session is not None
            assert interrupted_request is not None
            interrupted_job.state = "running"
            interrupted_job.stage = "synthesize"
            interrupted_job.progress = 500_000
            interrupted_job.cancellation_requested = False
            interrupted_job.error_code = None
            interrupted_job.error_message = None
            interrupted_job.error_retryable = None
            interrupted_job.terminal_at = None
            interrupted_job.updated_at = utc_now()
            interrupted_session.state = "generating"
            interrupted_request.outcome = "running"
            interrupted_request.retryable = False
            interrupted_request.finished_at = None
            attempt = session.get(
                JobAttemptRow,
                {
                    "job_id": interrupted_job.id,
                    "number": interrupted_job.current_attempt,
                },
            )
            assert attempt is not None
            attempt.ended_at = None
            attempt.outcome = None
    finally:
        database.close()

    scale_timings: dict[str, float]
    scale_counts: dict[str, Any]
    dictionary_fingerprint = ""
    dictionary_revision = 0
    last_scale_role_ordinal = 0
    session_limit_evidence: dict[str, Any] = {}
    with _timed_client(settings) as (client, scale_timings):
        recovered = client.get(
            f"/api/v1/jobs/{cancelled_job_id}",
            headers=auth_headers,
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["job"]["state"] == "interrupted"
        resumed = client.post(
            f"/api/v1/jobs/{cancelled_job_id}/resume",
            headers=auth_headers,
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["job"]["attempt"] == 2
        assert (
            wait_for_job(
                client,
                auth_headers,
                cancelled_job_id,
                {"succeeded", "failed"},
                timeout=30,
            )["state"]
            == "succeeded"
        )

        database = cast(Database, client.app.state.database)
        (
            dictionary_fingerprint,
            dictionary_revision,
            last_scale_role_ordinal,
        ) = _seed_roles_and_pronunciations(
            database,
            cast(CastingRepository, client.app.state.casting),
            project_id=project_id,
        )
        scaled_workspace = _workspace(
            client,
            auth_headers,
            project_id,
            role_limit=_MAX_PAGE_SIZE,
        )
        first_role_page = scaled_workspace["roles"]
        assert first_role_page["pageSize"] == _MAX_PAGE_SIZE
        assert first_role_page["total"] == _ROLE_COUNT
        assert isinstance(first_role_page["nextCursor"], str)
        assert len(first_role_page["nextCursor"]) <= 512
        second_role_workspace = _workspace(
            client,
            auth_headers,
            project_id,
            role_cursor=first_role_page["nextCursor"],
            role_limit=_MAX_PAGE_SIZE,
        )
        second_role_page = second_role_workspace["roles"]
        assert second_role_page["pageSize"] == _ROLE_COUNT - _MAX_PAGE_SIZE
        assert second_role_page["total"] == _ROLE_COUNT
        assert "nextCursor" not in second_role_page
        scaled_roles = [*first_role_page["items"], *second_role_page["items"]]
        assert len(scaled_roles) == _ROLE_COUNT
        assert [value["displayLabel"] for value in scaled_roles[-3:]] == [
            f"Scale role {ordinal:04d}"
            for ordinal in range(last_scale_role_ordinal - 2, last_scale_role_ordinal + 1)
        ]
        assert scaled_workspace["currentDictionary"]["currentEntryCount"] == (_PRONUNCIATION_COUNT)
        session_limit_evidence = cast(
            dict[str, Any],
            scaled_roles[0]["sessionEvidence"],
        )
        assert session_limit_evidence is not None

        scale_counts = _seed_audition_and_cache_metadata(
            database,
            project_id=project_id,
            dictionary_fingerprint=dictionary_fingerprint,
            dictionary_revision=dictionary_revision,
        )
        assert scale_counts == {
            **{
                "roles": _ROLE_COUNT,
                "pronunciations": _PRONUNCIATION_COUNT,
                "sessions": _AUDITION_METADATA_COUNT,
                "clips": _AUDITION_METADATA_COUNT,
                "cacheRecords": _CACHE_COUNT,
                "audioArtifacts": 3,
            },
            "seedSeconds": scale_counts["seedSeconds"],
        }
        assert scale_counts["seedSeconds"] < _MAX_BOUNDARY_SECONDS
        assert scale_counts["audioArtifacts"] < 10

        wrong_role_cursor = client.get(
            f"/api/v1/projects/{other_project_id}/auditions/workspace",
            headers=auth_headers,
            params={
                "roleCursor": first_role_page["nextCursor"],
                "roleLimit": _MAX_PAGE_SIZE,
            },
        )
        assert wrong_role_cursor.status_code == 400, wrong_role_cursor.text
        assert wrong_role_cursor.json()["error"]["code"] == "INVALID_CURSOR"
        stale_role_cursor = client.get(
            f"/api/v1/projects/{project_id}/auditions/workspace",
            headers=auth_headers,
            params={
                "roleCursor": first_role_page["nextCursor"],
                "roleLimit": _MAX_PAGE_SIZE,
            },
        )
        assert stale_role_cursor.status_code == 400, stale_role_cursor.text
        assert stale_role_cursor.json()["error"]["code"] == "INVALID_CURSOR"

        pronunciation_path = f"/api/v1/projects/{project_id}/pronunciations/entries"
        session_path = f"/api/v1/projects/{project_id}/audition-sessions"
        clip_path = f"/api/v1/projects/{project_id}/audition-clips"
        model_path = f"/api/v1/projects/{project_id}/speech/model-packages"
        for path in (pronunciation_path, session_path, clip_path, model_path):
            _assert_page_cap(client, auth_headers, path)

        entry_first, entry_second = _first_two_pages(
            client,
            auth_headers,
            pronunciation_path,
            expected_total=_PRONUNCIATION_COUNT,
        )
        entry_ids = [value["entryId"] for value in [*entry_first["items"], *entry_second["items"]]]
        assert len(set(entry_ids)) == _READ_PAGE_SIZE * 2
        assert entry_ids == sorted(entry_ids, reverse=True)

        session_first, session_second = _first_two_pages(
            client,
            auth_headers,
            session_path,
            expected_total=_AUDITION_METADATA_COUNT,
        )
        session_ids = [
            value["auditionSessionId"]
            for value in [*session_first["items"], *session_second["items"]]
        ]
        assert len(set(session_ids)) == _READ_PAGE_SIZE * 2
        assert session_ids == sorted(session_ids, reverse=True)

        clip_first, clip_second = _first_two_pages(
            client,
            auth_headers,
            clip_path,
            expected_total=_AUDITION_METADATA_COUNT,
        )
        clip_ids = [
            value["auditionClipId"] for value in [*clip_first["items"], *clip_second["items"]]
        ]
        assert len(set(clip_ids)) == _READ_PAGE_SIZE * 2
        assert clip_ids == sorted(clip_ids, reverse=True)

        repeated_clip_page = client.get(
            clip_path,
            headers=auth_headers,
            params={"limit": _READ_PAGE_SIZE},
        )
        assert repeated_clip_page.status_code == 200, repeated_clip_page.text
        assert [value["auditionClipId"] for value in repeated_clip_page.json()["items"]] == [
            value["auditionClipId"] for value in clip_first["items"]
        ]

        wrong_entry_cursor = client.get(
            f"/api/v1/projects/{other_project_id}/pronunciations/entries",
            headers=auth_headers,
            params={"limit": 1, "cursor": entry_first["nextCursor"]},
        )
        assert wrong_entry_cursor.status_code == 400, wrong_entry_cursor.text
        assert wrong_entry_cursor.json()["error"]["code"] == "INVALID_CURSOR"
        wrong_session_cursor = client.get(
            session_path,
            headers=auth_headers,
            params={
                "limit": 1,
                "cursor": session_first["nextCursor"],
                "roleId": scaled_roles[0]["roleId"],
            },
        )
        assert wrong_session_cursor.status_code == 400, wrong_session_cursor.text
        assert wrong_session_cursor.json()["error"]["code"] == "INVALID_CURSOR"
        wrong_clip_cursor = client.get(
            clip_path,
            headers=auth_headers,
            params={
                "limit": 1,
                "cursor": clip_first["nextCursor"],
                "auditionSessionId": session_first["items"][0]["auditionSessionId"],
            },
        )
        assert wrong_clip_cursor.status_code == 400, wrong_clip_cursor.text
        assert wrong_clip_cursor.json()["error"]["code"] == "INVALID_CURSOR"

        model_first = client.get(model_path, headers=auth_headers, params={"limit": 1})
        assert model_first.status_code == 200, model_first.text
        assert model_first.json()["total"] == 2
        assert isinstance(model_first.json()["nextCursor"], str)
        wrong_model_cursor = client.get(
            f"/api/v1/projects/{other_project_id}/speech/model-packages",
            headers=auth_headers,
            params={"limit": 1, "cursor": model_first.json()["nextCursor"]},
        )
        assert wrong_model_cursor.status_code == 400, wrong_model_cursor.text
        assert wrong_model_cursor.json()["error"]["code"] == "INVALID_CURSOR"

        dictionary = scaled_workspace["currentDictionary"]
        pronunciation_limit = client.post(
            pronunciation_path,
            headers=auth_headers,
            json={
                "expectedDictionaryRevision": dictionary["revision"],
                "expectedDictionaryFingerprint": dictionary["dictionaryFingerprint"],
                "writtenForm": "BeyondLimit",
                "language": "en",
                "locale": "en-US",
                "scope": "project",
                "scopeId": None,
                "representation": "provider_neutral",
                "pronunciation": "beyond-limit",
                "ipa": None,
                "providerId": None,
                "providerCompiledValue": None,
                "caseSensitive": False,
                "matchRule": "whole_word",
                "priority": 0,
                "reason": "Prove the bounded pronunciation limit.",
                "supersedesEntryId": None,
                "idempotencyKey": "phase3b-scale-pronunciation-overflow",
            },
        )
        assert pronunciation_limit.status_code == 409, pronunciation_limit.text
        assert pronunciation_limit.json()["error"]["code"] == ("PRONUNCIATION_ENTRY_LIMIT_EXCEEDED")
        session_limit = client.post(
            session_path,
            headers=auth_headers,
            json={
                "roleId": scaled_roles[0]["roleId"],
                "evidence": session_limit_evidence,
                "idempotencyKey": "phase3b-scale-session-overflow",
            },
        )
        assert session_limit.status_code == 409, session_limit.text
        assert session_limit.json()["error"]["code"] == "AUDITION_SESSION_LIMIT_EXCEEDED"

    assert scale_timings["startupSeconds"] < _MAX_BOUNDARY_SECONDS
    assert scale_timings["shutdownSeconds"] < _MAX_BOUNDARY_SECONDS

    final_timings: dict[str, float]
    with _timed_client(settings) as (restarted, final_timings):
        restored_entries = restarted.get(
            f"/api/v1/projects/{project_id}/pronunciations/entries",
            headers=auth_headers,
            params={"limit": 1},
        )
        restored_sessions = restarted.get(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            params={"limit": 1},
        )
        restored_clips = restarted.get(
            f"/api/v1/projects/{project_id}/audition-clips",
            headers=auth_headers,
            params={"limit": 1},
        )
        assert restored_entries.status_code == 200, restored_entries.text
        assert restored_sessions.status_code == 200, restored_sessions.text
        assert restored_clips.status_code == 200, restored_clips.text
        assert restored_entries.json()["total"] == _PRONUNCIATION_COUNT
        assert restored_sessions.json()["total"] == _AUDITION_METADATA_COUNT
        assert restored_clips.json()["total"] == _AUDITION_METADATA_COUNT
        database = cast(Database, restarted.app.state.database)
        with database.session() as session:
            assert (
                int(
                    session.scalar(
                        select(func.count())
                        .select_from(AuditionCacheRecordRow)
                        .where(AuditionCacheRecordRow.project_id == project_id)
                    )
                    or 0
                )
                == _CACHE_COUNT
            )
            assert (
                int(
                    session.scalar(
                        select(func.count())
                        .select_from(ProductionRoleRow)
                        .where(
                            ProductionRoleRow.project_id == project_id,
                            ProductionRoleRow.status.in_(("active", "unresolved")),
                        )
                    )
                    or 0
                )
                == _ROLE_COUNT
            )
            profiles = list(session.scalars(select(SpeechRuntimeProfileRow)))
            assert profiles
            assert all(value.maximum_concurrency == 1 for value in profiles)
            manifests = list(session.scalars(select(ModelPackageManifestRow)))
            assert len(manifests) == 2

    assert final_timings["startupSeconds"] < _MAX_BOUNDARY_SECONDS
    assert final_timings["shutdownSeconds"] < _MAX_BOUNDARY_SECONDS
    database = Database(settings.database_path)
    try:
        with database.session() as session:
            runtime_instances = list(session.scalars(select(SpeechRuntimeInstanceRow)))
            assert runtime_instances
            assert all(value.state == "stopped" for value in runtime_instances)
            assert all(value.stopped_at is not None for value in runtime_instances)
    finally:
        database.close()

    print(
        "phase3b-scale "
        f"roles={_ROLE_COUNT} pronunciations={_PRONUNCIATION_COUNT} "
        f"sessions={_AUDITION_METADATA_COUNT} clips={_AUDITION_METADATA_COUNT} "
        f"cacheRecords={_CACHE_COUNT} audioArtifacts={scale_counts['audioArtifacts']} "
        f"seedSeconds={scale_counts['seedSeconds']:.3f} "
        f"initialStartupSeconds={initial_timings['startupSeconds']:.3f} "
        f"initialShutdownSeconds={initial_timings['shutdownSeconds']:.3f} "
        f"scaleRestartSeconds={scale_timings['startupSeconds']:.3f} "
        f"scaleShutdownSeconds={scale_timings['shutdownSeconds']:.3f} "
        f"finalRestartSeconds={final_timings['startupSeconds']:.3f} "
        f"finalShutdownSeconds={final_timings['shutdownSeconds']:.3f}"
    )
