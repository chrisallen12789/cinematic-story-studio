from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.audition_repository import (
    _GOVERNED_PRIVATE_AUDITION_WARNING,
    _GOVERNED_PRIVATE_AUDITION_WARNING_FINGERPRINT,
    _GOVERNED_VOICE_INVENTORY_FINGERPRINT,
    _GOVERNED_VOICE_INVENTORY_RECORD_FINGERPRINT,
    AuditionRepository,
    _governed_voice_inventory,
)
from cinematic_story_service.casting import (
    CASTING_GATE_IDS,
    CASTING_PROFILE_FINGERPRINT,
    GOVERNED_KOKORO_RIGHTS_RECORD_FINGERPRINT,
    GOVERNED_KOKORO_VOICE_PROFILE_FINGERPRINT,
    GOVERNED_KOKORO_VOICE_PROFILE_ID,
    GOVERNED_VOICE_CATALOG_FINGERPRINT,
    GOVERNED_VOICE_CATALOG_REVISION_ID,
    LEGACY_CASTING_PROFILE_FINGERPRINT,
    LEGACY_CASTING_PROFILE_ID,
    load_governed_voice_catalog,
    load_synthetic_catalog,
)
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.local_speech import (
    SpeechInvocationContext,
    SpeechSynthesisRequest,
)
from cinematic_story_service.model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    ModelPackageManager,
)
from cinematic_story_service.models import (
    ApprovedCastSnapshotRow,
    AudioArtifactRow,
    AuditionClipRow,
    AuditionReviewDecisionRow,
    AuditionSessionRow,
    CastAssignmentRow,
    CastingRunRow,
    ModelPackageManifestRow,
    SpeechProviderRequestRow,
    SpeechRuntimeInstanceRow,
    VoiceCatalogRevisionRow,
    VoiceRightsRecordRow,
)
from cinematic_story_service.schemas import (
    InstallModelPackageRequest,
    ModelInstallationOperationRequest,
)
from cinematic_story_service.speech_runtime import ManagedSpeechRuntime, SpeechRuntimeConfig
from cinematic_story_service.util import (
    canonical_json,
    parse_json,
    request_fingerprint,
    sha256_text,
)
from tests.conftest import wait_for_job
from tests.test_phase3a_casting import _candidates, _evidence, _roles
from tests.test_phase3a_governance import (
    _apply_correction,
    _decision_payload,
    _post_decision,
    _prepare_casting,
    _reviews,
)
from tests.test_phase3b_real_provider import (
    _CapturingRuntime,
    _VerifiedModelPackageManager,
)
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _approve_audition_review,
    _clips,
    _generate,
    _workspace,
)

_REAL_PROFILE_ID = GOVERNED_KOKORO_VOICE_PROFILE_ID
_SYNTHETIC_TEXT = "A lantern glows beside the quiet harbor."


