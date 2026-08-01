import type {
  ApprovalDecision,
  CastingAssignment,
  Chapter,
  Character,
  ContractWarning,
  DialogueAttribution,
  DialogueLine,
  DocumentFormat,
  EntityId,
  HumanCorrection,
  ImportedStory,
  IsoDateTime,
  Project,
  Provenance,
  SchemaVersion,
  Scene,
  Sha256,
  SourceDocument,
  SourceMediaType,
  StoryBeat
} from "./domain.js";
import type {
  AnalysisCorrection,
  AnalysisCorrectionRequestSelection,
  AnalysisEntityCollection,
  AnalysisEntityMap,
  AnalysisGateDecision,
  AnalysisGateId,
  AnalysisGateReview,
  AnalysisProfileReference,
  StoryAnalysisRun
} from "./story-analysis.js";
import type {
  ApprovedCastSnapshot,
  CastAssignment,
  CastingCandidate,
  CastingConflict,
  CastingCorrection,
  CastingCorrectionSelection,
  CastingGateId,
  CastingGateReview,
  CastingProfile,
  CastingReviewDecision,
  CastingRun,
  CastingVoiceProfile,
  CustomProductionRole,
  ProductionRole,
  ProductionRoleRequirement,
  VoiceCastingContractVersion,
  VoiceCatalogRevision,
  VoiceModelDescriptor,
  VoiceProviderDescriptor,
  VoiceRightsRecord
} from "./voice-casting.js";
import type { AuditionGateId } from "./local-speech-auditions.js";

export interface ApiError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  readonly correlationId: EntityId;
  readonly details?: Readonly<Record<string, string | number | boolean>>;
}

export interface ApiErrorResponse {
  readonly error: ApiError;
}

export interface CorrelatedResponse {
  readonly correlationId: EntityId;
}

export interface HealthResponse extends CorrelatedResponse {
  readonly status: "starting" | "ready" | "degraded";
  readonly serviceVersion: string;
  readonly contractVersion: "1.0.0";
  readonly instanceId: EntityId;
  readonly database: {
    readonly status: "starting" | "ready" | "degraded" | "unavailable";
  };
  readonly checkedAt: IsoDateTime;
}

export interface ProviderHealth {
  readonly providerId: string;
  readonly kind: "speech" | "language" | "sound" | "music";
  readonly executionLocation: "local" | "cloud";
  readonly status:
    | "available"
    | "degraded"
    | "unavailable"
    | "disabled"
    | "unauthorized";
  readonly capabilities: readonly string[];
  readonly version?: string;
  readonly redactedReason?: string;
  readonly checkedAt: IsoDateTime;
}

export interface ProviderHealthResponse extends CorrelatedResponse {
  readonly providers: readonly ProviderHealth[];
}

export interface FfmpegCapabilityResponse extends CorrelatedResponse {
  readonly status: "available" | "missing" | "incompatible" | "failed";
  readonly executableOrigin: "bundled" | "configured" | "path_lookup" | "none";
  readonly version?: string;
  readonly capabilities: readonly string[];
  readonly missingCapabilities: readonly string[];
  readonly redactedReason?: string;
  readonly checkedAt: IsoDateTime;
}

export interface ProjectSummary {
  readonly projectId: EntityId;
  readonly name: string;
  readonly status: Project["status"];
  readonly revision: number;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
}

export interface ProjectPageResponse extends CorrelatedResponse {
  readonly items: readonly ProjectSummary[];
  readonly nextCursor?: string;
}

export interface CreateProjectRequest {
  readonly name: string;
}

export interface CreateProjectResponse extends CorrelatedResponse {
  readonly project: Project;
}

export type DeclaredImportFormat = DocumentFormat;

/**
 * Metadata fields accompanying the streamed multipart `file` part.
 * File bytes are intentionally not represented as a JSON/string DTO.
 */
export interface ImportStoryMultipartFields {
  readonly declaredFormat?: DeclaredImportFormat;
}

