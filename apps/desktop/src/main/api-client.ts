import { createHash, randomBytes, randomUUID } from "node:crypto";
import { constants, type Stats } from "node:fs";
import { lstat, open } from "node:fs/promises";
import http, { type IncomingMessage } from "node:http";
import path from "node:path";
import { Transform } from "node:stream";

import type {
  ApiErrorResponse,
  CorrectDialogueSpeakerResponse,
  CreateProjectResponse,
  DecideImportReviewResponse,
  DeclaredImportFormat,
  FfmpegCapabilityResponse,
  ImportReviewResponse,
  ImportStoryResponse,
  Job,
  JobEventsResponse,
  JobResponse,
  ProjectDetail,
  ProjectPageResponse,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";

import type {
  AnalysisCorrectionsResponse,
  AnalysisEntityPageResponse,
  AnalysisReviewsResponse,
  AnalysisRunResponse,
  AnalysisRunsResponse,
  AppendAnalysisCorrectionInput,
  AppendAnalysisCorrectionResponse,
  CreateAnalysisRunInput,
  CreateAnalysisRunResponse,
  DecideAnalysisReviewInput,
  DecideAnalysisReviewResponse,
  ListAnalysisCorrectionsInput,
  ListAnalysisEntitiesInput,
  ListAnalysisReviewsInput,
  ListAnalysisRunsInput
} from "../shared/analysis-api.js";
import type {
  AppendCastingCorrectionInput,
  AppendCastingCorrectionResponse,
  CastAssignmentsResponse,
  CastingCandidatesResponse,
  CastingConflictsResponse,
  CastingCorrectionsResponse,
  CastingRunInput,
  CastingRunResponse,
  CastingRunsResponse,
  CastingReviewsResponse,
  CreateCustomProductionRoleInput,
  CreateCustomProductionRoleResponse,
  CreateCastingRunInput,
  CreateCastingRunResponse,
  DecideCastingReviewInput,
  DecideCastingReviewResponse,
  ListCastAssignmentsInput,
  ListCastingCandidatesInput,
  ListCastingConflictsInput,
  ListCastingCorrectionsInput,
  ListCastingReviewsInput,
  ListCastingRunsInput,
  ListProductionRolesInput,
  ListVoiceCatalogInput,
  ProductionRolesResponse,
  VoiceCatalogResponse
} from "../shared/casting-api.js";
import type {
  AppendPronunciationEntryInput,
  AuditionAudioPayload,
  AuditionClipsResponse,
  AuditionReviewDecisionsResponse,
  AuditionSessionsResponse,
  AuditionWorkspaceResponse,
  ClearAuditionCacheInput,
  ClearAuditionCacheResponse,
  CreateAuditionScriptInput,
  CreateAuditionScriptResponse,
  CreateAuditionSessionInput,
  CreateAuditionSessionResponse,
  CreatePronunciationEntryResponse,
  DecideAuditionReviewInput,
  DecideAuditionReviewResponse,
  DecidePronunciationEntryInput,
  DecidePronunciationEntryResponse,
  GenerateAuditionInput,
  GenerateAuditionResponse,
  GetAuditionWorkspaceInput,
  ListAuditionClipsInput,
  ListAuditionReviewDecisionsInput,
  ListAuditionSessionsInput,
  ListModelPackagesInput,
  ListPronunciationEntriesInput,
  LoadAuditionAudioInput,
  ModelPackageActionResponse,
  ModelPackagesResponse,
  PerformModelPackageActionInput,
  SelectLocalModelPackageInput,
  PreviewAuditionNormalizationInput,
  PreviewNormalizationResponse,
  PronunciationEntriesResponse
} from "../shared/audition-api.js";
import type {
  AnalysisRunInput,
  CorrectSpeakerInput,
  CreateJobInput,
  CreateProjectInput,
  DecideImportReviewInput,
  ImportReviewIdInput
} from "../shared/desktop-api.js";
import {
  validateAnalysisCorrectionsResponse,
  validateAnalysisEntityPageResponse,
  validateAnalysisReviewsResponse,
  validateAnalysisRunResponse,
  validateAnalysisRunsResponse,
  validateAppendAnalysisCorrectionResponse,
  validateCreateAnalysisRunResponse,
  validateDecideAnalysisReviewResponse,
  validateProjectAnalysisProjection
} from "./analysis-validation.js";
import {
  validateAppendCastingCorrectionResponse,
  validateCastAssignmentsResponse,
  validateCastingCandidatesResponse,
  validateCastingConflictsResponse,
  validateCastingCorrectionsResponse,
  validateCastingRunResponse,
  validateCastingRunsResponse,
  validateCastingReviewsResponse,
  validateCreateCustomProductionRoleResponse,
  validateCreateCastingRunResponse,
  validateDecideCastingReviewResponse,
  validateProductionRolesResponse,
  validateVoiceCatalogResponse
} from "./casting-validation.js";
import {
  validateAuditionClipsResponse,
  validateAuditionReviewDecisionsResponse,
  validateAuditionSessionsResponse,
  validateAuditionWorkspaceResponse,
  validateClearAuditionCacheResponse,
  validateCreateAuditionScriptResponse,
  validateCreateAuditionSessionResponse,
  validateCreatePronunciationEntryResponse,
  validateDecideAuditionReviewResponse,
  validateDecidePronunciationEntryResponse,
  validateGenerateAuditionResponse,
  validateModelPackageActionResponse,
  validateModelPackagesResponse,
  validatePreviewNormalizationResponse,
  validatePronunciationEntriesResponse
} from "./audition-validation.js";
import { BackendUnavailableError, DesktopMainError } from "./errors.js";
import type { ServiceManager } from "./service-manager.js";
import {
  validateFfmpegCapabilityResponse,
  validateDecideImportReviewResponse,
  validateImportReviewResponse,
  validateImportStoryResponse,
  validateProjectDetail,
  validateProjectPageResponse,
  validateProviderHealthResponse,
  ValidationError
} from "./validation.js";

const JSON_RESPONSE_LIMIT_BYTES = 16 * 1024 * 1024;
export const IMPORT_LIMIT_BYTES = 8 * 1024 * 1024;
// The detail projection can contain the manuscript, disjoint beat text, and
// dialogue text. The 24x envelope covers worst-case JSON escaping of those
// three projections plus bounded entity metadata; other routes stay at 16 MiB.
export const PROJECT_RESPONSE_LIMIT_BYTES = IMPORT_LIMIT_BYTES * 24;
const JSON_REQUEST_LIMIT_BYTES = 64 * 1024;
const MAX_API_ROUTE_LENGTH = 2_048;
const REQUEST_TIMEOUT_MS = 12_000;
const IMPORT_TIMEOUT_MS = 45_000;
const AUDITION_AUDIO_LIMIT_BYTES = 24 * 1024 * 1024;
export const MODEL_PACKAGE_ARCHIVE_LIMIT_BYTES = 90 * 1024 * 1024;
const MODEL_PACKAGE_UPLOAD_TIMEOUT_MS = 120_000;

interface ImportFileSnapshot {
  readonly sha256: string;
  readonly byteLength: number;
}

interface MultipartImportResult {
  readonly response: unknown;
  readonly snapshot: ImportFileSnapshot;
}

export interface JobResponseExpectation {
  readonly jobId?: string;
  readonly projectId?: string;
  readonly type?: Job["type"];
  readonly inputRevision?: number;
  readonly inputFingerprint?: string;
}

export class BackendApiClient {
  readonly #service: ServiceManager;

  constructor(service: ServiceManager) {
    this.#service = service;
  }

  async listProjects(): Promise<ProjectPageResponse> {
    return validateProjectPageResponse(
      await this.#jsonRequest("GET", "/api/v1/projects")
    );
  }

  async createProject(
    input: CreateProjectInput
  ): Promise<CreateProjectResponse> {
    const response = await this.#jsonRequest(
      "POST",
      "/api/v1/projects",
      { name: input.name },
      input.idempotencyKey
    );
    validateCreateProjectResponse(response);
    return response as CreateProjectResponse;
  }

  async openProject(projectId: string): Promise<ProjectDetail> {
    const detail = validateProjectDetail(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(projectId)}`,
        undefined,
        undefined,
        PROJECT_RESPONSE_LIMIT_BYTES
      ),
      projectId
    );
    validateProjectAnalysisProjection(detail, projectId);
    return detail;
  }

  async importSelectedFile(
    projectId: string,
    selectedPath: string,
    declaredFormat: DeclaredImportFormat
  ): Promise<ImportStoryResponse> {
    const upload = await this.#multipartImport(
      `/api/v1/projects/${encodeURIComponent(projectId)}/imports`,
      selectedPath,
      declaredFormat
    );
    return validateImportStoryResponse(upload.response, {
      projectId,
      declaredFormat,
      sourceSha256: upload.snapshot.sha256,
      sourceByteCount: upload.snapshot.byteLength
    });
  }

  async getImportReview(
    input: ImportReviewIdInput
  ): Promise<ImportReviewResponse> {
    return validateImportReviewResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/imports/${encodeURIComponent(input.reviewId)}/review`
      ),
      input
    );
  }

  async decideImportReview(
    input: DecideImportReviewInput
  ): Promise<DecideImportReviewResponse> {
    return validateDecideImportReviewResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/imports/${encodeURIComponent(input.reviewId)}/review/decision`,
        {
          reviewId: input.reviewId,
          decision: input.decision,
          ...(input.rationale === undefined
            ? {}
            : { rationale: input.rationale }),
          expectedRevision: input.expectedRevision,
          evidenceFingerprint: input.evidenceFingerprint,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async correctSpeaker(
    input: CorrectSpeakerInput
  ): Promise<CorrectDialogueSpeakerResponse> {
    return validateCorrectionResponse(
      await this.#jsonRequest(
        "PUT",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/dialogue-lines/${encodeURIComponent(input.lineId)}/speaker`,
        {
          characterId: input.characterId,
          reason: input.reason,
          expectedRevision: input.expectedRevision
        },
        randomUUID()
      ),
      input
    );
  }

  async createAnalysisRun(
    input: CreateAnalysisRunInput
  ): Promise<CreateAnalysisRunResponse> {
    return validateCreateAnalysisRunResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/analysis-runs`,
        {
          expectedExtractionId: input.expectedExtractionId,
          expectedExtractionRevision: input.expectedExtractionRevision,
          expectedReviewId: input.expectedReviewId,
          expectedReviewRevision: input.expectedReviewRevision,
          expectedEvidenceFingerprint: input.expectedEvidenceFingerprint,
          expectedProfileFingerprint: input.expectedProfileFingerprint,
          profile: input.profile,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async listAnalysisRuns(
    input: ListAnalysisRunsInput
  ): Promise<AnalysisRunsResponse> {
    const query = cursorPageQuery(input);
    return validateAnalysisRunsResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/analysis-runs?${query.toString()}`
      ),
      input
    );
  }

  async getAnalysisRun(
    input: AnalysisRunInput
  ): Promise<AnalysisRunResponse> {
    return validateAnalysisRunResponse(
      await this.#jsonRequest(
        "GET",
        analysisRunRoute(input.projectId, input.runId)
      ),
      input
    );
  }

  async listAnalysisEntities(
    input: ListAnalysisEntitiesInput
  ): Promise<AnalysisEntityPageResponse> {
    const query = new URLSearchParams();
    if (input.cursor !== undefined) {
      query.set("cursor", input.cursor);
    }
    query.set("limit", String(input.limit ?? 50));
    if (input.confidenceMax !== undefined) {
      query.set("confidenceMax", String(input.confidenceMax));
    }
    if (input.requiresReview !== undefined) {
      query.set("requiresReview", String(input.requiresReview));
    }
    if (input.speakerState !== undefined) {
      query.set("speakerState", input.speakerState);
    }
    return validateAnalysisEntityPageResponse(
      await this.#jsonRequest(
        "GET",
        `${analysisRunRoute(
          input.projectId,
          input.runId
        )}/entities/${encodeURIComponent(input.collection)}?${query.toString()}`
      ),
      input
    );
  }

  async listAnalysisCorrections(
    input: ListAnalysisCorrectionsInput
  ): Promise<AnalysisCorrectionsResponse> {
    const query = cursorPageQuery(input);
    return validateAnalysisCorrectionsResponse(
      await this.#jsonRequest(
        "GET",
        `${analysisRunRoute(
          input.projectId,
          input.runId
        )}/corrections?${query.toString()}`
      ),
      input
    );
  }

  async appendAnalysisCorrection(
    input: AppendAnalysisCorrectionInput
  ): Promise<AppendAnalysisCorrectionResponse> {
    return validateAppendAnalysisCorrectionResponse(
      await this.#jsonRequest(
        "POST",
        `${analysisRunRoute(input.projectId, input.runId)}/corrections`,
        {
          category: input.category,
          targetCollection: input.targetCollection,
          targetEntityId: input.targetEntityId,
          expectedTargetRevision: input.expectedTargetRevision,
          expectedRunFingerprint: input.expectedRunFingerprint,
          previousValueFingerprint: input.previousValueFingerprint,
          patch: input.patch,
          reason: input.reason,
          ...(input.supersedesCorrectionId === undefined
            ? {}
            : { supersedesCorrectionId: input.supersedesCorrectionId }),
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async listAnalysisReviews(
    input: ListAnalysisReviewsInput
  ): Promise<AnalysisReviewsResponse> {
    return validateAnalysisReviewsResponse(
      await this.#jsonRequest(
        "GET",
        `${analysisRunRoute(input.projectId, input.runId)}/reviews`
      ),
      input
    );
  }

  async decideAnalysisReview(
    input: DecideAnalysisReviewInput
  ): Promise<DecideAnalysisReviewResponse> {
    return validateDecideAnalysisReviewResponse(
      await this.#jsonRequest(
        "POST",
        `${analysisRunRoute(
          input.projectId,
          input.runId
        )}/reviews/${encodeURIComponent(input.gateId)}/decisions`,
        {
          decision: input.decision,
          ...(input.rationale === undefined
            ? {}
            : { rationale: input.rationale }),
          expectedRevision: input.expectedRevision,
          expectedArtifactFingerprint: input.expectedArtifactFingerprint,
          expectedEvidenceFingerprint: input.expectedEvidenceFingerprint,
          acknowledgedWarningIds: input.acknowledgedWarningIds,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async getVoiceCatalog(
    input: ListVoiceCatalogInput
  ): Promise<VoiceCatalogResponse> {
    const query = castingCatalogPageQuery(input);
    return validateVoiceCatalogResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/casting/catalog?${query.toString()}`
      ),
      input
    );
  }

  async createCastingRun(
    input: CreateCastingRunInput
  ): Promise<CreateCastingRunResponse> {
    return validateCreateCastingRunResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/casting-runs`,
        {
          expectedAnalysisRunId: input.expectedAnalysisRunId,
          expectedSnapshotId: input.expectedSnapshotId,
          expectedSnapshotRevision: input.expectedSnapshotRevision,
          expectedSnapshotFingerprint: input.expectedSnapshotFingerprint,
          expectedCorrectionSetFingerprint:
            input.expectedCorrectionSetFingerprint,
          expectedImportReviewDecisionId:
            input.expectedImportReviewDecisionId,
          expectedAnalysisGateDecisionIds:
            input.expectedAnalysisGateDecisionIds,
          expectedCatalogRevisionId: input.expectedCatalogRevisionId,
          expectedCatalogFingerprint: input.expectedCatalogFingerprint,
          expectedCastingProfileFingerprint:
            input.expectedCastingProfileFingerprint,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async listCastingRuns(
    input: ListCastingRunsInput
  ): Promise<CastingRunsResponse> {
    const query = cursorPageQuery(input);
    return validateCastingRunsResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/casting-runs?${query.toString()}`
      ),
      input
    );
  }

  async getCastingRun(
    input: CastingRunInput
  ): Promise<CastingRunResponse> {
    return validateCastingRunResponse(
      await this.#jsonRequest(
        "GET",
        castingRunRoute(input.projectId, input.runId)
      ),
      input
    );
  }

  async listProductionRoles(
    input: ListProductionRolesInput
  ): Promise<ProductionRolesResponse> {
    const query = castingEvidencePageQuery(input);
    return validateProductionRolesResponse(
      await this.#jsonRequest(
        "GET",
        `${castingRunRoute(input.projectId, input.runId)}/roles?${query.toString()}`
      ),
      input
    );
  }

  async createCustomProductionRole(
    input: CreateCustomProductionRoleInput
  ): Promise<CreateCustomProductionRoleResponse> {
    return validateCreateCustomProductionRoleResponse(
      await this.#jsonRequest(
        "POST",
        `${castingRunRoute(input.projectId, input.runId)}/roles`,
        {
          definitionId: input.definitionId,
          label: input.label,
          performanceRequirements: input.performanceRequirements,
          reason: input.reason,
          expectedRunFingerprint: input.expectedRunFingerprint,
          expectedCatalogRevisionId: input.expectedCatalogRevisionId,
          expectedCatalogFingerprint: input.expectedCatalogFingerprint,
          expectedSnapshotId: input.expectedSnapshotId,
          expectedSnapshotRevision: input.expectedSnapshotRevision,
          expectedSnapshotFingerprint: input.expectedSnapshotFingerprint,
          expectedCorrectionSetFingerprint:
            input.expectedCorrectionSetFingerprint,
          expectedCastingProfileFingerprint:
            input.expectedCastingProfileFingerprint,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async listCastingCandidates(
    input: ListCastingCandidatesInput
  ): Promise<CastingCandidatesResponse> {
    const query = castingEvidencePageQuery(input);
    query.set("expectedRoleRevision", String(input.expectedRoleRevision));
    return validateCastingCandidatesResponse(
      await this.#jsonRequest(
        "GET",
        `${castingRunRoute(
          input.projectId,
          input.runId
        )}/roles/${encodeURIComponent(
          input.roleId
        )}/candidates?${query.toString()}`
      ),
      input
    );
  }

  async listCastingConflicts(
    input: ListCastingConflictsInput
  ): Promise<CastingConflictsResponse> {
    const query = castingEvidencePageQuery(input);
    return validateCastingConflictsResponse(
      await this.#jsonRequest(
        "GET",
        `${castingRunRoute(
          input.projectId,
          input.runId
        )}/conflicts?${query.toString()}`
      ),
      input
    );
  }

  async listCastAssignments(
    input: ListCastAssignmentsInput
  ): Promise<CastAssignmentsResponse> {
    const query = castingEvidencePageQuery(input);
    return validateCastAssignmentsResponse(
      await this.#jsonRequest(
        "GET",
        `${castingRunRoute(
          input.projectId,
          input.runId
        )}/assignments?${query.toString()}`
      ),
      input
    );
  }

  async listCastingCorrections(
    input: ListCastingCorrectionsInput
  ): Promise<CastingCorrectionsResponse> {
    const query = castingEvidencePageQuery(input);
    return validateCastingCorrectionsResponse(
      await this.#jsonRequest(
        "GET",
        `${castingRunRoute(
          input.projectId,
          input.runId
        )}/corrections?${query.toString()}`
      ),
      input
    );
  }

  async appendCastingCorrection(
    input: AppendCastingCorrectionInput
  ): Promise<AppendCastingCorrectionResponse> {
    return validateAppendCastingCorrectionResponse(
      await this.#jsonRequest(
        "POST",
        `${castingRunRoute(input.projectId, input.runId)}/corrections`,
        {
          operation: input.operation,
          targetRoleId: input.targetRoleId,
          expectedRoleRevision: input.expectedRoleRevision,
          expectedRunFingerprint: input.expectedRunFingerprint,
          expectedCatalogFingerprint: input.expectedCatalogFingerprint,
          expectedSnapshotFingerprint: input.expectedSnapshotFingerprint,
          expectedCorrectionSetFingerprint:
            input.expectedCorrectionSetFingerprint,
          previousEffectiveFingerprint: input.previousEffectiveFingerprint,
          voiceProfileId: input.voiceProfileId,
          correctedValue: input.correctedValue,
          reason: input.reason,
          supersedesCorrectionId: input.supersedesCorrectionId,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async listCastingReviews(
    input: ListCastingReviewsInput
  ): Promise<CastingReviewsResponse> {
    const query = castingEvidenceQuery(input);
    query.set(
      "expectedApprovedCastSnapshotId",
      input.expectedApprovedCastSnapshotId
    );
    query.set(
      "expectedApprovedCastSnapshotRevision",
      String(input.expectedApprovedCastSnapshotRevision)
    );
    return validateCastingReviewsResponse(
      await this.#jsonRequest(
        "GET",
        `${castingRunRoute(
          input.projectId,
          input.runId
        )}/reviews?${query.toString()}`
      ),
      input
    );
  }

  async decideCastingReview(
    input: DecideCastingReviewInput
  ): Promise<DecideCastingReviewResponse> {
    return validateDecideCastingReviewResponse(
      await this.#jsonRequest(
        "POST",
        `${castingRunRoute(
          input.projectId,
          input.runId
        )}/reviews/${encodeURIComponent(input.gateId)}/decisions`,
        {
          decision: input.decision,
          expectedRevision: input.expectedRevision,
          expectedEvidenceFingerprint: input.expectedEvidenceFingerprint,
          expectedRunFingerprint: input.expectedRunFingerprint,
          expectedApprovedCastSnapshotId:
            input.expectedApprovedCastSnapshotId,
          expectedApprovedCastSnapshotRevision:
            input.expectedApprovedCastSnapshotRevision,
          warningAcknowledgementIds: input.warningAcknowledgementIds,
          rationale: input.rationale,
          supersedesDecisionId: input.supersedesDecisionId,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async getAuditionWorkspace(
    input: GetAuditionWorkspaceInput
  ): Promise<AuditionWorkspaceResponse> {
    const query = new URLSearchParams();
    if (input.roleCursor !== undefined) {
      query.set("roleCursor", input.roleCursor);
    }
    if (input.roleLimit !== undefined) {
      query.set("roleLimit", String(input.roleLimit));
    }
    const queryString = query.toString();
    return validateAuditionWorkspaceResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(input.projectId)}/auditions/workspace${
          queryString.length === 0 ? "" : `?${queryString}`
        }`
      ),
      input
    );
  }

  async listModelPackages(
    input: ListModelPackagesInput
  ): Promise<ModelPackagesResponse> {
    return validateModelPackagesResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/speech/model-packages?${cursorPageQuery(input).toString()}`
      ),
      input
    );
  }

  async performModelPackageAction(
    input: PerformModelPackageActionInput
  ): Promise<ModelPackageActionResponse> {
    return validateModelPackageActionResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/speech/model-packages/${encodeURIComponent(
          input.modelPackageId
        )}/actions`,
        {
          modelPackageId: input.modelPackageId,
          expectedManifestFingerprint: input.expectedManifestFingerprint,
          expectedInstallationRevision: input.expectedInstallationRevision,
          action: input.action,
          reason: input.reason,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async applySelectedLocalModelPackage(
    input: SelectLocalModelPackageInput,
    selectedPath: string
  ): Promise<ModelPackageActionResponse> {
    const response = await this.#multipartLocalModelPackage(
      `/api/v1/projects/${encodeURIComponent(
        input.projectId
      )}/speech/model-packages/${encodeURIComponent(
        input.modelPackageId
      )}/${input.operation}`,
      selectedPath,
      input
    );
    return validateModelPackageActionResponse(response, input);
  }

  async listPronunciationEntries(
    input: ListPronunciationEntriesInput
  ): Promise<PronunciationEntriesResponse> {
    const query = cursorPageQuery(input);
    query.set(
      "expectedDictionaryRevision",
      String(input.expectedDictionaryRevision)
    );
    query.set(
      "expectedDictionaryFingerprint",
      input.expectedDictionaryFingerprint
    );
    return validatePronunciationEntriesResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/pronunciations/entries?${query.toString()}`
      ),
      input
    );
  }

  async appendPronunciationEntry(
    input: AppendPronunciationEntryInput
  ): Promise<CreatePronunciationEntryResponse> {
    return validateCreatePronunciationEntryResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/pronunciations/entries`,
        {
          expectedDictionaryRevision: input.expectedDictionaryRevision,
          expectedDictionaryFingerprint: input.expectedDictionaryFingerprint,
          writtenForm: input.writtenForm,
          language: input.language,
          locale: input.locale,
          scope: input.scope,
          scopeId: input.scopeId,
          representation: input.representation,
          pronunciation: input.pronunciation,
          ipa: input.ipa,
          providerId: input.providerId,
          providerCompiledValue: input.providerCompiledValue,
          caseSensitive: input.caseSensitive,
          matchRule: input.matchRule,
          priority: input.priority,
          reason: input.reason,
          supersedesEntryId: input.supersedesEntryId,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async decidePronunciationEntry(
    input: DecidePronunciationEntryInput
  ): Promise<DecidePronunciationEntryResponse> {
    return validateDecidePronunciationEntryResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/pronunciations/entries/${encodeURIComponent(
          input.entryId
        )}/decisions`,
        {
          expectedEntryRevision: input.expectedEntryRevision,
          expectedEntryFingerprint: input.expectedEntryFingerprint,
          expectedDictionaryRevision: input.expectedDictionaryRevision,
          expectedDictionaryFingerprint: input.expectedDictionaryFingerprint,
          decision: input.decision,
          rationale: input.rationale,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async clearAuditionCache(
    input: ClearAuditionCacheInput
  ): Promise<ClearAuditionCacheResponse> {
    return validateClearAuditionCacheResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/audition-cache/clear`,
        {
          expectedProjectRevision: input.expectedProjectRevision,
          reason: input.reason,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async listAuditionSessions(
    input: ListAuditionSessionsInput
  ): Promise<AuditionSessionsResponse> {
    const query = cursorPageQuery(input);
    if (input.roleId !== undefined) query.set("roleId", input.roleId);
    return validateAuditionSessionsResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/audition-sessions?${query.toString()}`
      ),
      input
    );
  }

  async createAuditionSession(
    input: CreateAuditionSessionInput
  ): Promise<CreateAuditionSessionResponse> {
    return validateCreateAuditionSessionResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/audition-sessions`,
        {
          roleId: input.roleId,
          evidence: input.evidence,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async createAuditionScript(
    input: CreateAuditionScriptInput
  ): Promise<CreateAuditionScriptResponse> {
    return validateCreateAuditionScriptResponse(
      await this.#jsonRequest(
        "POST",
        `${auditionSessionRoute(
          input.projectId,
          input.auditionSessionId
        )}/scripts`,
        {
          auditionSessionId: input.auditionSessionId,
          expectedSessionRevision: input.expectedSessionRevision,
          kind: input.kind,
          text: input.text,
          sourceDocumentId: input.sourceDocumentId,
          sourceRevision: input.sourceRevision,
          sourceSpan: input.sourceSpan,
          sourceTextSha256: input.sourceTextSha256,
          acceptedOptionalNormalizationIds:
            input.acceptedOptionalNormalizationIds,
          customPronunciationScopeIds: input.customPronunciationScopeIds,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async previewAuditionNormalization(
    input: PreviewAuditionNormalizationInput
  ): Promise<PreviewNormalizationResponse> {
    return validatePreviewNormalizationResponse(
      await this.#jsonRequest(
        "POST",
        `${auditionSessionRoute(
          input.projectId,
          input.auditionSessionId
        )}/normalization-preview`,
        {
          auditionSessionId: input.auditionSessionId,
          expectedSessionRevision: input.expectedSessionRevision,
          text: input.text,
          sourceTextSha256: input.sourceTextSha256,
          acceptedOptionalNormalizationIds:
            input.acceptedOptionalNormalizationIds,
          customPronunciationScopeIds: input.customPronunciationScopeIds
        }
      ),
      input
    );
  }

  async generateAudition(
    input: GenerateAuditionInput
  ): Promise<GenerateAuditionResponse> {
    return validateGenerateAuditionResponse(
      await this.#jsonRequest(
        "POST",
        `${auditionSessionRoute(
          input.projectId,
          input.preview.auditionSessionId
        )}/generate`,
        { preview: input.preview },
        input.preview.idempotencyKey
      ),
      input
    );
  }

  async listAuditionClips(
    input: ListAuditionClipsInput
  ): Promise<AuditionClipsResponse> {
    const query = cursorPageQuery(input);
    if (input.auditionSessionId !== undefined) {
      query.set("auditionSessionId", input.auditionSessionId);
    }
    if (input.roleId !== undefined) query.set("roleId", input.roleId);
    return validateAuditionClipsResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/audition-clips?${query.toString()}`
      ),
      input
    );
  }

  async loadAuditionAudio(
    input: LoadAuditionAudioInput
  ): Promise<AuditionAudioPayload> {
    const query = new URLSearchParams({
      auditionSessionId: input.auditionSessionId,
      audioArtifactId: input.audioArtifactId,
      expectedClipRevision: String(input.expectedClipRevision),
      expectedClipFingerprint: input.expectedClipFingerprint,
      expectedArtifactSha256: input.expectedArtifactSha256,
      byteSize: String(input.byteSize)
    });
    const bytes = await this.#binaryRequest(
      `/api/v1/projects/${encodeURIComponent(
        input.projectId
      )}/audition-clips/${encodeURIComponent(
        input.auditionClipId
      )}/audio?${query.toString()}`,
      input
    );
    return {
      projectId: input.projectId,
      auditionClipId: input.auditionClipId,
      auditionSessionId: input.auditionSessionId,
      audioArtifactId: input.audioArtifactId,
      mediaType: "audio/wav",
      byteSize: bytes.byteLength,
      sha256: input.expectedArtifactSha256,
      bytes: Uint8Array.from(bytes).buffer
    };
  }

  async listAuditionReviewDecisions(
    input: ListAuditionReviewDecisionsInput
  ): Promise<AuditionReviewDecisionsResponse> {
    const query = cursorPageQuery(input);
    query.set("gateId", input.gateId);
    if (input.roleId !== null) query.set("roleId", input.roleId);
    return validateAuditionReviewDecisionsResponse(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/audition-review-decisions?${query.toString()}`
      ),
      input
    );
  }

  async decideAuditionReview(
    input: DecideAuditionReviewInput
  ): Promise<DecideAuditionReviewResponse> {
    return validateDecideAuditionReviewResponse(
      await this.#jsonRequest(
        "POST",
        `/api/v1/projects/${encodeURIComponent(
          input.projectId
        )}/audition-reviews/${encodeURIComponent(
          input.gateId
        )}/${encodeURIComponent(input.reviewId)}/decisions`,
        {
          expectedReviewRevision: input.expectedReviewRevision,
          expectedEvidenceFingerprint: input.expectedEvidenceFingerprint,
          decision: input.decision,
          rationale: input.rationale,
          supersedesDecisionId: input.supersedesDecisionId,
          idempotencyKey: input.idempotencyKey
        },
        input.idempotencyKey
      ),
      input
    );
  }

  async createJob(input: CreateJobInput): Promise<JobResponse> {
    const response = await this.#jsonRequest(
      "POST",
      `/api/v1/projects/${encodeURIComponent(input.projectId)}/jobs`,
      {
        type: input.type,
        inputRevision: input.inputRevision,
        idempotencyKey: input.idempotencyKey
      },
      input.idempotencyKey
    );
    validateJobResponse(response, {
      projectId: input.projectId,
      type: input.type,
      inputRevision: input.inputRevision
    });
    return response as JobResponse;
  }

  async getJob(
    jobId: string,
    expected: JobResponseExpectation = {}
  ): Promise<JobResponse> {
    const response = await this.#jsonRequest(
      "GET",
      `/api/v1/jobs/${encodeURIComponent(jobId)}`
    );
    validateJobResponse(response, { ...expected, jobId });
    return response as JobResponse;
  }

  async getJobEvents(
    jobId: string,
    afterSequence?: number
  ): Promise<JobEventsResponse> {
    const suffix =
      afterSequence === undefined ? "" : `?afterSequence=${afterSequence}`;
    const response = await this.#jsonRequest(
      "GET",
      `/api/v1/jobs/${encodeURIComponent(jobId)}/events${suffix}`
    );
    validateJobEventsResponse(response, { jobId, afterSequence });
    return response as JobEventsResponse;
  }

  async cancelJob(
    jobId: string,
    expected: JobResponseExpectation = {}
  ): Promise<JobResponse> {
    return this.#jobAction(jobId, "cancel", expected);
  }

  async retryJob(
    jobId: string,
    expected: JobResponseExpectation = {}
  ): Promise<JobResponse> {
    return this.#jobAction(jobId, "retry", expected);
  }

  async resumeJob(
    jobId: string,
    expected: JobResponseExpectation = {}
  ): Promise<JobResponse> {
    return this.#jobAction(jobId, "resume", expected);
  }

  async providerHealth(): Promise<ProviderHealthResponse> {
    return validateProviderHealthResponse(
      await this.#jsonRequest("GET", "/api/v1/providers/health")
    );
  }

  async ffmpegCapability(): Promise<FfmpegCapabilityResponse> {
    return validateFfmpegCapabilityResponse(
      await this.#jsonRequest("GET", "/api/v1/capabilities/ffmpeg")
    );
  }

  async #jobAction(
    jobId: string,
    action: "cancel" | "retry" | "resume",
    expected: JobResponseExpectation
  ): Promise<JobResponse> {
    const response = await this.#jsonRequest(
      "POST",
      `/api/v1/jobs/${encodeURIComponent(jobId)}/${action}`,
      undefined,
      randomUUID()
    );
    validateJobResponse(response, { ...expected, jobId });
    return response as JobResponse;
  }

  async #jsonRequest(
    method: "GET" | "POST" | "PUT",
    route: string,
    body?: Readonly<Record<string, unknown>>,
    idempotencyKey?: string,
    responseLimitBytes = JSON_RESPONSE_LIMIT_BYTES
  ): Promise<unknown> {
    const credentials = this.#service.connection();
    ensureFixedApiRoute(route);
    const encodedBody = body === undefined ? undefined : JSON.stringify(body);
    if (
      encodedBody !== undefined &&
      Buffer.byteLength(encodedBody, "utf8") > JSON_REQUEST_LIMIT_BYTES
    ) {
      throw new ValidationError("The request payload exceeded its limit.");
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(
        `http://127.0.0.1:${credentials.port}${route}`,
        {
          method,
          headers: {
            Authorization: `Bearer ${credentials.token}`,
            Accept: "application/json",
            "Cache-Control": "no-store",
            "X-CSS-Contract-Version": "1.0.0",
            ...(encodedBody === undefined
              ? {}
              : { "Content-Type": "application/json" }),
            ...(idempotencyKey === undefined
              ? {}
              : { "Idempotency-Key": idempotencyKey })
          },
          body: encodedBody,
          cache: "no-store",
          signal: controller.signal
        }
      );
      return await parseFetchResponse(response, responseLimitBytes);
    } catch (error) {
      if (error instanceof DesktopMainError || error instanceof ValidationError) {
        throw error;
      }
      throw new BackendUnavailableError();
    } finally {
      clearTimeout(timeout);
    }
  }

  async #binaryRequest(
    route: string,
    expected: LoadAuditionAudioInput
  ): Promise<Buffer> {
    const credentials = this.#service.connection();
    ensureFixedApiRoute(route);
    if (
      expected.byteSize < 45 ||
      expected.byteSize > AUDITION_AUDIO_LIMIT_BYTES
    ) {
      throw new ValidationError("The audition audio size was invalid.");
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(
        `http://127.0.0.1:${credentials.port}${route}`,
        {
          method: "GET",
          headers: {
            Authorization: `Bearer ${credentials.token}`,
            Accept: "audio/wav",
            "Cache-Control": "no-store",
            "X-CSS-Contract-Version": "1.0.0"
          },
          cache: "no-store",
          signal: controller.signal
        }
      );
      if (!response.ok) {
        await parseFetchResponse(response);
        throw new DesktopMainError(
          "AUDITION_AUDIO_UNAVAILABLE",
          "The audition audio could not be loaded.",
          response.status >= 500
        );
      }
      const mediaType = response.headers
        .get("content-type")
        ?.split(";", 1)[0]
        ?.trim()
        .toLowerCase();
      if (
        mediaType !== "audio/wav" ||
        response.headers.get("cache-control")?.toLowerCase() !== "no-store"
      ) {
        throw new DesktopMainError(
          "AUDITION_AUDIO_RESPONSE_INVALID",
          "The local service returned invalid audition audio metadata.",
          false
        );
      }
      const bytes = await readFetchBodyLimited(
        response,
        AUDITION_AUDIO_LIMIT_BYTES
      );
      if (
        bytes.byteLength !== expected.byteSize ||
        createHash("sha256").update(bytes).digest("hex") !==
          expected.expectedArtifactSha256
      ) {
        throw new DesktopMainError(
          "AUDITION_AUDIO_INTEGRITY_FAILED",
          "The audition audio did not match its verified artifact.",
          false
        );
      }
      validateAuditionPcmWav(bytes);
      return bytes;
    } catch (error) {
      if (
        error instanceof DesktopMainError ||
        error instanceof ValidationError
      ) {
        throw error;
      }
      throw new BackendUnavailableError();
    } finally {
      clearTimeout(timeout);
    }
  }

  async #multipartImport(
    route: string,
    selectedPath: string,
    declaredFormat: DeclaredImportFormat
  ): Promise<MultipartImportResult> {
    const credentials = this.#service.connection();
    ensureFixedApiRoute(route);
    const initialMetadata = await lstat(selectedPath);
    if (
      !initialMetadata.isFile() ||
      initialMetadata.isSymbolicLink() ||
      initialMetadata.size <= 0 ||
      initialMetadata.size > IMPORT_LIMIT_BYTES
    ) {
      throw new DesktopMainError(
        initialMetadata.size > IMPORT_LIMIT_BYTES
          ? "IMPORT_TOO_LARGE"
          : "IMPORT_FILE_INVALID",
        initialMetadata.size > IMPORT_LIMIT_BYTES
          ? "The selected document exceeds the 8 MiB desktop import limit."
          : "The selected document is not a supported regular file.",
        false
      );
    }
    const fileHandle = await open(
      selectedPath,
      constants.O_RDONLY | constants.O_NOFOLLOW
    );
    try {
      const [openedMetadata, currentMetadata] = await Promise.all([
        fileHandle.stat(),
        lstat(selectedPath)
      ]);
      if (
        !openedMetadata.isFile() ||
        currentMetadata.isSymbolicLink() ||
        !sameFileIdentity(openedMetadata, currentMetadata) ||
        openedMetadata.size <= 0 ||
        openedMetadata.size > IMPORT_LIMIT_BYTES
      ) {
        throw new DesktopMainError(
          openedMetadata.size > IMPORT_LIMIT_BYTES
            ? "IMPORT_TOO_LARGE"
            : "IMPORT_FILE_CHANGED",
          openedMetadata.size > IMPORT_LIMIT_BYTES
            ? "The selected document exceeds the 8 MiB desktop import limit."
            : "The selected document changed before it could be imported.",
          false
        );
      }
      const safeName = sanitizeMultipartFilename(path.basename(selectedPath));
      const boundary = `css-${randomBytes(18).toString("hex")}`;
      const prefix = Buffer.from(
        `--${boundary}\r\n` +
          `Content-Disposition: form-data; name="declaredFormat"\r\n\r\n` +
          `${declaredFormat}\r\n` +
          `--${boundary}\r\n` +
          `Content-Disposition: form-data; name="file"; filename="${safeName}"\r\n` +
          `Content-Type: ${importMediaType(declaredFormat)}\r\n\r\n`,
        "utf8"
      );
      const suffix = Buffer.from(`\r\n--${boundary}--\r\n`, "utf8");
      const contentLength =
        prefix.byteLength + openedMetadata.size + suffix.byteLength;
      const source = fileHandle.createReadStream({
        autoClose: false,
        start: 0,
        end: openedMetadata.size - 1,
        highWaterMark: 64 * 1024
      });
      const digest = createHash("sha256");
      let observedByteLength = 0;
      let snapshot: ImportFileSnapshot | null = null;
      const hashingStream = new Transform({
        transform(chunk, _encoding, callback) {
          const bytes = Buffer.isBuffer(chunk)
            ? chunk
            : Buffer.from(chunk as Uint8Array);
          digest.update(bytes);
          observedByteLength += bytes.byteLength;
          callback(null, bytes);
        }
      });

      return await new Promise<MultipartImportResult>((resolve, reject) => {
        const request = http.request(
          {
            hostname: "127.0.0.1",
            port: credentials.port,
            method: "POST",
            path: route,
            headers: {
              Authorization: `Bearer ${credentials.token}`,
              Accept: "application/json",
              "Cache-Control": "no-store",
              "X-CSS-Contract-Version": "1.0.0",
              "Idempotency-Key": randomUUID(),
              "Content-Type": `multipart/form-data; boundary=${boundary}`,
              "Content-Length": contentLength
            },
            timeout: IMPORT_TIMEOUT_MS
          },
          (response) => {
            void parseNodeResponse(response).then((value) => {
              const verifiedSnapshot = snapshot;
              if (verifiedSnapshot === null) {
                reject(importFileChangedError());
                return;
              }
              resolve({ response: value, snapshot: verifiedSnapshot });
            }, reject);
          }
        );
        request.once("timeout", () => {
          request.destroy(
            new DesktopMainError(
              "IMPORT_TIMEOUT",
              "The selected document import timed out.",
              true
            )
          );
        });
        request.once("error", (error) => {
          source.destroy();
          hashingStream.destroy();
          reject(
            error instanceof DesktopMainError
              ? error
              : new BackendUnavailableError()
          );
        });
        request.write(prefix);
        source.once("error", () => {
          request.destroy(
            new DesktopMainError(
              "IMPORT_READ_FAILED",
              "The selected document could not be read.",
              false
            )
          );
        });
        hashingStream.once("error", () => {
          request.destroy(
            new DesktopMainError(
              "IMPORT_READ_FAILED",
              "The selected document could not be read.",
              false
            )
          );
        });
        hashingStream.once("end", () => {
          void (async () => {
            try {
              const [finalOpenedMetadata, finalPathMetadata] =
                await Promise.all([fileHandle.stat(), lstat(selectedPath)]);
              if (
                observedByteLength !== openedMetadata.size ||
                !finalOpenedMetadata.isFile() ||
                finalPathMetadata.isSymbolicLink() ||
                !sameFileIdentity(openedMetadata, finalOpenedMetadata) ||
                !sameFileIdentity(finalOpenedMetadata, finalPathMetadata)
              ) {
                throw importFileChangedError();
              }
              snapshot = {
                sha256: digest.digest("hex"),
                byteLength: observedByteLength
              };
              request.end(suffix);
            } catch {
              request.destroy(importFileChangedError());
            }
          })();
        });
        source.pipe(hashingStream).pipe(request, { end: false });
      });
    } finally {
      await fileHandle.close().catch(() => undefined);
    }
  }

  async #multipartLocalModelPackage(
    route: string,
    selectedPath: string,
    input: SelectLocalModelPackageInput
  ): Promise<unknown> {
    const credentials = this.#service.connection();
    ensureFixedApiRoute(route);
    const initialMetadata = await lstat(selectedPath);
    if (
      !initialMetadata.isFile() ||
      initialMetadata.isSymbolicLink() ||
      initialMetadata.size <= 0 ||
      initialMetadata.size > MODEL_PACKAGE_ARCHIVE_LIMIT_BYTES
    ) {
      throw new DesktopMainError(
        initialMetadata.size > MODEL_PACKAGE_ARCHIVE_LIMIT_BYTES
          ? "MODEL_PACKAGE_TOO_LARGE"
          : "MODEL_PACKAGE_FILE_INVALID",
        initialMetadata.size > MODEL_PACKAGE_ARCHIVE_LIMIT_BYTES
          ? "The selected model package exceeds the 90 MiB desktop upload limit."
          : "The selected model package is not a supported regular ZIP file.",
        false
      );
    }
    const fileHandle = await open(
      selectedPath,
      constants.O_RDONLY | constants.O_NOFOLLOW
    );
    try {
      const [openedMetadata, currentMetadata] = await Promise.all([
        fileHandle.stat(),
        lstat(selectedPath)
      ]);
      if (
        !openedMetadata.isFile() ||
        currentMetadata.isSymbolicLink() ||
        !sameFileIdentity(openedMetadata, currentMetadata) ||
        openedMetadata.size <= 0 ||
        openedMetadata.size > MODEL_PACKAGE_ARCHIVE_LIMIT_BYTES
      ) {
        throw openedMetadata.size > MODEL_PACKAGE_ARCHIVE_LIMIT_BYTES
          ? new DesktopMainError(
              "MODEL_PACKAGE_TOO_LARGE",
              "The selected model package exceeds the 90 MiB desktop upload limit.",
              false
            )
          : modelPackageFileChangedError();
      }
      const boundary = `css-model-${randomBytes(18).toString("hex")}`;
      const textFields: readonly (readonly [string, string])[] = [
        ["expectedManifestFingerprint", input.expectedManifestFingerprint],
        ...(input.expectedInstallationRevision === null
          ? []
          : [[
              "expectedInstallationRevision",
              String(input.expectedInstallationRevision)
            ] as const]),
        [
          "acknowledgeRestrictedLocalUse",
          String(input.acknowledgeRestrictedLocalUse)
        ],
        ["reason", input.reason],
        ["idempotencyKey", input.idempotencyKey]
      ];
      const textPrefix = textFields
        .map(
          ([name, value]) =>
            `--${boundary}\r\n` +
            `Content-Disposition: form-data; name="${name}"\r\n\r\n` +
            `${value}\r\n`
        )
        .join("");
      const safeName = sanitizeMultipartFilename(
        path.basename(selectedPath),
        "selected-model-package.zip"
      );
      const prefix = Buffer.from(
        textPrefix +
          `--${boundary}\r\n` +
          `Content-Disposition: form-data; name="file"; filename="${safeName}"\r\n` +
          "Content-Type: application/zip\r\n\r\n",
        "utf8"
      );
      const suffix = Buffer.from(`\r\n--${boundary}--\r\n`, "utf8");
      const contentLength =
        prefix.byteLength + openedMetadata.size + suffix.byteLength;
      const source = fileHandle.createReadStream({
        autoClose: false,
        start: 0,
        end: openedMetadata.size - 1,
        highWaterMark: 64 * 1024
      });
      let observedByteLength = 0;
      let fileSnapshotVerified = false;
      const countingStream = new Transform({
        transform(chunk, _encoding, callback) {
          const bytes = Buffer.isBuffer(chunk)
            ? chunk
            : Buffer.from(chunk as Uint8Array);
          observedByteLength += bytes.byteLength;
          callback(null, bytes);
        }
      });

      return await new Promise<unknown>((resolve, reject) => {
        const request = http.request(
          {
            hostname: "127.0.0.1",
            port: credentials.port,
            method: "POST",
            path: route,
            headers: {
              Authorization: `Bearer ${credentials.token}`,
              Accept: "application/json",
              "Cache-Control": "no-store",
              "X-CSS-Contract-Version": "1.0.0",
              "Idempotency-Key": input.idempotencyKey,
              "Content-Type": `multipart/form-data; boundary=${boundary}`,
              "Content-Length": contentLength
            },
            timeout: MODEL_PACKAGE_UPLOAD_TIMEOUT_MS
          },
          (response) => {
            void parseNodeResponse(response).then((value) => {
              if (!fileSnapshotVerified) {
                reject(modelPackageFileChangedError());
                return;
              }
              resolve(value);
            }, reject);
          }
        );
        request.once("timeout", () => {
          request.destroy(
            new DesktopMainError(
              "MODEL_PACKAGE_UPLOAD_TIMEOUT",
              "The local model package upload timed out.",
              true
            )
          );
        });
        request.once("error", (error) => {
          source.destroy();
          countingStream.destroy();
          reject(
            error instanceof DesktopMainError
              ? error
              : new BackendUnavailableError()
          );
        });
        request.write(prefix);
        source.once("error", () => {
          request.destroy(
            new DesktopMainError(
              "MODEL_PACKAGE_READ_FAILED",
              "The selected model package could not be read.",
              false
            )
          );
        });
        countingStream.once("error", () => {
          request.destroy(
            new DesktopMainError(
              "MODEL_PACKAGE_READ_FAILED",
              "The selected model package could not be read.",
              false
            )
          );
        });
        countingStream.once("end", () => {
          void (async () => {
            try {
              const [finalOpenedMetadata, finalPathMetadata] =
                await Promise.all([fileHandle.stat(), lstat(selectedPath)]);
              if (
                observedByteLength !== openedMetadata.size ||
                !finalOpenedMetadata.isFile() ||
                finalPathMetadata.isSymbolicLink() ||
                !sameFileIdentity(openedMetadata, finalOpenedMetadata) ||
                !sameFileIdentity(finalOpenedMetadata, finalPathMetadata)
              ) {
                throw modelPackageFileChangedError();
              }
              fileSnapshotVerified = true;
              request.end(suffix);
            } catch {
              request.destroy(modelPackageFileChangedError());
            }
          })();
        });
        source.pipe(countingStream).pipe(request, { end: false });
      });
    } finally {
      await fileHandle.close().catch(() => undefined);
    }
  }
}