def _write_absent_package_evidence_if_requested(payload: dict[str, Any]) -> None:
    configured_path = os.environ.get("CSS_PHASE3B1_ABSENT_PACKAGE_EVIDENCE_PATH")
    if not configured_path:
        return
    evidence_path = Path(configured_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = evidence_path.with_name(f".{evidence_path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(evidence_path)


def _prepare_real_narrator_and_character_cast(
    client: TestClient,
    auth_headers: dict[str, str],
) -> tuple[str, dict[str, Any], tuple[str, str]]:
    project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="phase3b1-governed-real-cast",
    )
    initial_roles = _roles(client, auth_headers, run=run)
    narrator = next(value for value in initial_roles if value["roleType"] == "primary_narrator")
    character = next(value for value in initial_roles if value["roleType"] == "named_character")
    real_role_ids = (narrator["roleId"], character["roleId"])
    character_voice_ids = iter(
        (
            "synthetic-character-01",
            "synthetic-character-02",
            "synthetic-character-03",
            "synthetic-character-04",
        )
    )

    for index, original_role in enumerate(initial_roles):
        role = next(
            value
            for value in _roles(client, auth_headers, run=run)
            if value["roleId"] == original_role["roleId"]
        )
        if role["performanceRequirements"]["language"] == "und" and role["roleType"] in {
            "primary_narrator",
            "named_character",
        }:
            run, _changed, _payload = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=role["roleId"],
                operation="change_casting_requirement",
                key=f"phase3b1-language-{index}",
                corrected_value={
                    "requirement": {
                        **role["performanceRequirements"],
                        "language": "en",
                        "locales": ["en-US"],
                    }
                },
            )
            role = next(
                value
                for value in _roles(client, auth_headers, run=run)
                if value["roleId"] == original_role["roleId"]
            )

        if role["roleId"] in real_role_ids:
            candidates = _candidates(client, auth_headers, run=run, role=role)
            real_candidate = next(
                value for value in candidates if value["voiceProfileId"] == _REAL_PROFILE_ID
            )
            assert real_candidate["rank"] <= 12
            assessment = real_candidate["assessment"]
            assert assessment["compatibilityStatus"] == "compatible_with_warnings"
            assert assessment["rightsEligibility"] == "restricted_requires_acknowledgement"
            assert assessment["longFormSuitability"] == "unknown"
            hard_constraints = {
                value["constraintId"]: value for value in assessment["hardConstraints"]
            }
            assert hard_constraints["rights_not_prohibited"]["result"] == "pass"
            assert (
                "exact fingerprint-bound"
                in hard_constraints["rights_not_prohibited"]["explanation"].casefold()
            )
            for constraint_id in (
                "rights_known",
                "required_consent",
                "declared_capabilities",
                "role_length_suitability",
            ):
                assert hard_constraints[constraint_id]["result"] == "unknown"
                assert (
                    "exact fingerprint-bound"
                    in hard_constraints[constraint_id]["explanation"].casefold()
                )
            public_explanations = " ".join(
                value["explanation"] for value in assessment["hardConstraints"]
            ).casefold()
            assert "rights and commercial-use states are declared" not in public_explanations
            assert "consent state meets declared requirements" not in public_explanations
            assert "capabilities and commercial scope are compatible" not in public_explanations
            assert "long-form suitability matches the workload" not in public_explanations
            soft_preferences = {
                value["preferenceId"]: value for value in assessment["softPreferences"]
            }
            for preference_id in (
                "narration_suitability",
                "dialogue_suitability",
                "long_form_preference",
            ):
                assert soft_preferences[preference_id]["score"] == 0
                assert "unknown" in soft_preferences[preference_id]["explanation"].casefold()
            run, selected, _payload = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=role["roleId"],
                operation="select_voice",
                key=f"phase3b1-select-real-{index}",
                voice_profile_id=_REAL_PROFILE_ID,
            )
            assignment = selected["assignment"]
            run, locked, _payload = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=role["roleId"],
                operation="lock_assignment",
                key=f"phase3b1-lock-real-{index}",
                corrected_value={"assignmentId": assignment["assignmentId"]},
            )
            assignment = locked["assignment"]
            run, acknowledgement, _payload = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=role["roleId"],
                operation="acknowledge_restricted_rights",
                key=f"phase3b1-ack-real-{index}",
                voice_profile_id=_REAL_PROFILE_ID,
                corrected_value={
                    "rightsRecordId": assignment["rightsRecordId"],
                    "rightsRecordRevision": assignment["rightsRecordRevision"],
                },
            )
            assert acknowledgement["correction"]["category"] == ("acknowledge_restricted_rights")
        elif role["roleType"] == "named_character":
            voice_profile_id = next(character_voice_ids)
            run, _selected, _payload = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=role["roleId"],
                operation="select_voice",
                key=f"phase3b1-select-fixture-{index}",
                voice_profile_id=voice_profile_id,
            )
        elif role["roleType"] == "primary_narrator":
            raise AssertionError("The primary narrator was not selected as a real role.")
        else:
            run, _uncast, _payload = _apply_correction(
                client,
                auth_headers,
                run=run,
                role_id=role["roleId"],
                operation="mark_intentionally_uncast",
                key=f"phase3b1-uncast-{index}",
            )

    reusable_categories = {
        "incompatible_voice_reuse",
        "narrator_major_character_reuse",
        "metadata_similarity_risk",
        "voice_reuse_threshold_exceeded",
    }
    conflict_index = 0
    while True:
        response = client.get(
            f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/conflicts",
            headers=auth_headers,
            params={**_evidence(run), "limit": 200},
        )
        assert response.status_code == 200, response.text
        conflict = next(
            (
                value
                for value in response.json()["items"]
                if value["resolutionState"] == "open" and value["category"] in reusable_categories
            ),
            None,
        )
        if conflict is None:
            break
        run, _resolved, _payload = _apply_correction(
            client,
            auth_headers,
            run=run,
            role_id=conflict["roleIds"][0],
            operation="approve_voice_reuse",
            key=f"phase3b1-reuse-{conflict_index}",
            corrected_value={
                "conflictId": conflict["conflictId"],
                "approvedRoleIds": conflict["roleIds"],
            },
        )
        conflict_index += 1

    assert all(value["state"] != "approved" for value in _reviews(client, auth_headers, run=run))
    preapproval_workspace = _workspace(client, auth_headers, project_id)
    assert preapproval_workspace["approvedCastSnapshot"] is None
    assert preapproval_workspace["voiceReadinessSnapshot"] is None
    prerequisite_by_id = {
        value["prerequisiteId"]: value for value in preapproval_workspace["prerequisites"]
    }
    for prerequisite_id in (
        "phase3a_narrator_casting_review",
        "phase3a_character_casting_review",
        "phase3a_complete_cast_review",
        "approved_cast_snapshot",
    ):
        assert prerequisite_by_id[prerequisite_id]["current"] is False
    blocked_real_roles = [
        value
        for value in preapproval_workspace["roles"]["items"]
        if value["roleId"] in real_role_ids
    ]
    assert len(blocked_real_roles) == 2
    for blocked_role in blocked_real_roles:
        assert blocked_role["voiceProfileId"] == _REAL_PROFILE_ID
        assert blocked_role["sessionEvidence"] is None
        assert blocked_role["generationRequest"] is None

    test_app = cast(FastAPI, client.app)
    auditions = cast(AuditionRepository, test_app.state.auditions)
    with auditions.database.session() as database_session:
        assert database_session.scalar(select(func.count()).select_from(AuditionSessionRow)) == 0
        assert (
            database_session.scalar(select(func.count()).select_from(SpeechProviderRequestRow)) == 0
        )
        assert (
            database_session.scalar(select(func.count()).select_from(SpeechRuntimeInstanceRow)) == 0
        )
        assert database_session.scalar(select(func.count()).select_from(AudioArtifactRow)) == 0
        assert database_session.scalar(select(func.count()).select_from(AuditionClipRow)) == 0

    for gate_id in CASTING_GATE_IDS:
        review = next(
            value for value in _reviews(client, auth_headers, run=run) if value["gateId"] == gate_id
        )
        decided = _post_decision(
            client,
            auth_headers,
            run=run,
            gate_id=gate_id,
            payload=_decision_payload(
                run=run,
                review=review,
                key=f"phase3b1-approve-{gate_id}",
            ),
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["decision"]["decision"] == "approved"
    return project_id, run, real_role_ids


def _activate_real_provider(
    app: FastAPI,
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_id: str,
) -> list[tuple[SpeechSynthesisRequest, SpeechInvocationContext]]:
    auditions = cast(AuditionRepository, app.state.auditions)
    package_path = (
        settings.data_dir
        / "models"
        / "packages"
        / KOKORO_LOCAL_ONNX_MANIFEST.package_id
        / KOKORO_LOCAL_ONNX_MANIFEST.package_version
    )
    package_path.mkdir(parents=True)
    manager = _VerifiedModelPackageManager(package_path)
    monkeypatch.setattr(
        auditions,
        "_model_package_manager",
        cast(ModelPackageManager, manager),
    )
    calls: list[tuple[SpeechSynthesisRequest, SpeechInvocationContext]] = []
    fixture_runtime_factory = auditions._runtime_factory

    def runtime_factory(config: SpeechRuntimeConfig) -> ManagedSpeechRuntime:
        if config.provider_id != "kokoro-local-onnx":
            return fixture_runtime_factory(config)
        return cast(ManagedSpeechRuntime, _CapturingRuntime(config, calls))

    monkeypatch.setattr(auditions, "_runtime_factory", runtime_factory)
    packages, _cursor, _total = auditions.list_model_packages(
        project_id=project_id,
        cursor=None,
        limit=200,
    )
    manifest = next(
        item["manifest"]
        for item in packages
        if item["manifest"]["providerId"] == "kokoro-local-onnx"
    )
    archive_path = settings.data_dir / "model-staging" / "phase3b1-focused.zip"
    archive_path.write_bytes(b"focused private model archive fixture")
    installed = auditions.install_model_package(
        project_id=project_id,
        model_package_id=manifest["modelPackageId"],
        request=InstallModelPackageRequest(
            expected_manifest_fingerprint=manifest["manifestFingerprint"],
            expected_installation_revision=None,
            acknowledge_restricted_local_use=True,
            reason="Install the exact restricted package for focused contract testing.",
            idempotency_key="phase3b1-install-kokoro",
        ),
        archive_path=archive_path,
        actor_id="local_user",
    )
    activated = auditions.perform_model_package_action(
        project_id=project_id,
        request=ModelInstallationOperationRequest(
            model_package_id=manifest["modelPackageId"],
            expected_manifest_fingerprint=manifest["manifestFingerprint"],
            expected_installation_revision=installed["installation"]["installationRevision"],
            action="activate",
            reason="Activate the exact verified restricted package.",
            idempotency_key="phase3b1-activate-kokoro",
        ),
        actor_id="local_user",
    )
    assert activated["installation"]["status"] == "active"
    return calls


def _create_and_generate_real_clip(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_id: str,
    key: str,
    text: str,
    test_activation_failures: bool,
    generation_errors: list[BaseException],
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = _workspace(client, auth_headers, project_id)
    role = next(value for value in workspace["roles"]["items"] if value["roleId"] == role_id)
    inventory = workspace["voiceInventory"]
    evidence = role["sessionEvidence"]
    assert evidence is not None
    if test_activation_failures:
        missing = client.post(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            json={
                "roleId": role_id,
                "evidence": evidence,
                "idempotencyKey": f"{key}-missing-activation",
            },
        )
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "AUDITION_RESTRICTED_ACTIVATION_REQUIRED"
        stale = client.post(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            json={
                "roleId": role_id,
                "evidence": evidence,
                "restrictedLocalAuditionActivation": {
                    "expectedInventoryFingerprint": "0" * 64,
                    "expectedWarningFingerprint": inventory["warningFingerprint"],
                    "reason": "Reject stale inventory evidence.",
                },
                "idempotencyKey": f"{key}-stale-activation",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "AUDITION_RESTRICTED_ACTIVATION_STALE"

    created = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": role_id,
            "evidence": evidence,
            "restrictedLocalAuditionActivation": {
                "expectedInventoryFingerprint": inventory["inventoryFingerprint"],
                "expectedWarningFingerprint": inventory["warningFingerprint"],
                "reason": "Authorize one bounded synthetic-text private local audition.",
            },
            "idempotencyKey": f"{key}-session",
        },
    )
    assert created.status_code == 200, created.text
    audition_session = created.json()["session"]
    activation = audition_session["governedLocalVoiceActivation"]
    assert activation["privateLocalAuditionOnly"] is True
    assert activation["productionExportEligible"] is False
    acknowledgement = activation["acknowledgement"]
    assert acknowledgement["actor"] == {"classification": "human", "actorId": "local_user"}
    assert acknowledgement["warningFingerprint"] == inventory["warningFingerprint"]
    assert acknowledgement["inventoryRecordId"] == inventory["items"][0]["inventoryRecordId"]
    assert acknowledgement["inventoryFingerprint"] == inventory["inventoryFingerprint"]
    assert acknowledgement["inventoryFingerprint"] != inventory["items"][0]["inventoryFingerprint"]
    assert acknowledgement["modelPackageFingerprint"] == KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
    assert acknowledgement["voiceTensorSha256"] == (
        "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b"
    )
    assert acknowledgement["productionExportAuthorized"] is False
    assert acknowledgement["commercialDistributionAuthorized"] is False
    assert acknowledgement["cloningAuthorized"] is False

    text_sha256 = sha256_text(text)
    preview = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{audition_session['auditionSessionId']}/normalization-preview"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": audition_session["auditionSessionId"],
            "expectedSessionRevision": audition_session["revision"],
            "text": text,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
        },
    )
    assert preview.status_code == 200, preview.text
    script = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{audition_session['auditionSessionId']}/scripts"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": audition_session["auditionSessionId"],
            "expectedSessionRevision": audition_session["revision"],
            "kind": "standardized_synthetic",
            "text": text,
            "sourceDocumentId": None,
            "sourceRevision": None,
            "sourceSpan": None,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
            "idempotencyKey": f"{key}-script",
        },
    )
    assert script.status_code == 200, script.text
    refreshed = _workspace(client, auth_headers, project_id)
    refreshed_role = next(
        value for value in refreshed["roles"]["items"] if value["roleId"] == role_id
    )
    generation_request = refreshed_role["generationRequest"]
    assert generation_request["evidence"]["governedLocalVoiceActivation"] == activation
    _queued, terminal = _generate(
        client,
        auth_headers,
        project_id=project_id,
        session_id=audition_session["auditionSessionId"],
        generation_request=generation_request,
    )
    if terminal["state"] != "succeeded" and generation_errors:
        raise generation_errors[-1]
    assert terminal["state"] == "succeeded", json.dumps(terminal, indent=2, sort_keys=True)
    clip = next(
        value
        for value in _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )
        if value["auditionSessionId"] == audition_session["auditionSessionId"]
    )
    assert clip["providerClass"] == "real_local"
    assert clip["productionExportEligible"] is False
    assert clip["governedLocalVoiceActivation"] == activation
    assert clip["audioArtifact"]["sampleRateHz"] == 24_000
    assert clip["audioArtifact"]["channels"] == 1
    assert clip["audioArtifact"]["sampleWidthBytes"] == 2
    return audition_session, clip


