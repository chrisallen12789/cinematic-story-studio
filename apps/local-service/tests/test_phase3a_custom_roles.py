from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.casting import (
    generate_candidates,
    load_synthetic_catalog,
)
from cinematic_story_service.casting_repository import (
    _custom_role_evidence,
    _deterministic_machine_fingerprint,
)
from cinematic_story_service.models import ApprovedCastSnapshotRow
from cinematic_story_service.util import (
    parse_json,
    request_fingerprint,
    stable_id,
)
from tests.test_phase3a_casting import _evidence
from tests.test_phase3a_governance import (
    _apply_correction,
    _prepare_casting,
)


def _custom_role_payload(
    run: dict[str, Any],
    *,
    key: str,
    definition_id: str = "custom-role-door-chime",
    label: str = "Door chime",
) -> dict[str, Any]:
    return {
        "definitionId": definition_id,
        "label": label,
        "performanceRequirements": {
            "language": "en",
            "locales": ["en-US"],
            "agePresentationRange": None,
            "vocalPresentations": [],
            "preferredTextures": ["clear"],
            "speakingRateRange": None,
            "requiredExpressiveRange": ["precise"],
            "longFormRequired": False,
        },
        "reason": "Track an explicit content-free production role.",
        "expectedRunFingerprint": (
            run["outputFingerprint"] or run["inputFingerprint"]
        ),
        "expectedCatalogRevisionId": run["catalogRevisionId"],
        "expectedCatalogFingerprint": run["catalogFingerprint"],
        "expectedSnapshotId": run["prerequisites"]["analysisSnapshotId"],
        "expectedSnapshotRevision": run["prerequisites"][
            "analysisSnapshotRevision"
        ],
        "expectedSnapshotFingerprint": run["prerequisites"][
            "analysisSnapshotFingerprint"
        ],
        "expectedCorrectionSetFingerprint": run[
            "effectiveCorrectionSetFingerprint"
        ],
        "expectedCastingProfileFingerprint": run["profile"]["fingerprint"],
        "idempotencyKey": key,
    }


def test_custom_role_machine_identity_excludes_recording_time() -> None:
    definition_fingerprint = request_fingerprint(
        {
            "definitionId": "custom-role-deterministic",
            "label": "Deterministic custom role",
            "requirement": {
                "language": "en",
                "locales": ["en-US"],
            },
        }
    )
    requirements = {
        "language": "en",
        "locales": ["en-US"],
        "agePresentationRange": None,
        "vocalPresentations": [],
        "preferredTextures": ["clear"],
        "speakingRateRange": None,
        "requiredExpressiveRange": ["precise"],
        "longFormRequired": False,
    }
    common = {
        "project_id": "project-deterministic",
        "run_id": "run-deterministic",
        "definition_id": "custom-role-deterministic",
        "definition_fingerprint": definition_fingerprint,
        "label": "Deterministic custom role",
        "performance_requirements": requirements,
        "reason": "Prove stable machine evidence.",
        "analysis_run_id": "analysis-run-deterministic",
        "analysis_snapshot_id": "snapshot-deterministic",
        "analysis_snapshot_fingerprint": "1" * 64,
        "casting_run_input_fingerprint": "2" * 64,
        "catalog_fingerprint": "3" * 64,
        "casting_profile_fingerprint": "4" * 64,
    }
    first_role, first_input = _custom_role_evidence(
        **common,
        recorded_at="2026-01-01T00:00:00Z",
    )
    second_role, second_input = _custom_role_evidence(
        **common,
        recorded_at="2036-12-31T23:59:59Z",
    )
    assert first_role["provenance"]["recordedAt"] != (
        second_role["provenance"]["recordedAt"]
    )
    assert first_role["unresolvedMaterialExplicitlyRepresented"] is False
    assert first_role["roleId"] == second_role["roleId"]
    assert first_role["roleFingerprint"] == (
        second_role["roleFingerprint"]
    )
    assert first_input == second_input

    catalog = load_synthetic_catalog()
    first_candidates, _ = generate_candidates(
        roles=[first_role],
        catalog=catalog,
        input_fingerprint=first_input,
    )
    second_candidates, _ = generate_candidates(
        roles=[second_role],
        catalog=catalog,
        input_fingerprint=second_input,
    )
    assert [
        value["candidateId"] for value in first_candidates
    ] == [
        value["candidateId"] for value in second_candidates
    ]
    for first, second in zip(
        first_candidates,
        second_candidates,
        strict=True,
    ):
        first["provenance"]["recordedAt"] = (
            "2026-01-01T00:00:00Z"
        )
        second["provenance"]["recordedAt"] = (
            "2036-12-31T23:59:59Z"
        )
        assert _deterministic_machine_fingerprint(first) == (
            _deterministic_machine_fingerprint(second)
        )

    first_proposal = next(
        value
        for value in first_candidates
        if value["compatibilityStatus"]
        in {"eligible", "conditional"}
    )
    second_proposal = next(
        value
        for value in second_candidates
        if value["compatibilityStatus"]
        in {"eligible", "conditional"}
    )
    first_proposal_id = stable_id(
        "machine-cast-proposal",
        common["run_id"],
        first_role["roleId"],
        first_proposal["candidateId"],
    )
    second_proposal_id = stable_id(
        "machine-cast-proposal",
        common["run_id"],
        second_role["roleId"],
        second_proposal["candidateId"],
    )
    assert first_proposal_id == second_proposal_id
    assert request_fingerprint(
        {
            "proposalId": first_proposal_id,
            "candidateId": first_proposal["candidateId"],
            "inputFingerprint": first_input,
        }
    ) == request_fingerprint(
        {
            "proposalId": second_proposal_id,
            "candidateId": second_proposal["candidateId"],
            "inputFingerprint": second_input,
        }
    )


