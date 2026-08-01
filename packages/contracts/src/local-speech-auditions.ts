import type { EntityId, IsoDateTime, Sha256 } from "./domain.js";

export const SPEECH_AUDITION_CONTRACT_VERSION = "1.0.0" as const;
export const SPEECH_RUNTIME_PROTOCOL_VERSION = "1.0.0" as const;
export const SPEECH_AUDITION_PRODUCER_ID =
  "local-speech-audition-orchestrator@1.0.0" as const;

export const AUDITION_GATE_IDS = [
  "per_role_audition_review",
  "narrator_audition_review",
  "character_audition_review",
  "pronunciation_review",
  "voice_readiness_review"
] as const;

export const PRONUNCIATION_SCOPES = [
  "project",
  "narrator",
  "character_role",
  "chapter",
  "scene",
  "custom"
] as const;

export const MODEL_PACKAGE_ACTIONS = [
  "install",
  "verify",
  "activate",
  "deactivate",
  "repair",
  "remove"
] as const;

export const SPEECH_AUDITION_LIMITS = Object.freeze({
  defaultPageSize: 50,
  maximumPageSize: 200,
  maximumCursorCodePoints: 512,
  maximumIdentifierCodePoints: 128,
  maximumIdempotencyKeyCodePoints: 160,
  maximumMutationBytes: 64 * 1024,
  maximumProductionRoles: 300,
  maximumPronunciationEntries: 1_000,
  maximumAuditionMetadataRecords: 2_000,
  maximumCacheRecords: 10_000,
  maximumScriptsPerSession: 20,
  maximumScriptCodePoints: 4_000,
  maximumWrittenFormCodePoints: 120,
  maximumPronunciationValueCodePoints: 256,
  maximumExplanationCodePoints: 2_000,
  maximumReviewRationaleCodePoints: 4_000,
  maximumWarningsPerEntity: 32,
  maximumModelFiles: 4_096,
  maximumModelFileRelativePathCodePoints: 512,
  maximumAudioBytes: 24 * 1024 * 1024,
  maximumAuditionDurationMilliseconds: 30_000,
  expectedSampleRateHz: 24_000,
  expectedChannels: 1,
  expectedSampleWidthBytes: 2,
  maximumComparedClips: 3
});

export type SpeechAuditionContractVersion =
  typeof SPEECH_AUDITION_CONTRACT_VERSION;
export type AuditionGateId = (typeof AUDITION_GATE_IDS)[number];
export type PronunciationScope = (typeof PRONUNCIATION_SCOPES)[number];
export type ModelPackageAction = (typeof MODEL_PACKAGE_ACTIONS)[number];

export interface SpeechAuditionProvenance {
  readonly origin:
    | "fixture_provider"
    | "real_local_provider"
    | "application"
    | "human"
    | "system";
  readonly producerId: string;
  readonly producerVersion: string;
  readonly recordedAt: IsoDateTime;
  readonly inputFingerprint?: Sha256;
  readonly reasonCode?: string;
}

export interface SpeechProviderAdapterDescriptor {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly adapterId: string;
  readonly adapterVersion: string;
  readonly providerClass:
    | "deterministic_fixture"
    | "real_local"
    | "development_only";
  readonly displayName: string;
  readonly synthesisImplemented: boolean;
  readonly localOnly: true;
  readonly networkRequired: false;
  readonly credentialsRequired: false;
  readonly deterministic: boolean;
  readonly productionExportEligible: boolean;
  readonly supportedLanguages: readonly string[];
  readonly outputFormats: readonly ("pcm_s16le_wav")[];
  readonly supportedSampleRatesHz: readonly number[];
  readonly licenseIdentifier: string;
  readonly commercialUseClassification:
    | "allowed"
    | "restricted"
    | "fixture_only"
    | "unknown";
  readonly attributionRequired: boolean;
  readonly status:
    | "available"
    | "degraded"
    | "unavailable"
    | "disabled";
  readonly statusReasonCode: string | null;
  readonly descriptorFingerprint: Sha256;
  readonly provenance: SpeechAuditionProvenance;
}

export interface LocalSpeechRuntimeDescriptor {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly runtimeDescriptorId: EntityId;
  readonly runtimeId: string;
  readonly runtimeVersion: string;
  readonly protocolVersion: typeof SPEECH_RUNTIME_PROTOCOL_VERSION;
  readonly platform: "windows";
  readonly architecture: "x64" | "arm64";
  readonly processBoundary: "managed_child_process";
  readonly transport: "authenticated_stdio" | "authenticated_loopback";
  readonly executableIdentity: string;
  readonly shellUsed: false;
  readonly networkPolicy: "deny_during_synthesis";
  readonly startupDeadlineMilliseconds: number;
  readonly requestDeadlineMilliseconds: number;
  readonly idleShutdownMilliseconds: number;
  readonly maximumRetryAttempts: number;
  readonly maximumConcurrentRequests: number;
  readonly descriptorFingerprint: Sha256;
  readonly provenance: SpeechAuditionProvenance;
}

export interface SpeechRuntimeProfile {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly runtimeProfileId: EntityId;
  readonly revision: number;
  readonly runtimeDescriptorId: EntityId;
  readonly providerIds: readonly string[];
  readonly compatibleModelPackageIds: readonly EntityId[];
  readonly protocolVersion: typeof SPEECH_RUNTIME_PROTOCOL_VERSION;
  readonly startupDeadlineMilliseconds: number;
  readonly requestDeadlineMilliseconds: number;
  readonly idleShutdownMilliseconds: number;
  readonly maximumRetryAttempts: number;
  readonly maximumConcurrentRequests: number;
  readonly shellUsed: false;
  readonly networkAccessDuringSynthesis: false;
  readonly profileFingerprint: Sha256;
  readonly active: boolean;
  readonly provenance: SpeechAuditionProvenance;
}