export type DocumentExtractionStatus =
  | "pending"
  | "running"
  | "complete"
  | "partial"
  | "failed";

export interface DocumentExtractionSummary {
  readonly schemaVersion: SchemaVersion;
  readonly revision: number;
  readonly provenance: Provenance;
  readonly extractionId: EntityId;
  readonly projectId: EntityId;
  readonly sourceDocumentId: EntityId;
  readonly declaredFormat: DeclaredImportFormat;
  readonly detectedFormat: DeclaredImportFormat;
  readonly mediaType: SourceMediaType;
  readonly status: DocumentExtractionStatus;
  readonly adapterId: string;
  readonly adapterVersion: string;
  readonly parserDependency: string;
  readonly parserVersion: string;
  readonly sourceSha256: Sha256;
  readonly sourceByteCount: number;
  readonly extractedTitle?: string;
  readonly extractedTextSha256?: Sha256;
  readonly extractedCharacterCount?: number;
  readonly sectionCount?: number;
  readonly pageCount?: number;
  readonly warnings: readonly ContractWarning[];
  readonly quality: {
    readonly classification:
      | "pending"
      | "exact_text_decode"
      | "structured_extraction"
      | "page_text_extraction"
      | "low_text_density"
      | "review_required";
    readonly confidence: number;
  };
  readonly retryability: "retryable" | "not_retryable";
  readonly reviewRequired: boolean;
  readonly originalPreserved: true;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly completedAt?: IsoDateTime;
}

export type ImportReviewState =
  | "pending"
  | "approved"
  | "changes_requested"
  | "rejected"
  | "invalidated";

export interface ImportReview {
  readonly schemaVersion: SchemaVersion;
  readonly revision: number;
  readonly provenance: Provenance;
  readonly reviewId: EntityId;
  readonly projectId: EntityId;
  readonly sourceDocumentId: EntityId;
  readonly extractionId: EntityId;
  readonly candidateStoryId: EntityId;
  readonly candidateStoryRevision: number;
  readonly state: ImportReviewState;
  readonly evidenceFingerprint: Sha256;
  /**
   * A service-bounded display excerpt. It is private story content and must
   * never be copied to diagnostics, telemetry, or generic project listings.
   */
  readonly previewText: string;
  readonly previewTruncated: boolean;
  readonly warnings: readonly ContractWarning[];
  readonly latestDecision?: ApprovalDecision;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
}

export interface ImportStoryResponse extends CorrelatedResponse {
  readonly sourceDocument: SourceDocument;
  readonly extraction: DocumentExtractionSummary;
  readonly job: Job;
}

export interface ReextractImportResponse extends CorrelatedResponse {
  readonly extraction: DocumentExtractionSummary;
  readonly job: Job;
}

export interface ImportReviewResponse extends CorrelatedResponse {
  readonly review: ImportReview;
}

export type ImportReviewDecision =
  | "approved"
  | "changes_requested"
  | "rejected";

export interface DecideImportReviewRequest {
  readonly reviewId: EntityId;
  readonly decision: ImportReviewDecision;
  readonly rationale?: string;
  readonly expectedRevision: number;
  readonly evidenceFingerprint: Sha256;
  readonly idempotencyKey: string;
}

export interface DecideImportReviewResponse extends CorrelatedResponse {
  readonly review: ImportReview;
  readonly decision: ApprovalDecision;
  readonly projectRevision: number;
  readonly analysisAllowed: boolean;
}

export interface CastingPlaceholder {
  readonly characterId: EntityId;
  readonly status: "unassigned";
  readonly providerId: null;
  readonly voiceId: null;
}

export interface VoiceCastingProjectSummary {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly currentRun: CastingRun | null;
  readonly catalogRevision: VoiceCatalogRevision;
  readonly catalogFingerprint: Sha256;
  readonly profile: CastingProfile;
  readonly gateReviews: readonly CastingGateReview[];
}

