/**
 * Desktop transport helpers for the canonical Phase 2 public contract.
 *
 * Public response and domain shapes are aliases of
 * `@cinematic-story-studio/contracts`; this module only adds the project/run
 * routing identity that the Electron IPC boundary needs.
 */
import {
  ANALYSIS_ENTITY_COLLECTIONS,
  ANALYSIS_GATE_IDS,
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
  type AnalysisConfidence,
  type AnalysisCorrection,
  type AnalysisCorrectionCategory,
  type AnalysisCorrectionPageResponse,
  type AnalysisCorrectionPatch,
  type AnalysisCorrectionRequestPatch,
  type AnalysisCorrectionRequestSelection,
  type AnalysisCorrectionResponse,
  type AnalysisCorrectionSelection,
  type AnalysisEntity,
  type AnalysisEntityCollection,
  type AnalysisEvidenceSpan,
  type AnalysisGateDecisionAction,
  type AnalysisGateId,
  type AnalysisGateReview,
  type AnalysisGateReviewListResponse,
  type AnalysisProfileReference,
  type AnalysisWarning,
  type CreateAnalysisCorrectionRequest,
  type CreateStoryAnalysisRunRequest,
  type CreateStoryAnalysisRunResponse,
  type DecideAnalysisGateRequest,
  type DecideAnalysisGateResponse,
  type HumanEffectiveBoundary,
  type HumanEffectiveRegistry,
  type JsonValue,
  type StoryAnalysisEntityPageResponse,
  type StoryAnalysisRun,
  type StoryAnalysisRunPageResponse,
  type StoryAnalysisRunResponse
} from "@cinematic-story-studio/contracts";

export {
  ANALYSIS_ENTITY_COLLECTIONS,
  ANALYSIS_GATE_IDS,
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION
};

export type {
  AnalysisConfidence,
  AnalysisCorrection,
  AnalysisCorrectionCategory,
  AnalysisCorrectionPatch,
  AnalysisCorrectionRequestPatch,
  AnalysisCorrectionRequestSelection,
  AnalysisCorrectionSelection,
  AnalysisEntity,
  AnalysisEntityCollection,
  AnalysisEvidenceSpan,
  AnalysisGateDecisionAction,
  AnalysisGateId,
  AnalysisGateReview,
  AnalysisProfileReference,
  AnalysisWarning,
  HumanEffectiveBoundary,
  HumanEffectiveRegistry,
  JsonValue,
  StoryAnalysisRun
};

export type AnalysisCollection = AnalysisEntityCollection;
export type AnalysisConfidenceClass = AnalysisConfidence["classification"];
export type AnalysisRun = StoryAnalysisRun;
export type AnalysisReview = AnalysisGateReview;
export type AnalysisEvidenceExcerpt = AnalysisEvidenceSpan;
export type AnalysisValue = JsonValue;

export type AnalysisRunsResponse = StoryAnalysisRunPageResponse;
export type AnalysisRunResponse = StoryAnalysisRunResponse;
export type CreateAnalysisRunResponse = CreateStoryAnalysisRunResponse;
export type AnalysisEntityPageResponse =
  StoryAnalysisEntityPageResponse<AnalysisEntityCollection>;
export type AnalysisCorrectionsResponse = AnalysisCorrectionPageResponse;
export type AppendAnalysisCorrectionResponse = AnalysisCorrectionResponse;
export type AnalysisReviewsResponse = AnalysisGateReviewListResponse;
export type DecideAnalysisReviewResponse = DecideAnalysisGateResponse;

export interface AnalysisProjectInput {
  readonly projectId: string;
}

export interface AnalysisRunIdInput extends AnalysisProjectInput {
  readonly runId: string;
}

export interface CreateAnalysisRunInput
  extends AnalysisProjectInput,
    CreateStoryAnalysisRunRequest {}

export interface ListAnalysisRunsInput extends AnalysisProjectInput {
  readonly cursor?: string;
  readonly limit?: number;
}

export type DialogueSpeakerState =
  | "unknown"
  | "ambiguous"
  | "proposed"
  | "corrected";

export interface ListAnalysisEntitiesInput extends AnalysisRunIdInput {
  /**
   * Immutable selected snapshot expected by the renderer. Main validates the
   * page and every returned entity against this identity.
   */
  readonly expectedSnapshotId: string;
  readonly collection: AnalysisEntityCollection;
  readonly cursor?: string;
  readonly limit?: number;
  /**
   * Global confidence ceiling applied by the service.
   */
  readonly confidenceMax?: number;
  /**
   * Global human-review filter applied by the service.
   */
  readonly requiresReview?: boolean;
  /**
   * Dialogue-only filter. Other collections reject this field in main.
   */
  readonly speakerState?: DialogueSpeakerState;
}

export interface ListAnalysisCorrectionsInput extends AnalysisRunIdInput {
  readonly cursor?: string;
  readonly limit?: number;
}

/**
 * Immutable selected-run evidence carried across IPC so main can bind every
 * returned review to the exact source, profile, and snapshot the renderer
 * requested rather than trusting the run id alone.
 */
export interface ListAnalysisReviewsInput extends AnalysisRunIdInput {
  readonly expectedSourceDocumentId: string;
  readonly expectedExtractionId: string;
  readonly expectedExtractionRevision: number;
  readonly expectedStoryId: string;
  readonly expectedProfileId: string;
  readonly expectedProfileFingerprint: string;
  readonly expectedRunFingerprint: string;
  readonly expectedSnapshotId: string;
  readonly expectedSnapshotRevision: number;
  readonly expectedSnapshotFingerprint: string;
}

export type AppendAnalysisCorrectionInput =
  AnalysisRunIdInput & CreateAnalysisCorrectionRequest;

export interface DecideAnalysisReviewInput
  extends AnalysisRunIdInput,
    DecideAnalysisGateRequest {
  readonly gateId: AnalysisGateId;
}