export interface SpeechRuntimeRestartReconciliation {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly reasonCode: "SERVICE_RESTART_INTERRUPTED";
  readonly priorState: "starting" | "ready" | "busy" | "idle" | "stopping";
  readonly observedAt: IsoDateTime;
  readonly observerServiceInstanceId: EntityId;
  readonly ownershipConfirmed: false;
  readonly gracefulShutdownConfirmed: false;
  readonly processExitConfirmed: false;
}

export interface SpeechRuntimeInstance {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly runtimeInstanceId: EntityId;
  readonly runtimeProfileId: EntityId;
  readonly runtimeProfileFingerprint: Sha256;
  readonly providerId: string;
  readonly modelPackageFingerprint: Sha256;
  readonly workerPid: number;
  readonly parentPid: number;
  readonly executableIdentity: string;
  readonly executableSha256: Sha256;
  readonly creationIdentity: string;
  readonly protocolVersion: typeof SPEECH_RUNTIME_PROTOCOL_VERSION;
  readonly handshakeAuthenticated: true;
  readonly state:
    | "starting"
    | "ready"
    | "busy"
    | "idle"
    | "stopping"
    | "stopped"
    | "failed";
  readonly startedAt: IsoDateTime;
  readonly lastActivityAt: IsoDateTime;
  readonly stoppedAt: IsoDateTime | null;
  readonly stopReasonCode: string | null;
  readonly shutdownAcknowledged: boolean | null;
  readonly gracefulShutdownConfirmed: boolean | null;
  readonly exitCode: number | null;
  readonly terminatedByParent: boolean | null;
  readonly ownershipConfirmed: boolean | null;
  readonly confirmedExited: boolean | null;
  readonly ownedProcessesConfirmedExited: boolean | null;
  readonly jobObjectAssigned: boolean;
  readonly deniedNetworkAttemptCount: number;
  readonly networkPolicy: "python_socket_api_denied";
  /** Null unless an external observer supplied a trustworthy count. */
  readonly observedNetworkRequestCount: number | null;
  /** Exact fail-closed evidence when service restart made process exit unknowable. */
  readonly restartReconciliation: SpeechRuntimeRestartReconciliation | null;
  readonly provenance: SpeechAuditionProvenance;
}

export interface SpeechRuntimeHealth {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly runtimeProfileId: EntityId;
  readonly runtimeProfileFingerprint: Sha256;
  readonly runtimeInstanceId: EntityId | null;
  readonly providerId: string;
  readonly status:
    | "available"
    | "degraded"
    | "unavailable"
    | "disabled";
  readonly reasonCode: string;
  readonly checkedAt: IsoDateTime;
  readonly expiresAt: IsoDateTime;
  readonly modelPackageFingerprint: Sha256 | null;
  readonly protocolVersion: typeof SPEECH_RUNTIME_PROTOCOL_VERSION;
}

export interface ModelPackageFile {
  readonly relativePath: string;
  readonly byteSize: number;
  readonly sha256: Sha256;
  readonly mediaClassification:
    | "onnx"
    | "safetensors"
    | "configuration"
    | "tokenizer"
    | "voice_data"
    | "license"
    | "notice";
  readonly executable: false;
}

export interface ModelPackageManifest {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly manifestVersion: string;
  readonly modelPackageId: EntityId;
  readonly providerId: string;
  readonly modelId: string;
  readonly modelVersion: string;
  readonly runtimeVersion: string;
  readonly platform: "windows";
  readonly architecture: "x64" | "arm64";
  readonly sourceClassification:
    | "official_release"
    | "maintainer_referenced_conversion"
    | "repository_fixture";
  readonly officialSourceReference: string;
  readonly licenseIdentifier: string;
  readonly commercialUseClassification:
    | "allowed"
    | "restricted"
    | "fixture_only"
    | "unknown";
  readonly attributionRequirements: readonly string[];
  readonly files: readonly ModelPackageFile[];
  readonly totalExpandedByteSize: number;
  readonly requiredRuntimeDependencies: readonly string[];
  readonly compatibilityConstraints: readonly string[];
  readonly state: "active" | "deprecated" | "revoked";
  readonly manifestFingerprint: Sha256;
  readonly provenance: SpeechAuditionProvenance;
}

export interface ModelInstallationRecord {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly installationId: EntityId;
  readonly modelPackageId: EntityId;
  readonly manifestFingerprint: Sha256;
  readonly installationRevision: number;
  readonly storageKey: string;
  readonly status:
    | "pending"
    | "installed"
    | "active"
    | "inactive"
    | "repair_required"
    | "removed"
    | "failed";
  readonly active: boolean;
  readonly installedAt: IsoDateTime | null;
  readonly updatedAt: IsoDateTime;
  readonly lastAction: ModelPackageAction;
  readonly actionReasonCode: string;
  readonly immutableEventId: EntityId;
  readonly provenance: SpeechAuditionProvenance;
}

export interface ModelVerificationRecord {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly verificationId: EntityId;
  readonly installationId: EntityId;
  readonly modelPackageId: EntityId;
  readonly manifestFingerprint: Sha256;
  readonly verificationFingerprint: Sha256;
  readonly status: "verified" | "mismatch" | "missing" | "unsafe";
  readonly verifiedFileCount: number;
  readonly verifiedByteSize: number;
  readonly unexpectedFileCount: number;
  readonly symlinkOrReparsePointDetected: boolean;
  readonly checkedAt: IsoDateTime;
  readonly blockingReasonCodes: readonly string[];
  readonly provenance: SpeechAuditionProvenance;
}

