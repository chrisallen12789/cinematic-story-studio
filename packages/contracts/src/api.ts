import type {
  ApprovalDecision,
  CastingAssignment,
  Chapter,
  Character,
  ContractWarning,
  DialogueAttribution,
  DialogueLine,
  EntityId,
  HumanCorrection,
  ImportedStory,
  IsoDateTime,
  Project,
  Scene,
  Sha256,
  SourceDocument,
  StoryBeat
} from "./domain.js";

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

export type DeclaredImportFormat =
  | "txt"
  | "markdown"
  | "docx"
  | "epub"
  | "pdf";

/**
 * Metadata fields accompanying the streamed multipart `file` part.
 * File bytes are intentionally not represented as a JSON/string DTO.
 */
export interface ImportStoryMultipartFields {
  readonly declaredFormat?: DeclaredImportFormat;
}

export interface ImportStoryResponse extends CorrelatedResponse {
  readonly sourceDocument: SourceDocument;
  readonly story: ImportedStory;
}

export interface CastingPlaceholder {
  readonly characterId: EntityId;
  readonly status: "unassigned";
  readonly providerId: null;
  readonly voiceId: null;
}

export interface ProjectDetail extends CorrelatedResponse {
  readonly project: Project;
  readonly sourceDocuments: readonly SourceDocument[];
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

export type JobType = "analyze_story";

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
  readonly type: JobType;
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

export const API_V1_PATHS = {
  health: "/api/v1/health",
  providerHealth: "/api/v1/providers/health",
  ffmpegCapability: "/api/v1/capabilities/ffmpeg",
  projects: "/api/v1/projects",
  project: (projectId: EntityId) => `/api/v1/projects/${projectId}`,
  projectImports: (projectId: EntityId) =>
    `/api/v1/projects/${projectId}/imports`,
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