function analysisRunRoute(projectId: string, runId: string): string {
  return `/api/v1/projects/${encodeURIComponent(
    projectId
  )}/analysis-runs/${encodeURIComponent(runId)}`;
}

function castingRunRoute(projectId: string, runId: string): string {
  return `/api/v1/projects/${encodeURIComponent(
    projectId
  )}/casting-runs/${encodeURIComponent(runId)}`;
}

function auditionSessionRoute(
  projectId: string,
  auditionSessionId: string
): string {
  return `/api/v1/projects/${encodeURIComponent(
    projectId
  )}/audition-sessions/${encodeURIComponent(auditionSessionId)}`;
}

function cursorPageQuery(input: {
  readonly cursor?: string;
  readonly limit?: number;
}): URLSearchParams {
  const query = new URLSearchParams();
  if (input.cursor !== undefined) {
    query.set("cursor", input.cursor);
  }
  query.set("limit", String(input.limit ?? 50));
  return query;
}

function castingCatalogPageQuery(input: {
  readonly cursor?: string;
  readonly limit?: number;
  readonly expectedCatalogRevisionId?: string;
  readonly expectedCatalogFingerprint?: string;
}): URLSearchParams {
  const query = cursorPageQuery(input);
  if (input.expectedCatalogRevisionId !== undefined) {
    query.set(
      "expectedCatalogRevisionId",
      input.expectedCatalogRevisionId
    );
  }
  if (input.expectedCatalogFingerprint !== undefined) {
    query.set(
      "expectedCatalogFingerprint",
      input.expectedCatalogFingerprint
    );
  }
  return query;
}