export interface VoiceRuntimeBinding {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly bindingId: EntityId;
  readonly bindingKind:
    | "exact_provider_match"
    | "declared_fixture_adapter";
  readonly voiceProfileId: EntityId;
  readonly voiceProfileVersion: string;
  readonly voiceProfileFingerprint: Sha256;
  readonly sourceProviderId: string;
  readonly sourceProviderVersion: string;
  readonly sourceProviderFingerprint: Sha256;
  readonly sourceModelId: string;
  readonly sourceModelVersion: string;
  readonly sourceModelFingerprint: Sha256;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly providerVoiceId: string;
  readonly modelId: string;
  readonly modelVersion: string;
  readonly modelPackageId: EntityId;
  readonly modelPackageFingerprint: Sha256;
  readonly runtimeProfileId: EntityId;
  readonly runtimeProfileFingerprint: Sha256;
  readonly bindingFingerprint: Sha256;
  readonly active: boolean;
  readonly provenance: SpeechAuditionProvenance;
  readonly createdAt: IsoDateTime;
}

export interface TextCodePointSpan {
  readonly start: number;
  readonly end: number;
}

export interface TextNormalizationTransformation {
  readonly transformationId: EntityId;
  readonly kind:
    | "line_ending"
    | "control_whitespace"
    | "unicode_composition"
    | "typographic_quote"
    | "typographic_dash"
    | "ellipsis"
    | "unsupported_character";
  readonly sourceSpan: TextCodePointSpan;
  readonly destinationSpan: TextCodePointSpan;
  readonly originalTextSha256: Sha256;
  readonly replacementTextSha256: Sha256;
  /** Present only on an authenticated, project-owned detail response. */
  readonly originalText?: string;
  /** Present only on an authenticated, project-owned detail response. */
  readonly replacementText?: string;
  readonly reasonCode: string;
  readonly requiredByProvider: boolean;
  readonly humanApprovalRequired: boolean;
  readonly approved: boolean;
}

export interface TextNormalizationPlan {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly normalizationPlanId: EntityId;
  readonly projectId: EntityId;
  readonly originalTextSha256: Sha256;
  readonly normalizedTextSha256: Sha256;
  readonly providerId: string;
  readonly profileId: string;
  readonly profileVersion: string;
  readonly transformations: readonly TextNormalizationTransformation[];
  readonly appliedPronunciationEntryIds: readonly EntityId[];
  readonly unsupportedCharacterCodePoints: readonly string[];
  readonly warnings: readonly string[];
  readonly humanReviewRequired: boolean;
  readonly planFingerprint: Sha256;
  readonly provenance: SpeechAuditionProvenance;
}

export type PronunciationRepresentation =
  | "provider_neutral"
  | "ipa"
  | "provider_specific";

export interface PronunciationDictionary {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly dictionaryId: EntityId;
  readonly projectId: EntityId;
  readonly revision: number;
  readonly entryCount: number;
  readonly currentEntryCount: number;
  readonly dictionaryFingerprint: Sha256;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly provenance: SpeechAuditionProvenance;
}

export interface PronunciationEntry {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly entryId: EntityId;
  readonly projectId: EntityId;
  readonly dictionaryId: EntityId;
  readonly dictionaryRevision: number;
  readonly revision: number;
  readonly writtenForm: string;
  readonly normalizedLookupForm: string;
  readonly language: string;
  readonly locale: string | null;
  readonly scope: PronunciationScope;
  readonly scopeId: EntityId | null;
  readonly representation: PronunciationRepresentation;
  readonly pronunciation: string;
  readonly ipa: string | null;
  readonly providerId: string | null;
  readonly providerCompiledValue: string | null;
  readonly caseSensitive: boolean;
  readonly matchRule: "whole_word" | "phrase";
  readonly priority: number;
  readonly actor: {
    readonly classification: "human";
    readonly actorId: EntityId;
  };
  readonly reason: string;
  readonly verificationState:
    | "pending"
    | "approved"
    | "changes_requested"
    | "rejected"
    | "superseded";
  readonly entryFingerprint: Sha256;
  readonly supersedesEntryId: EntityId | null;
  readonly supersededByEntryId: EntityId | null;
  readonly immutable: true;
  readonly provenance: SpeechAuditionProvenance;
}

export interface CompiledPronunciationSpan {
  readonly sourceSpan: TextCodePointSpan;
  readonly entryId: EntityId;
  readonly entryRevision: number;
  readonly writtenFormSha256: Sha256;
  readonly compiledValueSha256: Sha256;
  readonly representation: PronunciationRepresentation;
}

export interface CompiledPronunciationPlan {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly pronunciationPlanId: EntityId;
  readonly projectId: EntityId;
  readonly dictionaryId: EntityId;
  readonly dictionaryRevision: number;
  readonly dictionaryFingerprint: Sha256;
  readonly sourceTextSha256: Sha256;
  readonly locale: string;
  readonly roleId: EntityId;
  readonly scopeContext: {
    readonly chapterId: EntityId | null;
    readonly sceneId: EntityId | null;
    readonly customScopeIds: readonly EntityId[];
  };
  readonly appliedEntries: readonly CompiledPronunciationSpan[];
  readonly dependencyEntryRevisions: readonly {
    readonly entryId: EntityId;
    readonly revision: number;
  }[];
  readonly providerId: string;
  readonly escapedProviderPayloadSha256: Sha256;
  readonly planFingerprint: Sha256;
  readonly provenance: SpeechAuditionProvenance;
}