def test_custom_role_is_content_free_idempotent_and_persists_restart(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        project_id, run = _prepare_casting(
            client,
            auth_headers,
            key="custom-role-restart",
        )
        route = (
            f"/api/v1/projects/{project_id}/casting-runs/"
            f"{run['castingRunId']}/roles"
        )
        payload = _custom_role_payload(run, key="create-door-chime")
        unauthenticated = client.post(route, json=payload)
        assert unauthenticated.status_code == 401

        before_page = client.get(
            route,
            headers=auth_headers,
            params={**_evidence(run), "limit": 1},
        )
        assert before_page.status_code == 200, before_page.text
        stale_cursor = before_page.json()["nextCursor"]
        assert stale_cursor

        created = client.post(route, headers=auth_headers, json=payload)
        assert created.status_code == 200, created.text
        body = created.json()
        role = body["role"]
        updated_run = body["run"]
        assert role["roleType"] == "custom"
        assert role["phase2EntityId"] is None
        assert role["characterId"] is None
        assert role["dialogueLineCount"] == 0
        assert role["narrationSpanCount"] == 0
        assert role["approximateWordCount"] == 0
        assert set(role["range"].values()) == {None}
        assert role["provenance"]["origin"] == "human"
        assert role["provenance"]["sourceRevisionId"] == (
            "custom-role-door-chime"
        )
        assert role["provenance"]["reason"] == payload["reason"]
        assert role["warnings"] == [
            {
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
        ]
        assert updated_run["approvedCastSnapshot"]["snapshotId"] != (
            run["approvedCastSnapshot"]["snapshotId"]
        )
        assert all(review["state"] == "pending" for review in body["reviews"])
        stale_page = client.get(
            route,
            headers=auth_headers,
            params={
                **_evidence(updated_run),
                "limit": 1,
                "cursor": stale_cursor,
            },
        )
        assert stale_page.status_code == 400
        assert stale_page.json()["error"]["code"] == "INVALID_CURSOR"

        candidates = client.get(
            f"{route}/{role['roleId']}/candidates",
            headers=auth_headers,
            params={
                **_evidence(updated_run),
                "expectedRoleRevision": role["revision"],
                "limit": 12,
            },
        )
        assert candidates.status_code == 200, candidates.text
        assert 1 <= candidates.json()["total"] <= 12

        assignments = client.get(
            (
                f"/api/v1/projects/{project_id}/casting-runs/"
                f"{updated_run['castingRunId']}/assignments"
            ),
            headers=auth_headers,
            params={**_evidence(updated_run), "limit": 200},
        )
        assert assignments.status_code == 200, assignments.text
        proposal = next(
            item
            for item in assignments.json()["items"]
            if item["roleId"] == role["roleId"]
        )
        assert proposal["authority"] == "machine_proposal"
        assert proposal["effective"] is True

        replay = client.post(route, headers=auth_headers, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["role"]["roleId"] == role["roleId"]
        assert replay.json()["run"]["approvedCastSnapshot"] == (
            updated_run["approvedCastSnapshot"]
        )

        custom_snapshot = updated_run["approvedCastSnapshot"]
        after_label_run, _label_change, _ = _apply_correction(
            client,
            auth_headers,
            run=updated_run,
            role_id=role["roleId"],
            operation="change_role_label",
            key="rename-custom-role-for-snapshot-proof",
            corrected_value={
                "effectiveDisplayLabel": "Door chime revised",
            },
        )
        assert (
            after_label_run["approvedCastSnapshot"]["snapshotId"]
            != custom_snapshot["snapshotId"]
        )
        repository = app.state.casting
        with repository.database.session() as session:
            initial_row = session.get(
                ApprovedCastSnapshotRow,
                run["approvedCastSnapshot"]["snapshotId"],
            )
            custom_row = session.get(
                ApprovedCastSnapshotRow,
                custom_snapshot["snapshotId"],
            )
            assert initial_row is not None
            assert custom_row is not None
            assert repository._snapshot_wire(
                session,
                initial_row,
            ) == run["approvedCastSnapshot"]
            assert repository._snapshot_wire(
                session,
                custom_row,
            ) == custom_snapshot
            initial_manifest = parse_json(
                initial_row.manifest_json,
                {},
            )
            custom_manifest = parse_json(
                custom_row.manifest_json,
                {},
            )
            for manifest in (initial_manifest, custom_manifest):
                assert manifest["productionRoleEvidence"]
                assert manifest["candidateEvidence"]
                assert isinstance(manifest["assignmentEvidence"], list)
                assert isinstance(manifest["correctionEvidence"], list)
                assert isinstance(
                    manifest["unresolvedConflictEvidence"],
                    list,
                )
                assert manifest["counts"]["productionRoles"] == len(
                    manifest["productionRoleEvidence"]
                )
            assert initial_manifest != custom_manifest

        stale = client.post(
            route,
            headers=auth_headers,
            json={
                **payload,
                "definitionId": "custom-role-stale",
                "idempotencyKey": "create-stale-custom-role",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "CASTING_EVIDENCE_STALE"

    with TestClient(create_app(settings)) as restarted:
        restored_run_response = restarted.get(
            (
                f"/api/v1/projects/{project_id}/casting-runs/"
                f"{updated_run['castingRunId']}"
            ),
            headers=auth_headers,
        )
        assert restored_run_response.status_code == 200
        restored_run = restored_run_response.json()["run"]
        restored_roles = restarted.get(
            (
                f"/api/v1/projects/{project_id}/casting-runs/"
                f"{restored_run['castingRunId']}/roles"
            ),
            headers=auth_headers,
            params={**_evidence(restored_run), "limit": 200},
        )
        assert restored_roles.status_code == 200, restored_roles.text
        restored = next(
            item
            for item in restored_roles.json()["items"]
            if item["roleId"] == role["roleId"]
        )
        assert restored["provenance"]["sourceRevisionId"] == (
            "custom-role-door-chime"
        )


def test_custom_role_rejects_invalid_payload_and_definition_reuse(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    project_id, run = _prepare_casting(
        client,
        auth_headers,
        key="custom-role-validation",
    )
    route = (
        f"/api/v1/projects/{project_id}/casting-runs/"
        f"{run['castingRunId']}/roles"
    )
    payload = _custom_role_payload(run, key="create-custom-validation")
    invalid = client.post(
        route,
        headers=auth_headers,
        json={
            **payload,
            "definitionId": "invalid definition id",
        },
    )
    assert invalid.status_code == 422

    locale_unknown_payload = _custom_role_payload(
        run,
        key="create-custom-locale-unknown",
        definition_id="custom-role-locale-unknown",
        label="Locale-unknown signal",
    )
    locale_unknown = client.post(
        route,
        headers=auth_headers,
        json={
            **locale_unknown_payload,
            "performanceRequirements": {
                **locale_unknown_payload["performanceRequirements"],
                "locales": [],
            },
        },
    )
    assert locale_unknown.status_code == 200, locale_unknown.text
    run = locale_unknown.json()["run"]
    payload = _custom_role_payload(run, key="create-custom-validation")
    created = client.post(route, headers=auth_headers, json=payload)
    assert created.status_code == 200, created.text
    current = created.json()["run"]
    conflict = client.post(
        route,
        headers=auth_headers,
        json={
            **_custom_role_payload(
                current,
                key="reuse-custom-definition",
                label="Different label",
            ),
            "definitionId": payload["definitionId"],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == (
        "CASTING_CUSTOM_ROLE_DEFINITION_CONFLICT"
    )
