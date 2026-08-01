from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.models import (
    AudioArtifactRow,
    AuditionCacheRecordRow,
    IdempotencyRow,
    ProjectRow,
)
from cinematic_story_service.util import utc_now
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _clips,
    _create_session_and_script,
    _establish_approved_cast,
    _generate,
    _workspace,
)


def _approve_current_review(
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
        snapshot = workspace["voiceReadinessSnapshot"]
        assert gate_id == "voice_readiness_review"
        assert snapshot is not None
        evidence_fingerprint = snapshot["snapshotFingerprint"]
    supersession_head = review["latestDecision"]
    response = client.post(
        (
            f"/api/v1/projects/{project_id}/audition-reviews/{gate_id}/"
            f"{review['reviewId']}/decisions"
        ),
        headers=auth_headers,
        json={
            "expectedReviewRevision": review["revision"],
            "expectedEvidenceFingerprint": evidence_fingerprint,
            "decision": "approve",
            "rationale": f"Approve current repository-owned {gate_id} evidence.",
            "supersedesDecisionId": (
                supersession_head["decisionId"] if supersession_head is not None else None
            ),
            "idempotencyKey": key,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"]["decision"] == "approved"
    return response.json()


def _generate_role_replacements(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_ids: list[str],
    key: str,
) -> None:
    for index, role_id in enumerate(role_ids):
        session, _script, request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text=f"Repository-owned reconciliation audition {key} {index}.",
            key=f"{key}-role-{index}",
        )
        _queued, terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session["auditionSessionId"],
            generation_request=request,
        )
        assert terminal["state"] == "succeeded", terminal


def _approve_current_voice_evidence(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    role_ids: list[str],
    key: str,
) -> dict[str, Any]:
    for index, role_id in enumerate(role_ids):
        _approve_current_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="per_role_audition_review",
            role_id=role_id,
            key=f"{key}-per-role-{index}",
        )
    for gate_id in (
        "narrator_audition_review",
        "character_audition_review",
        "pronunciation_review",
    ):
        _approve_current_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id=gate_id,
            key=f"{key}-{gate_id}",
        )
    return _approve_current_review(
        client,
        auth_headers,
        project_id=project_id,
        gate_id="voice_readiness_review",
        key=f"{key}-voice-readiness",
    )