def _create_and_generate_fixture_clip(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_id: str,
    key: str,
    text: str,
    generation_errors: list[BaseException],
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = _workspace(client, auth_headers, project_id)
    role = next(value for value in workspace["roles"]["items"] if value["roleId"] == role_id)
    assert role["governedLocalVoice"] is None
    assert role["sessionEvidence"]["providerId"] == "deterministic-pcm-wav-fixture"
    created = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": role_id,
            "evidence": role["sessionEvidence"],
            "idempotencyKey": f"{key}-session",
        },
    )
    assert created.status_code == 200, created.text
    audition_session = created.json()["session"]
    assert audition_session["governedLocalVoiceActivation"] is None
    text_sha256 = sha256_text(text)
    preview = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{audition_session['auditionSessionId']}/normalization-preview"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": audition_session["auditionSessionId"],
            "expectedSessionRevision": audition_session["revision"],
            "text": text,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
        },
    )
    assert preview.status_code == 200, preview.text
    script = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{audition_session['auditionSessionId']}/scripts"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": audition_session["auditionSessionId"],
            "expectedSessionRevision": audition_session["revision"],
            "kind": "standardized_synthetic",
            "text": text,
            "sourceDocumentId": None,
            "sourceRevision": None,
            "sourceSpan": None,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
            "idempotencyKey": f"{key}-script",
        },
    )
    assert script.status_code == 200, script.text
    refreshed = _workspace(client, auth_headers, project_id)
    refreshed_role = next(
        value for value in refreshed["roles"]["items"] if value["roleId"] == role_id
    )
    generation_request = refreshed_role["generationRequest"]
    assert generation_request["evidence"]["governedLocalVoiceActivation"] is None
    _queued, terminal = _generate(
        client,
        auth_headers,
        project_id=project_id,
        session_id=audition_session["auditionSessionId"],
        generation_request=generation_request,
    )
    if terminal["state"] != "succeeded" and generation_errors:
        raise generation_errors[-1]
    assert terminal["state"] == "succeeded", json.dumps(terminal, indent=2, sort_keys=True)
    clip = next(
        value
        for value in _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )
        if value["auditionSessionId"] == audition_session["auditionSessionId"]
    )
    assert clip["providerClass"] == "deterministic_fixture"
    assert clip["governedLocalVoiceActivation"] is None
    return audition_session, clip