function castingEvidenceQuery(input: {
  readonly expectedRunFingerprint: string;
  readonly expectedCatalogRevisionId: string;
  readonly expectedCatalogFingerprint: string;
  readonly expectedSnapshotId: string;
  readonly expectedSnapshotRevision: number;
  readonly expectedSnapshotFingerprint: string;
}): URLSearchParams {
  return new URLSearchParams({
    expectedRunFingerprint: input.expectedRunFingerprint,
    expectedCatalogRevisionId: input.expectedCatalogRevisionId,
    expectedCatalogFingerprint: input.expectedCatalogFingerprint,
    expectedSnapshotId: input.expectedSnapshotId,
    expectedSnapshotRevision: String(input.expectedSnapshotRevision),
    expectedSnapshotFingerprint: input.expectedSnapshotFingerprint
  });
}

function castingEvidencePageQuery(input: {
  readonly cursor?: string;
  readonly limit?: number;
  readonly expectedRunFingerprint: string;
  readonly expectedCatalogRevisionId: string;
  readonly expectedCatalogFingerprint: string;
  readonly expectedSnapshotId: string;
  readonly expectedSnapshotRevision: number;
  readonly expectedSnapshotFingerprint: string;
}): URLSearchParams {
  const query = cursorPageQuery(input);
  for (const [key, value] of castingEvidenceQuery(input)) {
    query.set(key, value);
  }
  return query;
}

