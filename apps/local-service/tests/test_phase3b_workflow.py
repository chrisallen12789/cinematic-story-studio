from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.casting import CASTING_GATE_IDS
from cinematic_story_service.models import (
    AnalysisEntityRow,
    AuditionReviewDecisionRow,
    AuditionReviewRecordRow,
    AuditionSessionRow,
    SpeechProviderRequestRow,
    VoiceReadinessDecisionRow,
)
from cinematic_story_service.util import request_fingerprint, sha256_bytes, sha256_text
from tests.conftest import SYNTHETIC_STORY, submit_import, wait_for_extraction, wait_for_job
from tests.test_phase2_api import _collection, create_phase2_run
from tests.test_phase3a_casting import (
    _approve_phase2,
    _candidates,
    _correct,
    _create_casting_run,
    _evidence,
    _roles,
)
from tests.test_phase3a_custom_roles import _custom_role_payload
from tests.test_phase3a_governance import (
    _decision_payload,
    _post_decision,
    _prepare_casting,
    _reviews,
)


def _assert_public_provenance(value: object) -> None:
    assert isinstance(value, dict)
    required = {"origin", "producerId", "producerVersion", "recordedAt"}
    assert required <= set(value)
    assert set(value) <= required | {"inputFingerprint", "reasonCode"}


def _establish_approved_cast(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    key: str,
) -> tuple[str, dict[str, Any]]:
    project_id, run = _prepare_casting(client, auth_headers, key=key)
    return _complete_approved_cast(
        client,
        auth_headers,
        project_id=project_id,
        run=run,
        key=key,
    )