export type AuditionScriptKind =
  | "standardized_synthetic"
  | "approved_manuscript_excerpt"
  | "role_dialogue_excerpt"
  | "narrator_excerpt"
  | "pronunciation_test"
  | "synthetic_fallback";

export interface AuditionSession {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly auditionSessionId: EntityId;
  readonly projectId: EntityId;
  readonly roleId: EntityId;
  readonly castAssignmentId: EntityId;
  readonly castAssignmentRevision: number;
  readonly approvedCastSnapshotId: EntityId;
  readonly approvedCastSnapshotRevision: number;
  readonly approvedCastSnapshotFingerprint: Sha256;
  readonly voiceRuntimeBindingId: EntityId;
  readonly voiceRuntimeBindingFingerprint: Sha256;
  readonly providerVoiceId: string;
  readonly voiceRuntimeBinding: VoiceRuntimeBinding;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly modelPackageFingerprint: Sha256;
  readonly runtimeProfileFingerprint: Sha256;
  readonly pronunciationDictionaryRevision: number;
  readonly pronunciationDictionaryFingerprint: Sha256;
  readonly state:
    | "draft"
    | "queued"
    | "generating"
    | "reviewable"
    | "failed"
    | "cancelled"
    | "invalidated";
  readonly revision: number;
  readonly scriptCount: number;
  readonly clipCount: number;
  readonly approvedClipId: EntityId | null;
  readonly jobId: EntityId | null;
  readonly sessionFingerprint: Sha256;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly provenance: SpeechAuditionProvenance;
}

export interface AuditionScriptSummary {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly auditionScriptId: EntityId;
  readonly auditionSessionId: EntityId;
  readonly projectId: EntityId;
  readonly roleId: EntityId;
  readonly kind: AuditionScriptKind;
  readonly sourceTextSha256: Sha256;
  readonly sourceSpan: TextCodePointSpan | null;
  readonly sourceDocumentId: EntityId | null;
  readonly sourceAnalysisEntity: {
    readonly entityId: EntityId;
    readonly collection: "dialogue-lines" | "narration-spans";
    readonly effectiveRevision: number;
    readonly effectiveFingerprint: Sha256;
  } | null;
  readonly sourceRevision: number | null;
  readonly normalizedTextSha256: Sha256;
  readonly normalizationPlanId: EntityId;
  readonly pronunciationPlanId: EntityId;
  readonly localOnly: true;
  readonly scriptFingerprint: Sha256;
  readonly createdAt: IsoDateTime;
}

export interface AuditionScriptDetail extends AuditionScriptSummary {
  /** Private project data; never include in lists, logs, events, or evidence. */
  readonly text: string;
}

export interface SpeechProviderControls {
  readonly speakingRate: number;
  readonly pitch: number | null;
  readonly style: string | null;
  readonly energy: number | null;
  readonly controlsFingerprint: Sha256;
}

export interface AuditionEvidenceBinding {
  readonly projectId: EntityId;
  readonly sourceDocumentId: EntityId;
  readonly sourceRevision: number;
  readonly extractionId: EntityId;
  readonly extractionRevision: number;
  readonly extractedTextSha256: Sha256;
  readonly phase2RunId: EntityId;
  readonly phase2SnapshotId: EntityId;
  readonly phase2SnapshotRevision: number;
  readonly phase2SnapshotFingerprint: Sha256;
  readonly phase2CorrectionSetFingerprint: Sha256;
  readonly castingRunId: EntityId;
  readonly approvedCastSnapshotId: EntityId;
  readonly approvedCastSnapshotRevision: number;
  readonly approvedCastSnapshotFingerprint: Sha256;
  readonly castAssignmentId: EntityId;
  readonly castAssignmentRevision: number;
  readonly voiceProfileId: EntityId;
  readonly voiceProfileVersion: string;
  readonly voiceRuntimeBindingId: EntityId;
  readonly voiceRuntimeBindingFingerprint: Sha256;
  readonly providerVoiceId: string;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly modelId: string;
  readonly modelVersion: string;
  readonly catalogRevisionId: EntityId;
  readonly catalogFingerprint: Sha256;
  readonly rightsRecordId: EntityId;
  readonly rightsRecordRevision: number;
  readonly rightsRecordFingerprint: Sha256;
  readonly pronunciationDictionaryId: EntityId;
  readonly pronunciationDictionaryRevision: number;
  readonly pronunciationDictionaryFingerprint: Sha256;
  readonly runtimeProfileId: EntityId;
  readonly runtimeProfileFingerprint: Sha256;
  readonly modelPackageId: EntityId;
  readonly modelPackageFingerprint: Sha256;
  readonly producerVersion: string;
}

export interface SpeechPreviewRequest {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly requestId: EntityId;
  readonly auditionSessionId: EntityId;
  readonly auditionSessionRevision: number;
  readonly auditionScriptId: EntityId;
  readonly auditionScriptFingerprint: Sha256;
  readonly evidence: AuditionEvidenceBinding;
  readonly normalizedTextSha256: Sha256;
  readonly normalizationPlanFingerprint: Sha256;
  readonly pronunciationPlanFingerprint: Sha256;
  readonly providerControls: SpeechProviderControls;
  readonly outputFormat: "pcm_s16le_wav";
  readonly sampleRateHz: number;
  readonly channels: 1;
  readonly idempotencyKey: string;
  readonly requestFingerprint: Sha256;
}

