from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.models import (
    AudioArtifactRow,
    AudioQualityRecordRow,
    AuditionClipRow,
    AuditionEvidenceInvalidationRow,
    AuditionReviewDecisionRow,
    AuditionReviewRecordRow,
)
from tests.conftest import wait_for_job
from tests.test_phase3b_atomic_publication import _prepare_generation, _queue_generation
from tests.test_phase3b_workflow import _approve_audition_review, _clips, _workspace


def _session_by_id(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    project_id: str,
    session_id: str,
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/projects/{project_id}/audition-sessions",
        headers=auth_headers,
        params={"limit": 200},
    )
    assert response.status_code == 200, response.text
    return next(
        value for value in response.json()["items"] if value["auditionSessionId"] == session_id
    )


def test_session_approved_clip_requires_latest_current_approval_after_restart(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    project_id = ""
    session_id = ""
    with TestClient(create_app(settings)) as client:
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-session-current-approved-clip",
        )
        session_id = audition_session["auditionSessionId"]
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session_id,
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=30.0,
        )
        assert terminal["state"] == "succeeded"
        clip_id = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=session_id,
        )[0]["auditionClipId"]
        approved = _approve_audition_review(
            client,
            auth_headers,
            project_id=project_id,
            gate_id="per_role_audition_review",
            role_id=audition_session["roleId"],
            key="phase3b-session-current-approved-clip-approve",
        )
        assert (
            _session_by_id(
                client,
                auth_headers,
                project_id=project_id,
                session_id=session_id,
            )["approvedClipId"]
            == clip_id
        )

        review = approved["review"]
        approval = approved["decision"]
        changed = client.post(
            (
                f"/api/v1/projects/{project_id}/audition-reviews/"
                f"per_role_audition_review/{review['reviewId']}/decisions"
            ),
            headers=auth_headers,
            json={
                "expectedReviewRevision": review["revision"],
                "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
                "decision": "request_changes",
                "rationale": "Request a new repository-owned synthetic performance.",
                "supersedesDecisionId": approval["decisionId"],
                "idempotencyKey": "phase3b-session-current-approved-clip-change",
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["decision"]["decision"] == "changes_requested"
        assert (
            _session_by_id(
                client,
                auth_headers,
                project_id=project_id,
                session_id=session_id,
            )["approvedClipId"]
            is None
        )

    with TestClient(create_app(settings)) as restarted:
        assert (
            _session_by_id(
                restarted,
                auth_headers,
                project_id=project_id,
                session_id=session_id,
            )["approvedClipId"]
            is None
        )


def test_per_role_approval_rejects_changed_audio_and_persists_invalidation(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-review-audio-integrity",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=30.0,
        )
        assert terminal["state"] == "succeeded"
        clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )[0]
        workspace = _workspace(client, auth_headers, project_id)
        review = next(
            value
            for value in workspace["reviews"]
            if value["gateId"] == "per_role_audition_review"
            and value["roleId"] == audition_session["roleId"]
        )
        assert review["state"] == "pending"
        assert isinstance(review["warningCodes"], list)

        with client.app.state.database.session() as database_session:
            artifact = database_session.get(
                AudioArtifactRow,
                clip["audioArtifact"]["audioArtifactId"],
            )
            assert artifact is not None
            audio_path = settings.data_dir / Path(artifact.storage_key)
        audio_path.write_bytes(b"repository-owned corrupted synthetic WAV")

        rejected = client.post(
            (
                f"/api/v1/projects/{project_id}/audition-reviews/"
                f"per_role_audition_review/{review['reviewId']}/decisions"
            ),
            headers=auth_headers,
            json={
                "expectedReviewRevision": review["revision"],
                "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
                "decision": "approve",
                "rationale": "Approve only unchanged repository-owned synthetic audition bytes.",
                "supersedesDecisionId": None,
                "idempotencyKey": "phase3b-reject-changed-audio-review",
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "AUDITION_AUDIO_CHANGED"

        with client.app.state.database.session() as database_session:
            approved_count = int(
                database_session.scalar(
                    select(func.count())
                    .select_from(AuditionReviewDecisionRow)
                    .where(
                        AuditionReviewDecisionRow.review_record_id == review["reviewId"],
                        AuditionReviewDecisionRow.decision == "approved",
                    )
                )
                or 0
            )
            invalidations = list(
                database_session.scalars(
                    select(AuditionEvidenceInvalidationRow).where(
                        AuditionEvidenceInvalidationRow.project_id == project_id,
                        AuditionEvidenceInvalidationRow.clip_id == clip["auditionClipId"],
                        AuditionEvidenceInvalidationRow.reason_code
                        == "AUDITION_AUDIO_INTEGRITY_CHANGED",
                    )
                )
            )
        assert approved_count == 0
        assert len(invalidations) == 1


def test_review_clip_binding_mismatch_persists_targeted_invalidation(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-review-clip-binding",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=30.0,
        )
        assert terminal["state"] == "succeeded"
        clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )[0]
        review = next(
            value
            for value in _workspace(client, auth_headers, project_id)["reviews"]
            if value["gateId"] == "per_role_audition_review"
            and value["roleId"] == audition_session["roleId"]
        )

        with client.app.state.database.immediate_session() as database_session:
            review_row = database_session.get(AuditionReviewRecordRow, review["reviewId"])
            assert review_row is not None
            review_row.session_id = None

        rejected = client.post(
            (
                f"/api/v1/projects/{project_id}/audition-reviews/"
                f"per_role_audition_review/{review['reviewId']}/decisions"
            ),
            headers=auth_headers,
            json={
                "expectedReviewRevision": review["revision"],
                "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
                "decision": "approve",
                "rationale": "Approve only the exact bound repository-owned clip.",
                "supersedesDecisionId": None,
                "idempotencyKey": "phase3b-reject-changed-review-clip-binding",
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "AUDITION_REVIEW_CHANGED"

        with client.app.state.database.session() as database_session:
            invalidations = list(
                database_session.scalars(
                    select(AuditionEvidenceInvalidationRow).where(
                        AuditionEvidenceInvalidationRow.project_id == project_id,
                        AuditionEvidenceInvalidationRow.clip_id == clip["auditionClipId"],
                        AuditionEvidenceInvalidationRow.source_kind == "review_clip_binding",
                        AuditionEvidenceInvalidationRow.reason_code
                        == "AUDITION_REVIEW_CLIP_CHANGED",
                    )
                )
            )
            decisions = list(
                database_session.scalars(
                    select(AuditionReviewDecisionRow).where(
                        AuditionReviewDecisionRow.review_record_id == review["reviewId"],
                        AuditionReviewDecisionRow.decision == "approved",
                    )
                )
            )
        assert len(invalidations) == 1
        assert decisions == []


def test_quality_measurement_count_and_policy_tampering_fails_closed(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-review-quality-evidence",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=30.0,
        )
        assert terminal["state"] == "succeeded"
        clip = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )[0]
        review = next(
            value
            for value in _workspace(client, auth_headers, project_id)["reviews"]
            if value["gateId"] == "per_role_audition_review"
            and value["roleId"] == audition_session["roleId"]
        )
        quality_id = clip["audioQuality"]["qualityRecordId"]
        clip_id = clip["auditionClipId"]

        mutations: tuple[tuple[str, Callable[[Any], Any]], ...] = (
            ("warning_count", lambda value: value + 1),
            ("blocking_finding_count", lambda value: value + 1),
            ("clipped_sample_count", lambda value: value + 1),
            ("peak_millidbfs", lambda value: value - 1 if value == 0 else value + 1),
            ("rms_millidbfs", lambda value: value - 1 if value == 0 else value + 1),
            (
                "silence_ratio_ppm",
                lambda value: value - 1 if value == 1_000_000 else value + 1,
            ),
            (
                "policy_fingerprint",
                lambda value: "0" * 64 if value != "0" * 64 else "1" * 64,
            ),
        )
        repository = client.app.state.auditions
        for field, mutate in mutations:
            with client.app.state.database.immediate_session() as database_session:
                quality = database_session.get(AudioQualityRecordRow, quality_id)
                assert quality is not None
                original = getattr(quality, field)
                setattr(quality, field, mutate(original))
            with client.app.state.database.session() as database_session:
                persisted_clip = database_session.get(AuditionClipRow, clip_id)
                assert persisted_clip is not None
                integrity_ok, _previous, _current = repository._clip_integrity_fingerprints(
                    database_session,
                    persisted_clip,
                )
                assert not integrity_ok, field
            with client.app.state.database.immediate_session() as database_session:
                quality = database_session.get(AudioQualityRecordRow, quality_id)
                assert quality is not None
                setattr(quality, field, original)

        with client.app.state.database.immediate_session() as database_session:
            quality = database_session.get(AudioQualityRecordRow, quality_id)
            assert quality is not None
            quality.warning_count += 1

        rejected = client.post(
            (
                f"/api/v1/projects/{project_id}/audition-reviews/"
                f"per_role_audition_review/{review['reviewId']}/decisions"
            ),
            headers=auth_headers,
            json={
                "expectedReviewRevision": review["revision"],
                "expectedEvidenceFingerprint": review["evidence"]["evidenceFingerprint"],
                "decision": "approve",
                "rationale": "Approve only exact persisted repository-owned QC evidence.",
                "supersedesDecisionId": None,
                "idempotencyKey": "phase3b-reject-changed-quality-evidence",
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "AUDITION_AUDIO_CHANGED"

        with client.app.state.database.session() as database_session:
            invalidations = list(
                database_session.scalars(
                    select(AuditionEvidenceInvalidationRow).where(
                        AuditionEvidenceInvalidationRow.project_id == project_id,
                        AuditionEvidenceInvalidationRow.clip_id == clip_id,
                        AuditionEvidenceInvalidationRow.source_kind == "audio_integrity",
                        AuditionEvidenceInvalidationRow.reason_code
                        == "AUDITION_AUDIO_INTEGRITY_CHANGED",
                    )
                )
            )
        assert len(invalidations) == 1