def _complete_approved_cast(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    run: dict[str, Any],
    key: str,
) -> tuple[str, dict[str, Any]]:
    initial_roles = _roles(client, auth_headers, run=run)
    character_voice_ids = (
        "synthetic-character-01",
        "synthetic-character-02",
        "synthetic-character-03",
        "synthetic-character-04",
    )
    character_index = 0

    for index, original_role in enumerate(initial_roles):
        role = next(
            value
            for value in _roles(client, auth_headers, run=run)
            if value["roleId"] == original_role["roleId"]
        )
        if role["roleType"] == "primary_narrator":
            voice_profile_id = "synthetic-narrator-01"
        elif role["roleType"] == "named_character":
            voice_profile_id = character_voice_ids[character_index % len(character_voice_ids)]
            character_index += 1
        else:
            run = _correct(
                client,
                auth_headers,
                run=run,
                role=role,
                operation="mark_intentionally_uncast",
                key=f"{key}-uncast-{index}",
            )
            continue

        if role["performanceRequirements"]["language"] == "und":
            run = _correct(
                client,
                auth_headers,
                run=run,
                role=role,
                operation="change_casting_requirement",
                key=f"{key}-language-{index}",
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
        assert voice_profile_id in {
            value["voiceProfileId"]
            for value in _candidates(
                client,
                auth_headers,
                run=run,
                role=role,
            )
        }
        run = _correct(
            client,
            auth_headers,
            run=run,
            role=role,
            operation="select_voice",
            key=f"{key}-select-{index}",
            voice_profile_id=voice_profile_id,
            corrected_value={"voiceProfileId": voice_profile_id},
        )

    conflicts_response = client.get(
        (f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/conflicts"),
        headers=auth_headers,
        params={**_evidence(run), "limit": 200},
    )
    assert conflicts_response.status_code == 200, conflicts_response.text
    conflict = next(
        (
            value
            for value in conflicts_response.json()["items"]
            if value["category"] == "metadata_similarity_risk"
            and value["resolutionState"] == "open"
        ),
        None,
    )
    if conflict is not None:
        role = next(
            value
            for value in _roles(client, auth_headers, run=run)
            if value["roleId"] == conflict["roleIds"][0]
        )
        run = _correct(
            client,
            auth_headers,
            run=run,
            role=role,
            operation="approve_voice_reuse",
            key=f"{key}-approve-metadata-conflict",
            corrected_value={
                "conflictId": conflict["conflictId"],
                "approvedRoleIds": conflict["roleIds"],
            },
        )

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
                key=f"{key}-approve-{gate_id}",
            ),
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["decision"]["decision"] == "approved"
    return project_id, run


def _establish_approved_cast_after_phase2_correction(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    key: str,
) -> tuple[str, dict[str, Any], str, str]:
    imported, created = create_phase2_run(
        client,
        auth_headers,
        idempotency_key=key,
    )
    project_id = imported["project"]["projectId"]
    run_id = created["run"]["runId"]
    detail = client.get(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    base_run = detail.json()["run"]
    base_correction_fingerprint = base_run["currentSnapshot"]["correctionSetFingerprint"]
    chapter = _collection(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        collection="chapters",
    )["items"][0]
    corrected = client.post(
        f"/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections",
        headers=auth_headers,
        json={
            "category": "structure_label",
            "targetCollection": "chapters",
            "targetEntityId": chapter["entityId"],
            "expectedTargetRevision": chapter["effectiveRevision"],
            "expectedRunFingerprint": base_run["runFingerprint"],
            "previousValueFingerprint": chapter["effectiveValueFingerprint"],
            "patch": {"title": "Chapter One: Corrected Audition Signal"},
            "reason": "Exercise a real Phase 2 correction before audition reconstruction.",
            "idempotencyKey": f"{key}-phase2-correction",
        },
    )
    assert corrected.status_code == 200, corrected.text
    corrected_run = corrected.json()["run"]
    corrected_correction_fingerprint = corrected_run["currentSnapshot"]["correctionSetFingerprint"]
    assert corrected_correction_fingerprint != base_correction_fingerprint

    analysis_run, decisions = _approve_phase2(
        client,
        auth_headers,
        project_id=project_id,
        run_id=run_id,
        idempotency_prefix=f"{key}-approve-phase2",
    )
    assert (
        analysis_run["currentSnapshot"]["correctionSetFingerprint"]
        == corrected_correction_fingerprint
    )
    casting_run = _create_casting_run(
        client,
        auth_headers,
        project_id=project_id,
        analysis_run=analysis_run,
        decisions=decisions,
        idempotency_key=f"{key}-casting",
    )
    _project_id, casting_run = _complete_approved_cast(
        client,
        auth_headers,
        project_id=project_id,
        run=casting_run,
        key=key,
    )
    return (
        project_id,
        casting_run,
        base_correction_fingerprint,
        corrected_correction_fingerprint,
    )


def _activate_fixture_model(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    key: str,
) -> None:
    listed = client.get(
        f"/api/v1/projects/{project_id}/speech/model-packages",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert listed.status_code == 200, listed.text
    manifest = next(
        value["manifest"]
        for value in listed.json()["items"]
        if value["manifest"]["providerId"] == "deterministic-pcm-wav-fixture"
    )
    route = (
        f"/api/v1/projects/{project_id}/speech/model-packages/{manifest['modelPackageId']}/actions"
    )
    verified = client.post(
        route,
        headers=auth_headers,
        json={
            "modelPackageId": manifest["modelPackageId"],
            "expectedManifestFingerprint": manifest["manifestFingerprint"],
            "expectedInstallationRevision": None,
            "action": "verify",
            "reason": "Verify the repository-owned deterministic fixture.",
            "idempotencyKey": f"{key}-verify-fixture",
        },
    )
    assert verified.status_code == 200, verified.text
    activated = client.post(
        route,
        headers=auth_headers,
        json={
            "modelPackageId": manifest["modelPackageId"],
            "expectedManifestFingerprint": manifest["manifestFingerprint"],
            "expectedInstallationRevision": verified.json()["installation"]["installationRevision"],
            "action": "activate",
            "reason": "Activate the repository-owned deterministic fixture.",
            "idempotencyKey": f"{key}-activate-fixture",
        },
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["installation"]["status"] == "active"


def _workspace(
    client: TestClient,
    auth_headers: dict[str, str],
    project_id: str,
    *,
    role_cursor: str | None = None,
    role_limit: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if role_cursor is not None:
        params["roleCursor"] = role_cursor
    if role_limit is not None:
        params["roleLimit"] = role_limit
    response = client.get(
        f"/api/v1/projects/{project_id}/auditions/workspace",
        headers=auth_headers,
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]


def _add_approved_pronunciation(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    written_form: str,
    pronunciation: str,
    key: str,
    supersedes_entry_id: str | None = None,
    scope: str = "project",
    scope_id: str | None = None,
    case_sensitive: bool = False,
    match_rule: str = "whole_word",
    priority: int = 10,
) -> dict[str, Any]:
    dictionary = _workspace(client, auth_headers, project_id)["currentDictionary"]
    created = client.post(
        f"/api/v1/projects/{project_id}/pronunciations/entries",
        headers=auth_headers,
        json={
            "expectedDictionaryRevision": dictionary["revision"],
            "expectedDictionaryFingerprint": dictionary["dictionaryFingerprint"],
            "writtenForm": written_form,
            "language": "en",
            "locale": "en-US",
            "scope": scope,
            "scopeId": scope_id,
            "representation": "provider_neutral",
            "pronunciation": pronunciation,
            "ipa": None,
            "providerId": None,
            "providerCompiledValue": None,
            "caseSensitive": case_sensitive,
            "matchRule": match_rule,
            "priority": priority,
            "reason": "Add a repository-owned synthetic pronunciation.",
            "supersedesEntryId": supersedes_entry_id,
            "idempotencyKey": f"{key}-create",
        },
    )
    assert created.status_code == 200, created.text
    pending = created.json()
    entry = pending["entry"]
    assert entry["verificationState"] == "pending"
    decided = client.post(
        (f"/api/v1/projects/{project_id}/pronunciations/entries/{entry['entryId']}/decisions"),
        headers=auth_headers,
        json={
            "decision": "approve",
            "expectedEntryRevision": entry["revision"],
            "expectedEntryFingerprint": entry["entryFingerprint"],
            "expectedDictionaryRevision": pending["dictionary"]["revision"],
            "expectedDictionaryFingerprint": pending["dictionary"]["dictionaryFingerprint"],
            "rationale": "Approve the repository-owned synthetic pronunciation.",
            "idempotencyKey": f"{key}-approve",
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["entry"]["verificationState"] == "approved"
    return decided.json()


def _create_session_and_script(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_id: str,
    text: str,
    key: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    workspace = _workspace(client, auth_headers, project_id)
    role = next(value for value in workspace["roles"]["items"] if value["roleId"] == role_id)
    assert role["sessionEvidence"] is not None
    created_session = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": role_id,
            "evidence": role["sessionEvidence"],
            "idempotencyKey": f"{key}-session",
        },
    )
    assert created_session.status_code == 200, created_session.text
    session = created_session.json()["session"]
    _assert_public_provenance(session["provenance"])
    _assert_public_provenance(session["voiceRuntimeBinding"]["provenance"])
    text_sha256 = sha256_text(text)
    preview = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{session['auditionSessionId']}/normalization-preview"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": session["auditionSessionId"],
            "expectedSessionRevision": session["revision"],
            "text": text,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
        },
    )
    assert preview.status_code == 200, preview.text
    preview_value = preview.json()
    assert preview_value["projectId"] == project_id
    assert preview_value["auditionSessionId"] == session["auditionSessionId"]
    assert preview_value["auditionSessionRevision"] == session["revision"]
    assert preview_value["providerId"] == session["providerId"]
    assert preview_value["acceptedOptionalNormalizationIds"] == []
    assert preview_value["customPronunciationScopeIds"] == []
    plan = preview_value["plan"]
    assert plan["originalTextSha256"] == text_sha256
    assert plan["normalizedTextSha256"] == sha256_text(text)
    assert plan["providerId"] == session["providerId"]
    assert plan["unsupportedCharacterCodePoints"] == []
    assert plan["warnings"] == []
    assert plan["humanReviewRequired"] is False

    created_script = client.post(
        (f"/api/v1/projects/{project_id}/audition-sessions/{session['auditionSessionId']}/scripts"),
        headers=auth_headers,
        json={
            "auditionSessionId": session["auditionSessionId"],
            "expectedSessionRevision": session["revision"],
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
    assert created_script.status_code == 200, created_script.text
    created_script_value = created_script.json()
    script = created_script_value["script"]
    assert script["text"] == text
    assert script["localOnly"] is True
    assert created_script_value["normalizationPlan"]["providerId"] == session["providerId"]
    assert created_script_value["normalizationPlan"]["humanReviewRequired"] is False

    refreshed = _workspace(client, auth_headers, project_id)
    refreshed_role = next(
        value for value in refreshed["roles"]["items"] if value["roleId"] == role_id
    )
    generation_request = refreshed_role["generationRequest"]
    assert generation_request is not None
    request_material = dict(generation_request)
    supplied_fingerprint = request_material.pop("requestFingerprint")
    assert supplied_fingerprint == request_fingerprint(request_material)
    return session, script, generation_request


def _create_contextual_session_and_script(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_id: str,
    text: str,
    kind: str,
    key: str,
    source_document_id: str | None = None,
    source_revision: int | None = None,
    source_span: dict[str, int] | None = None,
    custom_scope_ids: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    custom_scope_ids = custom_scope_ids or []
    workspace = _workspace(client, auth_headers, project_id)
    role = next(value for value in workspace["roles"]["items"] if value["roleId"] == role_id)
    evidence = role["sessionEvidence"]
    assert evidence is not None
    created_session = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": role_id,
            "evidence": evidence,
            "idempotencyKey": f"{key}-session",
        },
    )
    assert created_session.status_code == 200, created_session.text
    session = created_session.json()["session"]
    _assert_public_provenance(session["provenance"])
    _assert_public_provenance(session["voiceRuntimeBinding"]["provenance"])
    text_sha256 = sha256_text(text)
    preview = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-sessions/"
            f"{session['auditionSessionId']}/normalization-preview"
        ),
        headers=auth_headers,
        json={
            "auditionSessionId": session["auditionSessionId"],
            "expectedSessionRevision": session["revision"],
            "text": text,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
            "customPronunciationScopeIds": custom_scope_ids,
        },
    )
    assert preview.status_code == 200, preview.text
    preview_value = preview.json()
    assert preview_value["projectId"] == project_id
    assert preview_value["auditionSessionId"] == session["auditionSessionId"]
    assert preview_value["auditionSessionRevision"] == session["revision"]
    assert preview_value["providerId"] == session["providerId"]
    assert preview_value["acceptedOptionalNormalizationIds"] == []
    assert preview_value["customPronunciationScopeIds"] == sorted(custom_scope_ids)
    assert preview_value["plan"]["providerId"] == session["providerId"]
    created_script = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions/{session['auditionSessionId']}/scripts",
        headers=auth_headers,
        json={
            "auditionSessionId": session["auditionSessionId"],
            "expectedSessionRevision": session["revision"],
            "kind": kind,
            "text": text,
            "sourceDocumentId": source_document_id,
            "sourceRevision": source_revision,
            "sourceSpan": source_span,
            "sourceTextSha256": text_sha256,
            "acceptedOptionalNormalizationIds": [],
            "customPronunciationScopeIds": custom_scope_ids,
            "idempotencyKey": f"{key}-script",
        },
    )
    assert created_script.status_code == 200, created_script.text
    refreshed = _workspace(client, auth_headers, project_id)
    refreshed_role = next(
        value for value in refreshed["roles"]["items"] if value["roleId"] == role_id
    )
    generation_request = refreshed_role["generationRequest"]
    assert generation_request is not None
    return session, created_script.json(), generation_request


def _generate(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    session_id: str,
    generation_request: dict[str, Any],
    terminal_states: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    queued = client.post(
        (f"/api/v1/projects/{project_id}/audition-sessions/{session_id}/generate"),
        headers=auth_headers,
        json={"preview": generation_request},
    )
    assert queued.status_code == 202, queued.text
    terminal = wait_for_job(
        client,
        auth_headers,
        queued.json()["jobId"],
        terminal_states or {"succeeded", "failed", "cancelled", "interrupted"},
        timeout=30,
    )
    return queued.json(), terminal


def _clips(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_id: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": 200}
    if role_id is not None:
        params["roleId"] = role_id
    if session_id is not None:
        params["auditionSessionId"] = session_id
    response = client.get(
        f"/api/v1/projects/{project_id}/audition-clips",
        headers=auth_headers,
        params=params,
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    for item in items:
        _assert_public_provenance(item["provenance"])
        _assert_public_provenance(item["audioQuality"]["provenance"])
    return items


def _approve_audition_review(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    gate_id: str,
    key: str,
    role_id: str | None = None,
) -> dict[str, Any]:
    workspace = _workspace(client, auth_headers, project_id)
    review = next(
        value
        for value in workspace["reviews"]
        if value["gateId"] == gate_id and value["roleId"] == role_id
    )
    assert review["state"] == "pending", review
    evidence_fingerprint = review["evidence"].get("evidenceFingerprint")
    if evidence_fingerprint is None:
        readiness = workspace["voiceReadinessSnapshot"]
        assert gate_id == "voice_readiness_review"
        assert readiness is not None
        evidence_fingerprint = readiness["snapshotFingerprint"]
    decided = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-reviews/{gate_id}/"
            f"{review['reviewId']}/decisions"
        ),
        headers=auth_headers,
        json={
            "expectedReviewRevision": review["revision"],
            "expectedEvidenceFingerprint": evidence_fingerprint,
            "decision": "approve",
            "rationale": f"Approve repository-owned synthetic {gate_id} evidence.",
            "supersedesDecisionId": None,
            "idempotencyKey": key,
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["decision"]["decision"] == "approved"
    return decided.json()


def test_workspace_uses_only_exact_approved_cast_assignments(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, run = _prepare_casting(
            client,
            auth_headers,
            key="phase3b-intentionally-uncast-workspace",
        )
        roles_route = f"/api/v1/projects/{project_id}/casting-runs/{run['castingRunId']}/roles"
        created = client.post(
            roles_route,
            headers=auth_headers,
            json=_custom_role_payload(
                run,
                key="phase3b-create-intentionally-uncast-role",
            ),
        )
        assert created.status_code == 200, created.text
        custom_role = created.json()["role"]
        custom_role_id = custom_role["roleId"]
        selected_candidate = next(
            value
            for value in _candidates(
                client,
                auth_headers,
                run=created.json()["run"],
                role=custom_role,
            )
            if value["assessment"]["compatibilityStatus"] != "incompatible"
            and value["assessment"]["rightsEligibility"] in {"eligible", "restricted"}
        )
        selected_run = _correct(
            client,
            auth_headers,
            run=created.json()["run"],
            role=custom_role,
            operation="select_voice",
            key="phase3b-select-then-uncast-role",
            voice_profile_id=selected_candidate["voiceProfileId"],
            corrected_value={"voiceProfileId": selected_candidate["voiceProfileId"]},
        )

        project_id, approved_run = _complete_approved_cast(
            client,
            auth_headers,
            project_id=project_id,
            run=selected_run,
            key="phase3b-intentionally-uncast-workspace",
        )
        workspace = _workspace(client, auth_headers, project_id)
        role_items = workspace["roles"]["items"]
        approved_assignment_ids = approved_run["approvedCastSnapshot"]["assignmentIds"]

        assert workspace["roles"]["total"] == len(approved_assignment_ids)
        assert custom_role_id not in {value["roleId"] for value in role_items}
        assert {value["assignmentId"] for value in role_items} == set(approved_assignment_ids)
        assert all(value["current"] for value in workspace["prerequisites"]), [
            (value["prerequisiteId"], value["statusCode"])
            for value in workspace["prerequisites"]
            if not value["current"]
        ]

        paged_role_ids: list[str] = []
        cursor: str | None = None
        while True:
            page = _workspace(
                client,
                auth_headers,
                project_id,
                role_cursor=cursor,
                role_limit=1,
            )
            paged_role_ids.extend(value["roleId"] for value in page["roles"]["items"])
            cursor = page["roles"].get("nextCursor")
            if cursor is None:
                break
        assert paged_role_ids == [value["roleId"] for value in role_items]


def test_fixture_workflow_cache_jobs_reviews_restart_and_targeted_invalidation(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    settings = replace(settings, runtime_shutdown_evidence_enabled=True)
    shutdown_evidence_path = settings.data_dir / "phase3b-runtime-shutdown-evidence.json"
    project_id = ""
    role_ids: list[str] = []
    all_clip_ids: set[str] = set()
    aster_clip_ids: set[str] = set()
    audio_clip: dict[str, Any] = {}
    original_entry_id = ""
    readiness_decision_id = ""
    runtime_instance_id = ""
    runtime_worker_pid = 0
    persisted_decision_ids: dict[tuple[str, str | None], str] = {}

    with TestClient(create_app(settings)) as client:
        project_id, _run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-complete-workflow",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-complete-workflow",
        )
        aster = _add_approved_pronunciation(
            client,
            auth_headers,
            project_id=project_id,
            written_form="Aster",
            pronunciation="AS-ter",
            key="phase3b-aster",
        )
        original_entry_id = aster["entry"]["entryId"]
        _add_approved_pronunciation(
            client,
            auth_headers,
            project_id=project_id,
            written_form="Zephyr",
            pronunciation="ZEF-er",
            key="phase3b-zephyr",
        )

        first_entry_page = client.get(
            f"/api/v1/projects/{project_id}/pronunciations/entries",
            headers=auth_headers,
            params={"limit": 1},
        )
        assert first_entry_page.status_code == 200, first_entry_page.text
        assert first_entry_page.json()["total"] == 4
        assert first_entry_page.json()["pageSize"] == 1
        assert isinstance(first_entry_page.json()["nextCursor"], str)
        pronunciation_history = list(first_entry_page.json()["items"])
        cursor = first_entry_page.json()["nextCursor"]
        while cursor is not None:
            next_entry_page = client.get(
                f"/api/v1/projects/{project_id}/pronunciations/entries",
                headers=auth_headers,
                params={"limit": 1, "cursor": cursor},
            )
            assert next_entry_page.status_code == 200, next_entry_page.text
            assert next_entry_page.json()["pageSize"] == 1
            pronunciation_history.extend(next_entry_page.json()["items"])
            cursor = next_entry_page.json().get("nextCursor")
        assert len(pronunciation_history) == 4
        assert len({value["entryId"] for value in pronunciation_history}) == 2
        assert {value["revision"] for value in pronunciation_history} == {1, 2}

        workspace = _workspace(client, auth_headers, project_id)
        assert workspace["approvedCastSnapshot"] is not None
        assert all(value["current"] for value in workspace["prerequisites"])
        role_ids = [value["roleId"] for value in workspace["roles"]["items"]]
        assert role_ids
        assert {value["roleType"] for value in workspace["roles"]["items"]} == {
            "narrator",
            "character",
        }

        bound_role = workspace["roles"]["items"][0]
        bound_evidence = bound_role["sessionEvidence"]
        assert bound_evidence is not None
        bound_session_response = client.post(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            json={
                "roleId": bound_role["roleId"],
                "evidence": bound_evidence,
                "idempotencyKey": "phase3b-exact-manuscript-session",
            },
        )
        assert bound_session_response.status_code == 200, bound_session_response.text
        bound_session = bound_session_response.json()["session"]
        exact_excerpt = 'Mara: "We begin now."'
        excerpt_start = SYNTHETIC_STORY.index(exact_excerpt)
        excerpt_end = excerpt_start + len(exact_excerpt)
        bound_script_response = client.post(
            (
                f"/api/v1/projects/{project_id}/audition-sessions/"
                f"{bound_session['auditionSessionId']}/scripts"
            ),
            headers=auth_headers,
            json={
                "auditionSessionId": bound_session["auditionSessionId"],
                "expectedSessionRevision": bound_session["revision"],
                "kind": "approved_manuscript_excerpt",
                "text": exact_excerpt,
                "sourceDocumentId": bound_evidence["sourceDocumentId"],
                "sourceRevision": bound_evidence["sourceRevision"],
                "sourceSpan": {"start": excerpt_start, "end": excerpt_end},
                "sourceTextSha256": sha256_text(exact_excerpt),
                "acceptedOptionalNormalizationIds": [],
                "idempotencyKey": "phase3b-exact-manuscript-script",
            },
        )
        assert bound_script_response.status_code == 200, bound_script_response.text
        bound_script = bound_script_response.json()["script"]
        assert bound_script["text"] == exact_excerpt
        assert bound_script["sourceSpan"] == {
            "start": excerpt_start,
            "end": excerpt_end,
        }
        stale_span = client.post(
            (
                f"/api/v1/projects/{project_id}/audition-sessions/"
                f"{bound_session['auditionSessionId']}/scripts"
            ),
            headers=auth_headers,
            json={
                "auditionSessionId": bound_session["auditionSessionId"],
                "expectedSessionRevision": bound_session["revision"],
                "kind": "role_dialogue_excerpt",
                "text": "Mara",
                "sourceDocumentId": bound_evidence["sourceDocumentId"],
                "sourceRevision": bound_evidence["sourceRevision"],
                "sourceSpan": {
                    "start": excerpt_start + 1,
                    "end": excerpt_start + 5,
                },
                "sourceTextSha256": sha256_text("Mara"),
                "acceptedOptionalNormalizationIds": [],
                "idempotencyKey": "phase3b-reject-shifted-manuscript-span",
            },
        )
        assert stale_span.status_code == 409, stale_span.text
        assert stale_span.json()["error"]["code"] == "AUDITION_SOURCE_SPAN_CHANGED"

        for index, role_id in enumerate(role_ids):
            text = (
                "Aster checks the repository-owned synthetic signal."
                if index == 0
                else f"Repository-owned synthetic audition phrase {index}."
            )
            session, _script, generation_request = _create_session_and_script(
                client,
                auth_headers,
                project_id=project_id,
                role_id=role_id,
                text=text,
                key=f"phase3b-role-{index}",
            )
            _queued, terminal = _generate(
                client,
                auth_headers,
                project_id=project_id,
                session_id=session["auditionSessionId"],
                generation_request=generation_request,
            )
            assert terminal["state"] == "succeeded", terminal
            clip = _clips(
                client,
                auth_headers,
                project_id=project_id,
                session_id=session["auditionSessionId"],
            )[0]
            assert clip["cacheStatus"] == "miss"
            assert clip["providerClass"] == "deterministic_fixture"
            assert clip["audioQuality"]["validWav"] is True
            assert clip["audioQuality"]["nonSilent"] is True
            assert clip["audioQuality"]["subjectiveQualityClaimed"] is False
            all_clip_ids.add(clip["auditionClipId"])
            if index == 0:
                aster_clip_ids.add(clip["auditionClipId"])

        first_role_id = role_ids[0]
        cache_session, _cache_script, cache_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=first_role_id,
            text="Aster checks the repository-owned synthetic signal.",
            key="phase3b-cache-hit",
        )
        _cache_queued, cache_terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=cache_session["auditionSessionId"],
            generation_request=cache_request,
        )
        assert cache_terminal["state"] == "succeeded", cache_terminal
        cache_clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=cache_session["auditionSessionId"],
        )[0]
        first_role_clips = _clips(
            client,
            auth_headers,
            project_id=project_id,
            role_id=first_role_id,
        )
        original_clip = next(
            value for value in first_role_clips if value["auditionClipId"] in aster_clip_ids
        )
        assert cache_clip["cacheStatus"] == "verified_hit"
        assert cache_clip["cacheKey"] == original_clip["cacheKey"]
        assert cache_clip["audioArtifact"] == original_clip["audioArtifact"]
        all_clip_ids.add(cache_clip["auditionClipId"])
        aster_clip_ids.add(cache_clip["auditionClipId"])

        worker = client.app.state.worker
        worker.controls.execution_gate.clear()
        cancel_session, _cancel_script, cancel_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=first_role_id,
            text="Repository-owned synthetic cancellation boundary.",
            key="phase3b-cancel",
        )
        try:
            cancel_queued, running = _generate(
                client,
                auth_headers,
                project_id=project_id,
                session_id=cancel_session["auditionSessionId"],
                generation_request=cancel_request,
                terminal_states={"running"},
            )
            assert running["state"] == "running"
            cancelled_response = client.post(
                f"/api/v1/jobs/{cancel_queued['jobId']}/cancel",
                headers=auth_headers,
            )
            assert cancelled_response.status_code == 200, cancelled_response.text
            cancelled = wait_for_job(
                client,
                auth_headers,
                cancel_queued["jobId"],
                {"cancelled"},
                timeout=20,
            )
            assert cancelled["state"] == "cancelled"
        finally:
            worker.controls.execution_gate.set()

        retry_session, _retry_script, retry_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=first_role_id,
            text="Repository-owned synthetic retry boundary.",
            key="phase3b-retry",
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
        retried_response = client.post(
            f"/api/v1/jobs/{retry_queued['jobId']}/retry",
            headers=auth_headers,
        )
        assert retried_response.status_code == 200, retried_response.text
        assert retried_response.json()["job"]["attempt"] == 2
        retried = wait_for_job(
            client,
            auth_headers,
            retry_queued["jobId"],
            {"succeeded", "failed"},
            timeout=30,
        )
        assert retried["state"] == "succeeded", retried
        retry_clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=retry_session["auditionSessionId"],
        )[0]
        with client.app.state.database.session() as database_session:
            retry_attempts = list(
                database_session.scalars(
                    select(SpeechProviderRequestRow)
                    .where(SpeechProviderRequestRow.job_id == retry_queued["jobId"])
                    .order_by(
                        SpeechProviderRequestRow.attempt,
                        SpeechProviderRequestRow.id,
                    )
                )
            )
            assert [value.attempt for value in retry_attempts] == [1, 2]
            assert len({value.id for value in retry_attempts}) == 2
            assert len({value.idempotency_key for value in retry_attempts}) == 2
            assert retry_attempts[0].outcome == "failed"
            assert retry_attempts[0].retryable is True
            assert retry_attempts[1].outcome == "succeeded"
            assert retry_attempts[1].retryable is False
            assert (
                retry_attempts[1].voice_runtime_binding_id
                == retry_attempts[0].voice_runtime_binding_id
                == retry_session["voiceRuntimeBindingId"]
            )
            assert (
                retry_attempts[1].voice_runtime_binding_fingerprint
                == retry_attempts[0].voice_runtime_binding_fingerprint
                == retry_session["voiceRuntimeBindingFingerprint"]
            )
            assert (
                retry_attempts[1].provider_voice_id
                == retry_attempts[0].provider_voice_id
                == retry_session["providerVoiceId"]
            )
            assert retry_clip["providerRequestId"] == retry_attempts[1].id
            assert retry_clip["voiceRuntimeBindingId"] == retry_attempts[1].voice_runtime_binding_id
            assert (
                retry_clip["voiceRuntimeBindingFingerprint"]
                == retry_attempts[1].voice_runtime_binding_fingerprint
            )
            assert retry_clip["providerVoiceId"] == retry_attempts[1].provider_voice_id
        all_clip_ids.add(retry_clip["auditionClipId"])
        audio_clip = retry_clip

        session_page = client.get(
            f"/api/v1/projects/{project_id}/audition-sessions",
            headers=auth_headers,
            params={"limit": 1},
        )
        assert session_page.status_code == 200, session_page.text
        assert session_page.json()["total"] == len(role_ids) + 4
        assert session_page.json()["pageSize"] == 1
        assert isinstance(session_page.json()["nextCursor"], str)
        clip_page = client.get(
            f"/api/v1/projects/{project_id}/audition-clips",
            headers=auth_headers,
            params={"limit": 1},
        )
        assert clip_page.status_code == 200, clip_page.text
        assert clip_page.json()["total"] == len(all_clip_ids)
        assert clip_page.json()["pageSize"] == 1
        assert isinstance(clip_page.json()["nextCursor"], str)

        artifact = audio_clip["audioArtifact"]
        audio = client.get(
            (f"/api/v1/projects/{project_id}/audition-clips/{audio_clip['auditionClipId']}/audio"),
            headers=auth_headers,
            params={
                "auditionSessionId": audio_clip["auditionSessionId"],
                "audioArtifactId": artifact["audioArtifactId"],
                "expectedClipRevision": audio_clip["revision"],
                "expectedClipFingerprint": audio_clip["clipFingerprint"],
                "expectedArtifactSha256": artifact["sha256"],
                "byteSize": artifact["byteSize"],
            },
        )
        assert audio.status_code == 200, audio.text
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.headers["cache-control"] == "no-store"
        assert int(audio.headers["content-length"]) == artifact["byteSize"]
        assert sha256_bytes(audio.content) == artifact["sha256"]

        for index, role_id in enumerate(role_ids):
            approved_role = _approve_audition_review(
                client,
                auth_headers,
                project_id=project_id,
                gate_id="per_role_audition_review",
                role_id=role_id,
                key=f"phase3b-approve-role-{index}",
            )
            persisted_decision_ids[("per_role_audition_review", role_id)] = approved_role[
                "decision"
            ]["decisionId"]
        for gate_id in (
            "narrator_audition_review",
            "character_audition_review",
            "pronunciation_review",
        ):
            approved_aggregate = _approve_audition_review(
                client,
                auth_headers,
                project_id=project_id,
                gate_id=gate_id,
                key=f"phase3b-approve-{gate_id}",
            )
            persisted_decision_ids[(gate_id, None)] = approved_aggregate["decision"]["decisionId"]
            if gate_id == "pronunciation_review":
                pronunciation_snapshot = approved_aggregate["voiceReadinessSnapshot"]
                assert pronunciation_snapshot is not None
                assert pronunciation_snapshot["rightsEvidenceFingerprint"] == (
                    approved_aggregate["review"]["evidence"]["rightsRecordFingerprint"]
                )
        readiness = _approve_audition_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="voice_readiness_review",
            key="phase3b-approve-voice-readiness",
        )
        readiness_decision_id = readiness["decision"]["decisionId"]
        persisted_decision_ids[("voice_readiness_review", None)] = readiness_decision_id
        readiness_history_response = client.get(
            f"/api/v1/projects/{project_id}/audition-review-decisions",
            headers=auth_headers,
            params={"gateId": "voice_readiness_review", "limit": 1},
        )
        assert readiness_history_response.status_code == 200, readiness_history_response.text
        readiness_history = readiness_history_response.json()
        assert readiness_history["gateId"] == "voice_readiness_review"
        assert readiness_history["roleId"] is None
        assert readiness_history["total"] == 1
        assert readiness_history["items"][0]["decisionId"] == readiness_decision_id
        snapshot = readiness["voiceReadinessSnapshot"]
        assert snapshot["requiredRoleCount"] == len(role_ids)
        assert snapshot["approvedRoleCount"] == len(role_ids)
        assert snapshot["reviewEligible"] is True
        assert snapshot["authorizes"] == "later_performance_direction_only"
        assert snapshot["authorizesFullBookRendering"] is False
        runtime_instances = _workspace(client, auth_headers, project_id)["runtimeInstances"]
        assert len(runtime_instances) == 1
        assert runtime_instances[0]["state"] == "idle"
        assert runtime_instances[0]["workerPid"] > 0
        runtime_instance_id = runtime_instances[0]["runtimeInstanceId"]
        runtime_worker_pid = runtime_instances[0]["workerPid"]

    shutdown_evidence = json.loads(shutdown_evidence_path.read_text(encoding="utf-8"))
    assert shutdown_evidence["contractVersion"] == "1.0.0"
    assert shutdown_evidence["serviceInstanceId"] == settings.instance_id
    assert shutdown_evidence["ownedRuntimeCount"] == 1
    assert shutdown_evidence["allGracefulShutdownsConfirmed"] is True
    assert len(shutdown_evidence["runtimeExits"]) == 1
    shutdown_exit = shutdown_evidence["runtimeExits"][0]
    assert shutdown_exit["runtimeInstanceId"] == runtime_instance_id
    assert shutdown_exit["workerPid"] == runtime_worker_pid
    assert shutdown_exit["state"] == "stopped"
    assert shutdown_exit["stopReasonCode"] == "clean"
    assert shutdown_exit["exitCode"] == 0
    assert shutdown_exit["shutdownAcknowledged"] is True
    assert shutdown_exit["gracefulShutdownConfirmed"] is True
    assert shutdown_exit["terminatedByParent"] is False
    assert shutdown_exit["ownershipConfirmed"] is True
    assert shutdown_exit["ownedProcessesConfirmedExited"] is True
    if os.name == "nt":
        assert shutdown_exit["jobObjectAssigned"] is True
    assert shutdown_exit["deniedNetworkAttemptCount"] == 0

    with TestClient(create_app(settings)) as restarted:
        restored = _workspace(restarted, auth_headers, project_id)
        for (gate_id, role_id), expected_decision_id in persisted_decision_ids.items():
            restored_review = next(
                value
                for value in restored["reviews"]
                if value["gateId"] == gate_id and value["roleId"] == role_id
            )
            assert restored_review["state"] == "approved"
            assert restored_review["latestDecision"] is not None
            assert restored_review["latestDecision"]["decision"] == "approved"
            assert restored_review["latestDecision"]["decisionId"] == expected_decision_id
        assert restored["voiceReadinessSnapshot"]["approvedRoleCount"] == len(role_ids)
        stopped_runtime = next(
            value
            for value in restored["runtimeInstances"]
            if value["runtimeInstanceId"] == runtime_instance_id
        )
        assert stopped_runtime["state"] == "stopped"
        assert stopped_runtime["stoppedAt"] is not None
        assert stopped_runtime["stopReasonCode"] == "clean"
        assert stopped_runtime["exitCode"] == 0
        assert stopped_runtime["shutdownAcknowledged"] is True
        assert stopped_runtime["gracefulShutdownConfirmed"] is True
        assert stopped_runtime["terminatedByParent"] is False
        assert stopped_runtime["ownershipConfirmed"] is True
        assert stopped_runtime["ownedProcessesConfirmedExited"] is True
        if os.name == "nt":
            assert stopped_runtime["jobObjectAssigned"] is True
        assert stopped_runtime["deniedNetworkAttemptCount"] == 0
        assert {
            value["auditionClipId"]
            for value in _clips(
                restarted,
                auth_headers,
                project_id=project_id,
            )
        } == all_clip_ids

        artifact = audio_clip["audioArtifact"]
        restored_audio = restarted.get(
            (f"/api/v1/projects/{project_id}/audition-clips/{audio_clip['auditionClipId']}/audio"),
            headers=auth_headers,
            params={
                "auditionSessionId": audio_clip["auditionSessionId"],
                "audioArtifactId": artifact["audioArtifactId"],
                "expectedClipRevision": audio_clip["revision"],
                "expectedClipFingerprint": audio_clip["clipFingerprint"],
                "expectedArtifactSha256": artifact["sha256"],
                "byteSize": artifact["byteSize"],
            },
        )
        assert restored_audio.status_code == 200, restored_audio.text
        changed = _add_approved_pronunciation(
            restarted,
            auth_headers,
            project_id=project_id,
            written_form="Aster",
            pronunciation="AS-tur",
            key="phase3b-aster-supersession",
            supersedes_entry_id=original_entry_id,
        )
        assert set(changed["invalidatedClipIds"]) == aster_clip_ids
        assert set(changed["preservedClipIds"]) == all_clip_ids - aster_clip_ids
        invalidated = _workspace(restarted, auth_headers, project_id)
        role_review_states = {
            value["roleId"]: value["state"]
            for value in invalidated["reviews"]
            if value["gateId"] == "per_role_audition_review"
        }
        assert role_review_states == {role_id: "approved" for role_id in role_ids}
        pronunciation_review = next(
            value for value in invalidated["reviews"] if value["gateId"] == "pronunciation_review"
        )
        assert pronunciation_review["state"] == "pending"
        assert not any(
            value["gateId"] == "voice_readiness_review"
            for value in invalidated["reviews"]
        )

        detail = restarted.get(
            f"/api/v1/projects/{project_id}",
            headers=auth_headers,
        )
        assert detail.status_code == 200, detail.text
        cleared = restarted.post(
            f"/api/v1/projects/{project_id}/audition-cache/clear",
            headers=auth_headers,
            json={
                "expectedProjectRevision": detail.json()["project"]["revision"],
                "reason": "Clear generated synthetic audition cache bytes.",
                "idempotencyKey": "phase3b-clear-populated-cache",
            },
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["projectId"] == project_id
        assert cleared.json()["clearedRecordCount"] > 0
        after_clear = _workspace(restarted, auth_headers, project_id)
        assert not any(
            value["gateId"] == "voice_readiness_review"
            for value in after_clear["reviews"]
        )
        readiness_history_response = restarted.get(
            f"/api/v1/projects/{project_id}/audition-review-decisions",
            headers=auth_headers,
            params={"gateId": "voice_readiness_review", "limit": 200},
        )
        assert readiness_history_response.status_code == 200, readiness_history_response.text
        readiness_history = readiness_history_response.json()
        assert readiness_history["total"] >= 2
        assert readiness_history["items"][0]["decision"] == "invalidated"
        assert any(
            item["decisionId"] == readiness_decision_id and item["decision"] == "approved"
            for item in readiness_history["items"]
        )
        assert all(
            value["state"] == "invalidated"
            for value in after_clear["reviews"]
            if value["gateId"] == "per_role_audition_review"
        )
        assert {
            value["gateId"]: value["state"]
            for value in after_clear["reviews"]
            if value["gateId"]
            in {"narrator_audition_review", "character_audition_review"}
        } == {
            "narrator_audition_review": "blocked",
            "character_audition_review": "blocked",
        }
        with restarted.app.state.database.session() as database_session:
            for (gate_id, _role_id), decision_id in persisted_decision_ids.items():
                decision_type = (
                    VoiceReadinessDecisionRow
                    if gate_id == "voice_readiness_review"
                    else AuditionReviewDecisionRow
                )
                historical_decision = database_session.get(decision_type, decision_id)
                assert historical_decision is not None
                assert historical_decision.decision == "approved"
            latest_readiness_decision = database_session.scalar(
                select(VoiceReadinessDecisionRow)
                .where(VoiceReadinessDecisionRow.project_id == project_id)
                .order_by(
                    VoiceReadinessDecisionRow.revision.desc(),
                    VoiceReadinessDecisionRow.id.desc(),
                )
                .limit(1)
            )
            assert latest_readiness_decision is not None
            assert latest_readiness_decision.decision == "invalidated"
            assert latest_readiness_decision.supersedes_decision_id == readiness_decision_id
        purged_audio = restarted.get(
            (f"/api/v1/projects/{project_id}/audition-clips/{audio_clip['auditionClipId']}/audio"),
            headers=auth_headers,
            params={
                "auditionSessionId": audio_clip["auditionSessionId"],
                "audioArtifactId": artifact["audioArtifactId"],
                "expectedClipRevision": audio_clip["revision"],
                "expectedClipFingerprint": audio_clip["clipFingerprint"],
                "expectedArtifactSha256": artifact["sha256"],
                "byteSize": artifact["byteSize"],
            },
        )
        assert purged_audio.status_code == 409
        assert purged_audio.json()["error"]["code"] == "AUDITION_AUDIO_CHANGED"


def test_corrected_phase2_reconstruction_and_scoped_pronunciation_contexts(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    (
        project_id,
        _run,
        base_correction_fingerprint,
        corrected_correction_fingerprint,
    ) = _establish_approved_cast_after_phase2_correction(
        client,
        auth_headers,
        key="phase3b-corrected-scopes",
    )
    assert corrected_correction_fingerprint != base_correction_fingerprint
    _activate_fixture_model(
        client,
        auth_headers,
        project_id=project_id,
        key="phase3b-corrected-scopes",
    )
    before_entries = _workspace(client, auth_headers, project_id)
    role_id = before_entries["roles"]["items"][0]["roleId"]
    source_evidence = before_entries["roles"]["items"][0]["sessionEvidence"]
    assert source_evidence is not None
    assert source_evidence["phase2CorrectionSetFingerprint"] == corrected_correction_fingerprint
    exact_excerpt = 'Mara: "We begin now."'
    excerpt_start = SYNTHETIC_STORY.index(exact_excerpt)
    excerpt_end = excerpt_start + len(exact_excerpt)
    with client.app.state.database.session() as database_session:
        chapter = database_session.scalar(
            select(AnalysisEntityRow)
            .where(
                AnalysisEntityRow.project_id == project_id,
                AnalysisEntityRow.run_id == source_evidence["phase2RunId"],
                AnalysisEntityRow.collection == "chapters",
                AnalysisEntityRow.start_offset <= excerpt_start,
                AnalysisEntityRow.end_offset >= excerpt_end,
            )
            .order_by(
                AnalysisEntityRow.end_offset - AnalysisEntityRow.start_offset,
                AnalysisEntityRow.id,
            )
            .limit(1)
        )
        assert chapter is not None
        scene = database_session.scalar(
            select(AnalysisEntityRow)
            .where(
                AnalysisEntityRow.project_id == project_id,
                AnalysisEntityRow.run_id == source_evidence["phase2RunId"],
                AnalysisEntityRow.collection == "scenes",
                AnalysisEntityRow.parent_entity_id == chapter.id,
                AnalysisEntityRow.start_offset <= excerpt_start,
                AnalysisEntityRow.end_offset >= excerpt_end,
            )
            .order_by(
                AnalysisEntityRow.end_offset - AnalysisEntityRow.start_offset,
                AnalysisEntityRow.id,
            )
            .limit(1)
        )
        assert scene is not None
        chapter_id = chapter.id
        scene_id = scene.id

    scoped_entries: dict[str, dict[str, Any]] = {}
    for label, written_form, pronunciation, scope, scope_id, case_sensitive in (
        ("chapter", "Mara", "MAH-rah", "chapter", chapter_id, True),
        ("scene", "begin", "bee-GIN", "scene", scene_id, False),
        ("custom", "now", "NAU", "custom", "custom-scope-match", False),
        (
            "other_chapter",
            "Mara",
            "MAR-ah",
            "chapter",
            "chapter-outside-source-span",
            True,
        ),
        (
            "other_scene",
            "begin",
            "BEG-in",
            "scene",
            "scene-outside-source-span",
            False,
        ),
        (
            "other_custom",
            "now",
            "NOH",
            "custom",
            "custom-scope-other",
            False,
        ),
    ):
        scoped_entries[label] = _add_approved_pronunciation(
            client,
            auth_headers,
            project_id=project_id,
            written_form=written_form,
            pronunciation=pronunciation,
            key=f"phase3b-scope-{label}",
            scope=scope,
            scope_id=scope_id,
            case_sensitive=case_sensitive,
            priority=20,
        )

    manuscript_custom_session, manuscript_custom, manuscript_custom_request = (
        _create_contextual_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text=exact_excerpt,
            kind="approved_manuscript_excerpt",
            source_document_id=source_evidence["sourceDocumentId"],
            source_revision=source_evidence["sourceRevision"],
            source_span={"start": excerpt_start, "end": excerpt_end},
            custom_scope_ids=["custom-scope-match"],
            key="phase3b-context-manuscript-custom",
        )
    )
    manuscript_session, manuscript, manuscript_request = _create_contextual_session_and_script(
        client,
        auth_headers,
        project_id=project_id,
        role_id=role_id,
        text=exact_excerpt,
        kind="approved_manuscript_excerpt",
        source_document_id=source_evidence["sourceDocumentId"],
        source_revision=source_evidence["sourceRevision"],
        source_span={"start": excerpt_start, "end": excerpt_end},
        custom_scope_ids=[],
        key="phase3b-context-manuscript",
    )
    synthetic_session, synthetic, synthetic_request = _create_contextual_session_and_script(
        client,
        auth_headers,
        project_id=project_id,
        role_id=role_id,
        text=exact_excerpt,
        kind="pronunciation_test",
        custom_scope_ids=["custom-scope-match"],
        key="phase3b-context-synthetic-custom",
    )

    matching_entry_ids = {
        label: value["entry"]["entryId"]
        for label, value in scoped_entries.items()
        if label in {"chapter", "scene", "custom"}
    }
    assert manuscript_custom["pronunciationPlan"]["scopeContext"] == {
        "chapterId": chapter_id,
        "sceneId": scene_id,
        "customScopeIds": ["custom-scope-match"],
    }
    assert set(manuscript_custom["normalizationPlan"]["appliedPronunciationEntryIds"]) == set(
        matching_entry_ids.values()
    )
    assert {
        value["entryId"]
        for value in manuscript_custom["pronunciationPlan"]["dependencyEntryRevisions"]
    } == set(matching_entry_ids.values())
    assert manuscript["pronunciationPlan"]["scopeContext"] == {
        "chapterId": chapter_id,
        "sceneId": scene_id,
        "customScopeIds": [],
    }
    assert set(manuscript["normalizationPlan"]["appliedPronunciationEntryIds"]) == {
        matching_entry_ids["chapter"],
        matching_entry_ids["scene"],
    }
    assert synthetic["pronunciationPlan"]["scopeContext"] == {
        "chapterId": None,
        "sceneId": None,
        "customScopeIds": ["custom-scope-match"],
    }
    assert synthetic["normalizationPlan"]["appliedPronunciationEntryIds"] == [
        matching_entry_ids["custom"]
    ]

    with client.app.state.database.session() as database_session:
        persisted_sessions = list(
            database_session.scalars(
                select(AuditionSessionRow).where(
                    AuditionSessionRow.id.in_(
                        (
                            manuscript_custom_session["auditionSessionId"],
                            manuscript_session["auditionSessionId"],
                            synthetic_session["auditionSessionId"],
                        )
                    )
                )
            )
        )
        assert len(persisted_sessions) == 3
        assert {value.analysis_correction_set_fingerprint for value in persisted_sessions} == {
            corrected_correction_fingerprint
        }

    generated_clip_ids: set[str] = set()
    stale_review: dict[str, Any] | None = None
    for index, (session, generation_request) in enumerate(
        (
            (manuscript_custom_session, manuscript_custom_request),
            (manuscript_session, manuscript_request),
            (synthetic_session, synthetic_request),
        )
    ):
        _queued, terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session["auditionSessionId"],
            generation_request=generation_request,
        )
        assert terminal["state"] == "succeeded", terminal
        clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session["auditionSessionId"],
        )[0]
        generated_clip_ids.add(clip["auditionClipId"])
        if index == 0:
            workspace_reviews = _workspace(client, auth_headers, project_id)["reviews"]
            assert not any(
                value["gateId"] == "per_role_audition_review"
                and value["roleId"] == role_id
                for value in workspace_reviews
            )
            with client.app.state.database.session() as database_session:
                historical_review = database_session.scalar(
                    select(AuditionReviewRecordRow).where(
                        AuditionReviewRecordRow.project_id == project_id,
                        AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                        AuditionReviewRecordRow.session_id == session["auditionSessionId"],
                        AuditionReviewRecordRow.clip_id == clip["auditionClipId"],
                    )
                )
                assert historical_review is not None
                stale_review = {
                    "reviewId": historical_review.id,
                    "revision": historical_review.revision,
                    "evidence": json.loads(historical_review.evidence_json),
                }

    assert stale_review is not None
    stale_decision = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-reviews/"
            f"per_role_audition_review/{stale_review['reviewId']}/decisions"
        ),
        headers=auth_headers,
        json={
            "expectedReviewRevision": stale_review["revision"],
            "expectedEvidenceFingerprint": stale_review["evidence"]["evidenceFingerprint"],
            "decision": "approve",
            "rationale": "Reject a decision over historical review evidence.",
            "supersedesDecisionId": None,
            "idempotencyKey": "phase3b-reject-historical-review-decision",
        },
    )
    assert stale_decision.status_code == 409, stale_decision.text
    assert stale_decision.json()["error"]["code"] == "AUDITION_REVIEW_CHANGED"

    for label, pronunciation in (
        ("other_chapter", "MAR-uh"),
        ("other_scene", "beh-GIN"),
        ("other_custom", "NOW"),
    ):
        prior = scoped_entries[label]["entry"]
        changed = _add_approved_pronunciation(
            client,
            auth_headers,
            project_id=project_id,
            written_form=prior["writtenForm"],
            pronunciation=pronunciation,
            key=f"phase3b-scope-{label}-supersession",
            supersedes_entry_id=prior["entryId"],
            scope=prior["scope"],
            scope_id=prior["scopeId"],
            case_sensitive=prior["caseSensitive"],
            match_rule=prior["matchRule"],
            priority=prior["priority"],
        )
        assert changed["invalidatedClipIds"] == []
        assert set(changed["preservedClipIds"]) == generated_clip_ids