export interface ProjectDetail extends CorrelatedResponse {
  readonly project: Project;
  readonly sourceDocuments: readonly SourceDocument[];
  readonly extractions: readonly DocumentExtractionSummary[];
  readonly importReviews: readonly ImportReview[];
  readonly analysisAllowed: boolean;
  readonly story: ImportedStory | null;
  readonly chapters: readonly Chapter[];
  readonly scenes: readonly Scene[];
  readonly beats: readonly StoryBeat[];
  readonly characters: readonly Character[];
  readonly dialogueLines: readonly DialogueLine[];
  readonly dialogueAttributions: readonly DialogueAttribution[];
  readonly castingAssignments: readonly CastingAssignment[];
  readonly castingPlaceholders: readonly CastingPlaceholder[];
  readonly approvals: readonly ApprovalDecision[];
  readonly jobs: readonly Job[];
  readonly currentAnalysisRun: StoryAnalysisRun | null;
  readonly analysisGateReviews: readonly AnalysisGateReview[];
  readonly voiceCasting: VoiceCastingProjectSummary;
  readonly currentCastingRun: CastingRun | null;
  readonly castingGateReviews: readonly CastingGateReview[];
}

export interface CorrectDialogueSpeakerRequest {
  readonly characterId: EntityId | null;
  readonly reason?: string;
  readonly expectedRevision: number;
}

export interface CorrectDialogueSpeakerResponse extends CorrelatedResponse {
  readonly attribution: DialogueAttribution;
  readonly appendedCorrection: HumanCorrection;
  readonly projectRevision: number;
  readonly lineRevision: number;
}

export type JobType =
  | "extract_document"
  | "analyze_story"
  | "analyze_whole_book"
  | "analyze_casting"
  | "generate_audition";

export type JobState =
  | "queued"
  | "running"
  | "cancel_requested"
  | "cancelled"
  | "failed"
  | "interrupted"
  | "paused"
  | "succeeded";

export interface JobError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export interface Job {
  readonly jobId: EntityId;
  readonly projectId: EntityId;
  readonly type: JobType;
  readonly state: JobState;
  readonly target: {
    readonly type:
      | "document_extraction"
      | "story"
      | "analysis_run"
      | "casting_run"
      | "audition_session";
    readonly id: EntityId;
  };
  readonly inputRevision: number;
  readonly inputFingerprint: Sha256;
  readonly attempt: number;
  readonly stage: string;
  readonly progress: number;
  readonly checkpointAvailable: boolean;
  readonly cancellationRequested: boolean;
  readonly warnings: readonly ContractWarning[];
  readonly error?: JobError;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly terminalAt?: IsoDateTime;
}

export interface CreateJobRequest {
  readonly type: "analyze_story";
  readonly inputRevision: number;
  readonly idempotencyKey: string;
}

export interface JobResponse extends CorrelatedResponse {
  readonly job: Job;
}

export type JobEventType =
  | "created"
  | "state_changed"
  | "progress"
  | "checkpoint"
  | "warning"
  | "failed"
  | "completed";

export interface JobEvent {
  readonly jobId: EntityId;
  readonly attempt: number;
  readonly sequence: number;
  readonly type: JobEventType;
  readonly state?: JobState;
  readonly stage?: string;
  readonly progress?: number;
  readonly completedUnits?: number;
  readonly totalUnits?: number;
  readonly warning?: ContractWarning;
  readonly error?: JobError;
  readonly createdAt: IsoDateTime;
}

export interface JobEventsResponse extends CorrelatedResponse {
  readonly events: readonly JobEvent[];
  readonly lastSequence: number;
}

export interface CursorPageRequest {
  readonly cursor?: string;
  /**
   * Defaults to 50. Runtime validation rejects values above 200.
   */
  readonly limit?: number;
}