def _assert_bound_dependency_drift(
    auditions: AuditionRepository,
    *,
    audition_session_id: str,
) -> None:
    cases: tuple[tuple[type[Any], str, str, Any, tuple[str, ...]], ...] = (
        (
            CastAssignmentRow,
            "assignment_id",
            "revision",
            99,
            ("CASTING_SNAPSHOT_MANIFEST_INVALID", "APPROVED_CAST_EVIDENCE_CHANGED"),
        ),
        (
            VoiceRightsRecordRow,
            "rights_record_id",
            "rights_fingerprint",
            "0" * 64,
            ("CASTING_SNAPSHOT_MANIFEST_INVALID", "VOICE_RIGHTS_CHANGED"),
        ),
        (
            VoiceCatalogRevisionRow,
            "catalog_revision_id",
            "catalog_fingerprint",
            "0" * 64,
            (
                "CASTING_SNAPSHOT_MANIFEST_INVALID",
                "GOVERNED_LOCAL_VOICE_ACTIVATION_CHANGED",
                "VOICE_RIGHTS_CHANGED",
            ),
        ),
        (
            ModelPackageManifestRow,
            "model_manifest_id",
            "manifest_fingerprint",
            "0" * 64,
            ("MODEL_PACKAGE_CHANGED",),
        ),
    )
    for model, dependency_id_attribute, field, stale_value, expected_codes in cases:
        with auditions.database.sessions() as database_session:
            audition_session = database_session.get(AuditionSessionRow, audition_session_id)
            assert audition_session is not None
            dependency = database_session.get(
                model,
                getattr(audition_session, dependency_id_attribute),
            )
            assert dependency is not None
            setattr(dependency, field, stale_value)
            try:
                drift = auditions._session_dependency_drift(database_session, audition_session)
            except ServiceError as exc:
                observed_code = exc.code
            else:
                assert drift is not None
                observed_code = drift[4]
            assert observed_code in expected_codes
            database_session.rollback()


def test_exact_governed_inventory_is_neutral_restricted_and_immutable() -> None:
    legacy = load_synthetic_catalog()
    governed = load_governed_voice_catalog()
    inventory = _governed_voice_inventory()

    assert legacy.fingerprint == (
        "68d116d1f66e4ea4bcceabfd0520fd889cf9da3074ee1b9186c43c285575c25f"
    )
    assert len(legacy.voices) == 14
    assert governed.revision_id == GOVERNED_VOICE_CATALOG_REVISION_ID
    assert governed.fingerprint == GOVERNED_VOICE_CATALOG_FINGERPRINT
    assert len(governed.voices) == len(governed.rights) == 15
    assert CASTING_PROFILE_FINGERPRINT == (
        "5377949573018b5d3a4f4cd343392155071640364d3ba36be80a1bf4ad58de97"
    )
    voice = next(value for value in governed.voices if value["voiceProfileId"] == _REAL_PROFILE_ID)
    rights = next(
        value for value in governed.rights if value["rightsRecordId"] == voice["rightsRecordId"]
    )
    assert voice["displayLabel"] == "Local Voice 001"
    assert request_fingerprint(voice) == GOVERNED_KOKORO_VOICE_PROFILE_FINGERPRINT
    assert voice["narrationSuitability"] == "unknown"
    assert voice["dialogueSuitability"] == "unknown"
    assert voice["agePresentationRange"] is None
    assert request_fingerprint(rights) == GOVERNED_KOKORO_RIGHTS_RECORD_FINGERPRINT
    assert rights["state"] == "restricted"
    assert rights["consentStatus"] == "unknown"
    assert rights["commercialUsePermission"] == "restricted"
    assert rights["humanVerificationStatus"] == "pending"

    assert inventory["inventoryFingerprint"] == _GOVERNED_VOICE_INVENTORY_FINGERPRINT
    assert inventory["warningText"] == _GOVERNED_PRIVATE_AUDITION_WARNING
    assert inventory["warningFingerprint"] == (_GOVERNED_PRIVATE_AUDITION_WARNING_FINGERPRINT)
    assert inventory["warningFingerprint"] == sha256_text(inventory["warningText"])
    assert len(inventory["items"]) == 1
    item = inventory["items"][0]
    assert item["inventoryFingerprint"] == _GOVERNED_VOICE_INVENTORY_RECORD_FINGERPRINT
    assert item["neutralDisplayLabel"] == "Local Voice 001"
    assert item["providerVoiceId"] == "af_heart"
    assert item["providerDeclaredMetadataIndependentlyVerified"] is False
    assert item["productionExportEligible"] is False
    assert item["voiceTensor"] == {
        "relativePath": "voices/af_heart.bin",
        "byteSize": 522_240,
        "sha256": "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b",
        "scalarFormat": "float32_le",
        "shape": [510, 256],
        "elementCount": 130_560,
    }
    assert item["rights"]["rightsState"] == "restricted"
    assert item["rights"]["consentStatus"] == "unknown"
    inventory["items"].clear()
    assert len(_governed_voice_inventory()["items"]) == 1