export interface SpeechProviderRequest {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly providerRequestId: EntityId;
  readonly speechPreviewRequestId: EntityId;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly modelId: string;
  readonly modelVersion: string;
  readonly modelPackageFingerprint: Sha256;
  readonly runtimeProfileId: EntityId;
  readonly runtimeProfileFingerprint: Sha256;
  /** Null until a managed worker instance is allocated to the queued request. */
  readonly runtimeInstanceId: EntityId | null;
  readonly voiceProfileId: EntityId;
  readonly voiceProfileVersion: string;
  readonly voiceRuntimeBindingId: EntityId;
  readonly voiceRuntimeBindingFingerprint: Sha256;
  readonly providerVoiceId: string;
  readonly castAssignmentId: EntityId;
  readonly castAssignmentRevision: number;
  readonly auditionSessionId: EntityId;
  readonly normalizedTextSha256: Sha256;
  readonly pronunciationPlanFingerprint: Sha256;
  readonly providerControlFingerprint: Sha256;
  readonly cacheKey: Sha256;
  readonly state:
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled";
  readonly startedAt: IsoDateTime | null;
  readonly finishedAt: IsoDateTime | null;
  readonly retryable: boolean;
  readonly warnings: readonly string[];
  readonly requestFingerprint: Sha256;
  readonly provenance: SpeechProviderRequestProvenance;
}

export type SpeechProviderRequestExecutionDetails =
  | {
      readonly executionClassification: "provider_execution";
      /** Zero before dispatch or for a preflight terminal outcome; one once
       * the exact authenticated-provider dispatch is durably committed. This
       * records a dispatch attempt, not proof of provider completion. */
      readonly providerDispatchCount: 0 | 1;
      readonly sourceProviderRequestId?: never;
    }
  | {
      readonly executionClassification: "verified_cache_lookup";
      readonly providerDispatchCount: 0;
      /** The prior provider execution whose verified artifact was reused. */
      readonly sourceProviderRequestId: EntityId;
    };

export type SpeechProviderRequestInvocationDetails = {
  readonly providerLanguage: string;
  readonly providerVoiceId: string;
  readonly voiceRuntimeBindingId: EntityId;
  readonly voiceRuntimeBindingFingerprint: Sha256;
} &
  (
    | {
        readonly restrictedLocalUseAcknowledged: false;
        readonly restrictedLocalUseAcknowledgementEventId: null;
      }
    | {
        readonly restrictedLocalUseAcknowledged: true;
        readonly restrictedLocalUseAcknowledgementEventId: EntityId;
      }
  );

export interface SpeechProviderRequestRetryDetails {
  readonly attempt: number;
  readonly supersedesProviderRequestId: EntityId;
  readonly originalIdempotencyKeyFingerprint: Sha256;
}

export interface SpeechProviderRequestProvenance
  extends SpeechAuditionProvenance {
  readonly inputFingerprint: Sha256;
  readonly details: SpeechProviderRequestExecutionDetails &
    (SpeechProviderRequestInvocationDetails | SpeechProviderRequestRetryDetails);
}

export interface SpeechProviderResult {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly providerRequestId: EntityId;
  readonly outcome: "succeeded" | "failed" | "cancelled";
  readonly retryable: boolean;
  readonly audioArtifactId: EntityId | null;
  readonly outputArtifactSha256: Sha256 | null;
  readonly networkRequestCount: number;
  readonly warnings: readonly string[];
  readonly startedAt: IsoDateTime;
  readonly finishedAt: IsoDateTime;
  readonly resultFingerprint: Sha256;
  readonly provenance: SpeechAuditionProvenance;
}

export interface AudioArtifactRecord {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly audioArtifactId: EntityId;
  readonly projectId: EntityId;
  readonly storageKey: string;
  readonly mediaType: "audio/wav";
  readonly codec: "pcm_s16le";
  readonly sampleRateHz: number;
  readonly channels: number;
  readonly sampleWidthBytes: number;
  readonly frameCount: number;
  readonly durationMilliseconds: number;
  readonly byteSize: number;
  readonly sha256: Sha256;
  readonly availability: "present" | "purged" | "corrupt" | "quarantined";
  readonly playbackEligible: boolean;
  readonly publishedAtomically: true;
  readonly createdAt: IsoDateTime;
  readonly immutable: true;
}

export interface AudioQualityRecord {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly qualityRecordId: EntityId;
  readonly projectId: EntityId;
  readonly audioArtifactId: EntityId;
  readonly profileId: string;
  readonly profileVersion: string;
  readonly validWav: boolean;
  readonly nonSilent: boolean;
  readonly peakDbfs: number;
  readonly silenceRatio: number;
  readonly clippedSampleCount: number;
  readonly blockingFindingCodes: readonly string[];
  readonly warningCodes: readonly string[];
  readonly subjectiveQualityClaimed: false;
  readonly qualityFingerprint: Sha256;
  readonly measuredAt: IsoDateTime;
  readonly provenance: SpeechAuditionProvenance;
}