async function parseFetchResponse(
  response: Response,
  maximumBytes = JSON_RESPONSE_LIMIT_BYTES
): Promise<unknown> {
  const declaredLength = Number(response.headers.get("content-length") ?? 0);
  if (declaredLength > maximumBytes) {
    throw new DesktopMainError(
      "SERVICE_RESPONSE_TOO_LARGE",
      "The local service response exceeded its limit.",
      false
    );
  }
  const bytes = await readFetchBodyLimited(
    response,
    maximumBytes
  );
  return parseResponseBytes(response.status, bytes, maximumBytes);
}

async function parseNodeResponse(
  response: IncomingMessage,
  maximumBytes = JSON_RESPONSE_LIMIT_BYTES
): Promise<unknown> {
  const declaredLength = Number(response.headers["content-length"] ?? 0);
  if (declaredLength > maximumBytes) {
    response.destroy();
    throw new DesktopMainError(
      "SERVICE_RESPONSE_TOO_LARGE",
      "The local service response exceeded its limit.",
      false
    );
  }
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const rawChunk of response) {
    const chunk: unknown = rawChunk;
    if (!Buffer.isBuffer(chunk) && !(chunk instanceof Uint8Array)) {
      throw new DesktopMainError(
        "SERVICE_RESPONSE_INVALID",
        "The local service returned an invalid response.",
        true
      );
    }
    const bytes = Buffer.from(chunk);
    total += bytes.byteLength;
    if (total > maximumBytes) {
      response.destroy();
      throw new DesktopMainError(
        "SERVICE_RESPONSE_TOO_LARGE",
        "The local service response exceeded its limit.",
        false
      );
    }
    chunks.push(bytes);
  }
  return parseResponseBytes(
    response.statusCode ?? 500,
    Buffer.concat(chunks),
    maximumBytes
  );
}