def test_legacy_casting_profile_projects_exactly_after_migrationless_restart(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as first:
        project_id, run = _prepare_casting(
            first,
            auth_headers,
            key="phase3b1-legacy-profile-restart",
        )
        with first.app.state.database.session() as database_session:
            stored_run = database_session.get(CastingRunRow, run["castingRunId"])
            assert stored_run is not None
            stored_run.casting_profile_fingerprint = LEGACY_CASTING_PROFILE_FINGERPRINT
            assignments = list(
                database_session.scalars(
                    select(CastAssignmentRow).where(
                        CastAssignmentRow.casting_run_id == stored_run.id
                    )
                )
            )
            for assignment in assignments:
                assignment.casting_profile_fingerprint = LEGACY_CASTING_PROFILE_FINGERPRINT
            snapshot = database_session.scalar(
                select(ApprovedCastSnapshotRow)
                .where(ApprovedCastSnapshotRow.casting_run_id == stored_run.id)
                .order_by(
                    ApprovedCastSnapshotRow.revision.desc(),
                    ApprovedCastSnapshotRow.id.desc(),
                )
                .limit(1)
            )
            assert snapshot is not None
            manifest = parse_json(snapshot.manifest_json, {})
            for assignment_evidence in manifest["assignmentEvidence"]:
                assignment_evidence["castingProfileFingerprint"] = (
                    LEGACY_CASTING_PROFILE_FINGERPRINT
                )
            snapshot.casting_profile_fingerprint = LEGACY_CASTING_PROFILE_FINGERPRINT
            snapshot.manifest_json = canonical_json(manifest)
            snapshot.snapshot_fingerprint = first.app.state.casting._snapshot_fingerprint(
                snapshot,
                manifest,
            )

    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get(
            f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}",
            headers=auth_headers,
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["run"]["profile"] == {
            "profileId": LEGACY_CASTING_PROFILE_ID,
            "fingerprint": LEGACY_CASTING_PROFILE_FINGERPRINT,
        }
        with restarted.app.state.database.session() as database_session:
            stored_run = database_session.get(CastingRunRow, run["castingRunId"])
            assert stored_run is not None
            stored_run.casting_profile_fingerprint = "0" * 64
        unknown = restarted.get(
            f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}",
            headers=auth_headers,
        )
        assert unknown.status_code == 500
        assert unknown.json()["error"]["code"] == "CASTING_PROFILE_EVIDENCE_INVALID"


def test_inactive_kokoro_package_blocks_real_generation_without_fallback_or_audio(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _run, (narrator_role_id, _character_role_id) = (
        _prepare_real_narrator_and_character_cast(client, auth_headers)
    )
    calls = _activate_real_provider(
        app,
        settings,
        monkeypatch,
        project_id=project_id,
    )
    workspace = _workspace(client, auth_headers, project_id)
    role = next(
        value for value in workspace["roles"]["items"] if value["roleId"] == narrator_role_id
    )
    assert role["runtimeBindingStatus"] == "compatible"
    assert role["sessionEvidence"]["providerId"] == "kokoro-local-onnx"
    created = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": narrator_role_id,
            "evidence": role["sessionEvidence"],
            "restrictedLocalAuditionActivation": {
                "expectedInventoryFingerprint": workspace["voiceInventory"]["inventoryFingerprint"],
                "expectedWarningFingerprint": workspace["voiceInventory"]["warningFingerprint"],
                "reason": "Create a bounded session before exercising inactive-package refusal.",
            },
            "idempotencyKey": "phase3b1-inactive-package-session",
        },
    )
    assert created.status_code == 200, created.text
    audition_session = created.json()["session"]
    text = "A synthetic sentence must not reach an inactive provider."
    text_sha256 = sha256_text(text)
    preview = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{audition_session['auditionSessionId']}/normalization-preview"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": audition_session["auditionSessionId"],
            "expectedSessionRevision": audition_session["revision"],
            "text": text,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
        },
    )
    assert preview.status_code == 200, preview.text
    script = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{audition_session['auditionSessionId']}/scripts"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": audition_session["auditionSessionId"],
            "expectedSessionRevision": audition_session["revision"],
            "kind": "standardized_synthetic",
            "text": text,
            "sourceDocumentId": None,
            "sourceRevision": None,
            "sourceSpan": None,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
            "idempotencyKey": "phase3b1-inactive-package-script",
        },
    )
    assert script.status_code == 200, script.text
    prepared = _workspace(client, auth_headers, project_id)
    prepared_role = next(
        value for value in prepared["roles"]["items"] if value["roleId"] == narrator_role_id
    )
    generation_request = prepared_role["generationRequest"]
    assert generation_request["evidence"]["providerId"] == "kokoro-local-onnx"

    packages = client.get(
        f"/api/v1/projects/{project_id}/speech/model-packages?limit=200",
        headers=auth_headers,
    )
    assert packages.status_code == 200, packages.text
    real_package = next(
        value
        for value in packages.json()["items"]
        if value["manifest"]["providerId"] == "kokoro-local-onnx"
    )
    deactivated = client.post(
        (
            f"/api/v1/projects/{project_id}/speech/model-packages/"
            f"{real_package['manifest']['modelPackageId']}/actions"
        ),
        headers=auth_headers,
        json={
            "modelPackageId": real_package["manifest"]["modelPackageId"],
            "expectedManifestFingerprint": real_package["manifest"]["manifestFingerprint"],
            "expectedInstallationRevision": real_package["installation"]["installationRevision"],
            "action": "deactivate",
            "reason": "Prove inactive real packages fail closed without provider fallback.",
            "idempotencyKey": "phase3b1-deactivate-real-package",
        },
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["installation"]["status"] != "active"

    blocked = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{audition_session['auditionSessionId']}/generate"
        ),
        headers=auth_headers,
        json={"preview": generation_request},
    )
    if blocked.status_code == 202:
        terminal = wait_for_job(
            client,
            auth_headers,
            blocked.json()["jobId"],
            {"failed", "cancelled", "interrupted"},
            timeout=30,
        )
        assert terminal["state"] == "failed"
        failure_code = terminal["error"]["code"]
    else:
        assert blocked.status_code in {409, 422, 503}, blocked.text
        failure_code = blocked.json()["error"]["code"]
    assert failure_code == "AUDITION_SESSION_INVALIDATED"
    assert calls == []

    auditions = cast(AuditionRepository, app.state.auditions)
    with auditions.database.session() as database_session:
        assert (
            database_session.scalar(select(func.count()).select_from(SpeechRuntimeInstanceRow)) == 0
        )
        assert database_session.scalar(select(func.count()).select_from(AudioArtifactRow)) == 0
        assert database_session.scalar(select(func.count()).select_from(AuditionClipRow)) == 0
        provider_requests = list(database_session.scalars(select(SpeechProviderRequestRow)))
    assert all(value.provider_id == "kokoro-local-onnx" for value in provider_requests)
    assert all(value.outcome != "succeeded" for value in provider_requests)


