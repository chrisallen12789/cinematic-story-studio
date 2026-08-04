"""Durable Phase 3B local-speech audition repository and job orchestrator.

The repository owns the relational evidence and private audition storage boundary.
Public dictionaries are deliberately content-free except for authenticated script
and normalization detail responses; filesystem paths never cross this boundary.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Final, NoReturn, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from .audition_jobs import (
    AUDITION_PIPELINE_STAGES,
    AuditionCheckpoint,
    advance_checkpoint,
    deterministic_clip_id,
    new_checkpoint,
)
from .auditions import (
    AUDITION_PROFILE_FINGERPRINT,
    AUDITION_PROFILE_VERSION,
    AuditionCacheIdentity,
    AuditionError,
    inspect_audition_wav_bytes,
)
from .casting import (
    GOVERNED_KOKORO_MODEL_FINGERPRINT,
    GOVERNED_KOKORO_MODEL_ID,
    GOVERNED_KOKORO_PROVIDER_FINGERPRINT,
    GOVERNED_KOKORO_RIGHTS_RECORD_FINGERPRINT,
    GOVERNED_KOKORO_RIGHTS_RECORD_ID,
    GOVERNED_KOKORO_VOICE_PROFILE_FINGERPRINT,
    GOVERNED_KOKORO_VOICE_PROFILE_ID,
    GOVERNED_KOKORO_VOICE_PROFILE_VERSION,
    GOVERNED_KOKORO_VOICE_TENSOR_FORMAT,
    GOVERNED_KOKORO_VOICE_TENSOR_PATH,
    GOVERNED_KOKORO_VOICE_TENSOR_SHAPE,
    GOVERNED_VOICE_CATALOG_FINGERPRINT,
    GOVERNED_VOICE_CATALOG_REVISION_ID,
)
from .casting_repository import CastingRepository
from .config import ServiceSettings
from .database import Database
from .errors import ServiceError, not_found
from .local_speech import (
    SpeechArtifact,
    SpeechInvocationContext,
    SpeechPronunciationOverrideSpan,
    SpeechSynthesisRequest,
)
from .model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    MAX_MODEL_PACKAGE_TOTAL_BYTES,
    ModelPackageError,
    ModelPackageManager,
    ModelPackageVerification,
)
from .models import (
    AnalysisEntityRow,
    AnalysisExecutionRow,
    AnalysisReviewDecisionRow,
    AnalysisRunRow,
    AnalysisSnapshotRow,
    ApprovedCastSnapshotRow,
    AudioArtifactRow,
    AudioQualityRecordRow,
    AuditionCacheRecordRow,
    AuditionClipRow,
    AuditionEvidenceInvalidationRow,
    AuditionReviewDecisionRow,
    AuditionReviewRecordRow,
    AuditionScriptRow,
    AuditionSessionRow,
    CastAssignmentInvalidationRow,
    CastAssignmentRow,
    CastingGateDecisionRow,
    CastingRunRow,
    DocumentExtractionRow,
    IdempotencyRow,
    ImportedStoryRow,
    ImportReviewRow,
    JobRow,
    ModelInstallationRow,
    ModelPackageManifestRow,
    ModelVerificationRow,
    ProductionRoleRow,
    ProjectRow,
    PronunciationDictionaryRow,
    PronunciationEntryRow,
    SourceDocumentRow,
    SpeechProviderRequestRow,
    SpeechRuntimeInstanceRow,
    SpeechRuntimeProfileRow,
    TextNormalizationPlanRow,
    VoiceCatalogRevisionRow,
    VoiceModelDescriptorRow,
    VoiceProfileRow,
    VoiceProviderDescriptorRow,
    VoiceReadinessDecisionRow,
    VoiceReadinessReviewRow,
    VoiceReadinessSnapshotRow,
    VoiceRightsRecordRow,
    VoiceRuntimeBindingRow,
)
from .projects import ProjectRepository
from .pronunciation import (
    PRONUNCIATION_PROFILE_VERSION,
    PronunciationContext,
    PronunciationEntry,
    PronunciationError,
    PronunciationPlan,
    compile_pronunciation_plan,
    compile_provider_text,
    dictionary_fingerprint,
)
from .schemas import (
    ClearAuditionCacheRequest,
    CreateAuditionScriptRequest,
    CreateAuditionSessionRequest,
    CreatePronunciationEntryRequest,
    DecideAuditionReviewRequest,
    DecidePronunciationEntryRequest,
    GenerateAuditionRequest,
    InstallModelPackageRequest,
    ModelInstallationOperationRequest,
    PreviewNormalizationRequest,
)
from .speech_providers import (
    FIXTURE_ADAPTER_VERSION,
    FIXTURE_PROVIDER_ID,
    KOKORO_ADAPTER_VERSION,
    KOKORO_MAX_CONTENT_TOKENS,
    KOKORO_PROVIDER_ID,
)
from .speech_runtime import (
    SPEECH_RUNTIME_PROTOCOL_VERSION,
    ManagedSpeechRuntime,
    SpeechRuntimeConfig,
    SpeechRuntimeError,
    SpeechRuntimeExitEvidence,
    SpeechWorkerIdentity,
)
from .story_intelligence import StoryIntelligenceRepository
from .text_normalization import (
    MAX_NORMALIZATION_EDITS,
    MAX_NORMALIZATION_WARNINGS,
    MAX_UNSUPPORTED_CHARACTER_CODE_POINTS,
    NORMALIZATION_PROFILE_ID,
    NORMALIZATION_PROFILE_VERSION,
    NormalizationPlan,
    TextNormalizationError,
    compile_normalization,
    propose_normalization,
)
from .util import (
    canonical_json,
    ensure_private_directory,
    new_id,
    parse_json,
    request_fingerprint,
    resolve_beneath,
    sha256_bytes,
    sha256_text,
    stable_id,
    utc_now,
)

if TYPE_CHECKING:
    from .jobs import JobRepository


RuntimeFactory = Callable[[SpeechRuntimeConfig], ManagedSpeechRuntime]

_PRODUCER_ID: Final = "local-speech-audition-orchestrator@1.0.0"
_PRODUCER_VERSION: Final = "1.0.0"
_FIXTURE_PACKAGE_ID: Final = "deterministic-pcm-wav-fixture-package"
_FIXTURE_MODEL_ID: Final = "deterministic-square-wave"
_FIXTURE_MODEL_VERSION: Final = "1.0.0"
_FIXTURE_RUNTIME_ID: Final = "python-integer-pcm"
_FIXTURE_RUNTIME_VERSION: Final = "1.0.0"
_LEGACY_RUNTIME_PROFILE_VERSION: Final = "1.0.0"
_RUNTIME_PROFILE_VERSION: Final = "1.0.1"
_KOKORO_RUNTIME_PROFILE_VERSION: Final = "1.0.2"
_LEGACY_FIXTURE_PROFILE_ID: Final = "deterministic-pcm-wav-fixture-windows"
_LEGACY_KOKORO_PROFILE_ID: Final = "kokoro-local-onnx-windows"
_FIXTURE_PROFILE_ID: Final = "deterministic-pcm-wav-fixture-windows-v1-0-1"
_PREVIOUS_KOKORO_PROFILE_ID: Final = "kokoro-local-onnx-windows-v1-0-1"
_KOKORO_PROFILE_ID: Final = "kokoro-local-onnx-windows-v1-0-2"
DEFAULT_AUDITION_PAGE_SIZE: Final = 50
MAX_AUDITION_PAGE_SIZE: Final = 200
_MAX_PAGE_SIZE: Final = MAX_AUDITION_PAGE_SIZE
_MAX_SCRIPT_BYTES: Final = 16 * 1024
_MAX_OWNED_RUNTIME_SHUTDOWN_PROOFS: Final = 200
_MAX_RUNTIME_STARTUP_RECONCILIATION_RECORDS: Final = 100_000
_RUNTIME_SHUTDOWN_EVIDENCE_FILENAME: Final = "phase3b-runtime-shutdown-evidence.json"
_LEGACY_RUNTIME_STARTUP_TIMEOUT_MS: Final = 10_000
_RUNTIME_STARTUP_TIMEOUT_MS: Final = 30_000
_MAX_AUDIO_BYTES: Final = 24 * 1024 * 1024
_MAX_SESSIONS: Final = 2_000
_MAX_SCRIPTS_PER_SESSION: Final = 20
_MAX_PRONUNCIATION_ENTRIES: Final = 1_000
_MAX_PRONUNCIATION_LINEAGE_RECORDS: Final = 4_096
_MAX_PRONUNCIATION_HISTORY_RECORDS: Final = 100_000
_MAX_MUTATION_CLIP_ID_SAMPLE: Final = 200
_MAX_CACHE_RECORDS: Final = 10_000
_MAX_SCRIPT_STORAGE_RECONCILIATION_RECORDS: Final = 100_000
_MAX_AUDIO_STORAGE_RECONCILIATION_ENTRIES: Final = 100_000
_AUDIO_POLICY_ID: Final = "audition-pcm-wav-integrity"
_UUID_FILE_COMPONENT: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_OWNED_SCRIPT_FILE_NAME: Final = re.compile(rf"^(?P<storage_id>{_UUID_FILE_COMPONENT})\.utf8$")
_OWNED_SCRIPT_STAGING_FILE_NAME: Final = re.compile(
    rf"^\.(?P<storage_id>{_UUID_FILE_COMPONENT})\.utf8\.pending\."
    rf"(?P<script_record_id>{_UUID_FILE_COMPONENT})\.tmp$"
)
_OWNED_STAGING_FILE_NAME: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.wav\.pending$"
)
_OWNED_AUDIO_FILE_NAME: Final = re.compile(rf"^(?P<artifact_id>{_UUID_FILE_COMPONENT})\.wav$")
_OWNED_ATOMIC_STAGING_TEMP_NAME: Final = re.compile(
    r"^\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"\.wav\.pending\.[0-9a-f]{32}\.tmp$"
)
_OWNED_AUDIO_PURGE_TOMBSTONE_NAME: Final = re.compile(
    r"^\.(?P<artifact_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12})\.wav\.purge\.[0-9a-f]{32}\.tmp$"
)
_PHASE2_GATE_IDS: Final = (
    "story_structure_review",
    "character_registry_review",
    "dialogue_attribution_review",
    "whole_book_analysis_review",
)
_PHASE3A_GATE_IDS: Final = (
    "narrator_casting_review",
    "character_casting_review",
    "complete_cast_review",
)
_AUDITION_GATE_IDS: Final = (
    "per_role_audition_review",
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review",
)

_GOVERNED_PRIVATE_AUDITION_WARNING: Final = (
    "Private local audition only. This voice is not cleared by Cinematic Story Studio "
    "for production export, commercial distribution, marketplace resale, cloning, or "
    "real-person imitation."
)
_GOVERNED_PRIVATE_AUDITION_WARNING_FINGERPRINT: Final = (
    "13b8747ea2ced9de9cc1d0f67b5c018b25de7de02359a1480744db4a37939645"
)
_GOVERNED_VOICE_INVENTORY_ID: Final = "governed-local-kokoro-voice-inventory-v1"
_GOVERNED_VOICE_INVENTORY_REVISION: Final = 1
_GOVERNED_VOICE_INVENTORY_RECORDED_AT: Final = "2026-08-02T00:00:00Z"
_GOVERNED_VOICE_INVENTORY_RECORD_FINGERPRINT: Final = (
    "5ce2d5bcaa016d1815183de99c7dca37bc3a615cb0a33efb6e1016498abb8fa8"
)
_GOVERNED_VOICE_INVENTORY_FINGERPRINT: Final = (
    "cb5657779b22d422cd7d8b9b81e09491aae1a82795e9e6af8a781c5f4c47c9bc"
)


def _governed_voice_inventory_record_material() -> dict[str, Any]:
    voice_tensor = next(
        artifact
        for artifact in KOKORO_LOCAL_ONNX_MANIFEST.artifacts
        if artifact.path == GOVERNED_KOKORO_VOICE_TENSOR_PATH
    )
    return {
        "contractVersion": "1.0.0",
        "inventoryRecordId": stable_id(
            "phase3b1-governed-local-voice-inventory-record",
            GOVERNED_KOKORO_VOICE_PROFILE_ID,
            GOVERNED_KOKORO_VOICE_PROFILE_VERSION,
            voice_tensor.sha256,
        ),
        "neutralDisplayLabel": "Local Voice 001",
        "providerId": KOKORO_LOCAL_ONNX_MANIFEST.provider_id,
        "providerVersion": KOKORO_LOCAL_ONNX_MANIFEST.provider_version,
        "providerVoiceId": KOKORO_LOCAL_ONNX_MANIFEST.voice_id,
        "modelId": GOVERNED_KOKORO_MODEL_ID,
        "modelVersion": KOKORO_LOCAL_ONNX_MANIFEST.model_version,
        "modelPackageId": KOKORO_LOCAL_ONNX_MANIFEST.package_id,
        "modelPackageFingerprint": KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
        "voiceProfileId": GOVERNED_KOKORO_VOICE_PROFILE_ID,
        "voiceProfileVersion": GOVERNED_KOKORO_VOICE_PROFILE_VERSION,
        "voiceProfileFingerprint": GOVERNED_KOKORO_VOICE_PROFILE_FINGERPRINT,
        "catalogRevisionId": GOVERNED_VOICE_CATALOG_REVISION_ID,
        "catalogRevisionFingerprint": GOVERNED_VOICE_CATALOG_FINGERPRINT,
        "voiceTensor": {
            "relativePath": voice_tensor.path,
            "byteSize": voice_tensor.size_bytes,
            "sha256": voice_tensor.sha256,
            "scalarFormat": "float32_le",
            "shape": list(GOVERNED_KOKORO_VOICE_TENSOR_SHAPE),
            "elementCount": (
                GOVERNED_KOKORO_VOICE_TENSOR_SHAPE[0] * GOVERNED_KOKORO_VOICE_TENSOR_SHAPE[1]
            ),
        },
        "rights": {
            "rightsRecordId": GOVERNED_KOKORO_RIGHTS_RECORD_ID,
            "rightsRecordRevision": 1,
            "rightsRecordFingerprint": GOVERNED_KOKORO_RIGHTS_RECORD_FINGERPRINT,
            "rightsState": "restricted",
            "consentStatus": "unknown",
            "commercialUseClassification": "restricted",
            "redistributionClassification": "restricted",
            "evidenceReferences": [
                (
                    "https://huggingface.co/onnx-community/"
                    "Kokoro-82M-v1.0-ONNX/tree/"
                    f"{KOKORO_LOCAL_ONNX_MANIFEST.source_revision}"
                ),
                "https://huggingface.co/hexgrad/Kokoro-82M",
                (
                    "https://github.com/hexgrad/kokoro/tree/"
                    f"{KOKORO_LOCAL_ONNX_MANIFEST.provenance.maintainer_reference_revision}"
                ),
            ],
        },
        "language": "en",
        "locale": "en-US",
        "providerDeclaredPresentationCategory": (
            "American English feminine presentation (provider-declared)"
        ),
        "providerDeclaredMetadataIndependentlyVerified": False,
        "technicalCompatibility": "compatible",
        "activationEligibility": "restricted_private_audition",
        "activationReasonCode": ("RESTRICTED_PRIVATE_LOCAL_AUDITION_ACKNOWLEDGEMENT_REQUIRED"),
        "knownLimitations": [
            "private_local_audition_only",
            "human_listening_required",
            "production_export_ineligible",
            "voice_rights_evidence_incomplete",
            "provider_declared_categories_not_independently_verified",
            "single_voice_package",
        ],
        "unresolvedEvidenceCodes": [
            "VOICE_TENSOR_LICENSE_SCOPE_UNRESOLVED",
            "DATASET_PROVENANCE_INCOMPLETE",
            "PERFORMER_CONSENT_UNKNOWN",
            "IDENTITY_AND_LIKENESS_CLEARANCE_UNKNOWN",
            "COMMERCIAL_CLEARANCE_NOT_ESTABLISHED",
            "REDISTRIBUTION_NOT_AUTHORIZED",
            "SUBLICENSING_NOT_AUTHORIZED",
        ],
        "productionExportEligible": False,
        "provenance": {
            "origin": "application",
            "producerId": _PRODUCER_ID,
            "producerVersion": _PRODUCER_VERSION,
            "recordedAt": _GOVERNED_VOICE_INVENTORY_RECORDED_AT,
            "inputFingerprint": KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
        },
    }


def _governed_voice_inventory() -> dict[str, Any]:
    if (
        sha256_text(_GOVERNED_PRIVATE_AUDITION_WARNING)
        != _GOVERNED_PRIVATE_AUDITION_WARNING_FINGERPRINT
        or GOVERNED_KOKORO_VOICE_TENSOR_FORMAT != "little_endian_float32"
    ):
        raise RuntimeError("The governed private-audition constants failed verification.")
    record = _governed_voice_inventory_record_material()
    record["inventoryFingerprint"] = request_fingerprint(record)
    inventory = {
        "inventoryId": _GOVERNED_VOICE_INVENTORY_ID,
        "inventoryRevision": _GOVERNED_VOICE_INVENTORY_REVISION,
        "warningText": _GOVERNED_PRIVATE_AUDITION_WARNING,
        "warningFingerprint": _GOVERNED_PRIVATE_AUDITION_WARNING_FINGERPRINT,
        "items": [record],
    }
    inventory["inventoryFingerprint"] = request_fingerprint(inventory)
    if (
        record["inventoryFingerprint"] != _GOVERNED_VOICE_INVENTORY_RECORD_FINGERPRINT
        or inventory["inventoryFingerprint"] != _GOVERNED_VOICE_INVENTORY_FINGERPRINT
    ):
        raise RuntimeError("The governed local voice inventory failed fingerprint validation.")
    return deepcopy(inventory)


_FIXTURE_FILE_SHA256: Final = sha256_text("deterministic-square-wave:1.0.0")
_FIXTURE_FILE_INVENTORY: Final = (
    {
        "byteSize": 1,
        "executable": False,
        "mediaClassification": "configuration",
        "relativePath": "virtual/deterministic-square-wave.fixture",
        "sha256": _FIXTURE_FILE_SHA256,
    },
)


def _fixture_manifest_fingerprint_material() -> dict[str, object]:
    return {
        "architecture": "x64",
        "attributionRequirements": [],
        "commercialUseClassification": "fixture_only",
        "compatibilityConstraints": [
            "platform:windows",
            "architecture:x64",
            f"runtime:{_FIXTURE_RUNTIME_VERSION}",
        ],
        "files": _FIXTURE_FILE_INVENTORY,
        "licenseIdentifier": "repository-test-fixture",
        "manifestVersion": "1.0.0",
        "modelId": _FIXTURE_MODEL_ID,
        "modelPackageId": _FIXTURE_PACKAGE_ID,
        "modelVersion": _FIXTURE_MODEL_VERSION,
        "officialSourceReference": "repository://deterministic-pcm-wav-fixture",
        "platform": "windows",
        "providerId": FIXTURE_PROVIDER_ID,
        "providerVersion": FIXTURE_ADAPTER_VERSION,
        "requiredRuntimeDependencies": [],
        "revocationState": "active",
        "runtimeId": _FIXTURE_RUNTIME_ID,
        "runtimeVersion": _FIXTURE_RUNTIME_VERSION,
        "sourceClassification": "repository_fixture",
        "totalExpandedSize": 1,
    }


_FIXTURE_MANIFEST_FINGERPRINT: Final = request_fingerprint(_fixture_manifest_fingerprint_material())


def _runtime_profile_fingerprint_material(
    *,
    profile_id: str,
    profile_version: str,
    provider_id: str,
    provider_version: str,
    runtime_id: str,
    runtime_version: str,
    startup_timeout_ms: int,
    maximum_content_tokens: int | None = None,
) -> dict[str, object]:
    limits = {
        "maximumAudioBytes": _MAX_AUDIO_BYTES,
        "maximumDurationMilliseconds": 30_000,
        "maximumRetryAttempts": 0,
        "maximumScriptCodePoints": 4_000,
    }
    if maximum_content_tokens is not None:
        limits["maximumContentTokens"] = maximum_content_tokens
    return {
        "architecture": "x64",
        "idleShutdownMilliseconds": 120_000,
        "limits": limits,
        "maximumConcurrentRequests": 1,
        "networkPolicy": "deny_during_synthesis",
        "outputFormats": ["pcm_s16le_wav"],
        "platform": "windows",
        "profileId": profile_id,
        "profileVersion": profile_version,
        "protocolVersion": SPEECH_RUNTIME_PROTOCOL_VERSION,
        "providerId": provider_id,
        "providerVersion": provider_version,
        "requestDeadlineMilliseconds": 60_000,
        "runtimeId": runtime_id,
        "runtimeVersion": runtime_version,
        "startupDeadlineMilliseconds": startup_timeout_ms,
    }


def _runtime_profile_row_fingerprint_material(
    row: SpeechRuntimeProfileRow,
) -> dict[str, object]:
    limits = parse_json(row.limits_json, {})
    output_formats = parse_json(row.output_format_json, [])
    return {
        "architecture": row.architecture,
        "idleShutdownMilliseconds": row.idle_shutdown_ms,
        "limits": limits,
        "maximumConcurrentRequests": row.maximum_concurrency,
        "networkPolicy": row.network_policy,
        "outputFormats": output_formats,
        "platform": row.platform,
        "profileId": row.profile_id,
        "profileVersion": row.profile_version,
        "protocolVersion": row.protocol_version,
        "providerId": row.provider_id,
        "providerVersion": row.provider_version,
        "requestDeadlineMilliseconds": row.request_timeout_ms,
        "runtimeId": row.runtime_id,
        "runtimeVersion": row.runtime_version,
        "startupDeadlineMilliseconds": row.startup_timeout_ms,
    }


def _runtime_creation_identity(identity: SpeechWorkerIdentity) -> str:
    return request_fingerprint(
        {
            "createdAtUnixNs": identity.created_at_unix_ns,
            "creationNonce": identity.creation_nonce,
            "launcherPid": identity.launcher_pid,
            "parentPid": identity.parent_pid,
            "pid": identity.pid,
        }
    )


def _runtime_exit_confirms_identity(
    evidence: SpeechRuntimeExitEvidence | None,
    identity: SpeechWorkerIdentity,
) -> bool:
    return (
        evidence is not None
        and evidence.pid == identity.pid
        and evidence.launcher_pid == identity.launcher_pid
        and evidence.ownership_confirmed
        and evidence.confirmed_exited
        and evidence.owned_processes_confirmed_exited
        and (os.name != "nt" or evidence.job_object_assigned)
    )


def _runtime_exit_confirms_owned_tree(
    evidence: SpeechRuntimeExitEvidence | None,
) -> bool:
    return (
        evidence is not None
        and evidence.confirmed_exited
        and evidence.owned_processes_confirmed_exited
        and (os.name != "nt" or evidence.job_object_assigned)
    )


_FIXTURE_PROFILE_FINGERPRINT: Final = request_fingerprint(
    _runtime_profile_fingerprint_material(
        profile_id=_FIXTURE_PROFILE_ID,
        profile_version=_RUNTIME_PROFILE_VERSION,
        provider_id=FIXTURE_PROVIDER_ID,
        provider_version=FIXTURE_ADAPTER_VERSION,
        runtime_id=_FIXTURE_RUNTIME_ID,
        runtime_version=_FIXTURE_RUNTIME_VERSION,
        startup_timeout_ms=_RUNTIME_STARTUP_TIMEOUT_MS,
    )
)
_KOKORO_PROFILE_FINGERPRINT: Final = request_fingerprint(
    _runtime_profile_fingerprint_material(
        profile_id=_KOKORO_PROFILE_ID,
        profile_version=_KOKORO_RUNTIME_PROFILE_VERSION,
        provider_id=KOKORO_PROVIDER_ID,
        provider_version=KOKORO_ADAPTER_VERSION,
        runtime_id="onnxruntime-cpu",
        runtime_version="1.28.0",
        startup_timeout_ms=_RUNTIME_STARTUP_TIMEOUT_MS,
        maximum_content_tokens=KOKORO_MAX_CONTENT_TOKENS,
    )
)
_PREVIOUS_KOKORO_PROFILE_FINGERPRINT: Final = request_fingerprint(
    _runtime_profile_fingerprint_material(
        profile_id=_PREVIOUS_KOKORO_PROFILE_ID,
        profile_version=_RUNTIME_PROFILE_VERSION,
        provider_id=KOKORO_PROVIDER_ID,
        provider_version=KOKORO_ADAPTER_VERSION,
        runtime_id="onnxruntime-cpu",
        runtime_version="1.28.0",
        startup_timeout_ms=_RUNTIME_STARTUP_TIMEOUT_MS,
    )
)
_LEGACY_FIXTURE_PROFILE_FINGERPRINT: Final = request_fingerprint(
    _runtime_profile_fingerprint_material(
        profile_id=_LEGACY_FIXTURE_PROFILE_ID,
        profile_version=_LEGACY_RUNTIME_PROFILE_VERSION,
        provider_id=FIXTURE_PROVIDER_ID,
        provider_version=FIXTURE_ADAPTER_VERSION,
        runtime_id=_FIXTURE_RUNTIME_ID,
        runtime_version=_FIXTURE_RUNTIME_VERSION,
        startup_timeout_ms=_LEGACY_RUNTIME_STARTUP_TIMEOUT_MS,
    )
)
_LEGACY_KOKORO_PROFILE_FINGERPRINT: Final = request_fingerprint(
    _runtime_profile_fingerprint_material(
        profile_id=_LEGACY_KOKORO_PROFILE_ID,
        profile_version=_LEGACY_RUNTIME_PROFILE_VERSION,
        provider_id=KOKORO_PROVIDER_ID,
        provider_version=KOKORO_ADAPTER_VERSION,
        runtime_id="onnxruntime-cpu",
        runtime_version="1.28.0",
        startup_timeout_ms=_LEGACY_RUNTIME_STARTUP_TIMEOUT_MS,
    )
)
_LEGACY_FIXTURE_PROFILE_RECORD_ID: Final = stable_id(
    "phase3b-runtime-profile",
    _LEGACY_FIXTURE_PROFILE_ID,
)
_LEGACY_KOKORO_PROFILE_RECORD_ID: Final = stable_id(
    "phase3b-runtime-profile",
    _LEGACY_KOKORO_PROFILE_ID,
)
_FIXTURE_PROFILE_RECORD_ID: Final = stable_id(
    "phase3b-runtime-profile",
    _FIXTURE_PROFILE_ID,
    _RUNTIME_PROFILE_VERSION,
)
_PREVIOUS_KOKORO_PROFILE_RECORD_ID: Final = stable_id(
    "phase3b-runtime-profile",
    _PREVIOUS_KOKORO_PROFILE_ID,
    _RUNTIME_PROFILE_VERSION,
)
_KOKORO_PROFILE_RECORD_ID: Final = stable_id(
    "phase3b-runtime-profile",
    _KOKORO_PROFILE_ID,
    _KOKORO_RUNTIME_PROFILE_VERSION,
)
_EMPTY_DICTIONARY_FINGERPRINT: Final = dictionary_fingerprint((), 0)


def _current_runtime_profile_identity(provider_id: str) -> tuple[str, str] | None:
    if provider_id == FIXTURE_PROVIDER_ID:
        return _FIXTURE_PROFILE_RECORD_ID, _FIXTURE_PROFILE_FINGERPRINT
    if provider_id == KOKORO_PROVIDER_ID:
        return _KOKORO_PROFILE_RECORD_ID, _KOKORO_PROFILE_FINGERPRINT
    return None


def _provenance(
    origin: str,
    *,
    input_fingerprint: str | None = None,
    details: Mapping[str, object] | None = None,
) -> str:
    value: dict[str, object] = {
        "origin": origin,
        "producerId": _PRODUCER_ID,
        "producerVersion": _PRODUCER_VERSION,
        "recordedAt": utc_now(),
    }
    if input_fingerprint is not None:
        value["inputFingerprint"] = input_fingerprint
    if details:
        value["details"] = dict(details)
    return canonical_json(value)


def _public_provenance(provenance_json: str) -> dict[str, Any]:
    """Project private provenance evidence onto the closed public contract."""

    def invalid() -> NoReturn:
        raise ServiceError(
            500,
            "SPEECH_PROVENANCE_INVALID",
            "Stored speech provenance failed integrity validation.",
        )

    try:
        value = parse_json(provenance_json, None)
    except (TypeError, ValueError):
        invalid()
    required_keys = ("origin", "producerId", "producerVersion", "recordedAt")
    safe_code = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-]{0,127}")

    if not isinstance(value, dict) or any(key not in value for key in required_keys):
        invalid()
    origin = value["origin"]
    producer_id = value["producerId"]
    producer_version = value["producerVersion"]
    recorded_at = value["recordedAt"]
    if (
        not isinstance(origin, str)
        or origin
        not in {"fixture_provider", "real_local_provider", "application", "human", "system"}
        or not isinstance(producer_id, str)
        or safe_code.fullmatch(producer_id) is None
        or not isinstance(producer_version, str)
        or safe_code.fullmatch(producer_version) is None
        or not isinstance(recorded_at, str)
        or len(recorded_at) > 40
        or re.match(r"\d{4}-\d{2}-\d{2}T", recorded_at) is None
    ):
        invalid()
    try:
        parsed_recorded_at = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError:
        invalid()
    if parsed_recorded_at.tzinfo is None:
        invalid()
    if "inputFingerprint" in value:
        input_fingerprint = value["inputFingerprint"]
        if (
            not isinstance(input_fingerprint, str)
            or re.fullmatch(r"[a-f0-9]{64}", input_fingerprint) is None
        ):
            invalid()
    if "reasonCode" in value:
        reason_code = value["reasonCode"]
        if not isinstance(reason_code, str) or safe_code.fullmatch(reason_code) is None:
            invalid()
    return {
        key: value[key]
        for key in (*required_keys, "inputFingerprint", "reasonCode")
        if key in value
    }


def _require_digest(value: str, *, code: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ServiceError(422, code, "The supplied evidence fingerprint is invalid.")


def _audio_quality_fingerprint(
    *,
    quality_record_id: str,
    project_id: str,
    clip_id: str,
    artifact_id: str,
    artifact_fingerprint: str,
    provider_request_id: str,
    revision: int,
    policy_id: str,
    policy_version: str,
    policy_fingerprint: str,
    outcome: str,
    peak_millidbfs: int,
    rms_millidbfs: int,
    silence_ratio_ppm: int,
    clipped_sample_count: int,
    warning_count: int,
    blocking_finding_count: int,
    blocking_finding_codes: Sequence[str],
    warning_codes: Sequence[str],
) -> str:
    """Bind every persisted machine-QC measurement, count, policy, and identity."""

    return request_fingerprint(
        {
            "artifactFingerprint": artifact_fingerprint,
            "artifactId": artifact_id,
            "blockingFindingCodes": list(blocking_finding_codes),
            "blockingFindingCount": blocking_finding_count,
            "clipId": clip_id,
            "clippedSampleCount": clipped_sample_count,
            "outcome": outcome,
            "peakMillidbfs": peak_millidbfs,
            "policyFingerprint": policy_fingerprint,
            "policyId": policy_id,
            "policyVersion": policy_version,
            "projectId": project_id,
            "providerRequestId": provider_request_id,
            "qualityRecordId": quality_record_id,
            "revision": revision,
            "rmsMillidbfs": rms_millidbfs,
            "silenceRatioPpm": silence_ratio_ppm,
            "warningCodes": list(warning_codes),
            "warningCount": warning_count,
        }
    )


def _validated_audio_quality_evidence(
    row: AudioQualityRecordRow,
    *,
    artifact: AudioArtifactRow,
    clip: AuditionClipRow,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return canonical QC codes only after exact persisted-evidence verification."""

    findings = parse_json(row.findings_json, None)
    if not isinstance(findings, dict) or set(findings) != {
        "blockingFindingCodes",
        "warningCodes",
    }:
        raise ValueError("The audio-quality findings shape is invalid.")

    def codes(key: str) -> tuple[str, ...]:
        value = findings.get(key)
        if (
            not isinstance(value, list)
            or len(value) > 100
            or any(
                not isinstance(code, str)
                or not code
                or len(code) > 120
                or any(ord(character) < 33 or ord(character) > 126 for character in code)
                for code in value
            )
            or len(value) != len(set(value))
        ):
            raise ValueError("The audio-quality finding codes are invalid.")
        return tuple(value)

    blocking = codes("blockingFindingCodes")
    warnings = codes("warningCodes")
    expected_outcome = "blocked" if blocking else "warning" if warnings else "passed"
    # The artifact request is creation provenance and legitimately differs from the
    # current clip request on a verified cache hit. The QC row binds both the reused
    # artifact identity and the current clip/provider-request identity instead.
    if (
        row.project_id != clip.project_id
        or artifact.project_id != clip.project_id
        or row.clip_id != clip.id
        or row.artifact_id != artifact.id
        or row.provider_request_id != clip.provider_request_id
        or row.policy_id != _AUDIO_POLICY_ID
        or row.policy_version != AUDITION_PROFILE_VERSION
        or row.policy_fingerprint != AUDITION_PROFILE_FINGERPRINT
        or row.warning_count != len(warnings)
        or row.blocking_finding_count != len(blocking)
        or row.outcome != expected_outcome
    ):
        raise ValueError("The audio-quality evidence binding is invalid.")
    expected_fingerprint = _audio_quality_fingerprint(
        quality_record_id=row.id,
        project_id=row.project_id,
        clip_id=row.clip_id,
        artifact_id=row.artifact_id,
        artifact_fingerprint=artifact.artifact_fingerprint,
        provider_request_id=row.provider_request_id,
        revision=row.revision,
        policy_id=row.policy_id,
        policy_version=row.policy_version,
        policy_fingerprint=row.policy_fingerprint,
        outcome=row.outcome,
        peak_millidbfs=row.peak_millidbfs,
        rms_millidbfs=row.rms_millidbfs,
        silence_ratio_ppm=row.silence_ratio_ppm,
        clipped_sample_count=row.clipped_sample_count,
        warning_count=row.warning_count,
        blocking_finding_count=row.blocking_finding_count,
        blocking_finding_codes=blocking,
        warning_codes=warnings,
    )
    if row.quality_fingerprint != expected_fingerprint:
        raise ValueError("The audio-quality evidence fingerprint is invalid.")
    return blocking, warnings


def _encode_cursor(binding: str, offset: int) -> str:
    value = canonical_json({"binding": binding, "offset": offset, "version": "v1"})
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None, *, binding: str) -> int:
    if cursor is None:
        return 0
    if not cursor or len(cursor) > 512:
        raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True).decode())
        if (
            not isinstance(value, dict)
            or value.get("version") != "v1"
            or value.get("binding") != binding
            or not isinstance(value.get("offset"), int)
        ):
            raise ValueError
        offset = int(value["offset"])
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.") from exc
    if offset < 0:
        raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
    return offset


def _bounded_page(limit: int) -> int:
    if not 1 <= limit <= _MAX_PAGE_SIZE:
        raise ServiceError(422, "PAGE_LIMIT_INVALID", "The requested page size is invalid.")
    return limit


def _encode_review_history_cursor(
    *,
    binding: str,
    latest_decision_id: str,
    total: int,
    offset: int,
) -> str:
    material = {
        "b": binding,
        "l": latest_decision_id,
        "n": total,
        "o": offset,
        "v": "v1",
    }
    value = material | {
        "i": request_fingerprint(
            {
                "cursor": material,
                "namespace": "phase3b-audition-review-decision-history",
            }
        )
    }
    return base64.urlsafe_b64encode(canonical_json(value).encode()).decode().rstrip("=")


def _decode_review_history_cursor(
    cursor: str | None,
    *,
    binding: str,
    latest_decision_id: str | None,
    total: int,
) -> int:
    if cursor is None:
        return 0
    if not cursor or len(cursor) > 512:
        raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True).decode())
        if not isinstance(value, dict) or set(value) != {"b", "i", "l", "n", "o", "v"}:
            raise ValueError
        material = {key: value[key] for key in ("b", "l", "n", "o", "v")}
        expected_integrity = request_fingerprint(
            {
                "cursor": material,
                "namespace": "phase3b-audition-review-decision-history",
            }
        )
        if (
            value["v"] != "v1"
            or value["b"] != binding
            or not isinstance(value["l"], str)
            or not value["l"]
            or len(value["l"]) > 128
            or isinstance(value["n"], bool)
            or not isinstance(value["n"], int)
            or isinstance(value["o"], bool)
            or not isinstance(value["o"], int)
            or not isinstance(value["i"], str)
            or value["i"] != expected_integrity
            or value["n"] < 1
            or not 0 < value["o"] < value["n"]
        ):
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ServiceError(
            400,
            "INVALID_CURSOR",
            "The pagination cursor is invalid.",
        ) from exc
    if value["l"] != latest_decision_id or value["n"] != total:
        raise ServiceError(
            409,
            "AUDITION_REVIEW_HISTORY_CURSOR_STALE",
            "The audition review decision history changed; restart pagination.",
        )
    return int(value["o"])


def _atomic_write(path: Path, payload: bytes) -> None:
    ensure_private_directory(path.parent)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _verified_storage_path(
    root: Path,
    candidate: Path,
    *,
    require_directory: bool = False,
) -> Path:
    """Resolve an existing owned path only after rejecting every lexical reparse point."""

    lexical_root = root.absolute()
    lexical_candidate = candidate.absolute()
    lexical_candidate.relative_to(lexical_root)
    current = lexical_candidate
    while True:
        metadata = current.lstat()
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if stat.S_ISLNK(metadata.st_mode) or bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError("Managed audition storage contains a reparse point.")
        if current == lexical_root:
            break
        if current == current.parent:
            raise ValueError("Managed audition storage escaped its root.")
        current = current.parent
    resolved_root = lexical_root.resolve(strict=True)
    resolved = lexical_candidate.resolve(strict=True)
    resolved.relative_to(resolved_root)
    metadata = lexical_candidate.lstat()
    if require_directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Managed audition storage is not a directory.")
    elif not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Managed audition storage is not a regular file.")
    return resolved


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
    )


def _stable_file_state(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        *_stable_file_identity(metadata),
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_bounded_stable_regular_file(
    managed_root: Path,
    path: Path,
    *,
    maximum_bytes: int,
    expected_byte_size: int | None = None,
) -> tuple[Path, bytes]:
    """Read one owned regular file while proving its entry stayed stable."""

    if maximum_bytes < 1 or (
        expected_byte_size is not None and not 1 <= expected_byte_size <= maximum_bytes
    ):
        raise ValueError("The managed file byte bound is invalid.")
    resolved = _verified_storage_path(managed_root, path)
    before_path = resolved.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(before_path.st_mode)
        or before_path.st_size < 1
        or before_path.st_size > maximum_bytes
        or (expected_byte_size is not None and before_path.st_size != expected_byte_size)
    ):
        raise ValueError("The managed file byte size is invalid.")
    with resolved.open("rb") as source:
        before_handle = os.fstat(source.fileno())
        if not stat.S_ISREG(before_handle.st_mode) or _stable_file_identity(
            before_handle
        ) != _stable_file_identity(before_path):
            raise ValueError("The managed file identity changed before reading.")
        payload = source.read(maximum_bytes + 1)
        after_handle = os.fstat(source.fileno())
    if (
        len(payload) > maximum_bytes
        or len(payload) != before_handle.st_size
        or (expected_byte_size is not None and len(payload) != expected_byte_size)
        or _stable_file_state(after_handle) != _stable_file_state(before_handle)
    ):
        raise ValueError("The managed file changed while reading.")
    verified_after = _verified_storage_path(managed_root, path)
    after_path = verified_after.stat(follow_symlinks=False)
    if (
        verified_after != resolved
        or _stable_file_identity(after_path) != _stable_file_identity(after_handle)
        or _stable_file_state(after_path) != _stable_file_state(before_path)
    ):
        raise ValueError("The managed file entry changed while reading.")
    return resolved, payload


def _verified_script_text(
    managed_root: Path,
    path: Path,
    *,
    expected_sha256: str,
    expected_codepoint_count: int,
) -> tuple[Path, str]:
    resolved, payload = _read_bounded_stable_regular_file(
        managed_root,
        path,
        maximum_bytes=_MAX_SCRIPT_BYTES,
    )
    if sha256_bytes(payload) != expected_sha256:
        raise ValueError("The private audition script digest is invalid.")
    text = payload.decode("utf-8")
    if len(text) != expected_codepoint_count:
        raise ValueError("The private audition script length is invalid.")
    return resolved, text


@dataclass(frozen=True, slots=True)
class _ScriptSourceEntityBinding:
    entity_id: str
    collection: str
    effective_revision: int
    effective_fingerprint: str


@dataclass(frozen=True, slots=True)
class _QuarantinedRuntime:
    runtime: ManagedSpeechRuntime
    identity: SpeechWorkerIdentity | None
    bound: bool


@dataclass(frozen=True, slots=True)
class _CurrentCastAuthority:
    casting_run: CastingRunRow
    cast_snapshot: ApprovedCastSnapshotRow
    assignments_by_role: Mapping[str, CastAssignmentRow]
    phase3a_decision_ids: tuple[str, ...] | None
    rights_current: bool


@dataclass(frozen=True, slots=True)
class _CurrentRoleAuditionEvidence:
    audition_session: AuditionSessionRow | None
    clip: AuditionClipRow | None
    review: AuditionReviewRecordRow | None
    decision: AuditionReviewDecisionRow | None


@dataclass(slots=True)
class _PendingScriptPublication:
    managed_root: Path
    final_path: Path | None = None
    staging_path: Path | None = None
    script_record_id: str | None = None
    expected_sha256: str | None = None
    expected_codepoint_count: int | None = None

    def stage(self, final_path: Path, payload: bytes, *, script_record_id: str) -> None:
        if self.staging_path is not None or self.final_path is not None:
            raise ValueError("A private audition script is already staged.")
        final_match = _OWNED_SCRIPT_FILE_NAME.fullmatch(final_path.name)
        if final_match is None or _path_entry_exists(final_path):
            raise ValueError("The private audition script destination is invalid.")
        directory = ensure_private_directory(final_path.parent)
        _verified_storage_path(
            self.managed_root,
            directory,
            require_directory=True,
        )
        staging_path = directory / (f".{final_path.name}.pending.{script_record_id}.tmp")
        staging_match = _OWNED_SCRIPT_STAGING_FILE_NAME.fullmatch(staging_path.name)
        if (
            staging_match is None
            or staging_match.group("storage_id") != final_match.group("storage_id")
            or staging_match.group("script_record_id") != script_record_id
        ):
            raise ValueError("The private audition script staging identity is invalid.")
        output = staging_path.open("xb")
        self.final_path = final_path
        self.staging_path = staging_path
        self.script_record_id = script_record_id
        self.expected_sha256 = sha256_bytes(payload)
        self.expected_codepoint_count = len(payload.decode("utf-8"))
        try:
            with output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.chmod(staging_path, 0o600)
            except OSError:
                pass
            _verified_script_text(
                self.managed_root,
                staging_path,
                expected_sha256=self.expected_sha256,
                expected_codepoint_count=self.expected_codepoint_count,
            )
        except Exception:
            self.discard()
            raise

    def _assert_identity(self) -> None:
        if (
            self.final_path is None
            or self.staging_path is None
            or self.script_record_id is None
            or self.expected_sha256 is None
            or self.expected_codepoint_count is None
        ):
            raise ValueError("The private audition script staging state is incomplete.")
        final_match = _OWNED_SCRIPT_FILE_NAME.fullmatch(self.final_path.name)
        staging_match = _OWNED_SCRIPT_STAGING_FILE_NAME.fullmatch(self.staging_path.name)
        if (
            final_match is None
            or staging_match is None
            or self.staging_path.parent != self.final_path.parent
            or staging_match.group("storage_id") != final_match.group("storage_id")
            or staging_match.group("script_record_id") != self.script_record_id
        ):
            raise ValueError("The private audition script staging identity changed.")

    def discard(self) -> None:
        if self.staging_path is None:
            return
        self._assert_identity()
        staging_path = self.staging_path
        if _path_entry_exists(staging_path):
            resolved = _verified_storage_path(self.managed_root, staging_path)
            resolved.unlink()
        self.staging_path = None

    def finalize(self) -> None:
        self._assert_identity()
        assert self.final_path is not None
        assert self.staging_path is not None
        assert self.expected_sha256 is not None
        assert self.expected_codepoint_count is not None
        if _path_entry_exists(self.final_path):
            raise ValueError("The private audition script destination already exists.")
        resolved_staging, _text = _verified_script_text(
            self.managed_root,
            self.staging_path,
            expected_sha256=self.expected_sha256,
            expected_codepoint_count=self.expected_codepoint_count,
        )
        _verified_storage_path(
            self.managed_root,
            self.final_path.parent,
            require_directory=True,
        )
        os.replace(resolved_staging, self.final_path)
        self.staging_path = None
        _verified_script_text(
            self.managed_root,
            self.final_path,
            expected_sha256=self.expected_sha256,
            expected_codepoint_count=self.expected_codepoint_count,
        )


class AuditionRepository:
    """Own Phase 3B evidence, private artifacts, and managed speech runtimes."""

    def __init__(
        self,
        database: Database,
        settings: ServiceSettings,
        *,
        runtime_factory: RuntimeFactory = ManagedSpeechRuntime,
        model_package_manager: ModelPackageManager | None = None,
        story_intelligence: StoryIntelligenceRepository | None = None,
        casting: CastingRepository | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.data_dir = settings.data_dir.resolve(strict=False)
        if self.data_dir != database.path.parent.resolve(strict=False):
            raise ValueError("Audition storage must share the database's managed data root.")
        self._runtime_factory = runtime_factory
        self._model_action_lock = threading.RLock()
        self._model_staging_root = ensure_private_directory(
            self.data_dir / "model-staging"
        ).resolve(strict=True)
        self._model_package_manager = model_package_manager or ModelPackageManager(
            self.data_dir / "models"
        )
        self._story_intelligence = story_intelligence or StoryIntelligenceRepository(
            database,
            ProjectRepository(database),
        )
        self._casting = casting or CastingRepository(
            database,
            ProjectRepository(database),
            self._story_intelligence,
        )
        self._runtime_lock = threading.RLock()
        self._review_decision_lock = threading.RLock()
        self._runtimes: dict[str, tuple[ManagedSpeechRuntime, str]] = {}
        self._quarantined_runtimes: dict[str, _QuarantinedRuntime] = {}
        self._runtime_shutdown_started = threading.Event()
        self._owned_runtime_instance_ids: set[str] = set()
        self._runtime_exit_records: dict[
            str,
            tuple[SpeechRuntimeExitEvidence, dict[str, Any]],
        ] = {}
        self._runtime_shutdown_evidence_path = self.data_dir / _RUNTIME_SHUTDOWN_EVIDENCE_FILENAME
        if self.settings.runtime_shutdown_evidence_enabled:
            try:
                if _path_entry_exists(self._runtime_shutdown_evidence_path):
                    prior = _verified_storage_path(
                        self.data_dir,
                        self._runtime_shutdown_evidence_path,
                    )
                    if not prior.is_file():
                        raise ValueError
                    prior.unlink()
            except (OSError, RuntimeError, ValueError) as exc:
                raise ServiceError(
                    500,
                    "RUNTIME_SHUTDOWN_EVIDENCE_RESET_FAILED",
                    "The bounded runtime shutdown evidence channel is unavailable.",
                ) from exc
        self.ensure_model_packages()
        self._reconcile_runtime_instances()
        if isinstance(self._model_package_manager, ModelPackageManager):
            self._reconcile_model_package_storage()
        self._reconcile_script_storage()
        self._reconcile_audio_storage()

    def _reconcile_runtime_instances(self) -> None:
        """Fail stale live-state rows closed without touching any process identity."""

        now = utc_now()
        with self.database.immediate_session() as session:
            rows = list(
                session.scalars(
                    select(SpeechRuntimeInstanceRow)
                    .where(
                        SpeechRuntimeInstanceRow.state.in_(
                            ("starting", "ready", "busy", "idle", "stopping")
                        )
                    )
                    .order_by(SpeechRuntimeInstanceRow.id)
                    .limit(_MAX_RUNTIME_STARTUP_RECONCILIATION_RECORDS + 1)
                )
            )
            if len(rows) > _MAX_RUNTIME_STARTUP_RECONCILIATION_RECORDS:
                raise ServiceError(
                    500,
                    "RUNTIME_STARTUP_RECONCILIATION_BOUNDS_EXCEEDED",
                    "Persisted speech-runtime reconciliation exceeded its fixed bound.",
                )
            for row in rows:
                prior_state = row.state
                warnings = parse_json(row.warnings_json, {})
                if not isinstance(warnings, dict):
                    warnings = {"priorWarnings": warnings if isinstance(warnings, list) else []}
                warnings.pop("exitEvidence", None)
                warnings["stopReasonCode"] = "service_restart_interrupted"
                warnings["restartReconciliation"] = {
                    "contractVersion": "1.0.0",
                    "gracefulShutdownConfirmed": False,
                    "observerServiceInstanceId": self.settings.instance_id,
                    "observedAt": now,
                    "ownershipConfirmed": False,
                    "priorState": prior_state,
                    "processExitConfirmed": False,
                    "reasonCode": "SERVICE_RESTART_INTERRUPTED",
                }
                row.warnings_json = canonical_json(warnings)
                row.state = "failed"
                row.health_status = "unavailable"
                row.exit_code = None
                row.last_health_at = now
                row.stopped_at = None

    @staticmethod
    def _unconfirmed_runtime_exit_evidence(
        row: SpeechRuntimeInstanceRow,
        warnings: Mapping[str, Any],
    ) -> SpeechRuntimeExitEvidence:
        job_object_assigned = warnings.get("jobObjectAssigned") is True
        denied_network_attempt_count = warnings.get("deniedNetworkAttemptCount", 0)
        if (
            not isinstance(denied_network_attempt_count, int)
            or isinstance(denied_network_attempt_count, bool)
            or not 0 <= denied_network_attempt_count <= 1_000_000
        ):
            denied_network_attempt_count = 0
        return SpeechRuntimeExitEvidence(
            pid=row.worker_pid,
            launcher_pid=row.worker_pid,
            exit_code=None,
            reason="process_error",
            ownership_confirmed=False,
            shutdown_acknowledged=False,
            graceful_shutdown_confirmed=False,
            terminated_by_parent=False,
            confirmed_exited=False,
            job_object_assigned=job_object_assigned,
            owned_processes_confirmed_exited=False,
            denied_network_attempt_count=denied_network_attempt_count,
        )

    def _reconcile_model_package_storage(self) -> None:
        """Resolve both commit windows for exact owned removal tombstones."""

        try:
            staged_removals = self._model_package_manager.staged_removals(
                KOKORO_LOCAL_ONNX_MANIFEST
            )
            if not staged_removals:
                return
            with self.database.session() as session:
                manifest = session.scalar(
                    select(ModelPackageManifestRow).where(
                        ModelPackageManifestRow.manifest_fingerprint
                        == KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
                    )
                )
                latest = (
                    self._latest_installation(session, manifest.id)
                    if manifest is not None
                    else None
                )
                committed_removed = latest is not None and latest.state == "removed"
            for staged in staged_removals:
                if committed_removed:
                    self._model_package_manager.commit_staged_removal(
                        staged,
                        KOKORO_LOCAL_ONNX_MANIFEST,
                    )
                else:
                    self._model_package_manager.rollback_staged_removal(
                        staged,
                        KOKORO_LOCAL_ONNX_MANIFEST,
                    )
        except (ModelPackageError, OSError, RuntimeError, ValueError) as exc:
            raise ServiceError(
                500,
                "MODEL_PACKAGE_STORAGE_RECONCILIATION_FAILED",
                "Managed model package storage reconciliation failed safely.",
            ) from exc

    @contextmanager
    def _script_storage_session(
        self,
        publication: _PendingScriptPublication,
    ) -> Iterator[Session]:
        try:
            with self.database.immediate_session() as session:
                yield session
        except Exception:
            try:
                publication.discard()
            except (OSError, RuntimeError, ValueError) as cleanup_exc:
                raise ServiceError(
                    500,
                    "AUDITION_SCRIPT_STORAGE_COMPENSATION_FAILED",
                    "Private audition script storage compensation failed safely.",
                ) from cleanup_exc
            raise

    def _reconcile_script_storage(self) -> None:
        """Reconcile only exact owned script staging and final artifacts."""

        try:
            with self.database.session() as session:
                rows = list(
                    session.scalars(
                        select(AuditionScriptRow)
                        .order_by(AuditionScriptRow.id)
                        .limit(_MAX_SCRIPT_STORAGE_RECONCILIATION_RECORDS + 1)
                    )
                )
            if len(rows) > _MAX_SCRIPT_STORAGE_RECONCILIATION_RECORDS:
                raise ValueError("Private audition script reconciliation is out of bounds.")

            rows_by_record_id: dict[str, AuditionScriptRow] = {}
            rows_by_storage_key: dict[str, AuditionScriptRow] = {}
            expected_paths: dict[str, Path] = {}
            for row in rows:
                if row.text_storage_key is None:
                    continue
                relative = Path(row.text_storage_key)
                if (
                    relative.is_absolute()
                    or relative.drive
                    or len(relative.parts) != 5
                    or relative.parts[:4] != ("projects", row.project_id, "auditions", "scripts")
                    or _OWNED_SCRIPT_FILE_NAME.fullmatch(relative.name) is None
                    or row.id in rows_by_record_id
                    or row.text_storage_key in rows_by_storage_key
                ):
                    raise ValueError("A committed audition script storage key is invalid.")
                rows_by_record_id[row.id] = row
                rows_by_storage_key[row.text_storage_key] = row
                expected_paths[row.id] = resolve_beneath(
                    self.data_dir,
                    relative,
                )

            # Inventory the complete managed scripts scope before any unlink or
            # replacement. Unknown regular entries are preserved; every reparse
            # point in the managed hierarchy fails startup safely.
            scanned_entries = len(rows)
            candidates: list[Path] = []

            def count_entry() -> None:
                nonlocal scanned_entries
                scanned_entries += 1
                if scanned_entries > _MAX_SCRIPT_STORAGE_RECONCILIATION_RECORDS:
                    raise ValueError("Private audition script reconciliation is out of bounds.")

            projects_root = self.data_dir / "projects"
            if _path_entry_exists(projects_root):
                count_entry()
                verified_projects = _verified_storage_path(
                    self.data_dir,
                    projects_root,
                    require_directory=True,
                )
                for project_entry in verified_projects.iterdir():
                    count_entry()
                    metadata = project_entry.lstat()
                    attributes = int(getattr(metadata, "st_file_attributes", 0))
                    if stat.S_ISLNK(metadata.st_mode) or bool(
                        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    ):
                        raise ValueError("Managed audition storage contains a reparse point.")
                    if not stat.S_ISDIR(metadata.st_mode):
                        continue
                    verified_project = _verified_storage_path(
                        self.data_dir,
                        project_entry,
                        require_directory=True,
                    )
                    auditions_directory = verified_project / "auditions"
                    if not _path_entry_exists(auditions_directory):
                        continue
                    count_entry()
                    verified_auditions = _verified_storage_path(
                        self.data_dir,
                        auditions_directory,
                        require_directory=True,
                    )
                    scripts_directory = verified_auditions / "scripts"
                    if not _path_entry_exists(scripts_directory):
                        continue
                    count_entry()
                    verified_scripts = _verified_storage_path(
                        self.data_dir,
                        scripts_directory,
                        require_directory=True,
                    )
                    for candidate in verified_scripts.iterdir():
                        count_entry()
                        candidate_metadata = candidate.lstat()
                        candidate_attributes = int(
                            getattr(candidate_metadata, "st_file_attributes", 0)
                        )
                        if stat.S_ISLNK(candidate_metadata.st_mode) or bool(
                            candidate_attributes
                            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                        ):
                            raise ValueError("Managed audition storage contains a reparse point.")
                        candidates.append(candidate)

            delete_paths: list[Path] = []
            restore_paths: list[tuple[Path, Path]] = []
            verified_record_ids: set[str] = set()
            staged_record_ids: set[str] = set()
            for candidate in candidates:
                staging_match = _OWNED_SCRIPT_STAGING_FILE_NAME.fullmatch(candidate.name)
                if staging_match is not None:
                    resolved_staging = _verified_storage_path(self.data_dir, candidate)
                    record_id = staging_match.group("script_record_id")
                    committed_row = rows_by_record_id.get(record_id)
                    if committed_row is None:
                        delete_paths.append(resolved_staging)
                        continue
                    final_path = candidate.parent / f"{staging_match.group('storage_id')}.utf8"
                    if (
                        expected_paths.get(committed_row.id) != final_path
                        or record_id in staged_record_ids
                    ):
                        raise ValueError("An owned script staging file did not match its record.")
                    staged_record_ids.add(record_id)
                    _verified_script_text(
                        self.data_dir,
                        resolved_staging,
                        expected_sha256=committed_row.exact_text_sha256,
                        expected_codepoint_count=committed_row.text_codepoint_count,
                    )
                    if _path_entry_exists(final_path):
                        _verified_script_text(
                            self.data_dir,
                            final_path,
                            expected_sha256=committed_row.exact_text_sha256,
                            expected_codepoint_count=committed_row.text_codepoint_count,
                        )
                        verified_record_ids.add(record_id)
                        delete_paths.append(resolved_staging)
                    else:
                        restore_paths.append((resolved_staging, final_path))
                        verified_record_ids.add(record_id)
                    continue

                if _OWNED_SCRIPT_FILE_NAME.fullmatch(candidate.name) is None:
                    continue
                resolved_final = _verified_storage_path(self.data_dir, candidate)
                storage_key = resolved_final.relative_to(self.data_dir).as_posix()
                committed_row = rows_by_storage_key.get(storage_key)
                if committed_row is None:
                    delete_paths.append(resolved_final)
                    continue
                _verified_script_text(
                    self.data_dir,
                    resolved_final,
                    expected_sha256=committed_row.exact_text_sha256,
                    expected_codepoint_count=committed_row.text_codepoint_count,
                )
                verified_record_ids.add(committed_row.id)

            if set(expected_paths) != verified_record_ids:
                raise ValueError("A committed audition script artifact is unavailable.")

            for path in delete_paths:
                _verified_storage_path(self.data_dir, path).unlink()
            for staging_path, final_path in restore_paths:
                os.replace(_verified_storage_path(self.data_dir, staging_path), final_path)
                row = rows_by_storage_key[final_path.relative_to(self.data_dir).as_posix()]
                _verified_script_text(
                    self.data_dir,
                    final_path,
                    expected_sha256=row.exact_text_sha256,
                    expected_codepoint_count=row.text_codepoint_count,
                )
        except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
            raise ServiceError(
                500,
                "AUDITION_SCRIPT_STORAGE_RECONCILIATION_FAILED",
                "Private audition script storage reconciliation failed safely.",
            ) from exc

    def _reconcile_audio_storage(self) -> None:
        """Remove owned staging/orphan files and invalidate missing committed audio."""

        try:
            with self.database.session() as session:
                artifact_ids = list(
                    session.scalars(
                        select(AudioArtifactRow.id)
                        .order_by(AudioArtifactRow.id)
                        .limit(_MAX_AUDIO_STORAGE_RECONCILIATION_ENTRIES + 1)
                    )
                )
            if len(artifact_ids) > _MAX_AUDIO_STORAGE_RECONCILIATION_ENTRIES:
                raise ValueError("Private audition audio reconciliation is out of bounds.")

            scanned_entries = len(artifact_ids)
            staging_entries: list[Path] = []
            audio_entries: list[Path] = []

            def collect_entries(pattern: str, destination: list[Path]) -> None:
                nonlocal scanned_entries
                for directory in self.data_dir.glob(pattern):
                    scanned_entries += 1
                    if scanned_entries > _MAX_AUDIO_STORAGE_RECONCILIATION_ENTRIES:
                        raise ValueError("Private audition audio reconciliation is out of bounds.")
                    resolved_directory = _verified_storage_path(
                        self.data_dir,
                        directory,
                        require_directory=True,
                    )
                    for candidate in resolved_directory.iterdir():
                        scanned_entries += 1
                        if scanned_entries > _MAX_AUDIO_STORAGE_RECONCILIATION_ENTRIES:
                            raise ValueError(
                                "Private audition audio reconciliation is out of bounds."
                            )
                        destination.append(candidate)

            # Inventory the entire bounded scope before changing any file or row. If
            # the bound is exceeded, startup fails without partial reconciliation.
            collect_entries("projects/*/auditions/audio-staging", staging_entries)
            collect_entries("projects/*/auditions/audio", audio_entries)

            for staging_path in staging_entries:
                if not (
                    _OWNED_STAGING_FILE_NAME.fullmatch(staging_path.name)
                    or _OWNED_ATOMIC_STAGING_TEMP_NAME.fullmatch(staging_path.name)
                ):
                    continue
                resolved = _verified_storage_path(self.data_dir, staging_path)
                resolved.unlink()
            with self.database.immediate_session() as session:
                artifacts = list(
                    session.scalars(
                        select(AudioArtifactRow)
                        .order_by(AudioArtifactRow.id)
                        .limit(_MAX_AUDIO_STORAGE_RECONCILIATION_ENTRIES + 1)
                    )
                )
                if [artifact.id for artifact in artifacts] != artifact_ids:
                    raise ValueError("Private audition audio reconciliation scope changed.")
                artifacts_by_id = {artifact.id: artifact for artifact in artifacts}
                committed_storage = {artifact.storage_key for artifact in artifacts}
                for audio_path in audio_entries:
                    tombstone_match = _OWNED_AUDIO_PURGE_TOMBSTONE_NAME.fullmatch(audio_path.name)
                    if tombstone_match is not None:
                        resolved_tombstone = _verified_storage_path(
                            self.data_dir,
                            audio_path,
                        )
                        artifact_id = tombstone_match.group("artifact_id")
                        artifact = artifacts_by_id.get(artifact_id)
                        original_path = audio_path.parent / f"{artifact_id}.wav"
                        expected_storage_key = original_path.relative_to(self.data_dir).as_posix()
                        if artifact is not None and artifact.availability != "purged":
                            if artifact.storage_key != expected_storage_key:
                                raise ValueError(
                                    "An owned cache tombstone did not match its artifact."
                                )
                            if _path_entry_exists(original_path):
                                raise ValueError(
                                    "An owned cache tombstone collided with restored audio."
                                )
                            resolved_tombstone.rename(original_path)
                            _verified_storage_path(self.data_dir, original_path)
                        else:
                            resolved_tombstone.unlink()
                        continue

                    if _OWNED_AUDIO_FILE_NAME.fullmatch(audio_path.name) is None:
                        continue
                    resolved = _verified_storage_path(self.data_dir, audio_path)
                    relative = resolved.relative_to(self.data_dir).as_posix()
                    if relative not in committed_storage:
                        resolved.unlink()
                for artifact in artifacts:
                    if artifact.availability != "present":
                        continue
                    try:
                        self._resolve_verified_artifact_path(artifact.storage_key)
                    except ServiceError:
                        artifact.availability = "corrupt"
                        self._invalidate_artifact_integrity(
                            session,
                            artifact=artifact,
                            reason_code="AUDITION_AUDIO_MISSING",
                            rationale="Approved audition audio is missing after restart.",
                        )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError(
                500,
                "AUDITION_STORAGE_RECONCILIATION_FAILED",
                "Private audition storage reconciliation failed safely.",
            ) from exc

    def ensure_model_packages(self) -> None:
        """Seed trusted manifest and runtime-profile metadata, never installations."""

        now = utc_now()
        media_classification = {
            "model": "onnx",
            "voice": "voice_data",
            "configuration": "configuration",
            "tokenizer": "tokenizer",
        }
        kokoro_files = tuple(
            {
                "byteSize": artifact.size_bytes,
                "executable": False,
                "mediaClassification": media_classification[artifact.role],
                "relativePath": artifact.path,
                "sha256": artifact.sha256,
            }
            for artifact in KOKORO_LOCAL_ONNX_MANIFEST.artifacts
        )
        seeds = (
            {
                "id": stable_id("phase3b-model-manifest", _FIXTURE_PACKAGE_ID),
                "package_id": _FIXTURE_PACKAGE_ID,
                "manifest_version": "1.0.0",
                "provider_id": FIXTURE_PROVIDER_ID,
                "provider_version": FIXTURE_ADAPTER_VERSION,
                "model_id": _FIXTURE_MODEL_ID,
                "model_version": _FIXTURE_MODEL_VERSION,
                "runtime_id": _FIXTURE_RUNTIME_ID,
                "runtime_version": _FIXTURE_RUNTIME_VERSION,
                "platform": "windows",
                "architecture": "x64",
                "source_classification": "repository_fixture",
                "official_source_reference": "repository://deterministic-pcm-wav-fixture",
                "license_identifier": "repository-test-fixture",
                "commercial_use_classification": "fixture_only",
                "attribution_requirements": (),
                "required_runtime_dependencies": (),
                "compatibility_constraints": (
                    "platform:windows",
                    "architecture:x64",
                    f"runtime:{_FIXTURE_RUNTIME_VERSION}",
                ),
                "revocation_state": "active",
                "files": _FIXTURE_FILE_INVENTORY,
                "total_size": 1,
                "fingerprint": _FIXTURE_MANIFEST_FINGERPRINT,
                "origin": "fixture_provider",
            },
            {
                "id": stable_id(
                    "phase3b-model-manifest",
                    KOKORO_LOCAL_ONNX_MANIFEST.package_id,
                ),
                "package_id": KOKORO_LOCAL_ONNX_MANIFEST.package_id,
                "manifest_version": KOKORO_LOCAL_ONNX_MANIFEST.schema_version,
                "provider_id": KOKORO_LOCAL_ONNX_MANIFEST.provider_id,
                "provider_version": KOKORO_LOCAL_ONNX_MANIFEST.provider_version,
                "model_id": KOKORO_LOCAL_ONNX_MANIFEST.model_id,
                "model_version": KOKORO_LOCAL_ONNX_MANIFEST.model_version,
                "runtime_id": KOKORO_LOCAL_ONNX_MANIFEST.runtime_id,
                "runtime_version": KOKORO_LOCAL_ONNX_MANIFEST.runtime_version,
                "platform": KOKORO_LOCAL_ONNX_MANIFEST.platform,
                "architecture": KOKORO_LOCAL_ONNX_MANIFEST.architecture,
                "source_classification": (KOKORO_LOCAL_ONNX_MANIFEST.source_classification),
                "official_source_reference": (KOKORO_LOCAL_ONNX_MANIFEST.official_source_reference),
                "license_identifier": KOKORO_LOCAL_ONNX_MANIFEST.license_id,
                "commercial_use_classification": (
                    KOKORO_LOCAL_ONNX_MANIFEST.commercial_use_classification
                ),
                "attribution_requirements": (KOKORO_LOCAL_ONNX_MANIFEST.attribution_requirements),
                "required_runtime_dependencies": (
                    KOKORO_LOCAL_ONNX_MANIFEST.required_runtime_dependencies
                ),
                "compatibility_constraints": (KOKORO_LOCAL_ONNX_MANIFEST.compatibility_constraints),
                "revocation_state": KOKORO_LOCAL_ONNX_MANIFEST.revocation_state,
                "files": kokoro_files,
                "total_size": KOKORO_LOCAL_ONNX_MANIFEST.total_size_bytes,
                "fingerprint": KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
                "origin": "application",
                "provenance": canonical_json(
                    {
                        **KOKORO_LOCAL_ONNX_MANIFEST.provenance.to_dict(),
                        "origin": "application",
                        "producerId": _PRODUCER_ID,
                        "producerVersion": _PRODUCER_VERSION,
                        "recordedAt": now,
                    }
                ),
            },
        )
        profiles = (
            {
                "id": _LEGACY_FIXTURE_PROFILE_RECORD_ID,
                "insert_if_missing": False,
                "profile_id": _LEGACY_FIXTURE_PROFILE_ID,
                "profile_version": _LEGACY_RUNTIME_PROFILE_VERSION,
                "provider_id": FIXTURE_PROVIDER_ID,
                "provider_version": FIXTURE_ADAPTER_VERSION,
                "runtime_id": _FIXTURE_RUNTIME_ID,
                "runtime_version": _FIXTURE_RUNTIME_VERSION,
                "startup_timeout_ms": _LEGACY_RUNTIME_STARTUP_TIMEOUT_MS,
                "maximum_content_tokens": None,
                "fingerprint": _LEGACY_FIXTURE_PROFILE_FINGERPRINT,
                "origin": "fixture_provider",
            },
            {
                "id": _LEGACY_KOKORO_PROFILE_RECORD_ID,
                "insert_if_missing": False,
                "profile_id": _LEGACY_KOKORO_PROFILE_ID,
                "profile_version": _LEGACY_RUNTIME_PROFILE_VERSION,
                "provider_id": KOKORO_PROVIDER_ID,
                "provider_version": KOKORO_ADAPTER_VERSION,
                "runtime_id": "onnxruntime-cpu",
                "runtime_version": "1.28.0",
                "startup_timeout_ms": _LEGACY_RUNTIME_STARTUP_TIMEOUT_MS,
                "maximum_content_tokens": None,
                "fingerprint": _LEGACY_KOKORO_PROFILE_FINGERPRINT,
                "origin": "application",
            },
            {
                "id": _FIXTURE_PROFILE_RECORD_ID,
                "insert_if_missing": True,
                "profile_id": _FIXTURE_PROFILE_ID,
                "profile_version": _RUNTIME_PROFILE_VERSION,
                "provider_id": FIXTURE_PROVIDER_ID,
                "provider_version": FIXTURE_ADAPTER_VERSION,
                "runtime_id": _FIXTURE_RUNTIME_ID,
                "runtime_version": _FIXTURE_RUNTIME_VERSION,
                "startup_timeout_ms": _RUNTIME_STARTUP_TIMEOUT_MS,
                "maximum_content_tokens": None,
                "fingerprint": _FIXTURE_PROFILE_FINGERPRINT,
                "origin": "fixture_provider",
            },
            {
                "id": _PREVIOUS_KOKORO_PROFILE_RECORD_ID,
                "insert_if_missing": False,
                "profile_id": _PREVIOUS_KOKORO_PROFILE_ID,
                "profile_version": _RUNTIME_PROFILE_VERSION,
                "provider_id": KOKORO_PROVIDER_ID,
                "provider_version": KOKORO_ADAPTER_VERSION,
                "runtime_id": "onnxruntime-cpu",
                "runtime_version": "1.28.0",
                "startup_timeout_ms": _RUNTIME_STARTUP_TIMEOUT_MS,
                "maximum_content_tokens": None,
                "fingerprint": _PREVIOUS_KOKORO_PROFILE_FINGERPRINT,
                "origin": "application",
            },
            {
                "id": _KOKORO_PROFILE_RECORD_ID,
                "insert_if_missing": True,
                "profile_id": _KOKORO_PROFILE_ID,
                "profile_version": _KOKORO_RUNTIME_PROFILE_VERSION,
                "provider_id": KOKORO_PROVIDER_ID,
                "provider_version": KOKORO_ADAPTER_VERSION,
                "runtime_id": "onnxruntime-cpu",
                "runtime_version": "1.28.0",
                "startup_timeout_ms": _RUNTIME_STARTUP_TIMEOUT_MS,
                "maximum_content_tokens": KOKORO_MAX_CONTENT_TOKENS,
                "fingerprint": _KOKORO_PROFILE_FINGERPRINT,
                "origin": "application",
            },
        )
        with self.database.immediate_session() as session:
            for seed in seeds:
                attribution_requirements = cast(
                    Sequence[str],
                    seed["attribution_requirements"],
                )
                required_runtime_dependencies = cast(
                    Sequence[str],
                    seed["required_runtime_dependencies"],
                )
                compatibility_constraints = cast(
                    Sequence[str],
                    seed["compatibility_constraints"],
                )
                seed_provenance_json = cast(
                    str,
                    seed.get("provenance")
                    or canonical_json(
                        {
                            "origin": str(seed["origin"]),
                            "producerId": _PRODUCER_ID,
                            "producerVersion": _PRODUCER_VERSION,
                            "recordedAt": now,
                        }
                    ),
                )
                existing = session.get(ModelPackageManifestRow, str(seed["id"]))
                if existing is not None:
                    expected_provenance = parse_json(seed_provenance_json, {})
                    persisted_provenance = parse_json(existing.provenance_json, {})
                    if not isinstance(expected_provenance, dict):
                        expected_provenance = {}
                    if not isinstance(persisted_provenance, dict):
                        persisted_provenance = {}
                    expected_provenance.pop("recordedAt", None)
                    persisted_recorded_at = persisted_provenance.pop(
                        "recordedAt",
                        None,
                    )
                    expected_projection = {
                        "architecture": str(seed["architecture"]),
                        "attributionRequirements": list(attribution_requirements),
                        "commercialUseClassification": str(seed["commercial_use_classification"]),
                        "compatibilityConstraints": list(compatibility_constraints),
                        "fileCount": len(cast(Sequence[object], seed["files"])),
                        "fileInventory": parse_json(
                            canonical_json(seed["files"]),
                            [],
                        ),
                        "id": str(seed["id"]),
                        "licenseIdentifier": str(seed["license_identifier"]),
                        "manifestFingerprint": str(seed["fingerprint"]),
                        "manifestVersion": str(seed["manifest_version"]),
                        "modelId": str(seed["model_id"]),
                        "modelVersion": str(seed["model_version"]),
                        "officialSourceReference": str(seed["official_source_reference"]),
                        "packageArchiveSha256": None,
                        "packageId": str(seed["package_id"]),
                        "platform": str(seed["platform"]),
                        "provenance": expected_provenance,
                        "providerId": str(seed["provider_id"]),
                        "providerVersion": str(seed["provider_version"]),
                        "requiredRuntimeDependencies": list(required_runtime_dependencies),
                        "revocationState": str(seed["revocation_state"]),
                        "runtimeId": str(seed["runtime_id"]),
                        "runtimeVersion": str(seed["runtime_version"]),
                        "sourceClassification": str(seed["source_classification"]),
                        "totalExpandedSize": cast(int, seed["total_size"]),
                    }
                    persisted_projection = {
                        "architecture": existing.architecture,
                        "attributionRequirements": parse_json(
                            existing.attribution_requirements_json,
                            None,
                        ),
                        "commercialUseClassification": (existing.commercial_use_classification),
                        "compatibilityConstraints": parse_json(
                            existing.compatibility_constraints_json,
                            None,
                        ),
                        "fileCount": existing.file_count,
                        "fileInventory": parse_json(
                            existing.file_inventory_json,
                            None,
                        ),
                        "id": existing.id,
                        "licenseIdentifier": existing.license_identifier,
                        "manifestFingerprint": existing.manifest_fingerprint,
                        "manifestVersion": existing.manifest_version,
                        "modelId": existing.model_id,
                        "modelVersion": existing.model_version,
                        "officialSourceReference": (existing.official_source_reference),
                        "packageArchiveSha256": existing.package_archive_sha256,
                        "packageId": existing.package_id,
                        "platform": existing.platform,
                        "provenance": persisted_provenance,
                        "providerId": existing.provider_id,
                        "providerVersion": existing.provider_version,
                        "requiredRuntimeDependencies": parse_json(
                            existing.required_runtime_dependencies_json,
                            None,
                        ),
                        "revocationState": existing.revocation_state,
                        "runtimeId": existing.runtime_id,
                        "runtimeVersion": existing.runtime_version,
                        "sourceClassification": existing.source_classification,
                        "totalExpandedSize": existing.total_expanded_size,
                    }
                    if (
                        persisted_projection != expected_projection
                        or persisted_recorded_at != existing.created_at
                    ):
                        raise ServiceError(
                            503,
                            "MODEL_MANIFEST_CONFLICT",
                            "Trusted model manifest metadata failed verification.",
                        )
                    continue
                session.add(
                    ModelPackageManifestRow(
                        id=str(seed["id"]),
                        package_id=str(seed["package_id"]),
                        manifest_version=str(seed["manifest_version"]),
                        provider_id=str(seed["provider_id"]),
                        provider_version=str(seed["provider_version"]),
                        model_id=str(seed["model_id"]),
                        model_version=str(seed["model_version"]),
                        runtime_id=str(seed["runtime_id"]),
                        runtime_version=str(seed["runtime_version"]),
                        platform=str(seed["platform"]),
                        architecture=str(seed["architecture"]),
                        source_classification=str(seed["source_classification"]),
                        official_source_reference=str(seed["official_source_reference"]),
                        license_identifier=str(seed["license_identifier"]),
                        commercial_use_classification=str(seed["commercial_use_classification"]),
                        attribution_requirements_json=canonical_json(attribution_requirements),
                        file_inventory_json=canonical_json(seed["files"]),
                        file_count=len(cast(Sequence[object], seed["files"])),
                        total_expanded_size=cast(int, seed["total_size"]),
                        package_archive_sha256=None,
                        required_runtime_dependencies_json=canonical_json(
                            required_runtime_dependencies
                        ),
                        compatibility_constraints_json=canonical_json(compatibility_constraints),
                        revocation_state=str(seed["revocation_state"]),
                        manifest_fingerprint=str(seed["fingerprint"]),
                        provenance_json=seed_provenance_json,
                        created_at=now,
                    )
                )
            for profile in profiles:
                existing_profile = session.get(SpeechRuntimeProfileRow, str(profile["id"]))
                if existing_profile is not None:
                    expected_fingerprint_material = _runtime_profile_fingerprint_material(
                        profile_id=str(profile["profile_id"]),
                        profile_version=str(profile["profile_version"]),
                        provider_id=str(profile["provider_id"]),
                        provider_version=str(profile["provider_version"]),
                        runtime_id=str(profile["runtime_id"]),
                        runtime_version=str(profile["runtime_version"]),
                        startup_timeout_ms=cast(int, profile["startup_timeout_ms"]),
                        maximum_content_tokens=cast(
                            int | None,
                            profile["maximum_content_tokens"],
                        ),
                    )
                    expected_provenance = {
                        "origin": str(profile["origin"]),
                        "producerId": _PRODUCER_ID,
                        "producerVersion": _PRODUCER_VERSION,
                        "recordedAt": existing_profile.created_at,
                    }
                    expected_limits = {
                        "maximumAudioBytes": _MAX_AUDIO_BYTES,
                        "maximumDurationMilliseconds": 30_000,
                        "maximumRetryAttempts": 0,
                        "maximumScriptCodePoints": 4_000,
                    }
                    if profile["maximum_content_tokens"] is not None:
                        expected_limits["maximumContentTokens"] = cast(
                            int,
                            profile["maximum_content_tokens"],
                        )
                    expected_projection = {
                        "active": True,
                        "architecture": "x64",
                        "createdAt": existing_profile.created_at,
                        "idleShutdownMilliseconds": 120_000,
                        "limits": expected_limits,
                        "maximumConcurrency": 1,
                        "networkPolicy": "deny_during_synthesis",
                        "outputFormats": ["pcm_s16le_wav"],
                        "platform": "windows",
                        "profileFingerprint": str(profile["fingerprint"]),
                        "profileId": str(profile["profile_id"]),
                        "profileRecordId": str(profile["id"]),
                        "profileVersion": str(profile["profile_version"]),
                        "protocolVersion": SPEECH_RUNTIME_PROTOCOL_VERSION,
                        "provenance": expected_provenance,
                        "providerId": str(profile["provider_id"]),
                        "providerVersion": str(profile["provider_version"]),
                        "requestDeadlineMilliseconds": 60_000,
                        "runtimeId": str(profile["runtime_id"]),
                        "runtimeVersion": str(profile["runtime_version"]),
                        "startupDeadlineMilliseconds": cast(
                            int,
                            profile["startup_timeout_ms"],
                        ),
                    }
                    persisted_projection = {
                        "active": existing_profile.active,
                        "architecture": existing_profile.architecture,
                        "createdAt": existing_profile.created_at,
                        "idleShutdownMilliseconds": (existing_profile.idle_shutdown_ms),
                        "limits": parse_json(existing_profile.limits_json, None),
                        "maximumConcurrency": existing_profile.maximum_concurrency,
                        "networkPolicy": existing_profile.network_policy,
                        "outputFormats": parse_json(
                            existing_profile.output_format_json,
                            None,
                        ),
                        "platform": existing_profile.platform,
                        "profileFingerprint": existing_profile.profile_fingerprint,
                        "profileId": existing_profile.profile_id,
                        "profileRecordId": existing_profile.id,
                        "profileVersion": existing_profile.profile_version,
                        "protocolVersion": existing_profile.protocol_version,
                        "provenance": parse_json(existing_profile.provenance_json, None),
                        "providerId": existing_profile.provider_id,
                        "providerVersion": existing_profile.provider_version,
                        "requestDeadlineMilliseconds": (existing_profile.request_timeout_ms),
                        "runtimeId": existing_profile.runtime_id,
                        "runtimeVersion": existing_profile.runtime_version,
                        "startupDeadlineMilliseconds": (existing_profile.startup_timeout_ms),
                    }
                    if (
                        persisted_projection != expected_projection
                        or _runtime_profile_row_fingerprint_material(existing_profile)
                        != expected_fingerprint_material
                        or request_fingerprint(expected_fingerprint_material)
                        != existing_profile.profile_fingerprint
                    ):
                        raise ServiceError(
                            503,
                            "RUNTIME_PROFILE_CONFLICT",
                            "Trusted runtime profile metadata failed verification.",
                        )
                    continue
                if profile["insert_if_missing"] is not True:
                    continue
                session.add(
                    SpeechRuntimeProfileRow(
                        id=str(profile["id"]),
                        profile_id=str(profile["profile_id"]),
                        profile_version=str(profile["profile_version"]),
                        provider_id=str(profile["provider_id"]),
                        provider_version=str(profile["provider_version"]),
                        runtime_id=str(profile["runtime_id"]),
                        runtime_version=str(profile["runtime_version"]),
                        protocol_version=SPEECH_RUNTIME_PROTOCOL_VERSION,
                        platform="windows",
                        architecture="x64",
                        network_policy="deny_during_synthesis",
                        startup_timeout_ms=cast(int, profile["startup_timeout_ms"]),
                        request_timeout_ms=60_000,
                        idle_shutdown_ms=120_000,
                        maximum_concurrency=1,
                        output_format_json=canonical_json(["pcm_s16le_wav"]),
                        limits_json=canonical_json(
                            {
                                "maximumAudioBytes": _MAX_AUDIO_BYTES,
                                "maximumDurationMilliseconds": 30_000,
                                "maximumRetryAttempts": 0,
                                "maximumScriptCodePoints": 4_000,
                                **(
                                    {
                                        "maximumContentTokens": cast(
                                            int,
                                            profile["maximum_content_tokens"],
                                        )
                                    }
                                    if profile["maximum_content_tokens"] is not None
                                    else {}
                                ),
                            }
                        ),
                        profile_fingerprint=str(profile["fingerprint"]),
                        active=True,
                        provenance_json=canonical_json(
                            {
                                "origin": str(profile["origin"]),
                                "producerId": _PRODUCER_ID,
                                "producerVersion": _PRODUCER_VERSION,
                                "recordedAt": now,
                            }
                        ),
                        created_at=now,
                    )
                )

    def list_model_packages(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        page_size = _bounded_page(limit)
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            self._reconcile_project_evidence(session, project_id)
            total = int(
                session.scalar(select(func.count()).select_from(ModelPackageManifestRow)) or 0
            )
            latest_identity = session.scalar(
                select(ModelPackageManifestRow.id)
                .order_by(
                    ModelPackageManifestRow.created_at.desc(),
                    ModelPackageManifestRow.id.desc(),
                )
                .limit(1)
            )
            binding = request_fingerprint(
                {
                    "latestId": latest_identity,
                    "projectId": project_id,
                    "total": total,
                    "type": "model-packages",
                }
            )
            offset = _decode_cursor(cursor, binding=binding)
            if offset > total:
                raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
            rows = list(
                session.scalars(
                    select(ModelPackageManifestRow)
                    .order_by(
                        ModelPackageManifestRow.provider_id,
                        ModelPackageManifestRow.model_id,
                        ModelPackageManifestRow.model_version,
                        ModelPackageManifestRow.id,
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            )
            next_offset = offset + len(rows)
            next_cursor = _encode_cursor(binding, next_offset) if next_offset < total else None
            items = [self._model_package_wire(session, row) for row in rows]
        return items, next_cursor, total

    def perform_model_package_action(
        self,
        *,
        project_id: str,
        request: ModelInstallationOperationRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._model_action_lock:
            return self._perform_model_package_action(
                project_id=project_id,
                request=request,
                actor_id=actor_id,
            )

    @contextmanager
    def _compensate_model_package_files(
        self,
        *,
        rollback: Callable[[], object],
        finalize: Callable[[], object] | None = None,
    ) -> Iterator[None]:
        """Keep filesystem state aligned with the enclosing database commit."""

        try:
            yield
        except Exception:
            try:
                rollback()
            except Exception as rollback_error:
                raise ServiceError(
                    500,
                    "MODEL_PACKAGE_ROLLBACK_FAILED",
                    "Managed model package rollback failed safely.",
                    retryable=True,
                ) from rollback_error
            raise
        else:
            if finalize is None:
                return
            try:
                finalize()
            except Exception as cleanup_error:
                raise ServiceError(
                    500,
                    "MODEL_PACKAGE_CLEANUP_FAILED",
                    "Managed model package cleanup is pending safe recovery.",
                    retryable=True,
                ) from cleanup_error

    def _remove_uncommitted_model_installation(self) -> None:
        self._model_package_manager.deactivate()
        self._model_package_manager.remove(KOKORO_LOCAL_ONNX_MANIFEST)

    def _restore_managed_model_activation(self, *, should_be_active: bool) -> None:
        if should_be_active:
            self._model_package_manager.activate(KOKORO_LOCAL_ONNX_MANIFEST)
        else:
            self._model_package_manager.deactivate()

    def _perform_model_package_action(
        self,
        *,
        project_id: str,
        request: ModelInstallationOperationRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        request_material = request.model_dump(mode="json", by_alias=True)
        request_hash = request_fingerprint(request_material | {"projectId": project_id})
        now = utc_now()
        with self.database.session() as session:
            self._require_project(session, project_id)
            manifest = self._require_model_manifest(
                session,
                package_id=request.model_package_id,
                expected_fingerprint=request.expected_manifest_fingerprint,
            )
            installation_id = stable_id("phase3b-installation", manifest.package_id)
            replay = session.scalar(
                select(ModelInstallationRow).where(
                    ModelInstallationRow.installation_id == installation_id,
                    ModelInstallationRow.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                self._assert_model_replay(replay, request_hash=request_hash)
                replay_verification = self._verification_for_installation_event(
                    session,
                    replay,
                )
                return self._model_action_wire(session, replay, replay_verification)

            current = session.scalar(
                select(ModelInstallationRow)
                .where(ModelInstallationRow.installation_id == installation_id)
                .order_by(ModelInstallationRow.revision.desc(), ModelInstallationRow.id.desc())
                .limit(1)
            )
            current_revision = current.revision if current is not None else None
            if request.expected_installation_revision != current_revision:
                raise ServiceError(
                    409,
                    "MODEL_INSTALLATION_CHANGED",
                    "The model installation revision changed; refresh before continuing.",
                )
            if request.action == "repair":
                raise ServiceError(
                    409,
                    (
                        "MODEL_REPAIR_UNAVAILABLE"
                        if manifest.provider_id == FIXTURE_PROVIDER_ID
                        else "MODEL_REPAIR_SOURCE_REQUIRED"
                    ),
                    (
                        "The deterministic fixture does not require repair."
                        if manifest.provider_id == FIXTURE_PROVIDER_ID
                        else "Repair requires a new authenticated local package archive."
                    ),
                )
            if request.action == "verify":
                if current is not None and current.state == "removed":
                    raise ServiceError(
                        409,
                        "MODEL_INSTALLATION_REMOVED",
                        "A removed model installation cannot be verified.",
                    )
            elif request.action == "activate":
                latest_verification = self._latest_verification(session, installation_id)
                if latest_verification is None or latest_verification.outcome != "verified":
                    raise ServiceError(
                        409,
                        "MODEL_VERIFICATION_REQUIRED",
                        "The model package must pass exact verification before activation.",
                    )
                if current is None or current.state not in {"installed", "inactive", "active"}:
                    raise ServiceError(
                        409,
                        "MODEL_INSTALLATION_STATE_INVALID",
                        "The model installation cannot be activated from its current state.",
                    )
            elif request.action == "deactivate":
                if current is None or current.state != "active":
                    raise ServiceError(
                        409,
                        "MODEL_INSTALLATION_STATE_INVALID",
                        "Only an active model installation can be deactivated.",
                    )
            else:
                if current is None or current.state != "inactive":
                    raise ServiceError(
                        409,
                        "MODEL_INSTALLATION_STATE_INVALID",
                        "A model installation must be inactive before removal.",
                    )
                dependent_job_id = session.scalar(
                    select(JobRow.id)
                    .join(
                        SpeechProviderRequestRow,
                        SpeechProviderRequestRow.job_id == JobRow.id,
                    )
                    .join(
                        ModelInstallationRow,
                        SpeechProviderRequestRow.model_installation_record_id
                        == ModelInstallationRow.id,
                    )
                    .where(
                        JobRow.type == "generate_audition",
                        ModelInstallationRow.installation_id == installation_id,
                        JobRow.state.in_(
                            (
                                "queued",
                                "running",
                                "cancel_requested",
                                "interrupted",
                                "paused",
                            )
                        )
                        | ((JobRow.state == "failed") & JobRow.error_retryable.is_(True)),
                    )
                    .limit(1)
                )
                if dependent_job_id is not None:
                    raise ServiceError(
                        409,
                        "MODEL_INSTALLATION_IN_USE",
                        "The model installation has active or retryable audition work.",
                        details={"jobId": dependent_job_id},
                    )
            current_revision = current.revision if current is not None else None
            current_state = current.state if current is not None else None
            manifest_id = manifest.id
            is_fixture = manifest.provider_id == FIXTURE_PROVIDER_ID

        managed_verification: ModelPackageVerification | None = None
        managed_storage_key = current.storage_key if current is not None else None
        managed_rollback: Callable[[], object] | None = None
        managed_finalize: Callable[[], object] | None = None
        if not is_fixture:
            self._assert_managed_manifest(manifest)
            try:
                if request.action == "verify":
                    managed_verification = self._model_package_manager.verify(
                        KOKORO_LOCAL_ONNX_MANIFEST
                    )
                    managed_storage_key = self._managed_storage_key(
                        managed_verification.package_path
                    )
                elif request.action == "activate":
                    package_path = self._model_package_manager.activate(KOKORO_LOCAL_ONNX_MANIFEST)
                    managed_storage_key = self._managed_storage_key(package_path)
                    managed_rollback = partial(
                        self._restore_managed_model_activation,
                        should_be_active=current_state == "active",
                    )
                elif request.action == "deactivate":
                    self._model_package_manager.deactivate()
                    managed_rollback = partial(
                        self._restore_managed_model_activation,
                        should_be_active=True,
                    )
                else:
                    staged_removal = self._model_package_manager.stage_remove(
                        KOKORO_LOCAL_ONNX_MANIFEST
                    )
                    if staged_removal is not None:
                        managed_rollback = partial(
                            self._model_package_manager.rollback_staged_removal,
                            staged_removal,
                            KOKORO_LOCAL_ONNX_MANIFEST,
                        )
                        managed_finalize = partial(
                            self._model_package_manager.commit_staged_removal,
                            staged_removal,
                            KOKORO_LOCAL_ONNX_MANIFEST,
                        )
            except ModelPackageError as exc:
                raise self._model_package_service_error(exc) from exc

        compensation = (
            self._compensate_model_package_files(
                rollback=managed_rollback,
                finalize=managed_finalize,
            )
            if managed_rollback is not None
            else nullcontext()
        )
        with compensation, self.database.immediate_session() as session:
            self._require_project(session, project_id)
            persisted_manifest = session.get(ModelPackageManifestRow, manifest_id)
            if (
                persisted_manifest is None
                or persisted_manifest.manifest_fingerprint != request.expected_manifest_fingerprint
            ):
                raise ServiceError(
                    409,
                    "MODEL_MANIFEST_CHANGED",
                    "The model package manifest changed; refresh before continuing.",
                )
            replay = session.scalar(
                select(ModelInstallationRow).where(
                    ModelInstallationRow.installation_id == installation_id,
                    ModelInstallationRow.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                self._assert_model_replay(replay, request_hash=request_hash)
                return self._model_action_wire(
                    session,
                    replay,
                    self._verification_for_installation_event(session, replay),
                )
            latest = self._latest_installation(session, persisted_manifest.id)
            latest_revision = latest.revision if latest is not None else None
            if latest_revision != current_revision:
                raise ServiceError(
                    409,
                    "MODEL_INSTALLATION_CHANGED",
                    "The model installation revision changed; refresh before continuing.",
                )
            if request.action == "verify":
                if is_fixture:
                    next_state = latest.state if latest is not None else "installed"
                    verification_outcome = "verified"
                    verified_file_count = persisted_manifest.file_count
                    verified_byte_count = persisted_manifest.total_expanded_size
                    findings: tuple[str, ...] = ()
                else:
                    assert managed_verification is not None
                    verification_outcome = self._managed_verification_outcome(managed_verification)
                    next_state = (
                        latest.state
                        if managed_verification.valid
                        and latest is not None
                        and latest.state == "active"
                        else "installed"
                        if managed_verification.valid
                        else "repair_required"
                    )
                    verified_file_count = len(managed_verification.verified_files)
                    verified_byte_count = managed_verification.total_size_bytes
                    findings = managed_verification.error_codes
            elif request.action == "activate":
                next_state = "active"
                verification_outcome = None
                findings = ()
            elif request.action == "deactivate":
                next_state = "inactive"
                verification_outcome = None
                findings = ()
            else:
                next_state = "removed"
                managed_storage_key = latest.storage_key if latest is not None else None
                verification_outcome = None
                findings = ()
            revision = (latest.revision + 1) if latest is not None else 1
            row = ModelInstallationRow(
                id=new_id(),
                installation_id=installation_id,
                manifest_id=persisted_manifest.id,
                revision=revision,
                operation=request.action,
                state=next_state,
                storage_key=managed_storage_key,
                installed_byte_count=(
                    verified_byte_count
                    if request.action == "verify"
                    else latest.installed_byte_count
                    if latest is not None
                    else persisted_manifest.total_expanded_size
                ),
                package_fingerprint=persisted_manifest.manifest_fingerprint,
                job_id=None,
                supersedes_installation_record_id=latest.id if latest is not None else None,
                actor_id=actor_id,
                reason=request.reason,
                idempotency_key=request.idempotency_key,
                warnings_json="[]",
                provenance_json=_provenance("human", input_fingerprint=request_hash),
                created_at=now,
                completed_at=now,
            )
            session.add(row)
            session.flush()
            persisted_verification: ModelVerificationRow | None
            if request.action == "verify":
                persisted_verification = self._append_model_verification(
                    session,
                    manifest=persisted_manifest,
                    installation=row,
                    outcome=cast(str, verification_outcome),
                    verified_file_count=verified_file_count,
                    verified_byte_count=verified_byte_count,
                    findings=findings,
                    request_hash=request_hash,
                    origin="fixture_provider" if is_fixture else "application",
                    now=now,
                )
            else:
                persisted_verification = self._latest_verification(
                    session,
                    installation_id,
                )
            self._reconcile_project_evidence(session, project_id)
            return self._model_action_wire(session, row, persisted_verification)

    def install_model_package(
        self,
        *,
        project_id: str,
        model_package_id: str,
        request: InstallModelPackageRequest,
        archive_path: Path,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._model_action_lock:
            return self._install_or_repair_model_package(
                operation="install",
                project_id=project_id,
                model_package_id=model_package_id,
                request=request,
                archive_path=archive_path,
                actor_id=actor_id,
            )

    def repair_model_package(
        self,
        *,
        project_id: str,
        model_package_id: str,
        request: InstallModelPackageRequest,
        archive_path: Path,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._model_action_lock:
            return self._install_or_repair_model_package(
                operation="repair",
                project_id=project_id,
                model_package_id=model_package_id,
                request=request,
                archive_path=archive_path,
                actor_id=actor_id,
            )

    def _install_or_repair_model_package(
        self,
        *,
        operation: str,
        project_id: str,
        model_package_id: str,
        request: InstallModelPackageRequest,
        archive_path: Path,
        actor_id: str,
    ) -> dict[str, Any]:
        if not bool(request.acknowledge_restricted_local_use):
            raise ServiceError(
                422,
                "RESTRICTED_MODEL_ACKNOWLEDGEMENT_REQUIRED",
                "Installing the restricted local model requires explicit acknowledgement.",
            )
        archive, archive_identity = self._model_archive_request_identity(archive_path)
        request_hash = request_fingerprint(
            request.model_dump(mode="json", by_alias=True)
            | {
                "archive": archive_identity,
                "modelPackageId": model_package_id,
                "operation": operation,
                "projectId": project_id,
            }
        )
        with self.database.session() as session:
            self._require_project(session, project_id)
            manifest = self._require_model_manifest(
                session,
                package_id=model_package_id,
                expected_fingerprint=request.expected_manifest_fingerprint,
            )
            self._assert_managed_manifest(manifest)
            installation_id = stable_id("phase3b-installation", manifest.package_id)
            replay = session.scalar(
                select(ModelInstallationRow).where(
                    ModelInstallationRow.installation_id == installation_id,
                    ModelInstallationRow.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                self._assert_model_replay(replay, request_hash=request_hash)
                return self._model_action_wire(
                    session,
                    replay,
                    self._verification_for_installation_event(session, replay),
                )
            current = self._latest_installation(session, manifest.id)
            current_revision = current.revision if current is not None else None
            if request.expected_installation_revision != current_revision:
                raise ServiceError(
                    409,
                    "MODEL_INSTALLATION_CHANGED",
                    "The model installation revision changed; refresh before continuing.",
                )
            if operation == "install":
                if current is not None and current.state != "removed":
                    raise ServiceError(
                        409,
                        "MODEL_INSTALLATION_STATE_INVALID",
                        "Only a missing or removed package can be installed.",
                    )
            elif current is None or current.state not in {
                "failed",
                "installed",
                "inactive",
                "repair_required",
            }:
                raise ServiceError(
                    409,
                    "MODEL_INSTALLATION_STATE_INVALID",
                    "The model installation cannot be repaired from its current state.",
                )
            manifest_id = manifest.id

        try:
            verification = (
                self._model_package_manager.install_from_archive(
                    archive,
                    KOKORO_LOCAL_ONNX_MANIFEST,
                )
                if operation == "install"
                else self._model_package_manager.repair(
                    archive,
                    KOKORO_LOCAL_ONNX_MANIFEST,
                )
            )
        except ModelPackageError as exc:
            raise self._model_package_service_error(exc) from exc
        compensation = (
            self._compensate_model_package_files(
                rollback=self._remove_uncommitted_model_installation,
            )
            if operation == "install"
            else nullcontext()
        )
        with compensation:
            if not verification.valid:
                raise ServiceError(
                    409,
                    "MODEL_PACKAGE_VERIFICATION_FAILED",
                    "The managed model package failed exact verification.",
                )
            return self._persist_model_package_installation(
                operation=operation,
                project_id=project_id,
                request=request,
                request_hash=request_hash,
                verification=verification,
                manifest_id=manifest_id,
                installation_id=installation_id,
                current_revision=current_revision,
                actor_id=actor_id,
            )

    def _persist_model_package_installation(
        self,
        *,
        operation: str,
        project_id: str,
        request: InstallModelPackageRequest,
        request_hash: str,
        verification: ModelPackageVerification,
        manifest_id: str,
        installation_id: str,
        current_revision: int | None,
        actor_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            persisted_manifest = session.get(ModelPackageManifestRow, manifest_id)
            if (
                persisted_manifest is None
                or persisted_manifest.manifest_fingerprint != request.expected_manifest_fingerprint
            ):
                raise ServiceError(
                    409,
                    "MODEL_MANIFEST_CHANGED",
                    "The model package manifest changed; refresh before continuing.",
                )
            replay = session.scalar(
                select(ModelInstallationRow).where(
                    ModelInstallationRow.installation_id == installation_id,
                    ModelInstallationRow.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                self._assert_model_replay(replay, request_hash=request_hash)
                return self._model_action_wire(
                    session,
                    replay,
                    self._verification_for_installation_event(session, replay),
                )
            latest = self._latest_installation(session, persisted_manifest.id)
            latest_revision = latest.revision if latest is not None else None
            if latest_revision != current_revision:
                raise ServiceError(
                    409,
                    "MODEL_INSTALLATION_CHANGED",
                    "The model installation revision changed; refresh before continuing.",
                )
            revision = (latest.revision + 1) if latest is not None else 1
            installation = ModelInstallationRow(
                id=new_id(),
                installation_id=installation_id,
                manifest_id=persisted_manifest.id,
                revision=revision,
                operation=operation,
                state="installed",
                storage_key=self._managed_storage_key(verification.package_path),
                installed_byte_count=verification.total_size_bytes,
                package_fingerprint=persisted_manifest.manifest_fingerprint,
                job_id=None,
                supersedes_installation_record_id=(latest.id if latest is not None else None),
                actor_id=actor_id,
                reason=request.reason,
                idempotency_key=request.idempotency_key,
                warnings_json="[]",
                provenance_json=_provenance(
                    "human",
                    input_fingerprint=request_hash,
                    details={
                        "acknowledgementScope": "managed_model_installation",
                        "projectId": project_id,
                        "restrictedLocalUseAcknowledged": True,
                    },
                ),
                created_at=now,
                completed_at=now,
            )
            session.add(installation)
            session.flush()
            verification_row = self._append_model_verification(
                session,
                manifest=persisted_manifest,
                installation=installation,
                outcome="verified",
                verified_file_count=len(verification.verified_files),
                verified_byte_count=verification.total_size_bytes,
                findings=verification.error_codes,
                request_hash=request_hash,
                origin="application",
                now=now,
            )
            self._reconcile_project_evidence(session, project_id)
            return self._model_action_wire(
                session,
                installation,
                verification_row,
            )

    @staticmethod
    def _require_model_manifest(
        session: Session,
        *,
        package_id: str,
        expected_fingerprint: str,
    ) -> ModelPackageManifestRow:
        manifest = session.scalar(
            select(ModelPackageManifestRow).where(ModelPackageManifestRow.package_id == package_id)
        )
        if manifest is None:
            raise not_found("model package")
        if manifest.manifest_fingerprint != expected_fingerprint:
            raise ServiceError(
                409,
                "MODEL_MANIFEST_CHANGED",
                "The model package manifest changed; refresh before continuing.",
            )
        return manifest

    @staticmethod
    def _assert_managed_manifest(manifest: ModelPackageManifestRow) -> None:
        if (
            manifest.package_id != KOKORO_LOCAL_ONNX_MANIFEST.package_id
            or manifest.manifest_fingerprint != KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
        ):
            raise ServiceError(
                409,
                "MODEL_PACKAGE_NOT_MANAGED",
                "The model package is not supported by the managed local installer.",
            )

    @staticmethod
    def _assert_model_replay(
        row: ModelInstallationRow,
        *,
        request_hash: str,
    ) -> None:
        provenance = parse_json(row.provenance_json, {})
        if not isinstance(provenance, dict) or provenance.get("inputFingerprint") != request_hash:
            raise ServiceError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "That idempotency key was already used for another model operation.",
            )

    def _validated_model_archive(self, archive_path: Path) -> Path:
        try:
            archive = _verified_storage_path(self._model_staging_root, archive_path)
            metadata = archive.stat(follow_symlinks=False)
            if (
                archive.suffix.casefold() != ".zip"
                or not stat.S_ISREG(metadata.st_mode)
                or bool(
                    getattr(metadata, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                or not 1 <= metadata.st_size <= MAX_MODEL_PACKAGE_TOTAL_BYTES
            ):
                raise ValueError
            return archive
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError(
                422,
                "MODEL_ARCHIVE_INVALID",
                "The staged model package archive failed storage verification.",
            ) from exc

    def _model_archive_request_identity(
        self,
        archive_path: Path,
    ) -> tuple[Path, dict[str, int | str]]:
        archive = self._validated_model_archive(archive_path)
        try:
            hasher = hashlib.sha256()
            total = 0
            with archive.open("rb") as source:
                before = os.fstat(source.fileno())
                if not 1 <= before.st_size <= MAX_MODEL_PACKAGE_TOTAL_BYTES:
                    raise ValueError("The model archive size was invalid.")
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > before.st_size:
                        raise ValueError("The model archive changed while hashing.")
                    hasher.update(chunk)
                after = os.fstat(source.fileno())
            if total != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("The model archive changed while hashing.")
        except (OSError, ValueError) as exc:
            raise ServiceError(
                422,
                "MODEL_ARCHIVE_INVALID",
                "The staged model package archive failed request-identity verification.",
            ) from exc
        return archive, {"byteSize": total, "sha256": hasher.hexdigest()}

    def _managed_storage_key(self, package_path: Path) -> str:
        try:
            resolved = package_path.resolve(strict=False)
            relative = resolved.relative_to(self.data_dir)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError(
                500,
                "MODEL_STORAGE_INVALID",
                "The managed model package escaped private service storage.",
            ) from exc
        return relative.as_posix()

    @staticmethod
    def _managed_verification_outcome(
        verification: ModelPackageVerification,
    ) -> str:
        if verification.valid:
            return "verified"
        if "MODEL_PACKAGE_MISSING" in verification.error_codes:
            return "missing"
        if any(
            marker in code
            for code in verification.error_codes
            for marker in ("REPARSE", "SPECIAL", "PATH_ESCAPE", "UNSAFE")
        ):
            return "unsafe"
        return "mismatch"

    @staticmethod
    def _model_package_service_error(error: ModelPackageError) -> ServiceError:
        return ServiceError(
            409,
            error.code,
            error.message,
            retryable=error.code in {"MODEL_PACKAGE_IO_ERROR"},
        )

    def _append_model_verification(
        self,
        session: Session,
        *,
        manifest: ModelPackageManifestRow,
        installation: ModelInstallationRow,
        outcome: str,
        verified_file_count: int,
        verified_byte_count: int,
        findings: Sequence[str],
        request_hash: str,
        origin: str,
        now: str,
    ) -> ModelVerificationRow:
        previous = self._latest_verification(
            session,
            installation.installation_id,
        )
        revision = previous.revision + 1 if previous is not None else 1
        fingerprint = request_fingerprint(
            {
                "findings": list(findings),
                "installationId": installation.installation_id,
                "installationRevision": installation.revision,
                "manifestFingerprint": manifest.manifest_fingerprint,
                "outcome": outcome,
                "revision": revision,
                "verifiedByteCount": verified_byte_count,
                "verifiedFileCount": verified_file_count,
            }
        )
        row = ModelVerificationRow(
            id=new_id(),
            installation_record_id=installation.id,
            installation_id=installation.installation_id,
            manifest_id=manifest.id,
            revision=revision,
            outcome=outcome,
            manifest_fingerprint=manifest.manifest_fingerprint,
            package_fingerprint=manifest.manifest_fingerprint,
            verified_file_count=verified_file_count,
            verified_byte_count=verified_byte_count,
            verifier_id=_PRODUCER_ID,
            verifier_version=_PRODUCER_VERSION,
            findings_json=canonical_json(list(findings)),
            verification_fingerprint=fingerprint,
            supersedes_verification_id=previous.id if previous is not None else None,
            provenance_json=_provenance(origin, input_fingerprint=request_hash),
            started_at=now,
            finished_at=now,
        )
        session.add(row)
        return row

    @staticmethod
    def _verification_for_installation_event(
        session: Session,
        installation: ModelInstallationRow,
    ) -> ModelVerificationRow | None:
        statement = select(ModelVerificationRow).where(
            ModelVerificationRow.installation_id == installation.installation_id
        )
        if installation.operation in {"install", "repair", "verify"}:
            statement = statement.where(
                ModelVerificationRow.installation_record_id == installation.id
            )
        else:
            statement = statement.where(ModelVerificationRow.finished_at <= installation.created_at)
        return session.scalar(
            statement.order_by(
                ModelVerificationRow.revision.desc(),
                ModelVerificationRow.id.desc(),
            ).limit(1)
        )

    @staticmethod
    def _require_project(session: Session, project_id: str) -> ProjectRow:
        project = session.get(ProjectRow, project_id)
        if project is None:
            raise not_found("project")
        return project

    @staticmethod
    def _latest_verification(
        session: Session,
        installation_id: str,
    ) -> ModelVerificationRow | None:
        return session.scalar(
            select(ModelVerificationRow)
            .where(ModelVerificationRow.installation_id == installation_id)
            .order_by(ModelVerificationRow.revision.desc(), ModelVerificationRow.id.desc())
            .limit(1)
        )

    @staticmethod
    def _latest_installation(
        session: Session,
        manifest_id: str,
    ) -> ModelInstallationRow | None:
        return session.scalar(
            select(ModelInstallationRow)
            .where(ModelInstallationRow.manifest_id == manifest_id)
            .order_by(ModelInstallationRow.revision.desc(), ModelInstallationRow.id.desc())
            .limit(1)
        )

    @staticmethod
    def _restricted_local_use_acknowledgement(
        session: Session,
        installation: ModelInstallationRow,
    ) -> ModelInstallationRow | None:
        records = session.scalars(
            select(ModelInstallationRow)
            .where(
                ModelInstallationRow.installation_id == installation.installation_id,
                ModelInstallationRow.manifest_id == installation.manifest_id,
                ModelInstallationRow.revision <= installation.revision,
                ModelInstallationRow.operation.in_(("install", "repair")),
            )
            .order_by(
                ModelInstallationRow.revision.desc(),
                ModelInstallationRow.id.desc(),
            )
        )
        for record in records:
            provenance = parse_json(record.provenance_json, {})
            details = provenance.get("details") if isinstance(provenance, dict) else None
            if (
                isinstance(details, dict)
                and details.get("acknowledgementScope") == "managed_model_installation"
                and details.get("restrictedLocalUseAcknowledged") is True
            ):
                return record
        return None

    def _active_provider_binding(
        self,
        session: Session,
        provider_id: str,
    ) -> (
        tuple[
            ModelPackageManifestRow,
            SpeechRuntimeProfileRow,
            ModelInstallationRow,
            ModelVerificationRow,
            ModelInstallationRow | None,
        ]
        | None
    ):
        manifest = session.scalar(
            select(ModelPackageManifestRow)
            .where(
                ModelPackageManifestRow.provider_id == provider_id,
                ModelPackageManifestRow.revocation_state == "active",
            )
            .order_by(
                ModelPackageManifestRow.created_at.desc(),
                ModelPackageManifestRow.id.desc(),
            )
            .limit(1)
        )
        current_profile_identity = _current_runtime_profile_identity(provider_id)
        if current_profile_identity is None:
            return None
        current_profile_record_id, current_profile_fingerprint = current_profile_identity
        profile = session.scalar(
            select(SpeechRuntimeProfileRow).where(
                SpeechRuntimeProfileRow.id == current_profile_record_id,
                SpeechRuntimeProfileRow.provider_id == provider_id,
                SpeechRuntimeProfileRow.profile_fingerprint == current_profile_fingerprint,
                SpeechRuntimeProfileRow.active.is_(True),
            )
        )
        if manifest is None or profile is None:
            return None
        installation = self._latest_installation(session, manifest.id)
        verification = (
            self._latest_verification(session, installation.installation_id)
            if installation is not None
            else None
        )
        if (
            installation is None
            or installation.state != "active"
            or installation.package_fingerprint != manifest.manifest_fingerprint
            or verification is None
            or verification.outcome != "verified"
            or verification.manifest_id != manifest.id
            or verification.manifest_fingerprint != manifest.manifest_fingerprint
            or verification.package_fingerprint != manifest.manifest_fingerprint
            or profile.provider_version != manifest.provider_version
            or profile.runtime_id != manifest.runtime_id
            or profile.runtime_version != manifest.runtime_version
        ):
            return None
        acknowledgement = (
            self._restricted_local_use_acknowledgement(session, installation)
            if provider_id == KOKORO_PROVIDER_ID
            else None
        )
        if provider_id == KOKORO_PROVIDER_ID and acknowledgement is None:
            return None
        return manifest, profile, installation, verification, acknowledgement

    def _ensure_voice_runtime_binding(
        self,
        session: Session,
        *,
        voice: VoiceProfileRow,
        manifest: ModelPackageManifestRow,
        runtime_profile: SpeechRuntimeProfileRow,
    ) -> VoiceRuntimeBindingRow | None:
        """Resolve one exact governed catalog voice to one exact local runtime voice."""

        source_provider = session.get(
            VoiceProviderDescriptorRow,
            voice.provider_descriptor_id,
        )
        source_model = session.get(
            VoiceModelDescriptorRow,
            voice.model_descriptor_id,
        )
        if (
            source_provider is None
            or source_model is None
            or source_provider.catalog_revision_id != voice.catalog_revision_id
            or source_model.catalog_revision_id != voice.catalog_revision_id
            or source_model.provider_descriptor_id != source_provider.id
            or voice.state != "active"
            or source_provider.runtime_availability != "available"
            or source_provider.catalog_availability != "available"
            or source_provider.health_status != "healthy"
            or source_model.availability != "available"
            or source_model.deprecated
            or runtime_profile.provider_id != manifest.provider_id
            or runtime_profile.provider_version != manifest.provider_version
            or runtime_profile.runtime_id != manifest.runtime_id
            or runtime_profile.runtime_version != manifest.runtime_version
            or runtime_profile.active is not True
            or manifest.revocation_state != "active"
        ):
            return None

        if manifest.provider_id == FIXTURE_PROVIDER_ID:
            if (
                source_provider.provider_id != "synthetic-local-fixture"
                or source_provider.provider_type != "development_fixture"
                or source_model.execution_classification != "fixture"
                or re.fullmatch(
                    r"fixture-(?:narrator|character)-[0-9]{2}",
                    voice.provider_voice_id,
                )
                is None
            ):
                return None
            binding_kind = "declared_fixture_adapter"
        elif manifest.provider_id == KOKORO_PROVIDER_ID:
            if (
                source_provider.provider_id != manifest.provider_id
                or source_provider.provider_version != manifest.provider_version
                or source_model.model_id != manifest.model_id
                or source_model.model_version != manifest.model_version
                or voice.provider_voice_id != KOKORO_LOCAL_ONNX_MANIFEST.voice_id
            ):
                return None
            binding_kind = "exact_provider_match"
        else:
            return None

        material = {
            "bindingKind": binding_kind,
            "bindingVersion": 1,
            "modelPackageFingerprint": manifest.manifest_fingerprint,
            "modelPackageId": manifest.package_id,
            "providerId": manifest.provider_id,
            "providerVersion": manifest.provider_version,
            "providerVoiceId": voice.provider_voice_id,
            "runtimeModelId": manifest.model_id,
            "runtimeModelVersion": manifest.model_version,
            "runtimeProfileFingerprint": runtime_profile.profile_fingerprint,
            "runtimeProfileId": runtime_profile.profile_id,
            "sourceModelDescriptorFingerprint": source_model.descriptor_fingerprint,
            "sourceModelDescriptorId": source_model.id,
            "sourceModelId": source_model.model_id,
            "sourceModelVersion": source_model.model_version,
            "sourceProviderDescriptorFingerprint": source_provider.descriptor_fingerprint,
            "sourceProviderDescriptorId": source_provider.id,
            "sourceProviderId": source_provider.provider_id,
            "sourceProviderVersion": source_provider.provider_version,
            "voiceProfileFingerprint": voice.profile_fingerprint,
            "voiceProfileId": voice.profile_id,
            "voiceProfileRecordId": voice.id,
            "voiceProfileVersion": voice.profile_version,
        }
        binding_fingerprint = request_fingerprint(material)
        existing = session.scalar(
            select(VoiceRuntimeBindingRow).where(
                VoiceRuntimeBindingRow.binding_fingerprint == binding_fingerprint
            )
        )
        if existing is not None:
            if (
                not existing.active
                or existing.binding_kind != binding_kind
                or existing.voice_profile_record_id != voice.id
                or existing.voice_profile_fingerprint != voice.profile_fingerprint
                or existing.provider_id != manifest.provider_id
                or existing.provider_voice_id != voice.provider_voice_id
                or existing.model_manifest_id != manifest.id
                or existing.model_package_fingerprint != manifest.manifest_fingerprint
                or existing.runtime_profile_record_id != runtime_profile.id
                or existing.runtime_profile_fingerprint != runtime_profile.profile_fingerprint
            ):
                return None
            return existing

        binding = VoiceRuntimeBindingRow(
            id=stable_id("phase3b-voice-runtime-binding", binding_fingerprint),
            binding_kind=binding_kind,
            voice_profile_record_id=voice.id,
            voice_profile_id=voice.profile_id,
            voice_profile_version=voice.profile_version,
            voice_profile_fingerprint=voice.profile_fingerprint,
            source_provider_descriptor_id=source_provider.id,
            source_provider_id=source_provider.provider_id,
            source_provider_version=source_provider.provider_version,
            source_provider_fingerprint=source_provider.descriptor_fingerprint,
            source_model_descriptor_id=source_model.id,
            source_model_id=source_model.model_id,
            source_model_version=source_model.model_version,
            source_model_fingerprint=source_model.descriptor_fingerprint,
            provider_id=manifest.provider_id,
            provider_version=manifest.provider_version,
            provider_voice_id=voice.provider_voice_id,
            model_id=manifest.model_id,
            model_version=manifest.model_version,
            model_manifest_id=manifest.id,
            model_package_id=manifest.package_id,
            model_package_fingerprint=manifest.manifest_fingerprint,
            runtime_profile_record_id=runtime_profile.id,
            runtime_profile_id=runtime_profile.profile_id,
            runtime_profile_fingerprint=runtime_profile.profile_fingerprint,
            binding_fingerprint=binding_fingerprint,
            active=True,
            provenance_json=_provenance(
                "application",
                input_fingerprint=binding_fingerprint,
                details={
                    "sourceModelDescriptorFingerprint": source_model.descriptor_fingerprint,
                    "sourceProviderDescriptorFingerprint": source_provider.descriptor_fingerprint,
                    "voiceProfileFingerprint": voice.profile_fingerprint,
                },
            ),
            created_at=utc_now(),
        )
        session.add(binding)
        session.flush()
        return binding

    @staticmethod
    def _governed_private_audition_rights_are_exact(
        session: Session,
        *,
        voice: VoiceProfileRow,
        rights: VoiceRightsRecordRow,
        catalog: VoiceCatalogRevisionRow,
    ) -> bool:
        provider = session.get(
            VoiceProviderDescriptorRow,
            voice.provider_descriptor_id,
        )
        model = session.get(
            VoiceModelDescriptorRow,
            voice.model_descriptor_id,
        )
        return (
            catalog.catalog_id == GOVERNED_VOICE_CATALOG_REVISION_ID
            and catalog.catalog_fingerprint == GOVERNED_VOICE_CATALOG_FINGERPRINT
            and voice.catalog_revision_id == catalog.id
            and voice.profile_id == GOVERNED_KOKORO_VOICE_PROFILE_ID
            and voice.profile_version == GOVERNED_KOKORO_VOICE_PROFILE_VERSION
            and voice.profile_fingerprint == GOVERNED_KOKORO_VOICE_PROFILE_FINGERPRINT
            and voice.provider_voice_id == KOKORO_LOCAL_ONNX_MANIFEST.voice_id
            and provider is not None
            and provider.provider_id == KOKORO_PROVIDER_ID
            and provider.provider_version == KOKORO_ADAPTER_VERSION
            and provider.descriptor_fingerprint == GOVERNED_KOKORO_PROVIDER_FINGERPRINT
            and model is not None
            and model.provider_descriptor_id == provider.id
            and model.model_id == GOVERNED_KOKORO_MODEL_ID
            and model.model_version == KOKORO_LOCAL_ONNX_MANIFEST.model_version
            and model.descriptor_fingerprint == GOVERNED_KOKORO_MODEL_FINGERPRINT
            and rights.voice_profile_record_id == voice.id
            and rights.rights_record_id == GOVERNED_KOKORO_RIGHTS_RECORD_ID
            and rights.revision == 1
            and rights.rights_fingerprint == GOVERNED_KOKORO_RIGHTS_RECORD_FINGERPRINT
            and rights.rights_state == "restricted"
            and rights.commercial_use_status == "restricted"
            and rights.consent_status == "unknown"
            and rights.human_verification_status == "pending"
        )

    @staticmethod
    def _governed_inventory_item() -> dict[str, Any]:
        inventory = _governed_voice_inventory()
        items = inventory.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ServiceError(
                500,
                "GOVERNED_VOICE_INVENTORY_INVALID",
                "The governed local voice inventory failed integrity validation.",
            )
        return cast(dict[str, Any], items[0])

    def _build_governed_local_voice_activation(
        self,
        session: Session,
        *,
        evidence: Mapping[str, Any],
        actor_id: str,
        acknowledged_at: str,
        reason: str,
        request_fingerprint_value: str,
    ) -> dict[str, Any]:
        assignment = cast(CastAssignmentRow, evidence["assignment"])
        cast_snapshot = cast(ApprovedCastSnapshotRow, evidence["cast_snapshot"])
        casting_run = cast(CastingRunRow, evidence["casting_run"])
        catalog = cast(VoiceCatalogRevisionRow, evidence["catalog"])
        rights = cast(VoiceRightsRecordRow, evidence["rights"])
        runtime_profile = cast(SpeechRuntimeProfileRow, evidence["runtime_profile"])
        manifest = cast(ModelPackageManifestRow, evidence["manifest"])
        installation = cast(ModelInstallationRow, evidence["installation"])
        verification = cast(ModelVerificationRow, evidence["verification"])
        voice = cast(VoiceProfileRow, evidence["voice"])

        inventory_record = self._governed_inventory_item()
        if (
            manifest.package_id != KOKORO_LOCAL_ONNX_MANIFEST.package_id
            or manifest.manifest_fingerprint != KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
            or manifest.provider_id != KOKORO_LOCAL_ONNX_MANIFEST.provider_id
            or manifest.provider_version != KOKORO_LOCAL_ONNX_MANIFEST.provider_version
            or manifest.model_id != KOKORO_LOCAL_ONNX_MANIFEST.model_id
            or manifest.model_version != KOKORO_LOCAL_ONNX_MANIFEST.model_version
            or runtime_profile.id != _KOKORO_PROFILE_RECORD_ID
            or runtime_profile.profile_id != _KOKORO_PROFILE_ID
            or runtime_profile.profile_fingerprint != _KOKORO_PROFILE_FINGERPRINT
            or not self._governed_private_audition_rights_are_exact(
                session,
                voice=voice,
                rights=rights,
                catalog=catalog,
            )
        ):
            raise ServiceError(
                409,
                "AUDITION_RESTRICTED_VOICE_BINDING_INVALID",
                "The selected restricted voice is not the exact governed local voice.",
            )
        rights_acknowledgement = self._casting._current_restricted_rights_acknowledgement(
            session,
            run_id=casting_run.id,
            role_id=assignment.role_id,
            voice_profile_id=voice.profile_id,
            rights_record_id=rights.rights_record_id,
            rights_record_revision=rights.revision,
        )
        if rights_acknowledgement is None:
            raise ServiceError(
                409,
                "AUDITION_RESTRICTED_RIGHTS_ACKNOWLEDGEMENT_REQUIRED",
                "A current Phase 3A restricted-rights acknowledgement is required.",
            )
        rights_acknowledgement_fingerprint = self._casting._validated_correction_fingerprint(
            rights_acknowledgement
        )
        model_acknowledgement = self._restricted_local_use_acknowledgement(
            session,
            installation,
        )
        if model_acknowledgement is None:
            raise ServiceError(
                409,
                "AUDITION_RESTRICTED_MODEL_ACKNOWLEDGEMENT_MISSING",
                "The restricted local model acknowledgement is unavailable.",
            )
        acknowledgement_provenance = {
            "origin": "human",
            "producerId": _PRODUCER_ID,
            "producerVersion": _PRODUCER_VERSION,
            "recordedAt": acknowledged_at,
            "inputFingerprint": request_fingerprint_value,
        }
        acknowledgement: dict[str, Any] = {
            "contractVersion": "1.0.0",
            "acknowledgementId": new_id(),
            "actor": {"classification": "human", "actorId": actor_id},
            "acknowledgedAt": acknowledged_at,
            "reason": reason,
            "warningText": _GOVERNED_PRIVATE_AUDITION_WARNING,
            "warningFingerprint": _GOVERNED_PRIVATE_AUDITION_WARNING_FINGERPRINT,
            "inventoryRecordId": inventory_record["inventoryRecordId"],
            "inventoryFingerprint": _GOVERNED_VOICE_INVENTORY_FINGERPRINT,
            "providerId": manifest.provider_id,
            "providerVersion": manifest.provider_version,
            "modelId": manifest.model_id,
            "modelVersion": manifest.model_version,
            "modelPackageId": manifest.package_id,
            "modelPackageFingerprint": manifest.manifest_fingerprint,
            "voiceProfileId": voice.profile_id,
            "voiceProfileVersion": voice.profile_version,
            "voiceProfileFingerprint": voice.profile_fingerprint,
            "catalogRevisionId": catalog.catalog_id,
            "catalogRevisionFingerprint": catalog.catalog_fingerprint,
            "voiceTensorSha256": inventory_record["voiceTensor"]["sha256"],
            "rightsRecordId": rights.rights_record_id,
            "rightsRecordRevision": rights.revision,
            "rightsRecordFingerprint": rights.rights_fingerprint,
            "restrictedRightsCorrectionId": rights_acknowledgement.id,
            "restrictedRightsCorrectionFingerprint": (rights_acknowledgement_fingerprint),
            "modelInstallationAcknowledgementEventId": model_acknowledgement.id,
            "modelVerificationId": verification.id,
            "modelVerificationFingerprint": verification.verification_fingerprint,
            "privateLocalAuditionOnly": True,
            "productionExportAuthorized": False,
            "commercialDistributionAuthorized": False,
            "marketplaceResaleAuthorized": False,
            "cloningAuthorized": False,
            "realPersonImitationAuthorized": False,
            "immutable": True,
            "provenance": acknowledgement_provenance,
        }
        acknowledgement["acknowledgementFingerprint"] = request_fingerprint(acknowledgement)
        binding: dict[str, Any] = {
            "contractVersion": "1.0.0",
            "acknowledgement": acknowledgement,
            "castAssignmentId": assignment.id,
            "castAssignmentRevision": assignment.revision,
            "castAssignmentFingerprint": self._assignment_fingerprint(assignment),
            "approvedCastSnapshotId": cast_snapshot.id,
            "approvedCastSnapshotRevision": cast_snapshot.revision,
            "approvedCastSnapshotFingerprint": cast_snapshot.snapshot_fingerprint,
            "runtimeProfileId": runtime_profile.profile_id,
            "runtimeProfileFingerprint": runtime_profile.profile_fingerprint,
            "privateLocalAuditionOnly": True,
            "productionExportEligible": False,
        }
        binding["bindingFingerprint"] = request_fingerprint(binding)
        return binding

    def _governed_local_voice_activation_wire(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
    ) -> dict[str, Any] | None:
        provenance = parse_json(audition_session.provenance_json, None)
        details = provenance.get("details") if isinstance(provenance, dict) else None
        stored = details.get("governedLocalVoiceActivation") if isinstance(details, dict) else None
        if audition_session.provider_id == FIXTURE_PROVIDER_ID:
            if stored is not None:
                raise ServiceError(
                    500,
                    "AUDITION_ACTIVATION_EVIDENCE_INVALID",
                    "Fixture audition evidence contained a real-voice activation.",
                )
            return None
        if audition_session.provider_id != KOKORO_PROVIDER_ID or not isinstance(stored, dict):
            raise ServiceError(
                409,
                "AUDITION_ACTIVATION_EVIDENCE_INVALID",
                "The governed real-voice activation is unavailable.",
            )
        acknowledgement = stored.get("acknowledgement")
        if not isinstance(acknowledgement, dict):
            raise ServiceError(
                409,
                "AUDITION_ACTIVATION_EVIDENCE_INVALID",
                "The governed real-voice activation failed integrity validation.",
            )
        actor = acknowledgement.get("actor")
        activation_provenance = acknowledgement.get("provenance")
        if (
            not isinstance(actor, dict)
            or actor.get("classification") != "human"
            or not isinstance(actor.get("actorId"), str)
            or not isinstance(acknowledgement.get("acknowledgedAt"), str)
            or not isinstance(acknowledgement.get("reason"), str)
            or not isinstance(activation_provenance, dict)
        ):
            raise ServiceError(
                409,
                "AUDITION_ACTIVATION_EVIDENCE_INVALID",
                "The governed real-voice activation failed integrity validation.",
            )
        evidence = self._validate_session_evidence(
            session,
            project_id=audition_session.project_id,
            role_id=audition_session.role_id,
            evidence=self._session_evidence_wire(
                session,
                audition_session,
                include_activation=False,
            ),
            require_current_pronunciation=False,
        )
        expected = self._build_governed_local_voice_activation(
            session,
            evidence=evidence,
            actor_id=cast(str, actor["actorId"]),
            acknowledged_at=cast(str, acknowledgement["acknowledgedAt"]),
            reason=cast(str, acknowledgement["reason"]),
            request_fingerprint_value=audition_session.request_fingerprint,
        )
        expected_acknowledgement = cast(dict[str, Any], expected["acknowledgement"])
        expected_acknowledgement["acknowledgementId"] = acknowledgement.get("acknowledgementId")
        expected_acknowledgement["acknowledgementFingerprint"] = request_fingerprint(
            {
                key: value
                for key, value in expected_acknowledgement.items()
                if key != "acknowledgementFingerprint"
            }
        )
        expected["bindingFingerprint"] = request_fingerprint(
            {key: value for key, value in expected.items() if key != "bindingFingerprint"}
        )
        if stored != expected:
            raise ServiceError(
                409,
                "AUDITION_ACTIVATION_EVIDENCE_CHANGED",
                "The governed real-voice activation is stale or invalid.",
            )
        return deepcopy(expected)

    def _assert_session_voice_runtime_binding(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
    ) -> VoiceRuntimeBindingRow:
        voice = session.get(VoiceProfileRow, audition_session.voice_profile_record_id)
        manifest = session.get(ModelPackageManifestRow, audition_session.model_manifest_id)
        runtime_profile = session.get(
            SpeechRuntimeProfileRow,
            audition_session.runtime_profile_id,
        )
        if voice is None or manifest is None or runtime_profile is None:
            raise ServiceError(
                409,
                "AUDITION_VOICE_RUNTIME_BINDING_INVALID",
                "The frozen voice runtime binding is unavailable.",
            )
        current = self._ensure_voice_runtime_binding(
            session,
            voice=voice,
            manifest=manifest,
            runtime_profile=runtime_profile,
        )
        persisted = session.get(
            VoiceRuntimeBindingRow,
            audition_session.voice_runtime_binding_id,
        )
        if (
            current is None
            or persisted is None
            or not persisted.active
            or current.id != persisted.id
            or current.binding_fingerprint != audition_session.voice_runtime_binding_fingerprint
            or persisted.binding_fingerprint != audition_session.voice_runtime_binding_fingerprint
            or persisted.provider_voice_id != audition_session.provider_voice_id
            or persisted.voice_profile_record_id != audition_session.voice_profile_record_id
            or persisted.provider_id != audition_session.provider_id
            or persisted.model_id != audition_session.model_id
            or persisted.model_package_fingerprint != audition_session.model_package_fingerprint
            or persisted.runtime_profile_fingerprint != audition_session.runtime_profile_fingerprint
        ):
            raise ServiceError(
                409,
                "AUDITION_VOICE_RUNTIME_BINDING_CHANGED",
                "The exact governed voice runtime binding is no longer current.",
            )
        return persisted

    def _model_package_wire(
        self,
        session: Session,
        manifest: ModelPackageManifestRow,
    ) -> dict[str, Any]:
        installation = self._latest_installation(session, manifest.id)
        verification = (
            self._latest_verification(session, installation.installation_id)
            if installation is not None
            else None
        )
        return {
            "manifest": self._manifest_wire(manifest),
            "installation": (
                self._installation_wire(manifest, installation)
                if installation is not None
                else None
            ),
            "verification": (
                self._verification_wire(manifest, verification)
                if verification is not None
                else None
            ),
        }

    @staticmethod
    def _manifest_wire(row: ModelPackageManifestRow) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "manifestVersion": row.manifest_version,
            "modelPackageId": row.package_id,
            "providerId": row.provider_id,
            "modelId": row.model_id,
            "modelVersion": row.model_version,
            "runtimeVersion": row.runtime_version,
            "platform": row.platform,
            "architecture": row.architecture,
            "sourceClassification": row.source_classification,
            "officialSourceReference": row.official_source_reference,
            "licenseIdentifier": row.license_identifier,
            "commercialUseClassification": row.commercial_use_classification,
            "attributionRequirements": parse_json(row.attribution_requirements_json, []),
            "files": parse_json(row.file_inventory_json, []),
            "totalExpandedByteSize": row.total_expanded_size,
            "requiredRuntimeDependencies": parse_json(
                row.required_runtime_dependencies_json,
                [],
            ),
            "compatibilityConstraints": parse_json(row.compatibility_constraints_json, []),
            "state": row.revocation_state,
            "manifestFingerprint": row.manifest_fingerprint,
            "provenance": _public_provenance(row.provenance_json),
        }

    @staticmethod
    def _installation_wire(
        manifest: ModelPackageManifestRow,
        row: ModelInstallationRow,
    ) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "installationId": row.installation_id,
            "modelPackageId": manifest.package_id,
            "manifestFingerprint": manifest.manifest_fingerprint,
            "installationRevision": row.revision,
            "storageKey": stable_id(
                "phase3b-model-storage-handle",
                row.installation_id,
            ),
            "status": row.state,
            "active": row.state == "active",
            "installedAt": row.created_at if row.state != "pending" else None,
            "updatedAt": row.completed_at or row.created_at,
            "lastAction": row.operation,
            "actionReasonCode": row.reason,
            "immutableEventId": row.id,
            "provenance": _public_provenance(row.provenance_json),
        }

    @staticmethod
    def _verification_wire(
        manifest: ModelPackageManifestRow,
        row: ModelVerificationRow,
    ) -> dict[str, Any]:
        findings = parse_json(row.findings_json, [])
        if not isinstance(findings, list):
            findings = []
        return {
            "contractVersion": "1.0.0",
            "verificationId": row.id,
            "installationId": row.installation_id,
            "modelPackageId": manifest.package_id,
            "manifestFingerprint": row.manifest_fingerprint,
            "verificationFingerprint": row.verification_fingerprint,
            "status": row.outcome,
            "verifiedFileCount": row.verified_file_count,
            "verifiedByteSize": row.verified_byte_count,
            "unexpectedFileCount": (1 if "MODEL_PACKAGE_INVENTORY_MISMATCH" in findings else 0),
            "symlinkOrReparsePointDetected": any(
                isinstance(code, str) and ("REPARSE" in code or "SPECIAL" in code)
                for code in findings
            ),
            "checkedAt": row.finished_at,
            "blockingReasonCodes": findings,
            "provenance": _public_provenance(row.provenance_json),
        }

    def _model_action_wire(
        self,
        session: Session,
        installation: ModelInstallationRow,
        verification: ModelVerificationRow | None,
    ) -> dict[str, Any]:
        manifest = session.get(ModelPackageManifestRow, installation.manifest_id)
        if manifest is None:
            raise ServiceError(
                500,
                "MODEL_MANIFEST_MISSING",
                "The model installation manifest is unavailable.",
            )
        return {
            "installation": self._installation_wire(manifest, installation),
            "verification": (
                self._verification_wire(manifest, verification)
                if verification is not None
                else None
            ),
        }

    # Pronunciation dictionaries -------------------------------------------------

    def list_pronunciation_entries(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int,
        expected_dictionary_revision: int | None = None,
        expected_dictionary_fingerprint: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None, int]:
        page_size = _bounded_page(limit)
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            dictionary = self._ensure_empty_dictionary(session, project_id)
            if (
                expected_dictionary_revision is not None
                or expected_dictionary_fingerprint is not None
            ):
                if expected_dictionary_revision is None or expected_dictionary_fingerprint is None:
                    raise ServiceError(
                        422,
                        "PRONUNCIATION_DICTIONARY_EVIDENCE_INCOMPLETE",
                        "Both dictionary revision and fingerprint are required.",
                    )
                self._assert_dictionary_evidence(
                    dictionary,
                    expected_revision=expected_dictionary_revision,
                    expected_fingerprint=expected_dictionary_fingerprint,
                )
            filters = [PronunciationEntryRow.project_id == project_id]
            total = int(
                session.scalar(
                    select(func.count()).select_from(PronunciationEntryRow).where(*filters)
                )
                or 0
            )
            latest_identity = session.scalar(
                select(PronunciationEntryRow.id)
                .where(*filters)
                .order_by(
                    PronunciationEntryRow.created_at.desc(),
                    PronunciationEntryRow.id.desc(),
                )
                .limit(1)
            )
            binding = request_fingerprint(
                {
                    "dictionaryFingerprint": dictionary.dictionary_fingerprint,
                    "latestId": latest_identity,
                    "projectId": project_id,
                    "total": total,
                    "type": "pronunciation-entries",
                }
            )
            offset = _decode_cursor(cursor, binding=binding)
            if offset > total:
                raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
            rows = list(
                session.scalars(
                    select(PronunciationEntryRow)
                    .where(*filters)
                    .order_by(
                        PronunciationEntryRow.created_at.desc(),
                        PronunciationEntryRow.id.desc(),
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            )
            next_offset = offset + len(rows)
            next_cursor = _encode_cursor(binding, next_offset) if next_offset < total else None
            return (
                self._dictionary_wire(session, project_id, dictionary),
                [self._pronunciation_entry_wire(session, row) for row in rows],
                next_cursor,
                total,
            )

    def create_pronunciation_entry(
        self,
        *,
        project_id: str,
        request: CreatePronunciationEntryRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        request_value = request.model_dump(mode="json", by_alias=True)
        request_hash = request_fingerprint(request_value | {"projectId": project_id})
        scope = f"phase3b-pronunciation-create:{project_id}"
        now = utc_now()
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            replay = session.get(
                IdempotencyRow,
                {"scope": scope, "key": request.idempotency_key},
            )
            if replay is not None:
                if replay.request_hash != request_hash:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another pronunciation entry.",
                    )
                replay_entry = session.get(PronunciationEntryRow, replay.resource_id)
                if replay_entry is None or replay_entry.project_id != project_id:
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved pronunciation entry is unavailable.",
                    )
                replay_dictionary = session.get(
                    PronunciationDictionaryRow,
                    replay_entry.dictionary_record_id,
                )
                return self._pronunciation_mutation_replay_wire(
                    session,
                    replay_entry,
                    replay_dictionary,
                )

            current = self._current_dictionary(session, project_id)
            self._assert_dictionary_evidence(
                current,
                expected_revision=request.expected_dictionary_revision,
                expected_fingerprint=request.expected_dictionary_fingerprint,
            )
            current_rows = self._latest_pronunciation_entry_rows(session, project_id)
            if len(current_rows) >= _MAX_PRONUNCIATION_ENTRIES:
                raise ServiceError(
                    409,
                    "PRONUNCIATION_ENTRY_LIMIT_EXCEEDED",
                    "This project reached the pronunciation entry limit.",
                )
            superseded: PronunciationEntryRow | None = None
            if request.supersedes_entry_id is not None:
                superseded = next(
                    (row for row in current_rows if row.entry_id == request.supersedes_entry_id),
                    None,
                )
                if superseded is None:
                    raise not_found("pronunciation entry")

            dictionary_id = (
                current.dictionary_id
                if current is not None
                else stable_id("phase3b-pronunciation-dictionary", project_id)
            )
            dictionary_revision = (current.revision + 1) if current is not None else 1
            dictionary_record_id = new_id()
            entry_id = new_id()
            provider_specific = {
                "providerCompiledValue": request.provider_compiled_value,
                "providerId": request.provider_id,
                "pronunciation": request.pronunciation,
                "representation": request.representation,
            }
            entry_material = {
                "caseSensitive": request.case_sensitive,
                "entryId": entry_id,
                "ipa": request.ipa,
                "language": request.language,
                "locale": request.locale,
                "matchRule": request.match_rule,
                "priority": request.priority,
                "providerSpecific": provider_specific,
                "revision": 1,
                "scope": request.scope,
                "scopeId": request.scope_id,
                "supersedesEntryId": request.supersedes_entry_id,
                "verificationState": "pending",
                "writtenForm": request.written_form,
            }
            entry_fingerprint = request_fingerprint(entry_material)
            row = PronunciationEntryRow(
                id=new_id(),
                project_id=project_id,
                dictionary_record_id=dictionary_record_id,
                dictionary_id=dictionary_id,
                dictionary_revision=dictionary_revision,
                entry_id=entry_id,
                revision=1,
                written_form=request.written_form,
                normalized_lookup_form=request.written_form.casefold(),
                language=request.language,
                locale=request.locale,
                scope_type=request.scope,
                scope_target_id=request.scope_id,
                provider_neutral_value=request.pronunciation,
                ipa_value=request.ipa,
                provider_specific_json=canonical_json(provider_specific),
                case_sensitive=request.case_sensitive,
                whole_word=request.match_rule == "whole_word",
                priority=request.priority,
                verification_state="pending",
                entry_fingerprint=entry_fingerprint,
                actor_id=actor_id,
                reason=request.reason,
                supersedes_entry_record_id=superseded.id if superseded is not None else None,
                provenance_json=_provenance(
                    "human",
                    input_fingerprint=request_hash,
                ),
                created_at=now,
            )
            dictionary_rows = [*current_rows, row]
            dictionary = self._build_dictionary_row(
                project_id=project_id,
                dictionary_id=dictionary_id,
                record_id=dictionary_record_id,
                revision=dictionary_revision,
                rows=dictionary_rows,
                supersedes=current,
                now=now,
            )
            session.add(dictionary)
            session.add(row)
            session.add(
                IdempotencyRow(
                    scope=scope,
                    key=request.idempotency_key,
                    request_hash=request_hash,
                    resource_id=row.id,
                    created_at=now,
                )
            )
            session.flush()
            result = self._pronunciation_mutation_wire(
                session,
                row,
                dictionary,
                invalidated_clip_ids=(),
                invalidated_gate_ids=(),
            )
            self._record_pronunciation_mutation_result(row, result)
            return result

    def decide_pronunciation_entry(
        self,
        *,
        project_id: str,
        entry_id: str,
        request: DecidePronunciationEntryRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        request_value = request.model_dump(mode="json", by_alias=True)
        request_hash = request_fingerprint(
            request_value | {"entryId": entry_id, "projectId": project_id}
        )
        idempotency_scope = f"phase3b-pronunciation-decision:{project_id}:{entry_id}"
        now = utc_now()
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            replay = session.get(
                IdempotencyRow,
                {"scope": idempotency_scope, "key": request.idempotency_key},
            )
            if replay is not None:
                if replay.request_hash != request_hash:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another pronunciation decision.",
                    )
                replay_entry = session.get(PronunciationEntryRow, replay.resource_id)
                if replay_entry is None or replay_entry.project_id != project_id:
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved pronunciation decision is unavailable.",
                    )
                replay_dictionary = session.get(
                    PronunciationDictionaryRow,
                    replay_entry.dictionary_record_id,
                )
                return self._pronunciation_mutation_replay_wire(
                    session,
                    replay_entry,
                    replay_dictionary,
                )

            current_dictionary = self._current_dictionary(session, project_id)
            if current_dictionary is None:
                raise not_found("pronunciation dictionary")
            self._assert_dictionary_evidence(
                current_dictionary,
                expected_revision=request.expected_dictionary_revision,
                expected_fingerprint=request.expected_dictionary_fingerprint,
            )
            latest_rows = self._latest_pronunciation_entry_rows(session, project_id)
            prior = next((row for row in latest_rows if row.entry_id == entry_id), None)
            if prior is None:
                raise not_found("pronunciation entry")
            if (
                prior.revision != request.expected_entry_revision
                or prior.entry_fingerprint != request.expected_entry_fingerprint
            ):
                raise ServiceError(
                    409,
                    "PRONUNCIATION_ENTRY_CHANGED",
                    "The pronunciation entry changed; refresh before deciding.",
                )
            if prior.verification_state in {"rejected", "superseded"}:
                raise ServiceError(
                    409,
                    "PRONUNCIATION_ENTRY_TERMINAL",
                    "The pronunciation entry is no longer decision-eligible.",
                )
            next_state = {
                "approve": "approved",
                "reject": "rejected",
                "request_changes": "changes_requested",
            }[request.decision]
            dictionary_revision = current_dictionary.revision + 1
            dictionary_record_id = new_id()
            next_revision = prior.revision + 1
            next_fingerprint = request_fingerprint(
                {
                    "entryFingerprint": prior.entry_fingerprint,
                    "entryId": entry_id,
                    "priorRecordId": prior.id,
                    "revision": next_revision,
                    "verificationState": next_state,
                }
            )
            row = PronunciationEntryRow(
                id=new_id(),
                project_id=project_id,
                dictionary_record_id=dictionary_record_id,
                dictionary_id=current_dictionary.dictionary_id,
                dictionary_revision=dictionary_revision,
                entry_id=prior.entry_id,
                revision=next_revision,
                written_form=prior.written_form,
                normalized_lookup_form=prior.normalized_lookup_form,
                language=prior.language,
                locale=prior.locale,
                scope_type=prior.scope_type,
                scope_target_id=prior.scope_target_id,
                provider_neutral_value=prior.provider_neutral_value,
                ipa_value=prior.ipa_value,
                provider_specific_json=prior.provider_specific_json,
                case_sensitive=prior.case_sensitive,
                whole_word=prior.whole_word,
                priority=prior.priority,
                verification_state=next_state,
                entry_fingerprint=next_fingerprint,
                actor_id=actor_id,
                reason=request.rationale,
                supersedes_entry_record_id=prior.id,
                provenance_json=_provenance(
                    "human",
                    input_fingerprint=request_hash,
                ),
                created_at=now,
            )
            updated_rows = [value for value in latest_rows if value.entry_id != entry_id]
            updated_rows.append(row)
            dictionary = self._build_dictionary_row(
                project_id=project_id,
                dictionary_id=current_dictionary.dictionary_id,
                record_id=dictionary_record_id,
                revision=dictionary_revision,
                rows=updated_rows,
                supersedes=current_dictionary,
                now=now,
            )
            session.add(dictionary)
            session.add(row)
            session.add(
                IdempotencyRow(
                    scope=idempotency_scope,
                    key=request.idempotency_key,
                    request_hash=request_hash,
                    resource_id=row.id,
                    created_at=now,
                )
            )
            session.flush()
            changed_effective_value = (
                prior.verification_state == "approved" or next_state == "approved"
            )
            invalidated, preserved = (
                self._invalidate_pronunciation_dependencies(
                    session,
                    project_id=project_id,
                    changed_entry=row,
                    previous_fingerprint=prior.entry_fingerprint,
                    current_fingerprint=row.entry_fingerprint,
                )
                if changed_effective_value
                else ([], None)
            )
            self._ensure_pronunciation_review(session, project_id)
            result = self._pronunciation_mutation_wire(
                session,
                row,
                dictionary,
                invalidated_clip_ids=invalidated,
                preserved_clip_ids=preserved,
                invalidated_gate_ids=(
                    ("pronunciation_review", "voice_readiness_review")
                    if changed_effective_value
                    else ()
                ),
            )
            self._record_pronunciation_mutation_result(row, result)
            return result

    @staticmethod
    def _current_dictionary(
        session: Session,
        project_id: str,
    ) -> PronunciationDictionaryRow | None:
        return session.scalar(
            select(PronunciationDictionaryRow)
            .where(PronunciationDictionaryRow.project_id == project_id)
            .order_by(
                PronunciationDictionaryRow.revision.desc(),
                PronunciationDictionaryRow.id.desc(),
            )
            .limit(1)
        )

    def _ensure_empty_dictionary(
        self,
        session: Session,
        project_id: str,
    ) -> PronunciationDictionaryRow:
        current = self._current_dictionary(session, project_id)
        if current is not None:
            return current
        now = utc_now()
        row = self._build_dictionary_row(
            project_id=project_id,
            dictionary_id=stable_id("phase3b-pronunciation-dictionary", project_id),
            record_id=new_id(),
            revision=1,
            rows=(),
            supersedes=None,
            now=now,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _latest_pronunciation_entry_rows(
        session: Session,
        project_id: str,
    ) -> list[PronunciationEntryRow]:
        ranked = (
            select(
                PronunciationEntryRow.id.label("record_id"),
                func.row_number()
                .over(
                    partition_by=PronunciationEntryRow.entry_id,
                    order_by=(
                        PronunciationEntryRow.revision.desc(),
                        PronunciationEntryRow.id.desc(),
                    ),
                )
                .label("record_rank"),
            )
            .where(PronunciationEntryRow.project_id == project_id)
            .subquery()
        )
        rows = list(
            session.scalars(
                select(PronunciationEntryRow)
                .join(ranked, ranked.c.record_id == PronunciationEntryRow.id)
                .where(ranked.c.record_rank == 1)
                .order_by(PronunciationEntryRow.entry_id, PronunciationEntryRow.id)
                .limit(_MAX_PRONUNCIATION_ENTRIES + 1)
            )
        )
        if len(rows) > _MAX_PRONUNCIATION_ENTRIES:
            raise ServiceError(
                500,
                "PRONUNCIATION_ENTRY_LIMIT_INVALID",
                "The pronunciation dictionary exceeds its bounded current-entry limit.",
            )
        return rows

    @staticmethod
    def _bounded_pronunciation_history_rows(
        session: Session,
        project_id: str,
    ) -> list[PronunciationEntryRow]:
        total = int(
            session.scalar(
                select(func.count())
                .select_from(PronunciationEntryRow)
                .where(PronunciationEntryRow.project_id == project_id)
            )
            or 0
        )
        if total > _MAX_PRONUNCIATION_HISTORY_RECORDS:
            raise ServiceError(
                500,
                "PRONUNCIATION_HISTORY_LIMIT_EXCEEDED",
                "Pronunciation history exceeds its fixed integrity bound.",
            )
        rows = list(
            session.scalars(
                select(PronunciationEntryRow)
                .where(PronunciationEntryRow.project_id == project_id)
                .order_by(PronunciationEntryRow.id)
                .limit(_MAX_PRONUNCIATION_HISTORY_RECORDS + 1)
            )
        )
        if len(rows) != total:
            raise ServiceError(
                500,
                "PRONUNCIATION_HISTORY_CHANGED",
                "Pronunciation history changed during bounded verification.",
            )
        return rows

    @staticmethod
    def _assert_dictionary_evidence(
        current: PronunciationDictionaryRow | None,
        *,
        expected_revision: int,
        expected_fingerprint: str,
    ) -> None:
        actual_revision = current.revision if current is not None else 0
        actual_fingerprint = (
            current.dictionary_fingerprint if current is not None else _EMPTY_DICTIONARY_FINGERPRINT
        )
        if actual_revision != expected_revision or actual_fingerprint != expected_fingerprint:
            raise ServiceError(
                409,
                "PRONUNCIATION_DICTIONARY_CHANGED",
                "The pronunciation dictionary changed; refresh before continuing.",
                details={
                    "currentDictionaryFingerprint": actual_fingerprint,
                    "currentDictionaryRevision": actual_revision,
                },
            )

    @staticmethod
    def _dictionary_fingerprint_for_rows(
        rows: Sequence[PronunciationEntryRow],
        revision: int,
    ) -> str:
        material = [
            {
                "caseSensitive": row.case_sensitive,
                "entryFingerprint": row.entry_fingerprint,
                "entryId": row.entry_id,
                "entryRevision": row.revision,
                "matchRule": "whole_word" if row.whole_word else "phrase",
                "scope": row.scope_type,
                "scopeId": row.scope_target_id,
                "verificationState": row.verification_state,
            }
            for row in sorted(rows, key=lambda value: (value.entry_id, value.revision))
        ]
        return request_fingerprint(
            {
                "entries": material,
                "profileVersion": PRONUNCIATION_PROFILE_VERSION,
                "revision": revision,
            }
        )

    def _build_dictionary_row(
        self,
        *,
        project_id: str,
        dictionary_id: str,
        record_id: str,
        revision: int,
        rows: Sequence[PronunciationEntryRow],
        supersedes: PronunciationDictionaryRow | None,
        now: str,
    ) -> PronunciationDictionaryRow:
        active_ids = sorted(
            row.entry_id for row in rows if row.verification_state not in {"rejected", "superseded"}
        )
        return PronunciationDictionaryRow(
            id=record_id,
            project_id=project_id,
            dictionary_id=dictionary_id,
            revision=revision,
            default_language="en",
            default_locale="en-US",
            entry_count=len(rows),
            active_entry_ids_json=canonical_json(active_ids),
            dictionary_fingerprint=self._dictionary_fingerprint_for_rows(rows, revision),
            producer_id=_PRODUCER_ID,
            producer_version=_PRODUCER_VERSION,
            supersedes_dictionary_record_id=supersedes.id if supersedes is not None else None,
            provenance_json=_provenance("application"),
            created_at=now,
        )

    def _dictionary_wire(
        self,
        session: Session,
        project_id: str,
        row: PronunciationDictionaryRow | None,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "contractVersion": "1.0.0",
                "dictionaryId": stable_id("phase3b-pronunciation-dictionary", project_id),
                "projectId": project_id,
                "revision": 0,
                "entryCount": 0,
                "currentEntryCount": 0,
                "dictionaryFingerprint": _EMPTY_DICTIONARY_FINGERPRINT,
                "createdAt": utc_now(),
                "updatedAt": utc_now(),
                "provenance": _public_provenance(_provenance("application")),
            }
        historical_count = int(
            session.scalar(
                select(func.count(func.distinct(PronunciationEntryRow.entry_id))).where(
                    PronunciationEntryRow.project_id == project_id
                )
            )
            or 0
        )
        return {
            "contractVersion": "1.0.0",
            "dictionaryId": row.dictionary_id,
            "projectId": project_id,
            "revision": row.revision,
            "entryCount": historical_count,
            "currentEntryCount": row.entry_count,
            "dictionaryFingerprint": row.dictionary_fingerprint,
            "createdAt": (
                session.scalar(
                    select(PronunciationDictionaryRow.created_at)
                    .where(PronunciationDictionaryRow.project_id == project_id)
                    .order_by(PronunciationDictionaryRow.revision, PronunciationDictionaryRow.id)
                    .limit(1)
                )
                or row.created_at
            ),
            "updatedAt": row.created_at,
            "provenance": _public_provenance(row.provenance_json),
        }

    @staticmethod
    def _pronunciation_representation(row: PronunciationEntryRow) -> str:
        value = parse_json(row.provider_specific_json, {})
        representation = value.get("representation") if isinstance(value, dict) else None
        return (
            representation
            if representation in {"provider_neutral", "ipa", "provider_specific"}
            else "provider_neutral"
        )

    @staticmethod
    def _pronunciation_record_lineage(
        session: Session,
        row: PronunciationEntryRow,
        *,
        direction: str,
        records_by_id: Mapping[str, PronunciationEntryRow] | None = None,
    ) -> tuple[PronunciationEntryRow, ...]:
        """Walk append-only record lineage with a fixed bound and cycle checks."""

        if direction == "predecessors":
            lineage: list[PronunciationEntryRow] = []
            visited = {row.id}
            current = row
            for depth in range(_MAX_PRONUNCIATION_LINEAGE_RECORDS + 1):
                prior_id = current.supersedes_entry_record_id
                if prior_id is None:
                    return tuple(lineage)
                prior = (
                    records_by_id.get(prior_id)
                    if records_by_id is not None
                    else session.get(PronunciationEntryRow, prior_id)
                )
                if (
                    prior is None
                    or prior.project_id != row.project_id
                    or prior.id in visited
                    or depth == _MAX_PRONUNCIATION_LINEAGE_RECORDS
                ):
                    raise ServiceError(
                        500,
                        "PRONUNCIATION_LINEAGE_INVALID",
                        "Pronunciation entry lineage failed bounded integrity checks.",
                    )
                lineage.append(prior)
                visited.add(prior.id)
                current = prior
            raise AssertionError("The bounded pronunciation lineage walk did not terminate.")

        if direction != "successors":
            raise ValueError("Pronunciation lineage direction was invalid.")
        lineage = []
        visited = {row.id}
        pending = [row]
        while pending:
            current = pending.pop(0)
            successors = list(
                session.scalars(
                    select(PronunciationEntryRow)
                    .where(
                        PronunciationEntryRow.project_id == row.project_id,
                        PronunciationEntryRow.supersedes_entry_record_id == current.id,
                    )
                    .order_by(
                        PronunciationEntryRow.created_at,
                        PronunciationEntryRow.id,
                    )
                    .limit(_MAX_PRONUNCIATION_LINEAGE_RECORDS + 1)
                )
            )
            if len(successors) > _MAX_PRONUNCIATION_LINEAGE_RECORDS:
                raise ServiceError(
                    500,
                    "PRONUNCIATION_LINEAGE_INVALID",
                    "Pronunciation entry lineage failed bounded integrity checks.",
                )
            for successor in successors:
                if successor.id in visited or len(lineage) >= _MAX_PRONUNCIATION_LINEAGE_RECORDS:
                    raise ServiceError(
                        500,
                        "PRONUNCIATION_LINEAGE_INVALID",
                        "Pronunciation entry lineage failed bounded integrity checks.",
                    )
                visited.add(successor.id)
                lineage.append(successor)
                pending.append(successor)
        return tuple(lineage)

    def _pronunciation_entry_wire(
        self,
        session: Session,
        row: PronunciationEntryRow,
    ) -> dict[str, Any]:
        provider_value = parse_json(row.provider_specific_json, {})
        if not isinstance(provider_value, dict):
            provider_value = {}
        provenance = _public_provenance(row.provenance_json)
        # Mutation replay evidence is persisted privately in provenance details.
        # It is not part of the public pronunciation-entry contract and would
        # otherwise duplicate the bounded clip-ID samples in every entry/list
        # response after the mutation is recorded.
        successor = next(
            (
                value
                for value in self._pronunciation_record_lineage(
                    session,
                    row,
                    direction="successors",
                )
                if value.entry_id != row.entry_id
            ),
            None,
        )
        supersedes = next(
            (
                value
                for value in self._pronunciation_record_lineage(
                    session,
                    row,
                    direction="predecessors",
                )
                if value.entry_id != row.entry_id
            ),
            None,
        )
        return {
            "contractVersion": "1.0.0",
            "entryId": row.entry_id,
            "projectId": row.project_id,
            "dictionaryId": row.dictionary_id,
            "dictionaryRevision": row.dictionary_revision,
            "revision": row.revision,
            "writtenForm": row.written_form,
            "normalizedLookupForm": row.normalized_lookup_form,
            "language": row.language,
            "locale": row.locale,
            "scope": row.scope_type,
            "scopeId": row.scope_target_id,
            "representation": self._pronunciation_representation(row),
            "pronunciation": provider_value.get(
                "pronunciation",
                row.provider_neutral_value or row.ipa_value or "",
            ),
            "ipa": row.ipa_value,
            "providerId": provider_value.get("providerId"),
            "providerCompiledValue": provider_value.get("providerCompiledValue"),
            "caseSensitive": row.case_sensitive,
            "matchRule": "whole_word" if row.whole_word else "phrase",
            "priority": row.priority,
            "actor": {"classification": "human", "actorId": row.actor_id},
            "reason": row.reason,
            "verificationState": row.verification_state,
            "entryFingerprint": row.entry_fingerprint,
            "supersedesEntryId": supersedes.entry_id if supersedes is not None else None,
            "supersededByEntryId": successor.entry_id if successor is not None else None,
            "immutable": True,
            "provenance": provenance,
        }

    def _pronunciation_mutation_wire(
        self,
        session: Session,
        entry: PronunciationEntryRow,
        dictionary: PronunciationDictionaryRow | None,
        *,
        invalidated_clip_ids: Sequence[str],
        invalidated_gate_ids: Sequence[str],
        preserved_clip_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if preserved_clip_ids is None:
            preserved_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionClipRow)
                    .where(AuditionClipRow.project_id == entry.project_id)
                )
                or 0
            )
            preserved = sorted(
                session.scalars(
                    select(AuditionClipRow.id)
                    .where(AuditionClipRow.project_id == entry.project_id)
                    .order_by(AuditionClipRow.created_at, AuditionClipRow.id)
                    .limit(_MAX_MUTATION_CLIP_ID_SAMPLE)
                )
            )
        else:
            preserved_all = sorted(set(preserved_clip_ids))
            preserved_count = len(preserved_all)
            preserved = preserved_all[:_MAX_MUTATION_CLIP_ID_SAMPLE]
        invalidated_all = sorted(set(invalidated_clip_ids))
        invalidated_count = len(invalidated_all)
        invalidated = invalidated_all[:_MAX_MUTATION_CLIP_ID_SAMPLE]
        return {
            "entry": self._pronunciation_entry_wire(session, entry),
            "dictionary": self._dictionary_wire(
                session,
                entry.project_id,
                dictionary,
            ),
            "invalidatedClipIds": invalidated,
            "invalidatedClipCount": invalidated_count,
            "invalidatedClipIdsTruncated": invalidated_count > len(invalidated),
            "preservedClipIds": preserved,
            "preservedClipCount": preserved_count,
            "preservedClipIdsTruncated": preserved_count > len(preserved),
            "invalidatedGateIds": list(invalidated_gate_ids),
        }

    @staticmethod
    def _record_pronunciation_mutation_result(
        entry: PronunciationEntryRow,
        result: dict[str, Any],
    ) -> None:
        result_keys = (
            "invalidatedClipIds",
            "invalidatedClipCount",
            "invalidatedClipIdsTruncated",
            "preservedClipIds",
            "preservedClipCount",
            "preservedClipIdsTruncated",
            "invalidatedGateIds",
        )
        summary = {key: result[key] for key in result_keys}
        provenance = parse_json(entry.provenance_json, {})
        if not isinstance(provenance, dict):
            provenance = {}
        details = provenance.get("details")
        if not isinstance(details, dict):
            details = {}
        details["mutationResult"] = summary
        details["mutationResultFingerprint"] = request_fingerprint(summary)
        provenance["details"] = details
        entry.provenance_json = canonical_json(provenance)

    def _pronunciation_mutation_replay_wire(
        self,
        session: Session,
        entry: PronunciationEntryRow,
        dictionary: PronunciationDictionaryRow | None,
    ) -> dict[str, Any]:
        provenance = parse_json(entry.provenance_json, {})
        details = provenance.get("details") if isinstance(provenance, dict) else None
        summary = details.get("mutationResult") if isinstance(details, dict) else None
        fingerprint = (
            details.get("mutationResultFingerprint") if isinstance(details, dict) else None
        )
        expected_keys = {
            "invalidatedClipIds",
            "invalidatedClipCount",
            "invalidatedClipIdsTruncated",
            "preservedClipIds",
            "preservedClipCount",
            "preservedClipIdsTruncated",
            "invalidatedGateIds",
        }
        if not isinstance(summary, dict) or set(summary) != expected_keys:
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved pronunciation mutation result is unavailable.",
            )
        invalidated_ids = summary.get("invalidatedClipIds")
        preserved_ids = summary.get("preservedClipIds")
        gate_ids = summary.get("invalidatedGateIds")
        invalidated_count = summary.get("invalidatedClipCount")
        preserved_count = summary.get("preservedClipCount")
        invalidated_truncated = summary.get("invalidatedClipIdsTruncated")
        preserved_truncated = summary.get("preservedClipIdsTruncated")
        if (
            not isinstance(invalidated_ids, list)
            or not isinstance(preserved_ids, list)
            or not isinstance(gate_ids, list)
            or len(invalidated_ids) > _MAX_MUTATION_CLIP_ID_SAMPLE
            or len(preserved_ids) > _MAX_MUTATION_CLIP_ID_SAMPLE
            or len(gate_ids) > len(_AUDITION_GATE_IDS)
            or any(not isinstance(value, str) or not value for value in invalidated_ids)
            or any(not isinstance(value, str) or not value for value in preserved_ids)
            or any(value not in _AUDITION_GATE_IDS for value in gate_ids)
            or invalidated_ids != sorted(set(invalidated_ids))
            or preserved_ids != sorted(set(preserved_ids))
            or not isinstance(invalidated_count, int)
            or isinstance(invalidated_count, bool)
            or invalidated_count < len(invalidated_ids)
            or not isinstance(preserved_count, int)
            or isinstance(preserved_count, bool)
            or preserved_count < len(preserved_ids)
            or not isinstance(invalidated_truncated, bool)
            or invalidated_truncated is not (invalidated_count > len(invalidated_ids))
            or not isinstance(preserved_truncated, bool)
            or preserved_truncated is not (preserved_count > len(preserved_ids))
            or fingerprint != request_fingerprint(summary)
        ):
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved pronunciation mutation result failed verification.",
            )
        return {
            "entry": self._pronunciation_entry_wire(session, entry),
            "dictionary": self._dictionary_wire(
                session,
                entry.project_id,
                dictionary,
            ),
            **summary,
        }

    # Governed audition sessions -------------------------------------------------

    def list_sessions(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int,
        role_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        page_size = _bounded_page(limit)
        with self.database.session() as session:
            self._require_project(session, project_id)
            self._reconcile_project_evidence(session, project_id)
            filters = [AuditionSessionRow.project_id == project_id]
            if role_id is not None:
                filters.append(AuditionSessionRow.role_id == role_id)
            total = int(
                session.scalar(select(func.count()).select_from(AuditionSessionRow).where(*filters))
                or 0
            )
            latest_identity = session.scalar(
                select(AuditionSessionRow.id)
                .where(*filters)
                .order_by(
                    AuditionSessionRow.created_at.desc(),
                    AuditionSessionRow.id.desc(),
                )
                .limit(1)
            )
            binding = request_fingerprint(
                {
                    "latestId": latest_identity,
                    "projectId": project_id,
                    "roleId": role_id,
                    "total": total,
                    "type": "audition-sessions",
                }
            )
            offset = _decode_cursor(cursor, binding=binding)
            if offset > total:
                raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
            rows = list(
                session.scalars(
                    select(AuditionSessionRow)
                    .where(*filters)
                    .order_by(
                        AuditionSessionRow.created_at.desc(),
                        AuditionSessionRow.id.desc(),
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            )
            next_offset = offset + len(rows)
            next_cursor = _encode_cursor(binding, next_offset) if next_offset < total else None
            return (
                [self._session_wire(session, row) for row in rows],
                next_cursor,
                total,
            )

    def create_session(
        self,
        *,
        project_id: str,
        request: CreateAuditionSessionRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        request_value = request.model_dump(mode="json", by_alias=True)
        request_hash = request_fingerprint(request_value | {"projectId": project_id})
        now = utc_now()
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            if request.evidence.project_id != project_id:
                raise ServiceError(
                    422,
                    "AUDITION_PROJECT_BINDING_INVALID",
                    "The audition evidence belongs to another project.",
                )
            replay = session.scalar(
                select(AuditionSessionRow).where(
                    AuditionSessionRow.project_id == project_id,
                    AuditionSessionRow.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                if replay.request_fingerprint != request_hash:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another audition session.",
                    )
                return self._session_wire(session, replay)
            session_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionSessionRow)
                    .where(AuditionSessionRow.project_id == project_id)
                )
                or 0
            )
            if session_count >= _MAX_SESSIONS:
                raise ServiceError(
                    409,
                    "AUDITION_SESSION_LIMIT_EXCEEDED",
                    "This project reached the audition session limit.",
                )
            evidence = self._validate_session_evidence(
                session,
                project_id=project_id,
                role_id=request.role_id,
                evidence=request.evidence.model_dump(mode="json", by_alias=True),
            )
            manifest = cast(ModelPackageManifestRow, evidence["manifest"])
            activation_request = request.restricted_local_audition_activation
            governed_activation: dict[str, Any] | None = None
            if manifest.provider_id == FIXTURE_PROVIDER_ID:
                if activation_request is not None:
                    raise ServiceError(
                        422,
                        "AUDITION_RESTRICTED_ACTIVATION_FORBIDDEN",
                        "Fixture auditions do not accept a real-voice activation.",
                    )
            elif manifest.provider_id == KOKORO_PROVIDER_ID:
                if activation_request is None:
                    raise ServiceError(
                        409,
                        "AUDITION_RESTRICTED_ACTIVATION_REQUIRED",
                        "A current restricted local-audition acknowledgement is required.",
                    )
                inventory = _governed_voice_inventory()
                if (
                    activation_request.expected_inventory_fingerprint
                    != inventory["inventoryFingerprint"]
                    or activation_request.expected_warning_fingerprint
                    != _GOVERNED_PRIVATE_AUDITION_WARNING_FINGERPRINT
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_RESTRICTED_ACTIVATION_STALE",
                        "The governed voice inventory or restriction warning changed.",
                    )
                governed_activation = self._build_governed_local_voice_activation(
                    session,
                    evidence=evidence,
                    actor_id=actor_id,
                    acknowledged_at=now,
                    reason=activation_request.reason,
                    request_fingerprint_value=request_hash,
                )
            else:
                raise ServiceError(
                    409,
                    "AUDITION_PROVIDER_BINDING_INVALID",
                    "The selected audition provider is not governed.",
                )
            prior = session.scalar(
                select(AuditionSessionRow)
                .where(
                    AuditionSessionRow.project_id == project_id,
                    AuditionSessionRow.role_id == request.role_id,
                )
                .order_by(
                    AuditionSessionRow.revision.desc(),
                    AuditionSessionRow.created_at.desc(),
                    AuditionSessionRow.id.desc(),
                )
                .limit(1)
            )
            revision = (prior.revision + 1) if prior is not None else 1
            rights = cast(VoiceRightsRecordRow, evidence["rights"])
            voice_runtime_binding = cast(
                VoiceRuntimeBindingRow,
                evidence["voice_runtime_binding"],
            )
            warning_codes = (
                ["RESTRICTED_VOICE_RIGHTS"] if rights.rights_state == "restricted" else []
            )
            row = AuditionSessionRow(
                id=new_id(),
                project_id=project_id,
                revision=revision,
                source_document_id=cast(SourceDocumentRow, evidence["source"]).id,
                source_revision=cast(SourceDocumentRow, evidence["source"]).source_revision,
                extraction_id=cast(DocumentExtractionRow, evidence["extraction"]).id,
                extraction_revision=cast(DocumentExtractionRow, evidence["extraction"]).revision,
                extracted_text_sha256=cast(str, evidence["extracted_text_sha256"]),
                analysis_run_id=cast(AnalysisRunRow, evidence["analysis_run"]).id,
                analysis_snapshot_id=cast(AnalysisSnapshotRow, evidence["analysis_snapshot"]).id,
                analysis_snapshot_fingerprint=cast(
                    AnalysisSnapshotRow,
                    evidence["analysis_snapshot"],
                ).fingerprint,
                analysis_correction_set_fingerprint=cast(
                    CastingRunRow,
                    evidence["casting_run"],
                ).analysis_correction_set_fingerprint,
                casting_run_id=cast(CastingRunRow, evidence["casting_run"]).id,
                cast_snapshot_id=cast(ApprovedCastSnapshotRow, evidence["cast_snapshot"]).id,
                cast_snapshot_revision=cast(
                    ApprovedCastSnapshotRow,
                    evidence["cast_snapshot"],
                ).revision,
                cast_snapshot_fingerprint=cast(
                    ApprovedCastSnapshotRow,
                    evidence["cast_snapshot"],
                ).snapshot_fingerprint,
                phase2_gate_decision_ids_json=canonical_json(
                    [row.id for row in cast(list[AnalysisReviewDecisionRow], evidence["phase2"])]
                ),
                phase3a_gate_decision_ids_json=canonical_json(
                    [row.id for row in cast(list[CastingGateDecisionRow], evidence["phase3a"])]
                ),
                role_id=cast(ProductionRoleRow, evidence["role"]).id,
                assignment_id=cast(CastAssignmentRow, evidence["assignment"]).id,
                assignment_revision=cast(CastAssignmentRow, evidence["assignment"]).revision,
                voice_profile_record_id=cast(VoiceProfileRow, evidence["voice"]).id,
                voice_profile_id=cast(VoiceProfileRow, evidence["voice"]).profile_id,
                voice_profile_version=cast(VoiceProfileRow, evidence["voice"]).profile_version,
                voice_runtime_binding_id=voice_runtime_binding.id,
                voice_runtime_binding_fingerprint=voice_runtime_binding.binding_fingerprint,
                provider_voice_id=voice_runtime_binding.provider_voice_id,
                provider_id=manifest.provider_id,
                provider_version=manifest.provider_version,
                model_id=manifest.model_id,
                model_version=manifest.model_version,
                catalog_revision_id=cast(VoiceCatalogRevisionRow, evidence["catalog"]).id,
                catalog_fingerprint=cast(
                    VoiceCatalogRevisionRow,
                    evidence["catalog"],
                ).catalog_fingerprint,
                rights_record_id=rights.id,
                rights_revision=rights.revision,
                pronunciation_dictionary_record_id=cast(
                    PronunciationDictionaryRow,
                    evidence["dictionary"],
                ).id,
                pronunciation_dictionary_revision=cast(
                    PronunciationDictionaryRow,
                    evidence["dictionary"],
                ).revision,
                pronunciation_dictionary_fingerprint=cast(
                    PronunciationDictionaryRow,
                    evidence["dictionary"],
                ).dictionary_fingerprint,
                runtime_profile_id=cast(SpeechRuntimeProfileRow, evidence["runtime_profile"]).id,
                runtime_profile_fingerprint=cast(
                    SpeechRuntimeProfileRow,
                    evidence["runtime_profile"],
                ).profile_fingerprint,
                model_manifest_id=manifest.id,
                model_installation_record_id=cast(
                    ModelInstallationRow,
                    evidence["installation"],
                ).id,
                model_verification_id=cast(ModelVerificationRow, evidence["verification"]).id,
                model_package_fingerprint=manifest.manifest_fingerprint,
                producer_id=_PRODUCER_ID,
                producer_version=_PRODUCER_VERSION,
                request_fingerprint=request_hash,
                state="draft",
                idempotency_key=request.idempotency_key,
                supersedes_session_id=prior.id if prior is not None else None,
                warnings_json=canonical_json(warning_codes),
                provenance_json=_provenance(
                    "human",
                    input_fingerprint=request_hash,
                    details={
                        **(
                            {"governedLocalVoiceActivation": governed_activation}
                            if governed_activation is not None
                            else {}
                        ),
                        "providerVoiceId": voice_runtime_binding.provider_voice_id,
                        "voiceRuntimeBindingFingerprint": (
                            voice_runtime_binding.binding_fingerprint
                        ),
                        "voiceRuntimeBindingId": voice_runtime_binding.id,
                    },
                ),
                created_at=now,
                published_at=None,
            )
            session.add(row)
            session.flush()
            return self._session_wire(session, row)

    def _validate_session_evidence(
        self,
        session: Session,
        *,
        project_id: str,
        role_id: str,
        evidence: Mapping[str, Any],
        require_current_pronunciation: bool = True,
    ) -> dict[str, Any]:
        def mismatch(code: str, message: str) -> NoReturn:
            raise ServiceError(409, code, message)

        source = session.get(SourceDocumentRow, evidence.get("sourceDocumentId"))
        if (
            source is None
            or source.project_id != project_id
            or source.source_revision != evidence.get("sourceRevision")
        ):
            mismatch("AUDITION_SOURCE_CHANGED", "The source-document evidence is not current.")
        extraction = session.get(DocumentExtractionRow, evidence.get("extractionId"))
        if (
            extraction is None
            or extraction.project_id != project_id
            or extraction.source_document_id != source.id
            or extraction.revision != evidence.get("extractionRevision")
            or extraction.status not in {"complete", "partial"}
            or extraction.text_sha256 != evidence.get("extractedTextSha256")
        ):
            mismatch("AUDITION_EXTRACTION_CHANGED", "The extraction evidence is not current.")
        extracted_text = extraction.exact_text
        if extracted_text is None:
            story = session.scalar(
                select(ImportedStoryRow).where(
                    ImportedStoryRow.project_id == project_id,
                    ImportedStoryRow.extraction_id == extraction.id,
                )
            )
            extracted_text = story.exact_text if story is not None else None
        if extracted_text is None or sha256_text(extracted_text) != evidence.get(
            "extractedTextSha256"
        ):
            mismatch("AUDITION_EXTRACTION_CHANGED", "The exact extracted text failed verification.")

        analysis_run = session.get(AnalysisRunRow, evidence.get("phase2RunId"))
        analysis_snapshot = session.get(
            AnalysisSnapshotRow,
            evidence.get("phase2SnapshotId"),
        )
        try:
            current_story = (
                self._story_intelligence._assert_run_approval_current(
                    session,
                    run=analysis_run,
                    check_correction_set=False,
                )
                if analysis_run is not None
                else None
            )
            current_snapshot = (
                self._story_intelligence._latest_snapshot(session, analysis_run.id)
                if analysis_run is not None
                else None
            )
            current_correction_fingerprint = (
                self._story_intelligence.effective_correction_set_fingerprint(
                    session,
                    run=analysis_run,
                )
                if analysis_run is not None
                else None
            )
        except ServiceError:
            mismatch(
                "AUDITION_PHASE2_CHANGED",
                "The approved import or Phase 2 input evidence is no longer current.",
            )
        snapshot_execution = (
            session.get(AnalysisExecutionRow, analysis_snapshot.execution_id)
            if analysis_snapshot is not None
            else None
        )
        if (
            analysis_run is None
            or analysis_run.project_id != project_id
            or current_story is None
            or current_story.id != analysis_run.story_id
            or analysis_run.source_document_id != source.id
            or analysis_run.source_revision != source.source_revision
            or analysis_run.extraction_id != extraction.id
            or analysis_run.extraction_revision != extraction.revision
            or analysis_run.extracted_text_sha256 != extraction.text_sha256
            or current_correction_fingerprint != evidence.get("phase2CorrectionSetFingerprint")
            or analysis_snapshot is None
            or analysis_snapshot.project_id != project_id
            or analysis_snapshot.run_id != analysis_run.id
            or analysis_snapshot.fingerprint != evidence.get("phase2SnapshotFingerprint")
            or current_snapshot is None
            or current_snapshot.id != analysis_snapshot.id
            or snapshot_execution is None
            or snapshot_execution.run_id != analysis_run.id
            or snapshot_execution.outcome != "succeeded"
            or snapshot_execution.attempt != evidence.get("phase2SnapshotRevision")
        ):
            mismatch("AUDITION_PHASE2_CHANGED", "The Phase 2 evidence is not current.")

        phase2: list[AnalysisReviewDecisionRow] = []
        for gate_id in _PHASE2_GATE_IDS:
            phase2_decision = session.scalar(
                select(AnalysisReviewDecisionRow)
                .where(
                    AnalysisReviewDecisionRow.run_id == analysis_run.id,
                    AnalysisReviewDecisionRow.gate_id == gate_id,
                )
                .order_by(
                    AnalysisReviewDecisionRow.revision.desc(),
                    AnalysisReviewDecisionRow.id.desc(),
                )
                .limit(1)
            )
            if (
                phase2_decision is None
                or phase2_decision.state != "approved"
                or not phase2_decision.eligible
                or phase2_decision.snapshot_id != analysis_snapshot.id
            ):
                mismatch(
                    "AUDITION_PHASE2_APPROVAL_REQUIRED",
                    "All Phase 2 review gates must be currently approved.",
                )
            phase2.append(phase2_decision)

        casting_run = session.get(CastingRunRow, evidence.get("castingRunId"))
        cast_snapshot = session.get(
            ApprovedCastSnapshotRow,
            evidence.get("approvedCastSnapshotId"),
        )
        if (
            casting_run is None
            or casting_run.project_id != project_id
            or casting_run.state != "succeeded"
            or casting_run.analysis_run_id != analysis_run.id
            or casting_run.analysis_snapshot_id != analysis_snapshot.id
            or casting_run.analysis_snapshot_revision != evidence.get("phase2SnapshotRevision")
            or casting_run.analysis_snapshot_fingerprint != analysis_snapshot.fingerprint
            or casting_run.analysis_correction_set_fingerprint != current_correction_fingerprint
            or casting_run.import_review_decision_id != analysis_run.review_decision_id
            or cast_snapshot is None
            or cast_snapshot.project_id != project_id
            or cast_snapshot.casting_run_id != casting_run.id
            or cast_snapshot.revision != evidence.get("approvedCastSnapshotRevision")
            or cast_snapshot.snapshot_fingerprint != evidence.get("approvedCastSnapshotFingerprint")
        ):
            mismatch("AUDITION_CAST_CHANGED", "The approved cast evidence is not current.")
        latest_casting_run = session.scalar(
            select(CastingRunRow)
            .where(
                CastingRunRow.project_id == project_id,
                CastingRunRow.state == "succeeded",
            )
            .order_by(CastingRunRow.created_at.desc(), CastingRunRow.id.desc())
            .limit(1)
        )
        if latest_casting_run is None or latest_casting_run.id != casting_run.id:
            mismatch("AUDITION_CAST_CHANGED", "A newer governed casting run is available.")
        latest_snapshot = session.scalar(
            select(ApprovedCastSnapshotRow)
            .where(ApprovedCastSnapshotRow.casting_run_id == casting_run.id)
            .order_by(
                ApprovedCastSnapshotRow.revision.desc(),
                ApprovedCastSnapshotRow.id.desc(),
            )
            .limit(1)
        )
        if latest_snapshot is None or latest_snapshot.id != cast_snapshot.id:
            mismatch("AUDITION_CAST_CHANGED", "A newer cast snapshot is available.")
        if evidence.get("castAssignmentId") not in self._approved_cast_assignment_ids(
            session,
            cast_snapshot=cast_snapshot,
            casting_run=casting_run,
        ):
            mismatch(
                "AUDITION_CAST_CHANGED",
                "The cast assignment is not part of the approved cast snapshot.",
            )

        frozen_phase2_decision_ids = parse_json(
            casting_run.phase2_gate_decision_ids_json,
            {},
        )
        if not isinstance(frozen_phase2_decision_ids, dict) or any(
            frozen_phase2_decision_ids.get(gate_id) != decision.id
            for gate_id, decision in zip(_PHASE2_GATE_IDS, phase2, strict=True)
        ):
            mismatch(
                "AUDITION_PHASE2_CHANGED",
                "The Phase 2 approval evidence changed after casting.",
            )

        phase3a: list[CastingGateDecisionRow] = []
        authority = self._current_cast_authority(session, project_id)
        if (
            authority is None
            or authority.casting_run.id != casting_run.id
            or authority.cast_snapshot.id != cast_snapshot.id
            or authority.phase3a_decision_ids is None
        ):
            mismatch(
                "AUDITION_PHASE3A_APPROVAL_REQUIRED",
                "All Phase 3A casting gates must be currently approved.",
            )
        for decision_id in authority.phase3a_decision_ids:
            phase3a_decision = session.get(CastingGateDecisionRow, decision_id)
            if phase3a_decision is None:
                mismatch(
                    "AUDITION_PHASE3A_APPROVAL_REQUIRED",
                    "The exact Phase 3A casting authority is unavailable.",
                )
            phase3a.append(phase3a_decision)

        role = session.get(ProductionRoleRow, role_id)
        assignment = session.get(CastAssignmentRow, evidence.get("castAssignmentId"))
        if (
            role is None
            or role.project_id != project_id
            or role.casting_run_id != casting_run.id
            or role.status not in {"active", "unresolved"}
            or assignment is None
            or assignment.project_id != project_id
            or assignment.casting_run_id != casting_run.id
            or assignment.role_id != role.id
            or assignment.revision != evidence.get("castAssignmentRevision")
            or assignment.assignment_state not in {"selected", "locked"}
            or assignment.voice_profile_record_id is None
        ):
            mismatch("AUDITION_ASSIGNMENT_CHANGED", "The cast assignment is not current.")
        latest_assignment = session.scalar(
            select(CastAssignmentRow)
            .where(CastAssignmentRow.role_id == role.id)
            .order_by(CastAssignmentRow.revision.desc(), CastAssignmentRow.id.desc())
            .limit(1)
        )
        invalidation = session.scalar(
            select(CastAssignmentInvalidationRow).where(
                CastAssignmentInvalidationRow.assignment_id == assignment.id
            )
        )
        if latest_assignment is None or latest_assignment.id != assignment.id or invalidation:
            mismatch("AUDITION_ASSIGNMENT_CHANGED", "The cast assignment is no longer current.")

        voice = session.get(VoiceProfileRow, assignment.voice_profile_record_id)
        catalog = session.get(VoiceCatalogRevisionRow, casting_run.catalog_revision_id)
        if (
            voice is None
            or voice.catalog_revision_id != casting_run.catalog_revision_id
            or voice.profile_id != evidence.get("voiceProfileId")
            or voice.profile_version != evidence.get("voiceProfileVersion")
            or voice.state != "active"
            or catalog is None
            or catalog.catalog_id != evidence.get("catalogRevisionId")
            or catalog.catalog_fingerprint != evidence.get("catalogFingerprint")
            or casting_run.catalog_fingerprint != catalog.catalog_fingerprint
        ):
            mismatch("AUDITION_VOICE_CHANGED", "The voice and catalog evidence is not current.")
        rights = session.scalar(
            select(VoiceRightsRecordRow).where(
                VoiceRightsRecordRow.voice_profile_record_id == voice.id,
                VoiceRightsRecordRow.rights_record_id == evidence.get("rightsRecordId"),
                VoiceRightsRecordRow.revision == evidence.get("rightsRecordRevision"),
            )
        )
        latest_rights = session.scalar(
            select(VoiceRightsRecordRow)
            .where(VoiceRightsRecordRow.voice_profile_record_id == voice.id)
            .order_by(VoiceRightsRecordRow.revision.desc(), VoiceRightsRecordRow.id.desc())
            .limit(1)
        )
        governed_private_rights = (
            rights is not None
            and catalog is not None
            and self._governed_private_audition_rights_are_exact(
                session,
                voice=voice,
                rights=rights,
                catalog=catalog,
            )
        )
        generally_eligible_rights = (
            rights is not None
            and rights.rights_state in {"verified", "restricted"}
            and rights.commercial_use_status not in {"unknown", "prohibited"}
            and rights.consent_status not in {"missing", "unknown", "prohibited"}
            and rights.human_verification_status in {"verified", "not_required_fixture"}
        )
        if (
            rights is None
            or latest_rights is None
            or latest_rights.id != rights.id
            or rights.rights_fingerprint != evidence.get("rightsRecordFingerprint")
            or not (generally_eligible_rights or governed_private_rights)
        ):
            mismatch("AUDITION_RIGHTS_INVALID", "The voice-rights evidence is not current.")

        dictionary = (
            self._current_dictionary(session, project_id)
            if require_current_pronunciation
            else session.scalar(
                select(PronunciationDictionaryRow)
                .where(
                    PronunciationDictionaryRow.project_id == project_id,
                    PronunciationDictionaryRow.dictionary_id
                    == evidence.get("pronunciationDictionaryId"),
                    PronunciationDictionaryRow.revision
                    == evidence.get("pronunciationDictionaryRevision"),
                    PronunciationDictionaryRow.dictionary_fingerprint
                    == evidence.get("pronunciationDictionaryFingerprint"),
                )
                .limit(1)
            )
        )
        if (
            dictionary is None
            or dictionary.dictionary_id != evidence.get("pronunciationDictionaryId")
            or dictionary.revision != evidence.get("pronunciationDictionaryRevision")
            or dictionary.dictionary_fingerprint
            != evidence.get("pronunciationDictionaryFingerprint")
        ):
            mismatch(
                "AUDITION_PRONUNCIATION_CHANGED",
                "The pronunciation dictionary evidence is not current.",
            )
        runtime_profile = session.scalar(
            select(SpeechRuntimeProfileRow).where(
                SpeechRuntimeProfileRow.profile_id == evidence.get("runtimeProfileId"),
                SpeechRuntimeProfileRow.profile_fingerprint
                == evidence.get("runtimeProfileFingerprint"),
                SpeechRuntimeProfileRow.provider_id == evidence.get("providerId"),
                SpeechRuntimeProfileRow.active.is_(True),
            )
        )
        manifest = session.scalar(
            select(ModelPackageManifestRow).where(
                ModelPackageManifestRow.package_id == evidence.get("modelPackageId"),
                ModelPackageManifestRow.manifest_fingerprint
                == evidence.get("modelPackageFingerprint"),
                ModelPackageManifestRow.provider_id == evidence.get("providerId"),
                ModelPackageManifestRow.provider_version == evidence.get("providerVersion"),
                ModelPackageManifestRow.model_id == evidence.get("modelId"),
                ModelPackageManifestRow.model_version == evidence.get("modelVersion"),
                ModelPackageManifestRow.revocation_state == "active",
            )
        )
        if runtime_profile is None or manifest is None:
            mismatch(
                "AUDITION_RUNTIME_BINDING_INVALID",
                "The provider, runtime, or model binding is not current.",
            )
        voice_runtime_binding = self._ensure_voice_runtime_binding(
            session,
            voice=voice,
            manifest=manifest,
            runtime_profile=runtime_profile,
        )
        if voice_runtime_binding is None:
            mismatch(
                "AUDITION_VOICE_RUNTIME_BINDING_INVALID",
                "The approved voice has no exact compatible local runtime binding.",
            )
        if (
            voice_runtime_binding.id != evidence.get("voiceRuntimeBindingId")
            or voice_runtime_binding.binding_fingerprint
            != evidence.get("voiceRuntimeBindingFingerprint")
            or voice_runtime_binding.provider_voice_id != evidence.get("providerVoiceId")
        ):
            mismatch(
                "AUDITION_VOICE_RUNTIME_BINDING_CHANGED",
                "The supplied exact voice runtime binding is not current.",
            )
        installation = self._latest_installation(session, manifest.id)
        verification = (
            self._latest_verification(session, installation.installation_id)
            if installation is not None
            else None
        )
        if (
            installation is None
            or installation.state != "active"
            or installation.package_fingerprint != manifest.manifest_fingerprint
            or verification is None
            or verification.outcome != "verified"
            or verification.manifest_fingerprint != manifest.manifest_fingerprint
            or verification.package_fingerprint != manifest.manifest_fingerprint
            or evidence.get("producerVersion") != _PRODUCER_VERSION
        ):
            mismatch(
                "AUDITION_MODEL_NOT_ACTIVE",
                "An exact verified active model package is required.",
            )
        return {
            "analysis_run": analysis_run,
            "analysis_snapshot": analysis_snapshot,
            "assignment": assignment,
            "cast_snapshot": cast_snapshot,
            "casting_run": casting_run,
            "catalog": catalog,
            "dictionary": dictionary,
            "extracted_text_sha256": extraction.text_sha256,
            "extraction": extraction,
            "installation": installation,
            "manifest": manifest,
            "phase2": phase2,
            "phase3a": phase3a,
            "rights": rights,
            "role": role,
            "runtime_profile": runtime_profile,
            "source": source,
            "verification": verification,
            "voice": voice,
            "voice_runtime_binding": voice_runtime_binding,
        }

    def _session_wire(
        self,
        session: Session,
        row: AuditionSessionRow,
        *,
        job_id_override: str | None = None,
    ) -> dict[str, Any]:
        voice_runtime_binding = self._assert_session_voice_runtime_binding(session, row)
        script_count = int(
            session.scalar(
                select(func.count())
                .select_from(AuditionScriptRow)
                .where(AuditionScriptRow.session_id == row.id)
            )
            or 0
        )
        clip_count = int(
            session.scalar(
                select(func.count())
                .select_from(AuditionClipRow)
                .where(AuditionClipRow.session_id == row.id)
            )
            or 0
        )
        latest_job = (
            session.scalar(
                select(JobRow)
                .where(
                    JobRow.project_id == row.project_id,
                    JobRow.type == "generate_audition",
                    JobRow.target_id == row.id,
                )
                .order_by(JobRow.created_at.desc(), JobRow.id.desc())
                .limit(1)
            )
            if job_id_override is None
            else None
        )
        current_review = session.scalar(
            select(AuditionReviewRecordRow)
            .where(
                AuditionReviewRecordRow.project_id == row.project_id,
                AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                AuditionReviewRecordRow.scope_key == row.role_id,
            )
            .order_by(
                AuditionReviewRecordRow.revision.desc(),
                AuditionReviewRecordRow.id.desc(),
            )
            .limit(1)
        )
        latest_review_decision = (
            session.scalar(
                select(AuditionReviewDecisionRow)
                .where(
                    AuditionReviewDecisionRow.project_id == row.project_id,
                    AuditionReviewDecisionRow.gate_id == "per_role_audition_review",
                    AuditionReviewDecisionRow.scope_key == row.role_id,
                )
                .order_by(
                    AuditionReviewDecisionRow.revision.desc(),
                    AuditionReviewDecisionRow.id.desc(),
                )
                .limit(1)
            )
            if current_review is not None
            else None
        )
        approved_clip_id = (
            current_review.clip_id
            if current_review is not None
            and current_review.eligible
            and current_review.session_id == row.id
            and current_review.clip_id is not None
            and latest_review_decision is not None
            and latest_review_decision.review_record_id == current_review.id
            and latest_review_decision.evidence_fingerprint == current_review.evidence_fingerprint
            and latest_review_decision.decision == "approved"
            else None
        )
        return {
            "contractVersion": "1.0.0",
            "auditionSessionId": row.id,
            "projectId": row.project_id,
            "roleId": row.role_id,
            "castAssignmentId": row.assignment_id,
            "castAssignmentRevision": row.assignment_revision,
            "approvedCastSnapshotId": row.cast_snapshot_id,
            "approvedCastSnapshotRevision": row.cast_snapshot_revision,
            "approvedCastSnapshotFingerprint": row.cast_snapshot_fingerprint,
            "voiceRuntimeBindingId": voice_runtime_binding.id,
            "voiceRuntimeBindingFingerprint": voice_runtime_binding.binding_fingerprint,
            "providerVoiceId": voice_runtime_binding.provider_voice_id,
            "voiceRuntimeBinding": self._voice_runtime_binding_wire(voice_runtime_binding),
            "governedLocalVoiceActivation": self._governed_local_voice_activation_wire(
                session,
                row,
            ),
            "providerId": row.provider_id,
            "providerVersion": row.provider_version,
            "modelPackageFingerprint": row.model_package_fingerprint,
            "runtimeProfileFingerprint": row.runtime_profile_fingerprint,
            "pronunciationDictionaryRevision": row.pronunciation_dictionary_revision,
            "pronunciationDictionaryFingerprint": row.pronunciation_dictionary_fingerprint,
            "state": row.state,
            "revision": row.revision,
            "scriptCount": script_count,
            "clipCount": clip_count,
            "approvedClipId": approved_clip_id,
            "jobId": (
                job_id_override
                if job_id_override is not None
                else latest_job.id
                if latest_job is not None
                else None
            ),
            "sessionFingerprint": row.request_fingerprint,
            "createdAt": row.created_at,
            "updatedAt": row.published_at or row.created_at,
            "provenance": _public_provenance(row.provenance_json),
        }

    @staticmethod
    def _voice_runtime_binding_wire(row: VoiceRuntimeBindingRow) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "bindingId": row.id,
            "bindingKind": row.binding_kind,
            "voiceProfileId": row.voice_profile_id,
            "voiceProfileVersion": row.voice_profile_version,
            "voiceProfileFingerprint": row.voice_profile_fingerprint,
            "sourceProviderId": row.source_provider_id,
            "sourceProviderVersion": row.source_provider_version,
            "sourceProviderFingerprint": row.source_provider_fingerprint,
            "sourceModelId": row.source_model_id,
            "sourceModelVersion": row.source_model_version,
            "sourceModelFingerprint": row.source_model_fingerprint,
            "providerId": row.provider_id,
            "providerVersion": row.provider_version,
            "providerVoiceId": row.provider_voice_id,
            "modelId": row.model_id,
            "modelVersion": row.model_version,
            "modelPackageId": row.model_package_id,
            "modelPackageFingerprint": row.model_package_fingerprint,
            "runtimeProfileId": row.runtime_profile_id,
            "runtimeProfileFingerprint": row.runtime_profile_fingerprint,
            "bindingFingerprint": row.binding_fingerprint,
            "active": row.active,
            "provenance": _public_provenance(row.provenance_json),
            "createdAt": row.created_at,
        }

    def _session_evidence_wire(
        self,
        session: Session,
        row: AuditionSessionRow,
        *,
        include_activation: bool = True,
    ) -> dict[str, Any]:
        casting_run = session.get(CastingRunRow, row.casting_run_id)
        catalog = session.get(VoiceCatalogRevisionRow, row.catalog_revision_id)
        rights = session.get(VoiceRightsRecordRow, row.rights_record_id)
        dictionary = session.get(
            PronunciationDictionaryRow,
            row.pronunciation_dictionary_record_id,
        )
        runtime_profile = session.get(SpeechRuntimeProfileRow, row.runtime_profile_id)
        manifest = session.get(ModelPackageManifestRow, row.model_manifest_id)
        if any(
            value is None
            for value in (
                casting_run,
                catalog,
                rights,
                dictionary,
                runtime_profile,
                manifest,
            )
        ):
            raise ServiceError(
                500,
                "AUDITION_SESSION_EVIDENCE_MISSING",
                "The audition session evidence is unavailable.",
            )
        assert casting_run is not None
        assert catalog is not None
        assert rights is not None
        assert dictionary is not None
        assert runtime_profile is not None
        assert manifest is not None
        voice_runtime_binding = self._assert_session_voice_runtime_binding(session, row)
        value = {
            "projectId": row.project_id,
            "sourceDocumentId": row.source_document_id,
            "sourceRevision": row.source_revision,
            "extractionId": row.extraction_id,
            "extractionRevision": row.extraction_revision,
            "extractedTextSha256": row.extracted_text_sha256,
            "phase2RunId": row.analysis_run_id,
            "phase2SnapshotId": row.analysis_snapshot_id,
            "phase2SnapshotRevision": casting_run.analysis_snapshot_revision,
            "phase2SnapshotFingerprint": row.analysis_snapshot_fingerprint,
            "phase2CorrectionSetFingerprint": row.analysis_correction_set_fingerprint,
            "castingRunId": row.casting_run_id,
            "approvedCastSnapshotId": row.cast_snapshot_id,
            "approvedCastSnapshotRevision": row.cast_snapshot_revision,
            "approvedCastSnapshotFingerprint": row.cast_snapshot_fingerprint,
            "castAssignmentId": row.assignment_id,
            "castAssignmentRevision": row.assignment_revision,
            "voiceProfileId": row.voice_profile_id,
            "voiceProfileVersion": row.voice_profile_version,
            "voiceRuntimeBindingId": voice_runtime_binding.id,
            "voiceRuntimeBindingFingerprint": voice_runtime_binding.binding_fingerprint,
            "providerVoiceId": voice_runtime_binding.provider_voice_id,
            "providerId": row.provider_id,
            "providerVersion": row.provider_version,
            "modelId": row.model_id,
            "modelVersion": row.model_version,
            "catalogRevisionId": catalog.catalog_id,
            "catalogFingerprint": row.catalog_fingerprint,
            "rightsRecordId": rights.rights_record_id,
            "rightsRecordRevision": row.rights_revision,
            "rightsRecordFingerprint": rights.rights_fingerprint,
            "pronunciationDictionaryId": dictionary.dictionary_id,
            "pronunciationDictionaryRevision": row.pronunciation_dictionary_revision,
            "pronunciationDictionaryFingerprint": row.pronunciation_dictionary_fingerprint,
            "runtimeProfileId": runtime_profile.profile_id,
            "runtimeProfileFingerprint": row.runtime_profile_fingerprint,
            "modelPackageId": manifest.package_id,
            "modelPackageFingerprint": row.model_package_fingerprint,
            "producerVersion": row.producer_version,
        }
        if include_activation:
            value["governedLocalVoiceActivation"] = self._governed_local_voice_activation_wire(
                session, row
            )
        return value

    def preview_normalization(
        self,
        *,
        project_id: str,
        request: PreviewNormalizationRequest,
    ) -> dict[str, Any]:
        if sha256_text(request.text) != request.source_text_sha256:
            raise ServiceError(
                409,
                "AUDITION_SCRIPT_HASH_MISMATCH",
                "The audition text did not match its declared SHA-256.",
            )
        with self.database.session() as session:
            self._require_project(session, project_id)
            audition_session = session.get(
                AuditionSessionRow,
                request.audition_session_id,
            )
            if audition_session is None or audition_session.project_id != project_id:
                raise not_found("audition session")
            self._assert_session_revision(
                audition_session,
                request.expected_session_revision,
            )
            self._validate_session_evidence(
                session,
                project_id=project_id,
                role_id=audition_session.role_id,
                evidence=self._session_evidence_wire(session, audition_session),
            )
            try:
                normalization = compile_normalization(
                    request.text,
                    accepted_optional_edit_ids=request.accepted_optional_normalization_ids,
                    provider_id=audition_session.provider_id,
                )
                pronunciation_context = self._pronunciation_context(
                    session,
                    audition_session,
                    custom_scope_ids=tuple(request.custom_pronunciation_scope_ids),
                )
                pronunciation = self._compile_pronunciation_plan(
                    session,
                    audition_session,
                    normalization.normalized_text,
                    context=pronunciation_context,
                )
            except (TextNormalizationError, PronunciationError) as exc:
                raise ServiceError(
                    422,
                    "AUDITION_TEXT_PLAN_INVALID",
                    "The audition text could not be compiled safely.",
                ) from exc
            plan_id = stable_id(
                "phase3b-normalization-preview",
                project_id,
                audition_session.id,
                normalization.fingerprint,
                pronunciation.effective_fingerprint,
            )
            return {
                "projectId": audition_session.project_id,
                "auditionSessionId": audition_session.id,
                "auditionSessionRevision": audition_session.revision,
                "providerId": audition_session.provider_id,
                "acceptedOptionalNormalizationIds": sorted(
                    request.accepted_optional_normalization_ids
                ),
                "customPronunciationScopeIds": sorted(request.custom_pronunciation_scope_ids),
                "plan": self._normalization_plan_wire(
                    project_id=project_id,
                    plan_id=plan_id,
                    source_text=request.text,
                    normalization=normalization,
                    applied_entry_ids=[
                        entry_id for entry_id, _revision in pronunciation.dependency_entry_revisions
                    ],
                    include_text=True,
                ),
            }

    def create_script(
        self,
        *,
        project_id: str,
        request: CreateAuditionScriptRequest,
    ) -> dict[str, Any]:
        if sha256_text(request.text) != request.source_text_sha256:
            raise ServiceError(
                409,
                "AUDITION_SCRIPT_HASH_MISMATCH",
                "The audition text did not match its declared SHA-256.",
            )
        request_value = request.model_dump(mode="json", by_alias=True)
        request_hash = request_fingerprint(request_value | {"projectId": project_id})
        idempotency_scope = f"phase3b-script:{project_id}:{request.audition_session_id}"
        now = utc_now()
        publication = _PendingScriptPublication(self.data_dir)
        created_script_id: str | None = None
        with self._script_storage_session(publication) as session:
            self._require_project(session, project_id)
            replay = session.get(
                IdempotencyRow,
                {"scope": idempotency_scope, "key": request.idempotency_key},
            )
            if replay is not None:
                if replay.request_hash != request_hash:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another audition script.",
                    )
                replay_script = session.get(AuditionScriptRow, replay.resource_id)
                if replay_script is None or replay_script.project_id != project_id:
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved audition script is unavailable.",
                    )
                return self._script_creation_wire(session, replay_script)
            audition_session = session.get(
                AuditionSessionRow,
                request.audition_session_id,
            )
            if audition_session is None or audition_session.project_id != project_id:
                raise not_found("audition session")
            self._assert_session_revision(
                audition_session,
                request.expected_session_revision,
            )
            if audition_session.state in {"queued", "generating", "invalidated"}:
                raise ServiceError(
                    409,
                    "AUDITION_SESSION_NOT_EDITABLE",
                    "The audition session cannot accept a script in its current state.",
                )
            self._validate_session_evidence(
                session,
                project_id=project_id,
                role_id=audition_session.role_id,
                evidence=self._session_evidence_wire(session, audition_session),
            )
            script_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditionScriptRow)
                    .where(AuditionScriptRow.session_id == audition_session.id)
                )
                or 0
            )
            if script_count >= _MAX_SCRIPTS_PER_SESSION:
                raise ServiceError(
                    409,
                    "AUDITION_SCRIPT_LIMIT_EXCEEDED",
                    "This audition session reached its script limit.",
                )
            source_entity_binding = self._validate_script_source(
                session,
                audition_session=audition_session,
                request=request,
            )
            try:
                normalization = compile_normalization(
                    request.text,
                    accepted_optional_edit_ids=request.accepted_optional_normalization_ids,
                    provider_id=audition_session.provider_id,
                )
                pronunciation_context = self._pronunciation_context(
                    session,
                    audition_session,
                    source_start=(
                        request.source_span.start if request.source_span is not None else None
                    ),
                    source_end=(
                        request.source_span.end if request.source_span is not None else None
                    ),
                    custom_scope_ids=tuple(request.custom_pronunciation_scope_ids),
                )
                pronunciation = self._compile_pronunciation_plan(
                    session,
                    audition_session,
                    normalization.normalized_text,
                    context=pronunciation_context,
                )
                provider_text = compile_provider_text(
                    normalization.normalized_text,
                    pronunciation,
                )
            except (TextNormalizationError, PronunciationError) as exc:
                raise ServiceError(
                    422,
                    "AUDITION_TEXT_PLAN_INVALID",
                    "The audition text could not be compiled safely.",
                ) from exc
            script_fingerprint = request_fingerprint(
                {
                    "kind": request.kind,
                    "customPronunciationScopeIds": sorted(request.custom_pronunciation_scope_ids),
                    "normalizationPlanFingerprint": normalization.fingerprint,
                    "normalizedTextSha256": normalization.normalized_text_sha256,
                    "pronunciationPlanFingerprint": pronunciation.effective_fingerprint,
                    "roleId": audition_session.role_id,
                    "sessionId": audition_session.id,
                    "sourceDocumentId": request.source_document_id,
                    "sourceAnalysisEntity": (
                        {
                            "entityId": source_entity_binding.entity_id,
                            "collection": source_entity_binding.collection,
                            "effectiveRevision": source_entity_binding.effective_revision,
                            "effectiveFingerprint": source_entity_binding.effective_fingerprint,
                        }
                        if source_entity_binding is not None
                        else None
                    ),
                    "sourceRevision": request.source_revision,
                    "sourceSpan": (
                        request.source_span.model_dump(mode="json", by_alias=True)
                        if request.source_span is not None
                        else None
                    ),
                    "sourceTextSha256": request.source_text_sha256,
                }
            )
            existing = session.scalar(
                select(AuditionScriptRow).where(
                    AuditionScriptRow.session_id == audition_session.id,
                    AuditionScriptRow.script_fingerprint == script_fingerprint,
                )
            )
            if existing is not None:
                raise ServiceError(
                    409,
                    "AUDITION_SCRIPT_ALREADY_EXISTS",
                    "An identical audition script already exists in this session.",
                )
            script_record_id = new_id()
            storage_key = str(
                Path("projects") / project_id / "auditions" / "scripts" / f"{new_id()}.utf8"
            ).replace("\\", "/")
            storage_path = resolve_beneath(self.data_dir, storage_key)
            payload = request.text.encode("utf-8")
            if not payload or len(payload) > _MAX_SCRIPT_BYTES:
                raise ServiceError(
                    413,
                    "AUDITION_SCRIPT_TOO_LARGE",
                    "The audition script exceeded its private storage bound.",
                )
            publication.stage(
                storage_path,
                payload,
                script_record_id=script_record_id,
            )
            prior = session.scalar(
                select(AuditionScriptRow)
                .where(
                    AuditionScriptRow.session_id == audition_session.id,
                    AuditionScriptRow.script_id
                    == stable_id("phase3b-script-lineage", audition_session.id, request.kind),
                )
                .order_by(AuditionScriptRow.revision.desc(), AuditionScriptRow.id.desc())
                .limit(1)
            )
            script = AuditionScriptRow(
                id=script_record_id,
                project_id=project_id,
                session_id=audition_session.id,
                script_id=stable_id(
                    "phase3b-script-lineage",
                    audition_session.id,
                    request.kind,
                ),
                revision=(prior.revision + 1) if prior is not None else 1,
                script_type=request.kind,
                role_id=audition_session.role_id,
                source_document_id=request.source_document_id,
                extraction_id=(
                    audition_session.extraction_id
                    if request.source_document_id is not None
                    else None
                ),
                source_start_offset=(
                    request.source_span.start if request.source_span is not None else None
                ),
                source_end_offset=(
                    request.source_span.end if request.source_span is not None else None
                ),
                source_analysis_entity_id=(
                    source_entity_binding.entity_id if source_entity_binding is not None else None
                ),
                source_analysis_entity_collection=(
                    source_entity_binding.collection if source_entity_binding is not None else None
                ),
                source_analysis_entity_effective_revision=(
                    source_entity_binding.effective_revision
                    if source_entity_binding is not None
                    else None
                ),
                source_analysis_entity_fingerprint=(
                    source_entity_binding.effective_fingerprint
                    if source_entity_binding is not None
                    else None
                ),
                exact_text_sha256=request.source_text_sha256,
                text_storage_key=storage_key,
                synthetic_text_id=(
                    stable_id("phase3b-synthetic-script", script_fingerprint)
                    if request.source_document_id is None
                    else None
                ),
                text_codepoint_count=len(request.text),
                script_fingerprint=script_fingerprint,
                supersedes_script_record_id=prior.id if prior is not None else None,
                provenance_json=_provenance(
                    "human",
                    input_fingerprint=request_hash,
                    details={
                        "customPronunciationScopeIds": list(request.custom_pronunciation_scope_ids)
                    },
                ),
                created_at=now,
            )
            plan_id = new_id()
            pronunciation_wire = self._pronunciation_plan_wire(
                project_id=project_id,
                session=audition_session,
                plan_id=plan_id,
                pronunciation=pronunciation,
                context=pronunciation_context,
                escaped_provider_payload_sha256=sha256_text(provider_text),
            )
            transformation_wire = self._normalization_transformations(
                request.text,
                normalization,
                include_text=False,
            )
            plan_fingerprint = request_fingerprint(
                {
                    "normalizationFingerprint": normalization.fingerprint,
                    "pronunciationPlanFingerprint": pronunciation.effective_fingerprint,
                    "scriptFingerprint": script_fingerprint,
                }
            )
            plan = TextNormalizationPlanRow(
                id=plan_id,
                project_id=project_id,
                session_id=audition_session.id,
                script_id=script.id,
                revision=1,
                normalization_profile_id=normalization.profile_id,
                normalization_profile_version=normalization.profile_version,
                provider_id=normalization.provider_id,
                provider_version=audition_session.provider_version,
                original_text_sha256=normalization.source_sha256,
                normalized_text_sha256=normalization.normalized_text_sha256,
                transformations_json=canonical_json(transformation_wire),
                pronunciation_dictionary_record_id=(
                    audition_session.pronunciation_dictionary_record_id
                ),
                pronunciation_dictionary_revision=(
                    audition_session.pronunciation_dictionary_revision
                ),
                pronunciation_dictionary_fingerprint=(
                    audition_session.pronunciation_dictionary_fingerprint
                ),
                pronunciation_entry_ids_json=canonical_json(
                    [entry_id for entry_id, _revision in pronunciation.dependency_entry_revisions]
                ),
                compiled_pronunciation_json=canonical_json(pronunciation_wire),
                pronunciation_plan_fingerprint=pronunciation.effective_fingerprint,
                unsupported_characters_json=canonical_json(
                    list(normalization.unsupported_character_code_points)
                ),
                warnings_json=canonical_json(list(normalization.warnings)),
                human_approval_required=normalization.human_review_required,
                plan_fingerprint=plan_fingerprint,
                provenance_json=_provenance("application", input_fingerprint=request_hash),
                created_at=now,
            )
            session.add(script)
            session.add(plan)
            session.add(
                IdempotencyRow(
                    scope=idempotency_scope,
                    key=request.idempotency_key,
                    request_hash=request_hash,
                    resource_id=script.id,
                    created_at=now,
                )
            )
            session.flush()
            created_script_id = script.id
        if created_script_id is None:
            raise ServiceError(
                500,
                "AUDITION_SCRIPT_EVIDENCE_MISSING",
                "The audition script evidence is unavailable.",
            )
        try:
            publication.finalize()
        except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
            raise ServiceError(
                500,
                "AUDITION_SCRIPT_STORAGE_FINALIZATION_FAILED",
                "Private audition script storage finalization is pending safe recovery.",
            ) from exc
        with self.database.session() as session:
            created_script = session.get(AuditionScriptRow, created_script_id)
            if created_script is None or created_script.project_id != project_id:
                raise ServiceError(
                    500,
                    "AUDITION_SCRIPT_EVIDENCE_MISSING",
                    "The audition script evidence is unavailable.",
                )
            return self._script_creation_wire(session, created_script)

    @staticmethod
    def _assert_session_revision(row: AuditionSessionRow, expected_revision: int) -> None:
        if row.revision != expected_revision:
            raise ServiceError(
                409,
                "AUDITION_SESSION_CHANGED",
                "The audition session changed; refresh before continuing.",
            )

    def _validate_script_source(
        self,
        session: Session,
        *,
        audition_session: AuditionSessionRow,
        request: CreateAuditionScriptRequest,
    ) -> _ScriptSourceEntityBinding | None:
        if request.source_document_id is None:
            return None
        source = session.get(SourceDocumentRow, request.source_document_id)
        extraction = session.get(DocumentExtractionRow, audition_session.extraction_id)
        if (
            source is None
            or source.id != audition_session.source_document_id
            or source.project_id != audition_session.project_id
            or source.source_revision != request.source_revision
            or extraction is None
            or extraction.source_document_id != source.id
            or request.source_span is None
        ):
            raise ServiceError(
                409,
                "AUDITION_SOURCE_SPAN_CHANGED",
                "The manuscript source binding is not current.",
            )
        exact_text = extraction.exact_text
        if exact_text is None:
            story = session.scalar(
                select(ImportedStoryRow).where(ImportedStoryRow.extraction_id == extraction.id)
            )
            exact_text = story.exact_text if story is not None else None
        span = request.source_span
        if (
            exact_text is None
            or span.end > len(exact_text)
            or exact_text[span.start : span.end] != request.text
            or sha256_text(exact_text[span.start : span.end]) != request.source_text_sha256
        ):
            raise ServiceError(
                409,
                "AUDITION_SOURCE_SPAN_CHANGED",
                "The exact manuscript source span failed verification.",
            )
        if request.kind not in {"role_dialogue_excerpt", "narrator_excerpt"}:
            return None
        return self._resolve_script_source_entity_binding(
            session,
            audition_session=audition_session,
            script_kind=request.kind,
            source_start=span.start,
            source_end=span.end,
        )

    def _resolve_script_source_entity_binding(
        self,
        session: Session,
        *,
        audition_session: AuditionSessionRow,
        script_kind: str,
        source_start: int,
        source_end: int,
    ) -> _ScriptSourceEntityBinding:
        collection = (
            "dialogue-lines" if script_kind == "role_dialogue_excerpt" else "narration-spans"
        )
        run = session.get(AnalysisRunRow, audition_session.analysis_run_id)
        story = session.get(ImportedStoryRow, run.story_id) if run is not None else None
        role = session.get(ProductionRoleRow, audition_session.role_id)
        rows = list(
            session.scalars(
                select(AnalysisEntityRow)
                .where(
                    AnalysisEntityRow.project_id == audition_session.project_id,
                    AnalysisEntityRow.run_id == audition_session.analysis_run_id,
                    AnalysisEntityRow.snapshot_id == audition_session.analysis_snapshot_id,
                    AnalysisEntityRow.collection == collection,
                    AnalysisEntityRow.start_offset == source_start,
                    AnalysisEntityRow.end_offset == source_end,
                )
                .order_by(AnalysisEntityRow.ordinal, AnalysisEntityRow.id)
                .limit(2)
            )
        )
        if (
            run is None
            or story is None
            or role is None
            or role.project_id != audition_session.project_id
            or role.casting_run_id != audition_session.casting_run_id
            or role.analysis_run_id != audition_session.analysis_run_id
            or role.analysis_snapshot_id != audition_session.analysis_snapshot_id
            or len(rows) != 1
        ):
            raise ServiceError(
                409,
                "AUDITION_SCRIPT_SEMANTIC_SOURCE_INVALID",
                "The audition script does not resolve to one current Phase 2 entity.",
            )
        entity = rows[0]
        effective = self._story_intelligence._entity_dict(
            session,
            run=run,
            entity=entity,
            story=story,
        )
        effective_revision = effective.get("effectiveRevision")
        effective_fingerprint = effective.get("effectiveValueFingerprint")
        if (
            not isinstance(effective_revision, int)
            or effective_revision < 1
            or not isinstance(effective_fingerprint, str)
            or re.fullmatch(r"[a-f0-9]{64}", effective_fingerprint) is None
        ):
            raise ServiceError(
                409,
                "AUDITION_SCRIPT_SEMANTIC_SOURCE_INVALID",
                "The current Phase 2 source entity failed integrity verification.",
            )
        if script_kind == "role_dialogue_excerpt":
            attribution = effective.get("effectiveAttribution")
            speaker_id = (
                attribution.get("speakerCharacterId") if isinstance(attribution, dict) else None
            )
            if not isinstance(speaker_id, str):
                speaker_id = effective.get("effectiveSpeakerId")
            if (
                role.role_type in {"primary_narrator", "secondary_narrator"}
                or not isinstance(speaker_id, str)
                or not role.character_id
                or speaker_id != role.character_id
            ):
                raise ServiceError(
                    409,
                    "AUDITION_SCRIPT_ROLE_SOURCE_MISMATCH",
                    "The dialogue source speaker does not map to the audition role.",
                )
        else:
            if role.role_type not in {"primary_narrator", "secondary_narrator"}:
                raise ServiceError(
                    409,
                    "AUDITION_SCRIPT_ROLE_SOURCE_MISMATCH",
                    "A narration source requires a narrator audition role.",
                )
            if role.phase2_entity_id is not None and entity.id != role.phase2_entity_id:
                raise ServiceError(
                    409,
                    "AUDITION_SCRIPT_ROLE_SOURCE_MISMATCH",
                    "The narration source does not map to the exact narrator role.",
                )
        return _ScriptSourceEntityBinding(
            entity_id=entity.id,
            collection=collection,
            effective_revision=effective_revision,
            effective_fingerprint=effective_fingerprint,
        )

    def _assert_script_source_entity_binding(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
        script: AuditionScriptRow,
    ) -> None:
        semantic_kind = script.script_type in {
            "role_dialogue_excerpt",
            "narrator_excerpt",
        }
        persisted_values = (
            script.source_analysis_entity_id,
            script.source_analysis_entity_collection,
            script.source_analysis_entity_effective_revision,
            script.source_analysis_entity_fingerprint,
        )
        if not semantic_kind:
            if any(value is not None for value in persisted_values):
                raise ServiceError(
                    409,
                    "AUDITION_SCRIPT_SOURCE_PROVENANCE_STALE",
                    "The audition script source provenance is inconsistent.",
                )
            return
        if (
            script.source_start_offset is None
            or script.source_end_offset is None
            or any(value is None for value in persisted_values)
        ):
            raise ServiceError(
                409,
                "AUDITION_SCRIPT_SOURCE_PROVENANCE_STALE",
                "The audition script source provenance is incomplete.",
            )
        current = self._resolve_script_source_entity_binding(
            session,
            audition_session=audition_session,
            script_kind=script.script_type,
            source_start=script.source_start_offset,
            source_end=script.source_end_offset,
        )
        if (
            current.entity_id != script.source_analysis_entity_id
            or current.collection != script.source_analysis_entity_collection
            or current.effective_revision != script.source_analysis_entity_effective_revision
            or current.effective_fingerprint != script.source_analysis_entity_fingerprint
        ):
            raise ServiceError(
                409,
                "AUDITION_SCRIPT_SOURCE_PROVENANCE_STALE",
                "The Phase 2 entity bound to the audition script changed.",
            )

    def _effective_pronunciation_rows(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
    ) -> list[PronunciationEntryRow]:
        latest = self._latest_pronunciation_entry_rows(
            session,
            audition_session.project_id,
        )
        by_record = {
            row.id: row
            for row in self._bounded_pronunciation_history_rows(
                session,
                audition_session.project_id,
            )
        }
        superseded_stable_ids: set[str] = set()
        for row in latest:
            if row.verification_state != "approved":
                continue
            for prior in self._pronunciation_record_lineage(
                session,
                row,
                direction="predecessors",
                records_by_id=by_record,
            ):
                if prior.entry_id != row.entry_id:
                    superseded_stable_ids.add(prior.entry_id)
        return [
            row
            for row in latest
            if row.verification_state == "approved"
            and row.entry_id not in superseded_stable_ids
            and (
                row.scope_type == "project"
                or row.scope_target_id == audition_session.role_id
                or row.scope_type in {"chapter", "scene", "custom"}
            )
        ]

    def _pronunciation_context(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
        *,
        source_start: int | None = None,
        source_end: int | None = None,
        custom_scope_ids: tuple[str, ...] = (),
    ) -> PronunciationContext:
        voice = session.get(
            VoiceProfileRow,
            audition_session.voice_profile_record_id,
        )
        chapter_id: str | None = None
        scene_id: str | None = None
        analysis_run = session.get(AnalysisRunRow, audition_session.analysis_run_id)
        if analysis_run is not None and source_start is not None and source_end is not None:
            story = session.get(ImportedStoryRow, analysis_run.story_id)
            if story is not None:
                chapter_id, scene_id = (
                    self._story_intelligence.effective_structure_context_for_span(
                        session,
                        run=analysis_run,
                        story=story,
                        start_offset=source_start,
                        end_offset=source_end,
                    )
                )
        return PronunciationContext(
            locale=voice.locale if voice is not None else "en-US",
            role_id=audition_session.role_id,
            chapter_id=chapter_id,
            scene_id=scene_id,
            custom_scope_ids=custom_scope_ids,
        )

    def _script_pronunciation_context(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
        script: AuditionScriptRow,
    ) -> PronunciationContext:
        provenance = parse_json(script.provenance_json, {})
        details = provenance.get("details", {}) if isinstance(provenance, dict) else {}
        custom_scope_values = (
            details.get("customPronunciationScopeIds", []) if isinstance(details, dict) else []
        )
        if (
            not isinstance(custom_scope_values, list)
            or len(custom_scope_values) > 50
            or len(custom_scope_values) != len(set(custom_scope_values))
            or any(
                not isinstance(value, str) or not value or len(value) > 128
                for value in custom_scope_values
            )
        ):
            raise ServiceError(
                409,
                "AUDITION_TEXT_PLAN_STALE",
                "The audition pronunciation context is invalid.",
            )
        return self._pronunciation_context(
            session,
            audition_session,
            source_start=script.source_start_offset,
            source_end=script.source_end_offset,
            custom_scope_ids=tuple(custom_scope_values),
        )

    def _compile_pronunciation_plan(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
        text: str,
        *,
        context: PronunciationContext | None = None,
    ) -> PronunciationPlan:
        values: list[PronunciationEntry] = []
        for row in self._effective_pronunciation_rows(session, audition_session):
            provider = parse_json(row.provider_specific_json, {})
            if not isinstance(provider, dict):
                provider = {}
            representation = self._pronunciation_representation(row)
            if (
                representation == "provider_specific"
                and provider.get("providerId") != audition_session.provider_id
            ):
                continue
            pronunciation_value = (
                provider.get("providerCompiledValue")
                if representation == "provider_specific"
                else row.ipa_value
                if representation == "ipa"
                else row.provider_neutral_value
            )
            if not isinstance(pronunciation_value, str) or not pronunciation_value:
                continue
            scope = "role" if row.scope_type in {"narrator", "character_role"} else row.scope_type
            if scope not in {"project", "role", "chapter", "scene", "custom"}:
                continue
            values.append(
                PronunciationEntry(
                    entry_id=row.entry_id,
                    revision=row.revision,
                    grapheme=row.written_form,
                    pronunciation=pronunciation_value,
                    representation="ipa" if representation == "ipa" else "neutral",
                    locale=row.locale or row.language,
                    scope=cast(Any, scope),
                    scope_id=row.scope_target_id,
                    priority=row.priority,
                    case_sensitive=row.case_sensitive,
                    whole_word=row.whole_word,
                    approved=True,
                    superseded=False,
                )
            )
        compiled = compile_pronunciation_plan(
            text,
            entries=values,
            dictionary_revision=audition_session.pronunciation_dictionary_revision,
            context=context or self._pronunciation_context(session, audition_session),
        )
        return PronunciationPlan(
            dictionary_revision=compiled.dictionary_revision,
            dictionary_fingerprint=audition_session.pronunciation_dictionary_fingerprint,
            source_text_sha256=compiled.source_text_sha256,
            spans=compiled.spans,
            dependency_entry_revisions=compiled.dependency_entry_revisions,
            effective_fingerprint=compiled.effective_fingerprint,
        )

    @staticmethod
    def _normalization_transformations(
        source_text: str,
        plan: NormalizationPlan,
        *,
        include_text: bool,
    ) -> list[dict[str, Any]]:
        accepted = set(plan.accepted_optional_edit_ids)
        transformations: list[dict[str, Any]] = []
        destination_delta = 0
        for edit in propose_normalization(source_text):
            applied = edit.required_by_provider or edit.edit_id in accepted
            destination_start = edit.source_start + destination_delta
            replacement = edit.replacement if applied else edit.original
            destination_end = destination_start + len(replacement)
            value: dict[str, Any] = {
                "transformationId": edit.edit_id,
                "kind": edit.kind,
                "sourceSpan": {"start": edit.source_start, "end": edit.source_end},
                "destinationSpan": {
                    "start": destination_start,
                    "end": destination_end,
                },
                "originalTextSha256": sha256_text(edit.original),
                "replacementTextSha256": sha256_text(edit.replacement),
                "reasonCode": f"NORMALIZATION_{edit.kind.upper()}",
                "requiredByProvider": edit.required_by_provider,
                "humanApprovalRequired": not edit.required_by_provider,
                "approved": applied,
            }
            if include_text:
                value["originalText"] = edit.original
                value["replacementText"] = edit.replacement
            transformations.append(value)
            if applied:
                destination_delta += len(edit.replacement) - len(edit.original)
        return transformations

    def _validated_normalization_plan(
        self,
        *,
        audition_session: AuditionSessionRow,
        script: AuditionScriptRow,
        plan: TextNormalizationPlanRow,
        source_text: str,
    ) -> NormalizationPlan:
        def invalid() -> NoReturn:
            raise ServiceError(
                500,
                "AUDITION_NORMALIZATION_PLAN_INVALID",
                "The audition normalization evidence failed verification.",
            )

        try:
            transformations = parse_json(plan.transformations_json, [])
            unsupported = parse_json(plan.unsupported_characters_json, [])
            warnings = parse_json(plan.warnings_json, [])
        except (json.JSONDecodeError, TypeError, ValueError):
            invalid()
        if (
            not isinstance(transformations, list)
            or len(transformations) > MAX_NORMALIZATION_EDITS
            or not isinstance(unsupported, list)
            or len(unsupported) > MAX_UNSUPPORTED_CHARACTER_CODE_POINTS
            or not isinstance(warnings, list)
            or len(warnings) > MAX_NORMALIZATION_WARNINGS
        ):
            invalid()
        unsupported_values: list[str] = []
        for value in unsupported:
            if not isinstance(value, str) or re.fullmatch(r"U\+[0-9A-F]{4,6}", value) is None:
                invalid()
            code_point = int(value[2:], 16)
            if code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
                invalid()
            unsupported_values.append(value)
        if (
            len(set(unsupported_values)) != len(unsupported_values)
            or unsupported_values
            != sorted(unsupported_values, key=lambda value: int(value[2:], 16))
            or any(
                not isinstance(value, str) or not value or len(value) > 128 for value in warnings
            )
        ):
            invalid()
        accepted_optional_ids: list[str] = []
        for value in transformations:
            if not isinstance(value, dict):
                invalid()
            if value.get("approved") is True and value.get("requiredByProvider") is False:
                transformation_id = value.get("transformationId")
                if not isinstance(transformation_id, str):
                    invalid()
                accepted_optional_ids.append(transformation_id)
        if (
            len(set(accepted_optional_ids)) != len(accepted_optional_ids)
            or plan.project_id != audition_session.project_id
            or plan.project_id != script.project_id
            or plan.session_id != audition_session.id
            or plan.session_id != script.session_id
            or plan.script_id != script.id
            or plan.provider_id != audition_session.provider_id
            or plan.provider_version != audition_session.provider_version
            or plan.normalization_profile_id != NORMALIZATION_PROFILE_ID
            or plan.normalization_profile_version != NORMALIZATION_PROFILE_VERSION
        ):
            invalid()
        try:
            normalization = compile_normalization(
                source_text,
                accepted_optional_edit_ids=accepted_optional_ids,
                provider_id=plan.provider_id,
                profile_id=plan.normalization_profile_id,
                profile_version=plan.normalization_profile_version,
            )
        except TextNormalizationError:
            invalid()
        expected_transformations = self._normalization_transformations(
            source_text,
            normalization,
            include_text=False,
        )
        expected_plan_fingerprint = request_fingerprint(
            {
                "normalizationFingerprint": normalization.fingerprint,
                "pronunciationPlanFingerprint": plan.pronunciation_plan_fingerprint,
                "scriptFingerprint": script.script_fingerprint,
            }
        )
        if (
            transformations != expected_transformations
            or canonical_json(transformations) != plan.transformations_json
            or unsupported_values != list(normalization.unsupported_character_code_points)
            or canonical_json(unsupported_values) != plan.unsupported_characters_json
            or warnings != list(normalization.warnings)
            or canonical_json(warnings) != plan.warnings_json
            or plan.human_approval_required != normalization.human_review_required
            or plan.original_text_sha256 != normalization.source_sha256
            or plan.original_text_sha256 != script.exact_text_sha256
            or plan.normalized_text_sha256 != normalization.normalized_text_sha256
            or plan.plan_fingerprint != expected_plan_fingerprint
        ):
            invalid()
        return normalization

    def _normalization_plan_wire(
        self,
        *,
        project_id: str,
        plan_id: str,
        source_text: str,
        normalization: NormalizationPlan,
        applied_entry_ids: Sequence[str],
        include_text: bool,
        plan_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "normalizationPlanId": plan_id,
            "projectId": project_id,
            "originalTextSha256": normalization.source_sha256,
            "normalizedTextSha256": normalization.normalized_text_sha256,
            "providerId": normalization.provider_id,
            "profileId": normalization.profile_id,
            "profileVersion": normalization.profile_version,
            "transformations": self._normalization_transformations(
                source_text,
                normalization,
                include_text=include_text,
            ),
            "appliedPronunciationEntryIds": list(applied_entry_ids),
            "unsupportedCharacterCodePoints": list(normalization.unsupported_character_code_points),
            "warnings": list(normalization.warnings),
            "humanReviewRequired": normalization.human_review_required,
            "planFingerprint": plan_fingerprint or normalization.fingerprint,
            "provenance": _public_provenance(_provenance("application")),
        }

    @staticmethod
    def _pronunciation_plan_wire(
        *,
        project_id: str,
        session: AuditionSessionRow,
        plan_id: str,
        pronunciation: PronunciationPlan,
        context: PronunciationContext,
        escaped_provider_payload_sha256: str,
    ) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "pronunciationPlanId": stable_id(
                "phase3b-pronunciation-plan",
                plan_id,
                pronunciation.effective_fingerprint,
            ),
            "projectId": project_id,
            "dictionaryId": stable_id("phase3b-pronunciation-dictionary", project_id),
            "dictionaryRevision": pronunciation.dictionary_revision,
            "dictionaryFingerprint": pronunciation.dictionary_fingerprint,
            "sourceTextSha256": pronunciation.source_text_sha256,
            "locale": context.locale,
            "roleId": session.role_id,
            "scopeContext": {
                "chapterId": context.chapter_id,
                "sceneId": context.scene_id,
                "customScopeIds": list(context.custom_scope_ids),
            },
            "appliedEntries": [
                {
                    "sourceSpan": {
                        "start": span.source_start,
                        "end": span.source_end,
                    },
                    "entryId": span.entry_id,
                    "entryRevision": span.entry_revision,
                    "writtenFormSha256": sha256_text(span.grapheme),
                    "compiledValueSha256": sha256_text(span.pronunciation),
                    "representation": (
                        "ipa" if span.representation == "ipa" else "provider_neutral"
                    ),
                }
                for span in pronunciation.spans
            ],
            "dependencyEntryRevisions": [
                {"entryId": entry_id, "revision": revision}
                for entry_id, revision in pronunciation.dependency_entry_revisions
            ],
            "providerId": session.provider_id,
            "escapedProviderPayloadSha256": escaped_provider_payload_sha256,
            "planFingerprint": pronunciation.effective_fingerprint,
            "provenance": _public_provenance(_provenance("application")),
        }

    def _read_script_text(self, row: AuditionScriptRow) -> str:
        if row.text_storage_key is None:
            raise ServiceError(
                500,
                "AUDITION_SCRIPT_STORAGE_MISSING",
                "The private audition script is unavailable.",
            )
        try:
            path = resolve_beneath(self.data_dir, row.text_storage_key)
            _resolved, text = _verified_script_text(
                self.data_dir,
                path,
                expected_sha256=row.exact_text_sha256,
                expected_codepoint_count=row.text_codepoint_count,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise ServiceError(
                500,
                "AUDITION_SCRIPT_STORAGE_INVALID",
                "The private audition script failed verification.",
            ) from exc
        return text

    def _script_creation_wire(
        self,
        session: Session,
        script: AuditionScriptRow,
    ) -> dict[str, Any]:
        audition_session = session.get(AuditionSessionRow, script.session_id)
        plan = session.scalar(
            select(TextNormalizationPlanRow)
            .where(TextNormalizationPlanRow.script_id == script.id)
            .order_by(TextNormalizationPlanRow.revision.desc(), TextNormalizationPlanRow.id.desc())
            .limit(1)
        )
        if audition_session is None or plan is None:
            raise ServiceError(
                500,
                "AUDITION_SCRIPT_EVIDENCE_MISSING",
                "The audition script evidence is unavailable.",
            )
        self._assert_script_source_entity_binding(session, audition_session, script)
        text = self._read_script_text(script)
        normalization = self._validated_normalization_plan(
            audition_session=audition_session,
            script=script,
            plan=plan,
            source_text=text,
        )
        pronunciation_wire = parse_json(plan.compiled_pronunciation_json, {})
        script_wire = self._script_wire(
            script,
            plan,
            text=text,
        )
        if script.source_document_id is not None:
            script_wire["sourceRevision"] = audition_session.source_revision
        return {
            "script": script_wire,
            "normalizationPlan": self._normalization_plan_wire(
                project_id=script.project_id,
                plan_id=plan.id,
                source_text=text,
                normalization=normalization,
                applied_entry_ids=parse_json(plan.pronunciation_entry_ids_json, []),
                include_text=True,
                plan_fingerprint=plan.plan_fingerprint,
            ),
            "pronunciationPlan": pronunciation_wire,
            "session": self._session_wire(session, audition_session),
        }

    @staticmethod
    def _script_wire(
        script: AuditionScriptRow,
        plan: TextNormalizationPlanRow,
        *,
        text: str | None = None,
    ) -> dict[str, Any]:
        pronunciation = parse_json(plan.compiled_pronunciation_json, {})
        value: dict[str, Any] = {
            "contractVersion": "1.0.0",
            "auditionScriptId": script.id,
            "auditionSessionId": script.session_id,
            "projectId": script.project_id,
            "roleId": script.role_id,
            "kind": script.script_type,
            "sourceTextSha256": script.exact_text_sha256,
            "sourceSpan": (
                {
                    "start": script.source_start_offset,
                    "end": script.source_end_offset,
                }
                if script.source_start_offset is not None and script.source_end_offset is not None
                else None
            ),
            "sourceDocumentId": script.source_document_id,
            "sourceAnalysisEntity": (
                {
                    "entityId": script.source_analysis_entity_id,
                    "collection": script.source_analysis_entity_collection,
                    "effectiveRevision": script.source_analysis_entity_effective_revision,
                    "effectiveFingerprint": script.source_analysis_entity_fingerprint,
                }
                if script.source_analysis_entity_id is not None
                else None
            ),
            "sourceRevision": None,
            "normalizedTextSha256": plan.normalized_text_sha256,
            "normalizationPlanId": plan.id,
            "pronunciationPlanId": pronunciation.get("pronunciationPlanId"),
            "localOnly": True,
            "scriptFingerprint": script.script_fingerprint,
            "createdAt": script.created_at,
        }
        if text is not None:
            value["text"] = text
        return value

    # Queue and clip reads --------------------------------------------------------

    def queue_generation(
        self,
        *,
        project_id: str,
        request: GenerateAuditionRequest,
        jobs: JobRepository,
    ) -> dict[str, Any]:
        preview = request.preview
        preview_value = preview.model_dump(mode="json", by_alias=True)
        supplied_request_fingerprint = preview_value.pop("requestFingerprint")
        expected_request_fingerprint = request_fingerprint(preview_value)
        if supplied_request_fingerprint != expected_request_fingerprint:
            raise ServiceError(
                409,
                "AUDITION_REQUEST_FINGERPRINT_INVALID",
                "The audition generation request failed fingerprint verification.",
            )
        controls_value = preview.provider_controls.model_dump(
            mode="json",
            by_alias=True,
        )
        supplied_controls_fingerprint = controls_value.pop("controlsFingerprint")
        if supplied_controls_fingerprint != request_fingerprint(controls_value):
            raise ServiceError(
                409,
                "AUDITION_CONTROLS_FINGERPRINT_INVALID",
                "The speech controls failed fingerprint verification.",
            )
        expected_job_payload = {
            "requestFingerprint": supplied_request_fingerprint,
            "schemaVersion": 1,
            "scriptId": preview.audition_script_id,
            "sessionId": preview.audition_session_id,
        }
        expected_job_request_hash = request_fingerprint(
            {
                "inputFingerprint": supplied_request_fingerprint,
                "inputRevision": preview.audition_session_revision,
                "payload": expected_job_payload,
                "projectId": project_id,
                "sessionId": preview.audition_session_id,
                "type": "generate_audition",
            }
        )
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            audition_session = session.get(AuditionSessionRow, preview.audition_session_id)
            if audition_session is None or audition_session.project_id != project_id:
                raise not_found("audition session")
            self._assert_session_revision(
                audition_session,
                preview.audition_session_revision,
            )
            existing_request = session.scalar(
                select(SpeechProviderRequestRow)
                .where(
                    SpeechProviderRequestRow.session_id == audition_session.id,
                    SpeechProviderRequestRow.idempotency_key == preview.idempotency_key,
                )
                .order_by(
                    SpeechProviderRequestRow.attempt.desc(),
                    SpeechProviderRequestRow.id.desc(),
                )
                .limit(1)
            )
            existing_job_idempotency = session.get(
                IdempotencyRow,
                {
                    "scope": f"create_audition_job:{project_id}",
                    "key": preview.idempotency_key,
                },
            )
            if existing_request is not None:
                if existing_request.request_fingerprint != supplied_request_fingerprint:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another audition.",
                    )
                existing_job = session.get(JobRow, existing_request.job_id)
                if existing_job is None:
                    raise ServiceError(
                        500,
                        "AUDITION_JOB_MISSING",
                        "The saved audition job is unavailable.",
                    )
                current_request = session.scalar(
                    select(SpeechProviderRequestRow)
                    .where(SpeechProviderRequestRow.job_id == existing_job.id)
                    .order_by(
                        SpeechProviderRequestRow.attempt.desc(),
                        SpeechProviderRequestRow.id.desc(),
                    )
                    .limit(1)
                )
                provider_requests = (existing_request, current_request)
                if (
                    existing_job.project_id != project_id
                    or existing_job.type != "generate_audition"
                    or existing_job.target_type != "audition_session"
                    or existing_job.target_id != audition_session.id
                    or existing_job.input_revision != preview.audition_session_revision
                    or existing_job.input_fingerprint != supplied_request_fingerprint
                    or existing_job.payload_json != canonical_json(expected_job_payload)
                    or existing_job_idempotency is None
                    or existing_job_idempotency.request_hash != expected_job_request_hash
                    or existing_job_idempotency.resource_id != existing_job.id
                    or current_request is None
                    or existing_request.attempt != 1
                    or current_request.attempt != existing_job.current_attempt
                    or any(
                        provider_request is None
                        or provider_request.project_id != project_id
                        or provider_request.job_id != existing_job.id
                        or provider_request.session_id != audition_session.id
                        or provider_request.script_id != preview.audition_script_id
                        or provider_request.request_fingerprint != supplied_request_fingerprint
                        or provider_request.attempt < 1
                        or provider_request.attempt > existing_job.current_attempt
                        for provider_request in provider_requests
                    )
                ):
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved audition request evidence failed verification.",
                    )
                return {
                    "session": self._session_wire(
                        session,
                        audition_session,
                        job_id_override=existing_job.id,
                    ),
                    "providerRequest": self._provider_request_wire(
                        session,
                        current_request,
                    ),
                    "jobId": existing_job.id,
                }
            if existing_job_idempotency is not None:
                idempotent_job = session.get(
                    JobRow,
                    existing_job_idempotency.resource_id,
                )
                if idempotent_job is not None and (
                    idempotent_job.project_id != project_id
                    or idempotent_job.type != "generate_audition"
                    or idempotent_job.target_type != "audition_session"
                    or idempotent_job.target_id != audition_session.id
                    or idempotent_job.input_revision != preview.audition_session_revision
                    or idempotent_job.input_fingerprint != supplied_request_fingerprint
                    or idempotent_job.payload_json != canonical_json(expected_job_payload)
                ):
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another audition.",
                    )
                raise ServiceError(
                    500,
                    "IDEMPOTENCY_RECORD_INVALID",
                    "The saved audition request evidence is unavailable.",
                )
            if audition_session.state == "invalidated":
                raise ServiceError(
                    409,
                    "AUDITION_SESSION_INVALIDATED",
                    "The audition session evidence is no longer current; create a new session.",
                )
            if audition_session.state in {"queued", "generating"}:
                raise ServiceError(
                    409,
                    "AUDITION_SESSION_BUSY",
                    "The audition session already has active generation work.",
                )
            canonical_evidence = self._session_evidence_wire(session, audition_session)
            if canonical_json(canonical_evidence) != canonical_json(
                preview.evidence.model_dump(mode="json", by_alias=True)
            ):
                raise ServiceError(
                    409,
                    "AUDITION_EVIDENCE_CHANGED",
                    "The audition evidence changed; refresh before generating.",
                )
            self._validate_session_evidence(
                session,
                project_id=project_id,
                role_id=audition_session.role_id,
                evidence=canonical_evidence,
            )
            script = session.get(AuditionScriptRow, preview.audition_script_id)
            if (
                script is None
                or script.project_id != project_id
                or script.session_id != audition_session.id
                or script.script_fingerprint != preview.audition_script_fingerprint
            ):
                raise ServiceError(
                    409,
                    "AUDITION_SCRIPT_CHANGED",
                    "The audition script evidence is not current.",
                )
            self._assert_script_source_entity_binding(session, audition_session, script)
            plan = session.scalar(
                select(TextNormalizationPlanRow)
                .where(TextNormalizationPlanRow.script_id == script.id)
                .order_by(
                    TextNormalizationPlanRow.revision.desc(),
                    TextNormalizationPlanRow.id.desc(),
                )
                .limit(1)
            )
            if (
                plan is None
                or plan.normalized_text_sha256 != preview.normalized_text_sha256
                or plan.plan_fingerprint != preview.normalization_plan_fingerprint
                or plan.pronunciation_plan_fingerprint != preview.pronunciation_plan_fingerprint
            ):
                raise ServiceError(
                    409,
                    "AUDITION_TEXT_PLAN_CHANGED",
                    "The normalization or pronunciation plan is not current.",
                )
            normalization = self._validated_normalization_plan(
                audition_session=audition_session,
                script=script,
                plan=plan,
                source_text=self._read_script_text(script),
            )
            if normalization.human_review_required:
                raise ServiceError(
                    409,
                    "AUDITION_NORMALIZATION_REVIEW_REQUIRED",
                    "Unsupported provider characters require review before generation.",
                )
            cache_key = AuditionCacheIdentity(
                project_id=project_id,
                provider_id=audition_session.provider_id,
                adapter_version=(
                    FIXTURE_ADAPTER_VERSION
                    if audition_session.provider_id == FIXTURE_PROVIDER_ID
                    else KOKORO_ADAPTER_VERSION
                ),
                runtime_fingerprint=audition_session.runtime_profile_fingerprint,
                model_package_fingerprint=audition_session.model_package_fingerprint,
                voice_profile_id=audition_session.voice_profile_id,
                voice_runtime_binding_fingerprint=(
                    audition_session.voice_runtime_binding_fingerprint
                ),
                provider_voice_id=audition_session.provider_voice_id,
                voice_assignment_id=audition_session.assignment_id,
                voice_assignment_revision=audition_session.assignment_revision,
                normalized_text_sha256=plan.normalized_text_sha256,
                pronunciation_plan_fingerprint=plan.pronunciation_plan_fingerprint,
                provider_control_fingerprint=supplied_controls_fingerprint,
                output_profile_fingerprint=AUDITION_PROFILE_FINGERPRINT,
                producer_version=_PRODUCER_VERSION,
            ).key()
            assignment = session.get(CastAssignmentRow, audition_session.assignment_id)
            voice = session.get(VoiceProfileRow, audition_session.voice_profile_record_id)
            if assignment is None or voice is None:
                raise ServiceError(
                    500,
                    "AUDITION_SESSION_EVIDENCE_MISSING",
                    "The audition session evidence is unavailable.",
                )
            job = jobs.create_audition_job(
                project_id=project_id,
                session_id=preview.audition_session_id,
                input_revision=preview.audition_session_revision,
                input_fingerprint=supplied_request_fingerprint,
                payload=expected_job_payload,
                idempotency_key=preview.idempotency_key,
                transaction_session=session,
            )
            job_row = session.get(JobRow, job["jobId"])
            if job_row is None:
                raise ServiceError(
                    500,
                    "AUDITION_QUEUE_EVIDENCE_MISSING",
                    "The audition queue evidence is unavailable.",
                )
            existing_request = session.scalar(
                select(SpeechProviderRequestRow).where(
                    SpeechProviderRequestRow.job_id == job_row.id,
                    SpeechProviderRequestRow.idempotency_key == preview.idempotency_key,
                )
            )
            if existing_request is not None:
                raise ServiceError(
                    500,
                    "IDEMPOTENCY_RECORD_INVALID",
                    "The audition request unexpectedly existed after a new job was queued.",
                )
            invocation_binding = self._provider_invocation_binding(
                session,
                audition_session,
            )
            existing_request = SpeechProviderRequestRow(
                id=new_id(),
                project_id=project_id,
                job_id=job_row.id,
                attempt=job_row.current_attempt,
                session_id=audition_session.id,
                script_id=script.id,
                normalization_plan_id=plan.id,
                runtime_profile_id=audition_session.runtime_profile_id,
                runtime_instance_id=None,
                model_installation_record_id=(audition_session.model_installation_record_id),
                model_verification_id=audition_session.model_verification_id,
                voice_profile_record_id=voice.id,
                assignment_id=assignment.id,
                assignment_revision=assignment.revision,
                provider_id=audition_session.provider_id,
                provider_version=audition_session.provider_version,
                provider_operation_id=preview.request_id,
                model_id=audition_session.model_id,
                model_version=audition_session.model_version,
                model_package_fingerprint=(audition_session.model_package_fingerprint),
                runtime_profile_fingerprint=(audition_session.runtime_profile_fingerprint),
                voice_profile_id=voice.profile_id,
                voice_profile_version=voice.profile_version,
                voice_runtime_binding_id=cast(
                    str,
                    invocation_binding["voiceRuntimeBindingId"],
                ),
                voice_runtime_binding_fingerprint=cast(
                    str,
                    invocation_binding["voiceRuntimeBindingFingerprint"],
                ),
                provider_voice_id=cast(str, invocation_binding["providerVoiceId"]),
                normalized_text_sha256=plan.normalized_text_sha256,
                pronunciation_plan_fingerprint=(plan.pronunciation_plan_fingerprint),
                provider_control_fingerprint=supplied_controls_fingerprint,
                cache_key=cache_key,
                request_fingerprint=supplied_request_fingerprint,
                idempotency_key=preview.idempotency_key,
                outcome="queued",
                retryable=False,
                output_artifact_sha256=None,
                audio_properties_json=canonical_json(
                    {
                        "channels": preview.channels,
                        "outputFormat": preview.output_format,
                        "sampleRateHz": preview.sample_rate_hz,
                        "speakingRate": preview.provider_controls.speaking_rate,
                        "pitch": preview.provider_controls.pitch,
                        "style": preview.provider_controls.style,
                        "energy": preview.provider_controls.energy,
                        **invocation_binding,
                    }
                ),
                warnings_json="[]",
                provenance_json=_provenance(
                    "human",
                    input_fingerprint=supplied_request_fingerprint,
                    details={
                        **invocation_binding,
                        "executionClassification": "provider_execution",
                        "providerDispatchCount": 0,
                    },
                ),
                started_at=None,
                finished_at=None,
            )
            session.add(existing_request)
            audition_session.state = "queued"
            session.flush()
            return {
                "session": self._session_wire(session, audition_session),
                "providerRequest": self._provider_request_wire(
                    session,
                    existing_request,
                ),
                "jobId": job_row.id,
            }

    @staticmethod
    def _provider_request_wire(
        session: Session,
        row: SpeechProviderRequestRow,
    ) -> dict[str, Any]:
        profile = session.get(SpeechRuntimeProfileRow, row.runtime_profile_id)
        if (
            profile is None
            or profile.profile_fingerprint != row.runtime_profile_fingerprint
            or profile.provider_id != row.provider_id
        ):
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_PROFILE_EVIDENCE_MISSING",
                "The provider request runtime profile evidence is unavailable.",
            )
        binding = session.get(VoiceRuntimeBindingRow, row.voice_runtime_binding_id)
        if (
            binding is None
            or not binding.active
            or binding.binding_fingerprint != row.voice_runtime_binding_fingerprint
            or binding.provider_voice_id != row.provider_voice_id
        ):
            raise ServiceError(
                409,
                "AUDITION_VOICE_RUNTIME_BINDING_CHANGED",
                "The provider request voice runtime binding failed verification.",
            )
        AuditionRepository._provider_execution_classification(row)
        return {
            "contractVersion": "1.0.0",
            "providerRequestId": row.id,
            "speechPreviewRequestId": row.provider_operation_id,
            "providerId": row.provider_id,
            "providerVersion": row.provider_version,
            "modelId": row.model_id,
            "modelVersion": row.model_version,
            "modelPackageFingerprint": row.model_package_fingerprint,
            "runtimeProfileId": profile.profile_id,
            "runtimeProfileFingerprint": row.runtime_profile_fingerprint,
            "runtimeInstanceId": row.runtime_instance_id,
            "voiceProfileId": row.voice_profile_id,
            "voiceProfileVersion": row.voice_profile_version,
            "voiceRuntimeBindingId": binding.id,
            "voiceRuntimeBindingFingerprint": binding.binding_fingerprint,
            "providerVoiceId": binding.provider_voice_id,
            "castAssignmentId": row.assignment_id,
            "castAssignmentRevision": row.assignment_revision,
            "auditionSessionId": row.session_id,
            "normalizedTextSha256": row.normalized_text_sha256,
            "pronunciationPlanFingerprint": row.pronunciation_plan_fingerprint,
            "providerControlFingerprint": row.provider_control_fingerprint,
            "cacheKey": row.cache_key,
            "state": row.outcome,
            "startedAt": row.started_at,
            "finishedAt": row.finished_at,
            "retryable": row.retryable,
            "warnings": parse_json(row.warnings_json, []),
            "requestFingerprint": row.request_fingerprint,
            "provenance": parse_json(row.provenance_json, {}),
        }

    @staticmethod
    def _record_provider_execution_classification(
        row: SpeechProviderRequestRow,
        *,
        execution_classification: str,
        provider_dispatch_count: int,
        source_provider_request_id: str | None = None,
    ) -> None:
        if execution_classification not in {
            "provider_execution",
            "verified_cache_lookup",
        } or provider_dispatch_count not in {0, 1}:
            raise ValueError("The provider execution classification is invalid.")
        if (
            execution_classification == "verified_cache_lookup"
            and (provider_dispatch_count != 0 or source_provider_request_id is None)
        ) or (
            execution_classification == "provider_execution"
            and source_provider_request_id is not None
        ):
            raise ValueError("The provider execution classification is incomplete.")
        provenance = parse_json(row.provenance_json, {})
        if not isinstance(provenance, dict):
            raise ValueError("The provider request provenance is invalid.")
        details = provenance.get("details")
        if not isinstance(details, dict):
            details = {}
        details["executionClassification"] = execution_classification
        details["providerDispatchCount"] = provider_dispatch_count
        if source_provider_request_id is None:
            details.pop("sourceProviderRequestId", None)
        else:
            details["sourceProviderRequestId"] = source_provider_request_id
        provenance["details"] = details
        row.provenance_json = canonical_json(provenance)

    @staticmethod
    def _assert_provider_execution_classification(
        row: SpeechProviderRequestRow,
        *,
        cache_hit: bool,
        source_provider_request_id: str | None,
    ) -> None:
        classification, count, source_id = AuditionRepository._provider_execution_classification(
            row
        )
        provenance = parse_json(row.provenance_json, {})
        details = provenance.get("details") if isinstance(provenance, dict) else None
        expected_classification = "verified_cache_lookup" if cache_hit else "provider_execution"
        expected_count = 0 if cache_hit else 1
        if (
            not isinstance(details, dict)
            or classification != expected_classification
            or count != expected_count
            or (row.runtime_instance_id is None) != cache_hit
            or (cache_hit and source_id != source_provider_request_id)
            or (not cache_hit and source_id is not None)
        ):
            raise ServiceError(
                409,
                "AUDITION_PROVIDER_EXECUTION_EVIDENCE_INVALID",
                "The provider execution classification failed verification.",
            )

    @staticmethod
    def _provider_execution_classification(
        row: SpeechProviderRequestRow,
    ) -> tuple[str, int, str | None]:
        provenance = parse_json(row.provenance_json, {})
        details = provenance.get("details") if isinstance(provenance, dict) else None
        classification = (
            details.get("executionClassification") if isinstance(details, dict) else None
        )
        count = details.get("providerDispatchCount") if isinstance(details, dict) else None
        source_id = details.get("sourceProviderRequestId") if isinstance(details, dict) else None
        valid_provider_execution = (
            classification == "provider_execution"
            and count in {0, 1}
            and not isinstance(count, bool)
            and source_id is None
            and (
                (count == 0 and row.runtime_instance_id is None and row.started_at is None)
                or (
                    count == 1
                    and row.runtime_instance_id is not None
                    and row.started_at is not None
                )
            )
        )
        valid_cache_lookup = (
            classification == "verified_cache_lookup"
            and count == 0
            and isinstance(source_id, str)
            and bool(source_id)
            and row.runtime_instance_id is None
            and row.started_at is not None
        )
        if not (valid_provider_execution or valid_cache_lookup):
            raise ServiceError(
                409,
                "AUDITION_PROVIDER_EXECUTION_EVIDENCE_INVALID",
                "The provider execution classification failed verification.",
            )
        return cast(str, classification), cast(int, count), cast(str | None, source_id)

    def list_clips(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int,
        audition_session_id: str | None = None,
        role_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        page_size = _bounded_page(limit)
        with self.database.session() as session:
            self._require_project(session, project_id)
            self._reconcile_project_evidence(session, project_id)
            filters = [AuditionClipRow.project_id == project_id]
            if audition_session_id is not None:
                filters.append(AuditionClipRow.session_id == audition_session_id)
            if role_id is not None:
                filters.append(AuditionClipRow.role_id == role_id)
            total = int(
                session.scalar(select(func.count()).select_from(AuditionClipRow).where(*filters))
                or 0
            )
            latest_identity = session.scalar(
                select(AuditionClipRow.id)
                .where(*filters)
                .order_by(AuditionClipRow.created_at.desc(), AuditionClipRow.id.desc())
                .limit(1)
            )
            binding = request_fingerprint(
                {
                    "auditionSessionId": audition_session_id,
                    "latestId": latest_identity,
                    "projectId": project_id,
                    "roleId": role_id,
                    "total": total,
                    "type": "audition-clips",
                }
            )
            offset = _decode_cursor(cursor, binding=binding)
            if offset > total:
                raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
            rows = list(
                session.scalars(
                    select(AuditionClipRow)
                    .where(*filters)
                    .order_by(AuditionClipRow.created_at.desc(), AuditionClipRow.id.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            )
            next_offset = offset + len(rows)
            next_cursor = _encode_cursor(binding, next_offset) if next_offset < total else None
            return (
                [self._clip_wire(session, row) for row in rows],
                next_cursor,
                total,
            )

    def get_audio_bytes(
        self,
        *,
        project_id: str,
        clip_id: str,
        audition_session_id: str,
        audio_artifact_id: str,
        expected_clip_revision: int,
        expected_clip_fingerprint: str,
        expected_artifact_sha256: str,
        expected_byte_size: int,
    ) -> tuple[bytes, dict[str, Any]]:
        with self.database.session() as session:
            self._require_project(session, project_id)
            clip = session.get(AuditionClipRow, clip_id)
            if (
                clip is None
                or clip.project_id != project_id
                or clip.session_id != audition_session_id
                or clip.artifact_id != audio_artifact_id
            ):
                raise not_found("audition clip")
            if (
                clip.revision != expected_clip_revision
                or clip.clip_fingerprint != expected_clip_fingerprint
            ):
                raise ServiceError(
                    409,
                    "AUDITION_CLIP_CHANGED",
                    "The audition clip changed; refresh before reading audio.",
                )
            artifact = session.get(AudioArtifactRow, clip.artifact_id)
            if (
                artifact is None
                or artifact.project_id != project_id
                or artifact.availability != "present"
                or artifact.content_sha256 != expected_artifact_sha256
                or artifact.byte_count != expected_byte_size
            ):
                raise ServiceError(
                    409,
                    "AUDITION_AUDIO_CHANGED",
                    "The audition audio identity changed; refresh before reading audio.",
                )
            storage_key = artifact.storage_key
            expected_properties = {
                "byte_count": artifact.byte_count,
                "channel_count": artifact.channel_count,
                "duration_ms": artifact.duration_ms,
                "frame_count": artifact.frame_count,
                "sample_rate_hz": artifact.sample_rate_hz,
                "sample_width_bytes": artifact.sample_width_bytes,
            }
        try:
            _path, payload = _read_bounded_stable_regular_file(
                self.data_dir,
                self._resolve_verified_artifact_path(storage_key),
                maximum_bytes=_MAX_AUDIO_BYTES,
                expected_byte_size=expected_byte_size,
            )
        except (OSError, ServiceError, ValueError) as exc:
            raise ServiceError(
                409,
                "AUDITION_AUDIO_MISSING",
                "The audition audio is no longer available.",
            ) from exc
        if len(payload) != expected_byte_size or sha256_bytes(payload) != expected_artifact_sha256:
            raise ServiceError(
                409,
                "AUDITION_AUDIO_CORRUPT",
                "The audition audio failed exact byte verification.",
            )
        try:
            qc = inspect_audition_wav_bytes(payload)
        except AuditionError as exc:
            raise ServiceError(
                409,
                "AUDITION_AUDIO_CORRUPT",
                "The audition audio failed format verification.",
            ) from exc
        if (
            qc.blocking_findings
            or qc.measurements.content_sha256 != expected_artifact_sha256
            or qc.byte_size != expected_properties["byte_count"]
            or qc.measurements.channel_count != expected_properties["channel_count"]
            or round(qc.measurements.duration_ms) != expected_properties["duration_ms"]
            or qc.measurements.frame_count != expected_properties["frame_count"]
            or qc.measurements.sample_rate_hz != expected_properties["sample_rate_hz"]
            or qc.measurements.sample_width_bytes != expected_properties["sample_width_bytes"]
        ):
            raise ServiceError(
                409,
                "AUDITION_AUDIO_CORRUPT",
                "The audition audio metadata failed verification.",
            )
        return payload, {
            "projectId": project_id,
            "auditionClipId": clip_id,
            "auditionSessionId": audition_session_id,
            "audioArtifactId": audio_artifact_id,
            "expectedClipRevision": expected_clip_revision,
            "expectedClipFingerprint": expected_clip_fingerprint,
            "expectedArtifactSha256": expected_artifact_sha256,
            "mediaType": "audio/wav",
            "byteSize": expected_byte_size,
        }

    def _resolve_verified_artifact_path(self, storage_key: str) -> Path:
        try:
            relative = Path(storage_key)
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ValueError
            return _verified_storage_path(
                self.data_dir,
                self.data_dir / relative,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError(
                409,
                "AUDITION_AUDIO_STORAGE_INVALID",
                "The audition audio storage boundary failed verification.",
            ) from exc

    def _clip_wire(
        self,
        session: Session,
        row: AuditionClipRow,
    ) -> dict[str, Any]:
        provider_request = session.get(SpeechProviderRequestRow, row.provider_request_id)
        audition_session = session.get(AuditionSessionRow, row.session_id)
        artifact = session.get(AudioArtifactRow, row.artifact_id)
        quality = session.scalar(
            select(AudioQualityRecordRow)
            .where(AudioQualityRecordRow.clip_id == row.id)
            .order_by(AudioQualityRecordRow.revision.desc(), AudioQualityRecordRow.id.desc())
            .limit(1)
        )
        if (
            provider_request is None
            or audition_session is None
            or artifact is None
            or quality is None
        ):
            raise ServiceError(
                500,
                "AUDITION_CLIP_EVIDENCE_MISSING",
                "The audition clip evidence is unavailable.",
            )
        latest_review = session.scalar(
            select(AuditionReviewRecordRow)
            .where(
                AuditionReviewRecordRow.clip_id == row.id,
                AuditionReviewRecordRow.gate_id == "per_role_audition_review",
            )
            .order_by(
                AuditionReviewRecordRow.revision.desc(),
                AuditionReviewRecordRow.id.desc(),
            )
            .limit(1)
        )
        latest_decision = (
            session.scalar(
                select(AuditionReviewDecisionRow)
                .where(AuditionReviewDecisionRow.review_record_id == latest_review.id)
                .order_by(
                    AuditionReviewDecisionRow.revision.desc(),
                    AuditionReviewDecisionRow.id.desc(),
                )
                .limit(1)
            )
            if latest_review is not None
            else None
        )
        if (
            latest_review is None
            or latest_review.project_id != row.project_id
            or latest_review.scope_key != row.role_id
            or latest_review.session_id != row.session_id
            or latest_review.clip_id != row.id
            or latest_review.role_id != row.role_id
        ):
            raise ServiceError(
                500,
                "AUDITION_REVIEW_EVIDENCE_MISSING",
                "The audition clip review evidence is unavailable.",
            )
        if latest_decision is not None and (
            latest_decision.project_id != latest_review.project_id
            or latest_decision.review_record_id != latest_review.id
            or latest_decision.gate_id != latest_review.gate_id
            or latest_decision.scope_key != latest_review.scope_key
            or latest_decision.evidence_fingerprint != latest_review.evidence_fingerprint
        ):
            raise ServiceError(
                500,
                "AUDITION_REVIEW_EVIDENCE_MISSING",
                "The audition clip review decision evidence is unavailable.",
            )
        clip_state = "reviewable"
        if latest_decision is not None:
            clip_state = latest_decision.decision
            if clip_state == "changes_requested":
                clip_state = "reviewable"
        try:
            blocking_quality_codes, quality_warnings = _validated_audio_quality_evidence(
                quality,
                artifact=artifact,
                clip=row,
            )
        except ValueError as exc:
            raise ServiceError(
                409,
                "AUDITION_AUDIO_QUALITY_EVIDENCE_INVALID",
                "The audition audio-quality evidence failed verification.",
            ) from exc
        cache_status = "verified_hit" if row.cache_hit else "miss"
        cache = (
            session.get(AuditionCacheRecordRow, row.cache_record_id)
            if row.cache_record_id is not None
            else None
        )
        if cache is None:
            raise ServiceError(
                409,
                "AUDITION_CACHE_EVIDENCE_INVALID",
                "The clip cache evidence is unavailable.",
            )
        try:
            cache_binding, source_request = self._validated_cache_graph(
                session,
                cache=cache,
                artifact=artifact,
            )
            self._assert_provider_execution_classification(
                provider_request,
                cache_hit=row.cache_hit,
                source_provider_request_id=(source_request.id if row.cache_hit else None),
            )
        except (ServiceError, ValueError) as exc:
            raise ServiceError(
                409,
                "AUDITION_CACHE_EVIDENCE_INVALID",
                "The clip cache evidence failed exact verification.",
            ) from exc
        binding = session.get(
            VoiceRuntimeBindingRow,
            provider_request.voice_runtime_binding_id,
        )
        if (
            binding is None
            or not binding.active
            or cache_binding.id != binding.id
            or binding.binding_fingerprint != provider_request.voice_runtime_binding_fingerprint
            or binding.provider_voice_id != provider_request.provider_voice_id
            or cache is None
            or cache.voice_runtime_binding_id != binding.id
            or cache.voice_runtime_binding_fingerprint != binding.binding_fingerprint
            or cache.provider_voice_id != binding.provider_voice_id
            or row.project_id != provider_request.project_id
            or row.project_id != artifact.project_id
            or row.session_id != provider_request.session_id
            or row.script_id != provider_request.script_id
            or row.cache_key != provider_request.cache_key
            or row.cache_key != cache.cache_key
            or provider_request.outcome != "succeeded"
            or provider_request.output_artifact_sha256 != artifact.content_sha256
        ):
            raise ServiceError(
                409,
                "AUDITION_VOICE_RUNTIME_BINDING_CHANGED",
                "The clip voice runtime binding failed exact verification.",
            )
        if not row.cache_hit and cache is not None and cache.state == "corrupt":
            cache_status = "corrupt_miss"
        if artifact.availability != "present" or cache is None or cache.state != "verified":
            clip_state = "invalidated"
            cache_status = "corrupt_miss"
        return {
            "contractVersion": "1.0.0",
            "auditionClipId": row.id,
            "projectId": row.project_id,
            "auditionSessionId": row.session_id,
            "auditionScriptId": row.script_id,
            "roleId": row.role_id,
            "castAssignmentId": row.assignment_id,
            "castAssignmentRevision": row.assignment_revision,
            "providerRequestId": row.provider_request_id,
            "providerId": provider_request.provider_id,
            "providerVersion": provider_request.provider_version,
            "voiceRuntimeBindingId": binding.id,
            "voiceRuntimeBindingFingerprint": binding.binding_fingerprint,
            "providerVoiceId": binding.provider_voice_id,
            "providerClass": (
                "deterministic_fixture"
                if provider_request.provider_id == FIXTURE_PROVIDER_ID
                else "real_local"
            ),
            "governedLocalVoiceActivation": (
                self._governed_local_voice_activation_wire(session, audition_session)
            ),
            "modelId": provider_request.model_id,
            "modelVersion": provider_request.model_version,
            "modelPackageFingerprint": provider_request.model_package_fingerprint,
            "runtimeProfileFingerprint": provider_request.runtime_profile_fingerprint,
            "normalizedTextSha256": provider_request.normalized_text_sha256,
            "pronunciationPlanFingerprint": (provider_request.pronunciation_plan_fingerprint),
            "providerControlFingerprint": provider_request.provider_control_fingerprint,
            "cacheKey": row.cache_key,
            "cacheStatus": cache_status,
            "cacheProof": {
                "cacheRecordId": cache.id,
                "cacheKey": cache.cache_key,
                "voiceRuntimeBindingId": cache.voice_runtime_binding_id,
                "voiceRuntimeBindingFingerprint": (cache.voice_runtime_binding_fingerprint),
                "providerVoiceId": cache.provider_voice_id,
                "verificationFingerprint": cache.verification_fingerprint,
            },
            "audioArtifact": self._artifact_wire(artifact),
            "audioQuality": self._quality_wire(
                quality,
                blocking_quality_codes=blocking_quality_codes,
                warning_codes=quality_warnings,
            ),
            "review": self._review_wire(
                session,
                latest_review,
                decision_override=latest_decision,
                decision_override_provided=True,
            ),
            "state": clip_state,
            "productionExportEligible": False,
            "clipFingerprint": row.clip_fingerprint,
            "revision": row.revision,
            "createdAt": row.created_at,
            "provenance": _public_provenance(row.provenance_json),
        }

    @staticmethod
    def _artifact_wire(row: AudioArtifactRow) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "audioArtifactId": row.id,
            "projectId": row.project_id,
            "storageKey": f"audition-artifact:{row.id}",
            "mediaType": "audio/wav",
            "codec": row.codec,
            "sampleRateHz": row.sample_rate_hz,
            "channels": row.channel_count,
            "sampleWidthBytes": row.sample_width_bytes,
            "frameCount": row.frame_count,
            "durationMilliseconds": row.duration_ms,
            "byteSize": row.byte_count,
            "sha256": row.content_sha256,
            "availability": row.availability,
            "playbackEligible": row.availability == "present",
            "publishedAtomically": True,
            "createdAt": row.created_at,
            "immutable": True,
        }

    @staticmethod
    def _quality_wire(
        row: AudioQualityRecordRow,
        *,
        blocking_quality_codes: Sequence[str],
        warning_codes: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "qualityRecordId": row.id,
            "projectId": row.project_id,
            "audioArtifactId": row.artifact_id,
            "profileId": row.policy_id,
            "profileVersion": row.policy_version,
            "validWav": len(blocking_quality_codes) == 0,
            "nonSilent": "AUDITION_ALL_SILENT" not in blocking_quality_codes,
            "peakDbfs": row.peak_millidbfs / 1000,
            "silenceRatio": row.silence_ratio_ppm / 1_000_000,
            "clippedSampleCount": row.clipped_sample_count,
            "blockingFindingCodes": list(blocking_quality_codes),
            "warningCodes": list(warning_codes),
            "subjectiveQualityClaimed": False,
            "qualityFingerprint": row.quality_fingerprint,
            "measuredAt": row.created_at,
            "provenance": _public_provenance(row.provenance_json),
        }

    # Durable generation orchestration ------------------------------------------

    def run_generation_job(
        self,
        claimed: dict[str, Any],
        jobs: JobRepository,
    ) -> None:
        job_id = claimed.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise ServiceError(
                500,
                "AUDITION_JOB_INVALID",
                "The claimed audition job identity is invalid.",
            )
        runtime: ManagedSpeechRuntime | None = None
        runtime_instance_id: str | None = None
        runtime_identity: SpeechWorkerIdentity | None = None
        staging_path: Path | None = None
        try:
            with self.database.session() as session:
                job = session.get(JobRow, job_id)
                if (
                    job is None
                    or job.type != "generate_audition"
                    or job.state != "running"
                    or job.target_id is None
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_JOB_NOT_RUNNING",
                        "The audition job is not in a runnable state.",
                    )
                payload = parse_json(job.payload_json, {})
                provider_request = session.scalar(
                    select(SpeechProviderRequestRow)
                    .where(
                        SpeechProviderRequestRow.job_id == job.id,
                        SpeechProviderRequestRow.attempt == job.current_attempt,
                    )
                    .limit(1)
                )
                audition_session = session.get(AuditionSessionRow, job.target_id)
                if (
                    not isinstance(payload, dict)
                    or provider_request is None
                    or audition_session is None
                    or provider_request.session_id != audition_session.id
                    or provider_request.request_fingerprint != job.input_fingerprint
                    or payload.get("requestFingerprint") != job.input_fingerprint
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_JOB_EVIDENCE_INVALID",
                        "The audition job evidence failed verification.",
                    )
                if (
                    provider_request.voice_runtime_binding_id
                    != audition_session.voice_runtime_binding_id
                    or provider_request.voice_runtime_binding_fingerprint
                    != audition_session.voice_runtime_binding_fingerprint
                    or provider_request.provider_voice_id != audition_session.provider_voice_id
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_VOICE_RUNTIME_BINDING_CHANGED",
                        "The queued voice runtime binding is no longer exact.",
                    )
                existing_clip = session.scalar(
                    select(AuditionClipRow).where(
                        AuditionClipRow.provider_request_id == provider_request.id
                    )
                )
                if existing_clip is not None and provider_request.outcome == "succeeded":
                    jobs.finish_success(job_id)
                    return
                script = session.get(AuditionScriptRow, provider_request.script_id)
                plan = session.get(
                    TextNormalizationPlanRow,
                    provider_request.normalization_plan_id,
                )
                runtime_profile = session.get(
                    SpeechRuntimeProfileRow,
                    audition_session.runtime_profile_id,
                )
                manifest = session.get(
                    ModelPackageManifestRow,
                    audition_session.model_manifest_id,
                )
                installation = session.get(
                    ModelInstallationRow,
                    audition_session.model_installation_record_id,
                )
                verification = session.get(
                    ModelVerificationRow,
                    audition_session.model_verification_id,
                )
                voice = session.get(
                    VoiceProfileRow,
                    audition_session.voice_profile_record_id,
                )
                rights = session.get(
                    VoiceRightsRecordRow,
                    audition_session.rights_record_id,
                )
                if any(
                    value is None
                    for value in (
                        script,
                        plan,
                        runtime_profile,
                        manifest,
                        installation,
                        verification,
                        voice,
                        rights,
                    )
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_JOB_EVIDENCE_INVALID",
                        "The audition job evidence is incomplete.",
                    )
                self._assert_script_source_entity_binding(
                    session,
                    audition_session,
                    cast(AuditionScriptRow, script),
                )
                canonical_evidence = self._session_evidence_wire(session, audition_session)
                self._validate_session_evidence(
                    session,
                    project_id=job.project_id,
                    role_id=audition_session.role_id,
                    evidence=canonical_evidence,
                )
                invocation_binding = self._provider_invocation_binding(
                    session,
                    audition_session,
                )
                checkpoint = new_checkpoint(
                    job_id=job.id,
                    attempt=job.current_attempt,
                    input_fingerprint=job.input_fingerprint,
                )
                job_attempt = job.current_attempt
                project_id = job.project_id
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "validate_phase3a_prerequisites",
            )
            checkpoint = self._advance_job_stage(jobs, checkpoint, "validate_rights")
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "freeze_assignment_and_catalog",
                evidence={
                    "casting_snapshot_fingerprint": audition_session.cast_snapshot_fingerprint
                },
            )
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "resolve_model_package",
                evidence={"model_package_fingerprint": audition_session.model_package_fingerprint},
            )
            cache, artifact, qc = self._verified_cache_hit(
                project_id=project_id,
                cache_key=provider_request.cache_key,
            )
            cache_hit = cache is not None and artifact is not None and qc is not None
            if cache_hit:
                assert cache is not None
                with self.database.immediate_session() as session:
                    request_row = session.get(SpeechProviderRequestRow, provider_request.id)
                    if request_row is None or request_row.runtime_instance_id is not None:
                        raise ServiceError(
                            409,
                            "AUDITION_CACHE_LOOKUP_EVIDENCE_INVALID",
                            "The cache lookup execution evidence changed.",
                        )
                    request_row.attempt = job_attempt
                    request_row.outcome = "running"
                    request_row.started_at = utc_now()
                    self._record_provider_execution_classification(
                        request_row,
                        execution_classification="verified_cache_lookup",
                        provider_dispatch_count=0,
                        source_provider_request_id=cache.provider_request_id,
                    )
            else:
                with self.database.immediate_session() as session:
                    request_row = session.get(SpeechProviderRequestRow, provider_request.id)
                    if request_row is None or request_row.runtime_instance_id is not None:
                        raise ServiceError(
                            409,
                            "AUDITION_PROVIDER_EXECUTION_EVIDENCE_INVALID",
                            "The provider execution evidence changed.",
                        )
                    request_row.attempt = job_attempt
                    request_row.outcome = "running"
                    classification, count, source_id = self._provider_execution_classification(
                        request_row
                    )
                    if (
                        classification != "provider_execution"
                        or count != 0
                        or source_id is not None
                    ):
                        raise ServiceError(
                            409,
                            "AUDITION_PROVIDER_EXECUTION_EVIDENCE_INVALID",
                            "The provider execution evidence changed.",
                        )
                runtime, runtime_instance_id = self._acquire_runtime(
                    project_id=project_id,
                    runtime_profile=cast(SpeechRuntimeProfileRow, runtime_profile),
                    manifest=cast(ModelPackageManifestRow, manifest),
                    installation=cast(ModelInstallationRow, installation),
                    verification=cast(ModelVerificationRow, verification),
                )
                with self._runtime_lock:
                    runtime_identity = runtime.identity or runtime.last_identity
                    runtime_owned = any(
                        cached_runtime is runtime and cached_instance_id == runtime_instance_id
                        for cached_runtime, cached_instance_id in self._runtimes.values()
                    )
                    with self.database.immediate_session() as session:
                        request_row = session.get(SpeechProviderRequestRow, provider_request.id)
                        runtime_row = session.get(SpeechRuntimeInstanceRow, runtime_instance_id)
                        if (
                            request_row is None
                            or runtime_row is None
                            or runtime_identity is None
                            or not runtime_owned
                            or runtime_row.state != "ready"
                            or runtime_row.worker_pid != runtime_identity.pid
                            or runtime_row.creation_identity
                            != _runtime_creation_identity(runtime_identity)
                        ):
                            raise ServiceError(
                                500,
                                "AUDITION_RUNTIME_EVIDENCE_MISSING",
                                "The managed runtime evidence is unavailable.",
                            )
                        if not runtime.is_running:
                            raise ServiceError(
                                503,
                                "AUDITION_RUNTIME_UNAVAILABLE",
                                "The managed local speech runtime exited during acquisition.",
                                retryable=True,
                            )
                        runtime_row.state = "busy"
                        runtime_row.last_used_at = utc_now()
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "acquire_local_runtime",
                evidence={"runtime_fingerprint": audition_session.runtime_profile_fingerprint},
            )
            text = self._read_script_text(cast(AuditionScriptRow, script))
            normalization = self._validated_normalization_plan(
                audition_session=audition_session,
                script=cast(AuditionScriptRow, script),
                plan=cast(TextNormalizationPlanRow, plan),
                source_text=text,
            )
            try:
                with self.database.session() as session:
                    current_session = session.get(AuditionSessionRow, audition_session.id)
                    current_script = session.get(
                        AuditionScriptRow,
                        cast(AuditionScriptRow, script).id,
                    )
                    if current_session is None or current_script is None:
                        raise not_found("audition session")
                    pronunciation_context = self._script_pronunciation_context(
                        session,
                        current_session,
                        current_script,
                    )
                    pronunciation = self._compile_pronunciation_plan(
                        session,
                        current_session,
                        normalization.normalized_text,
                        context=pronunciation_context,
                    )
                _escaped_provider_text = compile_provider_text(
                    normalization.normalized_text,
                    pronunciation,
                )
            except PronunciationError as exc:
                raise ServiceError(
                    409,
                    "AUDITION_TEXT_PLAN_STALE",
                    "The audition text plan is no longer reproducible.",
                ) from exc
            if (
                normalization.normalized_text_sha256
                != cast(TextNormalizationPlanRow, plan).normalized_text_sha256
                or pronunciation.effective_fingerprint
                != cast(TextNormalizationPlanRow, plan).pronunciation_plan_fingerprint
            ):
                raise ServiceError(
                    409,
                    "AUDITION_TEXT_PLAN_STALE",
                    "The audition text plan changed before generation.",
                )
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "compile_pronunciation_plan",
                evidence={"pronunciation_plan_fingerprint": pronunciation.effective_fingerprint},
            )
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "build_normalization_plan",
                evidence={
                    "normalization_plan_fingerprint": cast(
                        TextNormalizationPlanRow,
                        plan,
                    ).plan_fingerprint
                },
            )
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "calculate_cache_key",
                evidence={"cache_key_fingerprint": provider_request.cache_key},
            )
            generated_artifact: SpeechArtifact | None = None
            if not cache_hit:
                controls = parse_json(provider_request.audio_properties_json, {})
                if not isinstance(controls, dict) or any(
                    controls.get(key) != value for key, value in invocation_binding.items()
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_PROVIDER_BINDING_INVALID",
                        "The queued provider invocation binding failed verification.",
                    )
                speed = controls.get("speakingRate", 1.0) if isinstance(controls, dict) else 1.0
                if not isinstance(speed, (float, int)) or isinstance(speed, bool):
                    raise ServiceError(
                        409,
                        "AUDITION_CONTROLS_INVALID",
                        "The stored speech controls failed verification.",
                    )
                context = SpeechInvocationContext(
                    correlation_id=provider_request.id,
                    job_id=job_id,
                    attempt_id=f"{job_id}:{job_attempt}",
                    idempotency_key=provider_request.idempotency_key,
                    deadline_monotonic=monotonic()
                    + cast(SpeechRuntimeProfileRow, runtime_profile).request_timeout_ms / 1000,
                    restricted_voice_acknowledged=bool(
                        invocation_binding["restrictedLocalUseAcknowledged"]
                    ),
                    rights_record_id=cast(VoiceRightsRecordRow, rights).rights_record_id,
                    rights_record_revision=cast(VoiceRightsRecordRow, rights).revision,
                    network_access_permitted=False,
                )
                speech_request = SpeechSynthesisRequest(
                    request_id=provider_request.id,
                    text=normalization.normalized_text,
                    voice_id=cast(str, invocation_binding["providerVoiceId"]),
                    language=cast(str, invocation_binding["providerLanguage"]),
                    speed=float(speed),
                    pronunciation_overrides=tuple(
                        SpeechPronunciationOverrideSpan(
                            source_start=span.source_start,
                            source_end=span.source_end,
                            grapheme=span.grapheme,
                            pronunciation=span.pronunciation,
                            representation=span.representation,
                            entry_id=span.entry_id,
                            entry_revision=span.entry_revision,
                        )
                        for span in pronunciation.spans
                    ),
                )
                assert runtime is not None
                assert runtime_instance_id is not None

                def commit_provider_dispatch() -> None:
                    with self.database.immediate_session() as session:
                        current_job = session.get(JobRow, job_id)
                        request_row = session.get(
                            SpeechProviderRequestRow,
                            provider_request.id,
                        )
                        runtime_row = session.get(
                            SpeechRuntimeInstanceRow,
                            runtime_instance_id,
                        )
                        if (
                            current_job is None
                            or current_job.state != "running"
                            or current_job.current_attempt != job_attempt
                            or request_row is None
                            or request_row.job_id != job_id
                            or request_row.attempt != job_attempt
                            or request_row.outcome != "running"
                            or request_row.runtime_instance_id is not None
                            or runtime_row is None
                            or runtime_row.state != "busy"
                            or runtime_identity is None
                            or runtime.identity != runtime_identity
                            or not runtime.is_running
                            or runtime_row.worker_pid != runtime_identity.pid
                            or runtime_row.creation_identity
                            != _runtime_creation_identity(runtime_identity)
                        ):
                            raise ServiceError(
                                409,
                                "AUDITION_PROVIDER_DISPATCH_EVIDENCE_INVALID",
                                "The provider dispatch evidence changed before synthesis.",
                            )
                        classification, count, source_id = self._provider_execution_classification(
                            request_row
                        )
                        if (
                            classification != "provider_execution"
                            or count != 0
                            or source_id is not None
                        ):
                            raise ServiceError(
                                409,
                                "AUDITION_PROVIDER_DISPATCH_EVIDENCE_INVALID",
                                "The provider dispatch disposition changed before synthesis.",
                            )
                        self._record_provider_execution_classification(
                            request_row,
                            execution_classification="provider_execution",
                            provider_dispatch_count=1,
                        )
                        request_row.runtime_instance_id = runtime_instance_id
                        request_row.started_at = utc_now()

                generated_artifact = runtime.synthesize(
                    speech_request,
                    context,
                    on_dispatch_committed=commit_provider_dispatch,
                    expected_identity=runtime_identity,
                )
                if (
                    generated_artifact.provider_id != provider_request.provider_id
                    or generated_artifact.model_id != provider_request.model_id
                    or generated_artifact.model_version != provider_request.model_version
                    or generated_artifact.voice_id != provider_request.provider_voice_id
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_PROVIDER_OUTPUT_BINDING_INVALID",
                        "The speech provider returned an artifact for another exact voice binding.",
                    )
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "cache_or_synthesize",
                evidence={"cache_hit": cache_hit},
            )
            if generated_artifact is not None:
                artifact, qc, staging_path = self._prepare_audio_file(
                    project_id=project_id,
                    provider_request_id=provider_request.id,
                    provider_id=provider_request.provider_id,
                    speech_artifact=generated_artifact,
                )
            assert artifact is not None
            assert qc is not None
            if qc.blocking_findings:
                raise ServiceError(
                    422,
                    "AUDITION_AUDIO_BLOCKED",
                    "The generated audition failed bounded audio integrity checks.",
                )
            checkpoint = self._advance_job_stage(
                jobs,
                checkpoint,
                "validate_audio_artifact",
                evidence={"artifact_fingerprint": artifact.artifact_fingerprint},
            )
            expected_clip_id = deterministic_clip_id(checkpoint)
            final_checkpoint = advance_checkpoint(
                checkpoint,
                "publish_audition_clip",
                evidence={"clip_id": expected_clip_id},
            )
            final_checkpoint = advance_checkpoint(
                final_checkpoint,
                "publish_audition_session",
            )
            final_checkpoint = advance_checkpoint(
                final_checkpoint,
                "release_or_idle_runtime",
            )
            published = jobs.publish_audition_and_finish(
                job_id,
                result={
                    "artifact": artifact,
                    "cacheHit": cache_hit,
                    "checkpoint": final_checkpoint.material(),
                    "expectedClipId": expected_clip_id,
                    "providerRequestId": provider_request.id,
                    "stagingPath": staging_path,
                },
            )
            if not published:
                if staging_path is not None:
                    staging_path.unlink(missing_ok=True)
                return
            staging_path = None
        except Exception as exc:
            if staging_path is not None:
                staging_path.unlink(missing_ok=True)
            self._mark_provider_request_failed(job_id, exc)
            if runtime_instance_id is not None:
                exit_evidence = runtime.last_exit if runtime is not None else None
                observed_identity = (
                    (runtime.identity or runtime.last_identity) if runtime is not None else None
                )
                identity_changed = (
                    runtime_identity is not None
                    and observed_identity is not None
                    and _runtime_creation_identity(observed_identity)
                    != _runtime_creation_identity(runtime_identity)
                )
                runtime_reusable = (
                    runtime is not None
                    and runtime.is_running
                    and exit_evidence is None
                    and not identity_changed
                    and observed_identity == runtime_identity
                )
                with self.database.immediate_session() as session:
                    runtime_row = session.get(SpeechRuntimeInstanceRow, runtime_instance_id)
                    if runtime_row is not None and runtime_row.state not in {"stopped", "failed"}:
                        runtime_row.state = "idle" if runtime_reusable else "failed"
                        runtime_row.health_status = (
                            "degraded" if runtime_row.state == "idle" else "unavailable"
                        )
                        runtime_row.last_used_at = utc_now()
                if runtime is not None and identity_changed:
                    replacement_instance_id = new_id()
                    with self._runtime_lock:
                        for key, cached in tuple(self._runtimes.items()):
                            if cached == (runtime, runtime_instance_id):
                                self._runtimes.pop(key, None)
                    self._account_unbound_runtime(
                        instance_id=replacement_instance_id,
                        runtime=runtime,
                        evidence=exit_evidence,
                    )
                    with self.database.session() as session:
                        runtime_row = session.get(
                            SpeechRuntimeInstanceRow,
                            runtime_instance_id,
                        )
                        if runtime_row is None:
                            raise ServiceError(
                                500,
                                "AUDITION_RUNTIME_EVIDENCE_MISSING",
                                "The managed runtime evidence is unavailable.",
                            ) from exc
                        warnings = parse_json(runtime_row.warnings_json, {})
                        if not isinstance(warnings, dict):
                            warnings = {}
                        prior_evidence = self._unconfirmed_runtime_exit_evidence(
                            runtime_row,
                            warnings,
                        )
                    persisted = self._persist_runtime_exit_evidence(
                        instance_id=runtime_instance_id,
                        evidence=prior_evidence,
                        stop_failed=True,
                    )
                    if persisted is None:
                        raise ServiceError(
                            503,
                            "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                            "The prior managed runtime turnover could not be persisted.",
                            retryable=True,
                        ) from exc
                elif runtime is not None and not runtime_reusable:
                    if exit_evidence is None and not runtime.is_running:
                        try:
                            exit_evidence = runtime.stop(reason="clean")
                        except Exception:
                            exit_evidence = runtime.last_exit
                    exact_identity = runtime_identity or runtime.identity or runtime.last_identity
                    if exact_identity is None:
                        raise ServiceError(
                            503,
                            "AUDITION_RUNTIME_IDENTITY_INVALID",
                            "The failed managed runtime identity is unavailable.",
                            retryable=True,
                        ) from exc
                    self._quarantine_bound_runtime(
                        instance_id=runtime_instance_id,
                        runtime=runtime,
                        identity=exact_identity,
                    )
                    evidence_to_persist = exit_evidence
                    if (
                        evidence_to_persist is None
                        or evidence_to_persist.pid != exact_identity.pid
                        or evidence_to_persist.launcher_pid != exact_identity.launcher_pid
                    ):
                        with self.database.session() as session:
                            runtime_row = session.get(
                                SpeechRuntimeInstanceRow,
                                runtime_instance_id,
                            )
                            if runtime_row is None:
                                raise ServiceError(
                                    500,
                                    "AUDITION_RUNTIME_EVIDENCE_MISSING",
                                    "The managed runtime evidence is unavailable.",
                                ) from exc
                            warnings = parse_json(runtime_row.warnings_json, {})
                            if not isinstance(warnings, dict):
                                warnings = {}
                            evidence_to_persist = self._unconfirmed_runtime_exit_evidence(
                                runtime_row,
                                warnings,
                            )
                    persisted = self._persist_runtime_exit_evidence(
                        instance_id=runtime_instance_id,
                        evidence=evidence_to_persist,
                        stop_failed=not _runtime_exit_confirms_identity(
                            exit_evidence,
                            exact_identity,
                        ),
                    )
                    if persisted is None:
                        raise ServiceError(
                            503,
                            "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                            "The managed runtime exit could not be persisted.",
                            retryable=True,
                        ) from exc
                    if _runtime_exit_confirms_identity(exit_evidence, exact_identity):
                        self._release_quarantined_runtime(
                            instance_id=runtime_instance_id,
                            runtime=runtime,
                        )
            raise

    @staticmethod
    def _advance_job_stage(
        jobs: JobRepository,
        checkpoint: AuditionCheckpoint,
        stage: Any,
        *,
        evidence: Mapping[str, object] | None = None,
    ) -> AuditionCheckpoint:
        advanced = advance_checkpoint(checkpoint, stage, evidence=evidence)
        progress = min(0.99, advanced.completed_stage_count / len(AUDITION_PIPELINE_STAGES))
        if not jobs.update_progress(
            checkpoint.job_id,
            stage=stage,
            progress=progress,
            completed_units=advanced.completed_stage_count,
            total_units=len(AUDITION_PIPELINE_STAGES),
        ):
            raise ServiceError(
                409,
                "AUDITION_JOB_STOPPED",
                "The audition job stopped before publication.",
            )
        return advanced

    def _acquire_runtime(
        self,
        *,
        project_id: str,
        runtime_profile: SpeechRuntimeProfileRow,
        manifest: ModelPackageManifestRow,
        installation: ModelInstallationRow,
        verification: ModelVerificationRow,
    ) -> tuple[ManagedSpeechRuntime, str]:
        del project_id
        if (
            request_fingerprint(_runtime_profile_row_fingerprint_material(runtime_profile))
            != runtime_profile.profile_fingerprint
        ):
            raise ServiceError(
                503,
                "RUNTIME_PROFILE_CONFLICT",
                "Trusted runtime profile metadata failed verification.",
            )
        self._reap_idle_runtimes()
        key = request_fingerprint(
            {
                "installationRecordId": installation.id,
                "manifestFingerprint": manifest.manifest_fingerprint,
                "runtimeProfileFingerprint": runtime_profile.profile_fingerprint,
            }
        )
        with self._runtime_lock:
            if self._runtime_shutdown_started.is_set():
                raise ServiceError(
                    503,
                    "AUDITION_RUNTIME_SHUTDOWN_STARTED",
                    "The managed runtime owner is shutting down.",
                    retryable=True,
                )
            cached = self._runtimes.get(key)
            runtime = cached[0] if cached is not None else None
            instance_id = cached[1] if cached is not None else None
            prior_identity: SpeechWorkerIdentity | None = None
            prior_exit: SpeechRuntimeExitEvidence | None = None
            if runtime is not None:
                if instance_id is None:
                    raise ServiceError(
                        500,
                        "AUDITION_RUNTIME_EVIDENCE_MISSING",
                        "The managed runtime evidence is unavailable.",
                    )
                prior_identity = runtime.identity or runtime.last_identity
                prior_exit = runtime.last_exit
                if prior_identity is None:
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_IDENTITY_INVALID",
                        "The cached managed runtime identity is unavailable.",
                        retryable=True,
                    )
                fallback_exit: SpeechRuntimeExitEvidence | None = None
                with self.database.session() as session:
                    prior_row = session.get(SpeechRuntimeInstanceRow, instance_id)
                    prior_warnings = (
                        parse_json(prior_row.warnings_json, {}) if prior_row is not None else None
                    )
                    if (
                        prior_row is None
                        or not isinstance(prior_warnings, dict)
                        or prior_row.state not in {"ready", "idle"}
                        or prior_row.stopped_at is not None
                        or prior_row.exit_code is not None
                        or prior_warnings.get("exitEvidence") is not None
                        or prior_row.worker_pid != prior_identity.pid
                        or prior_row.parent_pid != prior_identity.parent_pid
                        or prior_row.executable_identity != prior_identity.executable.name
                        or prior_row.creation_identity != _runtime_creation_identity(prior_identity)
                    ):
                        raise ServiceError(
                            503,
                            "AUDITION_RUNTIME_IDENTITY_INVALID",
                            "The cached managed runtime identity failed verification.",
                            retryable=True,
                        )
                    if prior_exit is not None and (
                        prior_exit.pid != prior_identity.pid
                        or prior_exit.launcher_pid != prior_identity.launcher_pid
                    ):
                        fallback_exit = self._unconfirmed_runtime_exit_evidence(
                            prior_row,
                            prior_warnings,
                        )
                if prior_exit is not None:
                    self._quarantine_bound_runtime(
                        instance_id=instance_id,
                        runtime=runtime,
                        identity=prior_identity,
                    )
                    evidence_to_persist = fallback_exit or prior_exit
                    persisted = self._persist_runtime_exit_evidence(
                        instance_id=instance_id,
                        evidence=evidence_to_persist,
                        stop_failed=not _runtime_exit_confirms_identity(
                            prior_exit,
                            prior_identity,
                        ),
                    )
                    if persisted is None:
                        raise ServiceError(
                            503,
                            "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                            "The cached managed runtime exit could not be persisted.",
                            retryable=True,
                        )
                    if _runtime_exit_confirms_identity(prior_exit, prior_identity):
                        self._release_quarantined_runtime(
                            instance_id=instance_id,
                            runtime=runtime,
                        )
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_TERMINATION_PENDING",
                        "The prior managed runtime is not eligible for reuse.",
                        retryable=True,
                    )
            if runtime is None:
                if len(self._owned_runtime_instance_ids) >= _MAX_OWNED_RUNTIME_SHUTDOWN_PROOFS:
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_CAPACITY_EXCEEDED",
                        "The managed runtime capacity is currently unavailable.",
                        retryable=True,
                    )
                model_path = (
                    resolve_beneath(self.data_dir, installation.storage_key)
                    if installation.storage_key is not None
                    else None
                )
                config = SpeechRuntimeConfig(
                    provider_id=manifest.provider_id,
                    runtime_id=manifest.runtime_id,
                    runtime_version=manifest.runtime_version,
                    model_id=manifest.model_id,
                    model_version=manifest.model_version,
                    model_manifest_fingerprint=manifest.manifest_fingerprint,
                    model_package_path=model_path,
                    startup_timeout_seconds=runtime_profile.startup_timeout_ms / 1000,
                    request_timeout_seconds=runtime_profile.request_timeout_ms / 1000,
                    idle_timeout_seconds=runtime_profile.idle_shutdown_ms / 1000,
                    max_retries=0,
                )
                runtime = self._runtime_factory(config)
                instance_id = new_id()
            try:
                identity = runtime.start()
            except (SpeechRuntimeError, OSError, RuntimeError, ValueError) as exc:
                if (
                    instance_id is not None
                    and prior_identity is not None
                    and isinstance(exc, SpeechRuntimeError)
                    and exc.code == "SPEECH_WORKER_EXITED"
                ):
                    exit_evidence = runtime.last_exit
                    if exit_evidence is prior_exit or not _runtime_exit_confirms_identity(
                        exit_evidence, prior_identity
                    ):
                        raise ServiceError(
                            503,
                            "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                            "The prior managed runtime exit could not be verified.",
                            retryable=True,
                        ) from exc
                    assert exit_evidence is not None
                    persisted = self._persist_runtime_exit_evidence(
                        instance_id=instance_id,
                        evidence=exit_evidence,
                    )
                    if persisted is None:
                        raise ServiceError(
                            503,
                            "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                            "The prior managed runtime exit could not be persisted.",
                            retryable=True,
                        ) from exc
                    self._runtimes.pop(key, None)
                elif (
                    instance_id is not None
                    and prior_identity is None
                    and (runtime.has_owned_process_handle or runtime.last_exit is not None)
                ):
                    exit_evidence = runtime.last_exit
                    self._account_unbound_runtime(
                        instance_id=instance_id,
                        runtime=runtime,
                        evidence=exit_evidence,
                    )
                raise ServiceError(
                    503,
                    "AUDITION_RUNTIME_UNAVAILABLE",
                    "The managed local speech runtime could not be acquired.",
                    retryable=True,
                ) from exc
            if prior_identity is None:
                assert instance_id is not None
                self._owned_runtime_instance_ids.add(instance_id)
            if prior_identity is not None and (
                instance_id is None
                or _runtime_creation_identity(identity)
                != _runtime_creation_identity(prior_identity)
            ):
                turnover_exit = runtime.last_exit
                replacement_instance_id = new_id()
                if instance_id is None:
                    raise ServiceError(
                        500,
                        "AUDITION_RUNTIME_EVIDENCE_MISSING",
                        "The managed runtime evidence is unavailable.",
                    )
                with self._runtime_lock:
                    if self._runtimes.get(key) == (runtime, instance_id):
                        self._runtimes.pop(key, None)
                    self._owned_runtime_instance_ids.add(replacement_instance_id)
                    self._quarantined_runtimes[replacement_instance_id] = _QuarantinedRuntime(
                        runtime=runtime,
                        identity=identity,
                        bound=False,
                    )
                stop_failed = turnover_exit is prior_exit or not _runtime_exit_confirms_identity(
                    turnover_exit,
                    prior_identity,
                )
                if stop_failed:
                    with self.database.session() as session:
                        prior_row = session.get(SpeechRuntimeInstanceRow, instance_id)
                        if prior_row is None:
                            raise ServiceError(
                                500,
                                "AUDITION_RUNTIME_EVIDENCE_MISSING",
                                "The managed runtime evidence is unavailable.",
                            )
                        warnings = parse_json(prior_row.warnings_json, {})
                        if not isinstance(warnings, dict):
                            warnings = {}
                        turnover_exit = self._unconfirmed_runtime_exit_evidence(
                            prior_row,
                            warnings,
                        )
                assert turnover_exit is not None
                persisted = self._persist_runtime_exit_evidence(
                    instance_id=instance_id,
                    evidence=turnover_exit,
                    stop_failed=stop_failed,
                )
                if persisted is None:
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                        "The prior managed runtime exit could not be persisted.",
                        retryable=True,
                    )
                replacement_exit = self._stop_and_account_unbound_runtime(
                    instance_id=replacement_instance_id,
                    runtime=runtime,
                )
                if not _runtime_exit_confirms_identity(replacement_exit, identity):
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                        "The replacement managed runtime exit could not be verified.",
                        retryable=True,
                    )
                raise ServiceError(
                    503,
                    "AUDITION_RUNTIME_IDENTITY_INVALID",
                    "The managed runtime identity changed during acquisition.",
                    retryable=True,
                )
            if prior_identity is None:
                executable = identity.executable
                try:
                    executable_sha256 = sha256_bytes(executable.read_bytes())
                except OSError as exc:
                    assert instance_id is not None
                    self._stop_and_account_unbound_runtime(
                        instance_id=instance_id,
                        runtime=runtime,
                    )
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_IDENTITY_INVALID",
                        "The managed runtime executable failed exact verification.",
                    ) from exc
                now = utc_now()
                assert instance_id is not None
                creation_identity = _runtime_creation_identity(identity)
                handshake_fingerprint = request_fingerprint(
                    {
                        "creationIdentity": creation_identity,
                        "modelManifestFingerprint": identity.model_manifest_fingerprint,
                        "protocolVersion": identity.protocol_version,
                        "providerId": identity.provider_id,
                        "runtimeId": identity.runtime_id,
                        "runtimeVersion": identity.runtime_version,
                    }
                )
                try:
                    with self.database.immediate_session() as session:
                        session.add(
                            SpeechRuntimeInstanceRow(
                                id=instance_id,
                                runtime_profile_id=runtime_profile.id,
                                model_installation_record_id=installation.id,
                                model_verification_id=verification.id,
                                provider_id=manifest.provider_id,
                                provider_version=manifest.provider_version,
                                runtime_id=manifest.runtime_id,
                                runtime_version=manifest.runtime_version,
                                model_id=manifest.model_id,
                                model_version=manifest.model_version,
                                model_package_fingerprint=manifest.manifest_fingerprint,
                                runtime_profile_fingerprint=runtime_profile.profile_fingerprint,
                                protocol_version=identity.protocol_version,
                                handshake_fingerprint=handshake_fingerprint,
                                worker_pid=identity.pid,
                                parent_pid=identity.parent_pid,
                                executable_identity=identity.executable.name,
                                executable_sha256=executable_sha256,
                                creation_identity=creation_identity,
                                state="ready",
                                health_status="available",
                                exit_code=None,
                                warnings_json=canonical_json(
                                    {
                                        "networkObservation": "not_instrumented",
                                        "networkPolicy": "python_socket_api_denied",
                                        "ownershipJobName": identity.ownership_job_name,
                                        "jobObjectAssigned": identity.job_object_assigned,
                                        "deniedNetworkAttemptCount": (
                                            identity.denied_network_attempt_count
                                        ),
                                    }
                                ),
                                provenance_json=_provenance("system"),
                                started_at=now,
                                ready_at=now,
                                last_health_at=now,
                                last_used_at=now,
                                stopped_at=None,
                            )
                        )
                except Exception:
                    self._stop_and_account_unbound_runtime(
                        instance_id=instance_id,
                        runtime=runtime,
                    )
                    raise
            else:
                with self.database.immediate_session() as session:
                    row = session.get(SpeechRuntimeInstanceRow, instance_id)
                    if row is None:
                        raise ServiceError(
                            500,
                            "AUDITION_RUNTIME_EVIDENCE_MISSING",
                            "The managed runtime evidence is unavailable.",
                        )
                    row.state = "ready"
                    row.health_status = "available"
                    row.last_health_at = utc_now()
                    row.last_used_at = utc_now()
            if instance_id is None:
                runtime.stop()
                raise ServiceError(
                    500,
                    "AUDITION_RUNTIME_EVIDENCE_MISSING",
                    "The managed runtime evidence is unavailable.",
                )
            self._runtimes[key] = (runtime, instance_id)
            return runtime, instance_id

    def _persist_runtime_exit_evidence(
        self,
        *,
        instance_id: str,
        evidence: SpeechRuntimeExitEvidence,
        stop_failed: bool = False,
    ) -> dict[str, Any] | None:
        with self.database.immediate_session() as session:
            row = session.get(SpeechRuntimeInstanceRow, instance_id)
            if row is None:
                return None
            if row.worker_pid != evidence.pid:
                raise ServiceError(
                    500,
                    "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                    "The managed runtime exit did not match its persisted process identity.",
                )
            now = utc_now()
            warnings = parse_json(row.warnings_json, {})
            if not isinstance(warnings, dict):
                warnings = {}
            warnings["stopReasonCode"] = evidence.reason
            warnings["exitEvidence"] = asdict(evidence)
            warnings["jobObjectAssigned"] = evidence.job_object_assigned
            warnings["deniedNetworkAttemptCount"] = evidence.denied_network_attempt_count
            row.exit_code = evidence.exit_code
            row.warnings_json = canonical_json(warnings)
            row.state = (
                "stopped"
                if evidence.reason in {"clean", "idle"}
                and evidence.graceful_shutdown_confirmed
                and not stop_failed
                else "failed"
            )
            row.health_status = "unavailable"
            row.last_health_at = now
            row.last_used_at = now
            row.stopped_at = now
            proof = {
                "runtimeInstanceId": row.id,
                "workerPid": row.worker_pid,
                "state": row.state,
                "stoppedAt": row.stopped_at,
                "stopReasonCode": evidence.reason,
                "exitCode": evidence.exit_code,
                "shutdownAcknowledged": evidence.shutdown_acknowledged,
                "gracefulShutdownConfirmed": evidence.graceful_shutdown_confirmed,
                "terminatedByParent": evidence.terminated_by_parent,
                "ownershipConfirmed": evidence.ownership_confirmed,
                "ownedProcessesConfirmedExited": (evidence.owned_processes_confirmed_exited),
                "jobObjectAssigned": evidence.job_object_assigned,
                "deniedNetworkAttemptCount": evidence.denied_network_attempt_count,
            }
        with self._runtime_lock:
            self._runtime_exit_records[instance_id] = (evidence, proof)
        return proof

    def _record_unbound_runtime_exit(
        self,
        *,
        instance_id: str,
        evidence: SpeechRuntimeExitEvidence,
        stop_failed: bool = False,
    ) -> dict[str, Any]:
        stopped_at = utc_now()
        state = (
            "stopped"
            if evidence.reason in {"clean", "idle"}
            and evidence.graceful_shutdown_confirmed
            and not stop_failed
            else "failed"
        )
        proof = {
            "runtimeInstanceId": instance_id,
            "workerPid": evidence.pid,
            "state": state,
            "stoppedAt": stopped_at,
            "stopReasonCode": evidence.reason,
            "exitCode": evidence.exit_code,
            "shutdownAcknowledged": evidence.shutdown_acknowledged,
            "gracefulShutdownConfirmed": evidence.graceful_shutdown_confirmed,
            "terminatedByParent": evidence.terminated_by_parent,
            "ownershipConfirmed": evidence.ownership_confirmed,
            "ownedProcessesConfirmedExited": evidence.owned_processes_confirmed_exited,
            "jobObjectAssigned": evidence.job_object_assigned,
            "deniedNetworkAttemptCount": evidence.denied_network_attempt_count,
        }
        with self._runtime_lock:
            self._owned_runtime_instance_ids.add(instance_id)
            self._runtime_exit_records[instance_id] = (evidence, proof)
        return proof

    def _account_unbound_runtime(
        self,
        *,
        instance_id: str,
        runtime: ManagedSpeechRuntime,
        evidence: SpeechRuntimeExitEvidence | None,
        stop_failed: bool = True,
    ) -> None:
        with self._runtime_lock:
            self._owned_runtime_instance_ids.add(instance_id)
        if evidence is not None:
            self._record_unbound_runtime_exit(
                instance_id=instance_id,
                evidence=evidence,
                stop_failed=stop_failed,
            )
        if not _runtime_exit_confirms_owned_tree(evidence):
            with self._runtime_lock:
                existing = self._quarantined_runtimes.get(instance_id)
                if existing is None or not existing.bound:
                    self._quarantined_runtimes[instance_id] = _QuarantinedRuntime(
                        runtime=runtime,
                        identity=runtime.identity or runtime.last_identity,
                        bound=False,
                    )
        else:
            with self._runtime_lock:
                existing = self._quarantined_runtimes.get(instance_id)
                if existing is not None and existing.runtime is runtime and not existing.bound:
                    self._quarantined_runtimes.pop(instance_id, None)

    def _quarantine_bound_runtime(
        self,
        *,
        instance_id: str,
        runtime: ManagedSpeechRuntime,
        identity: SpeechWorkerIdentity,
    ) -> None:
        """Remove a poisoned bound worker from reuse while retaining exact ownership."""

        with self._runtime_lock:
            for key, cached in tuple(self._runtimes.items()):
                if cached == (runtime, instance_id):
                    self._runtimes.pop(key, None)
            existing = self._quarantined_runtimes.get(instance_id)
            if existing is not None and existing.runtime is not runtime:
                raise ServiceError(
                    500,
                    "AUDITION_RUNTIME_OWNERSHIP_CONFLICT",
                    "The quarantined runtime ownership record conflicted.",
                )
            self._quarantined_runtimes[instance_id] = _QuarantinedRuntime(
                runtime=runtime,
                identity=identity,
                bound=True,
            )

    def _release_quarantined_runtime(
        self,
        *,
        instance_id: str,
        runtime: ManagedSpeechRuntime,
    ) -> None:
        with self._runtime_lock:
            existing = self._quarantined_runtimes.get(instance_id)
            if existing is not None and existing.runtime is runtime:
                self._quarantined_runtimes.pop(instance_id, None)

    def _stop_and_account_unbound_runtime(
        self,
        *,
        instance_id: str,
        runtime: ManagedSpeechRuntime,
    ) -> SpeechRuntimeExitEvidence | None:
        try:
            evidence = runtime.stop()
        except Exception:
            evidence = runtime.last_exit
        self._account_unbound_runtime(
            instance_id=instance_id,
            runtime=runtime,
            evidence=evidence,
        )
        return evidence

    def _reap_idle_runtimes(self) -> tuple[SpeechRuntimeExitEvidence, ...]:
        """Observe only cached owned workers whose durable state is idle."""

        with self._runtime_lock:
            if not self._runtimes:
                return ()
            instance_ids = [instance_id for _runtime, instance_id in self._runtimes.values()]
            with self.database.session() as session:
                idle_instance_ids = set(
                    session.scalars(
                        select(SpeechRuntimeInstanceRow.id).where(
                            SpeechRuntimeInstanceRow.id.in_(instance_ids),
                            SpeechRuntimeInstanceRow.state == "idle",
                        )
                    )
                )
            exits: list[SpeechRuntimeExitEvidence] = []
            for _key, (runtime, instance_id) in tuple(self._runtimes.items()):
                identity = runtime.identity
                if (
                    instance_id not in idle_instance_ids
                    or identity is None
                    or not runtime.reap_if_idle()
                ):
                    continue
                evidence = runtime.last_exit
                self._quarantine_bound_runtime(
                    instance_id=instance_id,
                    runtime=runtime,
                    identity=identity,
                )
                if not _runtime_exit_confirms_identity(evidence, identity):
                    if (
                        evidence is not None
                        and evidence.pid == identity.pid
                        and evidence.launcher_pid == identity.launcher_pid
                    ):
                        self._persist_runtime_exit_evidence(
                            instance_id=instance_id,
                            evidence=evidence,
                            stop_failed=True,
                        )
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                        "The idle managed runtime exit could not be verified.",
                        retryable=True,
                    )
                assert evidence is not None
                persisted = self._persist_runtime_exit_evidence(
                    instance_id=instance_id,
                    evidence=evidence,
                )
                if persisted is None:
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_EXIT_EVIDENCE_INVALID",
                        "The idle managed runtime exit could not be persisted.",
                        retryable=True,
                    )
                self._release_quarantined_runtime(
                    instance_id=instance_id,
                    runtime=runtime,
                )
                exits.append(evidence)
            return tuple(exits)

    def _invalidate_artifact_integrity(
        self,
        session: Session,
        *,
        artifact: AudioArtifactRow,
        reason_code: str,
        rationale: str,
    ) -> None:
        clips = list(
            session.scalars(
                select(AuditionClipRow).where(
                    AuditionClipRow.project_id == artifact.project_id,
                    AuditionClipRow.artifact_id == artifact.id,
                )
            )
        )
        self._invalidate_clip_dependencies(
            session,
            project_id=artifact.project_id,
            clips=clips,
            source_kind="audio_integrity",
            source_record_id=artifact.id,
            previous_fingerprint=artifact.artifact_fingerprint,
            current_fingerprint=request_fingerprint(
                {
                    "artifactId": artifact.id,
                    "availability": artifact.availability,
                    "reasonCode": reason_code,
                }
            ),
            reason_code=reason_code,
            rationale=rationale,
        )

    def _validated_cache_graph(
        self,
        session: Session,
        *,
        cache: AuditionCacheRecordRow,
        artifact: AudioArtifactRow,
    ) -> tuple[VoiceRuntimeBindingRow, SpeechProviderRequestRow]:
        binding = session.get(VoiceRuntimeBindingRow, cache.voice_runtime_binding_id)
        source_request = session.get(
            SpeechProviderRequestRow,
            cache.provider_request_id,
        )
        audition_session = session.get(AuditionSessionRow, cache.session_id)
        script = session.get(AuditionScriptRow, cache.script_id)
        plan = (
            session.get(
                TextNormalizationPlanRow,
                source_request.normalization_plan_id,
            )
            if source_request is not None
            else None
        )
        job = session.get(JobRow, source_request.job_id) if source_request is not None else None
        runtime = (
            session.get(SpeechRuntimeInstanceRow, source_request.runtime_instance_id)
            if source_request is not None and source_request.runtime_instance_id is not None
            else None
        )
        expected_properties = {
            "channelCount": artifact.channel_count,
            "durationMilliseconds": artifact.duration_ms,
            "frameCount": artifact.frame_count,
            "sampleRateHz": artifact.sample_rate_hz,
            "sampleWidthBytes": artifact.sample_width_bytes,
        }
        expected_verification_fingerprint = request_fingerprint(
            {
                "artifactFingerprint": artifact.artifact_fingerprint,
                "cacheKey": cache.cache_key,
                "expectedAudioProperties": expected_properties,
                "providerVoiceId": cache.provider_voice_id,
                "voiceRuntimeBindingFingerprint": (cache.voice_runtime_binding_fingerprint),
            }
        )
        if (
            binding is None
            or not binding.active
            or source_request is None
            or audition_session is None
            or script is None
            or plan is None
            or job is None
            or runtime is None
            or cache.project_id != artifact.project_id
            or artifact.provider_request_id != source_request.id
            or source_request.project_id != cache.project_id
            or source_request.id != cache.provider_request_id
            or source_request.outcome != "succeeded"
            or source_request.retryable
            or source_request.finished_at is None
            or source_request.output_artifact_sha256 != artifact.content_sha256
            or source_request.output_artifact_sha256 != cache.expected_artifact_sha256
            or source_request.cache_key != cache.cache_key
            or source_request.session_id != cache.session_id
            or source_request.script_id != cache.script_id
            or source_request.voice_runtime_binding_id != binding.id
            or source_request.voice_runtime_binding_fingerprint != binding.binding_fingerprint
            or source_request.provider_voice_id != binding.provider_voice_id
            or cache.voice_runtime_binding_fingerprint != binding.binding_fingerprint
            or cache.provider_voice_id != binding.provider_voice_id
            or audition_session.project_id != cache.project_id
            or audition_session.id != cache.session_id
            or audition_session.voice_runtime_binding_id != binding.id
            or audition_session.voice_runtime_binding_fingerprint != binding.binding_fingerprint
            or audition_session.provider_voice_id != binding.provider_voice_id
            or script.project_id != cache.project_id
            or script.id != cache.script_id
            or script.session_id != audition_session.id
            or plan.project_id != cache.project_id
            or plan.session_id != audition_session.id
            or plan.script_id != script.id
            or source_request.normalized_text_sha256 != plan.normalized_text_sha256
            or source_request.pronunciation_plan_fingerprint != plan.pronunciation_plan_fingerprint
            or source_request.provider_id != audition_session.provider_id
            or source_request.provider_version != audition_session.provider_version
            or source_request.model_id != audition_session.model_id
            or source_request.model_version != audition_session.model_version
            or source_request.model_package_fingerprint
            != audition_session.model_package_fingerprint
            or source_request.runtime_profile_id != audition_session.runtime_profile_id
            or source_request.runtime_profile_fingerprint
            != audition_session.runtime_profile_fingerprint
            or source_request.model_installation_record_id
            != audition_session.model_installation_record_id
            or source_request.model_verification_id != audition_session.model_verification_id
            or source_request.voice_profile_record_id != audition_session.voice_profile_record_id
            or source_request.voice_profile_id != audition_session.voice_profile_id
            or source_request.voice_profile_version != audition_session.voice_profile_version
            or source_request.assignment_id != audition_session.assignment_id
            or source_request.assignment_revision != audition_session.assignment_revision
            or job.project_id != cache.project_id
            or job.type != "generate_audition"
            or job.target_id != audition_session.id
            or job.state != "succeeded"
            or job.current_attempt != source_request.attempt
            or job.input_fingerprint != source_request.request_fingerprint
            or runtime.id != source_request.runtime_instance_id
            or runtime.provider_id != source_request.provider_id
            or runtime.model_id != source_request.model_id
            or runtime.model_version != source_request.model_version
            or runtime.model_package_fingerprint != source_request.model_package_fingerprint
            or runtime.runtime_profile_fingerprint != source_request.runtime_profile_fingerprint
            or cache.artifact_id != artifact.id
            or cache.expected_artifact_sha256 != artifact.content_sha256
            or cache.expected_byte_count != artifact.byte_count
            or parse_json(cache.expected_audio_properties_json, None) != expected_properties
            or cache.verification_fingerprint != expected_verification_fingerprint
        ):
            raise ValueError("The audition cache graph failed exact verification.")
        input_and_output = parse_json(source_request.audio_properties_json, None)
        input_properties = (
            input_and_output.get("input") if isinstance(input_and_output, dict) else None
        )
        output_properties = (
            input_and_output.get("output") if isinstance(input_and_output, dict) else None
        )
        controls = (
            {
                "energy": input_properties.get("energy"),
                "pitch": input_properties.get("pitch"),
                "speakingRate": input_properties.get("speakingRate"),
                "style": input_properties.get("style"),
            }
            if isinstance(input_properties, dict)
            else None
        )
        try:
            invocation_binding = self._provider_invocation_binding(
                session,
                audition_session,
            )
            classification, count, source_id = self._provider_execution_classification(
                source_request
            )
        except ServiceError as exc:
            raise ValueError("The audition cache source evidence is invalid.") from exc
        if (
            not isinstance(input_properties, dict)
            or output_properties != expected_properties
            or controls is None
            or request_fingerprint(controls) != source_request.provider_control_fingerprint
            or any(input_properties.get(key) != value for key, value in invocation_binding.items())
            or input_properties.get("channels") != artifact.channel_count
            or input_properties.get("outputFormat") != "pcm_s16le_wav"
            or input_properties.get("sampleRateHz") != artifact.sample_rate_hz
            or classification != "provider_execution"
            or count != 1
            or source_id is not None
        ):
            raise ValueError("The audition cache source execution failed verification.")
        expected_cache_key = AuditionCacheIdentity(
            project_id=cache.project_id,
            provider_id=source_request.provider_id,
            adapter_version=(
                FIXTURE_ADAPTER_VERSION
                if source_request.provider_id == FIXTURE_PROVIDER_ID
                else KOKORO_ADAPTER_VERSION
            ),
            runtime_fingerprint=source_request.runtime_profile_fingerprint,
            model_package_fingerprint=source_request.model_package_fingerprint,
            voice_profile_id=source_request.voice_profile_id,
            voice_runtime_binding_fingerprint=(source_request.voice_runtime_binding_fingerprint),
            provider_voice_id=source_request.provider_voice_id,
            voice_assignment_id=source_request.assignment_id,
            voice_assignment_revision=source_request.assignment_revision,
            normalized_text_sha256=source_request.normalized_text_sha256,
            pronunciation_plan_fingerprint=(source_request.pronunciation_plan_fingerprint),
            provider_control_fingerprint=(source_request.provider_control_fingerprint),
            output_profile_fingerprint=AUDITION_PROFILE_FINGERPRINT,
            producer_version=_PRODUCER_VERSION,
        ).key()
        if expected_cache_key != cache.cache_key:
            raise ValueError("The audition cache key failed exact verification.")
        return binding, source_request

    def _verified_cache_hit(
        self,
        *,
        project_id: str,
        cache_key: str,
    ) -> tuple[
        AuditionCacheRecordRow | None,
        AudioArtifactRow | None,
        Any | None,
    ]:
        with self.database.immediate_session() as session:
            cache = session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.project_id == project_id,
                    AuditionCacheRecordRow.cache_key == cache_key,
                )
            )
            if cache is None or cache.state != "verified":
                return None, None, None
            binding = session.get(
                VoiceRuntimeBindingRow,
                cache.voice_runtime_binding_id,
            )
            source_request = session.get(
                SpeechProviderRequestRow,
                cache.provider_request_id,
            )
            if (
                binding is None
                or not binding.active
                or binding.binding_fingerprint != cache.voice_runtime_binding_fingerprint
                or binding.provider_voice_id != cache.provider_voice_id
                or source_request is None
                or source_request.voice_runtime_binding_id != binding.id
                or source_request.voice_runtime_binding_fingerprint != binding.binding_fingerprint
                or source_request.provider_voice_id != binding.provider_voice_id
            ):
                cache.state = "corrupt"
                cache.last_verified_at = utc_now()
                return None, None, None
            artifact = session.get(AudioArtifactRow, cache.artifact_id)
            if artifact is None or artifact.project_id != project_id:
                cache.state = "missing"
                cache.last_verified_at = utc_now()
                return None, None, None
            try:
                self._validated_cache_graph(
                    session,
                    cache=cache,
                    artifact=artifact,
                )
            except (ServiceError, ValueError):
                cache.state = "corrupt"
                cache.last_verified_at = utc_now()
                if artifact.availability == "present":
                    artifact.availability = "corrupt"
                self._invalidate_artifact_integrity(
                    session,
                    artifact=artifact,
                    reason_code="AUDITION_CACHE_GRAPH_CHANGED",
                    rationale="Approved audition cache evidence failed exact verification.",
                )
                return None, None, None
            try:
                _path, payload = _read_bounded_stable_regular_file(
                    self.data_dir,
                    self._resolve_verified_artifact_path(artifact.storage_key),
                    maximum_bytes=_MAX_AUDIO_BYTES,
                    expected_byte_size=artifact.byte_count,
                )
                qc = inspect_audition_wav_bytes(payload)
            except (OSError, ServiceError, AuditionError, ValueError):
                cache.state = "missing"
                cache.last_verified_at = utc_now()
                if artifact.availability == "present":
                    artifact.availability = "corrupt"
                self._invalidate_artifact_integrity(
                    session,
                    artifact=artifact,
                    reason_code="AUDITION_AUDIO_MISSING",
                    rationale="Approved audition audio is no longer available.",
                )
                return None, None, None
            expected = parse_json(cache.expected_audio_properties_json, {})
            if (
                artifact.availability != "present"
                or len(payload) != cache.expected_byte_count
                or sha256_bytes(payload) != cache.expected_artifact_sha256
                or qc.measurements.content_sha256 != cache.expected_artifact_sha256
                or qc.blocking_findings
                or expected
                != {
                    "channelCount": qc.measurements.channel_count,
                    "durationMilliseconds": round(qc.measurements.duration_ms),
                    "frameCount": qc.measurements.frame_count,
                    "sampleRateHz": qc.measurements.sample_rate_hz,
                    "sampleWidthBytes": qc.measurements.sample_width_bytes,
                }
            ):
                cache.state = "corrupt"
                cache.last_verified_at = utc_now()
                if artifact.availability == "present":
                    artifact.availability = "corrupt"
                self._invalidate_artifact_integrity(
                    session,
                    artifact=artifact,
                    reason_code="AUDITION_AUDIO_INTEGRITY_CHANGED",
                    rationale="Approved audition audio failed current integrity checks.",
                )
                return None, None, None
            return cache, artifact, qc

    def _prepare_audio_file(
        self,
        *,
        project_id: str,
        provider_request_id: str,
        provider_id: str,
        speech_artifact: SpeechArtifact,
    ) -> tuple[AudioArtifactRow, Any, Path]:
        if len(speech_artifact.wav_bytes) > _MAX_AUDIO_BYTES:
            raise ServiceError(
                422,
                "AUDITION_AUDIO_TOO_LARGE",
                "The generated audition exceeded its fixed audio bound.",
            )
        artifact_id = new_id()
        storage_key = str(
            Path("projects") / project_id / "auditions" / "audio" / f"{artifact_id}.wav"
        ).replace("\\", "/")
        staging_key = str(
            Path("projects")
            / project_id
            / "auditions"
            / "audio-staging"
            / f"{artifact_id}.wav.pending"
        ).replace("\\", "/")
        staging_path = self.data_dir / Path(staging_key)
        ensure_private_directory(staging_path.parent)
        _verified_storage_path(
            self.data_dir,
            staging_path.parent,
            require_directory=True,
        )
        _atomic_write(staging_path, speech_artifact.wav_bytes)
        try:
            staging_path, staged_payload = _read_bounded_stable_regular_file(
                self.data_dir,
                staging_path,
                maximum_bytes=_MAX_AUDIO_BYTES,
                expected_byte_size=len(speech_artifact.wav_bytes),
            )
            if sha256_bytes(staged_payload) != sha256_bytes(speech_artifact.wav_bytes):
                raise AuditionError("The staged audition audio changed.")
            qc = inspect_audition_wav_bytes(staged_payload)
        except (OSError, AuditionError, ValueError) as exc:
            staging_path.unlink(missing_ok=True)
            raise ServiceError(
                422,
                "AUDITION_AUDIO_INVALID",
                "The generated audition failed PCM WAV verification.",
            ) from exc
        if qc.blocking_findings:
            staging_path.unlink(missing_ok=True)
            raise ServiceError(
                422,
                "AUDITION_AUDIO_BLOCKED",
                "The generated audition failed bounded audio integrity checks.",
            )
        duration_ms = max(1, round(qc.measurements.duration_ms))
        artifact_fingerprint = request_fingerprint(
            {
                "byteCount": qc.byte_size,
                "channelCount": qc.measurements.channel_count,
                "contentSha256": qc.measurements.content_sha256,
                "durationMilliseconds": duration_ms,
                "frameCount": qc.measurements.frame_count,
                "modelId": speech_artifact.model_id,
                "modelVersion": speech_artifact.model_version,
                "providerVoiceId": speech_artifact.voice_id,
                "sampleRateHz": qc.measurements.sample_rate_hz,
                "sampleWidthBytes": qc.measurements.sample_width_bytes,
                "voiceSha256": speech_artifact.voice_sha256,
            }
        )
        row = AudioArtifactRow(
            id=artifact_id,
            project_id=project_id,
            provider_request_id=provider_request_id,
            storage_key=storage_key,
            content_sha256=qc.measurements.content_sha256,
            byte_count=qc.byte_size,
            container_format="wav",
            codec="pcm_s16le",
            sample_format="signed_integer_little_endian",
            sample_rate_hz=qc.measurements.sample_rate_hz,
            channel_count=qc.measurements.channel_count,
            sample_width_bytes=qc.measurements.sample_width_bytes,
            frame_count=qc.measurements.frame_count,
            duration_ms=duration_ms,
            artifact_fingerprint=artifact_fingerprint,
            availability="present",
            provenance_json=_provenance(
                "fixture_provider" if provider_id == FIXTURE_PROVIDER_ID else "real_local_provider"
            ),
            created_at=utc_now(),
            purged_at=None,
        )
        return row, qc, staging_path

    def publish_generation_result(
        self,
        session: Session,
        job: JobRow,
        result: Mapping[str, Any],
    ) -> Callable[[], None] | None:
        """Publish exact audition evidence inside the queue's final write claim."""

        if self._runtime_shutdown_started.is_set():
            raise ServiceError(
                503,
                "AUDITION_RUNTIME_SHUTDOWN_STARTED",
                "Audition publication is unavailable during runtime shutdown.",
                retryable=True,
            )
        provider_request_id = result.get("providerRequestId")
        expected_clip_id = result.get("expectedClipId")
        cache_hit = result.get("cacheHit")
        artifact_value = result.get("artifact")
        staging_value = result.get("stagingPath")
        if (
            not isinstance(provider_request_id, str)
            or not isinstance(expected_clip_id, str)
            or not isinstance(cache_hit, bool)
            or not isinstance(artifact_value, AudioArtifactRow)
            or (staging_value is not None and not isinstance(staging_value, Path))
        ):
            raise ServiceError(
                500,
                "AUDITION_PUBLICATION_INVALID",
                "The audition publication payload is invalid.",
            )
        request_row = session.get(SpeechProviderRequestRow, provider_request_id)
        audition_session = (
            session.get(AuditionSessionRow, request_row.session_id)
            if request_row is not None
            else None
        )
        script = (
            session.get(AuditionScriptRow, request_row.script_id)
            if request_row is not None
            else None
        )
        plan = (
            session.get(TextNormalizationPlanRow, request_row.normalization_plan_id)
            if request_row is not None
            else None
        )
        if (
            request_row is None
            or audition_session is None
            or script is None
            or plan is None
            or job.type != "generate_audition"
            or job.project_id != request_row.project_id
            or job.target_id != audition_session.id
            or job.input_fingerprint != request_row.request_fingerprint
            or request_row.job_id != job.id
            or request_row.attempt != job.current_attempt
            or request_row.outcome != "running"
            or request_row.normalized_text_sha256 != plan.normalized_text_sha256
            or request_row.pronunciation_plan_fingerprint != plan.pronunciation_plan_fingerprint
        ):
            raise ServiceError(
                409,
                "AUDITION_PUBLICATION_EVIDENCE_CHANGED",
                "The audition publication evidence changed before commit.",
            )
        self._validate_session_evidence(
            session,
            project_id=job.project_id,
            role_id=audition_session.role_id,
            evidence=self._session_evidence_wire(session, audition_session),
        )
        if self._session_dependency_drift(session, audition_session) is not None:
            raise ServiceError(
                409,
                "AUDITION_PUBLICATION_EVIDENCE_CHANGED",
                "The audition dependency evidence changed before commit.",
            )

        artifact: AudioArtifactRow
        placed_path: Path | None = None
        try:
            if cache_hit:
                cached_artifact = session.get(AudioArtifactRow, artifact_value.id)
                if cached_artifact is None or cached_artifact.project_id != job.project_id:
                    raise ServiceError(
                        409,
                        "AUDITION_CACHE_CHANGED",
                        "The verified cache artifact changed before publication.",
                    )
                artifact = cached_artifact
                cache_row = session.scalar(
                    select(AuditionCacheRecordRow).where(
                        AuditionCacheRecordRow.project_id == job.project_id,
                        AuditionCacheRecordRow.cache_key == request_row.cache_key,
                        AuditionCacheRecordRow.artifact_id == artifact.id,
                        AuditionCacheRecordRow.state == "verified",
                    )
                )
                if cache_row is None:
                    raise ServiceError(
                        409,
                        "AUDITION_CACHE_CHANGED",
                        "The verified cache record changed before publication.",
                    )
                artifact_path = self._resolve_verified_artifact_path(artifact.storage_key)
            else:
                if staging_value is None:
                    raise ServiceError(
                        500,
                        "AUDITION_STAGING_MISSING",
                        "The generated audition staging file is unavailable.",
                    )
                try:
                    staging_path = _verified_storage_path(
                        self.data_dir,
                        staging_value,
                    )
                except (OSError, ValueError) as exc:
                    raise ServiceError(
                        500,
                        "AUDITION_STAGING_INVALID",
                        "The generated audition staging file escaped private storage.",
                    ) from exc
                artifact = artifact_value
                if (
                    artifact.project_id != job.project_id
                    or artifact.provider_request_id != request_row.id
                    or session.get(AudioArtifactRow, artifact.id) is not None
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_ARTIFACT_CHANGED",
                        "The generated audition artifact identity changed.",
                    )
                artifact_path = staging_path

            artifact_path, payload = _read_bounded_stable_regular_file(
                self.data_dir,
                artifact_path,
                maximum_bytes=_MAX_AUDIO_BYTES,
                expected_byte_size=artifact.byte_count,
            )
            qc = inspect_audition_wav_bytes(payload)
            if (
                qc.blocking_findings
                or artifact.availability != "present"
                or len(payload) != artifact.byte_count
                or sha256_bytes(payload) != artifact.content_sha256
                or qc.measurements.content_sha256 != artifact.content_sha256
                or qc.measurements.channel_count != artifact.channel_count
                or qc.measurements.frame_count != artifact.frame_count
                or qc.measurements.sample_rate_hz != artifact.sample_rate_hz
                or qc.measurements.sample_width_bytes != artifact.sample_width_bytes
                or max(1, round(qc.measurements.duration_ms)) != artifact.duration_ms
            ):
                raise ServiceError(
                    409,
                    "AUDITION_AUDIO_CHANGED",
                    "The audition audio changed before publication.",
                )

            runtime_row: SpeechRuntimeInstanceRow | None = None
            if not cache_hit:
                runtime_row = (
                    session.get(SpeechRuntimeInstanceRow, request_row.runtime_instance_id)
                    if request_row.runtime_instance_id is not None
                    else None
                )
                if self._runtime_shutdown_started.is_set() or (
                    request_row.runtime_instance_id is not None
                    and (
                        runtime_row is None
                        or runtime_row.state != "busy"
                        or runtime_row.stopped_at is not None
                        or runtime_row.exit_code is not None
                    )
                ):
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_SHUTDOWN_STARTED",
                        "The managed runtime became unavailable before publication.",
                        retryable=True,
                    )
                relative_final_path = Path(artifact.storage_key)
                if (
                    relative_final_path.is_absolute()
                    or relative_final_path.drive
                    or ".." in relative_final_path.parts
                ):
                    raise ServiceError(
                        500,
                        "AUDITION_ARTIFACT_CHANGED",
                        "The generated audition destination failed verification.",
                    )
                final_path = self.data_dir / relative_final_path
                ensure_private_directory(final_path.parent)
                _verified_storage_path(
                    self.data_dir,
                    final_path.parent,
                    require_directory=True,
                )
                if final_path.exists():
                    raise ServiceError(
                        409,
                        "AUDITION_ARTIFACT_CHANGED",
                        "The generated audition destination already exists.",
                    )
                artifact_path.replace(final_path)
                placed_path = final_path
                session.add(artifact)
                session.flush()
            clip_id = self._publish_clip_and_cache(
                project_id=job.project_id,
                job_id=job.id,
                provider_request_id=request_row.id,
                artifact_id=artifact.id,
                qc=qc,
                cache_hit=cache_hit,
                expected_clip_id=expected_clip_id,
                publication_session=session,
            )
            if clip_id != expected_clip_id:
                raise ServiceError(
                    409,
                    "AUDITION_PUBLICATION_CONFLICT",
                    "A different audition clip already owns this request.",
                )
            if runtime_row is not None:
                if self._runtime_shutdown_started.is_set() or runtime_row.state != "busy":
                    raise ServiceError(
                        503,
                        "AUDITION_RUNTIME_SHUTDOWN_STARTED",
                        "The managed runtime became unavailable during publication.",
                        retryable=True,
                    )
                runtime_row.state = "idle"
                runtime_row.last_used_at = utc_now()
        except Exception:
            if placed_path is not None:
                placed_path.unlink(missing_ok=True)
            raise

        if placed_path is None:
            return None

        def cleanup() -> None:
            placed_path.unlink(missing_ok=True)

        return cleanup

    def _publish_clip_and_cache(
        self,
        *,
        project_id: str,
        job_id: str,
        provider_request_id: str,
        artifact_id: str,
        qc: Any,
        cache_hit: bool,
        expected_clip_id: str,
        publication_session: Session | None = None,
    ) -> str:
        now = utc_now()
        session_context = (
            nullcontext(publication_session)
            if publication_session is not None
            else self.database.immediate_session()
        )
        with session_context as session:
            request_row = session.get(SpeechProviderRequestRow, provider_request_id)
            artifact = session.get(AudioArtifactRow, artifact_id)
            if request_row is None or artifact is None or request_row.job_id != job_id:
                raise ServiceError(
                    500,
                    "AUDITION_PUBLICATION_EVIDENCE_MISSING",
                    "The audition publication evidence is unavailable.",
                )
            existing_clip = session.scalar(
                select(AuditionClipRow).where(AuditionClipRow.provider_request_id == request_row.id)
            )
            if existing_clip is not None:
                return existing_clip.id
            audition_session = session.get(AuditionSessionRow, request_row.session_id)
            script = session.get(AuditionScriptRow, request_row.script_id)
            plan = session.get(TextNormalizationPlanRow, request_row.normalization_plan_id)
            if audition_session is None or script is None or plan is None:
                raise ServiceError(
                    500,
                    "AUDITION_PUBLICATION_EVIDENCE_MISSING",
                    "The audition publication evidence is unavailable.",
                )
            if (
                request_row.voice_runtime_binding_id != audition_session.voice_runtime_binding_id
                or request_row.voice_runtime_binding_fingerprint
                != audition_session.voice_runtime_binding_fingerprint
                or request_row.provider_voice_id != audition_session.provider_voice_id
            ):
                raise ServiceError(
                    409,
                    "AUDITION_VOICE_RUNTIME_BINDING_CHANGED",
                    "The provider request voice runtime binding changed before publication.",
                )
            cache = session.scalar(
                select(AuditionCacheRecordRow).where(
                    AuditionCacheRecordRow.project_id == project_id,
                    AuditionCacheRecordRow.cache_key == request_row.cache_key,
                )
            )
            self._assert_provider_execution_classification(
                request_row,
                cache_hit=cache_hit,
                source_provider_request_id=(
                    cache.provider_request_id if cache is not None else None
                ),
            )
            expected_properties = {
                "channelCount": artifact.channel_count,
                "durationMilliseconds": artifact.duration_ms,
                "frameCount": artifact.frame_count,
                "sampleRateHz": artifact.sample_rate_hz,
                "sampleWidthBytes": artifact.sample_width_bytes,
            }
            verification_fingerprint = request_fingerprint(
                {
                    "artifactFingerprint": artifact.artifact_fingerprint,
                    "cacheKey": request_row.cache_key,
                    "expectedAudioProperties": expected_properties,
                    "providerVoiceId": request_row.provider_voice_id,
                    "voiceRuntimeBindingFingerprint": (
                        request_row.voice_runtime_binding_fingerprint
                    ),
                }
            )
            if cache is None:
                cache_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(AuditionCacheRecordRow)
                        .where(AuditionCacheRecordRow.project_id == project_id)
                    )
                    or 0
                )
                if cache_count >= _MAX_CACHE_RECORDS:
                    raise ServiceError(
                        409,
                        "AUDITION_CACHE_LIMIT_EXCEEDED",
                        "This project reached its private audition cache limit.",
                    )
                cache = AuditionCacheRecordRow(
                    id=new_id(),
                    project_id=project_id,
                    cache_key=request_row.cache_key,
                    voice_runtime_binding_id=request_row.voice_runtime_binding_id,
                    voice_runtime_binding_fingerprint=(
                        request_row.voice_runtime_binding_fingerprint
                    ),
                    provider_voice_id=request_row.provider_voice_id,
                    artifact_id=artifact.id,
                    provider_request_id=request_row.id,
                    session_id=audition_session.id,
                    script_id=script.id,
                    expected_artifact_sha256=artifact.content_sha256,
                    expected_byte_count=artifact.byte_count,
                    expected_audio_properties_json=canonical_json(expected_properties),
                    verification_fingerprint=verification_fingerprint,
                    state="verified",
                    hit_count=0,
                    created_at=now,
                    last_verified_at=now,
                    last_hit_at=None,
                    purged_at=None,
                )
                session.add(cache)
            elif not cache_hit:
                cache.artifact_id = artifact.id
                cache.voice_runtime_binding_id = request_row.voice_runtime_binding_id
                cache.voice_runtime_binding_fingerprint = (
                    request_row.voice_runtime_binding_fingerprint
                )
                cache.provider_voice_id = request_row.provider_voice_id
                cache.provider_request_id = request_row.id
                cache.session_id = audition_session.id
                cache.script_id = script.id
                cache.expected_artifact_sha256 = artifact.content_sha256
                cache.expected_byte_count = artifact.byte_count
                cache.expected_audio_properties_json = canonical_json(expected_properties)
                cache.verification_fingerprint = verification_fingerprint
                cache.state = "verified"
                cache.last_verified_at = now
                cache.purged_at = None
            else:
                if (
                    cache.voice_runtime_binding_id != request_row.voice_runtime_binding_id
                    or cache.voice_runtime_binding_fingerprint
                    != request_row.voice_runtime_binding_fingerprint
                    or cache.provider_voice_id != request_row.provider_voice_id
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_CACHE_BINDING_CHANGED",
                        "The verified cache voice runtime binding changed.",
                    )
                cache.hit_count += 1
                cache.last_hit_at = now
                cache.last_verified_at = now
            latest_clip = session.scalar(
                select(AuditionClipRow)
                .where(
                    AuditionClipRow.session_id == audition_session.id,
                    AuditionClipRow.script_id == script.id,
                )
                .order_by(AuditionClipRow.revision.desc(), AuditionClipRow.id.desc())
                .limit(1)
            )
            revision = (latest_clip.revision + 1) if latest_clip is not None else 1
            clip_fingerprint = request_fingerprint(
                {
                    "artifactFingerprint": artifact.artifact_fingerprint,
                    "assignmentId": audition_session.assignment_id,
                    "assignmentRevision": audition_session.assignment_revision,
                    "cacheKey": request_row.cache_key,
                    "providerVoiceId": request_row.provider_voice_id,
                    "requestFingerprint": request_row.request_fingerprint,
                    "revision": revision,
                    "scriptId": script.id,
                    "sessionId": audition_session.id,
                    "voiceRuntimeBindingFingerprint": (
                        request_row.voice_runtime_binding_fingerprint
                    ),
                }
            )
            clip = AuditionClipRow(
                id=expected_clip_id,
                project_id=project_id,
                session_id=audition_session.id,
                script_id=script.id,
                provider_request_id=request_row.id,
                artifact_id=artifact.id,
                cache_record_id=cache.id,
                role_id=audition_session.role_id,
                assignment_id=audition_session.assignment_id,
                assignment_revision=audition_session.assignment_revision,
                voice_profile_record_id=audition_session.voice_profile_record_id,
                revision=revision,
                request_fingerprint=request_row.request_fingerprint,
                cache_key=request_row.cache_key,
                cache_hit=cache_hit,
                clip_fingerprint=clip_fingerprint,
                producer_id=_PRODUCER_ID,
                producer_version=_PRODUCER_VERSION,
                supersedes_clip_id=latest_clip.id if latest_clip is not None else None,
                provenance_json=_provenance(
                    "fixture_provider"
                    if request_row.provider_id == FIXTURE_PROVIDER_ID
                    else "real_local_provider",
                    details={
                        "providerVoiceId": request_row.provider_voice_id,
                        "voiceRuntimeBindingFingerprint": (
                            request_row.voice_runtime_binding_fingerprint
                        ),
                        "voiceRuntimeBindingId": request_row.voice_runtime_binding_id,
                    },
                ),
                created_at=now,
            )
            session.add(clip)
            session.flush()
            peak = max(-200_000, min(0, round(qc.measurements.peak_dbfs * 1000)))
            rms = max(-200_000, min(0, round(qc.measurements.rms_dbfs * 1000)))
            silent_frame_count = max(
                0,
                qc.measurements.frame_count - qc.measurements.non_silent_frames,
            )
            silence_ratio_ppm = (
                silent_frame_count * 1_000_000 // qc.measurements.frame_count
                if qc.measurements.frame_count > 0
                else 1_000_000
            )
            findings = {
                "blockingFindingCodes": list(qc.blocking_findings),
                "warningCodes": list(qc.warnings),
            }
            blocking_finding_codes = tuple(qc.blocking_findings)
            warning_codes = tuple(qc.warnings)
            quality_revision = (
                int(
                    session.scalar(
                        select(func.max(AudioQualityRecordRow.revision)).where(
                            AudioQualityRecordRow.artifact_id == artifact.id
                        )
                    )
                    or 0
                )
                + 1
            )
            quality_id = new_id()
            quality_outcome = (
                "blocked" if blocking_finding_codes else "warning" if warning_codes else "passed"
            )
            bounded_silence_ratio_ppm = max(0, min(1_000_000, silence_ratio_ppm))
            quality_fingerprint = _audio_quality_fingerprint(
                quality_record_id=quality_id,
                project_id=project_id,
                clip_id=clip.id,
                artifact_id=artifact.id,
                artifact_fingerprint=artifact.artifact_fingerprint,
                provider_request_id=request_row.id,
                revision=quality_revision,
                policy_id=_AUDIO_POLICY_ID,
                policy_version=AUDITION_PROFILE_VERSION,
                policy_fingerprint=AUDITION_PROFILE_FINGERPRINT,
                outcome=quality_outcome,
                peak_millidbfs=peak,
                rms_millidbfs=rms,
                silence_ratio_ppm=bounded_silence_ratio_ppm,
                clipped_sample_count=qc.measurements.clipped_sample_count,
                warning_count=len(warning_codes),
                blocking_finding_count=len(blocking_finding_codes),
                blocking_finding_codes=blocking_finding_codes,
                warning_codes=warning_codes,
            )
            quality = AudioQualityRecordRow(
                id=quality_id,
                project_id=project_id,
                clip_id=clip.id,
                artifact_id=artifact.id,
                provider_request_id=request_row.id,
                revision=quality_revision,
                policy_id=_AUDIO_POLICY_ID,
                policy_version=AUDITION_PROFILE_VERSION,
                policy_fingerprint=AUDITION_PROFILE_FINGERPRINT,
                outcome=quality_outcome,
                peak_millidbfs=peak,
                rms_millidbfs=rms,
                silence_ratio_ppm=bounded_silence_ratio_ppm,
                clipped_sample_count=qc.measurements.clipped_sample_count,
                warning_count=len(warning_codes),
                blocking_finding_count=len(blocking_finding_codes),
                findings_json=canonical_json(findings),
                quality_fingerprint=quality_fingerprint,
                provenance_json=_provenance("application"),
                created_at=now,
            )
            session.add(quality)
            request_row.outcome = "succeeded"
            request_row.retryable = False
            request_row.output_artifact_sha256 = artifact.content_sha256
            input_properties = parse_json(request_row.audio_properties_json, {})
            if not isinstance(input_properties, dict):
                raise ServiceError(
                    409,
                    "AUDITION_CONTROLS_INVALID",
                    "The stored speech controls failed verification.",
                )
            request_row.audio_properties_json = canonical_json(
                {
                    "input": input_properties,
                    "output": expected_properties,
                }
            )
            request_row.finished_at = now
            audition_session.state = "reviewable"
            audition_session.published_at = now
            session.flush()
            self._ensure_per_role_review(
                session,
                audition_session=audition_session,
                clip=clip,
                quality=quality,
            )
            return clip.id

    def _mark_provider_request_failed(self, job_id: str, error: BaseException) -> None:
        with self.database.immediate_session() as session:
            job = session.get(JobRow, job_id)
            request_row = session.scalar(
                select(SpeechProviderRequestRow)
                .where(
                    SpeechProviderRequestRow.job_id == job_id,
                    SpeechProviderRequestRow.attempt
                    == (job.current_attempt if job is not None else -1),
                )
                .limit(1)
            )
            if request_row is None or request_row.outcome in {"succeeded", "cancelled"}:
                return
            self._provider_execution_classification(request_row)
            if job is not None and (
                job.state in {"cancel_requested", "cancelled"} or job.cancellation_requested
            ):
                request_row.outcome = "cancelled"
                request_row.retryable = False
                request_row.finished_at = utc_now()
                return
            request_row.outcome = "failed"
            request_row.retryable = bool(getattr(error, "retryable", True))
            request_row.finished_at = utc_now()

    @staticmethod
    def _append_provider_attempt(
        session: Session,
        *,
        prior: SpeechProviderRequestRow,
        job: JobRow,
        now: str,
    ) -> SpeechProviderRequestRow:
        """Append one immutable execution identity when a durable job is retried."""

        if prior.attempt >= job.current_attempt:
            return prior
        input_properties = parse_json(prior.audio_properties_json, {})
        if isinstance(input_properties, dict) and isinstance(
            input_properties.get("input"),
            dict,
        ):
            input_properties = input_properties["input"]
        if not isinstance(input_properties, dict):
            input_properties = {}
        row = SpeechProviderRequestRow(
            id=new_id(),
            project_id=prior.project_id,
            job_id=prior.job_id,
            attempt=job.current_attempt,
            session_id=prior.session_id,
            script_id=prior.script_id,
            normalization_plan_id=prior.normalization_plan_id,
            runtime_profile_id=prior.runtime_profile_id,
            runtime_instance_id=None,
            model_installation_record_id=prior.model_installation_record_id,
            model_verification_id=prior.model_verification_id,
            voice_profile_record_id=prior.voice_profile_record_id,
            assignment_id=prior.assignment_id,
            assignment_revision=prior.assignment_revision,
            voice_runtime_binding_id=prior.voice_runtime_binding_id,
            voice_runtime_binding_fingerprint=prior.voice_runtime_binding_fingerprint,
            provider_voice_id=prior.provider_voice_id,
            provider_id=prior.provider_id,
            provider_version=prior.provider_version,
            provider_operation_id=prior.provider_operation_id,
            model_id=prior.model_id,
            model_version=prior.model_version,
            model_package_fingerprint=prior.model_package_fingerprint,
            runtime_profile_fingerprint=prior.runtime_profile_fingerprint,
            voice_profile_id=prior.voice_profile_id,
            voice_profile_version=prior.voice_profile_version,
            normalized_text_sha256=prior.normalized_text_sha256,
            pronunciation_plan_fingerprint=prior.pronunciation_plan_fingerprint,
            provider_control_fingerprint=prior.provider_control_fingerprint,
            cache_key=prior.cache_key,
            request_fingerprint=prior.request_fingerprint,
            idempotency_key=stable_id(
                "phase3b-provider-attempt",
                job.id,
                job.current_attempt,
                prior.request_fingerprint,
            ),
            outcome="running",
            retryable=False,
            output_artifact_sha256=None,
            audio_properties_json=canonical_json(input_properties),
            warnings_json="[]",
            provenance_json=_provenance(
                "application",
                input_fingerprint=prior.request_fingerprint,
                details={
                    "attempt": job.current_attempt,
                    "executionClassification": "provider_execution",
                    "providerDispatchCount": 0,
                    "supersedesProviderRequestId": prior.id,
                    "originalIdempotencyKeyFingerprint": sha256_text(prior.idempotency_key),
                },
            ),
            started_at=None,
            finished_at=None,
        )
        session.add(row)
        return row

    def mark_job_terminal(self, session: Session, job: JobRow, state: str) -> None:
        """Project a durable job transition onto its audition lifecycle records."""

        if job.type != "generate_audition" or job.target_id is None:
            return
        audition_session = session.get(AuditionSessionRow, job.target_id)
        if (
            audition_session is None
            or audition_session.project_id != job.project_id
            or audition_session.state == "invalidated"
        ):
            return
        provider_request = session.scalar(
            select(SpeechProviderRequestRow)
            .where(
                SpeechProviderRequestRow.job_id == job.id,
                SpeechProviderRequestRow.session_id == audition_session.id,
            )
            .order_by(
                SpeechProviderRequestRow.attempt.desc(),
                SpeechProviderRequestRow.id.desc(),
            )
            .limit(1)
        )
        now = utc_now()
        if provider_request is not None and provider_request.attempt < job.current_attempt:
            provider_request = self._append_provider_attempt(
                session,
                prior=provider_request,
                job=job,
                now=now,
            )
        if provider_request is not None:
            self._provider_execution_classification(provider_request)
        if state == "running":
            audition_session.state = "generating"
            if provider_request is not None and provider_request.outcome == "queued":
                provider_request.outcome = "running"
                provider_request.retryable = False
            return
        if state == "succeeded":
            audition_session.state = "reviewable"
            audition_session.published_at = audition_session.published_at or now
            return
        if state == "cancelled":
            audition_session.state = "cancelled"
            if provider_request is not None and provider_request.outcome != "succeeded":
                provider_request.outcome = "cancelled"
                provider_request.retryable = False
                provider_request.finished_at = now
            return
        if state in {"failed", "interrupted"}:
            audition_session.state = "failed"
            if provider_request is not None and provider_request.outcome != "succeeded":
                provider_request.outcome = "failed"
                provider_request.retryable = True
                provider_request.finished_at = now

    def begin_runtime_shutdown(self) -> None:
        """Seal runtime acquisition and publication before worker drain begins."""

        self._runtime_shutdown_started.set()

    def shutdown_runtimes(self) -> tuple[SpeechRuntimeExitEvidence, ...]:
        """Stop only workers owned by this repository and persist their exit evidence."""

        with self._runtime_lock:
            self._runtime_shutdown_started.set()
            owned_runtimes = tuple(self._runtimes.items())
        shutdown_scope_exceeded = (
            len(self._owned_runtime_instance_ids) > _MAX_OWNED_RUNTIME_SHUTDOWN_PROOFS
        )
        shutdown_incomplete = False
        for key, (runtime, instance_id) in owned_runtimes:
            identity = runtime.identity or runtime.last_identity
            evidence: SpeechRuntimeExitEvidence | None = None
            stop_failed = False
            try:
                evidence = runtime.stop(reason="clean")
            except Exception:
                stop_failed = True
                evidence = runtime.last_exit
            if evidence is None:
                try:
                    with self.database.session() as session:
                        row = session.get(SpeechRuntimeInstanceRow, instance_id)
                        warnings = parse_json(row.warnings_json, {}) if row is not None else {}
                        if not isinstance(warnings, dict):
                            warnings = {}
                        evidence = (
                            self._unconfirmed_runtime_exit_evidence(row, warnings)
                            if row is not None
                            else None
                        )
                except Exception:
                    shutdown_incomplete = True
                stop_failed = True
            persisted: dict[str, Any] | None = None
            if evidence is not None:
                try:
                    persisted = self._persist_runtime_exit_evidence(
                        instance_id=instance_id,
                        evidence=evidence,
                        stop_failed=stop_failed,
                    )
                except Exception:
                    shutdown_incomplete = True
            else:
                shutdown_incomplete = True
            exact_exit = identity is not None and _runtime_exit_confirms_identity(
                evidence,
                identity,
            )
            if persisted is not None and exact_exit:
                with self._runtime_lock:
                    if self._runtimes.get(key) == (runtime, instance_id):
                        self._runtimes.pop(key, None)
            else:
                shutdown_incomplete = True
                if identity is not None:
                    try:
                        self._quarantine_bound_runtime(
                            instance_id=instance_id,
                            runtime=runtime,
                            identity=identity,
                        )
                    except Exception:
                        shutdown_incomplete = True
        with self._runtime_lock:
            quarantined_runtimes = tuple(self._quarantined_runtimes.items())
        for instance_id, quarantined in quarantined_runtimes:
            runtime = quarantined.runtime
            identity = quarantined.identity or runtime.identity or runtime.last_identity
            stop_failed = False
            try:
                evidence = runtime.stop(reason="clean")
            except Exception:
                stop_failed = True
                evidence = runtime.last_exit
            quarantine_persisted: dict[str, Any] | None = None
            if quarantined.bound:
                if evidence is not None:
                    try:
                        quarantine_persisted = self._persist_runtime_exit_evidence(
                            instance_id=instance_id,
                            evidence=evidence,
                            stop_failed=stop_failed,
                        )
                    except Exception:
                        shutdown_incomplete = True
            else:
                try:
                    self._account_unbound_runtime(
                        instance_id=instance_id,
                        runtime=runtime,
                        evidence=evidence,
                        stop_failed=stop_failed,
                    )
                except Exception:
                    shutdown_incomplete = True
            exact_tree_exit = (
                _runtime_exit_confirms_identity(evidence, identity)
                if identity is not None
                else _runtime_exit_confirms_owned_tree(evidence)
            )
            if quarantined.bound and quarantine_persisted is None:
                exact_tree_exit = False
            if exact_tree_exit:
                self._release_quarantined_runtime(
                    instance_id=instance_id,
                    runtime=runtime,
                )
            else:
                shutdown_incomplete = True
        if self.settings.runtime_shutdown_evidence_enabled:
            with self._runtime_lock:
                ordered_records = sorted(self._runtime_exit_records.items())
            ordered_proofs = sorted(
                (record[1][1] for record in ordered_records),
                key=lambda value: str(value["runtimeInstanceId"]),
            )
            owned_runtime_count = len(self._owned_runtime_instance_ids)
            evidence_document = {
                "contractVersion": "1.0.0",
                "serviceInstanceId": self.settings.instance_id,
                "ownedRuntimeCount": owned_runtime_count,
                "runtimeExits": ordered_proofs,
                "allGracefulShutdownsConfirmed": bool(ordered_proofs)
                and len(ordered_proofs) == owned_runtime_count
                and all(
                    value["state"] == "stopped" and value["gracefulShutdownConfirmed"] is True
                    for value in ordered_proofs
                ),
                "writtenAt": utc_now(),
            }
            try:
                _atomic_write(
                    self._runtime_shutdown_evidence_path,
                    (canonical_json(evidence_document) + "\n").encode("utf-8"),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise ServiceError(
                    500,
                    "RUNTIME_SHUTDOWN_EVIDENCE_WRITE_FAILED",
                    "The bounded runtime shutdown evidence could not be published.",
                ) from exc
        if shutdown_scope_exceeded:
            raise ServiceError(
                500,
                "RUNTIME_SHUTDOWN_SCOPE_EXCEEDED",
                "The owned speech-runtime shutdown scope exceeded its fixed bound.",
            )
        if shutdown_incomplete:
            raise ServiceError(
                500,
                "RUNTIME_SHUTDOWN_INCOMPLETE",
                "One or more owned speech runtimes did not produce complete exit evidence.",
            )
        with self._runtime_lock:
            return tuple(
                record[0] for _instance_id, record in sorted(self._runtime_exit_records.items())
            )

    # Cache lifecycle ------------------------------------------------------------

    @contextmanager
    def _compensate_cache_clear_files(
        self,
    ) -> Iterator[list[tuple[Path, Path]]]:
        renamed_files: list[tuple[Path, Path]] = []
        try:
            yield renamed_files
        except Exception:
            try:
                for original_path, tombstone_path in reversed(renamed_files):
                    if not tombstone_path.exists():
                        _verified_storage_path(self.data_dir, original_path)
                        continue
                    resolved_tombstone = _verified_storage_path(
                        self.data_dir,
                        tombstone_path,
                    )
                    if original_path.exists():
                        raise ValueError(
                            "Cache rollback would overwrite an existing audio artifact."
                        )
                    resolved_tombstone.rename(original_path)
                    _verified_storage_path(self.data_dir, original_path)
            except (OSError, RuntimeError, ValueError) as rollback_error:
                raise ServiceError(
                    500,
                    "AUDITION_CACHE_CLEAR_ROLLBACK_FAILED",
                    "Private audition cache rollback failed safely.",
                ) from rollback_error
            raise
        else:
            try:
                for _original_path, tombstone_path in renamed_files:
                    if not tombstone_path.exists():
                        continue
                    resolved_tombstone = _verified_storage_path(
                        self.data_dir,
                        tombstone_path,
                    )
                    resolved_tombstone.unlink()
            except (OSError, RuntimeError, ValueError) as cleanup_error:
                raise ServiceError(
                    500,
                    "AUDITION_CACHE_CLEAR_CLEANUP_FAILED",
                    "Private audition cache cleanup is pending safe startup recovery.",
                    retryable=True,
                ) from cleanup_error

    def clear_cache(
        self,
        *,
        project_id: str,
        request: ClearAuditionCacheRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        del actor_id
        request_hash = request_fingerprint(
            request.model_dump(mode="json", by_alias=True) | {"projectId": project_id}
        )
        scope = f"phase3b-cache-clear:{project_id}"
        now = utc_now()
        with (
            self._compensate_cache_clear_files() as renamed_files,
            self.database.immediate_session() as session,
        ):
            project = self._require_project(session, project_id)
            replay = session.get(
                IdempotencyRow,
                {"scope": scope, "key": request.idempotency_key},
            )
            if replay is not None:
                if replay.request_hash != request_hash:
                    raise ServiceError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "That idempotency key was already used for another cache operation.",
                    )
                try:
                    cleared, already_cleared, project_revision = (
                        int(value) for value in replay.resource_id.split(":", maxsplit=2)
                    )
                except (TypeError, ValueError) as exc:
                    raise ServiceError(
                        500,
                        "IDEMPOTENCY_RECORD_INVALID",
                        "The saved cache-clear result is unavailable.",
                    ) from exc
                return {
                    "projectId": project_id,
                    "clearedRecordCount": cleared,
                    "alreadyClearedRecordCount": already_cleared,
                    "projectRevision": project_revision,
                }
            if project.revision != request.expected_project_revision:
                raise ServiceError(
                    409,
                    "PROJECT_REVISION_CHANGED",
                    "The project revision changed; refresh before clearing the cache.",
                )
            cache_rows = list(
                session.scalars(
                    select(AuditionCacheRecordRow)
                    .where(AuditionCacheRecordRow.project_id == project_id)
                    .order_by(AuditionCacheRecordRow.created_at, AuditionCacheRecordRow.id)
                )
            )
            already_cleared = sum(row.state == "cleared" for row in cache_rows)
            rows_to_clear = [row for row in cache_rows if row.state != "cleared"]
            artifact_ids = {row.artifact_id for row in rows_to_clear}
            for artifact_id in artifact_ids:
                artifact = session.get(AudioArtifactRow, artifact_id)
                if (
                    artifact is None
                    or artifact.project_id != project_id
                    or artifact.availability == "purged"
                ):
                    continue
                try:
                    path = self._resolve_verified_artifact_path(artifact.storage_key)
                except ServiceError:
                    try:
                        candidate = resolve_beneath(self.data_dir, artifact.storage_key)
                    except ValueError:
                        artifact.availability = "quarantined"
                    else:
                        if candidate.exists():
                            artifact.availability = "quarantined"
                        else:
                            artifact.availability = "purged"
                            artifact.purged_at = now
                    continue
                try:
                    tombstone_path = path.parent / (f".{path.name}.purge.{uuid4().hex}.tmp")
                    if (
                        _OWNED_AUDIO_PURGE_TOMBSTONE_NAME.fullmatch(tombstone_path.name) is None
                        or tombstone_path.exists()
                    ):
                        raise ValueError("The cache tombstone identity was invalid.")
                    path.rename(tombstone_path)
                    renamed_files.append((path, tombstone_path))
                    _verified_storage_path(self.data_dir, tombstone_path)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise ServiceError(
                        500,
                        "AUDITION_CACHE_CLEAR_FAILED",
                        "A private audition cache artifact could not be staged for removal.",
                        retryable=True,
                    ) from exc
                artifact.availability = "purged"
                artifact.purged_at = now
            purged_artifacts: list[AudioArtifactRow] = []
            cache_cleared_clips: list[AuditionClipRow] = []
            for artifact_id in sorted(artifact_ids):
                artifact = session.get(AudioArtifactRow, artifact_id)
                if artifact is None or artifact.availability == "present":
                    continue
                purged_artifacts.append(artifact)
                cache_cleared_clips.extend(
                    session.scalars(
                        select(AuditionClipRow).where(
                            AuditionClipRow.project_id == project_id,
                            AuditionClipRow.artifact_id == artifact.id,
                        )
                    )
                )
            if purged_artifacts:
                self._invalidate_clip_dependencies(
                    session,
                    project_id=project_id,
                    clips=cache_cleared_clips,
                    source_kind="audio_integrity",
                    source_record_id=request_fingerprint(
                        {
                            "idempotencyKey": request.idempotency_key,
                            "projectId": project_id,
                            "type": "cache_clear",
                        }
                    ),
                    previous_fingerprint=request_fingerprint(
                        sorted(artifact.artifact_fingerprint for artifact in purged_artifacts)
                    ),
                    current_fingerprint=request_fingerprint(
                        [
                            {
                                "artifactId": artifact.id,
                                "availability": artifact.availability,
                                "purgedAt": artifact.purged_at,
                            }
                            for artifact in purged_artifacts
                        ]
                    ),
                    reason_code="AUDITION_AUDIO_PURGED",
                    rationale="Approved audition audio was removed from private cache storage.",
                )
            for row in rows_to_clear:
                row.state = "cleared"
                row.purged_at = now
            cleared = len(rows_to_clear)
            if cleared:
                project.revision += 1
                project.updated_at = now
            result_identity = f"{cleared}:{already_cleared}:{project.revision}"
            session.add(
                IdempotencyRow(
                    scope=scope,
                    key=request.idempotency_key,
                    request_hash=request_hash,
                    resource_id=result_identity,
                    created_at=now,
                )
            )
            return {
                "projectId": project_id,
                "clearedRecordCount": cleared,
                "alreadyClearedRecordCount": already_cleared,
                "projectRevision": project.revision,
            }

    # Review governance and targeted invalidation --------------------------------

    def _ensure_per_role_review(
        self,
        session: Session,
        *,
        audition_session: AuditionSessionRow,
        clip: AuditionClipRow,
        quality: AudioQualityRecordRow,
    ) -> AuditionReviewRecordRow:
        assignment = session.get(CastAssignmentRow, audition_session.assignment_id)
        rights = session.get(VoiceRightsRecordRow, audition_session.rights_record_id)
        verification = session.get(
            ModelVerificationRow,
            audition_session.model_verification_id,
        )
        artifact = session.get(AudioArtifactRow, clip.artifact_id)
        request_row = session.get(SpeechProviderRequestRow, clip.provider_request_id)
        plan = (
            session.get(TextNormalizationPlanRow, request_row.normalization_plan_id)
            if request_row is not None
            else None
        )
        if any(
            value is None
            for value in (assignment, rights, verification, artifact, request_row, plan)
        ):
            raise ServiceError(
                500,
                "AUDITION_REVIEW_EVIDENCE_MISSING",
                "The audition review evidence is unavailable.",
            )
        assert assignment is not None
        assert rights is not None
        assert verification is not None
        assert artifact is not None
        assert plan is not None
        try:
            blockers, warnings = _validated_audio_quality_evidence(
                quality,
                artifact=artifact,
                clip=clip,
            )
        except ValueError as exc:
            raise ServiceError(
                409,
                "AUDITION_AUDIO_QUALITY_EVIDENCE_INVALID",
                "The audition audio-quality evidence failed verification.",
            ) from exc
        assignment_fingerprint = self._assignment_fingerprint(assignment)
        evidence = {
            "projectId": audition_session.project_id,
            "gateId": "per_role_audition_review",
            "roleId": audition_session.role_id,
            "auditionSessionId": audition_session.id,
            "auditionClipId": clip.id,
            "auditionClipRevision": clip.revision,
            "approvedCastSnapshotFingerprint": audition_session.cast_snapshot_fingerprint,
            "castAssignmentFingerprint": assignment_fingerprint,
            "rightsRecordFingerprint": rights.rights_fingerprint,
            "runtimeProfileFingerprint": audition_session.runtime_profile_fingerprint,
            "modelVerificationFingerprint": verification.verification_fingerprint,
            "pronunciationDictionaryFingerprint": (
                audition_session.pronunciation_dictionary_fingerprint
            ),
            "pronunciationDependencyFingerprint": plan.pronunciation_plan_fingerprint,
            "audioQualityFingerprint": quality.quality_fingerprint,
        }
        evidence_fingerprint = request_fingerprint(evidence)
        evidence["evidenceFingerprint"] = evidence_fingerprint
        existing = session.scalar(
            select(AuditionReviewRecordRow)
            .where(
                AuditionReviewRecordRow.project_id == audition_session.project_id,
                AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                AuditionReviewRecordRow.scope_key == audition_session.role_id,
                AuditionReviewRecordRow.evidence_fingerprint == evidence_fingerprint,
            )
            .limit(1)
        )
        if existing is not None:
            return existing
        latest_revision = int(
            session.scalar(
                select(func.max(AuditionReviewRecordRow.revision)).where(
                    AuditionReviewRecordRow.project_id == audition_session.project_id,
                    AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                    AuditionReviewRecordRow.scope_key == audition_session.role_id,
                )
            )
            or 0
        )
        row = AuditionReviewRecordRow(
            id=new_id(),
            project_id=audition_session.project_id,
            gate_id="per_role_audition_review",
            scope_key=audition_session.role_id,
            subject_type="role",
            revision=latest_revision + 1,
            session_id=audition_session.id,
            clip_id=clip.id,
            role_id=audition_session.role_id,
            pronunciation_dictionary_record_id=None,
            eligible=not blockers,
            evidence_json=canonical_json(evidence),
            evidence_fingerprint=evidence_fingerprint,
            required_decision_ids_json=audition_session.phase3a_gate_decision_ids_json,
            blockers_json=canonical_json(list(blockers)),
            warnings_json=canonical_json(list(warnings)),
            provenance_json=_provenance("application"),
            created_at=utc_now(),
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _assignment_fingerprint(row: CastAssignmentRow) -> str:
        return request_fingerprint(
            {
                "assignmentId": row.id,
                "assignmentState": row.assignment_state,
                "authority": row.authority,
                "catalogRevisionId": row.catalog_revision_id,
                "revision": row.revision,
                "rightsState": row.rights_state,
                "roleId": row.role_id,
                "voiceProfileRecordId": row.voice_profile_record_id,
            }
        )

    def _ensure_pronunciation_review(
        self,
        session: Session,
        project_id: str,
    ) -> AuditionReviewRecordRow | None:
        dictionary = self._current_dictionary(session, project_id)
        authority = self._current_cast_authority(session, project_id)
        audition_session = (
            self._latest_current_audition_session(session, authority)
            if authority is not None
            else None
        )
        if (
            dictionary is None
            or authority is None
            or authority.phase3a_decision_ids is None
            or audition_session is None
            or self._session_dependency_drift(session, audition_session) is not None
        ):
            return None
        required_phase3a_ids_json = canonical_json(list(authority.phase3a_decision_ids))
        current_rows = self._latest_pronunciation_entry_rows(session, project_id)
        blockers = sorted(
            {
                "PRONUNCIATION_ENTRY_REVIEW_REQUIRED"
                for row in current_rows
                if row.verification_state not in {"approved", "rejected", "superseded"}
            }
        )
        verification = session.get(
            ModelVerificationRow,
            audition_session.model_verification_id,
        )
        voice_record_ids = {
            assignment.voice_profile_record_id
            for assignment in authority.assignments_by_role.values()
            if assignment.voice_profile_record_id is not None
        }
        rights_by_voice_record_id: dict[str, VoiceRightsRecordRow] = {}
        for rights in session.scalars(
            select(VoiceRightsRecordRow)
            .where(VoiceRightsRecordRow.voice_profile_record_id.in_(voice_record_ids))
            .order_by(
                VoiceRightsRecordRow.voice_profile_record_id,
                VoiceRightsRecordRow.revision.desc(),
                VoiceRightsRecordRow.id.desc(),
            )
        ):
            rights_by_voice_record_id.setdefault(rights.voice_profile_record_id, rights)
        if verification is None or set(rights_by_voice_record_id) != voice_record_ids:
            return None
        rights_evidence = [
            {
                "roleId": role_id,
                "rightsRecordId": rights.rights_record_id,
                "rightsRecordRevision": rights.revision,
                "rightsRecordFingerprint": rights.rights_fingerprint,
            }
            for role_id, assignment in sorted(authority.assignments_by_role.items())
            if assignment.voice_profile_record_id is not None
            for rights in [rights_by_voice_record_id[assignment.voice_profile_record_id]]
        ]
        evidence = {
            "projectId": project_id,
            "gateId": "pronunciation_review",
            "roleId": None,
            "auditionSessionId": None,
            "auditionClipId": None,
            "auditionClipRevision": None,
            "approvedCastSnapshotFingerprint": audition_session.cast_snapshot_fingerprint,
            "castAssignmentFingerprint": None,
            "rightsRecordFingerprint": request_fingerprint(rights_evidence),
            "runtimeProfileFingerprint": audition_session.runtime_profile_fingerprint,
            "modelVerificationFingerprint": verification.verification_fingerprint,
            "pronunciationDictionaryFingerprint": dictionary.dictionary_fingerprint,
            "pronunciationDependencyFingerprint": request_fingerprint(
                {
                    "activeEntryIds": parse_json(dictionary.active_entry_ids_json, []),
                    "dictionaryFingerprint": dictionary.dictionary_fingerprint,
                    "phase3aGateDecisionIds": list(authority.phase3a_decision_ids),
                }
            ),
            "audioQualityFingerprint": None,
        }
        evidence_fingerprint = request_fingerprint(evidence)
        evidence["evidenceFingerprint"] = evidence_fingerprint
        existing = session.scalar(
            select(AuditionReviewRecordRow).where(
                AuditionReviewRecordRow.project_id == project_id,
                AuditionReviewRecordRow.gate_id == "pronunciation_review",
                AuditionReviewRecordRow.scope_key == dictionary.dictionary_id,
                AuditionReviewRecordRow.evidence_fingerprint == evidence_fingerprint,
                AuditionReviewRecordRow.required_decision_ids_json == required_phase3a_ids_json,
            )
        )
        if existing is not None:
            return existing
        revision = 1 + int(
            session.scalar(
                select(func.max(AuditionReviewRecordRow.revision)).where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == "pronunciation_review",
                    AuditionReviewRecordRow.scope_key == dictionary.dictionary_id,
                )
            )
            or 0
        )
        row = AuditionReviewRecordRow(
            id=new_id(),
            project_id=project_id,
            gate_id="pronunciation_review",
            scope_key=dictionary.dictionary_id,
            subject_type="pronunciation_dictionary",
            revision=revision,
            session_id=None,
            clip_id=None,
            role_id=None,
            pronunciation_dictionary_record_id=dictionary.id,
            eligible=not blockers,
            evidence_json=canonical_json(evidence),
            evidence_fingerprint=evidence_fingerprint,
            required_decision_ids_json=required_phase3a_ids_json,
            blockers_json=canonical_json(blockers),
            warnings_json="[]",
            provenance_json=_provenance("application"),
            created_at=utc_now(),
        )
        session.add(row)
        session.flush()
        return row

    def _ensure_aggregate_review(
        self,
        session: Session,
        *,
        project_id: str,
        narrator: bool,
    ) -> AuditionReviewRecordRow | None:
        authority = self._current_cast_authority(session, project_id)
        if authority is None or authority.phase3a_decision_ids is None:
            return None
        latest_session = self._latest_current_audition_session(session, authority)
        if latest_session is None:
            return None
        role_statement = select(ProductionRoleRow).where(
            ProductionRoleRow.project_id == project_id,
            ProductionRoleRow.casting_run_id == authority.casting_run.id,
            ProductionRoleRow.status.in_(("active", "unresolved")),
            ProductionRoleRow.id.in_(authority.assignments_by_role),
        )
        roles = list(
            session.scalars(
                role_statement.order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        )
        narrator_types = {"primary_narrator", "secondary_narrator"}
        scoped_roles = [role for role in roles if (role.role_type in narrator_types) == narrator]
        gate_id = "narrator_audition_review" if narrator else "character_audition_review"
        subject_type = "narrator_scope" if narrator else "character_scope"
        scope_key = "aggregate:narrator" if narrator else "aggregate:character"
        approved_decisions: list[AuditionReviewDecisionRow] = []
        accepted_sessions: list[AuditionSessionRow] = []
        blockers: list[str] = []
        evidence_parts: list[dict[str, str]] = []
        for role in scoped_roles:
            current = self._approved_current_role_audition_evidence(
                session,
                authority=authority,
                role_id=role.id,
            )
            if current is None:
                blockers.append(f"ROLE_AUDITION_APPROVAL_REQUIRED:{role.id}")
            else:
                role_session = current.audition_session
                clip = current.clip
                review = current.review
                decision = current.decision
                assert role_session is not None
                assert clip is not None
                assert review is not None
                assert decision is not None
                approved_decisions.append(decision)
                accepted_sessions.append(role_session)
                evidence_parts.append(
                    {
                        "auditionClipId": clip.id,
                        "auditionSessionId": role_session.id,
                        "decisionId": decision.id,
                        "evidenceFingerprint": decision.evidence_fingerprint,
                        "roleId": role.id,
                    }
                )
        verification = session.get(
            ModelVerificationRow,
            latest_session.model_verification_id,
        )
        rights_rows = [
            rights
            for accepted_session in accepted_sessions
            if (
                rights := session.get(
                    VoiceRightsRecordRow,
                    accepted_session.rights_record_id,
                )
            )
            is not None
        ]
        if verification is None:
            return None
        evidence = {
            "projectId": project_id,
            "gateId": gate_id,
            "roleId": None,
            "auditionSessionId": None,
            "auditionClipId": None,
            "auditionClipRevision": None,
            "approvedCastSnapshotFingerprint": authority.cast_snapshot.snapshot_fingerprint,
            "castAssignmentFingerprint": request_fingerprint(evidence_parts),
            "rightsRecordFingerprint": request_fingerprint(
                sorted(row.rights_fingerprint for row in rights_rows)
            ),
            "runtimeProfileFingerprint": latest_session.runtime_profile_fingerprint,
            "modelVerificationFingerprint": verification.verification_fingerprint,
            "pronunciationDictionaryFingerprint": (
                latest_session.pronunciation_dictionary_fingerprint
            ),
            "pronunciationDependencyFingerprint": request_fingerprint(evidence_parts),
            "audioQualityFingerprint": request_fingerprint(evidence_parts),
        }
        evidence_fingerprint = request_fingerprint(evidence)
        evidence["evidenceFingerprint"] = evidence_fingerprint
        existing = session.scalar(
            select(AuditionReviewRecordRow).where(
                AuditionReviewRecordRow.project_id == project_id,
                AuditionReviewRecordRow.gate_id == gate_id,
                AuditionReviewRecordRow.scope_key == scope_key,
                AuditionReviewRecordRow.evidence_fingerprint == evidence_fingerprint,
            )
        )
        if existing is not None:
            return existing
        revision = 1 + int(
            session.scalar(
                select(func.max(AuditionReviewRecordRow.revision)).where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == gate_id,
                    AuditionReviewRecordRow.scope_key == scope_key,
                )
            )
            or 0
        )
        row = AuditionReviewRecordRow(
            id=new_id(),
            project_id=project_id,
            gate_id=gate_id,
            scope_key=scope_key,
            subject_type=subject_type,
            revision=revision,
            session_id=None,
            clip_id=None,
            role_id=None,
            pronunciation_dictionary_record_id=None,
            eligible=not blockers and bool(scoped_roles),
            evidence_json=canonical_json(evidence),
            evidence_fingerprint=evidence_fingerprint,
            required_decision_ids_json=canonical_json(
                [decision.id for decision in approved_decisions]
            ),
            blockers_json=canonical_json(blockers),
            warnings_json="[]",
            provenance_json=_provenance("application"),
            created_at=utc_now(),
        )
        session.add(row)
        session.flush()
        return row

    def _refresh_reviews(
        self,
        session: Session,
        project_id: str,
    ) -> tuple[
        AuditionReviewRecordRow | None,
        AuditionReviewRecordRow | None,
        AuditionReviewRecordRow | None,
        VoiceReadinessSnapshotRow | None,
    ]:
        pronunciation = self._ensure_pronunciation_review(session, project_id)
        narrator = self._ensure_aggregate_review(
            session,
            project_id=project_id,
            narrator=True,
        )
        character = self._ensure_aggregate_review(
            session,
            project_id=project_id,
            narrator=False,
        )
        readiness = self._ensure_voice_readiness(session, project_id)
        return pronunciation, narrator, character, readiness

    def _assert_per_role_review_clip_integrity(
        self,
        *,
        project_id: str,
        review_id: str,
    ) -> None:
        failure_code: str | None = None
        with self.database.immediate_session() as session:
            review = session.get(AuditionReviewRecordRow, review_id)
            if (
                review is None
                or review.project_id != project_id
                or review.gate_id != "per_role_audition_review"
            ):
                return
            clip = (
                session.get(AuditionClipRow, review.clip_id) if review.clip_id is not None else None
            )
            audition_session = (
                session.get(AuditionSessionRow, review.session_id)
                if review.session_id is not None
                else None
            )
            evidence = parse_json(review.evidence_json, {})
            fingerprint_material = (
                {
                    key: value
                    for key, value in evidence.items()
                    if key != "evidenceFingerprint"
                }
                if isinstance(evidence, dict)
                else None
            )
            quality = (
                session.scalar(
                    select(AudioQualityRecordRow)
                    .where(AudioQualityRecordRow.clip_id == clip.id)
                    .order_by(
                        AudioQualityRecordRow.revision.desc(),
                        AudioQualityRecordRow.id.desc(),
                    )
                    .limit(1)
                )
                if clip is not None
                else None
            )
            binding_ok = (
                clip is not None
                and audition_session is not None
                and clip.project_id == project_id
                and audition_session.project_id == project_id
                and audition_session.role_id == review.role_id
                and audition_session.id == clip.session_id
                and review.scope_key == review.role_id
                and clip.session_id == review.session_id
                and clip.role_id == review.role_id
                and isinstance(evidence, dict)
                and isinstance(fingerprint_material, dict)
                and evidence.get("evidenceFingerprint") == review.evidence_fingerprint
                and request_fingerprint(fingerprint_material) == review.evidence_fingerprint
                and evidence.get("auditionSessionId") == clip.session_id
                and evidence.get("auditionClipId") == clip.id
                and evidence.get("auditionClipRevision") == clip.revision
                and evidence.get("roleId") == clip.role_id
                and review.required_decision_ids_json
                == audition_session.phase3a_gate_decision_ids_json
                and quality is not None
                and quality.project_id == project_id
                and evidence.get("audioQualityFingerprint") == quality.quality_fingerprint
            )
            integrity_ok = False
            previous_fingerprint = review.evidence_fingerprint
            current_fingerprint = request_fingerprint(
                {
                    "clipId": review.clip_id,
                    "reviewId": review.id,
                    "state": "binding_changed",
                }
            )
            if binding_ok and clip is not None:
                integrity_ok, previous_fingerprint, current_fingerprint = (
                    self._clip_integrity_fingerprints(session, clip)
                )
            if binding_ok and integrity_ok:
                return

            if clip is not None and clip.project_id == project_id:
                artifact = session.get(AudioArtifactRow, clip.artifact_id)
                cache = session.get(AuditionCacheRecordRow, clip.cache_record_id)
                if binding_ok:
                    if artifact is not None and artifact.availability == "present":
                        artifact.availability = "corrupt"
                    if cache is not None and cache.state == "verified":
                        cache.state = "corrupt"
                        cache.last_verified_at = utc_now()
                self._invalidate_clip_dependencies(
                    session,
                    project_id=project_id,
                    clips=[clip],
                    source_kind=("audio_integrity" if binding_ok else "review_clip_binding"),
                    source_record_id=clip.artifact_id if binding_ok else review.id,
                    previous_fingerprint=previous_fingerprint,
                    current_fingerprint=current_fingerprint,
                    reason_code=(
                        "AUDITION_AUDIO_INTEGRITY_CHANGED"
                        if binding_ok
                        else "AUDITION_REVIEW_CLIP_CHANGED"
                    ),
                    rationale=(
                        "Audition audio failed current integrity checks before review."
                        if binding_ok
                        else "The audition review no longer identifies its exact clip evidence."
                    ),
                )
            else:
                self._append_audition_review_invalidation(
                    session,
                    project_id=project_id,
                    review=review,
                    rationale="The audition review clip is unavailable.",
                )
                self._append_voice_readiness_invalidation(
                    session,
                    project_id=project_id,
                    rationale="The audition review clip is unavailable.",
                )
            failure_code = "AUDITION_AUDIO_CHANGED" if binding_ok else "AUDITION_REVIEW_CHANGED"
        if failure_code is not None:
            raise ServiceError(
                409,
                failure_code,
                "The audition clip evidence changed; refresh before deciding.",
            )

    @staticmethod
    def _record_review_response_projection(
        decision: AuditionReviewDecisionRow | VoiceReadinessDecisionRow,
        *,
        review_id: str,
        readiness_snapshot_id: str | None,
    ) -> None:
        projection = {
            "decisionId": decision.id,
            "projectId": decision.project_id,
            "reviewId": review_id,
            "voiceReadinessSnapshotId": readiness_snapshot_id,
        }
        provenance = parse_json(decision.provenance_json, {})
        if not isinstance(provenance, dict):
            provenance = {}
        details = provenance.get("details")
        if not isinstance(details, dict):
            details = {}
        details["idempotentResponseProjection"] = projection
        details["idempotentResponseProjectionFingerprint"] = request_fingerprint(projection)
        provenance["details"] = details
        decision.provenance_json = canonical_json(provenance)

    @staticmethod
    def _review_response_snapshot(
        session: Session,
        *,
        decision: AuditionReviewDecisionRow | VoiceReadinessDecisionRow,
        review_id: str,
        expected_request_hash: str,
        require_snapshot_id: str | None = None,
    ) -> VoiceReadinessSnapshotRow | None:
        provenance = parse_json(decision.provenance_json, {})
        details = provenance.get("details") if isinstance(provenance, dict) else None
        projection = (
            details.get("idempotentResponseProjection") if isinstance(details, dict) else None
        )
        projection_fingerprint = (
            details.get("idempotentResponseProjectionFingerprint")
            if isinstance(details, dict)
            else None
        )
        if (
            not isinstance(provenance, dict)
            or provenance.get("inputFingerprint") != expected_request_hash
        ):
            raise ServiceError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "That idempotency key was already used for another review decision.",
            )
        expected_keys = {
            "decisionId",
            "projectId",
            "reviewId",
            "voiceReadinessSnapshotId",
        }
        if (
            not isinstance(projection, dict)
            or set(projection) != expected_keys
            or projection.get("decisionId") != decision.id
            or projection.get("projectId") != decision.project_id
            or projection.get("reviewId") != review_id
            or projection_fingerprint != request_fingerprint(projection)
        ):
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved review-decision response failed verification.",
            )
        snapshot_id = projection.get("voiceReadinessSnapshotId")
        if snapshot_id is not None and not isinstance(snapshot_id, str):
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved review-decision response failed verification.",
            )
        if require_snapshot_id is not None and snapshot_id != require_snapshot_id:
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved readiness-decision response failed verification.",
            )
        if snapshot_id is None:
            return None
        snapshot = session.get(VoiceReadinessSnapshotRow, snapshot_id)
        if snapshot is None or snapshot.project_id != decision.project_id:
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved review-decision readiness snapshot is unavailable.",
            )
        return snapshot

    def _audition_review_response(
        self,
        session: Session,
        *,
        review: AuditionReviewRecordRow,
        decision: AuditionReviewDecisionRow,
        expected_request_hash: str,
    ) -> dict[str, Any]:
        readiness_snapshot = self._review_response_snapshot(
            session,
            decision=decision,
            review_id=review.id,
            expected_request_hash=expected_request_hash,
        )
        if (
            decision.review_record_id != review.id
            or decision.project_id != review.project_id
            or decision.gate_id != review.gate_id
            or decision.scope_key != review.scope_key
            or decision.evidence_fingerprint != review.evidence_fingerprint
        ):
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved review decision does not match its evidence.",
            )
        return {
            "review": self._review_wire(
                session,
                review,
                decision_override=decision,
            ),
            "decision": self._review_decision_wire(session, review, decision),
            "voiceReadinessSnapshot": (
                self._readiness_snapshot_wire(session, readiness_snapshot)
                if readiness_snapshot is not None
                else None
            ),
        }

    def _audition_review_replay_response(
        self,
        *,
        project_id: str,
        gate_id: str,
        review_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        with self.database.session() as session:
            self._require_project(session, project_id)
            review = session.get(AuditionReviewRecordRow, review_id)
            if review is None or review.project_id != project_id or review.gate_id != gate_id:
                raise not_found("audition review")
            replay = session.scalar(
                select(AuditionReviewDecisionRow).where(
                    AuditionReviewDecisionRow.project_id == project_id,
                    AuditionReviewDecisionRow.gate_id == gate_id,
                    AuditionReviewDecisionRow.scope_key == review.scope_key,
                    AuditionReviewDecisionRow.idempotency_key == idempotency_key,
                )
            )
            if replay is None:
                return None
            return self._audition_review_response(
                session,
                review=review,
                decision=replay,
                expected_request_hash=request_hash,
            )

    @staticmethod
    def _stored_listening_attestation(
        decision: AuditionReviewDecisionRow,
    ) -> dict[str, Any] | None:
        provenance = parse_json(decision.provenance_json, None)
        details = provenance.get("details") if isinstance(provenance, dict) else None
        value = details.get("listeningAttestation") if isinstance(details, dict) else None
        if value is None:
            return None
        allowed_dispositions = {
            "approved": frozenset({"acceptable"}),
            "changes_requested": frozenset({"needs_changes", "undecided"}),
            "rejected": frozenset({"unacceptable"}),
        }.get(decision.decision, frozenset())
        actor = value.get("actor") if isinstance(value, dict) else None
        expected_keys = {
            "attestationId",
            "auditionClipId",
            "auditionClipRevision",
            "auditionClipFingerprint",
            "audioArtifactId",
            "audioArtifactSha256",
            "listened",
            "disposition",
            "actor",
            "recordedAt",
            "rationale",
            "attestationFingerprint",
            "immutable",
        }
        fingerprint_material = (
            {key: item for key, item in value.items() if key != "attestationFingerprint"}
            if isinstance(value, dict)
            else None
        )
        if (
            not isinstance(value, dict)
            or set(value) != expected_keys
            or not isinstance(value.get("attestationId"), str)
            or not isinstance(value.get("auditionClipId"), str)
            or not isinstance(value.get("auditionClipRevision"), int)
            or value.get("auditionClipRevision", 0) < 1
            or not isinstance(value.get("auditionClipFingerprint"), str)
            or not isinstance(value.get("audioArtifactId"), str)
            or not isinstance(value.get("audioArtifactSha256"), str)
            or value.get("listened") is not True
            or value.get("disposition") not in allowed_dispositions
            or not isinstance(actor, dict)
            or actor
            != {
                "classification": "human",
                "actorId": decision.actor_id,
            }
            or value.get("recordedAt") != decision.decided_at
            or value.get("rationale") != decision.rationale
            or value.get("immutable") is not True
            or not isinstance(fingerprint_material, dict)
            or value.get("attestationFingerprint") != request_fingerprint(fingerprint_material)
        ):
            raise ServiceError(
                500,
                "AUDITION_LISTENING_ATTESTATION_INVALID",
                "Stored human-listening evidence failed integrity validation.",
            )
        return deepcopy(value)

    def _build_listening_attestation(
        self,
        session: Session,
        *,
        review: AuditionReviewRecordRow,
        request: DecideAuditionReviewRequest,
        actor_id: str,
        decision_value: str,
        recorded_at: str,
    ) -> dict[str, Any] | None:
        request_attestation = request.listening_attestation
        audition_session = (
            session.get(AuditionSessionRow, review.session_id)
            if review.session_id is not None
            else None
        )
        real_per_role = (
            review.gate_id == "per_role_audition_review"
            and audition_session is not None
            and audition_session.provider_id == KOKORO_PROVIDER_ID
        )
        if not real_per_role:
            if request_attestation is not None:
                raise ServiceError(
                    422,
                    "AUDITION_LISTENING_ATTESTATION_FORBIDDEN",
                    "Listening evidence is accepted only for real-provider per-role reviews.",
                )
            return None
        assert audition_session is not None
        if request_attestation is None:
            raise ServiceError(
                409,
                "AUDITION_LISTENING_ATTESTATION_REQUIRED",
                "A current human-listening attestation is required for this real audition.",
            )
        if review.clip_id is None:
            raise ServiceError(
                409,
                "AUDITION_LISTENING_ATTESTATION_CHANGED",
                "The review no longer identifies an audition clip.",
            )
        clip = session.get(AuditionClipRow, review.clip_id)
        artifact = session.get(AudioArtifactRow, clip.artifact_id) if clip is not None else None
        allowed_dispositions = {
            "approved": frozenset({"acceptable"}),
            "changes_requested": frozenset({"needs_changes", "undecided"}),
            "rejected": frozenset({"unacceptable"}),
        }[decision_value]
        if (
            clip is None
            or artifact is None
            or clip.session_id != audition_session.id
            or clip.project_id != review.project_id
            or clip.role_id != review.role_id
            or artifact.project_id != review.project_id
            or artifact.availability != "present"
            or request_attestation.audition_clip_id != clip.id
            or request_attestation.audition_clip_revision != clip.revision
            or request_attestation.audition_clip_fingerprint != clip.clip_fingerprint
            or request_attestation.audio_artifact_id != artifact.id
            or request_attestation.audio_artifact_sha256 != artifact.content_sha256
            or request_attestation.disposition not in allowed_dispositions
        ):
            raise ServiceError(
                409,
                "AUDITION_LISTENING_ATTESTATION_CHANGED",
                "The human-listening evidence is stale or does not match the decision.",
            )
        if self._governed_local_voice_activation_wire(session, audition_session) is None:
            raise ServiceError(
                409,
                "AUDITION_ACTIVATION_EVIDENCE_INVALID",
                "The governed real-voice activation is unavailable.",
            )
        value: dict[str, Any] = {
            "attestationId": new_id(),
            "auditionClipId": clip.id,
            "auditionClipRevision": clip.revision,
            "auditionClipFingerprint": clip.clip_fingerprint,
            "audioArtifactId": artifact.id,
            "audioArtifactSha256": artifact.content_sha256,
            "listened": True,
            "disposition": request_attestation.disposition,
            "actor": {"classification": "human", "actorId": actor_id},
            "recordedAt": recorded_at,
            "rationale": request.rationale,
            "immutable": True,
        }
        value["attestationFingerprint"] = request_fingerprint(value)
        return value

    def _current_listening_attestation_is_valid(
        self,
        session: Session,
        *,
        review: AuditionReviewRecordRow,
        decision: AuditionReviewDecisionRow,
        audition_session: AuditionSessionRow,
        clip: AuditionClipRow,
    ) -> bool:
        try:
            value = self._stored_listening_attestation(decision)
            artifact = session.get(AudioArtifactRow, clip.artifact_id)
            quality = session.scalar(
                select(AudioQualityRecordRow)
                .where(AudioQualityRecordRow.clip_id == clip.id)
                .order_by(
                    AudioQualityRecordRow.revision.desc(),
                    AudioQualityRecordRow.id.desc(),
                )
                .limit(1)
            )
            review_evidence = parse_json(review.evidence_json, None)
            review_fingerprint_material = (
                {
                    key: item
                    for key, item in review_evidence.items()
                    if key != "evidenceFingerprint"
                }
                if isinstance(review_evidence, dict)
                else None
            )
            return (
                value is not None
                and review.gate_id == "per_role_audition_review"
                and review.project_id == audition_session.project_id
                and review.project_id == clip.project_id
                and review.scope_key == review.role_id
                and review.role_id == audition_session.role_id
                and review.role_id == clip.role_id
                and review.session_id == audition_session.id
                and review.clip_id == clip.id
                and review.required_decision_ids_json
                == audition_session.phase3a_gate_decision_ids_json
                and audition_session.provider_id == KOKORO_PROVIDER_ID
                and clip.project_id == audition_session.project_id
                and clip.session_id == audition_session.id
                and decision.project_id == review.project_id
                and decision.review_record_id == review.id
                and decision.gate_id == review.gate_id
                and decision.scope_key == review.scope_key
                and decision.evidence_fingerprint == review.evidence_fingerprint
                and decision.actor_classification == "human"
                and isinstance(review_evidence, dict)
                and isinstance(review_fingerprint_material, dict)
                and review_evidence.get("evidenceFingerprint")
                == review.evidence_fingerprint
                and request_fingerprint(review_fingerprint_material)
                == review.evidence_fingerprint
                and review_evidence.get("auditionSessionId") == audition_session.id
                and review_evidence.get("auditionClipId") == clip.id
                and review_evidence.get("auditionClipRevision") == clip.revision
                and review_evidence.get("roleId") == clip.role_id
                and quality is not None
                and quality.project_id == review.project_id
                and quality.clip_id == clip.id
                and quality.artifact_id == clip.artifact_id
                and review_evidence.get("audioQualityFingerprint")
                == quality.quality_fingerprint
                and artifact is not None
                and artifact.project_id == review.project_id
                and artifact.availability == "present"
                and value["auditionClipId"] == clip.id
                and value["auditionClipRevision"] == clip.revision
                and value["auditionClipFingerprint"] == clip.clip_fingerprint
                and value["audioArtifactId"] == artifact.id
                and value["audioArtifactSha256"] == artifact.content_sha256
                and self._governed_local_voice_activation_wire(session, audition_session)
                is not None
            )
        except ServiceError:
            return False

    def decide_review(
        self,
        *,
        project_id: str,
        gate_id: str,
        review_id: str,
        request: DecideAuditionReviewRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        if gate_id not in _AUDITION_GATE_IDS:
            raise not_found("audition review")
        request_hash = request_fingerprint(
            request.model_dump(mode="json", by_alias=True)
            | {"gateId": gate_id, "projectId": project_id, "reviewId": review_id}
        )
        if gate_id == "voice_readiness_review":
            if request.listening_attestation is not None:
                raise ServiceError(
                    422,
                    "AUDITION_LISTENING_ATTESTATION_FORBIDDEN",
                    "The aggregate readiness review does not accept clip-listening evidence.",
                )
            with self._review_decision_lock:
                return self._decide_voice_readiness(
                    project_id=project_id,
                    review_id=review_id,
                    request=request,
                    actor_id=actor_id,
                    request_hash=request_hash,
                )
        with self._review_decision_lock:
            replay_response = self._audition_review_replay_response(
                project_id=project_id,
                gate_id=gate_id,
                review_id=review_id,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
            )
            if replay_response is not None:
                return replay_response
            if gate_id == "per_role_audition_review":
                self._assert_per_role_review_clip_integrity(
                    project_id=project_id,
                    review_id=review_id,
                )
            with self.database.immediate_session() as session:
                self._require_project(session, project_id)
                review = session.get(AuditionReviewRecordRow, review_id)
                if review is None or review.project_id != project_id or review.gate_id != gate_id:
                    raise not_found("audition review")
                replay = session.scalar(
                    select(AuditionReviewDecisionRow).where(
                        AuditionReviewDecisionRow.project_id == project_id,
                        AuditionReviewDecisionRow.gate_id == gate_id,
                        AuditionReviewDecisionRow.scope_key == review.scope_key,
                        AuditionReviewDecisionRow.idempotency_key == request.idempotency_key,
                    )
                )
                if replay is not None:
                    return self._audition_review_response(
                        session,
                        review=review,
                        decision=replay,
                        expected_request_hash=request_hash,
                    )
                self._reconcile_project_evidence(session, project_id)
                (
                    pronunciation_review,
                    narrator_review,
                    character_review,
                    _readiness_snapshot,
                ) = self._refresh_reviews(session, project_id)
                if gate_id == "per_role_audition_review":
                    authority = self._current_cast_authority(session, project_id)
                    current_role_evidence = (
                        self._current_role_audition_evidence(
                            session,
                            authority=authority,
                            role_id=review.role_id,
                        )
                        if authority is not None and review.role_id is not None
                        else None
                    )
                    latest_review = (
                        current_role_evidence.review if current_role_evidence is not None else None
                    )
                else:
                    latest_review = {
                        "narrator_audition_review": narrator_review,
                        "character_audition_review": character_review,
                        "pronunciation_review": pronunciation_review,
                    }.get(gate_id)
                review_session = (
                    session.get(AuditionSessionRow, review.session_id)
                    if review.session_id is not None
                    else None
                )
                real_per_role_review = (
                    gate_id == "per_role_audition_review"
                    and review_session is not None
                    and review_session.provider_id == KOKORO_PROVIDER_ID
                )
                historical_real_per_role_review = (
                    real_per_role_review
                    and latest_review is not None
                    and latest_review.id != review.id
                    and review.revision < latest_review.revision
                )
                if latest_review is None or (
                    latest_review.id != review.id and not historical_real_per_role_review
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_REVIEW_CHANGED",
                        "The audition review is no longer current.",
                    )
                if review.session_id is not None:
                    if review_session is None:
                        raise ServiceError(
                            409,
                            "AUDITION_REVIEW_CHANGED",
                            "The audition review session is unavailable.",
                        )
                    if real_per_role_review and review_session.state != "reviewable":
                        raise ServiceError(
                            409,
                            "AUDITION_REVIEW_CHANGED",
                            "The real audition review session is no longer reviewable.",
                        )
                    self._validate_session_evidence(
                        session,
                        project_id=project_id,
                        role_id=review_session.role_id,
                        evidence=self._session_evidence_wire(session, review_session),
                    )
                if (
                    review.revision != request.expected_review_revision
                    or review.evidence_fingerprint != request.expected_evidence_fingerprint
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_REVIEW_CHANGED",
                        "The audition review changed; refresh before deciding.",
                    )
                latest_decision = session.scalar(
                    select(AuditionReviewDecisionRow)
                    .where(
                        AuditionReviewDecisionRow.project_id == project_id,
                        AuditionReviewDecisionRow.gate_id == gate_id,
                        AuditionReviewDecisionRow.scope_key == review.scope_key,
                    )
                    .order_by(
                        AuditionReviewDecisionRow.revision.desc(),
                        AuditionReviewDecisionRow.id.desc(),
                    )
                    .limit(1)
                )
                if request.supersedes_decision_id != (
                    latest_decision.id if latest_decision is not None else None
                ):
                    raise ServiceError(
                        409,
                        "AUDITION_REVIEW_DECISION_CHANGED",
                        "The latest review decision changed; refresh before deciding.",
                    )
                if real_per_role_review:
                    prior_human_listening_rows = session.execute(
                        select(AuditionReviewRecordRow, AuditionReviewDecisionRow)
                        .join(
                            AuditionReviewDecisionRow,
                            AuditionReviewDecisionRow.review_record_id
                            == AuditionReviewRecordRow.id,
                        )
                        .join(
                            AuditionSessionRow,
                            AuditionSessionRow.id == AuditionReviewRecordRow.session_id,
                        )
                        .where(
                            AuditionReviewRecordRow.project_id == project_id,
                            AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                            AuditionReviewRecordRow.scope_key == review.scope_key,
                            AuditionReviewDecisionRow.project_id == project_id,
                            AuditionReviewDecisionRow.gate_id
                            == "per_role_audition_review",
                            AuditionReviewDecisionRow.scope_key == review.scope_key,
                            AuditionReviewDecisionRow.actor_classification == "human",
                            AuditionSessionRow.provider_id == KOKORO_PROVIDER_ID,
                        )
                    ).all()
                    latest_human_listening_review_revision: int | None = None
                    for decided_review, decided_decision in prior_human_listening_rows:
                        decided_session = (
                            session.get(AuditionSessionRow, decided_review.session_id)
                            if decided_review.session_id is not None
                            else None
                        )
                        decided_clip = (
                            session.get(AuditionClipRow, decided_review.clip_id)
                            if decided_review.clip_id is not None
                            else None
                        )
                        if (
                            decided_session is None
                            or decided_clip is None
                            or not self._current_listening_attestation_is_valid(
                                session,
                                review=decided_review,
                                decision=decided_decision,
                                audition_session=decided_session,
                                clip=decided_clip,
                            )
                        ):
                            raise ServiceError(
                                500,
                                "AUDITION_LISTENING_ATTESTATION_INVALID",
                                "Stored real-provider review evidence has no exact listening "
                                "attestation.",
                            )
                        latest_human_listening_review_revision = max(
                            latest_human_listening_review_revision or 0,
                            decided_review.revision,
                        )
                    if (
                        latest_human_listening_review_revision is not None
                        and review.revision < latest_human_listening_review_revision
                    ):
                        raise ServiceError(
                            409,
                            "AUDITION_REVIEW_SEQUENCE_CHANGED",
                            "A newer real audition review already has a human decision.",
                        )
                if request.decision == "approve" and not review.eligible:
                    raise ServiceError(
                        409,
                        "AUDITION_REVIEW_BLOCKED",
                        "Blocked audition evidence cannot be approved.",
                    )
                decision_value = {
                    "approve": "approved",
                    "request_changes": "changes_requested",
                    "reject": "rejected",
                }[request.decision]
                now = utc_now()
                listening_attestation = self._build_listening_attestation(
                    session,
                    review=review,
                    request=request,
                    actor_id=actor_id,
                    decision_value=decision_value,
                    recorded_at=now,
                )
                decision = AuditionReviewDecisionRow(
                    id=new_id(),
                    project_id=project_id,
                    review_record_id=review.id,
                    gate_id=gate_id,
                    scope_key=review.scope_key,
                    revision=(latest_decision.revision + 1) if latest_decision else 1,
                    decision=decision_value,
                    evidence_fingerprint=review.evidence_fingerprint,
                    actor_classification="human",
                    actor_id=actor_id,
                    warning_acknowledgements_json="[]",
                    rationale=request.rationale,
                    supersedes_decision_id=(
                        latest_decision.id if latest_decision is not None else None
                    ),
                    idempotency_key=request.idempotency_key,
                    provenance_json=_provenance(
                        "human",
                        input_fingerprint=request_hash,
                        details=(
                            {"listeningAttestation": listening_attestation}
                            if listening_attestation is not None
                            else None
                        ),
                    ),
                    decided_at=now,
                    created_at=now,
                )
                session.add(decision)
                session.flush()
                _pronunciation, _narrator, _character, readiness_snapshot = self._refresh_reviews(
                    session, project_id
                )
                self._record_review_response_projection(
                    decision,
                    review_id=review.id,
                    readiness_snapshot_id=(
                        readiness_snapshot.id if readiness_snapshot is not None else None
                    ),
                )
                return self._audition_review_response(
                    session,
                    review=review,
                    decision=decision,
                    expected_request_hash=request_hash,
                )

    def list_review_decisions(
        self,
        *,
        project_id: str,
        gate_id: str,
        role_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        """Return immutable decision history across superseded review evidence."""

        page_size = _bounded_page(limit)
        if gate_id not in _AUDITION_GATE_IDS:
            raise ServiceError(
                422,
                "AUDITION_REVIEW_HISTORY_SCOPE_INVALID",
                "The audition review history scope is invalid.",
            )
        if (gate_id == "per_role_audition_review") != (role_id is not None):
            raise ServiceError(
                422,
                "AUDITION_REVIEW_HISTORY_SCOPE_INVALID",
                "Per-role history requires one role and aggregate history forbids it.",
            )
        binding = request_fingerprint(
            {
                "gateId": gate_id,
                "projectId": project_id,
                "roleId": role_id,
                "type": "audition-review-decision-history",
            }
        )
        with self.database.session() as session:
            self._require_project(session, project_id)
            items: list[dict[str, Any]]
            latest_decision_id: str | None
            if gate_id == "voice_readiness_review":
                readiness_predicates = (
                    VoiceReadinessDecisionRow.project_id == project_id,
                    VoiceReadinessDecisionRow.gate_id == gate_id,
                    VoiceReadinessReviewRow.project_id == project_id,
                    VoiceReadinessReviewRow.gate_id == gate_id,
                )
                total = int(
                    session.scalar(
                        select(func.count())
                        .select_from(VoiceReadinessDecisionRow)
                        .join(
                            VoiceReadinessReviewRow,
                            VoiceReadinessReviewRow.id == VoiceReadinessDecisionRow.review_id,
                        )
                        .where(*readiness_predicates)
                    )
                    or 0
                )
                latest_decision_id = session.scalar(
                    select(VoiceReadinessDecisionRow.id)
                    .join(
                        VoiceReadinessReviewRow,
                        VoiceReadinessReviewRow.id == VoiceReadinessDecisionRow.review_id,
                    )
                    .where(*readiness_predicates)
                    .order_by(
                        VoiceReadinessDecisionRow.created_at.desc(),
                        VoiceReadinessDecisionRow.revision.desc(),
                        VoiceReadinessDecisionRow.id.desc(),
                    )
                    .limit(1)
                )
                offset = _decode_review_history_cursor(
                    cursor,
                    binding=binding,
                    latest_decision_id=latest_decision_id,
                    total=total,
                )
                readiness_rows = session.execute(
                    select(VoiceReadinessDecisionRow, VoiceReadinessReviewRow)
                    .join(
                        VoiceReadinessReviewRow,
                        VoiceReadinessReviewRow.id == VoiceReadinessDecisionRow.review_id,
                    )
                    .where(*readiness_predicates)
                    .order_by(
                        VoiceReadinessDecisionRow.created_at.desc(),
                        VoiceReadinessDecisionRow.revision.desc(),
                        VoiceReadinessDecisionRow.id.desc(),
                    )
                    .offset(offset)
                    .limit(page_size)
                ).all()
                items = [
                    self._readiness_decision_wire(review, decision)
                    for decision, review in readiness_rows
                ]
            else:
                role_predicate = (
                    AuditionReviewRecordRow.role_id == role_id
                    if role_id is not None
                    else AuditionReviewRecordRow.role_id.is_(None)
                )
                review_predicates = (
                    AuditionReviewDecisionRow.project_id == project_id,
                    AuditionReviewDecisionRow.gate_id == gate_id,
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == gate_id,
                    role_predicate,
                )
                total = int(
                    session.scalar(
                        select(func.count())
                        .select_from(AuditionReviewDecisionRow)
                        .join(
                            AuditionReviewRecordRow,
                            AuditionReviewRecordRow.id
                            == AuditionReviewDecisionRow.review_record_id,
                        )
                        .where(*review_predicates)
                    )
                    or 0
                )
                latest_decision_id = session.scalar(
                    select(AuditionReviewDecisionRow.id)
                    .join(
                        AuditionReviewRecordRow,
                        AuditionReviewRecordRow.id == AuditionReviewDecisionRow.review_record_id,
                    )
                    .where(*review_predicates)
                    .order_by(
                        AuditionReviewDecisionRow.created_at.desc(),
                        AuditionReviewDecisionRow.revision.desc(),
                        AuditionReviewDecisionRow.id.desc(),
                    )
                    .limit(1)
                )
                offset = _decode_review_history_cursor(
                    cursor,
                    binding=binding,
                    latest_decision_id=latest_decision_id,
                    total=total,
                )
                review_rows = session.execute(
                    select(AuditionReviewDecisionRow, AuditionReviewRecordRow)
                    .join(
                        AuditionReviewRecordRow,
                        AuditionReviewRecordRow.id == AuditionReviewDecisionRow.review_record_id,
                    )
                    .where(*review_predicates)
                    .order_by(
                        AuditionReviewDecisionRow.created_at.desc(),
                        AuditionReviewDecisionRow.revision.desc(),
                        AuditionReviewDecisionRow.id.desc(),
                    )
                    .offset(offset)
                    .limit(page_size)
                ).all()
                items = [
                    self._review_decision_wire(session, review, decision)
                    for decision, review in review_rows
                ]
            next_offset = offset + len(items)
            next_cursor = (
                _encode_review_history_cursor(
                    binding=binding,
                    latest_decision_id=cast(str, latest_decision_id),
                    total=total,
                    offset=next_offset,
                )
                if next_offset < total
                else None
            )
            return items, next_cursor, total

    def _review_wire(
        self,
        session: Session,
        row: AuditionReviewRecordRow,
        *,
        decision_override: AuditionReviewDecisionRow | None = None,
        decision_override_provided: bool = False,
    ) -> dict[str, Any]:
        decision = decision_override
        if decision is None and not decision_override_provided:
            decision = session.scalar(
                select(AuditionReviewDecisionRow)
                .where(
                    AuditionReviewDecisionRow.project_id == row.project_id,
                    AuditionReviewDecisionRow.gate_id == row.gate_id,
                    AuditionReviewDecisionRow.scope_key == row.scope_key,
                )
                .order_by(
                    AuditionReviewDecisionRow.revision.desc(),
                    AuditionReviewDecisionRow.id.desc(),
                )
                .limit(1)
            )
        decision_review = (
            session.get(AuditionReviewRecordRow, decision.review_record_id)
            if decision is not None
            else None
        )
        current_decision = (
            decision
            if decision is not None
            and decision.review_record_id == row.id
            and decision.evidence_fingerprint == row.evidence_fingerprint
            else None
        )
        state = (
            current_decision.decision
            if current_decision is not None
            else "pending"
            if row.eligible
            else "blocked"
        )
        return {
            "contractVersion": "1.0.0",
            "reviewId": row.id,
            "projectId": row.project_id,
            "gateId": row.gate_id,
            "roleId": row.role_id,
            "state": state,
            "revision": row.revision,
            "prerequisiteGateIds": (
                ["per_role_audition_review"]
                if row.gate_id in {"narrator_audition_review", "character_audition_review"}
                else []
            ),
            "evidence": parse_json(row.evidence_json, {}),
            "blockerCodes": parse_json(row.blockers_json, []),
            "warningCodes": parse_json(row.warnings_json, []),
            "latestDecision": (
                self._review_decision_wire(session, decision_review, decision)
                if decision is not None and decision_review is not None
                else None
            ),
            "updatedAt": decision.created_at if decision is not None else row.created_at,
        }

    def _review_decision_wire(
        self,
        session: Session,
        review: AuditionReviewRecordRow,
        row: AuditionReviewDecisionRow,
    ) -> dict[str, Any]:
        listening_attestation = self._stored_listening_attestation(row)
        review_session = (
            session.get(AuditionSessionRow, review.session_id)
            if review.session_id is not None
            else None
        )
        real_per_role = (
            review.gate_id == "per_role_audition_review"
            and review_session is not None
            and review_session.provider_id == KOKORO_PROVIDER_ID
        )
        if listening_attestation is not None and not real_per_role:
            raise ServiceError(
                500,
                "AUDITION_LISTENING_ATTESTATION_INVALID",
                "Stored listening evidence is attached to an ineligible review.",
            )
        if (
            real_per_role
            and row.actor_classification == "human"
            and row.decision in {"approved", "changes_requested", "rejected"}
            and listening_attestation is None
        ):
            raise ServiceError(
                500,
                "AUDITION_LISTENING_ATTESTATION_INVALID",
                "Stored real-provider review evidence lacks its listening attestation.",
            )
        return {
            "contractVersion": "1.0.0",
            "decisionId": row.id,
            "reviewId": review.id,
            "projectId": row.project_id,
            "gateId": row.gate_id,
            "roleId": review.role_id,
            "decision": row.decision,
            "actor": {
                "classification": row.actor_classification,
                "actorId": row.actor_id,
            },
            "expectedReviewRevision": review.revision,
            "evidenceFingerprint": row.evidence_fingerprint,
            "rationale": row.rationale,
            "decidedAt": row.decided_at,
            "immutable": True,
            "supersedesDecisionId": row.supersedes_decision_id,
            "listeningAttestation": listening_attestation,
            "provenance": _public_provenance(row.provenance_json),
        }

    def _ensure_voice_readiness(
        self,
        session: Session,
        project_id: str,
    ) -> VoiceReadinessSnapshotRow | None:
        authority = self._current_cast_authority(session, project_id)
        dictionary = self._current_dictionary(session, project_id)
        if authority is None or authority.phase3a_decision_ids is None or dictionary is None:
            return None
        latest_session = self._latest_current_audition_session(session, authority)
        if latest_session is None:
            return None
        verification = session.get(
            ModelVerificationRow,
            latest_session.model_verification_id,
        )
        if verification is None:
            return None
        roles = list(
            session.scalars(
                select(ProductionRoleRow)
                .where(
                    ProductionRoleRow.project_id == project_id,
                    ProductionRoleRow.casting_run_id == authority.casting_run.id,
                    ProductionRoleRow.status.in_(("active", "unresolved")),
                    ProductionRoleRow.id.in_(authority.assignments_by_role),
                )
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        )
        rights_evidence: list[dict[str, object]] = []
        audio_integrity_fingerprints: list[str] = []
        role_decision_ids: dict[str, list[str]] = {
            "narrator_audition_review": [],
            "character_audition_review": [],
        }
        for role in roles:
            current = self._approved_current_role_audition_evidence(
                session,
                authority=authority,
                role_id=role.id,
            )
            if current is None:
                return None
            role_session = current.audition_session
            clip = current.clip
            decision = current.decision
            assert role_session is not None
            assert clip is not None
            assert decision is not None
            integrity_ok, previous_integrity, current_integrity = self._clip_integrity_fingerprints(
                session, clip
            )
            if not integrity_ok:
                self._invalidate_clip_dependencies(
                    session,
                    project_id=project_id,
                    clips=[clip],
                    source_kind="audio_integrity",
                    source_record_id=clip.artifact_id,
                    previous_fingerprint=previous_integrity,
                    current_fingerprint=current_integrity,
                    reason_code="AUDITION_AUDIO_INTEGRITY_CHANGED",
                    rationale="Approved audition audio failed current integrity checks.",
                )
                return None
            rights = session.get(VoiceRightsRecordRow, role_session.rights_record_id)
            if rights is None:
                return None
            audio_integrity_fingerprints.append(current_integrity)
            rights_evidence.append(
                {
                    "roleId": role.id,
                    "rightsRecordId": rights.rights_record_id,
                    "rightsRecordRevision": rights.revision,
                    "rightsRecordFingerprint": rights.rights_fingerprint,
                }
            )
            aggregate_gate_id = (
                "narrator_audition_review"
                if role.role_type in {"primary_narrator", "secondary_narrator"}
                else "character_audition_review"
            )
            role_decision_ids[aggregate_gate_id].append(decision.id)

        decisions: dict[str, AuditionReviewDecisionRow] = {}
        scope_keys = {
            "narrator_audition_review": "aggregate:narrator",
            "character_audition_review": "aggregate:character",
            "pronunciation_review": dictionary.dictionary_id,
        }
        for gate_id, scope_key in scope_keys.items():
            review = session.scalar(
                select(AuditionReviewRecordRow)
                .where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == gate_id,
                    AuditionReviewRecordRow.scope_key == scope_key,
                )
                .order_by(
                    AuditionReviewRecordRow.revision.desc(),
                    AuditionReviewRecordRow.id.desc(),
                )
                .limit(1)
            )
            decision = session.scalar(
                select(AuditionReviewDecisionRow)
                .where(
                    AuditionReviewDecisionRow.project_id == project_id,
                    AuditionReviewDecisionRow.gate_id == gate_id,
                    AuditionReviewDecisionRow.scope_key == scope_key,
                )
                .order_by(
                    AuditionReviewDecisionRow.revision.desc(),
                    AuditionReviewDecisionRow.id.desc(),
                )
                .limit(1)
            )
            review_evidence = parse_json(review.evidence_json, {}) if review is not None else {}
            expected_decision_ids = (
                list(authority.phase3a_decision_ids)
                if gate_id == "pronunciation_review"
                else role_decision_ids[gate_id]
            )
            if (
                review is None
                or not review.eligible
                or not isinstance(review_evidence, dict)
                or review_evidence.get("approvedCastSnapshotFingerprint")
                != authority.cast_snapshot.snapshot_fingerprint
                or (
                    gate_id == "pronunciation_review"
                    and review_evidence.get("pronunciationDictionaryFingerprint")
                    != dictionary.dictionary_fingerprint
                )
                or review.required_decision_ids_json != canonical_json(expected_decision_ids)
                or decision is None
                or decision.review_record_id != review.id
                or decision.decision != "approved"
                or decision.evidence_fingerprint != review.evidence_fingerprint
            ):
                return None
            decisions[gate_id] = decision

        phase3a_ids = list(authority.phase3a_decision_ids)
        cast_snapshot = authority.cast_snapshot
        evidence = {
            "approvedCastSnapshotFingerprint": cast_snapshot.snapshot_fingerprint,
            "audioIntegrityFingerprint": request_fingerprint(sorted(audio_integrity_fingerprints)),
            "characterReviewDecisionId": decisions["character_audition_review"].id,
            "modelVerificationFingerprint": verification.verification_fingerprint,
            "narratorReviewDecisionId": decisions["narrator_audition_review"].id,
            "phase3aGateDecisionIds": phase3a_ids,
            "pronunciationDictionaryFingerprint": dictionary.dictionary_fingerprint,
            "pronunciationReviewDecisionId": decisions["pronunciation_review"].id,
            "rightsEvidenceFingerprint": request_fingerprint(
                sorted(rights_evidence, key=lambda value: str(value["roleId"]))
            ),
            "runtimeProfileFingerprint": latest_session.runtime_profile_fingerprint,
        }
        snapshot_fingerprint = request_fingerprint(evidence)
        existing = session.scalar(
            select(VoiceReadinessSnapshotRow).where(
                VoiceReadinessSnapshotRow.project_id == project_id,
                VoiceReadinessSnapshotRow.snapshot_fingerprint == snapshot_fingerprint,
            )
        )
        if existing is not None:
            return existing
        revision = 1 + int(
            session.scalar(
                select(func.max(VoiceReadinessSnapshotRow.revision)).where(
                    VoiceReadinessSnapshotRow.project_id == project_id
                )
            )
            or 0
        )
        snapshot = VoiceReadinessSnapshotRow(
            id=new_id(),
            project_id=project_id,
            revision=revision,
            cast_snapshot_id=cast_snapshot.id,
            model_verification_id=verification.id,
            pronunciation_dictionary_record_id=dictionary.id,
            narrator_review_decision_id=decisions["narrator_audition_review"].id,
            character_review_decision_id=decisions["character_audition_review"].id,
            pronunciation_review_decision_id=decisions["pronunciation_review"].id,
            phase3a_gate_decision_ids_json=canonical_json(phase3a_ids),
            required_role_count=len(roles),
            approved_role_count=len(roles),
            blocking_finding_count=0,
            evidence_json=canonical_json(evidence),
            snapshot_fingerprint=snapshot_fingerprint,
            blockers_json="[]",
            warnings_json="[]",
            provenance_json=_provenance("application"),
            created_at=utc_now(),
        )
        session.add(snapshot)
        session.flush()
        readiness_review = VoiceReadinessReviewRow(
            id=new_id(),
            project_id=project_id,
            snapshot_id=snapshot.id,
            gate_id="voice_readiness_review",
            revision=revision,
            eligible=True,
            evidence_fingerprint=snapshot.snapshot_fingerprint,
            required_decision_ids_json=canonical_json(
                [*phase3a_ids, *(decision.id for decision in decisions.values())]
            ),
            blockers_json="[]",
            warnings_json="[]",
            provenance_json=_provenance("application"),
            created_at=utc_now(),
        )
        session.add(readiness_review)
        session.flush()
        return snapshot

    def _decide_voice_readiness(
        self,
        *,
        project_id: str,
        review_id: str,
        request: DecideAuditionReviewRequest,
        actor_id: str,
        request_hash: str,
    ) -> dict[str, Any]:
        with self.database.immediate_session() as session:
            self._require_project(session, project_id)
            review = session.get(VoiceReadinessReviewRow, review_id)
            if review is None or review.project_id != project_id:
                raise not_found("voice readiness review")
            snapshot = session.get(VoiceReadinessSnapshotRow, review.snapshot_id)
            if snapshot is None:
                raise ServiceError(
                    500,
                    "VOICE_READINESS_EVIDENCE_MISSING",
                    "The voice readiness evidence is unavailable.",
                )
            replay = session.scalar(
                select(VoiceReadinessDecisionRow).where(
                    VoiceReadinessDecisionRow.project_id == project_id,
                    VoiceReadinessDecisionRow.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                return self._voice_readiness_response(
                    session,
                    review=review,
                    decision=replay,
                    expected_request_hash=request_hash,
                )
            self._reconcile_project_evidence(session, project_id)
            _pronunciation, _narrator, _character, current_snapshot = self._refresh_reviews(
                session, project_id
            )
            latest_review = (
                session.scalar(
                    select(VoiceReadinessReviewRow)
                    .where(
                        VoiceReadinessReviewRow.project_id == project_id,
                        VoiceReadinessReviewRow.snapshot_id == current_snapshot.id,
                    )
                    .order_by(
                        VoiceReadinessReviewRow.revision.desc(),
                        VoiceReadinessReviewRow.id.desc(),
                    )
                    .limit(1)
                )
                if current_snapshot is not None
                else None
            )
            if (
                latest_review is None
                or latest_review.id != review.id
                or current_snapshot is None
                or current_snapshot.id != snapshot.id
                or current_snapshot.snapshot_fingerprint != review.evidence_fingerprint
            ):
                raise ServiceError(
                    409,
                    "VOICE_READINESS_CHANGED",
                    "The voice readiness evidence is no longer current.",
                )
            if (
                review.revision != request.expected_review_revision
                or review.evidence_fingerprint != request.expected_evidence_fingerprint
            ):
                raise ServiceError(
                    409,
                    "VOICE_READINESS_CHANGED",
                    "The voice readiness review changed; refresh before deciding.",
                )
            latest = session.scalar(
                select(VoiceReadinessDecisionRow)
                .where(VoiceReadinessDecisionRow.project_id == project_id)
                .order_by(
                    VoiceReadinessDecisionRow.revision.desc(),
                    VoiceReadinessDecisionRow.id.desc(),
                )
                .limit(1)
            )
            if request.supersedes_decision_id != (latest.id if latest is not None else None):
                raise ServiceError(
                    409,
                    "VOICE_READINESS_DECISION_CHANGED",
                    "The latest readiness decision changed; refresh before deciding.",
                )
            if request.decision == "approve" and not review.eligible:
                raise ServiceError(
                    409,
                    "VOICE_READINESS_BLOCKED",
                    "Blocked readiness evidence cannot be approved.",
                )
            now = utc_now()
            decision = VoiceReadinessDecisionRow(
                id=new_id(),
                project_id=project_id,
                snapshot_id=snapshot.id,
                review_id=review.id,
                gate_id="voice_readiness_review",
                revision=(latest.revision + 1) if latest is not None else 1,
                decision={
                    "approve": "approved",
                    "request_changes": "changes_requested",
                    "reject": "rejected",
                }[request.decision],
                evidence_fingerprint=review.evidence_fingerprint,
                actor_classification="human",
                actor_id=actor_id,
                warning_acknowledgements_json="[]",
                rationale=request.rationale,
                supersedes_decision_id=latest.id if latest is not None else None,
                idempotency_key=request.idempotency_key,
                provenance_json=_provenance("human", input_fingerprint=request_hash),
                decided_at=now,
                created_at=now,
            )
            session.add(decision)
            session.flush()
            self._record_review_response_projection(
                decision,
                review_id=review.id,
                readiness_snapshot_id=snapshot.id,
            )
            return self._voice_readiness_response(
                session,
                review=review,
                decision=decision,
                expected_request_hash=request_hash,
            )

    def _voice_readiness_response(
        self,
        session: Session,
        *,
        review: VoiceReadinessReviewRow,
        decision: VoiceReadinessDecisionRow,
        expected_request_hash: str,
    ) -> dict[str, Any]:
        snapshot = self._review_response_snapshot(
            session,
            decision=decision,
            review_id=review.id,
            expected_request_hash=expected_request_hash,
            require_snapshot_id=review.snapshot_id,
        )
        if (
            decision.review_id != review.id
            or decision.snapshot_id != review.snapshot_id
            or decision.project_id != review.project_id
            or decision.gate_id != "voice_readiness_review"
            or decision.evidence_fingerprint != review.evidence_fingerprint
        ):
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved readiness decision does not match its evidence.",
            )
        if snapshot is None:
            raise ServiceError(
                500,
                "IDEMPOTENCY_RECORD_INVALID",
                "The saved readiness-decision snapshot is unavailable.",
            )
        return {
            "review": self._readiness_review_wire(
                session,
                review,
                decision_override=decision,
            ),
            "decision": self._readiness_decision_wire(review, decision),
            "voiceReadinessSnapshot": self._readiness_snapshot_wire(
                session,
                snapshot,
            ),
        }

    @staticmethod
    def _readiness_snapshot_wire(
        session: Session,
        row: VoiceReadinessSnapshotRow,
    ) -> dict[str, Any]:
        snapshot = session.get(ApprovedCastSnapshotRow, row.cast_snapshot_id)
        verification = session.get(ModelVerificationRow, row.model_verification_id)
        evidence = parse_json(row.evidence_json, {})
        if snapshot is None or verification is None:
            raise ServiceError(
                500,
                "VOICE_READINESS_EVIDENCE_MISSING",
                "The voice readiness evidence is unavailable.",
            )
        return {
            "contractVersion": "1.0.0",
            "snapshotId": row.id,
            "projectId": row.project_id,
            "revision": row.revision,
            "approvedCastSnapshotId": snapshot.id,
            "approvedCastSnapshotRevision": snapshot.revision,
            "approvedCastSnapshotFingerprint": snapshot.snapshot_fingerprint,
            "runtimeProfileFingerprint": evidence.get("runtimeProfileFingerprint"),
            "modelVerificationFingerprint": verification.verification_fingerprint,
            "rightsEvidenceFingerprint": evidence.get("rightsEvidenceFingerprint"),
            "narratorAuditionDecisionIds": [row.narrator_review_decision_id],
            "characterAuditionDecisionIds": [row.character_review_decision_id],
            "pronunciationReviewDecisionId": row.pronunciation_review_decision_id,
            "requiredRoleCount": row.required_role_count,
            "approvedRoleCount": row.approved_role_count,
            "blockingFindingCodes": parse_json(row.blockers_json, []),
            "snapshotFingerprint": row.snapshot_fingerprint,
            "reviewEligible": row.blocking_finding_count == 0,
            "authorizes": "later_performance_direction_only",
            "authorizesFullBookRendering": False,
            "createdAt": row.created_at,
            "immutable": True,
        }

    def _readiness_review_wire(
        self,
        session: Session,
        row: VoiceReadinessReviewRow,
        *,
        decision_override: VoiceReadinessDecisionRow | None = None,
    ) -> dict[str, Any]:
        decision = decision_override
        if decision is None:
            decision = session.scalar(
                select(VoiceReadinessDecisionRow)
                .where(VoiceReadinessDecisionRow.project_id == row.project_id)
                .order_by(
                    VoiceReadinessDecisionRow.revision.desc(),
                    VoiceReadinessDecisionRow.id.desc(),
                )
                .limit(1)
            )
        decision_review = (
            session.get(VoiceReadinessReviewRow, decision.review_id)
            if decision is not None
            else None
        )
        current_decision = (
            decision
            if decision is not None
            and decision.review_id == row.id
            and decision.evidence_fingerprint == row.evidence_fingerprint
            else None
        )
        snapshot = session.get(VoiceReadinessSnapshotRow, row.snapshot_id)
        snapshot_evidence = parse_json(snapshot.evidence_json, {}) if snapshot is not None else {}
        required_hashes = (
            "approvedCastSnapshotFingerprint",
            "audioIntegrityFingerprint",
            "modelVerificationFingerprint",
            "pronunciationDictionaryFingerprint",
            "rightsEvidenceFingerprint",
            "runtimeProfileFingerprint",
        )
        if (
            snapshot is None
            or snapshot.project_id != row.project_id
            or snapshot.snapshot_fingerprint != row.evidence_fingerprint
            or not isinstance(snapshot_evidence, dict)
            or any(
                not isinstance(snapshot_evidence.get(key), str)
                or re.fullmatch(r"[0-9a-f]{64}", snapshot_evidence[key]) is None
                for key in required_hashes
            )
            or not isinstance(
                snapshot_evidence.get("pronunciationReviewDecisionId"),
                str,
            )
        ):
            raise ServiceError(
                500,
                "VOICE_READINESS_EVIDENCE_MISSING",
                "The voice readiness review evidence is unavailable.",
            )
        gate_evidence = {
            "projectId": row.project_id,
            "gateId": "voice_readiness_review",
            "roleId": None,
            "auditionSessionId": None,
            "auditionClipId": None,
            "auditionClipRevision": None,
            "approvedCastSnapshotFingerprint": snapshot_evidence["approvedCastSnapshotFingerprint"],
            "castAssignmentFingerprint": None,
            "rightsRecordFingerprint": snapshot_evidence["rightsEvidenceFingerprint"],
            "runtimeProfileFingerprint": snapshot_evidence["runtimeProfileFingerprint"],
            "modelVerificationFingerprint": snapshot_evidence["modelVerificationFingerprint"],
            "pronunciationDictionaryFingerprint": snapshot_evidence[
                "pronunciationDictionaryFingerprint"
            ],
            "pronunciationDependencyFingerprint": request_fingerprint(
                {
                    "pronunciationDictionaryFingerprint": snapshot_evidence[
                        "pronunciationDictionaryFingerprint"
                    ],
                    "pronunciationReviewDecisionId": snapshot_evidence[
                        "pronunciationReviewDecisionId"
                    ],
                }
            ),
            "audioQualityFingerprint": snapshot_evidence["audioIntegrityFingerprint"],
            "evidenceFingerprint": row.evidence_fingerprint,
        }
        return {
            "contractVersion": "1.0.0",
            "reviewId": row.id,
            "projectId": row.project_id,
            "gateId": "voice_readiness_review",
            "roleId": None,
            "state": (
                current_decision.decision
                if current_decision is not None
                else "pending"
                if row.eligible
                else "blocked"
            ),
            "revision": row.revision,
            "prerequisiteGateIds": [
                "narrator_audition_review",
                "character_audition_review",
                "pronunciation_review",
            ],
            "evidence": gate_evidence,
            "blockerCodes": parse_json(row.blockers_json, []),
            "warningCodes": parse_json(row.warnings_json, []),
            "latestDecision": (
                self._readiness_decision_wire(decision_review, decision)
                if decision is not None and decision_review is not None
                else None
            ),
            "updatedAt": decision.created_at if decision is not None else row.created_at,
        }

    @staticmethod
    def _readiness_decision_wire(
        review: VoiceReadinessReviewRow,
        row: VoiceReadinessDecisionRow,
    ) -> dict[str, Any]:
        return {
            "contractVersion": "1.0.0",
            "decisionId": row.id,
            "reviewId": review.id,
            "projectId": row.project_id,
            "gateId": "voice_readiness_review",
            "roleId": None,
            "decision": row.decision,
            "actor": {
                "classification": row.actor_classification,
                "actorId": row.actor_id,
            },
            "expectedReviewRevision": review.revision,
            "evidenceFingerprint": row.evidence_fingerprint,
            "rationale": row.rationale,
            "decidedAt": row.decided_at,
            "immutable": True,
            "supersedesDecisionId": row.supersedes_decision_id,
            "provenance": _public_provenance(row.provenance_json),
        }

    def _invalidate_pronunciation_dependencies(
        self,
        session: Session,
        *,
        project_id: str,
        changed_entry: PronunciationEntryRow,
        previous_fingerprint: str,
        current_fingerprint: str,
    ) -> tuple[list[str], list[str]]:
        clips = list(
            session.scalars(
                select(AuditionClipRow)
                .where(AuditionClipRow.project_id == project_id)
                .order_by(AuditionClipRow.created_at, AuditionClipRow.id)
            )
        )
        affected_clips: list[AuditionClipRow] = []
        preserved: list[str] = []
        changed_lineage_ids = {changed_entry.entry_id}
        changed_lineage_ids.update(
            value.entry_id
            for value in self._pronunciation_record_lineage(
                session,
                changed_entry,
                direction="predecessors",
            )
        )
        for clip in clips:
            audition_session = session.get(AuditionSessionRow, clip.session_id)
            script = session.get(AuditionScriptRow, clip.script_id)
            request_row = session.get(SpeechProviderRequestRow, clip.provider_request_id)
            plan = (
                session.get(TextNormalizationPlanRow, request_row.normalization_plan_id)
                if request_row is not None
                else None
            )
            if audition_session is None or script is None or plan is None:
                preserved.append(clip.id)
                continue
            dependencies = set(parse_json(plan.pronunciation_entry_ids_json, []))
            affected = bool(dependencies & changed_lineage_ids)
            if not affected:
                try:
                    source_text = self._read_script_text(script)
                    normalization = self._validated_normalization_plan(
                        audition_session=audition_session,
                        script=script,
                        plan=plan,
                        source_text=source_text,
                    )
                    current_plan = self._compile_pronunciation_plan(
                        session,
                        audition_session,
                        normalization.normalized_text,
                        context=self._script_pronunciation_context(
                            session,
                            audition_session,
                            script,
                        ),
                    )
                    current_dependencies = {
                        entry_id
                        for entry_id, _revision in (current_plan.dependency_entry_revisions)
                    }
                    affected = bool(current_dependencies & changed_lineage_ids)
                except (ServiceError, PronunciationError):
                    affected = True
            if not affected:
                preserved.append(clip.id)
                continue
            affected_clips.append(clip)

        pronunciation_review = session.scalar(
            select(AuditionReviewRecordRow)
            .where(
                AuditionReviewRecordRow.project_id == project_id,
                AuditionReviewRecordRow.gate_id == "pronunciation_review",
            )
            .order_by(
                AuditionReviewRecordRow.revision.desc(),
                AuditionReviewRecordRow.id.desc(),
            )
            .limit(1)
        )
        if pronunciation_review is not None:
            self._append_audition_review_invalidation(
                session,
                project_id=project_id,
                review=pronunciation_review,
                rationale="The applicable pronunciation dictionary changed.",
            )
        self._append_voice_readiness_invalidation(
            session,
            project_id=project_id,
            rationale="Applicable pronunciation evidence changed.",
        )

        if not affected_clips:
            return [], preserved

        affected_clip_ids = {clip.id for clip in affected_clips}
        review_rows = list(
            session.scalars(
                select(AuditionReviewRecordRow).where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.clip_id.in_(affected_clip_ids),
                )
            )
        )
        reviews_by_clip: dict[str, list[AuditionReviewRecordRow]] = {}
        for review_row in review_rows:
            if review_row.clip_id is not None:
                reviews_by_clip.setdefault(review_row.clip_id, []).append(review_row)

        for clip in affected_clips:
            reviews = reviews_by_clip.get(clip.id, [])
            invalidation_fingerprint = request_fingerprint(
                {
                    "clipId": clip.id,
                    "currentFingerprint": current_fingerprint,
                    "entryId": changed_entry.entry_id,
                    "previousFingerprint": previous_fingerprint,
                    "reasonCode": "PRONUNCIATION_ENTRY_CHANGED",
                }
            )
            existing = session.scalar(
                select(AuditionEvidenceInvalidationRow).where(
                    AuditionEvidenceInvalidationRow.clip_id == clip.id,
                    AuditionEvidenceInvalidationRow.invalidation_fingerprint
                    == invalidation_fingerprint,
                )
            )
            if existing is None:
                session.add(
                    AuditionEvidenceInvalidationRow(
                        id=new_id(),
                        project_id=project_id,
                        clip_id=clip.id,
                        session_id=clip.session_id,
                        role_id=clip.role_id,
                        source_kind="pronunciation_entry",
                        source_record_id=changed_entry.id,
                        previous_fingerprint=previous_fingerprint,
                        current_fingerprint=current_fingerprint,
                        reason_code="PRONUNCIATION_ENTRY_CHANGED",
                        affected_review_ids_json=canonical_json([review.id for review in reviews]),
                        invalidation_fingerprint=invalidation_fingerprint,
                        provenance_json=_provenance("system"),
                        created_at=utc_now(),
                    )
                )
            audition_session = session.get(AuditionSessionRow, clip.session_id)
            if audition_session is not None:
                audition_session.state = "invalidated"

        invalidated_aggregate_gates: set[str] = set()
        for role_id in sorted({clip.role_id for clip in affected_clips}):
            role_review = session.scalar(
                select(AuditionReviewRecordRow)
                .where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                    AuditionReviewRecordRow.scope_key == role_id,
                )
                .order_by(
                    AuditionReviewRecordRow.revision.desc(),
                    AuditionReviewRecordRow.id.desc(),
                )
                .limit(1)
            )
            if role_review is None or role_review.clip_id not in affected_clip_ids:
                continue
            if self._append_audition_review_invalidation(
                session,
                project_id=project_id,
                review=role_review,
                rationale="Applicable pronunciation evidence changed.",
            ):
                role = session.get(ProductionRoleRow, role_id)
                if role is not None:
                    invalidated_aggregate_gates.add(
                        "narrator_audition_review"
                        if role.role_type in {"primary_narrator", "secondary_narrator"}
                        else "character_audition_review"
                    )

        aggregate_scope_keys = {
            "narrator_audition_review": "aggregate:narrator",
            "character_audition_review": "aggregate:character",
        }
        for gate_id in sorted(invalidated_aggregate_gates):
            aggregate_review = session.scalar(
                select(AuditionReviewRecordRow)
                .where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == gate_id,
                    AuditionReviewRecordRow.scope_key == aggregate_scope_keys[gate_id],
                )
                .order_by(
                    AuditionReviewRecordRow.revision.desc(),
                    AuditionReviewRecordRow.id.desc(),
                )
                .limit(1)
            )
            if aggregate_review is not None:
                self._append_audition_review_invalidation(
                    session,
                    project_id=project_id,
                    review=aggregate_review,
                    rationale="A dependent role audition approval was invalidated.",
                )

        return [clip.id for clip in affected_clips], preserved

    @staticmethod
    def _append_audition_review_invalidation(
        session: Session,
        *,
        project_id: str,
        review: AuditionReviewRecordRow,
        rationale: str,
    ) -> bool:
        latest_decision = session.scalar(
            select(AuditionReviewDecisionRow)
            .where(
                AuditionReviewDecisionRow.project_id == project_id,
                AuditionReviewDecisionRow.gate_id == review.gate_id,
                AuditionReviewDecisionRow.scope_key == review.scope_key,
            )
            .order_by(
                AuditionReviewDecisionRow.revision.desc(),
                AuditionReviewDecisionRow.id.desc(),
            )
            .limit(1)
        )
        if latest_decision is None or latest_decision.decision == "invalidated":
            return False
        now = utc_now()
        session.add(
            AuditionReviewDecisionRow(
                id=new_id(),
                project_id=project_id,
                review_record_id=review.id,
                gate_id=review.gate_id,
                scope_key=review.scope_key,
                revision=latest_decision.revision + 1,
                decision="invalidated",
                evidence_fingerprint=review.evidence_fingerprint,
                actor_classification="system",
                actor_id=_PRODUCER_ID,
                warning_acknowledgements_json="[]",
                rationale=rationale,
                supersedes_decision_id=latest_decision.id,
                idempotency_key=None,
                provenance_json=_provenance("system"),
                decided_at=now,
                created_at=now,
            )
        )
        session.flush()
        return True

    @staticmethod
    def _append_voice_readiness_invalidation(
        session: Session,
        *,
        project_id: str,
        rationale: str,
    ) -> bool:
        review = session.scalar(
            select(VoiceReadinessReviewRow)
            .where(VoiceReadinessReviewRow.project_id == project_id)
            .order_by(
                VoiceReadinessReviewRow.revision.desc(),
                VoiceReadinessReviewRow.id.desc(),
            )
            .limit(1)
        )
        latest_decision = session.scalar(
            select(VoiceReadinessDecisionRow)
            .where(VoiceReadinessDecisionRow.project_id == project_id)
            .order_by(
                VoiceReadinessDecisionRow.revision.desc(),
                VoiceReadinessDecisionRow.id.desc(),
            )
            .limit(1)
        )
        if review is None or latest_decision is None or latest_decision.decision == "invalidated":
            return False
        snapshot = session.get(VoiceReadinessSnapshotRow, review.snapshot_id)
        if snapshot is None:
            return False
        now = utc_now()
        session.add(
            VoiceReadinessDecisionRow(
                id=new_id(),
                project_id=project_id,
                snapshot_id=snapshot.id,
                review_id=review.id,
                gate_id="voice_readiness_review",
                revision=latest_decision.revision + 1,
                decision="invalidated",
                evidence_fingerprint=review.evidence_fingerprint,
                actor_classification="system",
                actor_id=_PRODUCER_ID,
                warning_acknowledgements_json="[]",
                rationale=rationale,
                supersedes_decision_id=latest_decision.id,
                idempotency_key=None,
                provenance_json=_provenance("system"),
                decided_at=now,
                created_at=now,
            )
        )
        session.flush()
        return True

    def _invalidate_clip_dependencies(
        self,
        session: Session,
        *,
        project_id: str,
        clips: Sequence[AuditionClipRow],
        source_kind: str,
        source_record_id: str,
        previous_fingerprint: str,
        current_fingerprint: str,
        reason_code: str,
        rationale: str,
    ) -> list[str]:
        ordered_clips = sorted(
            {clip.id: clip for clip in clips}.values(),
            key=lambda clip: (clip.created_at, clip.id),
        )
        if not ordered_clips:
            return []
        clip_ids = {clip.id for clip in ordered_clips}
        exact_reviews = list(
            session.scalars(
                select(AuditionReviewRecordRow).where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.clip_id.in_(clip_ids),
                )
            )
        )
        review_ids_by_clip: dict[str, list[str]] = {}
        for exact_review in exact_reviews:
            if exact_review.clip_id is not None:
                review_ids_by_clip.setdefault(exact_review.clip_id, []).append(exact_review.id)
        for clip in ordered_clips:
            fingerprint = request_fingerprint(
                {
                    "clipId": clip.id,
                    "currentFingerprint": current_fingerprint,
                    "previousFingerprint": previous_fingerprint,
                    "reasonCode": reason_code,
                    "sourceKind": source_kind,
                    "sourceRecordId": source_record_id,
                }
            )
            existing = session.scalar(
                select(AuditionEvidenceInvalidationRow).where(
                    AuditionEvidenceInvalidationRow.clip_id == clip.id,
                    AuditionEvidenceInvalidationRow.invalidation_fingerprint == fingerprint,
                )
            )
            if existing is None:
                session.add(
                    AuditionEvidenceInvalidationRow(
                        id=new_id(),
                        project_id=project_id,
                        clip_id=clip.id,
                        session_id=clip.session_id,
                        role_id=clip.role_id,
                        source_kind=source_kind,
                        source_record_id=source_record_id,
                        previous_fingerprint=previous_fingerprint,
                        current_fingerprint=current_fingerprint,
                        reason_code=reason_code,
                        affected_review_ids_json=canonical_json(
                            review_ids_by_clip.get(clip.id, [])
                        ),
                        invalidation_fingerprint=fingerprint,
                        provenance_json=_provenance("system"),
                        created_at=utc_now(),
                    )
                )
            affected_session = session.get(AuditionSessionRow, clip.session_id)
            if affected_session is not None:
                affected_session.state = "invalidated"

        aggregate_gates: set[str] = set()
        for role_id in sorted({clip.role_id for clip in ordered_clips}):
            current_review = session.scalar(
                select(AuditionReviewRecordRow)
                .where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                    AuditionReviewRecordRow.scope_key == role_id,
                )
                .order_by(
                    AuditionReviewRecordRow.revision.desc(),
                    AuditionReviewRecordRow.id.desc(),
                )
                .limit(1)
            )
            if current_review is None or current_review.clip_id not in clip_ids:
                continue
            if self._append_audition_review_invalidation(
                session,
                project_id=project_id,
                review=current_review,
                rationale=rationale,
            ):
                role = session.get(ProductionRoleRow, role_id)
                if role is not None:
                    aggregate_gates.add(
                        "narrator_audition_review"
                        if role.role_type in {"primary_narrator", "secondary_narrator"}
                        else "character_audition_review"
                    )
        aggregate_scopes = {
            "narrator_audition_review": "aggregate:narrator",
            "character_audition_review": "aggregate:character",
        }
        for gate_id in sorted(aggregate_gates):
            aggregate_review = session.scalar(
                select(AuditionReviewRecordRow)
                .where(
                    AuditionReviewRecordRow.project_id == project_id,
                    AuditionReviewRecordRow.gate_id == gate_id,
                    AuditionReviewRecordRow.scope_key == aggregate_scopes[gate_id],
                )
                .order_by(
                    AuditionReviewRecordRow.revision.desc(),
                    AuditionReviewRecordRow.id.desc(),
                )
                .limit(1)
            )
            if aggregate_review is not None:
                self._append_audition_review_invalidation(
                    session,
                    project_id=project_id,
                    review=aggregate_review,
                    rationale=rationale,
                )
        if aggregate_gates:
            self._append_voice_readiness_invalidation(
                session,
                project_id=project_id,
                rationale=rationale,
            )
        return [clip.id for clip in ordered_clips]

    def _clip_integrity_fingerprints(
        self,
        session: Session,
        clip: AuditionClipRow,
    ) -> tuple[bool, str, str]:
        artifact = session.get(AudioArtifactRow, clip.artifact_id)
        cache = session.get(AuditionCacheRecordRow, clip.cache_record_id)
        previous = (
            artifact.artifact_fingerprint
            if artifact is not None
            else request_fingerprint({"artifactId": clip.artifact_id, "state": "missing"})
        )
        quality = (
            session.scalar(
                select(AudioQualityRecordRow)
                .where(AudioQualityRecordRow.clip_id == clip.id)
                .order_by(
                    AudioQualityRecordRow.revision.desc(),
                    AudioQualityRecordRow.id.desc(),
                )
                .limit(1)
            )
            if artifact is not None
            else None
        )
        state_material: dict[str, object] = {
            "artifactAvailability": (artifact.availability if artifact is not None else "missing"),
            "artifactId": clip.artifact_id,
            "cacheState": cache.state if cache is not None else "missing",
            "clipId": clip.id,
            "qualityFingerprint": (quality.quality_fingerprint if quality is not None else None),
        }
        quality_evidence_valid = False
        blocking_quality_codes: Sequence[str] = ("AUDIO_QUALITY_EVIDENCE_INVALID",)
        if quality is not None and artifact is not None:
            try:
                blocking_quality_codes, _quality_warnings = _validated_audio_quality_evidence(
                    quality,
                    artifact=artifact,
                    clip=clip,
                )
                quality_evidence_valid = True
            except ValueError:
                state_material["qualityEvidenceFingerprint"] = request_fingerprint(
                    {
                        "artifactId": quality.artifact_id,
                        "blockingFindingCount": quality.blocking_finding_count,
                        "clipId": quality.clip_id,
                        "clippedSampleCount": quality.clipped_sample_count,
                        "findingsJsonSha256": sha256_text(quality.findings_json),
                        "outcome": quality.outcome,
                        "peakMillidbfs": quality.peak_millidbfs,
                        "policyFingerprint": quality.policy_fingerprint,
                        "policyId": quality.policy_id,
                        "policyVersion": quality.policy_version,
                        "qualityFingerprint": quality.quality_fingerprint,
                        "rmsMillidbfs": quality.rms_millidbfs,
                        "silenceRatioPpm": quality.silence_ratio_ppm,
                        "warningCount": quality.warning_count,
                    }
                )
        if (
            artifact is None
            or artifact.project_id != clip.project_id
            or artifact.availability != "present"
            or cache is None
            or cache.project_id != clip.project_id
            or cache.artifact_id != artifact.id
            or cache.state != "verified"
            or quality is None
            or quality.project_id != clip.project_id
            or not quality_evidence_valid
            or blocking_quality_codes
        ):
            return False, previous, request_fingerprint(state_material)
        try:
            _path, payload = _read_bounded_stable_regular_file(
                self.data_dir,
                self._resolve_verified_artifact_path(artifact.storage_key),
                maximum_bytes=_MAX_AUDIO_BYTES,
                expected_byte_size=artifact.byte_count,
            )
            qc = inspect_audition_wav_bytes(payload)
        except (OSError, ServiceError, AuditionError, ValueError):
            state_material["fileState"] = "unavailable"
            return False, previous, request_fingerprint(state_material)
        actual_properties = {
            "byteCount": len(payload),
            "channelCount": qc.measurements.channel_count,
            "contentSha256": sha256_bytes(payload),
            "durationMilliseconds": max(1, round(qc.measurements.duration_ms)),
            "frameCount": qc.measurements.frame_count,
            "sampleRateHz": qc.measurements.sample_rate_hz,
            "sampleWidthBytes": qc.measurements.sample_width_bytes,
        }
        expected_properties = {
            "byteCount": artifact.byte_count,
            "channelCount": artifact.channel_count,
            "contentSha256": artifact.content_sha256,
            "durationMilliseconds": artifact.duration_ms,
            "frameCount": artifact.frame_count,
            "sampleRateHz": artifact.sample_rate_hz,
            "sampleWidthBytes": artifact.sample_width_bytes,
        }
        if (
            qc.blocking_findings
            or qc.measurements.content_sha256 != actual_properties["contentSha256"]
            or actual_properties != expected_properties
        ):
            state_material["actualPropertiesFingerprint"] = request_fingerprint(actual_properties)
            state_material["blockingFindingCodes"] = list(qc.blocking_findings)
            return False, previous, request_fingerprint(state_material)
        return True, previous, previous

    def _session_dependency_drift(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
    ) -> tuple[str, str, str, str, str, str] | None:
        authority = self._current_cast_authority(session, audition_session.project_id)
        current_assignment = (
            authority.assignments_by_role.get(audition_session.role_id)
            if authority is not None
            else None
        )
        frozen_phase3a_ids = parse_json(
            audition_session.phase3a_gate_decision_ids_json,
            None,
        )
        exact_phase3a_ids = (
            tuple(frozen_phase3a_ids)
            if isinstance(frozen_phase3a_ids, list)
            and all(isinstance(value, str) for value in frozen_phase3a_ids)
            else None
        )
        if (
            authority is None
            or authority.phase3a_decision_ids is None
            or authority.casting_run.id != audition_session.casting_run_id
            or authority.cast_snapshot.id != audition_session.cast_snapshot_id
            or authority.cast_snapshot.revision != audition_session.cast_snapshot_revision
            or authority.cast_snapshot.snapshot_fingerprint
            != audition_session.cast_snapshot_fingerprint
            or current_assignment is None
            or current_assignment.id != audition_session.assignment_id
            or current_assignment.revision != audition_session.assignment_revision
            or exact_phase3a_ids != authority.phase3a_decision_ids
        ):
            previous = request_fingerprint(
                {
                    "assignmentId": audition_session.assignment_id,
                    "assignmentRevision": audition_session.assignment_revision,
                    "castSnapshotFingerprint": audition_session.cast_snapshot_fingerprint,
                    "castSnapshotId": audition_session.cast_snapshot_id,
                    "castSnapshotRevision": audition_session.cast_snapshot_revision,
                    "castingRunId": audition_session.casting_run_id,
                    "phase3aGateDecisionIds": (
                        list(exact_phase3a_ids) if exact_phase3a_ids is not None else None
                    ),
                }
            )
            return (
                "cast_snapshot",
                audition_session.cast_snapshot_id,
                previous,
                self._current_cast_authority_fingerprint(authority),
                "APPROVED_CAST_EVIDENCE_CHANGED",
                "The approved cast snapshot or Phase 3A authority changed.",
            )

        assignment = session.get(CastAssignmentRow, audition_session.assignment_id)
        latest_assignment = session.scalar(
            select(CastAssignmentRow)
            .where(CastAssignmentRow.role_id == audition_session.role_id)
            .order_by(CastAssignmentRow.revision.desc(), CastAssignmentRow.id.desc())
            .limit(1)
        )
        assignment_invalidation = (
            session.scalar(
                select(CastAssignmentInvalidationRow).where(
                    CastAssignmentInvalidationRow.assignment_id == audition_session.assignment_id
                )
            )
            if assignment is not None
            else None
        )
        if (
            assignment is None
            or latest_assignment is None
            or latest_assignment.id != assignment.id
            or assignment.revision != audition_session.assignment_revision
            or assignment.assignment_state not in {"selected", "locked"}
            or assignment_invalidation is not None
        ):
            previous = (
                self._assignment_fingerprint(assignment)
                if assignment is not None
                else request_fingerprint(
                    {"assignmentId": audition_session.assignment_id, "state": "missing"}
                )
            )
            current = (
                self._assignment_fingerprint(latest_assignment)
                if latest_assignment is not None
                else request_fingerprint(
                    {"roleId": audition_session.role_id, "state": "unassigned"}
                )
            )
            return (
                "assignment",
                audition_session.assignment_id,
                previous,
                current,
                "CAST_ASSIGNMENT_CHANGED",
                "The governed cast assignment changed.",
            )

        rights = session.get(VoiceRightsRecordRow, audition_session.rights_record_id)
        voice = session.get(VoiceProfileRow, audition_session.voice_profile_record_id)
        catalog = session.get(VoiceCatalogRevisionRow, audition_session.catalog_revision_id)
        latest_rights = session.scalar(
            select(VoiceRightsRecordRow)
            .where(
                VoiceRightsRecordRow.voice_profile_record_id
                == audition_session.voice_profile_record_id
            )
            .order_by(VoiceRightsRecordRow.revision.desc(), VoiceRightsRecordRow.id.desc())
            .limit(1)
        )
        governed_private_rights = (
            voice is not None
            and rights is not None
            and catalog is not None
            and self._governed_private_audition_rights_are_exact(
                session,
                voice=voice,
                rights=rights,
                catalog=catalog,
            )
        )
        generally_eligible_rights = (
            rights is not None
            and rights.rights_state in {"verified", "restricted"}
            and rights.commercial_use_status not in {"unknown", "prohibited"}
            and rights.consent_status not in {"missing", "unknown", "prohibited"}
            and rights.human_verification_status in {"verified", "not_required_fixture"}
        )
        if (
            rights is None
            or latest_rights is None
            or latest_rights.id != rights.id
            or rights.revision != audition_session.rights_revision
            or not (generally_eligible_rights or governed_private_rights)
        ):
            previous = (
                rights.rights_fingerprint
                if rights is not None
                else request_fingerprint(
                    {"rightsRecordId": audition_session.rights_record_id, "state": "missing"}
                )
            )
            current = (
                latest_rights.rights_fingerprint
                if latest_rights is not None
                else request_fingerprint(
                    {
                        "state": "missing",
                        "voiceProfileRecordId": audition_session.voice_profile_record_id,
                    }
                )
            )
            return (
                "rights",
                audition_session.rights_record_id,
                previous,
                current,
                "VOICE_RIGHTS_CHANGED",
                "The governed voice-rights evidence changed.",
            )

        runtime_profile = session.get(
            SpeechRuntimeProfileRow,
            audition_session.runtime_profile_id,
        )
        if (
            runtime_profile is None
            or not runtime_profile.active
            or runtime_profile.profile_fingerprint != audition_session.runtime_profile_fingerprint
            or runtime_profile.provider_id != audition_session.provider_id
        ):
            current = request_fingerprint(
                {
                    "active": runtime_profile.active if runtime_profile is not None else False,
                    "profileFingerprint": (
                        runtime_profile.profile_fingerprint if runtime_profile is not None else None
                    ),
                    "profileId": audition_session.runtime_profile_id,
                    "providerId": (
                        runtime_profile.provider_id if runtime_profile is not None else None
                    ),
                }
            )
            return (
                "runtime",
                audition_session.runtime_profile_id,
                audition_session.runtime_profile_fingerprint,
                current,
                "SPEECH_RUNTIME_CHANGED",
                "The bound local speech runtime changed.",
            )

        manifest = session.get(
            ModelPackageManifestRow,
            audition_session.model_manifest_id,
        )
        if (
            manifest is None
            or manifest.revocation_state != "active"
            or manifest.manifest_fingerprint != audition_session.model_package_fingerprint
            or manifest.provider_id != audition_session.provider_id
            or manifest.provider_version != audition_session.provider_version
            or manifest.model_id != audition_session.model_id
            or manifest.model_version != audition_session.model_version
        ):
            current = request_fingerprint(
                {
                    "manifestFingerprint": (
                        manifest.manifest_fingerprint if manifest is not None else None
                    ),
                    "manifestId": audition_session.model_manifest_id,
                    "revocationState": (
                        manifest.revocation_state if manifest is not None else "missing"
                    ),
                }
            )
            return (
                "model_package",
                audition_session.model_manifest_id,
                audition_session.model_package_fingerprint,
                current,
                "MODEL_PACKAGE_CHANGED",
                "The bound model package changed.",
            )
        latest_installation = self._latest_installation(session, manifest.id)
        latest_verification = (
            self._latest_verification(session, latest_installation.installation_id)
            if latest_installation is not None
            else None
        )
        if (
            latest_installation is None
            or latest_installation.id != audition_session.model_installation_record_id
            or latest_installation.state != "active"
            or latest_installation.package_fingerprint != audition_session.model_package_fingerprint
            or latest_verification is None
            or latest_verification.id != audition_session.model_verification_id
            or latest_verification.outcome != "verified"
            or latest_verification.package_fingerprint != audition_session.model_package_fingerprint
        ):
            current = request_fingerprint(
                {
                    "installationRecordId": (
                        latest_installation.id if latest_installation is not None else None
                    ),
                    "installationState": (
                        latest_installation.state if latest_installation is not None else "missing"
                    ),
                    "verificationId": (
                        latest_verification.id if latest_verification is not None else None
                    ),
                    "verificationOutcome": (
                        latest_verification.outcome
                        if latest_verification is not None
                        else "missing"
                    ),
                }
            )
            previous = request_fingerprint(
                {
                    "installationRecordId": (audition_session.model_installation_record_id),
                    "modelPackageFingerprint": (audition_session.model_package_fingerprint),
                    "verificationId": audition_session.model_verification_id,
                }
            )
            return (
                "model_package",
                audition_session.model_manifest_id,
                previous,
                current,
                "MODEL_VERIFICATION_CHANGED",
                "The active model installation or verification changed.",
            )
        try:
            activation = self._governed_local_voice_activation_wire(
                session,
                audition_session,
            )
        except ServiceError:
            provenance = parse_json(audition_session.provenance_json, {})
            details = provenance.get("details") if isinstance(provenance, dict) else None
            stored = (
                details.get("governedLocalVoiceActivation") if isinstance(details, dict) else None
            )
            # The frozen v5 provider source domain includes session-owned
            # governed activation authority; activation has no separate row type.
            return (
                "provider",
                audition_session.id,
                request_fingerprint(stored),
                _governed_voice_inventory()["inventoryFingerprint"],
                "GOVERNED_LOCAL_VOICE_ACTIVATION_CHANGED",
                "The restricted local-voice activation changed or failed verification.",
            )
        if audition_session.provider_id == KOKORO_PROVIDER_ID and activation is None:
            return (
                "provider",
                audition_session.id,
                request_fingerprint(None),
                _governed_voice_inventory()["inventoryFingerprint"],
                "GOVERNED_LOCAL_VOICE_ACTIVATION_CHANGED",
                "The restricted local-voice activation is unavailable.",
            )
        return None

    def _reconcile_project_evidence(
        self,
        session: Session,
        project_id: str,
    ) -> list[str]:
        invalidated_clip_ids: list[str] = []
        sessions = list(
            session.scalars(
                select(AuditionSessionRow)
                .where(AuditionSessionRow.project_id == project_id)
                .order_by(AuditionSessionRow.created_at, AuditionSessionRow.id)
            )
        )
        for audition_session in sessions:
            drift = self._session_dependency_drift(session, audition_session)
            if drift is None:
                continue
            (
                source_kind,
                source_record_id,
                previous_fingerprint,
                current_fingerprint,
                reason_code,
                rationale,
            ) = drift
            clips = list(
                session.scalars(
                    select(AuditionClipRow)
                    .where(AuditionClipRow.session_id == audition_session.id)
                    .order_by(AuditionClipRow.created_at, AuditionClipRow.id)
                )
            )
            invalidated_clip_ids.extend(
                self._invalidate_clip_dependencies(
                    session,
                    project_id=project_id,
                    clips=clips,
                    source_kind=source_kind,
                    source_record_id=source_record_id,
                    previous_fingerprint=previous_fingerprint,
                    current_fingerprint=current_fingerprint,
                    reason_code=reason_code,
                    rationale=rationale,
                )
            )
            audition_session.state = "invalidated"
        return invalidated_clip_ids

    # Workspace snapshot ---------------------------------------------------------

    def _approved_cast_assignment_ids(
        self,
        session: Session,
        *,
        cast_snapshot: ApprovedCastSnapshotRow,
        casting_run: CastingRunRow,
    ) -> tuple[str, ...]:
        cache_key = (
            "phase3b-approved-cast-assignment-ids",
            cast_snapshot.id,
            cast_snapshot.snapshot_fingerprint,
            casting_run.id,
        )
        cached = session.info.get(cache_key)
        if isinstance(cached, tuple) and all(isinstance(value, str) for value in cached):
            return cached
        manifest = self._casting.validated_snapshot_manifest(
            session,
            snapshot=cast_snapshot,
            run=casting_run,
        )
        assignment_ids = manifest.get("assignmentIds")
        if not isinstance(assignment_ids, list) or not all(
            isinstance(value, str) and value for value in assignment_ids
        ):
            raise ServiceError(
                500,
                "CASTING_SNAPSHOT_MANIFEST_INVALID",
                "The approved cast snapshot assignment manifest is invalid.",
            )
        result = tuple(assignment_ids)
        session.info[cache_key] = result
        return result

    def _current_cast_authority(
        self,
        session: Session,
        project_id: str,
    ) -> _CurrentCastAuthority | None:
        cache_key = ("phase3b-current-cast-authority", project_id)
        if cache_key in session.info:
            return cast(_CurrentCastAuthority | None, session.info[cache_key])
        casting_run = session.scalar(
            select(CastingRunRow)
            .where(
                CastingRunRow.project_id == project_id,
                CastingRunRow.state == "succeeded",
            )
            .order_by(CastingRunRow.created_at.desc(), CastingRunRow.id.desc())
            .limit(1)
        )
        cast_snapshot = (
            session.scalar(
                select(ApprovedCastSnapshotRow)
                .where(ApprovedCastSnapshotRow.casting_run_id == casting_run.id)
                .order_by(
                    ApprovedCastSnapshotRow.revision.desc(),
                    ApprovedCastSnapshotRow.id.desc(),
                )
                .limit(1)
            )
            if casting_run is not None
            else None
        )
        if casting_run is None or cast_snapshot is None:
            session.info[cache_key] = None
            return None
        assignment_ids = self._approved_cast_assignment_ids(
            session,
            cast_snapshot=cast_snapshot,
            casting_run=casting_run,
        )
        assignments = list(
            session.scalars(
                select(CastAssignmentRow)
                .where(CastAssignmentRow.id.in_(assignment_ids))
                .order_by(CastAssignmentRow.role_id, CastAssignmentRow.id)
            )
        )
        assignments_by_role = {row.role_id: row for row in assignments}
        if (
            {row.id for row in assignments} != set(assignment_ids)
            or len(assignments_by_role) != len(assignments)
            or any(
                row.project_id != project_id
                or row.casting_run_id != casting_run.id
                or row.assignment_state not in {"selected", "locked"}
                or row.voice_profile_record_id is None
                for row in assignments
            )
        ):
            raise ServiceError(
                500,
                "CASTING_SNAPSHOT_MANIFEST_INVALID",
                "The current approved cast authority failed verification.",
            )
        rights_current = self._casting.snapshot_assignment_evidence_is_current(
            session,
            snapshot=cast_snapshot,
            assignments=assignments,
        )
        decisions = self._casting.validated_phase3a_gate_decisions(
            session,
            snapshot=cast_snapshot,
            run=casting_run,
        )
        phase3a_decision_ids = (
            tuple(decisions[gate_id].id for gate_id in _PHASE3A_GATE_IDS)
            if decisions is not None and rights_current
            else None
        )
        authority = _CurrentCastAuthority(
            casting_run=casting_run,
            cast_snapshot=cast_snapshot,
            assignments_by_role=assignments_by_role,
            phase3a_decision_ids=phase3a_decision_ids,
            rights_current=rights_current,
        )
        session.info[cache_key] = authority
        return authority

    @staticmethod
    def _current_cast_authority_fingerprint(
        authority: _CurrentCastAuthority | None,
    ) -> str:
        return request_fingerprint(
            {
                "assignmentIds": (
                    sorted(row.id for row in authority.assignments_by_role.values())
                    if authority is not None
                    else []
                ),
                "castSnapshotFingerprint": (
                    authority.cast_snapshot.snapshot_fingerprint if authority is not None else None
                ),
                "castSnapshotId": authority.cast_snapshot.id if authority is not None else None,
                "castSnapshotRevision": (
                    authority.cast_snapshot.revision if authority is not None else None
                ),
                "castingRunId": authority.casting_run.id if authority is not None else None,
                "phase3aGateDecisionIds": (
                    list(authority.phase3a_decision_ids)
                    if authority is not None and authority.phase3a_decision_ids is not None
                    else None
                ),
            }
        )

    @staticmethod
    def _latest_current_audition_session(
        session: Session,
        authority: _CurrentCastAuthority,
    ) -> AuditionSessionRow | None:
        if authority.phase3a_decision_ids is None or not authority.assignments_by_role:
            return None
        audition_session = session.scalar(
            select(AuditionSessionRow)
            .where(
                AuditionSessionRow.project_id == authority.casting_run.project_id,
                AuditionSessionRow.casting_run_id == authority.casting_run.id,
                AuditionSessionRow.cast_snapshot_id == authority.cast_snapshot.id,
                AuditionSessionRow.cast_snapshot_revision == authority.cast_snapshot.revision,
                AuditionSessionRow.cast_snapshot_fingerprint
                == authority.cast_snapshot.snapshot_fingerprint,
                AuditionSessionRow.phase3a_gate_decision_ids_json
                == canonical_json(list(authority.phase3a_decision_ids)),
                AuditionSessionRow.assignment_id.in_(
                    row.id for row in authority.assignments_by_role.values()
                ),
            )
            .order_by(AuditionSessionRow.created_at.desc(), AuditionSessionRow.id.desc())
            .limit(1)
        )
        if audition_session is None:
            return None
        assignment = authority.assignments_by_role.get(audition_session.role_id)
        if (
            assignment is None
            or assignment.id != audition_session.assignment_id
            or assignment.revision != audition_session.assignment_revision
        ):
            return None
        return audition_session

    def _current_role_audition_evidence(
        self,
        session: Session,
        *,
        authority: _CurrentCastAuthority,
        role_id: str,
    ) -> _CurrentRoleAuditionEvidence:
        assignment = authority.assignments_by_role.get(role_id)
        if assignment is None or authority.phase3a_decision_ids is None:
            return _CurrentRoleAuditionEvidence(None, None, None, None)
        audition_session = session.scalar(
            select(AuditionSessionRow)
            .where(
                AuditionSessionRow.project_id == authority.casting_run.project_id,
                AuditionSessionRow.casting_run_id == authority.casting_run.id,
                AuditionSessionRow.cast_snapshot_id == authority.cast_snapshot.id,
                AuditionSessionRow.cast_snapshot_revision == authority.cast_snapshot.revision,
                AuditionSessionRow.cast_snapshot_fingerprint
                == authority.cast_snapshot.snapshot_fingerprint,
                AuditionSessionRow.phase3a_gate_decision_ids_json
                == canonical_json(list(authority.phase3a_decision_ids)),
                AuditionSessionRow.role_id == role_id,
                AuditionSessionRow.assignment_id == assignment.id,
                AuditionSessionRow.assignment_revision == assignment.revision,
            )
            .order_by(AuditionSessionRow.created_at.desc(), AuditionSessionRow.id.desc())
            .limit(1)
        )
        if audition_session is None:
            return _CurrentRoleAuditionEvidence(None, None, None, None)
        clip = session.scalar(
            select(AuditionClipRow)
            .where(
                AuditionClipRow.project_id == authority.casting_run.project_id,
                AuditionClipRow.session_id == audition_session.id,
                AuditionClipRow.role_id == role_id,
                AuditionClipRow.assignment_id == assignment.id,
                AuditionClipRow.assignment_revision == assignment.revision,
            )
            .order_by(AuditionClipRow.created_at.desc(), AuditionClipRow.id.desc())
            .limit(1)
        )
        if clip is None:
            return _CurrentRoleAuditionEvidence(audition_session, None, None, None)
        review = session.scalar(
            select(AuditionReviewRecordRow)
            .where(
                AuditionReviewRecordRow.project_id == authority.casting_run.project_id,
                AuditionReviewRecordRow.gate_id == "per_role_audition_review",
                AuditionReviewRecordRow.scope_key == role_id,
                AuditionReviewRecordRow.role_id == role_id,
                AuditionReviewRecordRow.session_id == audition_session.id,
                AuditionReviewRecordRow.clip_id == clip.id,
            )
            .order_by(
                AuditionReviewRecordRow.revision.desc(),
                AuditionReviewRecordRow.id.desc(),
            )
            .limit(1)
        )
        decision = (
            session.scalar(
                select(AuditionReviewDecisionRow)
                .where(
                    AuditionReviewDecisionRow.project_id == authority.casting_run.project_id,
                    AuditionReviewDecisionRow.gate_id == "per_role_audition_review",
                    AuditionReviewDecisionRow.scope_key == role_id,
                )
                .order_by(
                    AuditionReviewDecisionRow.revision.desc(),
                    AuditionReviewDecisionRow.id.desc(),
                )
                .limit(1)
            )
            if review is not None
            else None
        )
        return _CurrentRoleAuditionEvidence(audition_session, clip, review, decision)

    def _approved_current_role_audition_evidence(
        self,
        session: Session,
        *,
        authority: _CurrentCastAuthority,
        role_id: str,
    ) -> _CurrentRoleAuditionEvidence | None:
        current = self._current_role_audition_evidence(
            session,
            authority=authority,
            role_id=role_id,
        )
        audition_session = current.audition_session
        clip = current.clip
        review = current.review
        decision = current.decision
        review_evidence = parse_json(review.evidence_json, {}) if review is not None else {}
        listening_evidence_current = False
        if (
            audition_session is not None
            and clip is not None
            and review is not None
            and decision is not None
        ):
            if audition_session.provider_id == KOKORO_PROVIDER_ID:
                listening_evidence_current = self._current_listening_attestation_is_valid(
                    session,
                    review=review,
                    decision=decision,
                    audition_session=audition_session,
                    clip=clip,
                )
            else:
                try:
                    listening_evidence_current = (
                        self._stored_listening_attestation(decision) is None
                    )
                except ServiceError:
                    listening_evidence_current = False
        if (
            authority.phase3a_decision_ids is None
            or audition_session is None
            or audition_session.state == "invalidated"
            or clip is None
            or review is None
            or not review.eligible
            or review.session_id != audition_session.id
            or review.clip_id != clip.id
            or review.required_decision_ids_json
            != canonical_json(list(authority.phase3a_decision_ids))
            or not isinstance(review_evidence, dict)
            or review_evidence.get("approvedCastSnapshotFingerprint")
            != authority.cast_snapshot.snapshot_fingerprint
            or review_evidence.get("auditionSessionId") != audition_session.id
            or review_evidence.get("auditionClipId") != clip.id
            or decision is None
            or decision.review_record_id != review.id
            or decision.decision != "approved"
            or decision.evidence_fingerprint != review.evidence_fingerprint
            or not listening_evidence_current
            or self._session_dependency_drift(session, audition_session) is not None
        ):
            return None
        return current

    def workspace_snapshot(
        self,
        project_id: str,
        *,
        role_cursor: str | None = None,
        role_limit: int = DEFAULT_AUDITION_PAGE_SIZE,
    ) -> dict[str, Any]:
        self._reap_idle_runtimes()
        with self._runtime_lock:
            live_runtime_instance_ids = frozenset(
                runtime_instance_id
                for runtime, runtime_instance_id in self._runtimes.values()
                if runtime.is_running and runtime.last_exit is None
            )
        role_page_size = _bounded_page(role_limit)
        with self.database.immediate_session() as session:
            project = self._require_project(session, project_id)
            self._reconcile_project_evidence(session, project_id)
            dictionary = self._ensure_empty_dictionary(session, project_id)
            (
                pronunciation_review,
                narrator_review,
                character_review,
                readiness_snapshot,
            ) = self._refresh_reviews(session, project_id)
            analysis_run = session.scalar(
                select(AnalysisRunRow)
                .where(AnalysisRunRow.project_id == project_id)
                .order_by(AnalysisRunRow.created_at.desc(), AnalysisRunRow.id.desc())
                .limit(1)
            )
            casting_run = session.scalar(
                select(CastingRunRow)
                .where(
                    CastingRunRow.project_id == project_id,
                    CastingRunRow.state == "succeeded",
                )
                .order_by(CastingRunRow.created_at.desc(), CastingRunRow.id.desc())
                .limit(1)
            )
            cast_snapshot = (
                session.scalar(
                    select(ApprovedCastSnapshotRow)
                    .where(ApprovedCastSnapshotRow.casting_run_id == casting_run.id)
                    .order_by(
                        ApprovedCastSnapshotRow.revision.desc(),
                        ApprovedCastSnapshotRow.id.desc(),
                    )
                    .limit(1)
                )
                if casting_run is not None
                else None
            )
            phase2 = self._latest_phase2_decisions(session, analysis_run)
            phase3a = self._latest_phase3a_decisions(session, casting_run)
            authority = self._current_cast_authority(session, project_id)
            phase3a_authority_current = (
                authority is not None
                and casting_run is not None
                and cast_snapshot is not None
                and authority.casting_run.id == casting_run.id
                and authority.cast_snapshot.id == cast_snapshot.id
                and authority.phase3a_decision_ids is not None
            )
            import_review = session.scalar(
                select(ImportReviewRow)
                .where(ImportReviewRow.project_id == project_id)
                .order_by(
                    ImportReviewRow.created_at.desc(),
                    ImportReviewRow.revision.desc(),
                    ImportReviewRow.id.desc(),
                )
                .limit(1)
            )
            (
                role_items,
                role_reviews,
                next_role_cursor,
                role_total,
                assignments_current,
                rights_current,
            ) = self._workspace_roles(
                session,
                project_id=project_id,
                casting_run=casting_run,
                cast_snapshot=cast_snapshot,
                dictionary=dictionary,
                phase2=phase2,
                phase3a=phase3a,
                cursor=role_cursor,
                limit=role_page_size,
            )
            prerequisites = self._workspace_prerequisites(
                import_review=import_review,
                phase2=phase2,
                phase3a=phase3a,
                cast_snapshot=cast_snapshot,
                phase3a_authority_current=phase3a_authority_current,
                role_count=role_total,
                assignments_current=assignments_current,
                rights_current=rights_current,
            )
            manifests = list(
                session.scalars(
                    select(ModelPackageManifestRow).order_by(
                        ModelPackageManifestRow.provider_id,
                        ModelPackageManifestRow.id,
                    )
                )
            )
            installations: list[ModelInstallationRow] = []
            for manifest in manifests:
                installation_row = self._latest_installation(session, manifest.id)
                if installation_row is not None:
                    installations.append(installation_row)
            verifications: list[ModelVerificationRow] = []
            for installation in installations:
                verification_row = self._latest_verification(
                    session,
                    installation.installation_id,
                )
                if verification_row is not None:
                    verifications.append(verification_row)
            manifests_by_id = {manifest.id: manifest for manifest in manifests}
            current_profile_record_ids = {
                identity[0]
                for provider_id in (FIXTURE_PROVIDER_ID, KOKORO_PROVIDER_ID)
                if (identity := _current_runtime_profile_identity(provider_id)) is not None
            }
            profiles = list(
                session.scalars(
                    select(SpeechRuntimeProfileRow).where(SpeechRuntimeProfileRow.active.is_(True))
                )
            )
            profiles.sort(
                key=lambda profile: (
                    profile.provider_id,
                    0 if profile.id in current_profile_record_ids else 1,
                    profile.id,
                )
            )
            current_profiles = [
                profile for profile in profiles if profile.id in current_profile_record_ids
            ]
            global_reviews = [
                review
                for review in (narrator_review, character_review, pronunciation_review)
                if review is not None
            ]
            display_readiness_snapshot = readiness_snapshot
            readiness_review = (
                session.scalar(
                    select(VoiceReadinessReviewRow)
                    .where(
                        VoiceReadinessReviewRow.project_id == project_id,
                        VoiceReadinessReviewRow.snapshot_id == display_readiness_snapshot.id,
                    )
                    .order_by(
                        VoiceReadinessReviewRow.revision.desc(),
                        VoiceReadinessReviewRow.id.desc(),
                    )
                    .limit(1)
                )
                if display_readiness_snapshot is not None
                else None
            )
            reviews = [
                self._review_wire(session, review) for review in (*role_reviews, *global_reviews)
            ]
            if readiness_review is not None:
                reviews.append(self._readiness_review_wire(session, readiness_review))
            return {
                "contractVersion": "1.0.0",
                "projectId": project_id,
                "prerequisites": prerequisites,
                "approvedCastSnapshot": (
                    {
                        "snapshotId": cast_snapshot.id,
                        "revision": cast_snapshot.revision,
                        "fingerprint": cast_snapshot.snapshot_fingerprint,
                    }
                    if cast_snapshot is not None and phase3a_authority_current
                    else None
                ),
                "providers": self._provider_descriptors_wire(session),
                "voiceInventory": _governed_voice_inventory(),
                "runtimeProfiles": [
                    self._runtime_profile_wire(profile, manifests) for profile in profiles
                ],
                "runtimeHealth": [
                    self._runtime_health_wire(
                        session,
                        profile,
                        installations,
                        verifications,
                        live_runtime_instance_ids=live_runtime_instance_ids,
                    )
                    for profile in current_profiles
                ],
                "runtimeInstances": self._runtime_instances_wire(
                    session,
                    project_id,
                ),
                "modelInstallations": [
                    self._installation_wire(
                        manifests_by_id[row.manifest_id],
                        row,
                    )
                    for row in installations
                    if row.manifest_id in manifests_by_id
                ],
                "modelVerifications": [
                    self._verification_wire(
                        manifests_by_id[row.manifest_id],
                        row,
                    )
                    for row in verifications
                    if row.manifest_id in manifests_by_id
                ],
                "currentDictionary": self._dictionary_wire(
                    session,
                    project_id,
                    dictionary,
                ),
                "roles": {
                    "items": role_items,
                    "pageSize": len(role_items),
                    "total": role_total,
                    **({"nextCursor": next_role_cursor} if next_role_cursor is not None else {}),
                },
                "reviews": reviews,
                "voiceReadinessSnapshot": (
                    self._readiness_snapshot_wire(session, display_readiness_snapshot)
                    if display_readiness_snapshot is not None
                    else None
                ),
                "updatedAt": project.updated_at,
            }

    @staticmethod
    def _latest_phase2_decisions(
        session: Session,
        run: AnalysisRunRow | None,
    ) -> dict[str, AnalysisReviewDecisionRow | None]:
        return {
            gate_id: (
                session.scalar(
                    select(AnalysisReviewDecisionRow)
                    .where(
                        AnalysisReviewDecisionRow.run_id == run.id,
                        AnalysisReviewDecisionRow.gate_id == gate_id,
                    )
                    .order_by(
                        AnalysisReviewDecisionRow.revision.desc(),
                        AnalysisReviewDecisionRow.id.desc(),
                    )
                    .limit(1)
                )
                if run is not None
                else None
            )
            for gate_id in _PHASE2_GATE_IDS
        }

    @staticmethod
    def _latest_phase3a_decisions(
        session: Session,
        run: CastingRunRow | None,
    ) -> dict[str, CastingGateDecisionRow | None]:
        return {
            gate_id: (
                session.scalar(
                    select(CastingGateDecisionRow)
                    .where(
                        CastingGateDecisionRow.casting_run_id == run.id,
                        CastingGateDecisionRow.gate_id == gate_id,
                    )
                    .order_by(
                        CastingGateDecisionRow.revision.desc(),
                        CastingGateDecisionRow.id.desc(),
                    )
                    .limit(1)
                )
                if run is not None
                else None
            )
            for gate_id in _PHASE3A_GATE_IDS
        }

    def _workspace_prerequisites(
        self,
        *,
        import_review: ImportReviewRow | None,
        phase2: Mapping[str, AnalysisReviewDecisionRow | None],
        phase3a: Mapping[str, CastingGateDecisionRow | None],
        cast_snapshot: ApprovedCastSnapshotRow | None,
        phase3a_authority_current: bool,
        role_count: int,
        assignments_current: bool,
        rights_current: bool,
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []

        def add(
            prerequisite_id: str,
            current: bool,
            status_code: str,
            evidence_id: str | None,
            evidence_fingerprint: str | None,
        ) -> None:
            values.append(
                {
                    "prerequisiteId": prerequisite_id,
                    "current": current,
                    "statusCode": status_code,
                    "evidenceId": evidence_id,
                    "evidenceFingerprint": evidence_fingerprint,
                }
            )

        add(
            "import_review",
            import_review is not None and import_review.state == "approved",
            (
                "CURRENT"
                if import_review is not None and import_review.state == "approved"
                else "APPROVAL_REQUIRED"
            ),
            import_review.id if import_review is not None else None,
            import_review.evidence_fingerprint if import_review is not None else None,
        )
        phase2_names = {
            "story_structure_review": "phase2_story_structure_review",
            "character_registry_review": "phase2_character_registry_review",
            "dialogue_attribution_review": "phase2_dialogue_attribution_review",
            "whole_book_analysis_review": "phase2_whole_book_analysis_review",
        }
        for gate_id, prerequisite_id in phase2_names.items():
            phase2_decision = phase2.get(gate_id)
            current = (
                phase2_decision is not None
                and phase2_decision.state == "approved"
                and phase2_decision.eligible
            )
            add(
                prerequisite_id,
                current,
                "CURRENT" if current else "APPROVAL_REQUIRED",
                phase2_decision.id if phase2_decision is not None else None,
                (phase2_decision.evidence_fingerprint if phase2_decision is not None else None),
            )
        phase3a_names = {
            "narrator_casting_review": "phase3a_narrator_casting_review",
            "character_casting_review": "phase3a_character_casting_review",
            "complete_cast_review": "phase3a_complete_cast_review",
        }
        for gate_id, prerequisite_id in phase3a_names.items():
            phase3a_decision = phase3a.get(gate_id)
            current = (
                phase3a_authority_current
                and phase3a_decision is not None
                and phase3a_decision.decision == "approved"
                and cast_snapshot is not None
                and phase3a_decision.cast_snapshot_id == cast_snapshot.id
            )
            add(
                prerequisite_id,
                current,
                "CURRENT" if current else "APPROVAL_REQUIRED",
                phase3a_decision.id if phase3a_decision is not None else None,
                (phase3a_decision.evidence_fingerprint if phase3a_decision is not None else None),
            )
        add(
            "approved_cast_snapshot",
            cast_snapshot is not None and phase3a_authority_current,
            (
                "CURRENT"
                if cast_snapshot is not None and phase3a_authority_current
                else "SNAPSHOT_APPROVAL_REQUIRED"
                if cast_snapshot is not None
                else "SNAPSHOT_REQUIRED"
            ),
            (cast_snapshot.id if cast_snapshot is not None and phase3a_authority_current else None),
            (
                cast_snapshot.snapshot_fingerprint
                if cast_snapshot is not None and phase3a_authority_current
                else None
            ),
        )
        add(
            "voice_rights",
            role_count > 0 and rights_current,
            "CURRENT" if rights_current else "RIGHTS_REQUIRED",
            cast_snapshot.id if rights_current and cast_snapshot is not None else None,
            (
                cast_snapshot.snapshot_fingerprint
                if rights_current and cast_snapshot is not None
                else None
            ),
        )
        add(
            "voice_assignment",
            role_count > 0 and assignments_current,
            "CURRENT" if assignments_current else "ASSIGNMENT_REQUIRED",
            cast_snapshot.id if assignments_current and cast_snapshot is not None else None,
            cast_snapshot.snapshot_fingerprint
            if assignments_current and cast_snapshot is not None
            else None,
        )
        return values

    def _workspace_roles(
        self,
        session: Session,
        *,
        project_id: str,
        casting_run: CastingRunRow | None,
        cast_snapshot: ApprovedCastSnapshotRow | None,
        dictionary: PronunciationDictionaryRow,
        phase2: Mapping[str, AnalysisReviewDecisionRow | None],
        phase3a: Mapping[str, CastingGateDecisionRow | None],
        cursor: str | None,
        limit: int,
    ) -> tuple[
        list[dict[str, Any]],
        list[AuditionReviewRecordRow],
        str | None,
        int,
        bool,
        bool,
    ]:
        del phase2
        if casting_run is None or cast_snapshot is None:
            if cursor is not None:
                _decode_cursor(cursor, binding="no-current-cast-snapshot")
            return [], [], None, 0, False, False
        authority = self._current_cast_authority(session, project_id)
        if (
            authority is None
            or authority.casting_run.id != casting_run.id
            or authority.cast_snapshot.id != cast_snapshot.id
        ):
            raise ServiceError(
                500,
                "CASTING_SNAPSHOT_MANIFEST_INVALID",
                "The workspace cast authority is not current.",
            )
        snapshot_assignment_ids = self._approved_cast_assignment_ids(
            session,
            cast_snapshot=cast_snapshot,
            casting_run=casting_run,
        )
        snapshot_assignments = list(authority.assignments_by_role.values())
        assignments_by_role = {value.role_id: value for value in snapshot_assignments}
        if (
            {value.id for value in snapshot_assignments} != set(snapshot_assignment_ids)
            or len(assignments_by_role) != len(snapshot_assignments)
            or any(
                value.project_id != project_id
                or value.casting_run_id != casting_run.id
                or value.assignment_state not in {"selected", "locked"}
                or value.voice_profile_record_id is None
                for value in snapshot_assignments
            )
        ):
            raise ServiceError(
                500,
                "CASTING_SNAPSHOT_MANIFEST_INVALID",
                "The approved cast snapshot assignments failed verification.",
            )
        snapshot_role_ids = tuple(sorted(assignments_by_role))
        role_filters = (
            ProductionRoleRow.project_id == project_id,
            ProductionRoleRow.casting_run_id == casting_run.id,
            ProductionRoleRow.status.in_(("active", "unresolved")),
            ProductionRoleRow.id.in_(snapshot_role_ids),
        )
        total = int(
            session.scalar(select(func.count()).select_from(ProductionRoleRow).where(*role_filters))
            or 0
        )
        if total != len(snapshot_assignments):
            raise ServiceError(
                500,
                "CASTING_SNAPSHOT_MANIFEST_INVALID",
                "The approved cast snapshot roles failed verification.",
            )
        prior_assignment = aliased(CastAssignmentRow)
        latest_assignment_revision = (
            select(func.max(prior_assignment.revision))
            .where(prior_assignment.role_id == CastAssignmentRow.role_id)
            .correlate(CastAssignmentRow)
            .scalar_subquery()
        )
        assignment_count = int(
            session.scalar(
                select(func.count(func.distinct(ProductionRoleRow.id)))
                .select_from(ProductionRoleRow)
                .join(CastAssignmentRow, CastAssignmentRow.role_id == ProductionRoleRow.id)
                .where(
                    *role_filters,
                    CastAssignmentRow.id.in_(snapshot_assignment_ids),
                    CastAssignmentRow.revision == latest_assignment_revision,
                    CastAssignmentRow.assignment_state.in_(("selected", "locked")),
                    CastAssignmentRow.voice_profile_record_id.is_not(None),
                    ~select(CastAssignmentInvalidationRow.id)
                    .where(CastAssignmentInvalidationRow.assignment_id == CastAssignmentRow.id)
                    .exists(),
                )
            )
            or 0
        )
        prior_rights = aliased(VoiceRightsRecordRow)
        latest_rights_revision = (
            select(func.max(prior_rights.revision))
            .where(
                prior_rights.voice_profile_record_id == VoiceRightsRecordRow.voice_profile_record_id
            )
            .correlate(VoiceRightsRecordRow)
            .scalar_subquery()
        )
        rights_count = int(
            session.scalar(
                select(func.count(func.distinct(ProductionRoleRow.id)))
                .select_from(ProductionRoleRow)
                .join(CastAssignmentRow, CastAssignmentRow.role_id == ProductionRoleRow.id)
                .join(
                    VoiceRightsRecordRow,
                    VoiceRightsRecordRow.voice_profile_record_id
                    == CastAssignmentRow.voice_profile_record_id,
                )
                .where(
                    *role_filters,
                    CastAssignmentRow.id.in_(snapshot_assignment_ids),
                    CastAssignmentRow.revision == latest_assignment_revision,
                    CastAssignmentRow.assignment_state.in_(("selected", "locked")),
                    VoiceRightsRecordRow.revision == latest_rights_revision,
                    VoiceRightsRecordRow.rights_state.in_(("verified", "restricted")),
                )
            )
            or 0
        )
        ordered_role_generation = [
            [role_id, ordinal, role_fingerprint]
            for role_id, ordinal, role_fingerprint in session.execute(
                select(
                    ProductionRoleRow.id,
                    ProductionRoleRow.ordinal,
                    ProductionRoleRow.role_fingerprint,
                )
                .where(*role_filters)
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
            )
        ]

        def evidence_generation(model: Any) -> list[Any]:
            count, latest_created_at, latest_id = session.execute(
                select(func.count(), func.max(model.created_at), func.max(model.id)).where(
                    model.project_id == project_id
                )
            ).one()
            return [int(count or 0), latest_created_at, latest_id]

        binding = request_fingerprint(
            {
                "approvedCastSnapshotFingerprint": cast_snapshot.snapshot_fingerprint,
                "approvedCastSnapshotId": cast_snapshot.id,
                "approvedCastSnapshotRevision": cast_snapshot.revision,
                "assignmentCount": assignment_count,
                "auditionClipGeneration": evidence_generation(AuditionClipRow),
                "auditionReviewGeneration": evidence_generation(AuditionReviewRecordRow),
                "auditionSessionGeneration": evidence_generation(AuditionSessionRow),
                "castingRunId": casting_run.id,
                "dictionaryFingerprint": dictionary.dictionary_fingerprint,
                "orderedRoleGeneration": ordered_role_generation,
                "projectId": project_id,
                "rightsCount": rights_count,
                "type": "audition-workspace-roles",
            }
        )
        offset = _decode_cursor(cursor, binding=binding)
        if offset > total:
            raise ServiceError(400, "INVALID_CURSOR", "The pagination cursor is invalid.")
        rows = list(
            session.scalars(
                select(ProductionRoleRow)
                .where(*role_filters)
                .order_by(ProductionRoleRow.ordinal, ProductionRoleRow.id)
                .offset(offset)
                .limit(limit)
            )
        )
        result: list[dict[str, Any]] = []
        current_role_reviews: list[AuditionReviewRecordRow] = []
        all_phase3a_approved = all(
            decision is not None and decision.decision == "approved"
            for decision in phase3a.values()
        ) and authority.phase3a_decision_ids == tuple(
            cast(CastingGateDecisionRow, phase3a[gate_id]).id for gate_id in _PHASE3A_GATE_IDS
        )
        provider_bindings = {
            provider_id: self._active_provider_binding(session, provider_id)
            for provider_id in (FIXTURE_PROVIDER_ID, KOKORO_PROVIDER_ID)
        }
        for role in rows:
            assignment = assignments_by_role.get(role.id)
            if assignment is None or assignment.voice_profile_record_id is None:
                raise ServiceError(
                    500,
                    "AUDITION_ROLE_EVIDENCE_MISSING",
                    "The approved role assignment evidence is unavailable.",
                )
            voice = session.get(VoiceProfileRow, assignment.voice_profile_record_id)
            rights = (
                session.scalar(
                    select(VoiceRightsRecordRow)
                    .where(VoiceRightsRecordRow.voice_profile_record_id == voice.id)
                    .order_by(
                        VoiceRightsRecordRow.revision.desc(),
                        VoiceRightsRecordRow.id.desc(),
                    )
                    .limit(1)
                )
                if voice is not None
                else None
            )
            if voice is None or rights is None:
                raise ServiceError(
                    500,
                    "AUDITION_ROLE_EVIDENCE_MISSING",
                    "The approved role voice or rights evidence is unavailable.",
                )
            source_provider = session.get(
                VoiceProviderDescriptorRow,
                voice.provider_descriptor_id,
            )
            governed_local_voice: dict[str, Any] | None = None
            governed_identity_claimed = voice.profile_id == GOVERNED_KOKORO_VOICE_PROFILE_ID or (
                source_provider is not None and source_provider.provider_id == KOKORO_PROVIDER_ID
            )
            if governed_identity_claimed:
                role_catalog = session.get(VoiceCatalogRevisionRow, voice.catalog_revision_id)
                if (
                    source_provider is None
                    or source_provider.provider_id != KOKORO_PROVIDER_ID
                    or role_catalog is None
                    or not self._governed_private_audition_rights_are_exact(
                        session,
                        voice=voice,
                        rights=rights,
                        catalog=role_catalog,
                    )
                ):
                    raise ServiceError(
                        500,
                        "GOVERNED_VOICE_INVENTORY_INVALID",
                        "The assigned governed local voice failed inventory validation.",
                    )
                governed_local_voice = self._governed_inventory_item()
                if (
                    not isinstance(governed_local_voice, dict)
                    or governed_local_voice.get("voiceProfileId") != voice.profile_id
                    or governed_local_voice.get("voiceProfileFingerprint")
                    != voice.profile_fingerprint
                    or governed_local_voice.get("inventoryFingerprint")
                    != _GOVERNED_VOICE_INVENTORY_RECORD_FINGERPRINT
                ):
                    raise ServiceError(
                        500,
                        "GOVERNED_VOICE_INVENTORY_INVALID",
                        "The assigned governed local voice failed inventory projection.",
                    )
            required_runtime_provider_id = (
                FIXTURE_PROVIDER_ID
                if source_provider is not None
                and source_provider.provider_id == "synthetic-local-fixture"
                else KOKORO_PROVIDER_ID
                if governed_local_voice is not None
                else None
            )
            provider_binding = (
                provider_bindings.get(required_runtime_provider_id)
                if required_runtime_provider_id is not None
                else None
            )
            current = self._current_role_audition_evidence(
                session,
                authority=authority,
                role_id=role.id,
            )
            latest_session = current.audition_session
            latest_clip = current.clip
            review = current.review
            review_state = (
                self._review_wire(session, review)["state"] if review is not None else "pending"
            )
            if review is not None:
                current_role_reviews.append(review)
            voice_runtime_binding = (
                self._ensure_voice_runtime_binding(
                    session,
                    voice=voice,
                    manifest=provider_binding[0],
                    runtime_profile=provider_binding[1],
                )
                if provider_binding is not None
                else None
            )
            runtime_binding_status = (
                "compatible"
                if voice_runtime_binding is not None
                else "unavailable"
                if required_runtime_provider_id is not None and provider_binding is None
                else "incompatible"
            )
            runtime_binding_reason_code = (
                None
                if voice_runtime_binding is not None
                else "VERIFIED_ACTIVE_MODEL_PACKAGE_REQUIRED"
                if required_runtime_provider_id is not None and provider_binding is None
                else "VOICE_RUNTIME_BINDING_INCOMPATIBLE"
            )
            evidence = (
                self._session_evidence_for_role(
                    session,
                    project_id=project_id,
                    role=role,
                    assignment=assignment,
                    voice=voice,
                    rights=rights,
                    casting_run=casting_run,
                    cast_snapshot=cast_snapshot,
                    dictionary=dictionary,
                    provider_binding=provider_binding,
                    voice_runtime_binding=voice_runtime_binding,
                )
                if all_phase3a_approved and voice_runtime_binding is not None
                else None
            )
            generation_request = (
                self._generation_request_wire(session, latest_session)
                if latest_session is not None
                else None
            )
            result.append(
                {
                    "roleId": role.id,
                    "roleType": (
                        "narrator"
                        if role.role_type in {"primary_narrator", "secondary_narrator"}
                        else "character"
                    ),
                    "displayLabel": role.effective_display_label,
                    "required": True,
                    "assignmentId": assignment.id,
                    "assignmentRevision": assignment.revision,
                    "voiceProfileId": voice.profile_id,
                    "voiceDisplayLabel": voice.display_label,
                    "governedLocalVoice": governed_local_voice,
                    "rightsState": rights.rights_state,
                    "latestSessionId": latest_session.id if latest_session is not None else None,
                    "latestClipId": latest_clip.id if latest_clip is not None else None,
                    "reviewState": review_state,
                    "voiceRuntimeBinding": (
                        self._voice_runtime_binding_wire(voice_runtime_binding)
                        if voice_runtime_binding is not None
                        else None
                    ),
                    "runtimeBindingStatus": runtime_binding_status,
                    "runtimeBindingReasonCode": runtime_binding_reason_code,
                    "sessionEvidence": evidence,
                    "generationRequest": generation_request,
                }
            )
        next_offset = offset + len(rows)
        next_cursor = _encode_cursor(binding, next_offset) if next_offset < total else None
        return (
            result,
            current_role_reviews,
            next_cursor,
            total,
            total > 0 and assignment_count == total,
            total > 0 and rights_count == total and authority.rights_current,
        )

    def _session_evidence_for_role(
        self,
        session: Session,
        *,
        project_id: str,
        role: ProductionRoleRow,
        assignment: CastAssignmentRow,
        voice: VoiceProfileRow,
        rights: VoiceRightsRecordRow,
        casting_run: CastingRunRow,
        cast_snapshot: ApprovedCastSnapshotRow,
        dictionary: PronunciationDictionaryRow,
        provider_binding: tuple[
            ModelPackageManifestRow,
            SpeechRuntimeProfileRow,
            ModelInstallationRow,
            ModelVerificationRow,
            ModelInstallationRow | None,
        ]
        | None,
        voice_runtime_binding: VoiceRuntimeBindingRow | None,
    ) -> dict[str, Any] | None:
        if provider_binding is None or voice_runtime_binding is None:
            return None
        manifest, profile, _installation, _verification, _acknowledgement = provider_binding
        catalog = session.get(VoiceCatalogRevisionRow, casting_run.catalog_revision_id)
        if catalog is None:
            return None
        evidence = {
            "projectId": project_id,
            "sourceDocumentId": casting_run.source_document_id,
            "sourceRevision": casting_run.source_revision,
            "extractionId": casting_run.extraction_id,
            "extractionRevision": casting_run.extraction_revision,
            "extractedTextSha256": casting_run.extracted_text_sha256,
            "phase2RunId": casting_run.analysis_run_id,
            "phase2SnapshotId": casting_run.analysis_snapshot_id,
            "phase2SnapshotRevision": casting_run.analysis_snapshot_revision,
            "phase2SnapshotFingerprint": casting_run.analysis_snapshot_fingerprint,
            "phase2CorrectionSetFingerprint": (casting_run.analysis_correction_set_fingerprint),
            "castingRunId": casting_run.id,
            "approvedCastSnapshotId": cast_snapshot.id,
            "approvedCastSnapshotRevision": cast_snapshot.revision,
            "approvedCastSnapshotFingerprint": cast_snapshot.snapshot_fingerprint,
            "castAssignmentId": assignment.id,
            "castAssignmentRevision": assignment.revision,
            "voiceProfileId": voice.profile_id,
            "voiceProfileVersion": voice.profile_version,
            "voiceRuntimeBindingId": voice_runtime_binding.id,
            "voiceRuntimeBindingFingerprint": voice_runtime_binding.binding_fingerprint,
            "providerVoiceId": voice_runtime_binding.provider_voice_id,
            "providerId": manifest.provider_id,
            "providerVersion": manifest.provider_version,
            "modelId": manifest.model_id,
            "modelVersion": manifest.model_version,
            "catalogRevisionId": catalog.catalog_id,
            "catalogFingerprint": catalog.catalog_fingerprint,
            "rightsRecordId": rights.rights_record_id,
            "rightsRecordRevision": rights.revision,
            "rightsRecordFingerprint": rights.rights_fingerprint,
            "pronunciationDictionaryId": dictionary.dictionary_id,
            "pronunciationDictionaryRevision": dictionary.revision,
            "pronunciationDictionaryFingerprint": dictionary.dictionary_fingerprint,
            "runtimeProfileId": profile.profile_id,
            "runtimeProfileFingerprint": profile.profile_fingerprint,
            "modelPackageId": manifest.package_id,
            "modelPackageFingerprint": manifest.manifest_fingerprint,
            "producerVersion": _PRODUCER_VERSION,
        }
        try:
            self._validate_session_evidence(
                session,
                project_id=project_id,
                role_id=role.id,
                evidence=evidence,
            )
        except ServiceError:
            return None
        return evidence

    def _provider_invocation_binding(
        self,
        session: Session,
        audition_session: AuditionSessionRow,
    ) -> dict[str, Any]:
        voice_runtime_binding = self._assert_session_voice_runtime_binding(
            session,
            audition_session,
        )
        manifest = session.get(
            ModelPackageManifestRow,
            audition_session.model_manifest_id,
        )
        installation = session.get(
            ModelInstallationRow,
            audition_session.model_installation_record_id,
        )
        if (
            manifest is None
            or installation is None
            or installation.manifest_id != manifest.id
            or manifest.provider_id != audition_session.provider_id
            or manifest.manifest_fingerprint != audition_session.model_package_fingerprint
        ):
            raise ServiceError(
                409,
                "AUDITION_PROVIDER_BINDING_INVALID",
                "The frozen audition provider binding failed verification.",
            )
        if manifest.provider_id == FIXTURE_PROVIDER_ID:
            return {
                "providerLanguage": "en-US",
                "providerVoiceId": voice_runtime_binding.provider_voice_id,
                "restrictedLocalUseAcknowledged": False,
                "restrictedLocalUseAcknowledgementEventId": None,
                "voiceRuntimeBindingFingerprint": (voice_runtime_binding.binding_fingerprint),
                "voiceRuntimeBindingId": voice_runtime_binding.id,
            }
        if (
            manifest.provider_id != KOKORO_PROVIDER_ID
            or manifest.package_id != KOKORO_LOCAL_ONNX_MANIFEST.package_id
            or manifest.manifest_fingerprint != KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
            or voice_runtime_binding.provider_voice_id != KOKORO_LOCAL_ONNX_MANIFEST.voice_id
        ):
            raise ServiceError(
                409,
                "AUDITION_PROVIDER_BINDING_INVALID",
                "The frozen audition provider binding is not trusted.",
            )
        acknowledgement = self._restricted_local_use_acknowledgement(
            session,
            installation,
        )
        if acknowledgement is None:
            raise ServiceError(
                409,
                "AUDITION_RESTRICTED_MODEL_ACKNOWLEDGEMENT_MISSING",
                "The restricted local model acknowledgement is unavailable.",
            )
        governed_activation = self._governed_local_voice_activation_wire(
            session,
            audition_session,
        )
        if governed_activation is None:
            raise ServiceError(
                409,
                "AUDITION_ACTIVATION_EVIDENCE_INVALID",
                "The governed real-voice activation is unavailable.",
            )
        return {
            "providerLanguage": "en-US",
            "providerVoiceId": voice_runtime_binding.provider_voice_id,
            "restrictedLocalUseAcknowledged": True,
            "restrictedLocalUseAcknowledgementEventId": acknowledgement.id,
            "voiceRuntimeBindingFingerprint": voice_runtime_binding.binding_fingerprint,
            "voiceRuntimeBindingId": voice_runtime_binding.id,
        }

    def _generation_request_wire(
        self,
        session: Session,
        row: AuditionSessionRow,
    ) -> dict[str, Any] | None:
        if row.state in {"queued", "generating", "invalidated"}:
            return None
        script = session.scalar(
            select(AuditionScriptRow)
            .where(AuditionScriptRow.session_id == row.id)
            .order_by(AuditionScriptRow.created_at.desc(), AuditionScriptRow.id.desc())
            .limit(1)
        )
        if script is None:
            return None
        plan = session.scalar(
            select(TextNormalizationPlanRow)
            .where(TextNormalizationPlanRow.script_id == script.id)
            .order_by(TextNormalizationPlanRow.revision.desc(), TextNormalizationPlanRow.id.desc())
            .limit(1)
        )
        if plan is None:
            return None
        try:
            normalization = self._validated_normalization_plan(
                audition_session=row,
                script=script,
                plan=plan,
                source_text=self._read_script_text(script),
            )
            if normalization.human_review_required:
                return None
            evidence = self._session_evidence_wire(session, row)
            self._validate_session_evidence(
                session,
                project_id=row.project_id,
                role_id=row.role_id,
                evidence=evidence,
            )
        except ServiceError:
            return None
        controls = {
            "speakingRate": 1.0,
            "pitch": None,
            "style": None,
            "energy": None,
        }
        controls_fingerprint = request_fingerprint(controls)
        request_id = stable_id(
            "phase3b-preview-request",
            row.id,
            script.id,
            plan.plan_fingerprint,
            controls_fingerprint,
        )
        value: dict[str, Any] = {
            "contractVersion": "1.0.0",
            "requestId": request_id,
            "auditionSessionId": row.id,
            "auditionSessionRevision": row.revision,
            "auditionScriptId": script.id,
            "auditionScriptFingerprint": script.script_fingerprint,
            "evidence": evidence,
            "normalizedTextSha256": plan.normalized_text_sha256,
            "normalizationPlanFingerprint": plan.plan_fingerprint,
            "pronunciationPlanFingerprint": plan.pronunciation_plan_fingerprint,
            "providerControls": controls | {"controlsFingerprint": controls_fingerprint},
            "outputFormat": "pcm_s16le_wav",
            "sampleRateHz": 24000,
            "channels": 1,
            "idempotencyKey": f"audition-{request_id}",
        }
        value["requestFingerprint"] = request_fingerprint(value)
        return value

    def _provider_descriptors_wire(self, session: Session) -> list[dict[str, Any]]:
        kokoro_available = self._active_provider_binding(session, KOKORO_PROVIDER_ID) is not None
        values = (
            {
                "providerId": FIXTURE_PROVIDER_ID,
                "providerVersion": FIXTURE_ADAPTER_VERSION,
                "providerClass": "deterministic_fixture",
                "displayName": "Deterministic PCM WAV fixture",
                "deterministic": True,
                "productionExportEligible": False,
                "licenseIdentifier": "repository-test-fixture",
                "commercialUseClassification": "fixture_only",
                "status": "available",
                "statusReasonCode": None,
            },
            {
                "providerId": KOKORO_PROVIDER_ID,
                "providerVersion": KOKORO_ADAPTER_VERSION,
                "providerClass": "real_local",
                "displayName": "Kokoro local ONNX",
                "deterministic": False,
                "productionExportEligible": False,
                "licenseIdentifier": "Apache-2.0",
                "commercialUseClassification": "restricted",
                "status": "available" if kokoro_available else "unavailable",
                "statusReasonCode": (
                    None if kokoro_available else "MANAGED_MODEL_INSTALLATION_REQUIRED"
                ),
            },
        )
        result = []
        for value in values:
            identity = request_fingerprint(value)
            result.append(
                {
                    "contractVersion": "1.0.0",
                    **value,
                    "adapterId": value["providerId"],
                    "adapterVersion": value["providerVersion"],
                    "synthesisImplemented": True,
                    "localOnly": True,
                    "networkRequired": False,
                    "credentialsRequired": False,
                    "supportedLanguages": ["en-US"],
                    "outputFormats": ["pcm_s16le_wav"],
                    "supportedSampleRatesHz": [24000],
                    "attributionRequired": value["providerId"] == KOKORO_PROVIDER_ID,
                    "descriptorFingerprint": identity,
                    "provenance": _public_provenance(_provenance("application")),
                }
            )
        return result

    @staticmethod
    def _runtime_profile_wire(
        row: SpeechRuntimeProfileRow,
        manifests: Sequence[ModelPackageManifestRow],
    ) -> dict[str, Any]:
        fingerprint_material = _runtime_profile_row_fingerprint_material(row)
        limits = fingerprint_material.get("limits")
        if (
            request_fingerprint(fingerprint_material) != row.profile_fingerprint
            or not isinstance(limits, dict)
            or limits.get("maximumRetryAttempts") != 0
        ):
            raise ServiceError(
                500,
                "RUNTIME_PROFILE_EVIDENCE_INVALID",
                "The runtime profile evidence failed exact verification.",
            )
        return {
            "contractVersion": "1.0.0",
            "runtimeProfileId": row.profile_id,
            "revision": 1,
            "runtimeDescriptorId": stable_id(
                "phase3b-runtime-descriptor",
                row.runtime_id,
                row.runtime_version,
            ),
            "providerIds": [row.provider_id],
            "compatibleModelPackageIds": [
                manifest.package_id
                for manifest in manifests
                if manifest.provider_id == row.provider_id
            ],
            "protocolVersion": row.protocol_version,
            "startupDeadlineMilliseconds": row.startup_timeout_ms,
            "requestDeadlineMilliseconds": row.request_timeout_ms,
            "idleShutdownMilliseconds": row.idle_shutdown_ms,
            "maximumRetryAttempts": limits["maximumRetryAttempts"],
            "maximumConcurrentRequests": row.maximum_concurrency,
            "shellUsed": False,
            "networkAccessDuringSynthesis": False,
            "profileFingerprint": row.profile_fingerprint,
            "active": row.active,
            "provenance": _public_provenance(row.provenance_json),
        }

    def _runtime_health_wire(
        self,
        session: Session,
        profile: SpeechRuntimeProfileRow,
        installations: Sequence[ModelInstallationRow],
        verifications: Sequence[ModelVerificationRow],
        *,
        live_runtime_instance_ids: frozenset[str],
    ) -> dict[str, Any]:
        installation = next(
            (
                row
                for row in installations
                if (
                    (manifest := session.get(ModelPackageManifestRow, row.manifest_id)) is not None
                    and manifest.provider_id == profile.provider_id
                    and row.state == "active"
                )
            ),
            None,
        )
        verification = next(
            (
                row
                for row in verifications
                if installation is not None
                and row.installation_id == installation.installation_id
                and row.outcome == "verified"
            ),
            None,
        )
        instance = session.scalar(
            select(SpeechRuntimeInstanceRow)
            .where(
                SpeechRuntimeInstanceRow.runtime_profile_id == profile.id,
                SpeechRuntimeInstanceRow.state.in_(("ready", "busy", "idle")),
            )
            .order_by(
                SpeechRuntimeInstanceRow.started_at.desc(),
                SpeechRuntimeInstanceRow.id.desc(),
            )
            .limit(1)
        )
        live_instance = instance is not None and instance.id in live_runtime_instance_ids
        verified_installation = installation is not None and verification is not None
        available = verified_installation and live_instance
        checked = utc_now()
        return {
            "contractVersion": "1.0.0",
            "runtimeProfileId": profile.profile_id,
            "runtimeProfileFingerprint": profile.profile_fingerprint,
            "runtimeInstanceId": instance.id if available and instance is not None else None,
            "providerId": profile.provider_id,
            "status": "available" if available else "unavailable",
            "reasonCode": (
                "RUNTIME_READY"
                if available
                else "INSTALLED_NOT_LIVE"
                if verified_installation
                else "VERIFIED_ACTIVE_MODEL_PACKAGE_REQUIRED"
            ),
            "checkedAt": checked,
            "expiresAt": checked,
            "modelPackageFingerprint": (
                installation.package_fingerprint if installation is not None else None
            ),
            "protocolVersion": profile.protocol_version,
        }

    @staticmethod
    def _runtime_instances_wire(
        session: Session,
        project_id: str,
    ) -> list[dict[str, Any]]:
        rows = list(
            session.scalars(
                select(SpeechRuntimeInstanceRow)
                .join(
                    SpeechProviderRequestRow,
                    SpeechProviderRequestRow.runtime_instance_id == SpeechRuntimeInstanceRow.id,
                )
                .where(SpeechProviderRequestRow.project_id == project_id)
                .distinct()
                .order_by(
                    SpeechRuntimeInstanceRow.started_at.desc(),
                    SpeechRuntimeInstanceRow.id.desc(),
                )
                .limit(200)
            )
        )
        return [AuditionRepository._runtime_instance_wire(session, row) for row in rows]

    @staticmethod
    def _validated_runtime_exit_wire(
        row: SpeechRuntimeInstanceRow,
        warnings: Mapping[str, Any],
    ) -> dict[str, Any]:
        job_object_assigned = warnings.get("jobObjectAssigned")
        denied_network_attempt_count = warnings.get("deniedNetworkAttemptCount")
        if (
            not isinstance(job_object_assigned, bool)
            or not isinstance(denied_network_attempt_count, int)
            or isinstance(denied_network_attempt_count, bool)
            or not 0 <= denied_network_attempt_count <= 1_000_000
        ):
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                "The runtime process evidence failed integrity verification.",
            )
        exit_evidence = warnings.get("exitEvidence")
        empty_exit = {
            "stopReasonCode": None,
            "exitCode": row.exit_code,
            "shutdownAcknowledged": None,
            "gracefulShutdownConfirmed": None,
            "ownershipConfirmed": None,
            "terminatedByParent": None,
            "confirmedExited": None,
            "ownedProcessesConfirmedExited": None,
            "jobObjectAssigned": job_object_assigned,
            "deniedNetworkAttemptCount": denied_network_attempt_count,
        }
        if exit_evidence is None:
            if row.state == "stopped" or row.stopped_at is not None:
                raise ServiceError(
                    500,
                    "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                    "The stopped runtime is missing exact exit evidence.",
                )
            return empty_exit
        required_fields = {
            "pid",
            "launcher_pid",
            "exit_code",
            "reason",
            "ownership_confirmed",
            "shutdown_acknowledged",
            "graceful_shutdown_confirmed",
            "terminated_by_parent",
            "confirmed_exited",
            "job_object_assigned",
            "owned_processes_confirmed_exited",
            "denied_network_attempt_count",
        }
        if not isinstance(exit_evidence, dict) or set(exit_evidence) != required_fields:
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                "The runtime exit evidence is malformed.",
            )
        boolean_fields = (
            "ownership_confirmed",
            "shutdown_acknowledged",
            "graceful_shutdown_confirmed",
            "terminated_by_parent",
            "confirmed_exited",
            "job_object_assigned",
            "owned_processes_confirmed_exited",
        )
        if (
            any(not isinstance(exit_evidence[field], bool) for field in boolean_fields)
            or not isinstance(exit_evidence["pid"], int)
            or isinstance(exit_evidence["pid"], bool)
            or exit_evidence["pid"] != row.worker_pid
            or not isinstance(exit_evidence["launcher_pid"], int)
            or isinstance(exit_evidence["launcher_pid"], bool)
            or exit_evidence["launcher_pid"] <= 0
            or (
                exit_evidence["exit_code"] is not None
                and (
                    not isinstance(exit_evidence["exit_code"], int)
                    or isinstance(exit_evidence["exit_code"], bool)
                )
            )
            or exit_evidence["exit_code"] != row.exit_code
            or exit_evidence["reason"]
            not in {"clean", "idle", "deadline", "protocol_error", "process_error"}
            or not isinstance(exit_evidence["denied_network_attempt_count"], int)
            or isinstance(exit_evidence["denied_network_attempt_count"], bool)
            or not 0 <= exit_evidence["denied_network_attempt_count"] <= 1_000_000
            or exit_evidence["job_object_assigned"] != job_object_assigned
            or exit_evidence["denied_network_attempt_count"] != denied_network_attempt_count
            or warnings.get("stopReasonCode") != exit_evidence["reason"]
            or row.stopped_at is None
        ):
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                "The runtime exit evidence does not match the persisted process.",
            )
        strict_graceful = (
            exit_evidence["reason"] in {"clean", "idle"}
            and exit_evidence["exit_code"] == 0
            and exit_evidence["ownership_confirmed"] is True
            and exit_evidence["shutdown_acknowledged"] is True
            and exit_evidence["terminated_by_parent"] is False
            and exit_evidence["confirmed_exited"] is True
            and exit_evidence["owned_processes_confirmed_exited"] is True
            and (os.name != "nt" or exit_evidence["job_object_assigned"] is True)
            and exit_evidence["denied_network_attempt_count"] == 0
        )
        if (
            exit_evidence["graceful_shutdown_confirmed"] is not strict_graceful
            or (row.state == "stopped") is not strict_graceful
        ):
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                "The runtime graceful-shutdown assertion is inconsistent.",
            )
        return {
            "stopReasonCode": exit_evidence["reason"],
            "exitCode": exit_evidence["exit_code"],
            "shutdownAcknowledged": exit_evidence["shutdown_acknowledged"],
            "gracefulShutdownConfirmed": exit_evidence["graceful_shutdown_confirmed"],
            "ownershipConfirmed": exit_evidence["ownership_confirmed"],
            "terminatedByParent": exit_evidence["terminated_by_parent"],
            "confirmedExited": exit_evidence["confirmed_exited"],
            "ownedProcessesConfirmedExited": exit_evidence["owned_processes_confirmed_exited"],
            "jobObjectAssigned": exit_evidence["job_object_assigned"],
            "deniedNetworkAttemptCount": exit_evidence["denied_network_attempt_count"],
        }

    @staticmethod
    def _validated_runtime_restart_reconciliation_wire(
        row: SpeechRuntimeInstanceRow,
        warnings: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        reconciliation = warnings.get("restartReconciliation")
        if reconciliation is None:
            if warnings.get("stopReasonCode") == "service_restart_interrupted":
                raise ServiceError(
                    500,
                    "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                    "The runtime restart evidence is unavailable.",
                )
            return None
        expected_keys = {
            "contractVersion",
            "gracefulShutdownConfirmed",
            "observedAt",
            "observerServiceInstanceId",
            "ownershipConfirmed",
            "priorState",
            "processExitConfirmed",
            "reasonCode",
        }
        if (
            not isinstance(reconciliation, dict)
            or set(reconciliation) != expected_keys
            or reconciliation.get("contractVersion") != "1.0.0"
            or reconciliation.get("reasonCode") != "SERVICE_RESTART_INTERRUPTED"
            or reconciliation.get("priorState")
            not in {"starting", "ready", "busy", "idle", "stopping"}
            or reconciliation.get("observedAt") != row.last_health_at
            or not isinstance(reconciliation.get("observedAt"), str)
            or not 1 <= len(reconciliation["observedAt"]) <= 64
            or not isinstance(reconciliation.get("observerServiceInstanceId"), str)
            or not 1 <= len(reconciliation["observerServiceInstanceId"]) <= 160
            or reconciliation.get("ownershipConfirmed") is not False
            or reconciliation.get("gracefulShutdownConfirmed") is not False
            or reconciliation.get("processExitConfirmed") is not False
            or warnings.get("stopReasonCode") != "service_restart_interrupted"
            or warnings.get("exitEvidence") is not None
            or row.state != "failed"
            or row.health_status != "unavailable"
            or row.exit_code is not None
            or row.stopped_at is not None
        ):
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                "The runtime restart evidence failed integrity verification.",
            )
        return cast(dict[str, Any], reconciliation)

    @staticmethod
    def _runtime_instance_wire(
        session: Session,
        row: SpeechRuntimeInstanceRow,
    ) -> dict[str, Any]:
        profile = session.get(SpeechRuntimeProfileRow, row.runtime_profile_id)
        if (
            profile is None
            or profile.profile_fingerprint != row.runtime_profile_fingerprint
            or profile.provider_id != row.provider_id
        ):
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_PROFILE_EVIDENCE_MISSING",
                "The runtime instance profile evidence is unavailable.",
            )
        warnings = parse_json(row.warnings_json, {})
        if not isinstance(warnings, dict):
            raise ServiceError(
                500,
                "SPEECH_RUNTIME_EXIT_EVIDENCE_INVALID",
                "The runtime process evidence is malformed.",
            )
        exit_wire = AuditionRepository._validated_runtime_exit_wire(row, warnings)
        restart_reconciliation = AuditionRepository._validated_runtime_restart_reconciliation_wire(
            row,
            warnings,
        )
        return {
            "contractVersion": "1.0.0",
            "runtimeInstanceId": row.id,
            "runtimeProfileId": profile.profile_id,
            "runtimeProfileFingerprint": row.runtime_profile_fingerprint,
            "providerId": row.provider_id,
            "modelPackageFingerprint": row.model_package_fingerprint,
            "workerPid": row.worker_pid,
            "parentPid": row.parent_pid,
            "executableIdentity": Path(row.executable_identity).name,
            "executableSha256": row.executable_sha256,
            "creationIdentity": row.creation_identity,
            "protocolVersion": row.protocol_version,
            "handshakeAuthenticated": True,
            "state": row.state,
            "startedAt": row.started_at,
            "lastActivityAt": row.last_used_at or row.ready_at or row.started_at,
            "stoppedAt": row.stopped_at,
            **exit_wire,
            "restartReconciliation": restart_reconciliation,
            "networkPolicy": "python_socket_api_denied",
            "observedNetworkRequestCount": (
                int(network_request_count)
                if isinstance(
                    network_request_count := warnings.get("networkRequestCount"),
                    int,
                )
                and not isinstance(network_request_count, bool)
                else None
            ),
            "provenance": _public_provenance(row.provenance_json),
        }
