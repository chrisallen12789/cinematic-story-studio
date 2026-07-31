from __future__ import annotations

import copy
import time
from collections import Counter

from fastapi.testclient import TestClient

from cinematic_story_service.casting import (
    MAX_FINAL_CANDIDATES,
    MAX_PRE_REDUCTION_CANDIDATES,
    MAX_PRODUCTION_ROLES,
    MAX_VOICE_PROFILES,
    VoiceCatalog,
    generate_candidates,
    load_synthetic_catalog,
    validate_catalog,
)
from cinematic_story_service.util import request_fingerprint


def _validated_catalog(
    fixture: VoiceCatalog,
    *,
    catalog_revision_id: str,
    voices: list[dict[str, object]],
    rights: list[dict[str, object]],
) -> VoiceCatalog:
    payload = copy.deepcopy(fixture.to_wire())
    revision = payload["catalogRevision"]
    revision.update(
        {
            "catalogRevisionId": catalog_revision_id,
            "revision": 2,
            "semanticVersion": "1.0.1-scale",
            "voiceProfileIds": [
                str(value["voiceProfileId"])
                for value in voices
            ],
        }
    )
    for voice in voices:
        voice["catalogRevisionId"] = catalog_revision_id
    payload["voices"] = voices
    payload["rights"] = rights
    fingerprint_input = copy.deepcopy(payload)
    fingerprint_input.pop("fingerprint", None)
    fingerprint_input["catalogRevision"].pop(
        "catalogFingerprint",
        None,
    )
    fingerprint = request_fingerprint(fingerprint_input)
    payload["fingerprint"] = fingerprint
    revision["catalogFingerprint"] = fingerprint
    return validate_catalog(payload)


def _scale_catalog() -> VoiceCatalog:
    fixture = load_synthetic_catalog()
    voices: list[dict[str, object]] = []
    rights: list[dict[str, object]] = []
    for ordinal in range(MAX_VOICE_PROFILES):
        source_voice = copy.deepcopy(fixture.voices[ordinal % len(fixture.voices)])
        source_rights = copy.deepcopy(fixture.rights[ordinal % len(fixture.rights)])
        profile_id = f"synthetic-scale-voice-{ordinal:04d}"
        rights_id = f"synthetic-scale-rights-{ordinal:04d}"
        source_voice.update(
            {
                "voiceProfileId": profile_id,
                "providerVoiceId": f"scale-key-{ordinal:04d}",
                "rightsRecordId": rights_id,
            }
        )
        source_rights.update(
            {
                "rightsRecordId": rights_id,
                "voiceProfileId": profile_id,
            }
        )
        voices.append(source_voice)
        rights.append(source_rights)
    return _validated_catalog(
        fixture,
        catalog_revision_id="synthetic-scale-catalog-v1@1.0.1",
        voices=voices,
        rights=rights,
    )


def _scale_roles() -> list[dict[str, object]]:
    return [
        {
            "roleId": f"synthetic-scale-role-{ordinal:03d}",
            "roleType": "primary_narrator" if ordinal == 0 else "named_character",
            "languageRequirements": ["en"],
            "locale": "en-US",
            "performanceRequirements": {
                "language": "en",
                "locales": ["en-US"],
                "agePresentationRange": None,
                "vocalPresentations": [],
                "preferredTextures": [],
                "speakingRateRange": None,
                "requiredExpressiveRange": [],
                "longFormRequired": ordinal == 0,
            },
        }
        for ordinal in range(MAX_PRODUCTION_ROLES)
    ]


def test_maximum_scale_candidate_reduction_is_bounded_and_deterministic() -> None:
    catalog = _scale_catalog()
    roles = _scale_roles()
    input_fingerprint = request_fingerprint(
        {
            "catalogFingerprint": catalog.fingerprint,
            "roleIds": [role["roleId"] for role in roles],
            "profile": "governed-voice-casting-v1@1.0.0",
        }
    )

    started_at = time.perf_counter()
    candidates, conflicts = generate_candidates(
        roles=roles,
        catalog=catalog,
        input_fingerprint=input_fingerprint,
    )
    elapsed_seconds = time.perf_counter() - started_at

    assert len(catalog.voices) == MAX_VOICE_PROFILES == 5_000
    assert len(roles) == MAX_PRODUCTION_ROLES == 300
    assert len(roles) * MAX_PRE_REDUCTION_CANDIDATES == 15_000
    assert len(candidates) <= len(roles) * MAX_FINAL_CANDIDATES == 3_600
    assert elapsed_seconds < 30

    counts = Counter(str(candidate["roleId"]) for candidate in candidates)
    assert set(counts) == {str(role["roleId"]) for role in roles}
    assert all(1 <= count <= MAX_FINAL_CANDIDATES for count in counts.values())
    assert all(
        1 <= int(candidate["preReductionRank"]) <= MAX_PRE_REDUCTION_CANDIDATES
        for candidate in candidates
    )
    assert all(0 <= int(candidate["ordinal"]) < MAX_FINAL_CANDIDATES for candidate in candidates)
    assert conflicts
    assert all(conflict["metadataBased"] is True for conflict in conflicts)
    assert all(conflict["acousticSimilarityClaimed"] is False for conflict in conflicts)

    repeated_candidates, repeated_conflicts = generate_candidates(
        roles=roles,
        catalog=catalog,
        input_fingerprint=input_fingerprint,
    )
    assert request_fingerprint(repeated_candidates) == request_fingerprint(candidates)
    assert request_fingerprint(repeated_conflicts) == request_fingerprint(conflicts)