def test_real_provider_fails_closed_when_exact_package_is_absent(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
) -> None:
    package_path = (
        settings.data_dir
        / "models"
        / "packages"
        / KOKORO_LOCAL_ONNX_MANIFEST.package_id
        / KOKORO_LOCAL_ONNX_MANIFEST.package_version
    )
    assert not package_path.exists()

    project_id, _run, (narrator_role_id, character_role_id) = (
        _prepare_real_narrator_and_character_cast(client, auth_headers)
    )
    workspace = _workspace(client, auth_headers, project_id)
    real_roles = [
        value
        for value in workspace["roles"]["items"]
        if value["roleId"] in {narrator_role_id, character_role_id}
    ]
    assert len(real_roles) == 2
    for role in real_roles:
        assert role["voiceProfileId"] == _REAL_PROFILE_ID
        assert role["runtimeBindingStatus"] == "unavailable"
        assert role["runtimeBindingReasonCode"] == ("VERIFIED_ACTIVE_MODEL_PACKAGE_REQUIRED")
        assert role["governedLocalVoice"]["productionExportEligible"] is False
        assert role["voiceRuntimeBinding"] is None
        assert role["sessionEvidence"] is None
        assert role["generationRequest"] is None

    auditions = cast(AuditionRepository, app.state.auditions)
    with auditions.database.session() as database_session:
        counts = {
            "auditionSessions": database_session.scalar(
                select(func.count()).select_from(AuditionSessionRow)
            ),
            "providerDispatches": database_session.scalar(
                select(func.count()).select_from(SpeechProviderRequestRow)
            ),
            "runtimeInstances": database_session.scalar(
                select(func.count()).select_from(SpeechRuntimeInstanceRow)
            ),
            "audioArtifacts": database_session.scalar(
                select(func.count()).select_from(AudioArtifactRow)
            ),
            "auditionClips": database_session.scalar(
                select(func.count()).select_from(AuditionClipRow)
            ),
        }
    assert counts == {
        "auditionSessions": 0,
        "providerDispatches": 0,
        "runtimeInstances": 0,
        "audioArtifacts": 0,
        "auditionClips": 0,
    }
    assert not package_path.exists()

    _write_absent_package_evidence_if_requested(
        {
            "schemaVersion": 1,
            "classification": "verified_absent_package_fail_closed",
            "packagePresent": False,
            "providerId": KOKORO_LOCAL_ONNX_MANIFEST.provider_id,
            "modelId": KOKORO_LOCAL_ONNX_MANIFEST.model_id,
            "modelPackageId": KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            "modelPackageVersion": KOKORO_LOCAL_ONNX_MANIFEST.package_version,
            "manifestFingerprint": KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
            "runtimeBindingStatus": "unavailable",
            "runtimeBindingReason": "VERIFIED_ACTIVE_MODEL_PACKAGE_REQUIRED",
            "sessionEvidencePresent": False,
            "generationRequestPresent": False,
            "counts": counts,
            "fallbackUsed": False,
            "productionExportEligible": False,
        }
    )