def test_reapproved_replacement_evidence_survives_repeated_reconciliation(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-reconciliation-reapproval",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-reconciliation-reapproval",
        )
        role_ids = [
            value["roleId"]
            for value in _workspace(client, auth_headers, project_id)["roles"]["items"]
        ]
        assert role_ids

        _generate_role_replacements(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="initial",
        )
        initial = _approve_current_voice_evidence(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="initial",
        )
        initial_readiness_id = initial["decision"]["decisionId"]
        readiness_evidence = initial["review"]["evidence"]
        assert set(readiness_evidence) == {
            "approvedCastSnapshotFingerprint",
            "audioQualityFingerprint",
            "auditionClipId",
            "auditionClipRevision",
            "auditionSessionId",
            "castAssignmentFingerprint",
            "evidenceFingerprint",
            "gateId",
            "modelVerificationFingerprint",
            "projectId",
            "pronunciationDependencyFingerprint",
            "pronunciationDictionaryFingerprint",
            "rightsRecordFingerprint",
            "roleId",
            "runtimeProfileFingerprint",
        }
        assert readiness_evidence["projectId"] == project_id
        assert readiness_evidence["gateId"] == "voice_readiness_review"
        assert readiness_evidence["roleId"] is None
        assert readiness_evidence["auditionSessionId"] is None
        assert readiness_evidence["auditionClipId"] is None
        assert readiness_evidence["auditionClipRevision"] is None
        assert readiness_evidence["castAssignmentFingerprint"] is None
        assert (
            readiness_evidence["evidenceFingerprint"]
            == initial["voiceReadinessSnapshot"]["snapshotFingerprint"]
        )
        assert all(
            isinstance(readiness_evidence[key], str) and len(readiness_evidence[key]) == 64
            for key in (
                "approvedCastSnapshotFingerprint",
                "audioQualityFingerprint",
                "evidenceFingerprint",
                "modelVerificationFingerprint",
                "pronunciationDependencyFingerprint",
                "pronunciationDictionaryFingerprint",
                "rightsRecordFingerprint",
                "runtimeProfileFingerprint",
            )
        )

        packages = client.get(
            f"/api/v1/projects/{project_id}/speech/model-packages",
            headers=auth_headers,
            params={"limit": 200},
        )
        assert packages.status_code == 200, packages.text
        fixture = next(
            value
            for value in packages.json()["items"]
            if value["manifest"]["providerId"] == "deterministic-pcm-wav-fixture"
        )
        manifest = fixture["manifest"]
        installation = fixture["installation"]
        assert installation is not None
        reverified = client.post(
            (
                f"/api/v1/projects/{project_id}/speech/model-packages/"
                f"{manifest['modelPackageId']}/actions"
            ),
            headers=auth_headers,
            json={
                "modelPackageId": manifest["modelPackageId"],
                "expectedManifestFingerprint": manifest["manifestFingerprint"],
                "expectedInstallationRevision": installation["installationRevision"],
                "action": "verify",
                "reason": "Rotate exact fixture verification evidence for reconciliation.",
                "idempotencyKey": "phase3b-reconciliation-reverify",
            },
        )
        assert reverified.status_code == 200, reverified.text
        assert reverified.json()["installation"]["status"] == "active"

        invalidated = _workspace(client, auth_headers, project_id)
        invalidated_readiness = next(
            value for value in invalidated["reviews"] if value["gateId"] == "voice_readiness_review"
        )
        assert invalidated_readiness["state"] == "invalidated"
        assert invalidated_readiness["latestDecision"]["decisionId"] != initial_readiness_id

        _generate_role_replacements(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="replacement",
        )
        replacement = _approve_current_voice_evidence(
            client,
            auth_headers,
            project_id=project_id,
            role_ids=role_ids,
            key="replacement",
        )
        replacement_readiness_id = replacement["decision"]["decisionId"]
        assert replacement_readiness_id != initial_readiness_id

        for _ in range(3):
            refreshed = _workspace(client, auth_headers, project_id)
            readiness_review = next(
                value
                for value in refreshed["reviews"]
                if value["gateId"] == "voice_readiness_review"
            )
            assert readiness_review["state"] == "approved"
            assert readiness_review["latestDecision"] is not None
            assert readiness_review["latestDecision"]["decisionId"] == replacement_readiness_id
            assert refreshed["voiceReadinessSnapshot"]["approvedRoleCount"] == len(role_ids)