export interface AuditionCacheRecord {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly cacheRecordId: EntityId;
  readonly projectId: EntityId;
  readonly cacheKey: Sha256;
  readonly providerId: string;
  readonly runtimeProfileFingerprint: Sha256;
  readonly modelPackageFingerprint: Sha256;
  readonly voiceProfileId: EntityId;
  readonly voiceRuntimeBindingId: EntityId;
  readonly voiceRuntimeBindingFingerprint: Sha256;
  readonly providerVoiceId: string;
  readonly castAssignmentId: EntityId;
  readonly castAssignmentRevision: number;
  readonly normalizedTextSha256: Sha256;
  readonly pronunciationDictionaryFingerprint: Sha256;
  readonly pronunciationPlanFingerprint: Sha256;
  readonly providerControlFingerprint: Sha256;
  readonly outputProfileFingerprint: Sha256;
  readonly producerVersion: string;
  readonly audioArtifactId: EntityId;
  readonly artifactSha256: Sha256;
  readonly state: "verified" | "corrupt" | "missing" | "cleared";
  readonly lastVerifiedAt: IsoDateTime;
  readonly createdAt: IsoDateTime;
}

export interface AuditionClip {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly auditionClipId: EntityId;
  readonly projectId: EntityId;
  readonly auditionSessionId: EntityId;
  readonly auditionScriptId: EntityId;
  readonly roleId: EntityId;
  readonly castAssignmentId: EntityId;
  readonly castAssignmentRevision: number;
  readonly providerRequestId: EntityId;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly voiceRuntimeBindingId: EntityId;
  readonly voiceRuntimeBindingFingerprint: Sha256;
  readonly providerVoiceId: string;
  readonly providerClass: "deterministic_fixture" | "real_local";
  readonly modelId: string;
  readonly modelVersion: string;
  readonly modelPackageFingerprint: Sha256;
  readonly runtimeProfileFingerprint: Sha256;
  readonly normalizedTextSha256: Sha256;
  readonly pronunciationPlanFingerprint: Sha256;
  readonly providerControlFingerprint: Sha256;
  readonly cacheKey: Sha256;
  readonly cacheStatus: "miss" | "verified_hit" | "corrupt_miss";
  readonly cacheProof: {
    readonly cacheRecordId: EntityId;
    readonly cacheKey: Sha256;
    readonly voiceRuntimeBindingId: EntityId;
    readonly voiceRuntimeBindingFingerprint: Sha256;
    readonly providerVoiceId: string;
    readonly verificationFingerprint: Sha256;
  };
  readonly audioArtifact: AudioArtifactRecord;
  readonly audioQuality: AudioQualityRecord;
  readonly state: "reviewable" | "approved" | "rejected" | "invalidated";
  readonly clipFingerprint: Sha256;
  readonly revision: number;
  readonly createdAt: IsoDateTime;
  readonly provenance: SpeechAuditionProvenance;
}

export type AuditionReviewState =
  | "blocked"
  | "pending"
  | "approved"
  | "changes_requested"
  | "rejected"
  | "invalidated";

export interface AuditionGateEvidence {
  readonly projectId: EntityId;
  readonly gateId: AuditionGateId;
  readonly roleId: EntityId | null;
  readonly auditionSessionId: EntityId | null;
  readonly auditionClipId: EntityId | null;
  readonly auditionClipRevision: number | null;
  readonly approvedCastSnapshotFingerprint: Sha256;
  readonly castAssignmentFingerprint: Sha256 | null;
  readonly rightsRecordFingerprint: Sha256;
  readonly runtimeProfileFingerprint: Sha256;
  readonly modelVerificationFingerprint: Sha256;
  readonly pronunciationDictionaryFingerprint: Sha256;
  readonly pronunciationDependencyFingerprint: Sha256;
  readonly audioQualityFingerprint: Sha256 | null;
  readonly evidenceFingerprint: Sha256;
}

export interface AuditionReview {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly reviewId: EntityId;
  readonly projectId: EntityId;
  readonly gateId: AuditionGateId;
  readonly roleId: EntityId | null;
  readonly state: AuditionReviewState;
  readonly revision: number;
  readonly prerequisiteGateIds: readonly AuditionGateId[];
  readonly evidence: AuditionGateEvidence;
  readonly blockerCodes: readonly string[];
  readonly warningCodes: readonly string[];
  readonly latestDecision: AuditionReviewDecision | null;
  readonly updatedAt: IsoDateTime;
}

export interface AuditionReviewDecision {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly decisionId: EntityId;
  readonly reviewId: EntityId;
  readonly projectId: EntityId;
  readonly gateId: AuditionGateId;
  readonly roleId: EntityId | null;
  readonly decision:
    | "approved"
    | "changes_requested"
    | "rejected"
    | "invalidated";
  readonly actor: {
    readonly classification: "human" | "system";
    readonly actorId: EntityId;
  };
  readonly expectedReviewRevision: number;
  readonly evidenceFingerprint: Sha256;
  readonly rationale: string;
  readonly decidedAt: IsoDateTime;
  readonly immutable: true;
  readonly supersedesDecisionId: EntityId | null;
  readonly provenance: SpeechAuditionProvenance;
}

export interface VoiceReadinessSnapshot {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly snapshotId: EntityId;
  readonly projectId: EntityId;
  readonly revision: number;
  readonly approvedCastSnapshotId: EntityId;
  readonly approvedCastSnapshotRevision: number;
  readonly approvedCastSnapshotFingerprint: Sha256;
  readonly runtimeProfileFingerprint: Sha256;
  readonly modelVerificationFingerprint: Sha256;
  readonly rightsEvidenceFingerprint: Sha256;
  readonly narratorAuditionDecisionIds: readonly EntityId[];
  readonly characterAuditionDecisionIds: readonly EntityId[];
  readonly pronunciationReviewDecisionId: EntityId | null;
  readonly requiredRoleCount: number;
  readonly approvedRoleCount: number;
  readonly blockingFindingCodes: readonly string[];
  readonly snapshotFingerprint: Sha256;
  readonly reviewEligible: boolean;
  readonly authorizes: "later_performance_direction_only";
  readonly authorizesFullBookRendering: false;
  readonly createdAt: IsoDateTime;
  readonly immutable: true;
}