def test_undecided_listening_disposition_is_idempotent_and_restart_safe(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        project_id, _run, (narrator_role_id, _character_role_id) = (
            _prepare_real_narrator_and_character_cast(first, auth_headers)
        )
        _activate_real_provider(
            first_app,
            settings,
            monkeypatch,
            project_id=project_id,
        )
        generation_errors: list[BaseException] = []
        worker = first_app.state.worker
        audition_runner = worker.audition_runner

        def capture_generation_error(job: dict[str, Any], jobs: Any) -> None:
            try:
                audition_runner(job, jobs)
            except BaseException as exc:
                generation_errors.append(exc)
                raise

        monkeypatch.setattr(worker, "audition_runner", capture_generation_error)
        _session, clip = _create_and_generate_real_clip(
            first,
            auth_headers,
            project_id=project_id,
            role_id=narrator_role_id,
            key="phase3b1-undecided-restart",
            text="A synthetic listening decision remains explicitly undecided.",
            test_activation_failures=False,
            generation_errors=generation_errors,
        )
        workspace = _workspace(first, auth_headers, project_id)
        review = next(
            value
            for value in workspace["reviews"]
            if value["gateId"] == "per_role_audition_review" and value["roleId"] == narrator_role_id
        )
        decision_url = (
            f"/api/v1/projects/{project_id}/audition-reviews/per_role_audition_review/"
            f"{review['reviewId']}/decisions"
        )
        payload = {
            "expectedReviewRevision": review["revision"],
            "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
            "decision": "request_changes",
            "rationale": "Synthetic persistence exercise; no human quality claim is made.",
            "supersedesDecisionId": None,
            "listeningAttestation": {
                "auditionClipId": clip["auditionClipId"],
                "auditionClipRevision": clip["revision"],
                "auditionClipFingerprint": clip["clipFingerprint"],
                "audioArtifactId": clip["audioArtifact"]["audioArtifactId"],
                "audioArtifactSha256": clip["audioArtifact"]["sha256"],
                "listened": True,
                "disposition": "undecided",
            },
            "idempotencyKey": "phase3b1-undecided-restart-decision",
        }
        invalid_pairing = first.post(
            decision_url,
            headers=auth_headers,
            json={
                **payload,
                "decision": "approve",
                "idempotencyKey": "phase3b1-undecided-invalid-approval",
            },
        )
        assert invalid_pairing.status_code == 409, invalid_pairing.text
        assert invalid_pairing.json()["error"]["code"] == ("AUDITION_LISTENING_ATTESTATION_CHANGED")
        decided = first.post(decision_url, headers=auth_headers, json=payload)
        assert decided.status_code == 200, decided.text
        decision = decided.json()["decision"]
        attestation = decision["listeningAttestation"]
        assert decision["decision"] == "changes_requested"
        assert attestation["disposition"] == "undecided"
        assert attestation["actor"] == {"classification": "human", "actorId": "local_user"}
        assert attestation["rationale"] == payload["rationale"]
        replay = first.post(decision_url, headers=auth_headers, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["decision"] == decision
        blocked = _workspace(first, auth_headers, project_id)
        assert blocked["voiceReadinessSnapshot"] is None

    with TestClient(create_app(settings)) as restarted:
        history = restarted.get(
            f"/api/v1/projects/{project_id}/audition-review-decisions",
            headers=auth_headers,
            params={
                "gateId": "per_role_audition_review",
                "roleId": narrator_role_id,
                "limit": 200,
            },
        )
        assert history.status_code == 200, history.text
        restored = next(
            value
            for value in history.json()["items"]
            if value["decisionId"] == decision["decisionId"]
        )
        assert restored["decision"] == "changes_requested"
        assert restored["listeningAttestation"] == attestation
        workspace = _workspace(restarted, auth_headers, project_id)
        restored_review = next(
            value
            for value in workspace["reviews"]
            if value["gateId"] == "per_role_audition_review" and value["roleId"] == narrator_role_id
        )
        assert restored_review["state"] == "changes_requested"
        assert workspace["voiceReadinessSnapshot"] is None


def test_real_narrator_and_character_product_path_requires_activation_and_listening(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _run, (narrator_role_id, character_role_id) = (
        _prepare_real_narrator_and_character_cast(client, auth_headers)
    )
    _activate_fixture_model(
        client,
        auth_headers,
        project_id=project_id,
        key="phase3b1-fixture",
    )
    calls = _activate_real_provider(
        app,
        settings,
        monkeypatch,
        project_id=project_id,
    )
    generation_errors: list[BaseException] = []
    worker = app.state.worker
    audition_runner = worker.audition_runner

    def capture_generation_error(job: dict[str, Any], jobs: Any) -> None:
        try:
            audition_runner(job, jobs)
        except BaseException as exc:
            generation_errors.append(exc)
            raise

    monkeypatch.setattr(worker, "audition_runner", capture_generation_error)

    auditions = cast(AuditionRepository, app.state.auditions)
    with monkeypatch.context() as projection_patch:
        projection_patch.setattr(auditions, "_governed_inventory_item", lambda: None)
        blocked_projection = client.get(
            f"/api/v1/projects/{project_id}/auditions/workspace",
            headers=auth_headers,
        )
    assert blocked_projection.status_code == 500
    assert blocked_projection.json()["error"]["code"] == "GOVERNED_VOICE_INVENTORY_INVALID"

    workspace = _workspace(client, auth_headers, project_id)
    assert workspace["voiceInventory"]["inventoryFingerprint"] == (
        _GOVERNED_VOICE_INVENTORY_FINGERPRINT
    )
    narrator_role = next(
        value for value in workspace["roles"]["items"] if value["roleId"] == narrator_role_id
    )
    character_role = next(
        value for value in workspace["roles"]["items"] if value["roleId"] == character_role_id
    )
    for role in (narrator_role, character_role):
        assert role["governedLocalVoice"]["voiceProfileId"] == _REAL_PROFILE_ID
        assert role["voiceRuntimeBinding"]["providerId"] == "kokoro-local-onnx"
        assert role["runtimeBindingStatus"] == "compatible"
        assert role["sessionEvidence"]["providerId"] == "kokoro-local-onnx"

    fixture_roles = [
        value
        for value in workspace["roles"]["items"]
        if value["voiceProfileId"] != _REAL_PROFILE_ID and value["sessionEvidence"] is not None
    ]
    assert fixture_roles
    fixture_role = fixture_roles[0]
    assert fixture_role["governedLocalVoice"] is None
    assert fixture_role["sessionEvidence"]["providerId"] == "deterministic-pcm-wav-fixture"
    forbidden_fixture_activation = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": fixture_role["roleId"],
            "evidence": fixture_role["sessionEvidence"],
            "restrictedLocalAuditionActivation": {
                "expectedInventoryFingerprint": workspace["voiceInventory"]["inventoryFingerprint"],
                "expectedWarningFingerprint": workspace["voiceInventory"]["warningFingerprint"],
                "reason": "A fixture must reject this acknowledgement.",
            },
            "idempotencyKey": "phase3b1-fixture-forbidden-activation",
        },
    )
    assert forbidden_fixture_activation.status_code == 422
    assert forbidden_fixture_activation.json()["error"]["code"] == (
        "AUDITION_RESTRICTED_ACTIVATION_FORBIDDEN"
    )

    for index, fixture in enumerate(fixture_roles):
        _fixture_session, fixture_clip = _create_and_generate_fixture_clip(
            client,
            auth_headers,
            project_id=project_id,
            role_id=fixture["roleId"],
            key=f"phase3b1-fixture-{index}",
            text=f"Synthetic fixture line number {index + 1}.",
            generation_errors=generation_errors,
        )
        assert fixture_clip["roleId"] == fixture["roleId"]
        fixture_decision = _approve_audition_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="per_role_audition_review",
            role_id=fixture["roleId"],
            key=f"phase3b1-approve-fixture-{index}",
        )
        assert fixture_decision["decision"]["listeningAttestation"] is None

    narrator_session, narrator_clip = _create_and_generate_real_clip(
        client,
        auth_headers,
        project_id=project_id,
        role_id=narrator_role_id,
        key="phase3b1-narrator",
        text=_SYNTHETIC_TEXT,
        test_activation_failures=True,
        generation_errors=generation_errors,
    )
    character_session, character_clip = _create_and_generate_real_clip(
        client,
        auth_headers,
        project_id=project_id,
        role_id=character_role_id,
        key="phase3b1-character",
        text="A compass rests beside the sheltered inlet.",
        test_activation_failures=False,
        generation_errors=generation_errors,
    )
    assert narrator_session["roleId"] == narrator_role_id
    assert character_session["roleId"] == character_role_id
    assert narrator_clip["roleId"] == narrator_role_id
    assert character_clip["roleId"] == character_role_id
    assert len(calls) == 2
    assert all(call[0].voice_id == "af_heart" for call in calls)
    _assert_bound_dependency_drift(
        auditions,
        audition_session_id=narrator_session["auditionSessionId"],
    )

    review_workspace = _workspace(client, auth_headers, project_id)
    review = next(
        value
        for value in review_workspace["reviews"]
        if value["gateId"] == "per_role_audition_review" and value["roleId"] == narrator_role_id
    )
    decision_base = {
        "expectedReviewRevision": review["revision"],
        "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
        "decision": "approve",
        "rationale": "Synthetic contract exercise; no human quality claim is made.",
        "supersedesDecisionId": None,
    }
    decision_url = (
        f"/api/v1/projects/{project_id}/audition-reviews/per_role_audition_review/"
        f"{review['reviewId']}/decisions"
    )
    missing = client.post(
        decision_url,
        headers=auth_headers,
        json={**decision_base, "idempotencyKey": "phase3b1-listening-missing"},
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "AUDITION_LISTENING_ATTESTATION_REQUIRED"
    attestation_request = {
        "auditionClipId": narrator_clip["auditionClipId"],
        "auditionClipRevision": narrator_clip["revision"],
        "auditionClipFingerprint": narrator_clip["clipFingerprint"],
        "audioArtifactId": narrator_clip["audioArtifact"]["audioArtifactId"],
        "audioArtifactSha256": narrator_clip["audioArtifact"]["sha256"],
        "listened": True,
        "disposition": "acceptable",
    }
    mismatched = client.post(
        decision_url,
        headers=auth_headers,
        json={
            **decision_base,
            "listeningAttestation": {**attestation_request, "disposition": "unacceptable"},
            "idempotencyKey": "phase3b1-listening-mismatch",
        },
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "AUDITION_LISTENING_ATTESTATION_CHANGED"
    undecided_payload = {
        **decision_base,
        "decision": "request_changes",
        "listeningAttestation": {**attestation_request, "disposition": "undecided"},
        "idempotencyKey": "phase3b1-listening-undecided",
    }
    undecided_response = client.post(
        decision_url,
        headers=auth_headers,
        json=undecided_payload,
    )
    assert undecided_response.status_code == 200, undecided_response.text
    undecided_decision = undecided_response.json()["decision"]
    assert undecided_decision["decision"] == "changes_requested"
    assert undecided_decision["listeningAttestation"]["disposition"] == "undecided"
    undecided_replay = client.post(
        decision_url,
        headers=auth_headers,
        json=undecided_payload,
    )
    assert undecided_replay.status_code == 200, undecided_replay.text
    assert undecided_replay.json()["decision"] == undecided_decision
    after_undecided = _workspace(client, auth_headers, project_id)
    assert after_undecided["voiceReadinessSnapshot"] is None
    undecided_review = next(
        value
        for value in after_undecided["reviews"]
        if value["gateId"] == "per_role_audition_review" and value["roleId"] == narrator_role_id
    )
    assert undecided_review["state"] == "changes_requested"
    decided = client.post(
        decision_url,
        headers=auth_headers,
        json={
            **decision_base,
            "supersedesDecisionId": undecided_decision["decisionId"],
            "listeningAttestation": attestation_request,
            "idempotencyKey": "phase3b1-listening-current",
        },
    )
    assert decided.status_code == 200, decided.text
    decision = decided.json()["decision"]
    attestation = decision["listeningAttestation"]
    assert attestation["disposition"] == "acceptable"
    assert attestation["actor"] == {"classification": "human", "actorId": "local_user"}
    assert attestation["auditionClipFingerprint"] == narrator_clip["clipFingerprint"]
    fingerprint_material = dict(attestation)
    supplied_fingerprint = fingerprint_material.pop("attestationFingerprint")
    assert supplied_fingerprint == request_fingerprint(fingerprint_material)

    auditions = cast(AuditionRepository, app.state.auditions)
    with auditions.database.session() as database_session:
        decision_row = database_session.get(AuditionReviewDecisionRow, decision["decisionId"])
        assert decision_row is not None
        stored_provenance = parse_json(decision_row.provenance_json, {})
    assert stored_provenance["details"]["listeningAttestation"] == attestation
    history = client.get(
        f"/api/v1/projects/{project_id}/audition-review-decisions",
        headers=auth_headers,
        params={
            "gateId": "per_role_audition_review",
            "roleId": narrator_role_id,
            "limit": 200,
        },
    )
    assert history.status_code == 200, history.text
    restored = next(
        value for value in history.json()["items"] if value["decisionId"] == decision["decisionId"]
    )
    assert restored["listeningAttestation"] == attestation

    after_one_real_attestation = _workspace(client, auth_headers, project_id)
    assert after_one_real_attestation["voiceReadinessSnapshot"] is None
    character_review = next(
        value
        for value in after_one_real_attestation["reviews"]
        if value["gateId"] == "per_role_audition_review" and value["roleId"] == character_role_id
    )
    assert character_review["state"] == "pending"
    character_attestation_request = {
        "auditionClipId": character_clip["auditionClipId"],
        "auditionClipRevision": character_clip["revision"],
        "auditionClipFingerprint": character_clip["clipFingerprint"],
        "audioArtifactId": character_clip["audioArtifact"]["audioArtifactId"],
        "audioArtifactSha256": character_clip["audioArtifact"]["sha256"],
        "listened": True,
        "disposition": "acceptable",
    }
    character_decided = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-reviews/per_role_audition_review/"
            f"{character_review['reviewId']}/decisions"
        ),
        headers=auth_headers,
        json={
            "expectedReviewRevision": character_review["revision"],
            "expectedEvidenceFingerprint": character_review["evidence"]["evidenceFingerprint"],
            "decision": "approve",
            "rationale": "Synthetic contract exercise; no human quality claim is made.",
            "supersedesDecisionId": None,
            "listeningAttestation": character_attestation_request,
            "idempotencyKey": "phase3b1-listening-character-current",
        },
    )
    assert character_decided.status_code == 200, character_decided.text
    character_attestation = character_decided.json()["decision"]["listeningAttestation"]
    assert character_attestation["auditionClipFingerprint"] == character_clip["clipFingerprint"]
    assert character_attestation["audioArtifactSha256"] == character_clip["audioArtifact"]["sha256"]

    for gate_id in (
        "narrator_audition_review",
        "character_audition_review",
        "pronunciation_review",
    ):
        aggregate = _approve_audition_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id=gate_id,
            key=f"phase3b1-approve-{gate_id}",
        )
        assert aggregate["decision"]["listeningAttestation"] is None

    ready_for_human_readiness_decision = _workspace(client, auth_headers, project_id)
    readiness = ready_for_human_readiness_decision["voiceReadinessSnapshot"]
    assert readiness is not None
    assert readiness["requiredRoleCount"] == len(
        ready_for_human_readiness_decision["roles"]["items"]
    )
    assert readiness["approvedRoleCount"] == readiness["requiredRoleCount"]
    assert readiness["reviewEligible"] is True
    assert readiness["authorizesFullBookRendering"] is False
    readiness_review = next(
        value
        for value in ready_for_human_readiness_decision["reviews"]
        if value["gateId"] == "voice_readiness_review"
    )
    assert readiness_review["state"] == "pending"