interface StoryAnalysisEntityFilterRequest extends CursorPageRequest {
  /**
   * Inclusive upper confidence bound in [0, 1].
   */
  readonly confidenceMax?: number;
  readonly requiresReview?: boolean;
}

export type StoryAnalysisEntityPageRequest<
  TCollection extends AnalysisEntityCollection
> = StoryAnalysisEntityFilterRequest &
  (TCollection extends "dialogue-lines"
    ? {
        readonly speakerState?:
          | "unknown"
          | "ambiguous"
          | "proposed"
          | "corrected";
      }
    : {
        readonly speakerState?: never;
      });

export interface CursorPageResponse extends CorrelatedResponse {
  readonly pageSize: number;
  readonly total: number;
  readonly nextCursor?: string;
}

export interface CreateStoryAnalysisRunRequest {
  readonly expectedExtractionId: EntityId;
  readonly expectedExtractionRevision: number;
  readonly expectedReviewId: EntityId;
  readonly expectedReviewRevision: number;
  readonly expectedEvidenceFingerprint: Sha256;
  readonly expectedProfileFingerprint: Sha256;
  readonly profile: AnalysisProfileReference;
  readonly idempotencyKey: string;
}

export interface CreateStoryAnalysisRunResponse extends CorrelatedResponse {
  readonly run: StoryAnalysisRun;
  readonly job: Job;
}

export interface StoryAnalysisRunResponse extends CorrelatedResponse {
  readonly run: StoryAnalysisRun;
}

export interface StoryAnalysisRunPageResponse extends CursorPageResponse {
  readonly runs: readonly StoryAnalysisRun[];
}

export interface StoryAnalysisEntityPageResponse<
  TCollection extends AnalysisEntityCollection
> extends CursorPageResponse {
  readonly collection: TCollection;
  readonly runId: EntityId;
  readonly snapshotId: EntityId;
  readonly items: readonly AnalysisEntityMap[TCollection][];
}

interface CreateAnalysisCorrectionRequestBase {
  readonly targetEntityId: EntityId;
  readonly expectedTargetRevision: number;
  readonly expectedRunFingerprint: Sha256;
  readonly previousValueFingerprint: Sha256;
  /**
   * Nonblank human rationale, bounded to 1,000 Unicode code points at the
   * service boundary.
   */
  readonly reason: string;
  readonly supersedesCorrectionId?: EntityId;
  readonly idempotencyKey: string;
}

export type CreateAnalysisCorrectionRequest =
  CreateAnalysisCorrectionRequestBase & AnalysisCorrectionRequestSelection;

export interface AnalysisCorrectionResponse extends CorrelatedResponse {
  readonly correction: AnalysisCorrection;
  readonly invalidatedGateIds: readonly AnalysisGateId[];
  readonly run: StoryAnalysisRun;
  readonly reviews: readonly AnalysisGateReview[];
}

export interface AnalysisCorrectionPageResponse extends CursorPageResponse {
  readonly runId: EntityId;
  readonly items: readonly AnalysisCorrection[];
}

export interface AnalysisGateReviewListResponse extends CorrelatedResponse {
  readonly runId: EntityId;
  readonly items: readonly AnalysisGateReview[];
}

export type AnalysisGateDecisionAction =
  | "approve"
  | "request_changes"
  | "reject";

export interface DecideAnalysisGateRequest {
  readonly decision: AnalysisGateDecisionAction;
  readonly expectedRevision: number;
  readonly expectedArtifactFingerprint: Sha256;
  readonly expectedEvidenceFingerprint: Sha256;
  readonly acknowledgedWarningIds: readonly EntityId[];
  /**
   * Nonblank human rationale, bounded to 4,000 Unicode code points at the
   * service boundary.
   */
  readonly rationale: string;
  readonly idempotencyKey: string;
}

export interface DecideAnalysisGateResponse extends CorrelatedResponse {
  readonly review: AnalysisGateReview;
  readonly decision: AnalysisGateDecision;
  readonly run: StoryAnalysisRun;
}

