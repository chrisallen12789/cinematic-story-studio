/**
 * Renderer-safe routing types for the canonical Phase 3A contract.
 *
 * Domain and response DTOs come directly from the shared contracts package.
 * This module adds only the project/run ownership evidence carried across the
 * Electron IPC boundary.
 */
import {
  CASTING_CORRECTION_CATEGORIES,
  CASTING_GATE_IDS,
  CASTING_JOB_STAGES,
  GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
  GOVERNED_VOICE_CASTING_PROFILE_ID,
  VOICE_CASTING_CONTRACT_VERSION,
  VOICE_CASTING_LIMITS,
  type ApprovedCastSnapshot,
  type CastAssignment,
  type CastingCandidate,
  type CastingConflict,
  type CastingCorrection,
  type CastingCorrectionCategory,
  type CastingGateId,
  type CastingGateReview,
  type CastingReviewDecision,
  type CastingRun,
  type CastingVoiceProfile,
  type CustomProductionRole,
  type ProductionRole,
  type ProductionRoleType,
  type VoiceCatalogRevision,
  type VoiceModelDescriptor,
  type VoiceProviderDescriptor,
  type VoiceRightsRecord
} from "@cinematic-story-studio/contracts";
import type {
  AppendCastingCorrectionRequest,
  AppendCastingCorrectionResponse,
  CastAssignmentPageRequest,
  CastAssignmentPageResponse,
  CastingCandidatePageRequest,
  CastingCandidatePageResponse,
  CastingCatalogPageRequest,
  CastingCatalogPageResponse,
  CastingConflictPageRequest,
  CastingConflictPageResponse,
  CastingCorrectionPageRequest,
  CastingCorrectionPageResponse,
  CastingCorrectionRequestValue,
  CastingEvidenceRequest,
  CastingGateReviewListResponse,
  CastingRunPageResponse,
  CastingRunResponse,
  CastingReviewListRequest,
  CreateCustomProductionRoleRequest,
  CreateCustomProductionRoleResponse,
  CreateCastingRunResponse,
  CreateCastingRunRequest,
  CursorPageRequest,
  DecideCastingReviewRequest,
  DecideCastingReviewResponse,
  ProductionRolePageRequest,
  ProductionRolePageResponse
} from "@cinematic-story-studio/contracts/api";

export {
  CASTING_CORRECTION_CATEGORIES as CASTING_CORRECTION_OPERATIONS,
  CASTING_GATE_IDS,
  CASTING_JOB_STAGES,
  GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT as CASTING_PROFILE_FINGERPRINT,
  GOVERNED_VOICE_CASTING_PROFILE_ID as CASTING_PROFILE_ID,
  VOICE_CASTING_CONTRACT_VERSION as CASTING_CONTRACT_VERSION
};

export const CASTING_PROFILE_VERSION = "1.0.0" as const;
export const CASTING_ROLE_TYPES = [
  "primary_narrator",
  "secondary_narrator",
  "named_character",
  "unresolved_speaker",
  "group_or_crowd",
  "quoted_document_or_announcement",
  "internal_thought",
  "custom"
] as const satisfies readonly ProductionRoleType[];
export const CASTING_LIMITS = {
  maximumProductionRoles: VOICE_CASTING_LIMITS.maximumProductionRoles,
  maximumPageSize: VOICE_CASTING_LIMITS.maximumPageSize,
  defaultPageSize: VOICE_CASTING_LIMITS.defaultPageSize,
  maximumCandidatesPerRole:
    VOICE_CASTING_LIMITS.maximumFinalCandidatesPerRole,
  maximumExplanationCodePoints:
    VOICE_CASTING_LIMITS.maximumExplanationCodePoints,
  maximumCorrectionReasonCodePoints: 1_000,
  maximumCustomRationaleCodePoints: 2_000,
  maximumReviewRationaleCodePoints: 4_000,
  maximumWarningAcknowledgements:
    VOICE_CASTING_LIMITS.maximumWarningsPerEntity
} as const;

export type {
  ApprovedCastSnapshot,
  CastAssignment,
  CastingCandidate,
  CastingConflict,
  CastingCorrection,
  CastingCorrectionCategory as CastingCorrectionOperation,
  CastingGateId,
  CastingGateReview as CastingReview,
  CastingReviewDecision,
  CastingRun,
  CastingVoiceProfile as VoiceProfile,
  CustomProductionRole,
  ProductionRole,
  ProductionRoleType,
  VoiceCatalogRevision,
  VoiceModelDescriptor,
  VoiceProviderDescriptor,
  VoiceRightsRecord
};

export type CastingCorrectedValue = CastingCorrectionRequestValue;
export type VoiceCatalogResponse = CastingCatalogPageResponse;

export type CastingRunsResponse = CastingRunPageResponse;
export type { CastingRunResponse };
export type ProductionRolesResponse = ProductionRolePageResponse;
export type { CreateCustomProductionRoleResponse };
export type CastingCandidatesResponse = CastingCandidatePageResponse;
export type CastingConflictsResponse = CastingConflictPageResponse;
export type CastAssignmentsResponse = CastAssignmentPageResponse;
export type CastingCorrectionsResponse = CastingCorrectionPageResponse;
export type CastingReviewsResponse = CastingGateReviewListResponse;
export type {
  AppendCastingCorrectionResponse,
  CreateCastingRunResponse,
  DecideCastingReviewResponse
};

export interface CastingProjectInput {
  readonly projectId: string;
}

export type CastingPageInput = CastingProjectInput & CursorPageRequest;

export interface CastingRunInput extends CastingProjectInput {
  readonly runId: string;
}

export interface CastingProfileExpectation {
  /** Renderer-held profile identity used only to validate returned persisted
   * evidence. The API client does not forward this field. */
  readonly expectedCastingProfileFingerprint: string;
}

export type CastingRunEvidenceInput =
  CastingRunInput & CastingEvidenceRequest & CastingProfileExpectation;

export type ListVoiceCatalogInput =
  CastingProjectInput & CastingCatalogPageRequest;

export type CreateCastingRunInput =
  CastingProjectInput & CreateCastingRunRequest;

export type CreateCustomProductionRoleInput =
  CastingRunInput & CreateCustomProductionRoleRequest;

export type ListCastingRunsInput = CastingPageInput;

export type ListProductionRolesInput =
  CastingRunInput & ProductionRolePageRequest & CastingProfileExpectation;

export type ListCastingCandidatesInput =
  CastingRunInput &
    CastingCandidatePageRequest & {
      readonly roleId: string;
    } & CastingProfileExpectation;

export type ListCastingConflictsInput =
  CastingRunInput & CastingConflictPageRequest & CastingProfileExpectation;

export type ListCastAssignmentsInput =
  CastingRunInput & CastAssignmentPageRequest & CastingProfileExpectation;

export type ListCastingCorrectionsInput =
  CastingRunInput & CastingCorrectionPageRequest & CastingProfileExpectation;

export type AppendCastingCorrectionInput =
  CastingRunInput &
    AppendCastingCorrectionRequest & {
      readonly voiceProfileId: string | null;
      readonly correctedValue: CastingCorrectedValue | null;
      readonly supersedesCorrectionId: string | null;
    };

export type ListCastingReviewsInput =
  CastingRunInput & CastingReviewListRequest & CastingProfileExpectation;

export type DecideCastingReviewInput =
  CastingRunInput &
    DecideCastingReviewRequest & {
      readonly gateId: CastingGateId;
      readonly warningAcknowledgementIds: readonly string[];
      readonly supersedesDecisionId: string | null;
    };