function parseResponseBytes(
  status: number,
  bytes: Buffer,
  maximumBytes = JSON_RESPONSE_LIMIT_BYTES
): unknown {
  if (bytes.byteLength > maximumBytes) {
    throw new DesktopMainError(
      "SERVICE_RESPONSE_TOO_LARGE",
      "The local service response exceeded its limit.",
      false
    );
  }
  let value: unknown;
  try {
    value = JSON.parse(bytes.toString("utf8")) as unknown;
  } catch {
    throw new DesktopMainError(
      "SERVICE_RESPONSE_INVALID",
      "The local service returned an invalid response.",
      true
    );
  }
  if (status < 200 || status >= 300) {
    throw parseApiError(value, status);
  }
  return value;
}

function parseApiError(value: unknown, status: number): DesktopMainError {
  if (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    "error" in value
  ) {
    const apiError = (value as ApiErrorResponse).error;
    if (
      apiError !== null &&
      typeof apiError === "object" &&
      typeof apiError.code === "string" &&
      apiError.code.length <= 80 &&
      typeof apiError.message === "string" &&
      apiError.message.length <= 500 &&
      typeof apiError.retryable === "boolean"
    ) {
      return new DesktopMainError(
        apiError.code,
        apiError.message,
        apiError.retryable,
        safeIdentifier(apiError.correlationId),
        sanitizeDetails(apiError.details)
      );
    }
  }
  return new DesktopMainError(
    `SERVICE_HTTP_${status}`,
    status === 409
      ? "The project changed. Refresh and compare before saving."
      : "The local service could not complete the request.",
    status >= 500
  );
}