export interface CastingCatalogPageRequest extends CursorPageRequest {
  readonly expectedCatalogRevisionId?: EntityId;
  readonly expectedCatalogFingerprint?: Sha256;
}

export interface CastingCatalogPageResponse extends CursorPageResponse {
  readonly catalogRevision: VoiceCatalogRevision;
  readonly providers: readonly VoiceProviderDescriptor[];
  readonly models: readonly VoiceModelDescriptor[];
  readonly items: readonly CastingVoiceProfile[];
  readonly rights: readonly VoiceRightsRecord[];
}

export interface CreateCastingRunRequest {
  readonly expectedAnalysisRunId: EntityId;
  readonly expectedSnapshotId: EntityId;
  readonly expectedSnapshotRevision: number;
  readonly expectedSnapshotFingerprint: Sha256;
  readonly expectedCorrectionSetFingerprint: Sha256;
  readonly expectedImportReviewDecisionId: EntityId;
  readonly expectedAnalysisGateDecisionIds: {
    readonly storyStructureReview: EntityId;
    readonly characterRegistryReview: EntityId;
    readonly dialogueAttributionReview: EntityId;
    readonly wholeBookAnalysisReview: EntityId;
  };
  readonly expectedCatalogRevisionId: EntityId;
  readonly expectedCatalogFingerprint: Sha256;
  readonly expectedCastingProfileFingerprint: Sha256;
  readonly idempotencyKey: string;
}

export interface CreateCastingRunResponse extends CorrelatedResponse {
  readonly run: CastingRun;
  readonly job: Job;
}

export interface CastingRunResponse extends CorrelatedResponse {
  readonly run: CastingRun;
}

export interface CastingRunPageResponse extends CursorPageResponse {
  readonly items: readonly CastingRun[];
}

export interface CastingEvidenceRequest {
  readonly expectedRunFingerprint: Sha256;
  readonly expectedCatalogRevisionId: EntityId;
  readonly expectedCatalogFingerprint: Sha256;
  readonly expectedSnapshotId: EntityId;
  readonly expectedSnapshotRevision: number;
  readonly expectedSnapshotFingerprint: Sha256;
}

export type CastingEvidencePageRequest =
  CastingEvidenceRequest & CursorPageRequest;

export type ProductionRolePageRequest = CastingEvidencePageRequest;

export interface ProductionRolePageResponse extends CursorPageResponse {
  readonly castingRunId: EntityId;
  readonly items: readonly ProductionRole[];
}

export interface CreateCustomProductionRoleRequest
  extends CastingEvidenceRequest {
  readonly definitionId: EntityId;
  readonly label: string;
  readonly performanceRequirements: ProductionRoleRequirement;
  readonly reason: string;
  readonly expectedCorrectionSetFingerprint: Sha256;
  readonly expectedCastingProfileFingerprint: Sha256;
  readonly idempotencyKey: string;
}

export interface CreateCustomProductionRoleResponse
  extends CorrelatedResponse {
  readonly role: CustomProductionRole;
  readonly invalidatedGateIds: readonly CastingGateId[];
  readonly run: CastingRun;
  readonly reviews: readonly CastingGateReview[];
}

export interface CastingCandidatePageRequest
  extends CastingEvidencePageRequest {
  readonly expectedRoleRevision: number;
}

export interface CastingCandidatePageResponse extends CursorPageResponse {
  readonly castingRunId: EntityId;
  readonly items: readonly CastingCandidate[];
}

export type CastingConflictPageRequest = CastingEvidencePageRequest;

export interface CastingConflictPageResponse extends CursorPageResponse {
  readonly castingRunId: EntityId;
  readonly items: readonly CastingConflict[];
}

export type CastAssignmentPageRequest = CastingEvidencePageRequest;

export interface CastAssignmentPageResponse extends CursorPageResponse {
  readonly castingRunId: EntityId;
  readonly items: readonly CastAssignment[];
}

