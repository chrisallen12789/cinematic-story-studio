from __future__ import annotations

from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.audition_repository import AuditionRepository
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.models import (
    AuditionSessionRow,
    PronunciationEntryRow,
    SpeechProviderRequestRow,
    SpeechRuntimeInstanceRow,
)
from tests.test_phase3b_workflow import (
    _activate_fixture_model,
    _add_approved_pronunciation,
    _clips,
    _create_session_and_script,
    _establish_approved_cast,
    _generate,
    _workspace,
)


def test_pronunciation_record_lineage_cycle_fails_closed() -> None:
    first = PronunciationEntryRow(
        id="record-1",
        project_id="project-1",
        entry_id="entry-1",
        supersedes_entry_record_id="record-2",
    )
    second = PronunciationEntryRow(
        id="record-2",
        project_id="project-1",
        entry_id="entry-2",
        supersedes_entry_record_id="record-1",
    )
    with Session() as session, pytest.raises(ServiceError) as error:
        AuditionRepository._pronunciation_record_lineage(
            session,
            first,
            direction="predecessors",
            records_by_id={first.id: first, second.id: second},
        )
    assert error.value.code == "PRONUNCIATION_LINEAGE_INVALID"


def test_pronunciation_decision_walks_full_external_supersession_lineage(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, _casting_run = _establish_approved_cast(
            client,
            auth_headers,
            key="phase3b-pronunciation-lineage",
        )
        _activate_fixture_model(
            client,
            auth_headers,
            project_id=project_id,
            key="phase3b-pronunciation-lineage",
        )
        original = _add_approved_pronunciation(
            client,
            auth_headers,
            project_id=project_id,
            written_form="Aster",
            pronunciation="AS-ter",
            key="phase3b-pronunciation-lineage-original",
        )["entry"]
        role_id = _workspace(client, auth_headers, project_id)["roles"]["items"][0]["roleId"]
        audition_session, _script, generation_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Aster carries the repository-owned lineage signal.",
            key="phase3b-pronunciation-lineage",
        )
        queued, terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        assert terminal["state"] == "succeeded", terminal
        public_runtime_profile_id = generation_request["evidence"]["runtimeProfileId"]
        assert queued["providerRequest"]["runtimeProfileId"] == public_runtime_profile_id
        runtime_instance = _workspace(client, auth_headers, project_id)["runtimeInstances"][0]
        assert runtime_instance["runtimeProfileId"] == public_runtime_profile_id
        original_clip_id = _clips(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
        )[0]["auditionClipId"]

        dictionary = _workspace(client, auth_headers, project_id)["currentDictionary"]
        created = client.post(
            f"/api/v1/projects/{project_id}/pronunciations/entries",
            headers=auth_headers,
            json={
                "expectedDictionaryRevision": dictionary["revision"],
                "expectedDictionaryFingerprint": dictionary["dictionaryFingerprint"],
                "writtenForm": "Aster",
                "language": "en",
                "locale": "en-US",
                "scope": "project",
                "scopeId": None,
                "representation": "provider_neutral",
                "pronunciation": "AS-tur",
                "ipa": None,
                "providerId": None,
                "providerCompiledValue": None,
                "caseSensitive": False,
                "matchRule": "whole_word",
                "priority": 10,
                "reason": "Supersede the original repository-owned pronunciation.",
                "supersedesEntryId": original["entryId"],
                "idempotencyKey": "phase3b-pronunciation-lineage-replacement-create",
            },
        )
        assert created.status_code == 200, created.text
        pending = created.json()
        replacement = pending["entry"]
        assert replacement["supersedesEntryId"] == original["entryId"]

        decision_request = {
            "decision": "approve",
            "expectedEntryRevision": replacement["revision"],
            "expectedEntryFingerprint": replacement["entryFingerprint"],
            "expectedDictionaryRevision": pending["dictionary"]["revision"],
            "expectedDictionaryFingerprint": pending["dictionary"]["dictionaryFingerprint"],
            "rationale": "Approve the replacement lineage evidence.",
            "idempotencyKey": "phase3b-pronunciation-lineage-replacement-approve",
        }
        decision_route = (
            f"/api/v1/projects/{project_id}/pronunciations/entries/"
            f"{replacement['entryId']}/decisions"
        )
        decided = client.post(
            decision_route,
            headers=auth_headers,
            json=decision_request,
        )
        assert decided.status_code == 200, decided.text
        decision = decided.json()
        approved_replacement = decision["entry"]
        assert approved_replacement["supersedesEntryId"] == original["entryId"]
        assert approved_replacement["supersedesEntryId"] != replacement["entryId"]
        assert decision["invalidatedClipIds"] == [original_clip_id]
        assert decision["invalidatedClipCount"] == 1
        assert decision["invalidatedClipIdsTruncated"] is False
        assert decision["preservedClipCount"] == 0
        assert decision["preservedClipIds"] == []
        assert decision["preservedClipIdsTruncated"] is False

        replacement_session, _replacement_script, replacement_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Aster carries the replacement pronunciation signal.",
            key="phase3b-pronunciation-lineage-after-decision",
        )
        _replacement_queued, replacement_terminal = _generate(
            client,
            auth_headers,
            project_id=project_id,
            session_id=replacement_session["auditionSessionId"],
            generation_request=replacement_request,
        )
        assert replacement_terminal["state"] == "succeeded", replacement_terminal
        assert len(_clips(client, auth_headers, project_id=project_id)) == 2

        replayed = client.post(
            decision_route,
            headers=auth_headers,
            json=decision_request,
        )
        assert replayed.status_code == 200, replayed.text
        replay_value = replayed.json()
        replay_value.pop("correlationId")
        decision.pop("correlationId")
        assert replay_value == decision

        history = client.get(
            f"/api/v1/projects/{project_id}/pronunciations/entries",
            headers=auth_headers,
            params={"limit": 20},
        )
        assert history.status_code == 200, history.text
        entries = history.json()["items"]
        assert len(entries) == 4
        assert all(
            entry["supersedesEntryId"] != entry["entryId"]
            and entry["supersededByEntryId"] != entry["entryId"]
            for entry in entries
        )
        original_history = [entry for entry in entries if entry["entryId"] == original["entryId"]]
        assert {entry["supersededByEntryId"] for entry in original_history} == {
            replacement["entryId"]
        }

        repository = cast(AuditionRepository, client.app.state.auditions)
        with cast(Session, client.app.state.database.sessions()) as database_session:
            persisted_session = database_session.get(
                AuditionSessionRow,
                audition_session["auditionSessionId"],
            )
            assert persisted_session is not None
            effective = repository._effective_pronunciation_rows(
                database_session,
                persisted_session,
            )
            provider_request = database_session.get(
                SpeechProviderRequestRow,
                queued["providerRequest"]["providerRequestId"],
            )
            persisted_runtime = database_session.scalar(
                select(SpeechRuntimeInstanceRow).where(
                    SpeechRuntimeInstanceRow.id == runtime_instance["runtimeInstanceId"]
                )
            )
            assert provider_request is not None
            assert persisted_runtime is not None
            assert provider_request.runtime_profile_id == persisted_runtime.runtime_profile_id
            assert provider_request.runtime_profile_id != public_runtime_profile_id
        assert [entry.entry_id for entry in effective] == [replacement["entryId"]]
