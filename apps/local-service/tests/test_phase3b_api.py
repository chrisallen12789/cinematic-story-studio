from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.app import _reconcile_model_staging
from cinematic_story_service.model_packages import (
    MAX_MANAGED_MODEL_DIRECTORY_ENTRIES,
    ModelPackageError,
)
from cinematic_story_service.models import (
    AuditionReviewDecisionRow,
    AuditionReviewRecordRow,
)
from cinematic_story_service.util import canonical_json, sha256_text


def _create_project(
    client: TestClient,
    auth_headers: dict[str, str],
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/projects",
        headers={**auth_headers, "Idempotency-Key": "phase3b-api-project"},
        json={"name": "Synthetic Phase 3B API"},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["project"])


def _append_aggregate_review_decision(
    database_session: Any,
    *,
    project_id: str,
    revision: int,
    decided_at: str,
    supersedes_decision_id: str | None,
) -> tuple[str, str]:
    review_id = str(uuid4())
    decision_id = str(uuid4())
    evidence_fingerprint = sha256_text(f"{project_id}:narrator-review-evidence:{revision}")
    database_session.add(
        AuditionReviewRecordRow(
            id=review_id,
            project_id=project_id,
            gate_id="narrator_audition_review",
            scope_key="aggregate:narrator",
            subject_type="narrator_scope",
            revision=revision,
            session_id=None,
            clip_id=None,
            role_id=None,
            pronunciation_dictionary_record_id=None,
            eligible=True,
            evidence_json=canonical_json({"evidenceFingerprint": evidence_fingerprint}),
            evidence_fingerprint=evidence_fingerprint,
            required_decision_ids_json="[]",
            blockers_json="[]",
            warnings_json="[]",
            provenance_json=canonical_json(
                {
                    "origin": "application",
                    "producerId": "phase3b-api-test",
                    "producerVersion": "1.0.0",
                    "recordedAt": decided_at,
                }
            ),
            created_at=decided_at,
        )
    )
    database_session.flush()
    database_session.add(
        AuditionReviewDecisionRow(
            id=decision_id,
            project_id=project_id,
            review_record_id=review_id,
            gate_id="narrator_audition_review",
            scope_key="aggregate:narrator",
            revision=revision,
            decision="approved" if revision % 2 else "changes_requested",
            evidence_fingerprint=evidence_fingerprint,
            actor_classification="human",
            actor_id="local_user",
            warning_acknowledgements_json="[]",
            rationale=f"Repository-owned historical decision {revision}.",
            supersedes_decision_id=supersedes_decision_id,
            idempotency_key=f"{project_id}-history-{revision}",
            provenance_json=canonical_json(
                {
                    "origin": "human",
                    "producerId": "phase3b-api-test",
                    "producerVersion": "1.0.0",
                    "recordedAt": decided_at,
                }
            ),
            decided_at=decided_at,
            created_at=decided_at,
        )
    )
    database_session.flush()
    return review_id, decision_id