function sanitizeDetails(
  value: Readonly<Record<string, string | number | boolean>> | undefined
): Readonly<Record<string, string | number | boolean>> | undefined {
  if (value === undefined) {
    return undefined;
  }
  const safe: Record<string, string | number | boolean> = {};
  for (const [key, detail] of Object.entries(value).slice(0, 12)) {
    if (
      /^[A-Za-z][A-Za-z0-9]{0,63}$/u.test(key) &&
      (typeof detail === "number" ||
        typeof detail === "boolean" ||
        (typeof detail === "string" && detail.length <= 200))
    ) {
      safe[key] = detail;
    }
  }
  return Object.keys(safe).length === 0 ? undefined : safe;
}

function safeIdentifier(value: unknown): string | undefined {
  return typeof value === "string" && value.length <= 128 ? value : undefined;
}

function ensureFixedApiRoute(route: string): void {
  if (
    !route.startsWith("/api/v1/") ||
    route.length > MAX_API_ROUTE_LENGTH ||
    route.includes("\\") ||
    route.includes("\0") ||
    route.includes("..")
  ) {
    throw new ValidationError("The service route was invalid.");
  }
}

function sanitizeMultipartFilename(
  filename: string,
  fallback = "selected-story.txt"
): string {
  const sanitized = filename
    .replace(/[\r\n"]/gu, "_")
    .replace(/[^\p{L}\p{N}._ -]/gu, "_")
    .slice(0, 160);
  if (sanitized.length === 0 || sanitized === "." || sanitized === "..") {
    return fallback;
  }
  return sanitized;
}

function importMediaType(format: DeclaredImportFormat): string {
  switch (format) {
    case "txt":
      return "text/plain";
    case "markdown":
      return "text/markdown";
    case "docx":
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    case "epub":
      return "application/epub+zip";
    case "pdf":
      return "application/pdf";
  }
}

function sameFileIdentity(opened: Stats, current: Stats): boolean {
  return (
    opened.dev === current.dev &&
    opened.ino === current.ino &&
    opened.size === current.size &&
    opened.mtimeMs === current.mtimeMs
  );
}

function validateAuditionPcmWav(bytes: Buffer): void {
  if (
    bytes.byteLength < 44 ||
    bytes.toString("ascii", 0, 4) !== "RIFF" ||
    bytes.toString("ascii", 8, 12) !== "WAVE" ||
    bytes.readUInt32LE(4) + 8 !== bytes.byteLength
  ) {
    throw invalidAuditionAudio();
  }
  let offset = 12;
  let formatVerified = false;
  let dataBytes = 0;
  while (offset + 8 <= bytes.byteLength) {
    const chunkId = bytes.toString("ascii", offset, offset + 4);
    const chunkBytes = bytes.readUInt32LE(offset + 4);
    const bodyStart = offset + 8;
    const bodyEnd = bodyStart + chunkBytes;
    if (bodyEnd > bytes.byteLength) throw invalidAuditionAudio();
    if (chunkId === "fmt ") {
      if (
        chunkBytes < 16 ||
        bytes.readUInt16LE(bodyStart) !== 1 ||
        bytes.readUInt16LE(bodyStart + 2) !== 1 ||
        bytes.readUInt32LE(bodyStart + 4) !== 24_000 ||
        bytes.readUInt16LE(bodyStart + 12) !== 2 ||
        bytes.readUInt16LE(bodyStart + 14) !== 16
      ) {
        throw invalidAuditionAudio();
      }
      formatVerified = true;
    } else if (chunkId === "data") {
      dataBytes += chunkBytes;
    }
    offset = bodyEnd + (chunkBytes % 2);
  }
  if (!formatVerified || dataBytes <= 0 || dataBytes % 2 !== 0) {
    throw invalidAuditionAudio();
  }
}

function invalidAuditionAudio(): DesktopMainError {
  return new DesktopMainError(
    "AUDITION_AUDIO_FORMAT_INVALID",
    "The audition audio was not valid 24 kHz mono PCM WAV.",
    false
  );
}

function importFileChangedError(): DesktopMainError {
  return new DesktopMainError(
    "IMPORT_FILE_CHANGED",
    "The selected document changed while it was being imported.",
    false
  );
}

function modelPackageFileChangedError(): DesktopMainError {
  return new DesktopMainError(
    "MODEL_PACKAGE_FILE_CHANGED",
    "The selected model package changed while it was being uploaded.",
    false
  );
}

async function readFetchBodyLimited(
  response: Response,
  maximumBytes: number
): Promise<Buffer> {
  if (response.body === null) {
    return Buffer.alloc(0);
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const result = await reader.read();
    if (result.done) {
      break;
    }
    total += result.value.byteLength;
    if (total > maximumBytes) {
      await reader.cancel();
      throw new DesktopMainError(
        "SERVICE_RESPONSE_TOO_LARGE",
        "The local service response exceeded its limit.",
        false
      );
    }
    chunks.push(result.value);
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)), total);
}

function validateCreateProjectResponse(value: unknown): void {
  const record = requireRecord(value, "create project response");
  requireIdentifier(record.correlationId, "correlationId");
  const project = requireRecord(record.project, "project");
  requireIdentifier(project.projectId, "projectId");
  requireText(project.name, "name", 120);
  requireInteger(project.revision, "revision");
}