export interface AuditionPrerequisiteStatus {
  readonly prerequisiteId:
    | "import_review"
    | "phase2_story_structure_review"
    | "phase2_character_registry_review"
    | "phase2_dialogue_attribution_review"
    | "phase2_whole_book_analysis_review"
    | "phase3a_narrator_casting_review"
    | "phase3a_character_casting_review"
    | "phase3a_complete_cast_review"
    | "approved_cast_snapshot"
    | "voice_rights"
    | "voice_assignment";
  readonly current: boolean;
  readonly statusCode: string;
  readonly evidenceId: EntityId | null;
  readonly evidenceFingerprint: Sha256 | null;
}

export interface AuditionRoleStatus {
  readonly roleId: EntityId;
  readonly roleType: "narrator" | "character";
  readonly displayLabel: string;
  readonly required: boolean;
  readonly assignmentId: EntityId;
  readonly assignmentRevision: number;
  readonly voiceProfileId: EntityId;
  readonly voiceDisplayLabel: string;
  readonly voiceRuntimeBinding: VoiceRuntimeBinding | null;
  readonly runtimeBindingStatus:
    | "compatible"
    | "incompatible"
    | "unavailable";
  readonly runtimeBindingReasonCode:
    | "VOICE_RUNTIME_BINDING_INCOMPATIBLE"
    | "VERIFIED_ACTIVE_MODEL_PACKAGE_REQUIRED"
    | null;
  readonly rightsState: "verified" | "restricted";
  readonly latestSessionId: EntityId | null;
  readonly latestClipId: EntityId | null;
  readonly reviewState: AuditionReviewState;
  /** Server-issued, hash-only material for creating the role's next session. */
  readonly sessionEvidence: AuditionEvidenceBinding | null;
  /** Server-issued, hash-only request material for the next explicit generation. */
  readonly generationRequest: SpeechPreviewRequest | null;
}

export interface AuditionWorkspaceSnapshot {
  readonly contractVersion: SpeechAuditionContractVersion;
  readonly projectId: EntityId;
  readonly prerequisites: readonly AuditionPrerequisiteStatus[];
  readonly approvedCastSnapshot: {
    readonly snapshotId: EntityId;
    readonly revision: number;
    readonly fingerprint: Sha256;
  } | null;
  readonly providers: readonly SpeechProviderAdapterDescriptor[];
  readonly runtimeProfiles: readonly SpeechRuntimeProfile[];
  readonly runtimeHealth: readonly SpeechRuntimeHealth[];
  readonly runtimeInstances: readonly SpeechRuntimeInstance[];
  readonly modelInstallations: readonly ModelInstallationRecord[];
  readonly modelVerifications: readonly ModelVerificationRecord[];
  readonly currentDictionary: PronunciationDictionary | null;
  readonly roles: {
    readonly items: readonly AuditionRoleStatus[];
    readonly pageSize: number;
    readonly total: number;
    readonly nextCursor?: string;
  };
  readonly reviews: readonly AuditionReview[];
  readonly voiceReadinessSnapshot: VoiceReadinessSnapshot | null;
  readonly updatedAt: IsoDateTime;
}

export interface SpeechAuditionPageRequest {
  readonly cursor?: string;
  readonly limit?: number;
}

export interface SpeechAuditionPageResponse {
  readonly correlationId: EntityId;
  readonly pageSize: number;
  readonly total: number;
  readonly nextCursor?: string;
}

export interface AuditionWorkspaceResponse {
  readonly correlationId: EntityId;
  readonly workspace: AuditionWorkspaceSnapshot;
}

export interface ModelPackagePageResponse extends SpeechAuditionPageResponse {
  readonly projectId: EntityId;
  readonly items: readonly {
    readonly manifest: ModelPackageManifest;
    readonly installation: ModelInstallationRecord | null;
    readonly verification: ModelVerificationRecord | null;
  }[];
}

export interface PronunciationEntryPageResponse
  extends SpeechAuditionPageResponse {
  readonly projectId: EntityId;
  readonly dictionary: PronunciationDictionary;
  readonly items: readonly PronunciationEntry[];
}

export interface AuditionSessionPageResponse
  extends SpeechAuditionPageResponse {
  readonly projectId: EntityId;
  readonly items: readonly AuditionSession[];
}

export interface AuditionClipPageResponse extends SpeechAuditionPageResponse {
  readonly projectId: EntityId;
  readonly items: readonly AuditionClip[];
}

export interface AuditionReviewDecisionPageResponse
  extends SpeechAuditionPageResponse {
  readonly projectId: EntityId;
  readonly gateId: AuditionGateId;
  readonly roleId: EntityId | null;
  readonly items: readonly AuditionReviewDecision[];
}

export interface CreatePronunciationEntryRequest {
  readonly expectedDictionaryRevision: number;
  readonly expectedDictionaryFingerprint: Sha256;
  readonly writtenForm: string;
  readonly language: string;
  readonly locale: string | null;
  readonly scope: PronunciationScope;
  readonly scopeId: EntityId | null;
  readonly representation: PronunciationRepresentation;
  readonly pronunciation: string;
  readonly ipa: string | null;
  readonly providerId: string | null;
  readonly providerCompiledValue: string | null;
  readonly caseSensitive: boolean;
  readonly matchRule: "whole_word" | "phrase";
  readonly priority: number;
  readonly reason: string;
  readonly supersedesEntryId: EntityId | null;
  readonly idempotencyKey: string;
}