export type CastingCorrectionPageRequest = CastingEvidencePageRequest;

export type CastingCorrectionRequestValue =
  CastingCorrectionSelection["correctedValue"];

export interface AppendCastingCorrectionRequest {
  readonly operation: CastingCorrection["category"];
  readonly targetRoleId: EntityId;
  readonly expectedRoleRevision: number;
  readonly expectedRunFingerprint: Sha256;
  readonly expectedCatalogFingerprint: Sha256;
  readonly expectedSnapshotFingerprint: Sha256;
  readonly expectedCorrectionSetFingerprint: Sha256;
  readonly previousEffectiveFingerprint: Sha256;
  readonly voiceProfileId?: EntityId | null;
  readonly correctedValue?: CastingCorrectionRequestValue | null;
  readonly reason: string;
  readonly supersedesCorrectionId?: EntityId | null;
  readonly idempotencyKey: string;
}

export interface AppendCastingCorrectionResponse extends CorrelatedResponse {
  readonly correction: CastingCorrection;
  readonly assignment: CastAssignment | null;
  readonly invalidatedGateIds: readonly CastingGateId[];
  readonly run: CastingRun;
  readonly reviews: readonly CastingGateReview[];
}

export interface CastingCorrectionPageResponse extends CursorPageResponse {
  readonly castingRunId: EntityId;
  readonly items: readonly CastingCorrection[];
}

export interface CastingReviewListRequest
  extends CastingEvidenceRequest {
  readonly expectedApprovedCastSnapshotId: EntityId;
  readonly expectedApprovedCastSnapshotRevision: number;
}

export interface CastingGateReviewListResponse extends CorrelatedResponse {
  readonly castingRunId: EntityId;
  readonly items: readonly CastingGateReview[];
}

export interface DecideCastingReviewRequest {
  readonly decision: "approve" | "request_changes" | "reject";
  readonly expectedRevision: number;
  readonly expectedEvidenceFingerprint: Sha256;
  readonly expectedRunFingerprint: Sha256;
  readonly expectedApprovedCastSnapshotId: EntityId;
  readonly expectedApprovedCastSnapshotRevision: number;
  readonly warningAcknowledgementIds?: readonly EntityId[];
  readonly rationale: string;
  readonly supersedesDecisionId?: EntityId | null;
  readonly idempotencyKey: string;
}

export interface DecideCastingReviewResponse extends CorrelatedResponse {
  readonly review: CastingGateReview;
  readonly decision: CastingReviewDecision;
  readonly snapshot: ApprovedCastSnapshot;
  readonly run: CastingRun;
}

