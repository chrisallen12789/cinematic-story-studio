from __future__ import annotations

import pytest
from pydantic import ValidationError

from cinematic_story_service.schemas import (
    CreateAuditionScriptRequest,
    CreateAuditionSessionRequest,
    CreatePronunciationEntryRequest,
    DecideAuditionReviewRequest,
    GenerateAuditionRequest,
    ListAuditionReviewDecisionsQuery,
    ModelInstallationOperationRequest,
)

_DIGEST = "a" * 64


def _pronunciation_payload() -> dict[str, object]:
    return {
        "expectedDictionaryRevision": 1,
        "expectedDictionaryFingerprint": _DIGEST,
        "writtenForm": "Aster",
        "language": "en",
        "locale": "en-US",
        "scope": "character_role",
        "scopeId": "role-1",
        "representation": "ipa",
        "pronunciation": "AST-er",
        "ipa": "\u02c8\u00e6st\u025a",
        "providerId": None,
        "providerCompiledValue": None,
        "caseSensitive": False,
        "matchRule": "whole_word",
        "priority": 10,
        "reason": "Synthetic fixture correction",
        "supersedesEntryId": None,
        "idempotencyKey": "pronunciation-1",
    }


def _evidence() -> dict[str, object]:
    return {
        "projectId": "project-1",
        "sourceDocumentId": "source-1",
        "sourceRevision": 1,
        "extractionId": "extraction-1",
        "extractionRevision": 1,
        "extractedTextSha256": _DIGEST,
        "phase2RunId": "analysis-1",
        "phase2SnapshotId": "analysis-snapshot-1",
        "phase2SnapshotRevision": 1,
        "phase2SnapshotFingerprint": _DIGEST,
        "phase2CorrectionSetFingerprint": _DIGEST,
        "castingRunId": "casting-1",
        "approvedCastSnapshotId": "cast-snapshot-1",
        "approvedCastSnapshotRevision": 1,
        "approvedCastSnapshotFingerprint": _DIGEST,
        "castAssignmentId": "assignment-1",
        "castAssignmentRevision": 1,
        "voiceProfileId": "voice-1",
        "voiceProfileVersion": "1.0.0",
        "voiceRuntimeBindingId": "voice-runtime-binding-1",
        "voiceRuntimeBindingFingerprint": _DIGEST,
        "providerVoiceId": "fixture-narrator-01",
        "providerId": "deterministic-pcm-wav-fixture",
        "providerVersion": "1.0.0",
        "modelId": "deterministic-square-wave",
        "modelVersion": "1.0.0",
        "catalogRevisionId": "catalog-1",
        "catalogFingerprint": _DIGEST,
        "rightsRecordId": "rights-1",
        "rightsRecordRevision": 1,
        "rightsRecordFingerprint": _DIGEST,
        "pronunciationDictionaryId": "dictionary-1",
        "pronunciationDictionaryRevision": 1,
        "pronunciationDictionaryFingerprint": _DIGEST,
        "runtimeProfileId": "runtime-profile-1",
        "runtimeProfileFingerprint": _DIGEST,
        "modelPackageId": "fixture-model-1",
        "modelPackageFingerprint": _DIGEST,
        "producerVersion": "1.0.0",
    }


def test_pronunciation_request_matches_contract_and_rejects_markup_or_bad_scope() -> None:
    request = CreatePronunciationEntryRequest.model_validate(_pronunciation_payload())
    assert request.scope_id == "role-1"
    assert request.model_dump(by_alias=True)["expectedDictionaryFingerprint"] == _DIGEST

    markup = _pronunciation_payload() | {"pronunciation": "<phoneme>unsafe</phoneme>"}
    with pytest.raises(ValidationError, match="provider markup"):
        CreatePronunciationEntryRequest.model_validate(markup)
    missing_scope = _pronunciation_payload() | {"scopeId": None}
    with pytest.raises(ValidationError, match="require scopeId"):
        CreatePronunciationEntryRequest.model_validate(missing_scope)
    project_scope = _pronunciation_payload() | {"scope": "project"}
    with pytest.raises(ValidationError, match="cannot have scopeId"):
        CreatePronunciationEntryRequest.model_validate(project_scope)


def test_session_binds_one_role_to_complete_current_evidence() -> None:
    payload = {
        "roleId": "role-1",
        "evidence": _evidence(),
        "idempotencyKey": "session-1",
    }
    assert CreateAuditionSessionRequest.model_validate(payload).role_id == "role-1"
    with pytest.raises(ValidationError, match="extra"):
        CreateAuditionSessionRequest.model_validate(payload | {"manuscriptText": "not accepted"})
    stale_provider = _evidence() | {"providerId": "unregistered-provider"}
    with pytest.raises(ValidationError):
        CreateAuditionSessionRequest.model_validate(payload | {"evidence": stale_provider})