function validateCorrectionResponse(
  value: unknown,
  expected: CorrectSpeakerInput
): CorrectDialogueSpeakerResponse {
  const record = requireRecord(value, "speaker correction response");
  rejectUnknownResponseFields(
    record,
    [
      "correlationId",
      "attribution",
      "appendedCorrection",
      "projectRevision",
      "lineRevision"
    ],
    "speaker correction response"
  );
  requireIdentifier(record.correlationId, "correlationId");
  const attribution = requireRecord(record.attribution, "attribution");
  rejectUnknownResponseFields(
    attribution,
    [
      "schemaVersion",
      "revision",
      "provenance",
      "attributionId",
      "projectId",
      "lineId",
      "proposedSpeakerId",
      "effectiveSpeakerId",
      "effectiveAuthority",
      "evidence",
      "confidence",
      "warnings",
      "humanCorrections",
      "updatedAt"
    ],
    "dialogue attribution"
  );
  if (attribution.schemaVersion !== "1.0.0") {
    throw new ValidationError(
      "The saved attribution schema version is invalid."
    );
  }
  const lineRevision = requireInteger(
    record.lineRevision,
    "lineRevision"
  );
  const attributionRevision = requireInteger(
    attribution.revision,
    "attribution revision"
  );
  const attributionId = requireIdentifier(
    attribution.attributionId,
    "attributionId"
  );
  const projectId = requireIdentifier(
    attribution.projectId,
    "attribution projectId"
  );
  const lineId = requireIdentifier(attribution.lineId, "attribution lineId");
  requireNullableIdentifier(
    attribution.proposedSpeakerId,
    "proposedSpeakerId"
  );
  const effectiveSpeakerId = requireNullableIdentifier(
    attribution.effectiveSpeakerId,
    "effectiveSpeakerId"
  );
  if (attribution.effectiveAuthority !== "human") {
    throw new ValidationError("The saved correction authority is invalid.");
  }
  const attributionProvenance = validateSpeakerCorrectionProvenance(
    attribution.provenance,
    expected.lineId,
    lineRevision
  );
  validateLegacyEvidence(attribution.evidence);
  validateLegacyConfidence(attribution.confidence);
  validateJobWarnings(attribution.warnings, "attribution warnings");
  const attributionUpdatedAt = requireIsoDate(
    attribution.updatedAt,
    "attribution updatedAt"
  );
  const projectRevision = requireInteger(
    record.projectRevision,
    "projectRevision"
  );
  if (projectRevision < 1) {
    throw new ValidationError("projectRevision is invalid.");
  }
  const expectedLineRevision = expected.expectedRevision + 1;
  if (
    projectId !== expected.projectId ||
    lineId !== expected.lineId ||
    effectiveSpeakerId !== expected.characterId ||
    lineRevision !== expectedLineRevision ||
    attributionRevision !== expectedLineRevision
  ) {
    throw new ValidationError(
      "The saved speaker correction did not bind the exact request."
    );
  }
  const expectedReason =
    expected.reason ?? "Speaker corrected by the local user.";
  const appended = validateLegacySpeakerCorrection(
    record.appendedCorrection,
    expected,
    attributionId,
    expectedLineRevision,
    expectedReason
  );
  if (
    !Array.isArray(attribution.humanCorrections) ||
    attribution.humanCorrections.length !== 1
  ) {
    throw new ValidationError(
      "The saved attribution correction history is invalid."
    );
  }
  const projected = validateLegacySpeakerCorrection(
    attribution.humanCorrections[0],
    expected,
    attributionId,
    expectedLineRevision,
    expectedReason
  );
  if (
    !jsonValuesExactlyEqual(projected.record, appended.record) ||
    attributionProvenance.actorId !== appended.actorId ||
    attributionProvenance.recordedAt !== appended.recordedAt ||
    attributionUpdatedAt !== appended.recordedAt
  ) {
    throw new ValidationError(
      "The saved attribution correction projection is inconsistent."
    );
  }
  return value as CorrectDialogueSpeakerResponse;
}

function validateLegacySpeakerCorrection(
  value: unknown,
  expected: CorrectSpeakerInput,
  attributionId: string,
  lineRevision: number,
  expectedReason: string
): {
  readonly record: Record<string, unknown>;
  readonly actorId: string;
  readonly recordedAt: string;
} {
  const correction = requireRecord(value, "speaker correction");
  rejectUnknownResponseFields(
    correction,
    [
      "correctionId",
      "target",
      "fieldPath",
      "previousValueFingerprint",
      "previousCharacterId",
      "correctedValue",
      "correctedCharacterId",
      "reason",
      "authority",
      "recordedAt",
      "immutable",
      "lockedAgainstAutomation",
      "supersedesCorrectionId"
    ],
    "speaker correction"
  );
  const correctionId = requireIdentifier(
    correction.correctionId,
    "correctionId"
  );
  const target = requireRecord(correction.target, "correction target");
  rejectUnknownResponseFields(
    target,
    ["entityType", "entityId", "revision"],
    "correction target"
  );
  const targetEntityId = requireIdentifier(
    target.entityId,
    "correction target entityId"
  );
  const targetRevision = requireInteger(
    target.revision,
    "correction target revision"
  );
  if (
    target.entityType !== "DialogueLine" ||
    targetEntityId !== expected.lineId ||
    targetRevision !== lineRevision ||
    correction.fieldPath !== "/effectiveSpeakerId"
  ) {
    throw new ValidationError(
      "The saved speaker correction target is inconsistent."
    );
  }
  requireSha256(
    correction.previousValueFingerprint,
    "previousValueFingerprint"
  );
  requireNullableIdentifier(
    correction.previousCharacterId,
    "previousCharacterId"
  );
  const correctedValue = requireNullableIdentifier(
    correction.correctedValue,
    "correctedValue"
  );
  const correctedCharacterId = requireNullableIdentifier(
    correction.correctedCharacterId,
    "correctedCharacterId"
  );
  const reason = requireText(correction.reason, "correction reason", 500);
  const authority = requireRecord(
    correction.authority,
    "correction authority"
  );
  rejectUnknownResponseFields(
    authority,
    ["source", "actorId"],
    "correction authority"
  );
  if (authority.source !== "human") {
    throw new ValidationError(
      "The saved correction authority is invalid."
    );
  }
  const actorId = requireIdentifier(
    authority.actorId,
    "correction actorId"
  );
  const recordedAt = requireIsoDate(
    correction.recordedAt,
    "correction recordedAt"
  );
  if (
    correctedValue !== expected.characterId ||
    correctedCharacterId !== expected.characterId ||
    reason !== expectedReason ||
    correction.immutable !== true ||
    correction.lockedAgainstAutomation !== true
  ) {
    throw new ValidationError(
      "The saved speaker correction value is inconsistent."
    );
  }
  if (correction.supersedesCorrectionId !== undefined) {
    const supersedes = requireIdentifier(
      correction.supersedesCorrectionId,
      "supersedesCorrectionId"
    );
    if (supersedes === correctionId) {
      throw new ValidationError(
        "A speaker correction cannot supersede itself."
      );
    }
  }
  // The attribution id is deliberately not accepted as the correction target:
  // the legacy contract protects the dialogue line revision itself.
  requireIdentifier(attributionId, "attributionId");
  return { record: correction, actorId, recordedAt };
}

function validateSpeakerCorrectionProvenance(
  value: unknown,
  lineId: string,
  lineRevision: number
): { readonly actorId: string; readonly recordedAt: string } {
  const provenance = requireRecord(value, "attribution provenance");
  rejectUnknownResponseFields(
    provenance,
    [
      "origin",
      "recordedAt",
      "actorId",
      "agentExecutionId",
      "sourceReferences",
      "inputFingerprint",
      "notes"
    ],
    "attribution provenance"
  );
  if (provenance.origin !== "human") {
    throw new ValidationError(
      "The saved attribution provenance is invalid."
    );
  }
  const recordedAt = requireIsoDate(
    provenance.recordedAt,
    "provenance recordedAt"
  );
  const actorId = requireIdentifier(
    provenance.actorId,
    "provenance actorId"
  );
  if (provenance.agentExecutionId !== undefined) {
    requireIdentifier(
      provenance.agentExecutionId,
      "provenance agentExecutionId"
    );
  }
  if (provenance.inputFingerprint !== undefined) {
    requireSha256(
      provenance.inputFingerprint,
      "provenance inputFingerprint"
    );
  }
  if (provenance.notes !== undefined) {
    requireText(provenance.notes, "provenance notes", 4_000);
  }
  if (
    !Array.isArray(provenance.sourceReferences) ||
    provenance.sourceReferences.length === 0 ||
    provenance.sourceReferences.length > 32
  ) {
    throw new ValidationError(
      "The saved attribution provenance source is invalid."
    );
  }
  let boundSource = false;
  for (const item of provenance.sourceReferences) {
    const reference = requireRecord(item, "provenance source reference");
    rejectUnknownResponseFields(
      reference,
      ["entityType", "entityId", "revision"],
      "provenance source reference"
    );
    const entityId = requireIdentifier(
      reference.entityId,
      "provenance source entityId"
    );
    const revision = requireInteger(
      reference.revision,
      "provenance source revision"
    );
    requireText(
      reference.entityType,
      "provenance source entityType",
      128
    );
    if (
      reference.entityType === "DialogueLine" &&
      entityId === lineId &&
      revision === lineRevision
    ) {
      boundSource = true;
    }
  }
  if (!boundSource) {
    throw new ValidationError(
      "The saved attribution provenance did not bind the dialogue line."
    );
  }
  return { actorId, recordedAt };
}

function validateLegacyEvidence(value: unknown): void {
  if (!Array.isArray(value) || value.length > 128) {
    throw new ValidationError("Attribution evidence is invalid.");
  }
  for (const item of value) {
    const span = requireRecord(item, "attribution evidence");
    rejectUnknownResponseFields(
      span,
      [
        "sourceDocumentId",
        "offsetUnit",
        "startOffset",
        "endOffset",
        "startUtf8Byte",
        "endUtf8Byte",
        "line",
        "column",
        "textSha256"
      ],
      "attribution evidence"
    );
    requireIdentifier(span.sourceDocumentId, "evidence sourceDocumentId");
    if (span.offsetUnit !== "unicode-code-point") {
      throw new ValidationError("Attribution evidence offset unit is invalid.");
    }
    const start = requireInteger(span.startOffset, "evidence startOffset");
    const end = requireInteger(span.endOffset, "evidence endOffset");
    if (end <= start) {
      throw new ValidationError("Attribution evidence range is invalid.");
    }
    for (const field of [
      "startUtf8Byte",
      "endUtf8Byte",
      "line",
      "column"
    ] as const) {
      if (span[field] !== undefined) {
        requireInteger(span[field], `evidence ${field}`);
      }
    }
    requireSha256(span.textSha256, "evidence textSha256");
  }
}

function validateLegacyConfidence(value: unknown): void {
  const confidence = requireRecord(value, "attribution confidence");
  rejectUnknownResponseFields(
    confidence,
    ["score", "basis", "calibrationId", "fieldScores"],
    "attribution confidence"
  );
  requireUnitInterval(confidence.score, "attribution confidence score");
  requireText(confidence.basis, "attribution confidence basis", 1_000);
  if (confidence.calibrationId !== undefined) {
    requireText(
      confidence.calibrationId,
      "attribution confidence calibrationId",
      128
    );
  }
  if (confidence.fieldScores !== undefined) {
    const fieldScores = requireRecord(
      confidence.fieldScores,
      "attribution confidence fieldScores"
    );
    if (Object.keys(fieldScores).length > 64) {
      throw new ValidationError(
        "Attribution confidence fieldScores is invalid."
      );
    }
    for (const [field, score] of Object.entries(fieldScores)) {
      requireText(field, "attribution confidence field", 128);
      requireUnitInterval(
        score,
        `attribution confidence ${field}`
      );
    }
  }
}