def test_audition_review_decision_history_is_scoped_stable_and_cursor_bounded(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    first_project = _create_project(client, auth_headers)
    second_response = client.post(
        "/api/v1/projects",
        headers={
            **auth_headers,
            "Idempotency-Key": "phase3b-history-second-project",
        },
        json={"name": "Synthetic Phase 3B History Isolation"},
    )
    assert second_response.status_code == 200, second_response.text
    second_project = second_response.json()["project"]
    database = client.app.state.database
    with database.session() as database_session:
        first_review_id, first_decision_id = _append_aggregate_review_decision(
            database_session,
            project_id=first_project["projectId"],
            revision=1,
            decided_at="2026-07-31T10:00:00.001Z",
            supersedes_decision_id=None,
        )
        second_review_id, second_decision_id = _append_aggregate_review_decision(
            database_session,
            project_id=first_project["projectId"],
            revision=2,
            decided_at="2026-07-31T10:00:00.002Z",
            supersedes_decision_id=first_decision_id,
        )
        _other_review_id, other_decision_id = _append_aggregate_review_decision(
            database_session,
            project_id=second_project["projectId"],
            revision=1,
            decided_at="2026-07-31T10:00:00.003Z",
            supersedes_decision_id=None,
        )

    route = f"/api/v1/projects/{first_project['projectId']}/audition-review-decisions"
    assert client.get(route, params={"gateId": "narrator_audition_review"}).status_code == 401

    first_page_response = client.get(
        route,
        headers=auth_headers,
        params={"gateId": "narrator_audition_review", "limit": 1},
    )
    assert first_page_response.status_code == 200, first_page_response.text
    first_page = first_page_response.json()
    assert first_page["projectId"] == first_project["projectId"]
    assert first_page["gateId"] == "narrator_audition_review"
    assert first_page["roleId"] is None
    assert first_page["pageSize"] == 1
    assert first_page["total"] == 2
    assert first_page["items"][0]["decisionId"] == second_decision_id
    assert first_page["items"][0]["reviewId"] == second_review_id
    assert "scopeKey" not in first_page["items"][0]
    cursor = first_page["nextCursor"]
    assert isinstance(cursor, str) and len(cursor) <= 512

    second_page_response = client.get(
        route,
        headers=auth_headers,
        params={
            "gateId": "narrator_audition_review",
            "cursor": cursor,
            "limit": 1,
        },
    )
    assert second_page_response.status_code == 200, second_page_response.text
    second_page = second_page_response.json()
    assert second_page["items"][0]["decisionId"] == first_decision_id
    assert second_page["items"][0]["reviewId"] == first_review_id
    assert "nextCursor" not in second_page

    tamper_index = len(cursor) // 2
    tampered_cursor = (
        cursor[:tamper_index]
        + ("A" if cursor[tamper_index] != "A" else "B")
        + cursor[tamper_index + 1 :]
    )
    tampered = client.get(
        route,
        headers=auth_headers,
        params={
            "gateId": "narrator_audition_review",
            "cursor": tampered_cursor,
            "limit": 1,
        },
    )
    assert tampered.status_code == 400, tampered.text
    assert tampered.json()["error"]["code"] == "INVALID_CURSOR"

    wrong_scope = client.get(
        route,
        headers=auth_headers,
        params={
            "gateId": "character_audition_review",
            "cursor": cursor,
            "limit": 1,
        },
    )
    assert wrong_scope.status_code == 400, wrong_scope.text
    assert wrong_scope.json()["error"]["code"] == "INVALID_CURSOR"

    second_project_route = (
        f"/api/v1/projects/{second_project['projectId']}/audition-review-decisions"
    )
    isolated = client.get(
        second_project_route,
        headers=auth_headers,
        params={"gateId": "narrator_audition_review", "limit": 200},
    )
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["total"] == 1
    assert isolated.json()["items"][0]["decisionId"] == other_decision_id
    cross_project_cursor = client.get(
        second_project_route,
        headers=auth_headers,
        params={
            "gateId": "narrator_audition_review",
            "cursor": cursor,
            "limit": 1,
        },
    )
    assert cross_project_cursor.status_code == 400
    assert cross_project_cursor.json()["error"]["code"] == "INVALID_CURSOR"

    with database.session() as database_session:
        _third_review_id, _third_decision_id = _append_aggregate_review_decision(
            database_session,
            project_id=first_project["projectId"],
            revision=3,
            decided_at="2026-07-31T10:00:00.004Z",
            supersedes_decision_id=second_decision_id,
        )
    stale = client.get(
        route,
        headers=auth_headers,
        params={
            "gateId": "narrator_audition_review",
            "cursor": cursor,
            "limit": 1,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "AUDITION_REVIEW_HISTORY_CURSOR_STALE"

    missing_role = client.get(
        route,
        headers=auth_headers,
        params={"gateId": "per_role_audition_review"},
    )
    assert missing_role.status_code == 422
    assert missing_role.json()["error"]["code"] == ("AUDITION_REVIEW_HISTORY_SCOPE_INVALID")
    forbidden_role = client.get(
        route,
        headers=auth_headers,
        params={"gateId": "narrator_audition_review", "roleId": "role-1"},
    )
    assert forbidden_role.status_code == 422
    assert forbidden_role.json()["error"]["code"] == ("AUDITION_REVIEW_HISTORY_SCOPE_INVALID")
    oversized_page = client.get(
        route,
        headers=auth_headers,
        params={"gateId": "narrator_audition_review", "limit": 201},
    )
    assert oversized_page.status_code == 422
    assert oversized_page.json()["error"]["code"] == "INVALID_REQUEST"


def test_phase3b_read_routes_are_authenticated_bounded_and_side_effect_free(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project = _create_project(client, auth_headers)
    project_id = project["projectId"]
    workspace_path = f"/api/v1/projects/{project_id}/auditions/workspace"

    unauthenticated = client.get(workspace_path)
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    before_sessions = client.get(
        f"/api/v1/projects/{project_id}/audition-sessions?limit=1",
        headers=auth_headers,
    )
    assert before_sessions.status_code == 200, before_sessions.text
    assert before_sessions.json()["items"] == []
    assert before_sessions.json()["total"] == 0

    workspace = client.get(workspace_path, headers=auth_headers)
    assert workspace.status_code == 200, workspace.text
    snapshot = workspace.json()["workspace"]
    assert snapshot["projectId"] == project_id
    assert snapshot["contractVersion"] == "1.0.0"
    assert snapshot["currentDictionary"]["revision"] == 1
    assert snapshot["roles"] == {"items": [], "pageSize": 0, "total": 0}
    assert snapshot["runtimeInstances"] == []

    after_sessions = client.get(
        f"/api/v1/projects/{project_id}/audition-sessions?limit=1",
        headers=auth_headers,
    )
    assert after_sessions.status_code == 200, after_sessions.text
    assert after_sessions.json()["items"] == []
    assert after_sessions.json()["total"] == 0

    dictionary = snapshot["currentDictionary"]
    pronunciations = client.get(
        f"/api/v1/projects/{project_id}/pronunciations/entries",
        headers=auth_headers,
        params={
            "expectedDictionaryRevision": dictionary["revision"],
            "expectedDictionaryFingerprint": dictionary["dictionaryFingerprint"],
            "limit": 1,
        },
    )
    assert pronunciations.status_code == 200, pronunciations.text
    assert pronunciations.json()["dictionary"] == dictionary
    assert pronunciations.json()["items"] == []
    assert pronunciations.json()["pageSize"] == 0
    assert pronunciations.json()["total"] == 0

    packages = client.get(
        f"/api/v1/projects/{project_id}/speech/model-packages?limit=1",
        headers=auth_headers,
    )
    assert packages.status_code == 200, packages.text
    package_page = packages.json()
    assert package_page["projectId"] == project_id
    assert package_page["pageSize"] == 1
    assert package_page["total"] == 2
    assert isinstance(package_page["nextCursor"], str)
    assert len(package_page["items"]) == 1


def test_phase3b_model_actions_enforce_path_identity_and_idempotency(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project = _create_project(client, auth_headers)
    project_id = project["projectId"]
    packages = client.get(
        f"/api/v1/projects/{project_id}/speech/model-packages?limit=200",
        headers=auth_headers,
    )
    assert packages.status_code == 200, packages.text
    fixture = next(
        item
        for item in packages.json()["items"]
        if item["manifest"]["providerId"] == "deterministic-pcm-wav-fixture"
    )
    package_id = fixture["manifest"]["modelPackageId"]
    manifest_fingerprint = fixture["manifest"]["manifestFingerprint"]
    route = f"/api/v1/projects/{project_id}/speech/model-packages/{package_id}/actions"
    payload = {
        "modelPackageId": package_id,
        "expectedManifestFingerprint": manifest_fingerprint,
        "expectedInstallationRevision": None,
        "action": "verify",
        "reason": "Verify the bounded synthetic fixture.",
        "idempotencyKey": "phase3b-fixture-verify",
    }

    mismatch = client.post(
        route,
        headers=auth_headers,
        json={**payload, "modelPackageId": "different-package"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "MODEL_PACKAGE_ID_MISMATCH"

    verified = client.post(route, headers=auth_headers, json=payload)
    assert verified.status_code == 200, verified.text
    assert verified.json()["installation"]["status"] == "installed"
    assert verified.json()["verification"]["status"] == "verified"

    replay = client.post(route, headers=auth_headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["installation"] == verified.json()["installation"]
    assert replay.json()["verification"] == verified.json()["verification"]


def test_real_model_upload_is_bounded_private_and_fails_closed(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: ServiceSettings,
) -> None:
    project = _create_project(client, auth_headers)
    project_id = project["projectId"]
    packages = client.get(
        f"/api/v1/projects/{project_id}/speech/model-packages?limit=200",
        headers=auth_headers,
    )
    assert packages.status_code == 200, packages.text
    real_package = next(
        item
        for item in packages.json()["items"]
        if item["manifest"]["providerId"] == "kokoro-local-onnx"
    )["manifest"]

    rejected = client.post(
        (
            f"/api/v1/projects/{project_id}/speech/model-packages/"
            f"{real_package['modelPackageId']}/install"
        ),
        headers=auth_headers,
        data={
            "expectedManifestFingerprint": real_package["manifestFingerprint"],
            "acknowledgeRestrictedLocalUse": "true",
            "reason": "Exercise a synthetic invalid local archive.",
            "idempotencyKey": "phase3b-invalid-real-package",
        },
        files={"file": ("synthetic-invalid.zip", b"not a ZIP", "application/zip")},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "MODEL_ARCHIVE_INVALID"
    assert "model-staging" not in rejected.text
    assert list((settings.data_dir / "model-staging").glob("*.zip")) == []


def test_phase3b_mutations_and_audio_reads_fail_closed(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project = _create_project(client, auth_headers)
    project_id = project["projectId"]

    oversized = client.post(
        f"/api/v1/projects/{project_id}/pronunciations/entries",
        headers=auth_headers,
        json={"writtenForm": "x" * (65 * 1024)},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"

    missing_audio = client.get(
        f"/api/v1/projects/{project_id}/audition-clips/missing/audio",
        headers=auth_headers,
        params={
            "auditionSessionId": "missing-session",
            "audioArtifactId": "missing-artifact",
            "expectedClipRevision": 1,
            "expectedClipFingerprint": "a" * 64,
            "expectedArtifactSha256": "b" * 64,
            "byteSize": 45,
        },
    )
    assert missing_audio.status_code == 404
    assert missing_audio.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    cleared = client.post(
        f"/api/v1/projects/{project_id}/audition-cache/clear",
        headers=auth_headers,
        json={
            "expectedProjectRevision": project["revision"],
            "reason": "Confirm the empty private cache boundary.",
            "idempotencyKey": "phase3b-empty-cache-clear",
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["projectId"] == project_id
    assert cleared.json()["clearedRecordCount"] == 0
    assert cleared.json()["alreadyClearedRecordCount"] == 0
    assert cleared.json()["projectRevision"] == project["revision"]


def test_model_upload_startup_reconciliation_removes_only_owned_archive(
    settings: ServiceSettings,
) -> None:
    staging = settings.data_dir / "model-staging"
    staging.mkdir(parents=True)
    owned = staging / "00000000-0000-4000-8000-000000000000.zip"
    unrelated = staging / "keep.txt"
    owned.write_bytes(b"partial synthetic upload")
    unrelated.write_bytes(b"not an owned archive name")

    application = create_app(settings)
    with TestClient(application):
        assert not owned.exists()
        assert unrelated.read_bytes() == b"not an owned archive name"


def test_model_upload_startup_reconciliation_fails_before_mutation_when_oversized(
    settings: ServiceSettings,
) -> None:
    staging = settings.data_dir / "model-staging"
    staging.mkdir(parents=True)
    owned = staging / "00000000-0000-4000-8000-000000000000.zip"
    owned.write_bytes(b"partial synthetic upload")
    sentinels = [
        staging / f"unrelated-{index:03d}.txt"
        for index in range(MAX_MANAGED_MODEL_DIRECTORY_ENTRIES)
    ]
    for sentinel in sentinels:
        sentinel.write_bytes(b"unrelated sentinel")

    with pytest.raises(ModelPackageError) as entry_limit_error:
        _reconcile_model_staging(staging)

    assert entry_limit_error.value.code == "MODEL_PACKAGE_ENTRY_LIMIT"
    assert owned.read_bytes() == b"partial synthetic upload"
    assert all(sentinel.read_bytes() == b"unrelated sentinel" for sentinel in sentinels)