def test_cache_clear_commit_failure_restores_exact_audio_and_metadata(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-cache-clear-compensation",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-cache-clear-compensation",
        )
        role_id = _workspace(client, auth_headers, project_id)["roles"]["items"][0]["roleId"]
        audition_session, _script, request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned cache rollback signal.",
            key="phase3b-cache-clear-compensation",
        )
        _queued, terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=request,
        )
        assert terminal["state"] == "succeeded", terminal
        clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )[0]
        artifact = clip["audioArtifact"]
        artifact_id = artifact["audioArtifactId"]
        audio_path = (
            settings.data_dir
            / "projects"
            / project_id
            / "auditions"
            / "audio"
            / f"{artifact_id}.wav"
        )
        original_audio = audio_path.read_bytes()
        assert hashlib.sha256(original_audio).hexdigest() == artifact["sha256"]
        project_response = client.get(
            f"/api/v1/projects/{project_id}",
            headers=auth_headers,
        )
        assert project_response.status_code == 200, project_response.text
        original_project_revision = project_response.json()["project"]["revision"]

        original_commit = Session.commit
        commit_failure_injected = False
        observed_tombstones: list[tuple[bool, int, bytes]] = []

        def fail_cache_clear_commit(database_session: Session) -> None:
            nonlocal commit_failure_injected
            pending_artifact = next(
                (
                    value
                    for value in database_session.dirty
                    if isinstance(value, AudioArtifactRow)
                    and value.id == artifact_id
                    and value.availability == "purged"
                ),
                None,
            )
            pending_cache = next(
                (
                    value
                    for value in database_session.dirty
                    if isinstance(value, AuditionCacheRecordRow)
                    and value.artifact_id == artifact_id
                    and value.state == "cleared"
                ),
                None,
            )
            if (
                not commit_failure_injected
                and pending_artifact is not None
                and pending_cache is not None
            ):
                tombstones = list(audio_path.parent.glob(f".{audio_path.name}.purge.*.tmp"))
                observed_tombstones.append(
                    (
                        audio_path.exists(),
                        len(tombstones),
                        tombstones[0].read_bytes() if len(tombstones) == 1 else b"",
                    )
                )
                commit_failure_injected = True
                raise RuntimeError("injected cache-clear commit failure")
            original_commit(database_session)

        monkeypatch.setattr(Session, "commit", fail_cache_clear_commit)
        with pytest.raises(RuntimeError, match="injected cache-clear commit failure"):
            client.post(
                f"/api/v1/projects/{project_id}/audition-cache/clear",
                headers=auth_headers,
                json={
                    "expectedProjectRevision": original_project_revision,
                    "reason": "Exercise rollback-safe private cache clearing.",
                    "idempotencyKey": "phase3b-cache-clear-commit-failure",
                },
            )

        assert commit_failure_injected
        assert observed_tombstones == [(False, 1, original_audio)]
        assert audio_path.read_bytes() == original_audio
        assert list(audio_path.parent.glob(f".{audio_path.name}.purge.*.tmp")) == []
        with client.app.state.database.session() as database_session:
            persisted_artifact = database_session.get(AudioArtifactRow, artifact_id)
            persisted_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            persisted_project = database_session.get(ProjectRow, project_id)
            persisted_idempotency = database_session.get(
                IdempotencyRow,
                {
                    "scope": f"phase3b-cache-clear:{project_id}",
                    "key": "phase3b-cache-clear-commit-failure",
                },
            )
            assert persisted_artifact is not None
            assert persisted_artifact.availability == "present"
            assert persisted_artifact.content_sha256 == artifact["sha256"]
            assert persisted_cache is not None
            assert persisted_cache.state == "verified"
            assert persisted_project is not None
            assert persisted_project.revision == original_project_revision
            assert persisted_idempotency is None

        rollback_crash_tombstone = audio_path.parent / f".{audio_path.name}.purge.{'a' * 32}.tmp"
        audio_path.rename(rollback_crash_tombstone)
        client.app.state.auditions._reconcile_audio_storage()
        assert audio_path.read_bytes() == original_audio
        assert not rollback_crash_tombstone.exists()
        with client.app.state.database.session() as database_session:
            restored_artifact = database_session.get(AudioArtifactRow, artifact_id)
            restored_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            assert restored_artifact is not None
            assert restored_artifact.availability == "present"
            assert restored_cache is not None
            assert restored_cache.state == "verified"

        committed_crash_tombstone = audio_path.parent / f".{audio_path.name}.purge.{'b' * 32}.tmp"
        audio_path.rename(committed_crash_tombstone)
        with client.app.state.database.immediate_session() as database_session:
            committed_artifact = database_session.get(AudioArtifactRow, artifact_id)
            committed_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            assert committed_artifact is not None
            assert committed_cache is not None
            committed_artifact.availability = "purged"
            committed_artifact.purged_at = utc_now()
            committed_cache.state = "cleared"
            committed_cache.purged_at = utc_now()
        client.app.state.auditions._reconcile_audio_storage()
        assert not audio_path.exists()
        assert not committed_crash_tombstone.exists()
        with client.app.state.database.session() as database_session:
            purged_artifact = database_session.get(AudioArtifactRow, artifact_id)
            cleared_cache = database_session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.artifact_id == artifact_id
                )
            )
            assert purged_artifact is not None
            assert purged_artifact.availability == "purged"
            assert cleared_cache is not None
            assert cleared_cache.state == "cleared"