function validateJobResponse(
  value: unknown,
  expected: JobResponseExpectation
): void {
  const record = requireRecord(value, "job response");
  rejectUnknownResponseFields(
    record,
    ["correlationId", "job"],
    "job response"
  );
  requireIdentifier(record.correlationId, "correlationId");
  const job = requireRecord(record.job, "job");
  rejectUnknownResponseFields(
    job,
    [
      "jobId",
      "projectId",
      "type",
      "state",
      "target",
      "inputRevision",
      "inputFingerprint",
      "attempt",
      "stage",
      "progress",
      "checkpointAvailable",
      "cancellationRequested",
      "warnings",
      "error",
      "createdAt",
      "updatedAt",
      "terminalAt"
    ],
    "job"
  );
  const jobId = requireIdentifier(job.jobId, "jobId");
  const projectId = requireIdentifier(job.projectId, "projectId");
  const type = requireOneOf(
    job.type,
    [
      "extract_document",
      "analyze_story",
      "analyze_whole_book",
      "analyze_casting",
      "generate_audition"
    ],
    "job type"
  );
  requireOneOf(
    job.state,
    [
      "queued",
      "running",
      "cancel_requested",
      "cancelled",
      "failed",
      "interrupted",
      "paused",
      "succeeded"
    ],
    "job state"
  );
  const target = requireRecord(job.target, "job target");
  rejectUnknownResponseFields(target, ["type", "id"], "job target");
  const targetType = requireOneOf(
    target.type,
    [
      "document_extraction",
      "story",
      "analysis_run",
      "casting_run",
      "audition_session"
    ],
    "job target type"
  );
  requireIdentifier(target.id, "job target id");
  const expectedTargetType = {
    extract_document: "document_extraction",
    analyze_story: "story",
    analyze_whole_book: "analysis_run",
    analyze_casting: "casting_run",
    generate_audition: "audition_session"
  }[type];
  if (targetType !== expectedTargetType) {
    throw new ValidationError("The job target type is inconsistent.");
  }
  const inputRevision = requireInteger(job.inputRevision, "inputRevision");
  const inputFingerprint = requireSha256(
    job.inputFingerprint,
    "inputFingerprint"
  );
  const attempt = requireInteger(job.attempt, "attempt");
  if (attempt < 1) {
    throw new ValidationError("Job attempt is invalid.");
  }
  requireText(job.stage, "stage", 160);
  if (
    typeof job.progress !== "number" ||
    !Number.isFinite(job.progress) ||
    job.progress < 0 ||
    job.progress > 1
  ) {
    throw new ValidationError("Job progress is invalid.");
  }
  requireBoolean(job.checkpointAvailable, "checkpointAvailable");
  requireBoolean(job.cancellationRequested, "cancellationRequested");
  validateJobWarnings(job.warnings, "job warnings");
  if (job.error !== undefined) {
    validateJobError(job.error, "job error");
  }
  requireIsoDate(job.createdAt, "createdAt");
  requireIsoDate(job.updatedAt, "updatedAt");
  if (job.terminalAt !== undefined) {
    requireIsoDate(job.terminalAt, "terminalAt");
  }
  if (
    (expected.jobId !== undefined && jobId !== expected.jobId) ||
    (expected.projectId !== undefined &&
      projectId !== expected.projectId) ||
    (expected.type !== undefined && type !== expected.type) ||
    (expected.inputRevision !== undefined &&
      inputRevision !== expected.inputRevision) ||
    (expected.inputFingerprint !== undefined &&
      inputFingerprint !== expected.inputFingerprint)
  ) {
    throw new ValidationError(
      "The returned job did not bind the exact requested work."
    );
  }
}

function validateJobEventsResponse(
  value: unknown,
  expected: {
    readonly jobId: string;
    readonly afterSequence?: number;
  }
): void {
  const record = requireRecord(value, "job events response");
  rejectUnknownResponseFields(
    record,
    ["correlationId", "events", "lastSequence"],
    "job events response"
  );
  requireIdentifier(record.correlationId, "correlationId");
  if (!Array.isArray(record.events) || record.events.length > 10_000) {
    throw new ValidationError("Job events are invalid.");
  }
  let previous = expected.afterSequence ?? 0;
  for (const item of record.events) {
    const event = requireRecord(item, "job event");
    rejectUnknownResponseFields(
      event,
      [
        "jobId",
        "attempt",
        "sequence",
        "type",
        "state",
        "stage",
        "progress",
        "completedUnits",
        "totalUnits",
        "warning",
        "error",
        "createdAt"
      ],
      "job event"
    );
    if (
      requireIdentifier(event.jobId, "event jobId") !== expected.jobId
    ) {
      throw new ValidationError(
        "The job event did not bind the requested job."
      );
    }
    const attempt = requireInteger(event.attempt, "event attempt");
    if (attempt < 1) {
      throw new ValidationError("Job event attempt is invalid.");
    }
    const sequence = requireInteger(event.sequence, "sequence");
    if (sequence <= previous) {
      throw new ValidationError("Job event ordering is invalid.");
    }
    previous = sequence;
    const eventType = requireOneOf(
      event.type,
      [
        "created",
        "state_changed",
        "progress",
        "checkpoint",
        "warning",
        "failed",
        "completed"
      ],
      "job event type"
    );
    if (event.state !== undefined) {
      requireOneOf(
        event.state,
        [
          "queued",
          "running",
          "cancel_requested",
          "cancelled",
          "failed",
          "interrupted",
          "paused",
          "succeeded"
        ],
        "job event state"
      );
    }
    if (event.stage !== undefined) {
      requireText(event.stage, "event stage", 160);
    }
    if (event.progress !== undefined) {
      requireUnitInterval(event.progress, "event progress");
    }
    const completedUnits =
      event.completedUnits === undefined
        ? undefined
        : requireInteger(event.completedUnits, "completedUnits");
    const totalUnits =
      event.totalUnits === undefined
        ? undefined
        : requireInteger(event.totalUnits, "totalUnits");
    if (
      completedUnits !== undefined &&
      totalUnits !== undefined &&
      completedUnits > totalUnits
    ) {
      throw new ValidationError("Job event units are inconsistent.");
    }
    if (event.warning !== undefined) {
      validateContractWarning(event.warning, "event warning");
    }
    if (event.error !== undefined) {
      validateJobError(event.error, "event error");
    }
    if (
      (eventType === "warning") !== (event.warning !== undefined) ||
      (eventType === "failed") !== (event.error !== undefined)
    ) {
      throw new ValidationError(
        "The job event payload is inconsistent with its type."
      );
    }
    requireIsoDate(event.createdAt, "event createdAt");
  }
  const lastSequence = requireInteger(
    record.lastSequence,
    "lastSequence"
  );
  if (record.events.length > 0 && lastSequence < previous) {
    throw new ValidationError("Job event lastSequence is inconsistent.");
  }
}

function requireRecord(
  value: unknown,
  field: string
): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value as Record<string, unknown>;
}

function jsonValuesExactlyEqual(left: unknown, right: unknown): boolean {
  if (left === right) {
    return true;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) =>
        jsonValuesExactlyEqual(item, right[index])
      )
    );
  }
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object"
  ) {
    return false;
  }
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        jsonValuesExactlyEqual(leftRecord[key], rightRecord[key])
    )
  );
}

function requireIdentifier(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(value)
  ) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
}

function requireNullableIdentifier(
  value: unknown,
  field: string
): string | null {
  return value === null ? null : requireIdentifier(value, field);
}

function requireText(
  value: unknown,
  field: string,
  maximumLength: number
): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > maximumLength
  ) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
}

function requireInteger(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value as number;
}

function rejectUnknownResponseFields(
  value: Record<string, unknown>,
  allowed: readonly string[],
  field: string
): void {
  const allowedFields = new Set(allowed);
  if (Object.keys(value).some((key) => !allowedFields.has(key))) {
    throw new ValidationError(`${field} contains unsupported fields.`);
  }
}

function requireOneOf<const TValue extends string>(
  value: unknown,
  allowed: readonly TValue[],
  field: string
): TValue {
  if (
    typeof value !== "string" ||
    !allowed.includes(value as TValue)
  ) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value as TValue;
}

function requireBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
}

function requireSha256(value: unknown, field: string): string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/u.test(value)) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
}

function requireIsoDate(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.length > 64 ||
    !/^\d{4}-\d{2}-\d{2}T/u.test(value) ||
    !Number.isFinite(Date.parse(value))
  ) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
}

function requireUnitInterval(value: unknown, field: string): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 1
  ) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
}

function validateJobWarnings(value: unknown, field: string): void {
  if (!Array.isArray(value) || value.length > 128) {
    throw new ValidationError(`${field} is invalid.`);
  }
  for (const warning of value) {
    validateContractWarning(warning, field);
  }
}

function validateContractWarning(value: unknown, field: string): void {
  const warning = requireRecord(value, field);
  rejectUnknownResponseFields(
    warning,
    [
      "code",
      "severity",
      "message",
      "requiresHumanReview",
      "relatedEntities"
    ],
    field
  );
  requireText(warning.code, `${field} code`, 128);
  requireOneOf(
    warning.severity,
    ["info", "warning", "error"],
    `${field} severity`
  );
  requireText(warning.message, `${field} message`, 2_000);
  requireBoolean(
    warning.requiresHumanReview,
    `${field} requiresHumanReview`
  );
  if (warning.relatedEntities !== undefined) {
    if (
      !Array.isArray(warning.relatedEntities) ||
      warning.relatedEntities.length > 64
    ) {
      throw new ValidationError(`${field} relatedEntities is invalid.`);
    }
    for (const relatedValue of warning.relatedEntities) {
      const related = requireRecord(
        relatedValue,
        `${field} related entity`
      );
      rejectUnknownResponseFields(
        related,
        ["entityType", "entityId", "revision"],
        `${field} related entity`
      );
      requireText(
        related.entityType,
        `${field} related entityType`,
        128
      );
      requireIdentifier(
        related.entityId,
        `${field} related entityId`
      );
      if (related.revision !== undefined) {
        requireInteger(
          related.revision,
          `${field} related revision`
        );
      }
    }
  }
}

function validateJobError(value: unknown, field: string): void {
  const error = requireRecord(value, field);
  rejectUnknownResponseFields(
    error,
    ["code", "message", "retryable"],
    field
  );
  requireText(error.code, `${field} code`, 128);
  requireText(error.message, `${field} message`, 2_000);
  requireBoolean(error.retryable, `${field} retryable`);
}