def test_new_import_blocks_sessions_bound_to_the_preceding_approved_source(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project_id, _run = _establish_approved_cast(
        client,
        auth_headers,
        key="phase3b-stale-import",
    )
    _activate_fixture_model(
        client,
        auth_headers,
        project_id=project_id,
        key="phase3b-stale-import",
    )
    before = _workspace(client, auth_headers, project_id)
    assert before["roles"]["items"]
    old_evidence = before["roles"]["items"][0]["sessionEvidence"]
    assert old_evidence is not None

    queued = submit_import(
        client,
        auth_headers,
        project_id=project_id,
        filename="synthetic-replacement.md",
        content=b"# Replacement\n\nRepository-owned synthetic replacement text.\n",
        media_type="text/markdown",
        declared_format="markdown",
        idempotency_key="phase3b-stale-import-replacement",
    )
    wait_for_extraction(client, auth_headers, queued)

    after = _workspace(client, auth_headers, project_id)
    import_prerequisite = next(
        value for value in after["prerequisites"] if value["prerequisiteId"] == "import_review"
    )
    assert import_prerequisite["current"] is False
    assert import_prerequisite["statusCode"] == "APPROVAL_REQUIRED"

    stale_creation = client.post(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        json={
            "roleId": before["roles"]["items"][0]["roleId"],
            "evidence": old_evidence,
            "idempotencyKey": "phase3b-reject-prior-source-session",
        },
    )
    assert stale_creation.status_code == 409, stale_creation.text
    assert stale_creation.json()["error"]["code"] in {
        "AUDITION_IMPORT_REVIEW_REQUIRED",
        "AUDITION_PHASE2_CHANGED",
        "AUDITION_SOURCE_CHANGED",
    }
    assert all(role["sessionEvidence"] is None for role in after["roles"]["items"])