export interface CreatePronunciationEntryResponse {
  readonly correlationId: EntityId;
  readonly entry: PronunciationEntry;
  readonly dictionary: PronunciationDictionary;
  readonly invalidatedClipIds: readonly EntityId[];
  readonly invalidatedClipCount: number;
  readonly invalidatedClipIdsTruncated: boolean;
  readonly preservedClipIds: readonly EntityId[];
  readonly preservedClipCount: number;
  readonly preservedClipIdsTruncated: boolean;
  readonly invalidatedGateIds: readonly AuditionGateId[];
}

export interface DecidePronunciationEntryRequest {
  readonly expectedEntryRevision: number;
  readonly expectedEntryFingerprint: Sha256;
  readonly expectedDictionaryRevision: number;
  readonly expectedDictionaryFingerprint: Sha256;
  readonly decision: "approve" | "request_changes" | "reject";
  readonly rationale: string;
  readonly idempotencyKey: string;
}

export type DecidePronunciationEntryResponse =
  CreatePronunciationEntryResponse;

export interface ClearAuditionCacheRequest {
  readonly expectedProjectRevision: number;
  readonly reason: string;
  readonly idempotencyKey: string;
}

export interface ClearAuditionCacheResponse {
  readonly correlationId: EntityId;
  readonly projectId: EntityId;
  readonly clearedRecordCount: number;
  readonly alreadyClearedRecordCount: number;
  readonly projectRevision: number;
  readonly purgedArtifactCount?: number;
}

export interface CreateAuditionSessionRequest {
  readonly roleId: EntityId;
  readonly evidence: AuditionEvidenceBinding;
  readonly idempotencyKey: string;
}

export interface CreateAuditionSessionResponse {
  readonly correlationId: EntityId;
  readonly session: AuditionSession;
}

export interface CreateAuditionScriptRequest {
  readonly auditionSessionId: EntityId;
  readonly expectedSessionRevision: number;
  readonly kind: AuditionScriptKind;
  readonly text: string;
  readonly sourceDocumentId: EntityId | null;
  readonly sourceRevision: number | null;
  readonly sourceSpan: TextCodePointSpan | null;
  readonly sourceTextSha256: Sha256;
  readonly acceptedOptionalNormalizationIds: readonly EntityId[];
  /** Explicit caller-selected custom pronunciation scopes. Source-derived
   * project, narrator, role, chapter, and scene scopes are compiled by the
   * service and are not accepted through this field. */
  readonly customPronunciationScopeIds?: readonly EntityId[];
  readonly idempotencyKey: string;
}

export interface CreateAuditionScriptResponse {
  readonly correlationId: EntityId;
  readonly script: AuditionScriptDetail;
  readonly normalizationPlan: TextNormalizationPlan;
  readonly pronunciationPlan: CompiledPronunciationPlan;
  readonly session: AuditionSession;
}

export interface PreviewNormalizationRequest {
  readonly auditionSessionId: EntityId;
  readonly expectedSessionRevision: number;
  readonly text: string;
  readonly sourceTextSha256: Sha256;
  readonly acceptedOptionalNormalizationIds: readonly EntityId[];
  /** Explicit caller-selected custom pronunciation scopes. */
  readonly customPronunciationScopeIds?: readonly EntityId[];
}

export interface PreviewNormalizationResponse {
  readonly correlationId: EntityId;
  readonly projectId: EntityId;
  readonly auditionSessionId: EntityId;
  readonly auditionSessionRevision: number;
  readonly providerId: string;
  readonly acceptedOptionalNormalizationIds: readonly EntityId[];
  readonly customPronunciationScopeIds: readonly EntityId[];
  readonly plan: TextNormalizationPlan;
}

export interface GenerateAuditionRequest {
  readonly preview: SpeechPreviewRequest;
}

export interface GenerateAuditionResponse {
  readonly correlationId: EntityId;
  readonly session: AuditionSession;
  readonly providerRequest: SpeechProviderRequest;
  readonly jobId: EntityId;
}

export interface DecideAuditionReviewRequest {
  readonly expectedReviewRevision: number;
  readonly expectedEvidenceFingerprint: Sha256;
  readonly decision: "approve" | "request_changes" | "reject";
  readonly rationale: string;
  readonly supersedesDecisionId: EntityId | null;
  readonly idempotencyKey: string;
}

export interface DecideAuditionReviewResponse {
  readonly correlationId: EntityId;
  readonly review: AuditionReview;
  readonly decision: AuditionReviewDecision;
  readonly voiceReadinessSnapshot: VoiceReadinessSnapshot | null;
}

export interface ModelPackageActionRequest {
  readonly modelPackageId: EntityId;
  readonly expectedManifestFingerprint: Sha256;
  readonly expectedInstallationRevision: number | null;
  readonly action: Exclude<ModelPackageAction, "install">;
  readonly reason: string;
  readonly idempotencyKey: string;
}

export interface ModelPackageActionResponse {
  readonly correlationId: EntityId;
  readonly installation: ModelInstallationRecord;
  readonly verification: ModelVerificationRecord | null;
}

export interface AuditionAudioDescriptor {
  readonly projectId: EntityId;
  readonly auditionClipId: EntityId;
  readonly auditionSessionId: EntityId;
  readonly audioArtifactId: EntityId;
  readonly expectedClipRevision: number;
  readonly expectedClipFingerprint: Sha256;
  readonly expectedArtifactSha256: Sha256;
  readonly mediaType: "audio/wav";
  readonly byteSize: number;
}