export const API_V1_PATHS = {
  health: "/api/v1/health",
  providerHealth: "/api/v1/providers/health",
  ffmpegCapability: "/api/v1/capabilities/ffmpeg",
  projects: "/api/v1/projects",
  project: (projectId: EntityId) => `/api/v1/projects/${projectId}`,
  projectImports: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/imports`,
  projectImportReextract: (
    projectId: EntityId,
    sourceDocumentId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/imports/${sourceDocumentId}/reextract`,
  projectImportReview: (projectId: EntityId, reviewId: EntityId) =>
    `/api/v1/projects/${projectId}/imports/${reviewId}/review`,
  projectImportReviewDecision: (
    projectId: EntityId,
    reviewId: EntityId
  ) => `/api/v1/projects/${projectId}/imports/${reviewId}/review/decision`,
  projectAnalysisRuns: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/analysis-runs`,
  projectAnalysisRun: (projectId: EntityId, runId: EntityId) =>
    `/api/v1/projects/${projectId}/analysis-runs/${runId}`,
  projectAnalysisRunEntities: (
    projectId: EntityId,
    runId: EntityId,
    collection: AnalysisEntityCollection
  ) =>
    `/api/v1/projects/${projectId}/analysis-runs/${runId}/entities/${collection}`,
  projectAnalysisRunCorrections: (
    projectId: EntityId,
    runId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/analysis-runs/${runId}/corrections`,
  projectAnalysisRunReviews: (
    projectId: EntityId,
    runId: EntityId
  ) => `/api/v1/projects/${projectId}/analysis-runs/${runId}/reviews`,
  projectAnalysisRunReviewDecision: (
    projectId: EntityId,
    runId: EntityId,
    gateId: AnalysisGateId
  ) =>
    `/api/v1/projects/${projectId}/analysis-runs/${runId}/reviews/${gateId}/decisions`,
  projectCastingCatalog: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/casting/catalog`,
  projectCastingRuns: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/casting-runs`,
  projectCastingRun: (
    projectId: EntityId,
    castingRunId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}`,
  projectCastingRunRoles: (
    projectId: EntityId,
    castingRunId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}/roles`,
  projectCastingRunCandidates: (
    projectId: EntityId,
    castingRunId: EntityId,
    roleId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}/roles/${roleId}/candidates`,
  projectCastingRunConflicts: (
    projectId: EntityId,
    castingRunId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}/conflicts`,
  projectCastingRunAssignments: (
    projectId: EntityId,
    castingRunId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}/assignments`,
  projectCastingRunCorrections: (
    projectId: EntityId,
    castingRunId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}/corrections`,
  projectCastingRunReviews: (
    projectId: EntityId,
    castingRunId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}/reviews`,
  projectCastingRunReviewDecision: (
    projectId: EntityId,
    castingRunId: EntityId,
    gateId: CastingGateId
  ) =>
    `/api/v1/projects/${projectId}/casting-runs/${castingRunId}/reviews/${gateId}/decisions`,
  projectAuditionWorkspace: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/auditions/workspace`,
  projectSpeechModelPackages: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/speech/model-packages`,
  projectSpeechModelPackageActions: (
    projectId: EntityId,
    modelPackageId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/speech/model-packages/${modelPackageId}/actions`,
  projectPronunciationEntries: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/pronunciations/entries`,
  projectAuditionSessions: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/audition-sessions`,
  projectAuditionSessionScripts: (
    projectId: EntityId,
    auditionSessionId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/audition-sessions/${auditionSessionId}/scripts`,
  projectAuditionSessionNormalizationPreview: (
    projectId: EntityId,
    auditionSessionId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/audition-sessions/${auditionSessionId}/normalization-preview`,
  projectAuditionSessionGenerate: (
    projectId: EntityId,
    auditionSessionId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/audition-sessions/${auditionSessionId}/generate`,
  projectAuditionClips: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/audition-clips`,
  projectAuditionReviewDecisions: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/audition-review-decisions`,
  projectAuditionClipAudio: (
    projectId: EntityId,
    auditionClipId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/audition-clips/${auditionClipId}/audio`,
  projectAuditionReviewDecision: (
    projectId: EntityId,
    gateId: AuditionGateId,
    reviewId: EntityId
  ) =>
    `/api/v1/projects/${projectId}/audition-reviews/${gateId}/${reviewId}/decisions`,
  dialogueSpeaker: (projectId: EntityId, lineId: EntityId) =>
    `/api/v1/projects/${projectId}/dialogue-lines/${lineId}/speaker`,
  projectJobs: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/jobs`,
  job: (jobId: EntityId) => `/api/v1/jobs/${jobId}`,
  jobEvents: (jobId: EntityId, afterSequence?: number) =>
    afterSequence === undefined
      ? `/api/v1/jobs/${jobId}/events`
      : `/api/v1/jobs/${jobId}/events?afterSequence=${afterSequence}`,
  jobCancel: (jobId: EntityId) => `/api/v1/jobs/${jobId}/cancel`,
  jobRetry: (jobId: EntityId) => `/api/v1/jobs/${jobId}/retry`,
  jobResume: (jobId: EntityId) => `/api/v1/jobs/${jobId}/resume`
} as const;
