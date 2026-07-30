import type {
  CorrectDialogueSpeakerResponse,
  CreateProjectResponse,
  DecideImportReviewResponse,
  FfmpegCapabilityResponse,
  HealthResponse,
  ImportReviewDecision,
  ImportReviewResponse,
  ImportStoryResponse,
  JobEventsResponse,
  JobResponse,
  JobType,
  ProjectDetail,
  ProjectPageResponse,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";

export const DESKTOP_CONTRACT_VERSION = "1.0.0" as const;

export const IPC_CHANNELS = {
  backendStatus: "css:backend:status",
  backendGetStatus: "css:backend:get-status",
  backendReconnect: "css:backend:reconnect",
  projectsList: "css:projects:list",
  projectsCreate: "css:projects:create",
  projectsOpen: "css:projects:open",
  projectsRestoreRecent: "css:projects:restore-recent",
  projectsImportSelectedFile: "css:projects:import-selected-file",
  projectsGetImportReview: "css:projects:get-import-review",
  projectsDecideImportReview: "css:projects:decide-import-review",
  dialogueCorrectSpeaker: "css:dialogue:correct-speaker",
  jobsCreate: "css:jobs:create",
  jobsGet: "css:jobs:get",
  jobsEvents: "css:jobs:events",
  jobsCancel: "css:jobs:cancel",
  jobsRetry: "css:jobs:retry",
  jobsResume: "css:jobs:resume",
  providersHealth: "css:providers:health",
  ffmpegCapability: "css:capabilities:ffmpeg"
} as const;

export type BackendConnectionState =
  | "starting"
  | "ready"
  | "degraded"
  | "disconnected"
  | "unavailable"
  | "stopping"
  | "stopped";

export interface BackendSnapshot {
  readonly state: BackendConnectionState;
  readonly message: string;
  readonly checkedAt: string;
  readonly health?: HealthResponse;
}

export interface DesktopError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  readonly correlationId?: string;
  readonly details?: Readonly<Record<string, string | number | boolean>>;
}

export type DesktopResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: DesktopError };

export interface DesktopRequest<T> {
  readonly contractVersion: typeof DESKTOP_CONTRACT_VERSION;
  readonly payload: T;
}

export interface CreateProjectInput {
  readonly name: string;
  readonly idempotencyKey: string;
}

export interface ProjectIdInput {
  readonly projectId: string;
}

export interface ImportReviewIdInput extends ProjectIdInput {
  readonly reviewId: string;
  readonly sourceDocumentId: string;
  readonly extractionId: string;
  readonly candidateStoryId: string;
  readonly candidateStoryRevision: number;
  readonly evidenceFingerprint: string;
}

export interface DecideImportReviewInput extends ImportReviewIdInput {
  readonly decision: ImportReviewDecision;
  readonly rationale?: string;
  readonly expectedRevision: number;
  readonly evidenceFingerprint: string;
  readonly idempotencyKey: string;
}

export interface CorrectSpeakerInput {
  readonly projectId: string;
  readonly lineId: string;
  readonly characterId: string | null;
  readonly reason?: string;
  readonly expectedRevision: number;
}

export interface CreateJobInput {
  readonly projectId: string;
  readonly type: JobType;
  readonly inputRevision: number;
  readonly idempotencyKey: string;
}

export interface JobIdInput {
  readonly jobId: string;
}

export interface JobEventsInput extends JobIdInput {
  readonly afterSequence?: number;
}

export interface CinematicStoryDesktopApi {
  readonly version: typeof DESKTOP_CONTRACT_VERSION;
  readonly backend: {
    readonly getStatus: () => Promise<DesktopResult<BackendSnapshot>>;
    readonly reconnect: () => Promise<DesktopResult<BackendSnapshot>>;
    readonly onStatus: (
      listener: (snapshot: BackendSnapshot) => void
    ) => () => void;
  };
  readonly projects: {
    readonly list: () => Promise<DesktopResult<ProjectPageResponse>>;
    readonly create: (
      input: CreateProjectInput
    ) => Promise<DesktopResult<CreateProjectResponse>>;
    readonly open: (
      projectId: string
    ) => Promise<DesktopResult<ProjectDetail>>;
    readonly restoreRecent: () => Promise<DesktopResult<ProjectDetail | null>>;
    readonly importSelectedFile: (
      projectId: string
    ) => Promise<DesktopResult<ImportStoryResponse | null>>;
    readonly getImportReview: (
      input: ImportReviewIdInput
    ) => Promise<DesktopResult<ImportReviewResponse>>;
    readonly decideImportReview: (
      input: DecideImportReviewInput
    ) => Promise<DesktopResult<DecideImportReviewResponse>>;
  };
  readonly dialogue: {
    readonly correctSpeaker: (
      input: CorrectSpeakerInput
    ) => Promise<DesktopResult<CorrectDialogueSpeakerResponse>>;
  };
  readonly jobs: {
    readonly create: (
      input: CreateJobInput
    ) => Promise<DesktopResult<JobResponse>>;
    readonly get: (jobId: string) => Promise<DesktopResult<JobResponse>>;
    readonly events: (
      jobId: string,
      afterSequence?: number
    ) => Promise<DesktopResult<JobEventsResponse>>;
    readonly cancel: (jobId: string) => Promise<DesktopResult<JobResponse>>;
    readonly retry: (jobId: string) => Promise<DesktopResult<JobResponse>>;
    readonly resume: (jobId: string) => Promise<DesktopResult<JobResponse>>;
  };
  readonly providers: {
    readonly health: () => Promise<DesktopResult<ProviderHealthResponse>>;
  };
  readonly capabilities: {
    readonly ffmpeg: () => Promise<DesktopResult<FfmpegCapabilityResponse>>;
  };
}