def test_maximum_validated_catalog_persists_and_paginates_through_api(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    catalog = _scale_catalog()
    created = client.post(
        "/api/v1/projects",
        headers={
            **auth_headers,
            "Idempotency-Key": "create-phase3a-scale-project",
        },
        json={"name": "Synthetic casting scale"},
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project"]["projectId"]
    client.app.state.casting.catalog = catalog

    cursor: str | None = None
    profile_ids: list[str] = []
    while True:
        response = client.get(
            f"/api/v1/projects/{project_id}/casting/catalog",
            headers=auth_headers,
            params={
                "limit": 200,
                **({"cursor": cursor} if cursor is not None else {}),
                "expectedCatalogRevisionId": catalog.revision_id,
                "expectedCatalogFingerprint": catalog.fingerprint,
            },
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == MAX_VOICE_PROFILES
        assert page["pageSize"] == len(page["items"])
        assert page["pageSize"] <= 200
        profile_ids.extend(
            str(value["voiceProfileId"])
            for value in page["items"]
        )
        cursor = page.get("nextCursor")
        if cursor is None:
            break

    assert len(profile_ids) == MAX_VOICE_PROFILES
    assert len(set(profile_ids)) == MAX_VOICE_PROFILES


def test_pre_reduction_searches_beyond_the_first_fifty_catalog_entries() -> None:
    fixture = load_synthetic_catalog()
    source_voice = next(
        value for value in fixture.voices if value["voiceProfileId"] == "synthetic-character-01"
    )
    source_rights = next(
        value for value in fixture.rights if value["voiceProfileId"] == "synthetic-character-01"
    )
    voices: list[dict[str, object]] = []
    rights: list[dict[str, object]] = []
    for ordinal in range(60):
        voice = copy.deepcopy(source_voice)
        rights_record = copy.deepcopy(source_rights)
        profile_id = f"aaa-incompatible-{ordinal:02d}"
        rights_id = f"rights-aaa-incompatible-{ordinal:02d}"
        voice.update(
            {
                "voiceProfileId": profile_id,
                "providerVoiceId": f"key-aaa-incompatible-{ordinal:02d}",
                "rightsRecordId": rights_id,
                "dialogueSuitability": "unsuitable",
            }
        )
        rights_record.update(
            {
                "rightsRecordId": rights_id,
                "voiceProfileId": profile_id,
            }
        )
        voices.append(voice)
        rights.append(rights_record)

    eligible_voice = copy.deepcopy(source_voice)
    eligible_rights = copy.deepcopy(source_rights)
    eligible_voice.update(
        {
            "voiceProfileId": "zzz-eligible-after-boundary",
            "providerVoiceId": "key-zzz-eligible-after-boundary",
            "rightsRecordId": "rights-zzz-eligible-after-boundary",
        }
    )
    eligible_rights.update(
        {
            "rightsRecordId": "rights-zzz-eligible-after-boundary",
            "voiceProfileId": "zzz-eligible-after-boundary",
        }
    )
    voices.append(eligible_voice)
    rights.append(eligible_rights)
    catalog = _validated_catalog(
        fixture,
        catalog_revision_id="synthetic-adversarial-catalog-v1@1.0.1",
        voices=voices,
        rights=rights,
    )
    role = {
        "roleId": "adversarial-role",
        "roleType": "named_character",
        "roleImportance": "supporting",
        "languageRequirements": ["en"],
        "performanceRequirements": {"longFormRequired": False},
    }

    candidates, _ = generate_candidates(
        roles=[role],
        catalog=catalog,
        input_fingerprint="e" * 64,
    )

    assert any(
        value["voiceProfileId"] == "zzz-eligible-after-boundary"
        and value["compatibilityStatus"] == "eligible"
        for value in candidates
    )