def test_script_requires_exact_source_shape_and_unique_normalization_choices() -> None:
    payload = {
        "auditionSessionId": "session-1",
        "expectedSessionRevision": 1,
        "kind": "role_dialogue_excerpt",
        "text": "Synthetic source excerpt.",
        "sourceDocumentId": "source-1",
        "sourceRevision": 1,
        "sourceSpan": {"start": 10, "end": 35},
        "sourceTextSha256": _DIGEST,
        "acceptedOptionalNormalizationIds": ["edit-1"],
        "idempotencyKey": "script-1",
    }
    assert CreateAuditionScriptRequest.model_validate(payload).source_span is not None
    with pytest.raises(ValidationError, match="span"):
        CreateAuditionScriptRequest.model_validate(
            payload | {"sourceSpan": {"start": 10, "end": 10}}
        )
    with pytest.raises(ValidationError, match="unique"):
        CreateAuditionScriptRequest.model_validate(
            payload | {"acceptedOptionalNormalizationIds": ["edit-1", "edit-1"]}
        )
    with pytest.raises(ValidationError, match="cannot claim"):
        CreateAuditionScriptRequest.model_validate(payload | {"kind": "standardized_synthetic"})


def test_generation_preview_and_review_rationales_are_bounded() -> None:
    preview = {
        "contractVersion": "1.0.0",
        "requestId": "request-1",
        "auditionSessionId": "session-1",
        "auditionSessionRevision": 1,
        "auditionScriptId": "script-1",
        "auditionScriptFingerprint": _DIGEST,
        "evidence": _evidence(),
        "normalizedTextSha256": _DIGEST,
        "normalizationPlanFingerprint": _DIGEST,
        "pronunciationPlanFingerprint": _DIGEST,
        "providerControls": {
            "speakingRate": 1.0,
            "pitch": None,
            "style": None,
            "energy": None,
            "controlsFingerprint": _DIGEST,
        },
        "outputFormat": "pcm_s16le_wav",
        "sampleRateHz": 24000,
        "channels": 1,
        "idempotencyKey": "clip-1",
        "requestFingerprint": _DIGEST,
    }
    assert GenerateAuditionRequest.model_validate({"preview": preview}).preview.channels == 1
    with pytest.raises(ValidationError):
        GenerateAuditionRequest.model_validate({"preview": preview | {"sampleRateHz": 48000}})

    review_payload = {
        "decision": "approve",
        "expectedReviewRevision": 1,
        "expectedEvidenceFingerprint": _DIGEST,
        "rationale": "Human fixture review",
        "supersedesDecisionId": None,
        "idempotencyKey": "review-1",
    }
    assert DecideAuditionReviewRequest.model_validate(review_payload).decision == "approve"
    with pytest.raises(ValidationError):
        DecideAuditionReviewRequest.model_validate(review_payload | {"rationale": " "})


def test_review_decision_history_query_enforces_bounded_gate_scope_and_cursor() -> None:
    per_role = ListAuditionReviewDecisionsQuery.model_validate(
        {
            "gateId": "per_role_audition_review",
            "roleId": "role-1",
            "limit": 200,
        }
    )
    assert per_role.role_id == "role-1"
    assert per_role.limit == 200

    aggregate = ListAuditionReviewDecisionsQuery.model_validate(
        {"gateId": "voice_readiness_review"}
    )
    assert aggregate.role_id is None
    assert aggregate.limit == 50

    with pytest.raises(ValidationError, match="requires roleId"):
        ListAuditionReviewDecisionsQuery.model_validate({"gateId": "per_role_audition_review"})
    with pytest.raises(ValidationError, match="forbids roleId"):
        ListAuditionReviewDecisionsQuery.model_validate(
            {
                "gateId": "narrator_audition_review",
                "roleId": "role-1",
            }
        )
    with pytest.raises(ValidationError):
        ListAuditionReviewDecisionsQuery.model_validate({"gateId": "unknown_review"})
    with pytest.raises(ValidationError):
        ListAuditionReviewDecisionsQuery.model_validate(
            {
                "gateId": "voice_readiness_review",
                "cursor": "x" * 513,
            }
        )
    with pytest.raises(ValidationError):
        ListAuditionReviewDecisionsQuery.model_validate(
            {"gateId": "voice_readiness_review", "limit": 201}
        )


def test_model_operations_cannot_be_unversioned_or_silent() -> None:
    payload = {
        "modelPackageId": "fixture-model-1",
        "expectedManifestFingerprint": _DIGEST,
        "expectedInstallationRevision": 1,
        "action": "activate",
        "reason": "Activate verified local package",
        "idempotencyKey": "activate-1",
    }
    assert ModelInstallationOperationRequest.model_validate(payload).action == "activate"
    with pytest.raises(ValidationError):
        ModelInstallationOperationRequest.model_validate(
            payload | {"expectedInstallationRevision": 0}
        )
    with pytest.raises(ValidationError):
        ModelInstallationOperationRequest.model_validate(payload | {"reason": "   "})
