/** Renderer-safe Phase 3B routing types. All private paths and bearer material
 * remain in Electron main and the authenticated local service. */
import {
  AUDITION_GATE_IDS,
  MODEL_PACKAGE_ACTIONS,
  PRONUNCIATION_SCOPES,
  SPEECH_AUDITION_CONTRACT_VERSION,
  SPEECH_AUDITION_LIMITS,
  type AuditionAudioDescriptor,
  type AuditionClipPageResponse,
  type AuditionGateId,
  type AuditionReviewDecisionPageResponse,
  type AuditionSessionPageResponse,
  type AuditionWorkspaceResponse,
  type CreateAuditionScriptRequest,
  type CreateAuditionScriptResponse,
  type CreateAuditionSessionRequest,
  type CreateAuditionSessionResponse,
  type CreatePronunciationEntryRequest,
  type CreatePronunciationEntryResponse,
  type ClearAuditionCacheRequest,
  type ClearAuditionCacheResponse,
  type DecideAuditionReviewRequest,
  type DecideAuditionReviewResponse,
  type DecidePronunciationEntryRequest,
  type DecidePronunciationEntryResponse,
  type GenerateAuditionRequest,
  type GenerateAuditionResponse,
  type ModelPackageActionRequest,
  type ModelPackageActionResponse,
  type ModelPackagePageResponse,
  type PreviewNormalizationRequest,
  type PreviewNormalizationResponse,
  type PronunciationEntryPageResponse,
  type SpeechAuditionPageRequest
} from "@cinematic-story-studio/contracts";

export {
  AUDITION_GATE_IDS,
  MODEL_PACKAGE_ACTIONS,
  PRONUNCIATION_SCOPES,
  SPEECH_AUDITION_CONTRACT_VERSION,
  SPEECH_AUDITION_LIMITS
};

export interface AuditionProjectInput {
  readonly projectId: string;
}

export type GetAuditionWorkspaceInput = AuditionProjectInput & {
  readonly roleCursor?: string;
  readonly roleLimit?: number;
};

export type ListModelPackagesInput =
  AuditionProjectInput & SpeechAuditionPageRequest;

export type ListPronunciationEntriesInput =
  AuditionProjectInput &
    SpeechAuditionPageRequest & {
      readonly expectedDictionaryRevision: number;
      readonly expectedDictionaryFingerprint: string;
    };

export type AppendPronunciationEntryInput =
  AuditionProjectInput & CreatePronunciationEntryRequest;

export type DecidePronunciationEntryInput =
  AuditionProjectInput &
    DecidePronunciationEntryRequest & {
      readonly entryId: string;
    };

export type ClearAuditionCacheInput =
  AuditionProjectInput & ClearAuditionCacheRequest;

export type ListAuditionSessionsInput =
  AuditionProjectInput &
    SpeechAuditionPageRequest & {
      readonly roleId?: string;
    };

export type CreateAuditionSessionInput =
  AuditionProjectInput & CreateAuditionSessionRequest;

export type CreateAuditionScriptInput =
  AuditionProjectInput & CreateAuditionScriptRequest;

export type PreviewAuditionNormalizationInput =
  AuditionProjectInput & PreviewNormalizationRequest;

export type GenerateAuditionInput =
  AuditionProjectInput & GenerateAuditionRequest;

export type ListAuditionClipsInput =
  AuditionProjectInput &
    SpeechAuditionPageRequest & {
      readonly auditionSessionId?: string;
      readonly roleId?: string;
    };

export type ListAuditionReviewDecisionsInput =
  AuditionProjectInput &
    SpeechAuditionPageRequest & {
      readonly gateId: AuditionGateId;
      readonly roleId: string | null;
    };

export type PerformModelPackageActionInput =
  AuditionProjectInput & ModelPackageActionRequest;

/** Renderer-safe request to choose and upload a local model archive. The
 * absolute selected path remains exclusively in Electron main. */
export interface SelectLocalModelPackageInput extends AuditionProjectInput {
  readonly modelPackageId: string;
  readonly expectedManifestFingerprint: string;
  readonly expectedInstallationRevision: number | null;
  readonly operation: "install" | "repair";
  readonly acknowledgeRestrictedLocalUse: true;
  readonly reason: string;
  readonly idempotencyKey: string;
}

export type DecideAuditionReviewInput =
  AuditionProjectInput &
    DecideAuditionReviewRequest & {
      readonly gateId: AuditionGateId;
      readonly roleId: string | null;
      readonly reviewId: string;
    };

export type LoadAuditionAudioInput = AuditionAudioDescriptor;

/** Structured-clone-safe bounded binary payload. `bytes` is never JSON or
 * base64. The renderer may create a short-lived Blob URL and must revoke it. */
export interface AuditionAudioPayload {
  readonly projectId: string;
  readonly auditionClipId: string;
  readonly auditionSessionId: string;
  readonly audioArtifactId: string;
  readonly mediaType: "audio/wav";
  readonly byteSize: number;
  readonly sha256: string;
  readonly bytes: ArrayBuffer;
}

export type {
  AuditionClipPageResponse as AuditionClipsResponse,
  AuditionReviewDecisionPageResponse as AuditionReviewDecisionsResponse,
  AuditionSessionPageResponse as AuditionSessionsResponse,
  AuditionWorkspaceResponse,
  CreateAuditionScriptResponse,
  CreateAuditionSessionResponse,
  CreatePronunciationEntryResponse,
  ClearAuditionCacheResponse,
  DecideAuditionReviewResponse,
  DecidePronunciationEntryResponse,
  GenerateAuditionResponse,
  ModelPackageActionResponse,
  ModelPackagePageResponse as ModelPackagesResponse,
  PreviewNormalizationResponse,
  PronunciationEntryPageResponse as PronunciationEntriesResponse
};
