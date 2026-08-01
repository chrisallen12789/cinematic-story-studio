import { createHash } from "node:crypto";

import {
  AUDITION_GATE_IDS,
  MODEL_PACKAGE_ACTIONS,
  PRONUNCIATION_SCOPES,
  SPEECH_AUDITION_CONTRACT_VERSION,
  SPEECH_AUDITION_LIMITS,
  type AuditionClip,
  type AuditionEvidenceBinding,
  type AuditionReview,
  type AuditionReviewDecision,
  type AuditionSession,
  type AuditionWorkspaceSnapshot,
  type PronunciationDictionary,
  type PronunciationEntry,
  type SpeechPreviewRequest,
  type VoiceReadinessSnapshot
} from "@cinematic-story-studio/contracts";

import type {
  AppendPronunciationEntryInput,
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
import {
  DESKTOP_CONTRACT_VERSION,
  type DesktopRequest
} from "../shared/desktop-api.js";
import { ValidationError } from "./validation.js";

const SHA256 = /^[a-f0-9]{64}$/u;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$/u;
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T/u;
const MARKUP_OR_CONTROL = /[<>\p{Cc}]/u;
const GATES = new Set<string>(AUDITION_GATE_IDS);
const SCOPES = new Set<string>(PRONUNCIATION_SCOPES);
const MODEL_ACTIONS = new Set<string>(MODEL_PACKAGE_ACTIONS);
const NORMALIZATION_KINDS = new Set([
  "line_ending",
  "control_whitespace",
  "unicode_composition",
  "typographic_quote",
  "typographic_dash",
  "ellipsis",
  "unsupported_character"
]);
const PROVENANCE_ORIGINS = new Set([
  "fixture_provider",
  "real_local_provider",
  "application",
  "human",
  "system"
]);
const PROVIDER_CLASSES = new Set(["deterministic_fixture", "real_local"]);
const CLIP_STATES = new Set([
  "reviewable",
  "approved",
  "rejected",
  "invalidated"
]);
const CACHE_STATUSES = new Set(["miss", "verified_hit", "corrupt_miss"]);
const MAXIMUM_NORMALIZED_TEXT_CODE_POINTS =
  SPEECH_AUDITION_LIMITS.maximumScriptCodePoints * 3;

export function parseGetAuditionWorkspaceRequest(
  raw: unknown
): DesktopRequest<GetAuditionWorkspaceInput> {
  return parseEnvelope(raw, ["projectId", "roleCursor", "roleLimit"], (payload) => {
    identifier(payload.projectId, "projectId");
    optionalBoundedText(
      payload.roleCursor,
      "roleCursor",
      SPEECH_AUDITION_LIMITS.maximumCursorCodePoints
    );
    if (payload.roleLimit !== undefined) {
      integerBetween(
        payload.roleLimit,
        1,
        SPEECH_AUDITION_LIMITS.maximumPageSize,
        "roleLimit"
      );
    }
  }) as DesktopRequest<GetAuditionWorkspaceInput>;
}

export function parseListModelPackagesRequest(
  raw: unknown
): DesktopRequest<ListModelPackagesInput> {
  return parseEnvelope(raw, ["projectId", "cursor", "limit"], (payload) => {
    identifier(payload.projectId, "projectId");
    page(payload);
  }) as DesktopRequest<ListModelPackagesInput>;
}

export function parseModelPackageActionRequest(
  raw: unknown
): DesktopRequest<PerformModelPackageActionInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "modelPackageId",
      "expectedManifestFingerprint",
      "expectedInstallationRevision",
      "action",
      "reason",
      "idempotencyKey"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      identifier(payload.modelPackageId, "modelPackageId");
      sha256(payload.expectedManifestFingerprint, "expectedManifestFingerprint");
      nullablePositiveInteger(
        payload.expectedInstallationRevision,
        "expectedInstallationRevision"
      );
      enumValue(
        payload.action,
        new Set(["verify", "activate", "deactivate", "repair", "remove"]),
        "action"
      );
      boundedText(payload.reason, "reason", 1_000);
      idempotencyKey(payload.idempotencyKey);
    }
  ) as DesktopRequest<PerformModelPackageActionInput>;
}

export function parseSelectLocalModelPackageRequest(
  raw: unknown
): DesktopRequest<SelectLocalModelPackageInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "modelPackageId",
      "expectedManifestFingerprint",
      "expectedInstallationRevision",
      "operation",
      "acknowledgeRestrictedLocalUse",
      "reason",
      "idempotencyKey"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      identifier(payload.modelPackageId, "modelPackageId");
      sha256(
        payload.expectedManifestFingerprint,
        "expectedManifestFingerprint"
      );
      nullablePositiveInteger(
        payload.expectedInstallationRevision,
        "expectedInstallationRevision"
      );
      enumValue(
        payload.operation,
        new Set(["install", "repair"]),
        "operation"
      );
      if (payload.acknowledgeRestrictedLocalUse !== true) {
        fail("The restricted local-use acknowledgement is required.");
      }
      const reason = boundedText(payload.reason, "reason", 1_000);
      if (MARKUP_OR_CONTROL.test(reason)) {
        fail("The model package reason contained markup or control characters.");
      }
      idempotencyKey(payload.idempotencyKey);
    }
  ) as DesktopRequest<SelectLocalModelPackageInput>;
}

export function parseListPronunciationEntriesRequest(
  raw: unknown
): DesktopRequest<ListPronunciationEntriesInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "cursor",
      "limit",
      "expectedDictionaryRevision",
      "expectedDictionaryFingerprint"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      page(payload);
      positiveInteger(
        payload.expectedDictionaryRevision,
        "expectedDictionaryRevision"
      );
      sha256(
        payload.expectedDictionaryFingerprint,
        "expectedDictionaryFingerprint"
      );
    }
  ) as DesktopRequest<ListPronunciationEntriesInput>;
}

export function parseAppendPronunciationEntryRequest(
  raw: unknown
): DesktopRequest<AppendPronunciationEntryInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "expectedDictionaryRevision",
      "expectedDictionaryFingerprint",
      "writtenForm",
      "language",
      "locale",
      "scope",
      "scopeId",
      "representation",
      "pronunciation",
      "ipa",
      "providerId",
      "providerCompiledValue",
      "caseSensitive",
      "matchRule",
      "priority",
      "reason",
      "supersedesEntryId",
      "idempotencyKey"
    ],
    validatePronunciationMutation
  ) as DesktopRequest<AppendPronunciationEntryInput>;
}

export function parseDecidePronunciationEntryRequest(
  raw: unknown
): DesktopRequest<DecidePronunciationEntryInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "entryId",
      "expectedEntryRevision",
      "expectedEntryFingerprint",
      "expectedDictionaryRevision",
      "expectedDictionaryFingerprint",
      "decision",
      "rationale",
      "idempotencyKey"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      identifier(payload.entryId, "entryId");
      positiveInteger(payload.expectedEntryRevision, "expectedEntryRevision");
      sha256(payload.expectedEntryFingerprint, "expectedEntryFingerprint");
      positiveInteger(
        payload.expectedDictionaryRevision,
        "expectedDictionaryRevision"
      );
      sha256(
        payload.expectedDictionaryFingerprint,
        "expectedDictionaryFingerprint"
      );
      enumValue(
        payload.decision,
        new Set(["approve", "request_changes", "reject"]),
        "decision"
      );
      boundedText(
        payload.rationale,
        "rationale",
        SPEECH_AUDITION_LIMITS.maximumReviewRationaleCodePoints
      );
      idempotencyKey(payload.idempotencyKey);
    }
  ) as DesktopRequest<DecidePronunciationEntryInput>;
}

export function parseClearAuditionCacheRequest(
  raw: unknown
): DesktopRequest<ClearAuditionCacheInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "expectedProjectRevision",
      "reason",
      "idempotencyKey"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      positiveInteger(payload.expectedProjectRevision, "expectedProjectRevision");
      boundedText(payload.reason, "reason", 1_000);
      idempotencyKey(payload.idempotencyKey);
    }
  ) as DesktopRequest<ClearAuditionCacheInput>;
}

export function parseListAuditionSessionsRequest(
  raw: unknown
): DesktopRequest<ListAuditionSessionsInput> {
  return parseEnvelope(
    raw,
    ["projectId", "cursor", "limit", "roleId"],
    (payload) => {
      identifier(payload.projectId, "projectId");
      page(payload);
      optionalIdentifier(payload.roleId, "roleId");
    }
  ) as DesktopRequest<ListAuditionSessionsInput>;
}

export function parseCreateAuditionSessionRequest(
  raw: unknown
): DesktopRequest<CreateAuditionSessionInput> {
  return parseEnvelope(
    raw,
    ["projectId", "roleId", "evidence", "idempotencyKey"],
    (payload) => {
      const projectId = identifier(payload.projectId, "projectId");
      identifier(payload.roleId, "roleId");
      validateEvidenceBinding(payload.evidence, projectId);
      idempotencyKey(payload.idempotencyKey);
    }
  ) as DesktopRequest<CreateAuditionSessionInput>;
}

export function parseCreateAuditionScriptRequest(
  raw: unknown
): DesktopRequest<CreateAuditionScriptInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "auditionSessionId",
      "expectedSessionRevision",
      "kind",
      "text",
      "sourceDocumentId",
      "sourceRevision",
      "sourceSpan",
      "sourceTextSha256",
      "acceptedOptionalNormalizationIds",
      "customPronunciationScopeIds",
      "idempotencyKey"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      identifier(payload.auditionSessionId, "auditionSessionId");
      positiveInteger(payload.expectedSessionRevision, "expectedSessionRevision");
      enumValue(
        payload.kind,
        new Set([
          "standardized_synthetic",
          "approved_manuscript_excerpt",
          "role_dialogue_excerpt",
          "narrator_excerpt",
          "pronunciation_test",
          "synthetic_fallback"
        ]),
        "kind"
      );
      const text = boundedPrivateText(payload.text, "text");
      nullableIdentifier(payload.sourceDocumentId, "sourceDocumentId");
      nullablePositiveInteger(payload.sourceRevision, "sourceRevision");
      nullableSpan(payload.sourceSpan, "sourceSpan");
      const sourceTextSha256 = sha256(
        payload.sourceTextSha256,
        "sourceTextSha256"
      );
      if (textSha256(text) !== sourceTextSha256) {
        fail("The audition text did not match its declared SHA-256.");
      }
      identifierArray(
        payload.acceptedOptionalNormalizationIds,
        "acceptedOptionalNormalizationIds",
        2_000
      );
      optionalIdentifierArray(
        payload.customPronunciationScopeIds,
        "customPronunciationScopeIds",
        50
      );
      idempotencyKey(payload.idempotencyKey);
    }
  ) as DesktopRequest<CreateAuditionScriptInput>;
}

export function parsePreviewAuditionNormalizationRequest(
  raw: unknown
): DesktopRequest<PreviewAuditionNormalizationInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "auditionSessionId",
      "expectedSessionRevision",
      "text",
      "sourceTextSha256",
      "acceptedOptionalNormalizationIds",
      "customPronunciationScopeIds"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      identifier(payload.auditionSessionId, "auditionSessionId");
      positiveInteger(payload.expectedSessionRevision, "expectedSessionRevision");
      const text = boundedPrivateText(payload.text, "text");
      const sourceTextSha256 = sha256(
        payload.sourceTextSha256,
        "sourceTextSha256"
      );
      if (textSha256(text) !== sourceTextSha256) {
        fail("The audition text did not match its declared SHA-256.");
      }
      identifierArray(
        payload.acceptedOptionalNormalizationIds,
        "acceptedOptionalNormalizationIds",
        2_000
      );
      optionalIdentifierArray(
        payload.customPronunciationScopeIds,
        "customPronunciationScopeIds",
        50
      );
    }
  ) as DesktopRequest<PreviewAuditionNormalizationInput>;
}

export function parseGenerateAuditionRequest(
  raw: unknown
): DesktopRequest<GenerateAuditionInput> {
  return parseEnvelope(raw, ["projectId", "preview"], (payload) => {
    const projectId = identifier(payload.projectId, "projectId");
    validateSpeechPreviewRequest(payload.preview, projectId);
  }) as DesktopRequest<GenerateAuditionInput>;
}

export function parseListAuditionClipsRequest(
  raw: unknown
): DesktopRequest<ListAuditionClipsInput> {
  return parseEnvelope(
    raw,
    ["projectId", "cursor", "limit", "auditionSessionId", "roleId"],
    (payload) => {
      identifier(payload.projectId, "projectId");
      page(payload);
      optionalIdentifier(payload.auditionSessionId, "auditionSessionId");
      optionalIdentifier(payload.roleId, "roleId");
    }
  ) as DesktopRequest<ListAuditionClipsInput>;
}

export function parseListAuditionReviewDecisionsRequest(
  raw: unknown
): DesktopRequest<ListAuditionReviewDecisionsInput> {
  return parseEnvelope(
    raw,
    ["projectId", "gateId", "roleId", "cursor", "limit"],
    (payload) => {
      identifier(payload.projectId, "projectId");
      enumValue(payload.gateId, GATES, "gateId");
      nullableIdentifier(payload.roleId, "roleId");
      validateReviewScope(payload.gateId, payload.roleId);
      page(payload);
    }
  ) as DesktopRequest<ListAuditionReviewDecisionsInput>;
}

export function parseLoadAuditionAudioRequest(
  raw: unknown
): DesktopRequest<LoadAuditionAudioInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "auditionClipId",
      "auditionSessionId",
      "audioArtifactId",
      "expectedClipRevision",
      "expectedClipFingerprint",
      "expectedArtifactSha256",
      "mediaType",
      "byteSize"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      identifier(payload.auditionClipId, "auditionClipId");
      identifier(payload.auditionSessionId, "auditionSessionId");
      identifier(payload.audioArtifactId, "audioArtifactId");
      positiveInteger(payload.expectedClipRevision, "expectedClipRevision");
      sha256(payload.expectedClipFingerprint, "expectedClipFingerprint");
      sha256(payload.expectedArtifactSha256, "expectedArtifactSha256");
      if (payload.mediaType !== "audio/wav") {
        fail("Only authenticated PCM WAV audition audio is allowed.");
      }
      integerBetween(
        payload.byteSize,
        45,
        SPEECH_AUDITION_LIMITS.maximumAudioBytes,
        "byteSize"
      );
    }
  ) as DesktopRequest<LoadAuditionAudioInput>;
}

export function parseDecideAuditionReviewRequest(
  raw: unknown
): DesktopRequest<DecideAuditionReviewInput> {
  return parseEnvelope(
    raw,
    [
      "projectId",
      "gateId",
      "roleId",
      "reviewId",
      "expectedReviewRevision",
      "expectedEvidenceFingerprint",
      "decision",
      "rationale",
      "supersedesDecisionId",
      "idempotencyKey"
    ],
    (payload) => {
      identifier(payload.projectId, "projectId");
      enumValue(payload.gateId, GATES, "gateId");
      nullableIdentifier(payload.roleId, "roleId");
      identifier(payload.reviewId, "reviewId");
      positiveInteger(payload.expectedReviewRevision, "expectedReviewRevision");
      sha256(payload.expectedEvidenceFingerprint, "expectedEvidenceFingerprint");
      enumValue(
        payload.decision,
        new Set(["approve", "request_changes", "reject"]),
        "decision"
      );
      boundedText(
        payload.rationale,
        "rationale",
        SPEECH_AUDITION_LIMITS.maximumReviewRationaleCodePoints
      );
      nullableIdentifier(payload.supersedesDecisionId, "supersedesDecisionId");
      idempotencyKey(payload.idempotencyKey);
    }
  ) as DesktopRequest<DecideAuditionReviewInput>;
}

export function validateAuditionWorkspaceResponse(
  raw: unknown,
  expected: GetAuditionWorkspaceInput
): AuditionWorkspaceResponse {
  const response = record(raw, "audition workspace response");
  exactKeys(response, ["correlationId", "workspace"], "audition workspace response");
  identifier(response.correlationId, "correlationId");
  validateWorkspace(
    response.workspace,
    expected.projectId,
    expected.roleLimit
  );
  return raw as AuditionWorkspaceResponse;
}

export function validateModelPackagesResponse(
  raw: unknown,
  expected: ListModelPackagesInput
): ModelPackagesResponse {
  const response = validatePageResponse(raw, "model package page", expected.limit);
  exactKeysWithOptional(response, ["correlationId", "projectId", "items", "pageSize", "total"], ["nextCursor"], "model package page");
  ownedProject(response.projectId, expected.projectId);
  const items = boundedArray(response.items, "items", SPEECH_AUDITION_LIMITS.maximumPageSize);
  for (const value of items) {
    const item = record(value, "model package item");
    exactKeys(item, ["manifest", "installation", "verification"], "model package item");
    const manifest = validateModelManifest(item.manifest);
    const installation =
      item.installation === null ? null : validateInstallation(item.installation);
    const verification =
      item.verification === null ? null : validateVerification(item.verification);
    if (
      installation !== null &&
      (installation.modelPackageId !== manifest.modelPackageId ||
        installation.manifestFingerprint !== manifest.manifestFingerprint)
    ) {
      fail("The model installation did not match its package manifest.");
    }
    if (verification !== null && installation === null) {
      fail("The model verification omitted its installation evidence.");
    }
    if (
      verification !== null &&
      installation !== null &&
      (verification.installationId !== installation.installationId ||
        verification.modelPackageId !== installation.modelPackageId ||
        verification.manifestFingerprint !== installation.manifestFingerprint)
    ) {
      fail("The model verification did not match its installation evidence.");
    }
  }
  return raw as ModelPackagesResponse;
}

export function validatePronunciationEntriesResponse(
  raw: unknown,
  expected: ListPronunciationEntriesInput
): PronunciationEntriesResponse {
  const response = validatePageResponse(raw, "pronunciation entry page", expected.limit);
  exactKeysWithOptional(response, ["correlationId", "projectId", "dictionary", "items", "pageSize", "total"], ["nextCursor"], "pronunciation entry page");
  ownedProject(response.projectId, expected.projectId);
  const dictionary = validateDictionary(response.dictionary, expected.projectId);
  if (
    dictionary.revision !== expected.expectedDictionaryRevision ||
    dictionary.dictionaryFingerprint !== expected.expectedDictionaryFingerprint
  ) {
    fail("The pronunciation page did not match the expected dictionary evidence.");
  }
  for (const entry of boundedArray(response.items, "items", SPEECH_AUDITION_LIMITS.maximumPageSize)) {
    validatePronunciationEntry(entry, expected.projectId, dictionary);
  }
  return raw as PronunciationEntriesResponse;
}

export function validateCreatePronunciationEntryResponse(
  raw: unknown,
  expected: AppendPronunciationEntryInput
): CreatePronunciationEntryResponse {
  const response = validatePronunciationMutationResponse(
    raw,
    expected.projectId,
    "create pronunciation response"
  );
  const entry = record(response.entry, "created pronunciation entry");
  const dictionary = record(response.dictionary, "created pronunciation dictionary");
  if (
    dictionary.revision !== expected.expectedDictionaryRevision + 1 ||
    dictionary.dictionaryFingerprint === expected.expectedDictionaryFingerprint ||
    entry.dictionaryRevision !== dictionary.revision ||
    entry.revision !== 1 ||
    entry.writtenForm !== expected.writtenForm ||
    entry.language !== expected.language ||
    entry.locale !== expected.locale ||
    entry.scope !== expected.scope ||
    entry.scopeId !== expected.scopeId ||
    entry.representation !== expected.representation ||
    entry.pronunciation !== expected.pronunciation ||
    entry.ipa !== expected.ipa ||
    entry.providerId !== expected.providerId ||
    entry.providerCompiledValue !== expected.providerCompiledValue ||
    entry.caseSensitive !== expected.caseSensitive ||
    entry.matchRule !== expected.matchRule ||
    entry.priority !== expected.priority ||
    entry.reason !== expected.reason ||
    entry.verificationState !== "pending" ||
    entry.supersedesEntryId !== expected.supersedesEntryId ||
    response.invalidatedClipCount !== 0 ||
    response.invalidatedClipIdsTruncated !== false ||
    (response.invalidatedGateIds as readonly unknown[]).length !== 0
  ) {
    fail("The pronunciation creation response did not match the requested mutation.");
  }
  return raw as CreatePronunciationEntryResponse;
}

export function validateDecidePronunciationEntryResponse(
  raw: unknown,
  expected: DecidePronunciationEntryInput
): DecidePronunciationEntryResponse {
  const response = validatePronunciationMutationResponse(
    raw,
    expected.projectId,
    "pronunciation decision response"
  );
  const entry = record(response.entry, "pronunciation decision entry");
  const dictionary = record(
    response.dictionary,
    "pronunciation decision dictionary"
  );
  const expectedState =
    expected.decision === "approve"
      ? "approved"
      : expected.decision === "request_changes"
        ? "changes_requested"
        : "rejected";
  if (
    entry.entryId !== expected.entryId ||
    entry.revision !== expected.expectedEntryRevision + 1 ||
    entry.entryFingerprint === expected.expectedEntryFingerprint ||
    entry.dictionaryRevision !== dictionary.revision ||
    dictionary.revision !== expected.expectedDictionaryRevision + 1 ||
    dictionary.dictionaryFingerprint === expected.expectedDictionaryFingerprint ||
    entry.verificationState !== expectedState ||
    entry.reason !== expected.rationale
  ) {
    fail("The pronunciation decision response did not match the requested entry.");
  }
  return raw as DecidePronunciationEntryResponse;
}

export function validateClearAuditionCacheResponse(
  raw: unknown,
  expected: ClearAuditionCacheInput
): ClearAuditionCacheResponse {
  const response = record(raw, "clear audition cache response");
  exactKeysWithOptional(
    response,
    [
      "correlationId",
      "projectId",
      "clearedRecordCount",
      "alreadyClearedRecordCount",
      "projectRevision"
    ],
    ["purgedArtifactCount"],
    "clear audition cache response"
  );
  identifier(response.correlationId, "correlationId");
  ownedProject(response.projectId, expected.projectId);
  integerBetween(response.clearedRecordCount, 0, 10_000, "clearedRecordCount");
  integerBetween(
    response.alreadyClearedRecordCount,
    0,
    10_000,
    "alreadyClearedRecordCount"
  );
  const projectRevision = positiveInteger(
    response.projectRevision,
    "projectRevision"
  );
  const expectedProjectRevision =
    expected.expectedProjectRevision +
    (Number(response.clearedRecordCount) > 0 ? 1 : 0);
  if (projectRevision !== expectedProjectRevision) {
    fail("The cache-clear response did not match the expected project revision.");
  }
  if (response.purgedArtifactCount !== undefined) {
    integerBetween(
      response.purgedArtifactCount,
      0,
      10_000,
      "purgedArtifactCount"
    );
  }
  return raw as ClearAuditionCacheResponse;
}

function validatePronunciationMutationResponse(
  raw: unknown,
  expectedProjectId: string,
  label: string
): Record<string, unknown> {
  const response = record(raw, label);
  exactKeys(
    response,
    [
      "correlationId",
      "entry",
      "dictionary",
      "invalidatedClipIds",
      "invalidatedClipCount",
      "invalidatedClipIdsTruncated",
      "preservedClipIds",
      "preservedClipCount",
      "preservedClipIdsTruncated",
      "invalidatedGateIds"
    ],
    label
  );
  identifier(response.correlationId, "correlationId");
  const dictionary = validateDictionary(response.dictionary, expectedProjectId);
  validatePronunciationEntry(response.entry, expectedProjectId, dictionary);
  const invalidatedClipIds = identifierArray(
    response.invalidatedClipIds,
    "invalidatedClipIds",
    200
  );
  const invalidatedClipCount = integerBetween(
    response.invalidatedClipCount,
    0,
    10_000,
    "invalidatedClipCount"
  );
  const invalidatedClipIdsTruncated = booleanValue(
    response.invalidatedClipIdsTruncated,
    "invalidatedClipIdsTruncated"
  );
  const preservedClipIds = identifierArray(
    response.preservedClipIds,
    "preservedClipIds",
    200
  );
  const preservedClipCount = integerBetween(
    response.preservedClipCount,
    0,
    10_000,
    "preservedClipCount"
  );
  const preservedClipIdsTruncated = booleanValue(
    response.preservedClipIdsTruncated,
    "preservedClipIdsTruncated"
  );
  if (
    invalidatedClipIdsTruncated !==
      (invalidatedClipCount > invalidatedClipIds.length) ||
    preservedClipIdsTruncated !==
      (preservedClipCount > preservedClipIds.length) ||
    invalidatedClipCount < invalidatedClipIds.length ||
    preservedClipCount < preservedClipIds.length
  ) {
    fail("The pronunciation mutation clip samples contradicted their counts.");
  }
  const invalidatedIds = new Set(invalidatedClipIds);
  if (preservedClipIds.some((clipId) => invalidatedIds.has(clipId))) {
    fail("The pronunciation mutation clip samples overlapped.");
  }
  gateArray(response.invalidatedGateIds, "invalidatedGateIds");
  return response;
}

export function validateAuditionSessionsResponse(
  raw: unknown,
  expected: ListAuditionSessionsInput
): AuditionSessionsResponse {
  const response = validatePageResponse(raw, "audition session page", expected.limit);
  exactKeysWithOptional(response, ["correlationId", "projectId", "items", "pageSize", "total"], ["nextCursor"], "audition session page");
  ownedProject(response.projectId, expected.projectId);
  for (const session of boundedArray(response.items, "items", SPEECH_AUDITION_LIMITS.maximumPageSize)) {
    validateSession(session, expected.projectId, expected.roleId);
  }
  return raw as AuditionSessionsResponse;
}

export function validateCreateAuditionSessionResponse(
  raw: unknown,
  expected: CreateAuditionSessionInput
): CreateAuditionSessionResponse {
  const response = record(raw, "create audition session response");
  exactKeys(response, ["correlationId", "session"], "create audition session response");
  identifier(response.correlationId, "correlationId");
  const session = validateSession(
    response.session,
    expected.projectId,
    expected.roleId
  );
  assertSessionMatchesEvidence(
    session,
    expected.evidence,
    "created audition session"
  );
  return raw as CreateAuditionSessionResponse;
}

export function validateCreateAuditionScriptResponse(
  raw: unknown,
  expected: CreateAuditionScriptInput
): CreateAuditionScriptResponse {
  const response = record(raw, "create audition script response");
  exactKeys(response, ["correlationId", "script", "normalizationPlan", "pronunciationPlan", "session"], "create audition script response");
  identifier(response.correlationId, "correlationId");
  const script = record(response.script, "script");
  exactKeys(
    script,
    [
      "contractVersion",
      "auditionScriptId",
      "auditionSessionId",
      "projectId",
      "roleId",
      "kind",
      "sourceTextSha256",
      "sourceSpan",
      "sourceDocumentId",
      "sourceAnalysisEntity",
      "sourceRevision",
      "normalizedTextSha256",
      "normalizationPlanId",
      "pronunciationPlanId",
      "localOnly",
      "scriptFingerprint",
      "createdAt",
      "text"
    ],
    "script"
  );
  contractVersion(script.contractVersion, "audition script");
  identifier(script.auditionScriptId, "auditionScriptId");
  ownedProject(script.projectId, expected.projectId);
  if (script.auditionSessionId !== expected.auditionSessionId) fail("The script did not belong to the requested session.");
  identifier(script.auditionSessionId, "auditionSessionId");
  identifier(script.roleId, "roleId");
  if (script.kind !== expected.kind) {
    fail("The script kind did not match the requested audition script.");
  }
  const text = boundedPrivateText(script.text, "script.text");
  if (
    text !== expected.text ||
    textSha256(text) !== expected.sourceTextSha256 ||
    sha256(script.sourceTextSha256, "sourceTextSha256") !==
      expected.sourceTextSha256
  ) {
    fail("The returned audition script text did not match its exact source hash.");
  }
  const sourceSpan =
    script.sourceSpan === null
      ? null
      : validateTextSpan(
          script.sourceSpan,
          "script source span",
          SPEECH_AUDITION_LIMITS.maximumScriptCodePoints
        );
  nullableIdentifier(script.sourceDocumentId, "sourceDocumentId");
  if (script.sourceRevision !== null) {
    positiveInteger(script.sourceRevision, "sourceRevision");
  }
  const expectedSpan = expected.sourceSpan;
  if (
    script.sourceDocumentId !== expected.sourceDocumentId ||
    script.sourceRevision !== expected.sourceRevision ||
    (sourceSpan === null) !== (expectedSpan === null) ||
    (sourceSpan !== null &&
      expectedSpan !== null &&
      (sourceSpan.start !== expectedSpan.start ||
        sourceSpan.end !== expectedSpan.end))
  ) {
    fail("The returned audition script source span did not match the request.");
  }
  const semanticKind =
    script.kind === "role_dialogue_excerpt" ||
    script.kind === "narrator_excerpt";
  if (semanticKind) {
    const sourceEntity = record(
      script.sourceAnalysisEntity,
      "script source analysis entity"
    );
    exactKeys(
      sourceEntity,
      ["entityId", "collection", "effectiveRevision", "effectiveFingerprint"],
      "script source analysis entity"
    );
    identifier(sourceEntity.entityId, "source analysis entityId");
    const expectedCollection =
      script.kind === "role_dialogue_excerpt"
        ? "dialogue-lines"
        : "narration-spans";
    if (sourceEntity.collection !== expectedCollection) {
      fail("The audition script semantic source collection was invalid.");
    }
    positiveInteger(sourceEntity.effectiveRevision, "effectiveRevision");
    sha256(sourceEntity.effectiveFingerprint, "effectiveFingerprint");
    if (sourceSpan === null || script.sourceDocumentId === null) {
      fail("The semantic audition script omitted its exact source span.");
    }
  } else if (script.sourceAnalysisEntity !== null) {
    fail("A non-semantic audition script exposed a Phase 2 entity binding.");
  }
  sha256(script.normalizedTextSha256, "normalizedTextSha256");
  identifier(script.normalizationPlanId, "normalizationPlanId");
  identifier(script.pronunciationPlanId, "pronunciationPlanId");
  if (script.localOnly !== true) {
    fail("The audition script was not marked local-only.");
  }
  sha256(script.scriptFingerprint, "scriptFingerprint");
  timestamp(script.createdAt, "createdAt");
  validateNormalizationPlan(
    response.normalizationPlan,
    expected.projectId,
    expected.sourceTextSha256
  );
  validatePronunciationPlan(response.pronunciationPlan, expected.projectId);
  const normalizationPlan = record(response.normalizationPlan, "normalization plan");
  const pronunciationPlan = record(response.pronunciationPlan, "pronunciation plan");
  validateNormalizationSelections(
    normalizationPlan,
    expected.acceptedOptionalNormalizationIds
  );
  validateExactIdentifierSet(
    record(pronunciationPlan.scopeContext, "pronunciation scope context")
      .customScopeIds,
    expected.customPronunciationScopeIds ?? [],
    "pronunciation custom scope evidence"
  );
  if (
    normalizationPlan.normalizationPlanId !== script.normalizationPlanId ||
    normalizationPlan.normalizedTextSha256 !== script.normalizedTextSha256 ||
    pronunciationPlan.pronunciationPlanId !== script.pronunciationPlanId ||
    pronunciationPlan.sourceTextSha256 !== script.normalizedTextSha256 ||
    pronunciationPlan.roleId !== script.roleId
  ) {
    fail("The audition script did not match its compiled plan evidence.");
  }
  const session = validateSession(response.session, expected.projectId);
  if (
    session.auditionSessionId !== script.auditionSessionId ||
    session.revision !== expected.expectedSessionRevision ||
    session.roleId !== script.roleId ||
    normalizationPlan.providerId !== session.providerId ||
    pronunciationPlan.providerId !== session.providerId ||
    pronunciationPlan.dictionaryRevision !==
      session.pronunciationDictionaryRevision ||
    pronunciationPlan.dictionaryFingerprint !==
      session.pronunciationDictionaryFingerprint
  ) {
    fail("The audition script did not match its returned session.");
  }
  return raw as CreateAuditionScriptResponse;
}

export function validatePreviewNormalizationResponse(
  raw: unknown,
  expected: PreviewAuditionNormalizationInput
): PreviewNormalizationResponse {
  const response = record(raw, "normalization preview response");
  exactKeys(
    response,
    [
      "correlationId",
      "projectId",
      "auditionSessionId",
      "auditionSessionRevision",
      "providerId",
      "acceptedOptionalNormalizationIds",
      "customPronunciationScopeIds",
      "plan"
    ],
    "normalization preview response"
  );
  identifier(response.correlationId, "correlationId");
  ownedProject(response.projectId, expected.projectId);
  if (
    response.auditionSessionId !== expected.auditionSessionId ||
    response.auditionSessionRevision !== expected.expectedSessionRevision
  ) {
    fail("The normalization preview did not match the requested session revision.");
  }
  identifier(response.auditionSessionId, "auditionSessionId");
  positiveInteger(response.auditionSessionRevision, "auditionSessionRevision");
  boundedText(response.providerId, "providerId", 128);
  validateNormalizationPlan(
    response.plan,
    expected.projectId,
    expected.sourceTextSha256
  );
  const plan = record(response.plan, "normalization plan");
  if (response.providerId !== plan.providerId) {
    fail("The normalization preview provider did not match its plan.");
  }
  validateExactIdentifierSet(
    response.acceptedOptionalNormalizationIds,
    expected.acceptedOptionalNormalizationIds,
    "normalization accepted selection evidence"
  );
  validateExactIdentifierSet(
    response.customPronunciationScopeIds,
    expected.customPronunciationScopeIds ?? [],
    "normalization custom scope evidence"
  );
  validateNormalizationSelections(
    plan,
    expected.acceptedOptionalNormalizationIds
  );
  return raw as PreviewNormalizationResponse;
}

export function validateGenerateAuditionResponse(
  raw: unknown,
  expected: GenerateAuditionInput
): GenerateAuditionResponse {
  const response = record(raw, "generate audition response");
  exactKeys(response, ["correlationId", "session", "providerRequest", "jobId"], "generate audition response");
  identifier(response.correlationId, "correlationId");
  const session = validateSession(response.session, expected.projectId);
  validateProviderRequest(response.providerRequest, expected.preview);
  const jobId = identifier(response.jobId, "jobId");
  const evidence = expected.preview.evidence;
  if (
    session.auditionSessionId !== expected.preview.auditionSessionId ||
    session.revision !== expected.preview.auditionSessionRevision ||
    session.jobId !== jobId
  ) {
    fail("The generated session did not match the governed preview evidence.");
  }
  assertSessionMatchesEvidence(
    session,
    evidence,
    "generated audition session"
  );
  return raw as GenerateAuditionResponse;
}

export function validateAuditionClipsResponse(
  raw: unknown,
  expected: ListAuditionClipsInput
): AuditionClipsResponse {
  const response = validatePageResponse(raw, "audition clip page", expected.limit);
  exactKeysWithOptional(response, ["correlationId", "projectId", "items", "pageSize", "total"], ["nextCursor"], "audition clip page");
  ownedProject(response.projectId, expected.projectId);
  for (const clip of boundedArray(response.items, "items", SPEECH_AUDITION_LIMITS.maximumPageSize)) {
    validateClip(clip, expected.projectId, expected.auditionSessionId, expected.roleId);
  }
  return raw as AuditionClipsResponse;
}

export function validateAuditionReviewDecisionsResponse(
  raw: unknown,
  expected: ListAuditionReviewDecisionsInput
): AuditionReviewDecisionsResponse {
  const response = validatePageResponse(
    raw,
    "audition review decision page",
    expected.limit
  );
  exactKeysWithOptional(
    response,
    [
      "correlationId",
      "projectId",
      "gateId",
      "roleId",
      "items",
      "pageSize",
      "total"
    ],
    ["nextCursor"],
    "audition review decision page"
  );
  ownedProject(response.projectId, expected.projectId);
  if (
    response.gateId !== expected.gateId ||
    response.roleId !== expected.roleId
  ) {
    fail("The review decision page did not match the requested scope.");
  }
  validateReviewScope(response.gateId, response.roleId);
  const decisionIds = new Set<string>();
  let previousTimestamp = Number.POSITIVE_INFINITY;
  for (const item of boundedArray(
    response.items,
    "items",
    SPEECH_AUDITION_LIMITS.maximumPageSize
  )) {
    const decision = validateReviewDecision(
      item,
      expected.projectId,
      expected.gateId,
      expected.roleId
    );
    const decisionId = decision.decisionId;
    if (decisionIds.has(decisionId)) {
      fail("The review decision page contained duplicate decisions.");
    }
    decisionIds.add(decisionId);
    const decidedAt = Date.parse(decision.decidedAt);
    if (decidedAt > previousTimestamp) {
      fail("The review decision page was not newest first.");
    }
    previousTimestamp = decidedAt;
  }
  return raw as AuditionReviewDecisionsResponse;
}

export function validateModelPackageActionResponse(
  raw: unknown,
  expected: PerformModelPackageActionInput | SelectLocalModelPackageInput
): ModelPackageActionResponse {
  const response = record(raw, "model package action response");
  exactKeys(response, ["correlationId", "installation", "verification"], "model package action response");
  identifier(response.correlationId, "correlationId");
  const installation = validateInstallation(response.installation);
  const expectedAction =
    "action" in expected ? expected.action : expected.operation;
  const expectedRevision =
    expected.expectedInstallationRevision === null
      ? 1
      : expected.expectedInstallationRevision + 1;
  const expectedStatus =
    expectedAction === "activate"
      ? "active"
      : expectedAction === "deactivate"
        ? "inactive"
        : expectedAction === "remove"
          ? "removed"
          : expectedAction === "install" || expectedAction === "repair"
            ? "installed"
            : null;
  if (
    installation.modelPackageId !== expected.modelPackageId ||
    installation.manifestFingerprint !== expected.expectedManifestFingerprint ||
    installation.installationRevision !== expectedRevision ||
    installation.lastAction !== expectedAction ||
    installation.actionReasonCode !== expected.reason ||
    (expectedStatus !== null && installation.status !== expectedStatus)
  ) {
    fail("The model action response did not match the requested package operation.");
  }
  let verification: Record<string, unknown> | null = null;
  if (response.verification !== null) {
    verification = validateVerification(response.verification);
    if (
      verification.installationId !== installation.installationId ||
      verification.modelPackageId !== expected.modelPackageId ||
      verification.manifestFingerprint !== expected.expectedManifestFingerprint
    ) {
      fail("The model verification did not match its installation response.");
    }
  }
  if (
    (expectedAction === "install" ||
      expectedAction === "repair" ||
      expectedAction === "verify" ||
      expectedAction === "activate") &&
    (verification === null || verification.status !== "verified")
  ) {
    fail("The requested model operation omitted exact verified model evidence.");
  }
  return raw as ModelPackageActionResponse;
}

export function validateDecideAuditionReviewResponse(
  raw: unknown,
  expected: DecideAuditionReviewInput
): DecideAuditionReviewResponse {
  const response = record(raw, "audition review decision response");
  exactKeys(response, ["correlationId", "review", "decision", "voiceReadinessSnapshot"], "audition review decision response");
  identifier(response.correlationId, "correlationId");
  const review = validateReview(response.review, expected.projectId);
  if (review.reviewId !== expected.reviewId || review.gateId !== expected.gateId || review.roleId !== expected.roleId) fail("The review response did not match the requested evidence.");
  const decision = validateReviewDecision(
    response.decision,
    expected.projectId,
    expected.gateId,
    expected.roleId
  );
  const expectedDecision =
    expected.decision === "approve"
      ? "approved"
      : expected.decision === "request_changes"
        ? "changes_requested"
        : "rejected";
  const reviewEvidence = record(review.evidence, "review evidence");
  if (
    review.revision !== expected.expectedReviewRevision ||
    reviewEvidence.evidenceFingerprint !== expected.expectedEvidenceFingerprint ||
    review.state !== expectedDecision ||
    decision.reviewId !== expected.reviewId ||
    decision.expectedReviewRevision !== expected.expectedReviewRevision ||
    decision.evidenceFingerprint !== expected.expectedEvidenceFingerprint ||
    decision.decision !== expectedDecision ||
    decision.rationale !== expected.rationale ||
    decision.supersedesDecisionId !== expected.supersedesDecisionId
  ) {
    fail("The decision response did not match the requested review.");
  }
  if (
    review.latestDecision === null ||
    canonicalJsonValue(review.latestDecision) !== canonicalJsonValue(decision) ||
    review.updatedAt !== decision.decidedAt
  ) {
    fail("The returned review did not project the requested immutable decision.");
  }
  const voiceReadinessSnapshot =
    response.voiceReadinessSnapshot === null
      ? null
      : validateVoiceReadinessSnapshot(
          response.voiceReadinessSnapshot,
          expected.projectId
        );
  validateDecisionResponseVoiceReadinessSnapshot(
    voiceReadinessSnapshot,
    review,
    decision
  );
  return raw as DecideAuditionReviewResponse;
}

function parseEnvelope(
  raw: unknown,
  payloadKeys: readonly string[],
  validate: (payload: Record<string, unknown>) => void
): DesktopRequest<unknown> {
  const envelope = record(raw, "desktop request");
  exactKeys(envelope, ["contractVersion", "payload"], "desktop request");
  if (envelope.contractVersion !== DESKTOP_CONTRACT_VERSION) fail("The desktop contract version was invalid.");
  const payload = record(envelope.payload, "request payload");
  exactKeys(payload, payloadKeys, "request payload", true);
  validate(payload);
  return raw as DesktopRequest<unknown>;
}

function validatePronunciationMutation(payload: Record<string, unknown>): void {
  identifier(payload.projectId, "projectId");
  positiveInteger(payload.expectedDictionaryRevision, "expectedDictionaryRevision");
  sha256(payload.expectedDictionaryFingerprint, "expectedDictionaryFingerprint");
  safePronunciationText(payload.writtenForm, "writtenForm", SPEECH_AUDITION_LIMITS.maximumWrittenFormCodePoints);
  boundedText(payload.language, "language", 16);
  nullableBoundedText(payload.locale, "locale", 32);
  enumValue(payload.scope, SCOPES, "scope");
  nullableIdentifier(payload.scopeId, "scopeId");
  if (payload.scope === "project" && payload.scopeId !== null) fail("Project-scoped pronunciation cannot carry a scope ID.");
  if (payload.scope !== "project" && payload.scopeId === null) fail("A scoped pronunciation requires its owned scope ID.");
  enumValue(payload.representation, new Set(["provider_neutral", "ipa", "provider_specific"]), "representation");
  safePronunciationText(payload.pronunciation, "pronunciation", SPEECH_AUDITION_LIMITS.maximumPronunciationValueCodePoints);
  nullableSafePronunciationText(payload.ipa, "ipa", SPEECH_AUDITION_LIMITS.maximumPronunciationValueCodePoints);
  nullableBoundedText(payload.providerId, "providerId", 128);
  nullableSafePronunciationText(payload.providerCompiledValue, "providerCompiledValue", 512);
  booleanValue(payload.caseSensitive, "caseSensitive");
  enumValue(payload.matchRule, new Set(["whole_word", "phrase"]), "matchRule");
  integerBetween(payload.priority, -1_000, 1_000, "priority");
  boundedText(payload.reason, "reason", 1_000);
  nullableIdentifier(payload.supersedesEntryId, "supersedesEntryId");
  idempotencyKey(payload.idempotencyKey);
}

function validateSpeechPreviewRequest(raw: unknown, expectedProjectId: string): SpeechPreviewRequest {
  const value = record(raw, "speech preview request");
  exactKeys(value, ["contractVersion", "requestId", "auditionSessionId", "auditionSessionRevision", "auditionScriptId", "auditionScriptFingerprint", "evidence", "normalizedTextSha256", "normalizationPlanFingerprint", "pronunciationPlanFingerprint", "providerControls", "outputFormat", "sampleRateHz", "channels", "idempotencyKey", "requestFingerprint"], "speech preview request");
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) fail("The speech audition contract version was invalid.");
  identifier(value.requestId, "requestId");
  identifier(value.auditionSessionId, "auditionSessionId");
  positiveInteger(value.auditionSessionRevision, "auditionSessionRevision");
  identifier(value.auditionScriptId, "auditionScriptId");
  sha256(value.auditionScriptFingerprint, "auditionScriptFingerprint");
  validateEvidenceBinding(value.evidence, expectedProjectId);
  sha256(value.normalizedTextSha256, "normalizedTextSha256");
  sha256(value.normalizationPlanFingerprint, "normalizationPlanFingerprint");
  sha256(value.pronunciationPlanFingerprint, "pronunciationPlanFingerprint");
  const controls = record(value.providerControls, "providerControls");
  exactKeys(controls, ["speakingRate", "pitch", "style", "energy", "controlsFingerprint"], "providerControls");
  finiteBetween(controls.speakingRate, 0.5, 2, "speakingRate");
  nullableFinite(controls.pitch, "pitch");
  nullableBoundedText(controls.style, "style", 64);
  nullableFinite(controls.energy, "energy");
  sha256(controls.controlsFingerprint, "controlsFingerprint");
  if (value.outputFormat !== "pcm_s16le_wav" || value.sampleRateHz !== 24_000 || value.channels !== 1) fail("The audition output profile was invalid.");
  idempotencyKey(value.idempotencyKey);
  sha256(value.requestFingerprint, "requestFingerprint");
  return raw as SpeechPreviewRequest;
}

function validateProviderRequest(
  raw: unknown,
  expected: SpeechPreviewRequest
): void {
  const value = record(raw, "provider request");
  exactKeys(
    value,
    [
      "contractVersion",
      "providerRequestId",
      "speechPreviewRequestId",
      "providerId",
      "providerVersion",
      "modelId",
      "modelVersion",
      "modelPackageFingerprint",
      "runtimeProfileId",
      "runtimeProfileFingerprint",
      "runtimeInstanceId",
      "voiceProfileId",
      "voiceProfileVersion",
      "voiceRuntimeBindingId",
      "voiceRuntimeBindingFingerprint",
      "providerVoiceId",
      "castAssignmentId",
      "castAssignmentRevision",
      "auditionSessionId",
      "normalizedTextSha256",
      "pronunciationPlanFingerprint",
      "providerControlFingerprint",
      "cacheKey",
      "state",
      "startedAt",
      "finishedAt",
      "retryable",
      "warnings",
      "requestFingerprint",
      "provenance"
    ],
    "provider request"
  );
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) {
    fail("The provider request contract version was invalid.");
  }
  identifier(value.providerRequestId, "providerRequestId");
  identifier(value.speechPreviewRequestId, "speechPreviewRequestId");
  boundedText(value.providerId, "providerId", 128);
  boundedText(value.providerVersion, "providerVersion", 128);
  boundedText(value.modelId, "modelId", 128);
  boundedText(value.modelVersion, "modelVersion", 128);
  sha256(value.modelPackageFingerprint, "modelPackageFingerprint");
  identifier(value.runtimeProfileId, "runtimeProfileId");
  sha256(value.runtimeProfileFingerprint, "runtimeProfileFingerprint");
  nullableIdentifier(value.runtimeInstanceId, "runtimeInstanceId");
  identifier(value.voiceProfileId, "voiceProfileId");
  boundedText(value.voiceProfileVersion, "voiceProfileVersion", 128);
  identifier(value.voiceRuntimeBindingId, "voiceRuntimeBindingId");
  sha256(
    value.voiceRuntimeBindingFingerprint,
    "voiceRuntimeBindingFingerprint"
  );
  boundedText(value.providerVoiceId, "providerVoiceId", 128);
  identifier(value.castAssignmentId, "castAssignmentId");
  positiveInteger(value.castAssignmentRevision, "castAssignmentRevision");
  identifier(value.auditionSessionId, "auditionSessionId");
  sha256(value.normalizedTextSha256, "normalizedTextSha256");
  sha256(value.pronunciationPlanFingerprint, "pronunciationPlanFingerprint");
  sha256(value.providerControlFingerprint, "providerControlFingerprint");
  sha256(value.cacheKey, "cacheKey");
  enumValue(
    value.state,
    new Set(["queued", "running", "succeeded", "failed", "cancelled"]),
    "provider request state"
  );
  if (value.startedAt !== null) timestamp(value.startedAt, "startedAt");
  if (value.finishedAt !== null) timestamp(value.finishedAt, "finishedAt");
  booleanValue(value.retryable, "retryable");
  validateFindingCodes(value.warnings, "provider request warnings");
  sha256(value.requestFingerprint, "requestFingerprint");
  const execution = validateProviderRequestProvenance(value.provenance);

  const evidence = expected.evidence;
  if (
    value.speechPreviewRequestId !== expected.requestId ||
    value.auditionSessionId !== expected.auditionSessionId ||
    value.providerId !== evidence.providerId ||
    value.providerVersion !== evidence.providerVersion ||
    value.modelId !== evidence.modelId ||
    value.modelVersion !== evidence.modelVersion ||
    value.modelPackageFingerprint !== evidence.modelPackageFingerprint ||
    value.runtimeProfileId !== evidence.runtimeProfileId ||
    value.runtimeProfileFingerprint !== evidence.runtimeProfileFingerprint ||
    value.voiceProfileId !== evidence.voiceProfileId ||
    value.voiceProfileVersion !== evidence.voiceProfileVersion ||
    value.voiceRuntimeBindingId !== evidence.voiceRuntimeBindingId ||
    value.voiceRuntimeBindingFingerprint !==
      evidence.voiceRuntimeBindingFingerprint ||
    value.providerVoiceId !== evidence.providerVoiceId ||
    value.castAssignmentId !== evidence.castAssignmentId ||
    value.castAssignmentRevision !== evidence.castAssignmentRevision ||
    value.normalizedTextSha256 !== expected.normalizedTextSha256 ||
    value.pronunciationPlanFingerprint !== expected.pronunciationPlanFingerprint ||
    value.providerControlFingerprint !== expected.providerControls.controlsFingerprint ||
    value.requestFingerprint !== expected.requestFingerprint ||
    record(value.provenance, "provider request provenance").inputFingerprint !==
      expected.requestFingerprint
  ) {
    fail("The provider request did not match the governed audition evidence.");
  }
  if (
    execution.detailKind === "invocation" &&
    (execution.providerVoiceId !== value.providerVoiceId ||
      execution.voiceRuntimeBindingId !== value.voiceRuntimeBindingId ||
      execution.voiceRuntimeBindingFingerprint !==
        value.voiceRuntimeBindingFingerprint)
  ) {
    fail("The provider invocation details did not match the governed binding.");
  }
  if (
    execution.detailKind === "retry" &&
    execution.supersedesProviderRequestId === value.providerRequestId
  ) {
    fail("The provider retry lineage referenced itself.");
  }
  if (
    value.state === "queued" &&
    (value.runtimeInstanceId !== null ||
      value.startedAt !== null ||
      value.finishedAt !== null ||
      execution.executionClassification !== "provider_execution" ||
      execution.providerDispatchCount !== 0)
  ) {
    fail("A queued provider request claimed runtime dispatch evidence.");
  }
  if (
    execution.executionClassification === "provider_execution" &&
    execution.providerDispatchCount === 0 &&
    (value.runtimeInstanceId !== null || value.startedAt !== null)
  ) {
    fail("A provider request claimed runtime identity without a committed provider dispatch.");
  }
  if (
    execution.executionClassification === "provider_execution" &&
    execution.providerDispatchCount === 1 &&
    (value.runtimeInstanceId === null || value.startedAt === null)
  ) {
    fail("A provider dispatch omitted its exact runtime identity.");
  }
  if (
    execution.executionClassification === "provider_execution" &&
    ((execution.providerDispatchCount === 0 &&
      value.state === "succeeded") ||
      (execution.providerDispatchCount === 1 && value.state === "queued"))
  ) {
    fail("The provider request state contradicted its dispatch count.");
  }
  if (
    execution.executionClassification === "verified_cache_lookup" &&
    (value.state === "queued" ||
      value.runtimeInstanceId !== null ||
      value.startedAt === null ||
      execution.sourceProviderRequestId === value.providerRequestId)
  ) {
    fail("The verified cache lookup claimed provider or runtime execution.");
  }
  if (
    (value.state === "queued" || value.state === "running") &&
    value.finishedAt !== null
  ) {
    fail("A non-terminal provider request claimed a completion timestamp.");
  }
  if (
    (value.state === "succeeded" ||
      value.state === "failed" ||
      value.state === "cancelled") &&
    value.finishedAt === null
  ) {
    fail("A terminal provider request omitted its completion timestamp.");
  }
  if (
    value.startedAt !== null &&
    value.finishedAt !== null &&
    Date.parse(value.finishedAt as string) < Date.parse(value.startedAt as string)
  ) {
    fail("The provider request completed before runtime execution began.");
  }
  if (value.state !== "failed" && value.retryable !== false) {
    fail("Only a failed provider request may be marked retryable.");
  }
}

function validateEvidenceBinding(raw: unknown, expectedProjectId: string): void {
  const value = record(raw, "audition evidence");
  const keys = ["projectId", "sourceDocumentId", "sourceRevision", "extractionId", "extractionRevision", "extractedTextSha256", "phase2RunId", "phase2SnapshotId", "phase2SnapshotRevision", "phase2SnapshotFingerprint", "phase2CorrectionSetFingerprint", "castingRunId", "approvedCastSnapshotId", "approvedCastSnapshotRevision", "approvedCastSnapshotFingerprint", "castAssignmentId", "castAssignmentRevision", "voiceProfileId", "voiceProfileVersion", "voiceRuntimeBindingId", "voiceRuntimeBindingFingerprint", "providerVoiceId", "providerId", "providerVersion", "modelId", "modelVersion", "catalogRevisionId", "catalogFingerprint", "rightsRecordId", "rightsRecordRevision", "rightsRecordFingerprint", "pronunciationDictionaryId", "pronunciationDictionaryRevision", "pronunciationDictionaryFingerprint", "runtimeProfileId", "runtimeProfileFingerprint", "modelPackageId", "modelPackageFingerprint", "producerVersion"] as const;
  exactKeys(value, keys, "audition evidence");
  ownedProject(value.projectId, expectedProjectId);
  for (const key of ["sourceDocumentId", "extractionId", "phase2RunId", "phase2SnapshotId", "castingRunId", "approvedCastSnapshotId", "castAssignmentId", "voiceProfileId", "voiceRuntimeBindingId", "catalogRevisionId", "rightsRecordId", "pronunciationDictionaryId", "runtimeProfileId", "modelPackageId"] as const) identifier(value[key], key);
  for (const key of ["sourceRevision", "extractionRevision", "phase2SnapshotRevision", "approvedCastSnapshotRevision", "castAssignmentRevision", "rightsRecordRevision", "pronunciationDictionaryRevision"] as const) positiveInteger(value[key], key);
  for (const key of ["extractedTextSha256", "phase2SnapshotFingerprint", "phase2CorrectionSetFingerprint", "approvedCastSnapshotFingerprint", "voiceRuntimeBindingFingerprint", "catalogFingerprint", "rightsRecordFingerprint", "pronunciationDictionaryFingerprint", "runtimeProfileFingerprint", "modelPackageFingerprint"] as const) sha256(value[key], key);
  for (const key of ["voiceProfileVersion", "providerVoiceId", "providerId", "providerVersion", "modelId", "modelVersion", "producerVersion"] as const) boundedText(value[key], key, 128);
}

function validateWorkspace(
  raw: unknown,
  expectedProjectId: string,
  requestedRoleLimit?: number
): AuditionWorkspaceSnapshot {
  const value = record(raw, "audition workspace");
  exactKeys(value, ["contractVersion", "projectId", "prerequisites", "approvedCastSnapshot", "providers", "runtimeProfiles", "runtimeHealth", "runtimeInstances", "modelInstallations", "modelVerifications", "currentDictionary", "roles", "reviews", "voiceReadinessSnapshot", "updatedAt"], "audition workspace");
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) fail("The speech audition contract version was invalid.");
  ownedProject(value.projectId, expectedProjectId);
  boundedArray(value.prerequisites, "prerequisites", 11).forEach((item) => {
    const prerequisite = record(item, "prerequisite");
    exactKeys(
      prerequisite,
      [
        "prerequisiteId",
        "current",
        "statusCode",
        "evidenceId",
        "evidenceFingerprint"
      ],
      "prerequisite"
    );
    enumValue(
      prerequisite.prerequisiteId,
      new Set([
        "import_review",
        "phase2_story_structure_review",
        "phase2_character_registry_review",
        "phase2_dialogue_attribution_review",
        "phase2_whole_book_analysis_review",
        "phase3a_narrator_casting_review",
        "phase3a_character_casting_review",
        "phase3a_complete_cast_review",
        "approved_cast_snapshot",
        "voice_rights",
        "voice_assignment"
      ]),
      "prerequisiteId"
    );
    boundedCode(prerequisite.statusCode, "prerequisite statusCode");
    nullableIdentifier(prerequisite.evidenceId, "prerequisite evidenceId");
    ownedFingerprintOrNull(prerequisite.evidenceFingerprint, "evidenceFingerprint");
    booleanValue(prerequisite.current, "current");
  });
  if (value.approvedCastSnapshot !== null) {
    const snapshot = record(value.approvedCastSnapshot, "approvedCastSnapshot");
    exactKeys(snapshot, ["snapshotId", "revision", "fingerprint"], "approvedCastSnapshot");
    identifier(snapshot.snapshotId, "snapshotId");
    positiveInteger(snapshot.revision, "revision");
    sha256(snapshot.fingerprint, "fingerprint");
  }
  const providers = boundedArray(value.providers, "providers", 32);
  providers.forEach(validateProvider);
  const providerIds = new Set(
    providers.map((provider) =>
      identifier(record(provider, "speech provider").providerId, "providerId")
    )
  );
  const runtimeProfiles = boundedArray(value.runtimeProfiles, "runtimeProfiles", 32);
  runtimeProfiles.forEach(validateRuntimeProfile);
  const runtimeProfilesById = new Map(
    runtimeProfiles.map((profileValue) => {
      const profile = record(profileValue, "runtime profile");
      return [identifier(profile.runtimeProfileId, "runtimeProfileId"), profile] as const;
    })
  );
  const runtimeInstances = boundedArray(value.runtimeInstances, "runtimeInstances", 200);
  const runtimeInstancesById = new Map<string, Record<string, unknown>>();
  runtimeInstances.forEach((instanceValue) => {
    validateRuntimeInstance(instanceValue);
    const instance = record(instanceValue, "speech runtime instance");
    validateRuntimeProfileReference(
      instance,
      runtimeProfilesById,
      providerIds,
      "runtime instance"
    );
    const runtimeInstanceId = identifier(
      instance.runtimeInstanceId,
      "runtimeInstanceId"
    );
    if (runtimeInstancesById.has(runtimeInstanceId)) {
      fail("The audition workspace contained duplicate runtime instances.");
    }
    runtimeInstancesById.set(runtimeInstanceId, instance);
  });
  const runtimeHealth = boundedArray(value.runtimeHealth, "runtimeHealth", 32);
  runtimeHealth.forEach((healthValue) => {
    validateRuntimeHealth(healthValue);
    const health = record(healthValue, "runtime health");
    validateRuntimeProfileReference(
      health,
      runtimeProfilesById,
      providerIds,
      "runtime health"
    );
    if (health.runtimeInstanceId !== null) {
      const instance = runtimeInstancesById.get(
        identifier(health.runtimeInstanceId, "runtimeInstanceId")
      );
      if (
        instance === undefined ||
        health.runtimeProfileId !== instance.runtimeProfileId ||
        health.runtimeProfileFingerprint !== instance.runtimeProfileFingerprint ||
        health.providerId !== instance.providerId ||
        health.modelPackageFingerprint !== instance.modelPackageFingerprint
      ) {
        fail("The runtime health did not match its emitted runtime instance.");
      }
    }
  });
  const installationsById = new Map<string, Record<string, unknown>>();
  boundedArray(value.modelInstallations, "modelInstallations", 200).forEach(
    (installationValue) => {
      const installation = validateInstallation(installationValue);
      const installationId = identifier(
        installation.installationId,
        "installationId"
      );
      if (installationsById.has(installationId)) {
        fail("The audition workspace contained duplicate model installations.");
      }
      installationsById.set(installationId, installation);
    }
  );
  boundedArray(value.modelVerifications, "modelVerifications", 200).forEach(
    (verificationValue) => {
      const verification = validateVerification(verificationValue);
      const installation = installationsById.get(
        identifier(verification.installationId, "installationId")
      );
      if (
        installation === undefined ||
        verification.modelPackageId !== installation.modelPackageId ||
        verification.manifestFingerprint !== installation.manifestFingerprint
      ) {
        fail("The workspace model verification did not match its installation.");
      }
    }
  );
  if (value.currentDictionary !== null) validateDictionary(value.currentDictionary, expectedProjectId);
  const roles = record(value.roles, "audition role page");
  exactKeysWithOptional(
    roles,
    ["items", "pageSize", "total"],
    ["nextCursor"],
    "audition role page"
  );
  const roleItems = boundedArray(
    roles.items,
    "roles.items",
    requestedRoleLimit ?? SPEECH_AUDITION_LIMITS.defaultPageSize
  );
  integerBetween(
    roles.pageSize,
    0,
    requestedRoleLimit ?? SPEECH_AUDITION_LIMITS.defaultPageSize,
    "roles.pageSize"
  );
  integerBetween(
    roles.total,
    0,
    SPEECH_AUDITION_LIMITS.maximumProductionRoles,
    "roles.total"
  );
  optionalBoundedText(
    roles.nextCursor,
    "roles.nextCursor",
    SPEECH_AUDITION_LIMITS.maximumCursorCodePoints
  );
  if (roles.pageSize !== roleItems.length || roleItems.length > Number(roles.total)) {
    fail("The audition role page metadata did not match its current page.");
  }
  roleItems.forEach((item) => validateRole(item, expectedProjectId));
  boundedArray(value.reviews, "reviews", SPEECH_AUDITION_LIMITS.maximumProductionRoles + 4).forEach((item) => validateReview(item, expectedProjectId));
  if (value.voiceReadinessSnapshot !== null) {
    validateVoiceReadinessSnapshot(value.voiceReadinessSnapshot, expectedProjectId);
  }
  timestamp(value.updatedAt, "updatedAt");
  return raw as AuditionWorkspaceSnapshot;
}

function validateRuntimeProfileReference(
  value: Record<string, unknown>,
  profilesById: ReadonlyMap<string, Record<string, unknown>>,
  providerIds: ReadonlySet<string>,
  label: string
): void {
  const runtimeProfileId = identifier(value.runtimeProfileId, "runtimeProfileId");
  const providerId = identifier(value.providerId, "providerId");
  const profile = profilesById.get(runtimeProfileId);
  if (
    profile === undefined ||
    value.runtimeProfileFingerprint !== profile.profileFingerprint ||
    !boundedArray(profile.providerIds, "runtime profile provider IDs", 32).includes(
      providerId
    ) ||
    !providerIds.has(providerId)
  ) {
    fail(`The ${label} did not match a public runtime profile and provider.`);
  }
}

function validateProvider(raw: unknown): void {
  const value = record(raw, "speech provider");
  exactKeys(
    value,
    [
      "contractVersion",
      "providerId",
      "providerVersion",
      "adapterId",
      "adapterVersion",
      "providerClass",
      "displayName",
      "synthesisImplemented",
      "localOnly",
      "networkRequired",
      "credentialsRequired",
      "deterministic",
      "productionExportEligible",
      "supportedLanguages",
      "outputFormats",
      "supportedSampleRatesHz",
      "licenseIdentifier",
      "commercialUseClassification",
      "attributionRequired",
      "status",
      "statusReasonCode",
      "descriptorFingerprint",
      "provenance"
    ],
    "speech provider"
  );
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION || value.localOnly !== true || value.networkRequired !== false || value.credentialsRequired !== false) fail("The provider descriptor crossed the local-only boundary.");
  enumValue(value.providerClass, new Set(["deterministic_fixture", "real_local", "development_only"]), "providerClass");
  identifier(value.providerId, "providerId");
  boundedCode(value.providerVersion, "providerVersion");
  identifier(value.adapterId, "adapterId");
  boundedCode(value.adapterVersion, "adapterVersion");
  boundedText(value.displayName, "provider displayName", 120);
  booleanValue(value.synthesisImplemented, "synthesisImplemented");
  booleanValue(value.deterministic, "deterministic");
  booleanValue(value.productionExportEligible, "productionExportEligible");
  identifierArray(value.supportedLanguages, "supportedLanguages", 32);
  const outputFormats = boundedArray(value.outputFormats, "outputFormats", 4);
  if (outputFormats.length !== 1 || outputFormats[0] !== "pcm_s16le_wav") {
    fail("The speech provider output formats were invalid.");
  }
  const sampleRates = boundedArray(
    value.supportedSampleRatesHz,
    "supportedSampleRatesHz",
    16
  );
  if (
    sampleRates.length === 0 ||
    sampleRates.some(
      (sampleRate) =>
        !Number.isSafeInteger(sampleRate) ||
        (sampleRate as number) < 8_000 ||
        (sampleRate as number) > 192_000
    )
  ) {
    fail("The speech provider sample rates were invalid.");
  }
  boundedText(value.licenseIdentifier, "licenseIdentifier", 128);
  enumValue(
    value.commercialUseClassification,
    new Set(["allowed", "restricted", "fixture_only", "unknown"]),
    "commercialUseClassification"
  );
  booleanValue(value.attributionRequired, "attributionRequired");
  enumValue(
    value.status,
    new Set(["available", "degraded", "unavailable", "disabled"]),
    "provider status"
  );
  nullableBoundedText(value.statusReasonCode, "statusReasonCode", 128);
  sha256(value.descriptorFingerprint, "descriptorFingerprint");
  validateProvenance(value.provenance, "provider provenance");
}

function validateRuntimeProfile(raw: unknown): void {
  const value = record(raw, "runtime profile");
  exactKeys(
    value,
    [
      "contractVersion",
      "runtimeProfileId",
      "revision",
      "runtimeDescriptorId",
      "providerIds",
      "compatibleModelPackageIds",
      "protocolVersion",
      "startupDeadlineMilliseconds",
      "requestDeadlineMilliseconds",
      "idleShutdownMilliseconds",
      "maximumRetryAttempts",
      "maximumConcurrentRequests",
      "shellUsed",
      "networkAccessDuringSynthesis",
      "profileFingerprint",
      "active",
      "provenance"
    ],
    "runtime profile"
  );
  contractVersion(value.contractVersion, "runtime profile");
  identifier(value.runtimeProfileId, "runtimeProfileId");
  positiveInteger(value.revision, "runtime profile revision");
  identifier(value.runtimeDescriptorId, "runtimeDescriptorId");
  identifierArray(value.providerIds, "runtime profile providerIds", 32);
  identifierArray(
    value.compatibleModelPackageIds,
    "compatibleModelPackageIds",
    200
  );
  sha256(value.profileFingerprint, "profileFingerprint");
  if (value.shellUsed !== false || value.networkAccessDuringSynthesis !== false || value.protocolVersion !== "1.0.0") fail("The runtime profile crossed its process or network boundary.");
  integerBetween(value.startupDeadlineMilliseconds, 1, 120_000, "startupDeadlineMilliseconds");
  integerBetween(value.requestDeadlineMilliseconds, 1, 300_000, "requestDeadlineMilliseconds");
  integerBetween(value.idleShutdownMilliseconds, 1, 3_600_000, "idleShutdownMilliseconds");
  integerBetween(value.maximumRetryAttempts, 0, 3, "maximumRetryAttempts");
  integerBetween(value.maximumConcurrentRequests, 1, 32, "maximumConcurrentRequests");
  booleanValue(value.active, "runtime profile active");
  validateProvenance(value.provenance, "runtime profile provenance");
}

function validateRuntimeHealth(raw: unknown): void {
  const value = record(raw, "runtime health");
  exactKeys(
    value,
    [
      "contractVersion",
      "runtimeProfileId",
      "runtimeProfileFingerprint",
      "runtimeInstanceId",
      "providerId",
      "status",
      "reasonCode",
      "checkedAt",
      "expiresAt",
      "modelPackageFingerprint",
      "protocolVersion"
    ],
    "runtime health"
  );
  contractVersion(value.contractVersion, "runtime health");
  identifier(value.runtimeProfileId, "runtimeProfileId");
  sha256(value.runtimeProfileFingerprint, "runtimeProfileFingerprint");
  nullableIdentifier(value.runtimeInstanceId, "runtimeInstanceId");
  identifier(value.providerId, "providerId");
  enumValue(value.status, new Set(["available", "degraded", "unavailable", "disabled"]), "status");
  boundedCode(value.reasonCode, "runtime health reasonCode");
  timestamp(value.checkedAt, "checkedAt");
  timestamp(value.expiresAt, "expiresAt");
  ownedFingerprintOrNull(value.modelPackageFingerprint, "modelPackageFingerprint");
  if (value.protocolVersion !== "1.0.0") {
    fail("The runtime health protocol version was invalid.");
  }
}

function validateRuntimeInstance(raw: unknown): void {
  const value = record(raw, "speech runtime instance");
  exactKeys(
    value,
    [
      "contractVersion",
      "runtimeInstanceId",
      "runtimeProfileId",
      "runtimeProfileFingerprint",
      "providerId",
      "modelPackageFingerprint",
      "workerPid",
      "parentPid",
      "executableIdentity",
      "executableSha256",
      "creationIdentity",
      "protocolVersion",
      "handshakeAuthenticated",
      "state",
      "startedAt",
      "lastActivityAt",
      "stoppedAt",
      "stopReasonCode",
      "shutdownAcknowledged",
      "gracefulShutdownConfirmed",
      "exitCode",
      "terminatedByParent",
      "ownershipConfirmed",
      "confirmedExited",
      "ownedProcessesConfirmedExited",
      "jobObjectAssigned",
      "deniedNetworkAttemptCount",
      "networkPolicy",
      "observedNetworkRequestCount",
      "restartReconciliation",
      "provenance"
    ],
    "speech runtime instance"
  );
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) {
    fail("The runtime instance contract version was invalid.");
  }
  identifier(value.runtimeInstanceId, "runtimeInstanceId");
  identifier(value.runtimeProfileId, "runtimeProfileId");
  sha256(value.runtimeProfileFingerprint, "runtimeProfileFingerprint");
  boundedText(value.providerId, "providerId", 128);
  sha256(value.modelPackageFingerprint, "modelPackageFingerprint");
  integerBetween(value.workerPid, 1, 4_294_967_295, "workerPid");
  integerBetween(value.parentPid, 1, 4_294_967_295, "parentPid");
  if (value.workerPid === value.parentPid) {
    fail("The runtime worker and parent process identities were invalid.");
  }
  const executableIdentity = boundedText(
    value.executableIdentity,
    "executableIdentity",
    200
  );
  if (/[\\/]/u.test(executableIdentity)) {
    fail("The runtime executable identity exposed a path.");
  }
  sha256(value.executableSha256, "executableSha256");
  sha256(value.creationIdentity, "creationIdentity");
  if (
    value.protocolVersion !== "1.0.0" ||
    value.handshakeAuthenticated !== true
  ) {
    fail("The runtime instance handshake was invalid.");
  }
  enumValue(
    value.state,
    new Set(["starting", "ready", "busy", "idle", "stopping", "stopped", "failed"]),
    "runtime state"
  );
  timestamp(value.startedAt, "startedAt");
  timestamp(value.lastActivityAt, "lastActivityAt");
  if (value.stoppedAt !== null) timestamp(value.stoppedAt, "stoppedAt");
  nullableBoundedText(value.stopReasonCode, "stopReasonCode", 128);
  for (const key of [
    "shutdownAcknowledged",
    "gracefulShutdownConfirmed",
    "terminatedByParent",
    "ownershipConfirmed",
    "confirmedExited",
    "ownedProcessesConfirmedExited"
  ] as const) {
    if (value[key] !== null && typeof value[key] !== "boolean") {
      fail(`The runtime ${key} evidence was invalid.`);
    }
  }
  if (
    value.exitCode !== null &&
    (!Number.isSafeInteger(value.exitCode) ||
      Number(value.exitCode) < -2_147_483_648 ||
      Number(value.exitCode) > 4_294_967_295)
  ) {
    fail("The runtime exitCode evidence was invalid.");
  }
  if (typeof value.jobObjectAssigned !== "boolean") {
    fail("The runtime job-object evidence was invalid.");
  }
  integerBetween(
    value.deniedNetworkAttemptCount,
    0,
    1_000_000,
    "deniedNetworkAttemptCount"
  );
  const terminalFields = [
    value.stopReasonCode,
    value.shutdownAcknowledged,
    value.gracefulShutdownConfirmed,
    value.exitCode,
    value.terminatedByParent,
    value.ownershipConfirmed,
    value.confirmedExited,
    value.ownedProcessesConfirmedExited
  ];
  const allTerminalFieldsNull = terminalFields.every((item) => item === null);
  const allTerminalFieldsPresent = terminalFields.every((item) => item !== null);
  const reconciliation = validateRuntimeRestartReconciliation(
    value.restartReconciliation
  );
  const failedWithUnknownExit =
    value.state === "failed" &&
    value.stoppedAt === null &&
    allTerminalFieldsNull &&
    reconciliation !== null;
  const failedWithObservedExit =
    value.state === "failed" &&
    value.stoppedAt !== null &&
    allTerminalFieldsPresent &&
    reconciliation === null;
  const stoppedWithObservedExit =
    value.state === "stopped" &&
    value.stoppedAt !== null &&
    allTerminalFieldsPresent &&
    reconciliation === null;
  const activeWithoutTerminalEvidence =
    value.state !== "stopped" &&
    value.state !== "failed" &&
    value.stoppedAt === null &&
    allTerminalFieldsNull &&
    reconciliation === null;
  if (
    !failedWithUnknownExit &&
    !failedWithObservedExit &&
    !stoppedWithObservedExit &&
    !activeWithoutTerminalEvidence
  ) {
    fail("The runtime terminal evidence was incomplete or premature.");
  }
  if (
    value.state === "stopped" &&
    ((value.stopReasonCode !== "clean" && value.stopReasonCode !== "idle") ||
      value.shutdownAcknowledged !== true ||
      value.gracefulShutdownConfirmed !== true ||
      value.exitCode !== 0 ||
      value.terminatedByParent !== false ||
      value.ownershipConfirmed !== true ||
      value.confirmedExited !== true ||
      value.ownedProcessesConfirmedExited !== true)
  ) {
    fail("The stopped runtime lacked authenticated graceful-exit evidence.");
  }
  if (
    failedWithObservedExit &&
    (value.gracefulShutdownConfirmed !== false ||
      value.stopReasonCode === "clean" ||
      value.stopReasonCode === "idle")
  ) {
    fail("The failed runtime exposed contradictory graceful-exit evidence.");
  }
  if (
    value.gracefulShutdownConfirmed === true &&
    value.state !== "stopped"
  ) {
    fail("Only a stopped runtime may report graceful shutdown.");
  }
  if (value.networkPolicy !== "python_socket_api_denied") {
    fail("The runtime instance network policy was invalid.");
  }
  if (value.observedNetworkRequestCount !== null) {
    integerBetween(
      value.observedNetworkRequestCount,
      0,
      1_000_000,
      "observedNetworkRequestCount"
    );
  }
  validateProvenance(value.provenance, "runtime provenance");
}

function validateRuntimeRestartReconciliation(
  raw: unknown
): Record<string, unknown> | null {
  if (raw === null) return null;
  const value = record(raw, "runtime restart reconciliation");
  exactKeys(
    value,
    [
      "contractVersion",
      "reasonCode",
      "priorState",
      "observedAt",
      "observerServiceInstanceId",
      "ownershipConfirmed",
      "gracefulShutdownConfirmed",
      "processExitConfirmed"
    ],
    "runtime restart reconciliation"
  );
  contractVersion(value.contractVersion, "runtime restart reconciliation");
  if (value.reasonCode !== "SERVICE_RESTART_INTERRUPTED") {
    fail("The runtime restart reconciliation reason was invalid.");
  }
  enumValue(
    value.priorState,
    new Set(["starting", "ready", "busy", "idle", "stopping"]),
    "runtime restart priorState"
  );
  timestamp(value.observedAt, "runtime restart observedAt");
  identifier(
    value.observerServiceInstanceId,
    "runtime restart observerServiceInstanceId"
  );
  if (
    value.ownershipConfirmed !== false ||
    value.gracefulShutdownConfirmed !== false ||
    value.processExitConfirmed !== false
  ) {
    fail("The runtime restart reconciliation overstated process-exit proof.");
  }
  return value;
}

function validateModelManifest(raw: unknown): Record<string, unknown> {
  const value = record(raw, "model manifest");
  exactKeys(
    value,
    [
      "contractVersion",
      "manifestVersion",
      "modelPackageId",
      "providerId",
      "modelId",
      "modelVersion",
      "runtimeVersion",
      "platform",
      "architecture",
      "sourceClassification",
      "officialSourceReference",
      "licenseIdentifier",
      "commercialUseClassification",
      "attributionRequirements",
      "files",
      "totalExpandedByteSize",
      "requiredRuntimeDependencies",
      "compatibilityConstraints",
      "state",
      "manifestFingerprint",
      "provenance"
    ],
    "model manifest"
  );
  contractVersion(value.contractVersion, "model manifest");
  boundedCode(value.manifestVersion, "manifestVersion");
  identifier(value.modelPackageId, "modelPackageId");
  boundedCode(value.providerId, "model providerId");
  const modelId = boundedText(value.modelId, "modelId", 128);
  if (MARKUP_OR_CONTROL.test(modelId)) {
    fail("The modelId contained markup or control text.");
  }
  boundedCode(value.modelVersion, "modelVersion");
  boundedCode(value.runtimeVersion, "runtimeVersion");
  if (
    value.platform !== "windows" ||
    (value.architecture !== "x64" && value.architecture !== "arm64")
  ) {
    fail("The model manifest platform was invalid.");
  }
  enumValue(
    value.sourceClassification,
    new Set([
      "official_release",
      "maintainer_referenced_conversion",
      "repository_fixture"
    ]),
    "sourceClassification"
  );
  sha256(value.manifestFingerprint, "manifestFingerprint");
  boundedText(value.officialSourceReference, "officialSourceReference", 512);
  boundedText(value.licenseIdentifier, "licenseIdentifier", 128);
  enumValue(
    value.commercialUseClassification,
    new Set(["allowed", "restricted", "fixture_only", "unknown"]),
    "commercialUseClassification"
  );
  boundedTextArray(
    value.attributionRequirements,
    "attributionRequirements",
    32,
    512
  );
  const files = boundedArray(value.files, "files", SPEECH_AUDITION_LIMITS.maximumModelFiles);
  if (files.length === 0) fail("A model manifest requires a verified file inventory.");
  for (const fileValue of files) {
    const file = record(fileValue, "model file");
    exactKeys(
      file,
      ["relativePath", "byteSize", "sha256", "mediaClassification", "executable"],
      "model file"
    );
    const relativePath = boundedText(file.relativePath, "relativePath", SPEECH_AUDITION_LIMITS.maximumModelFileRelativePathCodePoints);
    if (/^(?:[A-Za-z]:|[/\\])|(?:^|[/\\])\.\.(?:[/\\]|$)|\0/u.test(relativePath)) fail("The model manifest contained an unsafe relative path.");
    integerBetween(file.byteSize, 1, 2_147_483_648, "byteSize");
    sha256(file.sha256, "sha256");
    enumValue(
      file.mediaClassification,
      new Set([
        "onnx",
        "safetensors",
        "configuration",
        "tokenizer",
        "voice_data",
        "license",
        "notice"
      ]),
      "mediaClassification"
    );
    if (file.executable !== false) fail("Model package files cannot be executable.");
  }
  integerBetween(
    value.totalExpandedByteSize,
    1,
    2_147_483_648,
    "totalExpandedByteSize"
  );
  boundedTextArray(
    value.requiredRuntimeDependencies,
    "requiredRuntimeDependencies",
    64,
    256
  );
  boundedTextArray(
    value.compatibilityConstraints,
    "compatibilityConstraints",
    64,
    512
  );
  enumValue(value.state, new Set(["active", "deprecated", "revoked"]), "model state");
  validateProvenance(value.provenance, "model manifest provenance");
  return value;
}

function validateInstallation(raw: unknown): Record<string, unknown> {
  const value = record(raw, "model installation");
  exactKeys(
    value,
    [
      "contractVersion",
      "installationId",
      "modelPackageId",
      "manifestFingerprint",
      "installationRevision",
      "storageKey",
      "status",
      "active",
      "installedAt",
      "updatedAt",
      "lastAction",
      "actionReasonCode",
      "immutableEventId",
      "provenance"
    ],
    "model installation"
  );
  contractVersion(value.contractVersion, "model installation");
  identifier(value.installationId, "installationId");
  identifier(value.modelPackageId, "modelPackageId");
  sha256(value.manifestFingerprint, "manifestFingerprint");
  positiveInteger(value.installationRevision, "installationRevision");
  const storageKey = identifier(value.storageKey, "storageKey");
  if (/[\\/:]/u.test(storageKey)) fail("Model installation storage keys must not reveal paths.");
  enumValue(
    value.status,
    new Set([
      "pending",
      "installed",
      "active",
      "inactive",
      "repair_required",
      "removed",
      "failed"
    ]),
    "installation status"
  );
  booleanValue(value.active, "installation active");
  if (value.active !== (value.status === "active")) {
    fail("The model installation active flag contradicted its status.");
  }
  if (value.installedAt !== null) timestamp(value.installedAt, "installedAt");
  timestamp(value.updatedAt, "updatedAt");
  enumValue(value.lastAction, MODEL_ACTIONS, "lastAction");
  const actionReason = boundedText(
    value.actionReasonCode,
    "actionReasonCode",
    1_000
  );
  if (MARKUP_OR_CONTROL.test(actionReason)) {
    fail("The model installation action reason contained markup or control text.");
  }
  identifier(value.immutableEventId, "immutableEventId");
  validateProvenance(value.provenance, "model installation provenance");
  return value;
}

function validateVerification(raw: unknown): Record<string, unknown> {
  const value = record(raw, "model verification");
  exactKeys(
    value,
    [
      "contractVersion",
      "verificationId",
      "installationId",
      "modelPackageId",
      "manifestFingerprint",
      "verificationFingerprint",
      "status",
      "verifiedFileCount",
      "verifiedByteSize",
      "unexpectedFileCount",
      "symlinkOrReparsePointDetected",
      "checkedAt",
      "blockingReasonCodes",
      "provenance"
    ],
    "model verification"
  );
  contractVersion(value.contractVersion, "model verification");
  identifier(value.verificationId, "verificationId");
  identifier(value.installationId, "installationId");
  identifier(value.modelPackageId, "modelPackageId");
  sha256(value.manifestFingerprint, "manifestFingerprint");
  sha256(value.verificationFingerprint, "verificationFingerprint");
  enumValue(
    value.status,
    new Set(["verified", "mismatch", "missing", "unsafe"]),
    "verification status"
  );
  const verifiedFileCount = integerBetween(value.verifiedFileCount, 0, SPEECH_AUDITION_LIMITS.maximumModelFiles, "verifiedFileCount");
  const verifiedByteSize = integerBetween(value.verifiedByteSize, 0, 2_147_483_648, "verifiedByteSize");
  const unexpectedFileCount = integerBetween(value.unexpectedFileCount, 0, SPEECH_AUDITION_LIMITS.maximumModelFiles, "unexpectedFileCount");
  booleanValue(value.symlinkOrReparsePointDetected, "symlinkOrReparsePointDetected");
  timestamp(value.checkedAt, "checkedAt");
  const blockingReasonCodes = identifierArray(
    value.blockingReasonCodes,
    "blockingReasonCodes",
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  if (
    value.status === "verified" &&
    (verifiedFileCount === 0 ||
      verifiedByteSize === 0 ||
      unexpectedFileCount !== 0 ||
      value.symlinkOrReparsePointDetected !== false ||
      blockingReasonCodes.length !== 0)
  ) {
    fail("The verified model record contained blocking or incomplete evidence.");
  }
  validateProvenance(value.provenance, "model verification provenance");
  return value;
}

function validateDictionary(raw: unknown, expectedProjectId: string): PronunciationDictionary {
  const value = record(raw, "pronunciation dictionary");
  exactKeys(
    value,
    [
      "contractVersion",
      "dictionaryId",
      "projectId",
      "revision",
      "entryCount",
      "currentEntryCount",
      "dictionaryFingerprint",
      "createdAt",
      "updatedAt",
      "provenance"
    ],
    "pronunciation dictionary"
  );
  contractVersion(value.contractVersion, "pronunciation dictionary");
  ownedProject(value.projectId, expectedProjectId);
  identifier(value.dictionaryId, "dictionaryId");
  positiveInteger(value.revision, "revision");
  integerBetween(value.entryCount, 0, SPEECH_AUDITION_LIMITS.maximumPronunciationEntries, "entryCount");
  integerBetween(value.currentEntryCount, 0, SPEECH_AUDITION_LIMITS.maximumPronunciationEntries, "currentEntryCount");
  if (Number(value.currentEntryCount) > Number(value.entryCount)) {
    fail("The pronunciation dictionary current count exceeded its history.");
  }
  sha256(value.dictionaryFingerprint, "dictionaryFingerprint");
  timestamp(value.createdAt, "createdAt");
  timestamp(value.updatedAt, "updatedAt");
  validateProvenance(value.provenance, "pronunciation dictionary provenance");
  return raw as PronunciationDictionary;
}

function validatePronunciationEntry(raw: unknown, expectedProjectId: string, dictionary: PronunciationDictionary): PronunciationEntry {
  const value = record(raw, "pronunciation entry");
  exactKeys(
    value,
    [
      "contractVersion",
      "entryId",
      "projectId",
      "dictionaryId",
      "dictionaryRevision",
      "revision",
      "writtenForm",
      "normalizedLookupForm",
      "language",
      "locale",
      "scope",
      "scopeId",
      "representation",
      "pronunciation",
      "ipa",
      "providerId",
      "providerCompiledValue",
      "caseSensitive",
      "matchRule",
      "priority",
      "actor",
      "reason",
      "verificationState",
      "entryFingerprint",
      "supersedesEntryId",
      "supersededByEntryId",
      "immutable",
      "provenance"
    ],
    "pronunciation entry"
  );
  contractVersion(value.contractVersion, "pronunciation entry");
  ownedProject(value.projectId, expectedProjectId);
  identifier(value.entryId, "entryId");
  if (value.dictionaryId !== dictionary.dictionaryId) fail("The pronunciation entry did not belong to the returned dictionary.");
  positiveInteger(value.dictionaryRevision, "dictionaryRevision");
  if (Number(value.dictionaryRevision) > dictionary.revision) {
    fail("The pronunciation entry claimed a future dictionary revision.");
  }
  positiveInteger(value.revision, "pronunciation entry revision");
  safePronunciationText(value.writtenForm, "writtenForm", SPEECH_AUDITION_LIMITS.maximumWrittenFormCodePoints);
  safePronunciationText(
    value.normalizedLookupForm,
    "normalizedLookupForm",
    SPEECH_AUDITION_LIMITS.maximumWrittenFormCodePoints
  );
  boundedCode(value.language, "pronunciation language");
  nullableBoundedText(value.locale, "pronunciation locale", 32);
  safePronunciationText(value.pronunciation, "pronunciation", SPEECH_AUDITION_LIMITS.maximumPronunciationValueCodePoints);
  nullableSafePronunciationText(value.ipa, "ipa", SPEECH_AUDITION_LIMITS.maximumPronunciationValueCodePoints);
  nullableSafePronunciationText(value.providerCompiledValue, "providerCompiledValue", 512);
  enumValue(value.scope, SCOPES, "scope");
  nullableIdentifier(value.scopeId, "scopeId");
  enumValue(
    value.representation,
    new Set(["provider_neutral", "ipa", "provider_specific"]),
    "pronunciation representation"
  );
  nullableBoundedText(value.providerId, "pronunciation providerId", 128);
  booleanValue(value.caseSensitive, "caseSensitive");
  enumValue(value.matchRule, new Set(["whole_word", "phrase"]), "matchRule");
  integerBetween(value.priority, -1_000, 1_000, "priority");
  const actor = record(value.actor, "pronunciation actor");
  exactKeys(actor, ["classification", "actorId"], "pronunciation actor");
  if (actor.classification !== "human") {
    fail("Pronunciation entries require a human actor.");
  }
  identifier(actor.actorId, "actorId");
  boundedText(
    value.reason,
    "pronunciation reason",
    SPEECH_AUDITION_LIMITS.maximumReviewRationaleCodePoints
  );
  enumValue(
    value.verificationState,
    new Set([
      "pending",
      "approved",
      "changes_requested",
      "rejected",
      "superseded"
    ]),
    "verificationState"
  );
  sha256(value.entryFingerprint, "entryFingerprint");
  nullableIdentifier(value.supersedesEntryId, "supersedesEntryId");
  nullableIdentifier(value.supersededByEntryId, "supersededByEntryId");
  if (value.immutable !== true) fail("Pronunciation entries must be immutable.");
  validateProvenance(value.provenance, "pronunciation entry provenance");
  return raw as PronunciationEntry;
}

function validateVoiceRuntimeBinding(raw: unknown): Record<string, unknown> {
  const value = record(raw, "voice runtime binding");
  exactKeys(
    value,
    [
      "contractVersion",
      "bindingId",
      "bindingKind",
      "voiceProfileId",
      "voiceProfileVersion",
      "voiceProfileFingerprint",
      "sourceProviderId",
      "sourceProviderVersion",
      "sourceProviderFingerprint",
      "sourceModelId",
      "sourceModelVersion",
      "sourceModelFingerprint",
      "providerId",
      "providerVersion",
      "providerVoiceId",
      "modelId",
      "modelVersion",
      "modelPackageId",
      "modelPackageFingerprint",
      "runtimeProfileId",
      "runtimeProfileFingerprint",
      "bindingFingerprint",
      "active",
      "provenance",
      "createdAt"
    ],
    "voice runtime binding"
  );
  contractVersion(value.contractVersion, "voice runtime binding");
  identifier(value.bindingId, "bindingId");
  enumValue(
    value.bindingKind,
    new Set(["exact_provider_match", "declared_fixture_adapter"]),
    "voice runtime binding kind"
  );
  identifier(value.voiceProfileId, "voiceProfileId");
  boundedCode(value.voiceProfileVersion, "voiceProfileVersion");
  sha256(value.voiceProfileFingerprint, "voiceProfileFingerprint");
  boundedCode(value.sourceProviderId, "sourceProviderId");
  boundedCode(value.sourceProviderVersion, "sourceProviderVersion");
  sha256(value.sourceProviderFingerprint, "sourceProviderFingerprint");
  boundedCode(value.sourceModelId, "sourceModelId");
  boundedCode(value.sourceModelVersion, "sourceModelVersion");
  sha256(value.sourceModelFingerprint, "sourceModelFingerprint");
  boundedCode(value.providerId, "providerId");
  boundedCode(value.providerVersion, "providerVersion");
  boundedCode(value.providerVoiceId, "providerVoiceId");
  boundedCode(value.modelId, "modelId");
  boundedCode(value.modelVersion, "modelVersion");
  identifier(value.modelPackageId, "modelPackageId");
  sha256(value.modelPackageFingerprint, "modelPackageFingerprint");
  identifier(value.runtimeProfileId, "runtimeProfileId");
  sha256(value.runtimeProfileFingerprint, "runtimeProfileFingerprint");
  sha256(value.bindingFingerprint, "bindingFingerprint");
  booleanValue(value.active, "voice runtime binding active");
  validateProvenance(value.provenance, "voice runtime binding provenance");
  timestamp(value.createdAt, "voice runtime binding createdAt");
  return value;
}

function validateSession(raw: unknown, expectedProjectId: string, expectedRoleId?: string): AuditionSession {
  const value = record(raw, "audition session");
  exactKeys(
    value,
    [
      "contractVersion",
      "auditionSessionId",
      "projectId",
      "roleId",
      "castAssignmentId",
      "castAssignmentRevision",
      "approvedCastSnapshotId",
      "approvedCastSnapshotRevision",
      "approvedCastSnapshotFingerprint",
      "voiceRuntimeBindingId",
      "voiceRuntimeBindingFingerprint",
      "providerVoiceId",
      "voiceRuntimeBinding",
      "providerId",
      "providerVersion",
      "modelPackageFingerprint",
      "runtimeProfileFingerprint",
      "pronunciationDictionaryRevision",
      "pronunciationDictionaryFingerprint",
      "state",
      "revision",
      "scriptCount",
      "clipCount",
      "approvedClipId",
      "jobId",
      "sessionFingerprint",
      "createdAt",
      "updatedAt",
      "provenance"
    ],
    "audition session"
  );
  contractVersion(value.contractVersion, "audition session");
  ownedProject(value.projectId, expectedProjectId);
  identifier(value.auditionSessionId, "auditionSessionId");
  const roleId = identifier(value.roleId, "roleId");
  if (expectedRoleId !== undefined && roleId !== expectedRoleId) fail("The audition session did not match the requested role.");
  identifier(value.castAssignmentId, "castAssignmentId");
  positiveInteger(value.castAssignmentRevision, "castAssignmentRevision");
  identifier(value.approvedCastSnapshotId, "approvedCastSnapshotId");
  positiveInteger(
    value.approvedCastSnapshotRevision,
    "approvedCastSnapshotRevision"
  );
  sha256(
    value.approvedCastSnapshotFingerprint,
    "approvedCastSnapshotFingerprint"
  );
  const voiceRuntimeBindingId = identifier(
    value.voiceRuntimeBindingId,
    "voiceRuntimeBindingId"
  );
  const voiceRuntimeBindingFingerprint = sha256(
    value.voiceRuntimeBindingFingerprint,
    "voiceRuntimeBindingFingerprint"
  );
  const providerVoiceId = boundedText(
    value.providerVoiceId,
    "providerVoiceId",
    128
  );
  const binding = validateVoiceRuntimeBinding(value.voiceRuntimeBinding);
  boundedCode(value.providerId, "providerId");
  boundedCode(value.providerVersion, "providerVersion");
  sha256(value.modelPackageFingerprint, "modelPackageFingerprint");
  sha256(value.runtimeProfileFingerprint, "runtimeProfileFingerprint");
  positiveInteger(
    value.pronunciationDictionaryRevision,
    "pronunciationDictionaryRevision"
  );
  sha256(
    value.pronunciationDictionaryFingerprint,
    "pronunciationDictionaryFingerprint"
  );
  enumValue(
    value.state,
    new Set([
      "draft",
      "queued",
      "generating",
      "reviewable",
      "failed",
      "cancelled",
      "invalidated"
    ]),
    "audition session state"
  );
  positiveInteger(value.revision, "revision");
  integerBetween(value.scriptCount, 0, SPEECH_AUDITION_LIMITS.maximumScriptsPerSession, "scriptCount");
  integerBetween(value.clipCount, 0, SPEECH_AUDITION_LIMITS.maximumAuditionMetadataRecords, "clipCount");
  nullableIdentifier(value.approvedClipId, "approvedClipId");
  nullableIdentifier(value.jobId, "jobId");
  sha256(value.sessionFingerprint, "sessionFingerprint");
  timestamp(value.createdAt, "createdAt");
  timestamp(value.updatedAt, "updatedAt");
  validateProvenance(value.provenance, "audition session provenance");
  if (
    binding.bindingId !== voiceRuntimeBindingId ||
    binding.bindingFingerprint !== voiceRuntimeBindingFingerprint ||
    binding.providerVoiceId !== providerVoiceId ||
    binding.providerId !== value.providerId ||
    binding.providerVersion !== value.providerVersion ||
    binding.modelPackageFingerprint !== value.modelPackageFingerprint ||
    binding.runtimeProfileFingerprint !== value.runtimeProfileFingerprint ||
    binding.active !== true
  ) {
    fail("The audition session voice runtime binding was inconsistent.");
  }
  return raw as AuditionSession;
}

function assertSessionMatchesEvidence(
  session: AuditionSession,
  evidence: AuditionEvidenceBinding,
  label: string
): void {
  const binding = session.voiceRuntimeBinding;
  if (
    session.projectId !== evidence.projectId ||
    session.castAssignmentId !== evidence.castAssignmentId ||
    session.castAssignmentRevision !== evidence.castAssignmentRevision ||
    session.approvedCastSnapshotId !== evidence.approvedCastSnapshotId ||
    session.approvedCastSnapshotRevision !==
      evidence.approvedCastSnapshotRevision ||
    session.approvedCastSnapshotFingerprint !==
      evidence.approvedCastSnapshotFingerprint ||
    session.voiceRuntimeBindingId !== evidence.voiceRuntimeBindingId ||
    session.voiceRuntimeBindingFingerprint !==
      evidence.voiceRuntimeBindingFingerprint ||
    session.providerVoiceId !== evidence.providerVoiceId ||
    session.providerId !== evidence.providerId ||
    session.providerVersion !== evidence.providerVersion ||
    session.modelPackageFingerprint !== evidence.modelPackageFingerprint ||
    session.runtimeProfileFingerprint !== evidence.runtimeProfileFingerprint ||
    session.pronunciationDictionaryRevision !==
      evidence.pronunciationDictionaryRevision ||
    session.pronunciationDictionaryFingerprint !==
      evidence.pronunciationDictionaryFingerprint ||
    binding.voiceProfileId !== evidence.voiceProfileId ||
    binding.voiceProfileVersion !== evidence.voiceProfileVersion ||
    binding.bindingId !== evidence.voiceRuntimeBindingId ||
    binding.bindingFingerprint !== evidence.voiceRuntimeBindingFingerprint ||
    binding.providerVoiceId !== evidence.providerVoiceId ||
    binding.providerId !== evidence.providerId ||
    binding.providerVersion !== evidence.providerVersion ||
    binding.modelId !== evidence.modelId ||
    binding.modelVersion !== evidence.modelVersion ||
    binding.modelPackageId !== evidence.modelPackageId ||
    binding.modelPackageFingerprint !== evidence.modelPackageFingerprint ||
    binding.runtimeProfileId !== evidence.runtimeProfileId ||
    binding.runtimeProfileFingerprint !== evidence.runtimeProfileFingerprint
  ) {
    fail(`The ${label} did not match the exact governed evidence.`);
  }
}

function validateClip(raw: unknown, expectedProjectId: string, expectedSessionId?: string, expectedRoleId?: string): AuditionClip {
  const value = record(raw, "audition clip");
  exactKeys(
    value,
    [
      "contractVersion",
      "auditionClipId",
      "projectId",
      "auditionSessionId",
      "auditionScriptId",
      "roleId",
      "castAssignmentId",
      "castAssignmentRevision",
      "providerRequestId",
      "providerId",
      "providerVersion",
      "voiceRuntimeBindingId",
      "voiceRuntimeBindingFingerprint",
      "providerVoiceId",
      "providerClass",
      "modelId",
      "modelVersion",
      "modelPackageFingerprint",
      "runtimeProfileFingerprint",
      "normalizedTextSha256",
      "pronunciationPlanFingerprint",
      "providerControlFingerprint",
      "cacheKey",
      "cacheStatus",
      "cacheProof",
      "audioArtifact",
      "audioQuality",
      "state",
      "clipFingerprint",
      "revision",
      "createdAt",
      "provenance"
    ],
    "audition clip"
  );
  contractVersion(value.contractVersion, "audition clip");
  ownedProject(value.projectId, expectedProjectId);
  identifier(value.auditionClipId, "auditionClipId");
  const auditionSessionId = identifier(
    value.auditionSessionId,
    "auditionSessionId"
  );
  if (expectedSessionId !== undefined && auditionSessionId !== expectedSessionId) fail("The clip did not match the requested session.");
  identifier(value.auditionScriptId, "auditionScriptId");
  const roleId = identifier(value.roleId, "roleId");
  if (expectedRoleId !== undefined && roleId !== expectedRoleId) fail("The clip did not match the requested role.");
  identifier(value.castAssignmentId, "castAssignmentId");
  positiveInteger(value.castAssignmentRevision, "castAssignmentRevision");
  identifier(value.providerRequestId, "providerRequestId");
  boundedCode(value.providerId, "providerId");
  boundedCode(value.providerVersion, "providerVersion");
  const voiceRuntimeBindingId = identifier(
    value.voiceRuntimeBindingId,
    "voiceRuntimeBindingId"
  );
  const voiceRuntimeBindingFingerprint = sha256(
    value.voiceRuntimeBindingFingerprint,
    "voiceRuntimeBindingFingerprint"
  );
  const providerVoiceId = boundedCode(
    value.providerVoiceId,
    "providerVoiceId"
  );
  enumValue(value.providerClass, PROVIDER_CLASSES, "providerClass");
  boundedCode(value.modelId, "modelId");
  boundedCode(value.modelVersion, "modelVersion");
  sha256(value.modelPackageFingerprint, "modelPackageFingerprint");
  sha256(value.runtimeProfileFingerprint, "runtimeProfileFingerprint");
  sha256(value.normalizedTextSha256, "normalizedTextSha256");
  sha256(value.pronunciationPlanFingerprint, "pronunciationPlanFingerprint");
  sha256(value.providerControlFingerprint, "providerControlFingerprint");
  const cacheKey = sha256(value.cacheKey, "cacheKey");
  enumValue(value.cacheStatus, CACHE_STATUSES, "cacheStatus");
  const cacheProof = record(value.cacheProof, "audition cache proof");
  exactKeys(
    cacheProof,
    [
      "cacheRecordId",
      "cacheKey",
      "voiceRuntimeBindingId",
      "voiceRuntimeBindingFingerprint",
      "providerVoiceId",
      "verificationFingerprint"
    ],
    "audition cache proof"
  );
  identifier(cacheProof.cacheRecordId, "cacheProof.cacheRecordId");
  sha256(cacheProof.cacheKey, "cacheProof.cacheKey");
  identifier(
    cacheProof.voiceRuntimeBindingId,
    "cacheProof.voiceRuntimeBindingId"
  );
  sha256(
    cacheProof.voiceRuntimeBindingFingerprint,
    "cacheProof.voiceRuntimeBindingFingerprint"
  );
  boundedCode(cacheProof.providerVoiceId, "cacheProof.providerVoiceId");
  sha256(
    cacheProof.verificationFingerprint,
    "cacheProof.verificationFingerprint"
  );
  if (
    cacheProof.cacheKey !== cacheKey ||
    cacheProof.voiceRuntimeBindingId !== voiceRuntimeBindingId ||
    cacheProof.voiceRuntimeBindingFingerprint !==
      voiceRuntimeBindingFingerprint ||
    cacheProof.providerVoiceId !== providerVoiceId
  ) {
    fail("The audition cache proof did not match the exact clip binding.");
  }
  enumValue(value.state, CLIP_STATES, "clip state");
  positiveInteger(value.revision, "revision");
  sha256(value.clipFingerprint, "clipFingerprint");
  timestamp(value.createdAt, "createdAt");
  validateProvenance(value.provenance, "audition clip provenance");

  const artifact = record(value.audioArtifact, "audio artifact");
  exactKeys(
    artifact,
    [
      "contractVersion",
      "audioArtifactId",
      "projectId",
      "storageKey",
      "mediaType",
      "codec",
      "sampleRateHz",
      "channels",
      "sampleWidthBytes",
      "frameCount",
      "durationMilliseconds",
      "byteSize",
      "sha256",
      "availability",
      "playbackEligible",
      "publishedAtomically",
      "createdAt",
      "immutable"
    ],
    "audio artifact"
  );
  contractVersion(artifact.contractVersion, "audio artifact");
  ownedProject(artifact.projectId, expectedProjectId);
  const artifactId = identifier(artifact.audioArtifactId, "audioArtifactId");
  if (artifact.mediaType !== "audio/wav" || artifact.codec !== "pcm_s16le" || artifact.sampleRateHz !== 24_000 || artifact.channels !== 1 || artifact.sampleWidthBytes !== 2 || artifact.publishedAtomically !== true || artifact.immutable !== true) fail("The clip audio artifact was not bounded PCM WAV.");
  const frameCount = integerBetween(
    artifact.frameCount,
    1,
    SPEECH_AUDITION_LIMITS.expectedSampleRateHz *
      Math.ceil(SPEECH_AUDITION_LIMITS.maximumAuditionDurationMilliseconds / 1_000),
    "frameCount"
  );
  const durationMilliseconds = finiteBetween(
    artifact.durationMilliseconds,
    Number.EPSILON,
    SPEECH_AUDITION_LIMITS.maximumAuditionDurationMilliseconds,
    "durationMilliseconds"
  );
  const calculatedDuration =
    (frameCount * 1_000) / SPEECH_AUDITION_LIMITS.expectedSampleRateHz;
  if (Math.abs(durationMilliseconds - calculatedDuration) > 1) {
    fail("The audio artifact duration did not match its frame count.");
  }
  const byteSize = integerBetween(artifact.byteSize, 45, SPEECH_AUDITION_LIMITS.maximumAudioBytes, "byteSize");
  const minimumPcmWavByteSize =
    44 +
    frameCount *
      SPEECH_AUDITION_LIMITS.expectedChannels *
      SPEECH_AUDITION_LIMITS.expectedSampleWidthBytes;
  if (byteSize < minimumPcmWavByteSize) {
    fail("The audio artifact byte size could not contain its declared PCM frames.");
  }
  sha256(artifact.sha256, "artifact.sha256");
  enumValue(
    artifact.availability,
    new Set(["present", "purged", "corrupt", "quarantined"]),
    "artifact.availability"
  );
  booleanValue(artifact.playbackEligible, "artifact.playbackEligible");
  if (
    (artifact.availability === "present") !== artifact.playbackEligible
  ) {
    fail("The audio artifact availability and playback eligibility disagreed.");
  }
  if (artifact.availability !== "present" && value.state !== "invalidated") {
    fail("A clip with unavailable audio must be invalidated.");
  }
  const storageKey = identifier(artifact.storageKey, "storageKey");
  if (/[\\/]/u.test(storageKey)) fail("Audio storage keys must not reveal paths.");
  timestamp(artifact.createdAt, "artifact.createdAt");

  const quality = record(value.audioQuality, "audio quality");
  exactKeys(
    quality,
    [
      "contractVersion",
      "qualityRecordId",
      "projectId",
      "audioArtifactId",
      "profileId",
      "profileVersion",
      "validWav",
      "nonSilent",
      "peakDbfs",
      "silenceRatio",
      "clippedSampleCount",
      "blockingFindingCodes",
      "warningCodes",
      "subjectiveQualityClaimed",
      "qualityFingerprint",
      "measuredAt",
      "provenance"
    ],
    "audio quality"
  );
  contractVersion(quality.contractVersion, "audio quality");
  ownedProject(quality.projectId, expectedProjectId);
  identifier(quality.qualityRecordId, "qualityRecordId");
  if (identifier(quality.audioArtifactId, "quality.audioArtifactId") !== artifactId) {
    fail("The audio quality record did not belong to the clip artifact.");
  }
  boundedCode(quality.profileId, "quality.profileId");
  boundedCode(quality.profileVersion, "quality.profileVersion");
  booleanValue(quality.validWav, "quality.validWav");
  booleanValue(quality.nonSilent, "quality.nonSilent");
  finiteBetween(quality.peakDbfs, -200, 0, "quality.peakDbfs");
  finiteBetween(quality.silenceRatio, 0, 1, "quality.silenceRatio");
  const clippedSampleCount = integerBetween(
    quality.clippedSampleCount,
    0,
    frameCount * SPEECH_AUDITION_LIMITS.expectedChannels,
    "quality.clippedSampleCount"
  );
  if (quality.subjectiveQualityClaimed !== false) fail("Machine QC cannot claim subjective quality.");
  const warningCodes = validateFindingCodes(
    quality.warningCodes,
    "quality.warningCodes"
  );
  const blockingFindingCodes = validateFindingCodes(
    quality.blockingFindingCodes,
    "quality.blockingFindingCodes"
  );
  if (warningCodes.some((code) => blockingFindingCodes.includes(code))) {
    fail("The audio quality finding codes overlapped.");
  }
  if (quality.validWav !== (blockingFindingCodes.length === 0)) {
    fail("The audio quality WAV status did not match its blocking findings.");
  }
  if (
    quality.nonSilent !== !blockingFindingCodes.includes("AUDITION_ALL_SILENT")
  ) {
    fail("The audio quality silence status did not match its findings.");
  }
  if (
    (clippedSampleCount > 0) !==
    warningCodes.includes("AUDITION_CLIPPING_DETECTED")
  ) {
    fail("The clipped sample count did not match its warning code.");
  }
  sha256(quality.qualityFingerprint, "quality.qualityFingerprint");
  timestamp(quality.measuredAt, "quality.measuredAt");
  validateProvenance(quality.provenance, "audio quality provenance");
  return raw as AuditionClip;
}

function validateReview(raw: unknown, expectedProjectId: string): AuditionReview {
  const value = record(raw, "audition review");
  exactKeys(
    value,
    [
      "contractVersion",
      "reviewId",
      "projectId",
      "gateId",
      "roleId",
      "state",
      "revision",
      "prerequisiteGateIds",
      "evidence",
      "blockerCodes",
      "warningCodes",
      "latestDecision",
      "updatedAt"
    ],
    "audition review"
  );
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) {
    fail("The audition review contract version was invalid.");
  }
  ownedProject(value.projectId, expectedProjectId);
  identifier(value.reviewId, "reviewId");
  enumValue(value.gateId, GATES, "gateId");
  nullableIdentifier(value.roleId, "roleId");
  enumValue(
    value.state,
    new Set([
      "blocked",
      "pending",
      "approved",
      "changes_requested",
      "rejected",
      "invalidated"
    ]),
    "review state"
  );
  positiveInteger(value.revision, "revision");
  gateArray(value.prerequisiteGateIds, "prerequisiteGateIds");
  identifierArray(
    value.blockerCodes,
    "blockerCodes",
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  identifierArray(
    value.warningCodes,
    "warningCodes",
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  const evidence = validateGateEvidence(value.evidence, expectedProjectId);
  if (evidence.gateId !== value.gateId || evidence.roleId !== value.roleId) {
    fail("The review scope and evidence scope did not match.");
  }
  if (value.latestDecision !== null) {
    const decision = validateReviewDecision(
      value.latestDecision,
      expectedProjectId,
      value.gateId,
      value.roleId
    );
    const currentDecision =
      decision.reviewId === value.reviewId &&
      decision.evidenceFingerprint === evidence.evidenceFingerprint;
    if (currentDecision && decision.decision !== value.state) {
      fail("The current review decision and review state did not match.");
    }
    if (
      !currentDecision &&
      !new Set(["pending", "blocked"]).has(value.state as string)
    ) {
      fail("Stale review evidence retained decision authority.");
    }
  } else if (!new Set(["pending", "blocked"]).has(value.state as string)) {
    fail("The review state claimed authority without an immutable decision.");
  }
  timestamp(value.updatedAt, "updatedAt");
  return raw as AuditionReview;
}

function validateGateEvidence(
  raw: unknown,
  expectedProjectId: string
): Record<string, unknown> {
  const value = record(raw, "review evidence");
  exactKeys(
    value,
    [
      "projectId",
      "gateId",
      "roleId",
      "auditionSessionId",
      "auditionClipId",
      "auditionClipRevision",
      "approvedCastSnapshotFingerprint",
      "castAssignmentFingerprint",
      "rightsRecordFingerprint",
      "runtimeProfileFingerprint",
      "modelVerificationFingerprint",
      "pronunciationDictionaryFingerprint",
      "pronunciationDependencyFingerprint",
      "audioQualityFingerprint",
      "evidenceFingerprint"
    ],
    "review evidence"
  );
  ownedProject(value.projectId, expectedProjectId);
  enumValue(value.gateId, GATES, "gateId");
  nullableIdentifier(value.roleId, "roleId");
  nullableIdentifier(value.auditionSessionId, "auditionSessionId");
  nullableIdentifier(value.auditionClipId, "auditionClipId");
  nullablePositiveInteger(value.auditionClipRevision, "auditionClipRevision");
  for (const key of [
    "approvedCastSnapshotFingerprint",
    "rightsRecordFingerprint",
    "runtimeProfileFingerprint",
    "modelVerificationFingerprint",
    "pronunciationDictionaryFingerprint",
    "pronunciationDependencyFingerprint",
    "evidenceFingerprint"
  ] as const) {
    sha256(value[key], key);
  }
  ownedFingerprintOrNull(
    value.castAssignmentFingerprint,
    "castAssignmentFingerprint"
  );
  ownedFingerprintOrNull(value.audioQualityFingerprint, "audioQualityFingerprint");
  return value;
}

function validateReviewDecision(
  raw: unknown,
  expectedProjectId: string,
  expectedGateId: unknown,
  expectedRoleId: unknown
): AuditionReviewDecision {
  const value = record(raw, "review decision");
  exactKeys(
    value,
    [
      "contractVersion",
      "decisionId",
      "reviewId",
      "projectId",
      "gateId",
      "roleId",
      "decision",
      "actor",
      "expectedReviewRevision",
      "evidenceFingerprint",
      "rationale",
      "decidedAt",
      "immutable",
      "supersedesDecisionId",
      "provenance"
    ],
    "review decision"
  );
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) {
    fail("The review decision contract version was invalid.");
  }
  identifier(value.decisionId, "decisionId");
  identifier(value.reviewId, "reviewId");
  ownedProject(value.projectId, expectedProjectId);
  enumValue(value.gateId, GATES, "gateId");
  nullableIdentifier(value.roleId, "roleId");
  if (value.gateId !== expectedGateId || value.roleId !== expectedRoleId) {
    fail("The review decision did not match the review scope.");
  }
  enumValue(
    value.decision,
    new Set(["approved", "changes_requested", "rejected", "invalidated"]),
    "review decision"
  );
  const actor = record(value.actor, "review actor");
  exactKeys(actor, ["classification", "actorId"], "review actor");
  enumValue(actor.classification, new Set(["human", "system"]), "actor classification");
  identifier(actor.actorId, "actorId");
  positiveInteger(value.expectedReviewRevision, "expectedReviewRevision");
  sha256(value.evidenceFingerprint, "evidenceFingerprint");
  boundedText(value.rationale, "rationale", 4_000);
  timestamp(value.decidedAt, "decidedAt");
  if (value.immutable !== true) fail("Review decisions must be immutable.");
  nullableIdentifier(value.supersedesDecisionId, "supersedesDecisionId");
  validateProvenance(value.provenance, "review decision provenance");
  return raw as AuditionReviewDecision;
}

function validateReviewScope(gateId: unknown, roleId: unknown): void {
  if (gateId === "per_role_audition_review") {
    if (roleId === null) {
      fail("A per-role audition review requires a role ID.");
    }
    return;
  }
  if (roleId !== null) {
    fail("An aggregate audition review cannot carry a role ID.");
  }
}

function validateVoiceReadinessSnapshot(
  raw: unknown,
  expectedProjectId: string
): VoiceReadinessSnapshot {
  const value = record(raw, "voiceReadinessSnapshot");
  exactKeys(
    value,
    [
      "contractVersion",
      "snapshotId",
      "projectId",
      "revision",
      "approvedCastSnapshotId",
      "approvedCastSnapshotRevision",
      "approvedCastSnapshotFingerprint",
      "runtimeProfileFingerprint",
      "modelVerificationFingerprint",
      "rightsEvidenceFingerprint",
      "narratorAuditionDecisionIds",
      "characterAuditionDecisionIds",
      "pronunciationReviewDecisionId",
      "requiredRoleCount",
      "approvedRoleCount",
      "blockingFindingCodes",
      "snapshotFingerprint",
      "reviewEligible",
      "authorizes",
      "authorizesFullBookRendering",
      "createdAt",
      "immutable"
    ],
    "voiceReadinessSnapshot"
  );
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) {
    fail("The voice readiness contract version was invalid.");
  }
  identifier(value.snapshotId, "snapshotId");
  ownedProject(value.projectId, expectedProjectId);
  positiveInteger(value.revision, "revision");
  identifier(value.approvedCastSnapshotId, "approvedCastSnapshotId");
  positiveInteger(
    value.approvedCastSnapshotRevision,
    "approvedCastSnapshotRevision"
  );
  for (const key of [
    "approvedCastSnapshotFingerprint",
    "runtimeProfileFingerprint",
    "modelVerificationFingerprint",
    "rightsEvidenceFingerprint",
    "snapshotFingerprint"
  ] as const) {
    sha256(value[key], key);
  }
  const narratorDecisions = boundedArray(
    value.narratorAuditionDecisionIds,
    "narratorAuditionDecisionIds",
    SPEECH_AUDITION_LIMITS.maximumProductionRoles
  );
  if (narratorDecisions.length === 0) {
    fail("Voice readiness omitted its narrator decision evidence.");
  }
  identifierArray(
    narratorDecisions,
    "narratorAuditionDecisionIds",
    SPEECH_AUDITION_LIMITS.maximumProductionRoles
  );
  identifierArray(
    value.characterAuditionDecisionIds,
    "characterAuditionDecisionIds",
    SPEECH_AUDITION_LIMITS.maximumProductionRoles
  );
  nullableIdentifier(
    value.pronunciationReviewDecisionId,
    "pronunciationReviewDecisionId"
  );
  const requiredRoleCount = integerBetween(
    value.requiredRoleCount,
    1,
    SPEECH_AUDITION_LIMITS.maximumProductionRoles,
    "requiredRoleCount"
  );
  const approvedRoleCount = integerBetween(
    value.approvedRoleCount,
    0,
    SPEECH_AUDITION_LIMITS.maximumProductionRoles,
    "approvedRoleCount"
  );
  if (approvedRoleCount > requiredRoleCount) {
    fail("Voice readiness approved more roles than were required.");
  }
  const blockingFindingCodes = boundedArray(
    value.blockingFindingCodes,
    "blockingFindingCodes",
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  identifierArray(
    blockingFindingCodes,
    "blockingFindingCodes",
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  booleanValue(value.reviewEligible, "reviewEligible");
  if (value.reviewEligible !== (blockingFindingCodes.length === 0)) {
    fail("Voice readiness eligibility did not match its blocking findings.");
  }
  if (
    value.authorizes !== "later_performance_direction_only" ||
    value.authorizesFullBookRendering !== false
  ) {
    fail("Voice readiness exceeded the Phase 3B authority boundary.");
  }
  timestamp(value.createdAt, "createdAt");
  if (value.immutable !== true) fail("Voice readiness snapshots must be immutable.");
  return raw as VoiceReadinessSnapshot;
}

function validateDecisionResponseVoiceReadinessSnapshot(
  snapshot: VoiceReadinessSnapshot | null,
  review: AuditionReview,
  decision: AuditionReviewDecision
): void {
  if (review.gateId === "voice_readiness_review" && snapshot === null) {
    fail("The voice readiness review response omitted its exact snapshot evidence.");
  }
  if (snapshot === null) return;

  const evidence = review.evidence;
  const snapshotRightsMustMatchReview =
    review.gateId === "pronunciation_review" ||
    review.gateId === "voice_readiness_review";
  if (
    snapshot.approvedCastSnapshotFingerprint !==
      evidence.approvedCastSnapshotFingerprint ||
    snapshot.runtimeProfileFingerprint !== evidence.runtimeProfileFingerprint ||
    snapshot.modelVerificationFingerprint !==
      evidence.modelVerificationFingerprint ||
    (snapshotRightsMustMatchReview &&
      snapshot.rightsEvidenceFingerprint !== evidence.rightsRecordFingerprint)
  ) {
    fail("The voice readiness snapshot did not match the returned review evidence.");
  }

  const approved = decision.decision === "approved";
  const narratorIncludesDecision =
    snapshot.narratorAuditionDecisionIds.includes(decision.decisionId);
  const characterIncludesDecision =
    snapshot.characterAuditionDecisionIds.includes(decision.decisionId);
  const pronunciationMatchesDecision =
    snapshot.pronunciationReviewDecisionId === decision.decisionId;
  if (
    (review.gateId === "narrator_audition_review" &&
      narratorIncludesDecision !== approved) ||
    (review.gateId === "character_audition_review" &&
      characterIncludesDecision !== approved) ||
    (review.gateId === "pronunciation_review" &&
      pronunciationMatchesDecision !== approved) ||
    (review.gateId === "voice_readiness_review" &&
      snapshot.snapshotFingerprint !== evidence.evidenceFingerprint)
  ) {
    fail("The voice readiness snapshot did not bind the returned review decision.");
  }
}

function validateNormalizationPlan(
  raw: unknown,
  expectedProjectId: string,
  expectedOriginalTextSha256: string
): void {
  const value = record(raw, "normalization plan");
  exactKeys(
    value,
    [
      "contractVersion",
      "normalizationPlanId",
      "projectId",
      "originalTextSha256",
      "normalizedTextSha256",
      "providerId",
      "profileId",
      "profileVersion",
      "transformations",
      "appliedPronunciationEntryIds",
      "unsupportedCharacterCodePoints",
      "warnings",
      "humanReviewRequired",
      "planFingerprint",
      "provenance"
    ],
    "normalization plan"
  );
  contractVersion(value.contractVersion, "normalization plan");
  identifier(value.normalizationPlanId, "normalizationPlanId");
  ownedProject(value.projectId, expectedProjectId);
  if (
    sha256(value.originalTextSha256, "originalTextSha256") !==
    expectedOriginalTextSha256
  ) {
    fail("The normalization plan did not match the requested source text.");
  }
  sha256(value.normalizedTextSha256, "normalizedTextSha256");
  boundedCode(value.providerId, "normalization providerId");
  boundedCode(value.profileId, "normalization profileId");
  boundedCode(value.profileVersion, "normalization profileVersion");
  sha256(value.planFingerprint, "planFingerprint");
  const transformationIds = new Set<string>();
  let priorSourceEnd = 0;
  let priorDestinationEnd = 0;
  for (const item of boundedArray(value.transformations, "transformations", 2_000)) {
    const transformation = record(item, "normalization transformation");
    exactKeys(
      transformation,
      [
        "transformationId",
        "kind",
        "sourceSpan",
        "destinationSpan",
        "originalTextSha256",
        "replacementTextSha256",
        "originalText",
        "replacementText",
        "reasonCode",
        "requiredByProvider",
        "humanApprovalRequired",
        "approved"
      ],
      "normalization transformation"
    );
    const transformationId = identifier(
      transformation.transformationId,
      "transformationId"
    );
    if (transformationIds.has(transformationId)) {
      fail("The normalization plan contained duplicate transformation identifiers.");
    }
    transformationIds.add(transformationId);
    enumValue(transformation.kind, NORMALIZATION_KINDS, "normalization kind");
    const sourceSpan = validateTextSpan(
      transformation.sourceSpan,
      "normalization source span",
      SPEECH_AUDITION_LIMITS.maximumScriptCodePoints
    );
    const destinationSpan = validateTextSpan(
      transformation.destinationSpan,
      "normalization destination span",
      MAXIMUM_NORMALIZED_TEXT_CODE_POINTS
    );
    if (
      sourceSpan.start < priorSourceEnd ||
      destinationSpan.start < priorDestinationEnd
    ) {
      fail("The normalization transformations overlapped or were out of order.");
    }
    priorSourceEnd = sourceSpan.end;
    priorDestinationEnd = destinationSpan.end;
    const originalTextSha256 = sha256(
      transformation.originalTextSha256,
      "originalTextSha256"
    );
    const replacementTextSha256 = sha256(
      transformation.replacementTextSha256,
      "replacementTextSha256"
    );
    const originalText = boundedTextAllowEmpty(
      transformation.originalText,
      "originalText",
      256
    );
    const replacementText = boundedTextAllowEmpty(
      transformation.replacementText,
      "replacementText",
      256
    );
    if (
      textSha256(originalText) !== originalTextSha256 ||
      textSha256(replacementText) !== replacementTextSha256
    ) {
      fail("The normalization transformation text did not match its SHA-256.");
    }
    if (originalText === replacementText) {
      fail("The normalization transformation did not change the source text.");
    }
    if ([...originalText].length !== sourceSpan.end - sourceSpan.start) {
      fail("The normalization source text did not match its span.");
    }
    boundedCode(transformation.reasonCode, "normalization reasonCode");
    booleanValue(
      transformation.requiredByProvider,
      "normalization requiredByProvider"
    );
    booleanValue(
      transformation.humanApprovalRequired,
      "normalization humanApprovalRequired"
    );
    booleanValue(transformation.approved, "normalization approved");
    if (
      transformation.humanApprovalRequired ===
      transformation.requiredByProvider
    ) {
      fail("The normalization approval classification was contradictory.");
    }
    if (
      transformation.requiredByProvider === true &&
      transformation.approved !== true
    ) {
      fail("A provider-required normalization transformation was not approved.");
    }
    const destinationText =
      transformation.approved === true ? replacementText : originalText;
    if (
      [...destinationText].length !==
      destinationSpan.end - destinationSpan.start
    ) {
      fail("The normalization destination text did not match its span.");
    }
  }
  identifierArray(
    value.appliedPronunciationEntryIds,
    "appliedPronunciationEntryIds",
    SPEECH_AUDITION_LIMITS.maximumPronunciationEntries
  );
  const unsupportedCodePoints = boundedArray(
    value.unsupportedCharacterCodePoints,
    "unsupportedCharacterCodePoints",
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  const uniqueUnsupportedCodePoints = new Set<string>();
  for (const item of unsupportedCodePoints) {
    if (typeof item !== "string" || !/^U\+[0-9A-F]{4,6}$/u.test(item)) {
      fail("The unsupported character code point was invalid.");
    }
    if (uniqueUnsupportedCodePoints.has(item)) {
      fail("The unsupported character code points contained duplicates.");
    }
    uniqueUnsupportedCodePoints.add(item);
  }
  const warningCodes = identifierArray(
    value.warnings,
    "normalization warnings",
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  const humanReviewRequired = booleanValue(
    value.humanReviewRequired,
    "normalization humanReviewRequired"
  );
  const derivedHumanReviewRequired =
    unsupportedCodePoints.length > 0 ||
    warningCodes.includes("NORMALIZATION_REVIEW_REQUIRED") ||
    boundedArray(value.transformations, "transformations", 2_000).some(
      (item) => {
        const transformation = record(item, "normalization transformation");
        return (
          transformation.humanApprovalRequired === true &&
          transformation.approved !== true
        );
      }
    );
  if (humanReviewRequired !== derivedHumanReviewRequired) {
    fail("The normalization human-review flag contradicted its evidence.");
  }
  validateProvenance(value.provenance, "normalization plan provenance");
}

function validateNormalizationSelections(
  plan: Record<string, unknown>,
  acceptedOptionalNormalizationIds: readonly string[]
): void {
  const acceptedIds = identifierArray(
    acceptedOptionalNormalizationIds,
    "accepted optional normalization identifiers",
    2_000
  );
  const acceptedSet = new Set(acceptedIds);
  const optionalIds = new Set<string>();
  for (const rawTransformation of boundedArray(
    plan.transformations,
    "normalization transformations",
    2_000
  )) {
    const transformation = record(
      rawTransformation,
      "normalization transformation"
    );
    if (transformation.humanApprovalRequired !== true) continue;
    const transformationId = identifier(
      transformation.transformationId,
      "normalization transformationId"
    );
    optionalIds.add(transformationId);
    if (transformation.approved !== acceptedSet.has(transformationId)) {
      fail("The normalization approval evidence did not match the request.");
    }
  }
  if (acceptedIds.some((identifierValue) => !optionalIds.has(identifierValue))) {
    fail("The normalization request accepted an identifier absent from the plan.");
  }
}

function validatePronunciationPlan(raw: unknown, expectedProjectId: string): void {
  const value = record(raw, "pronunciation plan");
  exactKeys(
    value,
    [
      "contractVersion",
      "pronunciationPlanId",
      "projectId",
      "dictionaryId",
      "dictionaryRevision",
      "dictionaryFingerprint",
      "sourceTextSha256",
      "locale",
      "roleId",
      "scopeContext",
      "appliedEntries",
      "dependencyEntryRevisions",
      "providerId",
      "escapedProviderPayloadSha256",
      "planFingerprint",
      "provenance"
    ],
    "pronunciation plan"
  );
  if (value.contractVersion !== SPEECH_AUDITION_CONTRACT_VERSION) {
    fail("The speech audition contract version was invalid.");
  }
  identifier(value.pronunciationPlanId, "pronunciationPlanId");
  ownedProject(value.projectId, expectedProjectId);
  identifier(value.dictionaryId, "dictionaryId");
  positiveInteger(value.dictionaryRevision, "dictionaryRevision");
  sha256(value.dictionaryFingerprint, "dictionaryFingerprint");
  sha256(value.sourceTextSha256, "sourceTextSha256");
  boundedText(value.locale, "locale", 32);
  identifier(value.roleId, "roleId");
  const scopeContext = record(value.scopeContext, "pronunciation scope context");
  exactKeys(
    scopeContext,
    ["chapterId", "sceneId", "customScopeIds"],
    "pronunciation scope context"
  );
  nullableIdentifier(scopeContext.chapterId, "scopeContext.chapterId");
  nullableIdentifier(scopeContext.sceneId, "scopeContext.sceneId");
  const customScopeIds = boundedArray(
    scopeContext.customScopeIds,
    "scopeContext.customScopeIds",
    50
  );
  const uniqueCustomScopeIds = new Set(
    customScopeIds.map((scopeId) =>
      identifier(scopeId, "scopeContext.customScopeIds[]")
    )
  );
  if (uniqueCustomScopeIds.size !== customScopeIds.length) {
    fail("The pronunciation scope context contained duplicate custom scope identifiers.");
  }
  const appliedEntries = boundedArray(
    value.appliedEntries,
    "appliedEntries",
    SPEECH_AUDITION_LIMITS.maximumPronunciationEntries
  );
  const appliedEntryIds = new Set<string>();
  for (const rawEntry of appliedEntries) {
    const entry = record(rawEntry, "applied pronunciation entry");
    exactKeys(
      entry,
      [
        "sourceSpan",
        "entryId",
        "entryRevision",
        "writtenFormSha256",
        "compiledValueSha256",
        "representation"
      ],
      "applied pronunciation entry"
    );
    validateTextSpan(
      entry.sourceSpan,
      "applied pronunciation source span",
      SPEECH_AUDITION_LIMITS.maximumScriptCodePoints
    );
    const entryId = identifier(entry.entryId, "applied pronunciation entryId");
    if (appliedEntryIds.has(entryId)) {
      fail("The pronunciation plan contained duplicate applied entries.");
    }
    appliedEntryIds.add(entryId);
    positiveInteger(entry.entryRevision, "applied pronunciation entryRevision");
    sha256(entry.writtenFormSha256, "writtenFormSha256");
    sha256(entry.compiledValueSha256, "compiledValueSha256");
    enumValue(
      entry.representation,
      new Set(["provider_neutral", "ipa", "provider_specific"]),
      "pronunciation representation"
    );
  }
  const dependencies = boundedArray(
    value.dependencyEntryRevisions,
    "dependencyEntryRevisions",
    SPEECH_AUDITION_LIMITS.maximumPronunciationEntries
  );
  const dependencyEntryIds = new Set<string>();
  for (const rawDependency of dependencies) {
    const dependency = record(
      rawDependency,
      "pronunciation dependency revision"
    );
    exactKeys(
      dependency,
      ["entryId", "revision"],
      "pronunciation dependency revision"
    );
    const entryId = identifier(
      dependency.entryId,
      "pronunciation dependency entryId"
    );
    if (dependencyEntryIds.has(entryId)) {
      fail("The pronunciation plan contained duplicate dependency entries.");
    }
    dependencyEntryIds.add(entryId);
    positiveInteger(dependency.revision, "pronunciation dependency revision");
  }
  if ([...appliedEntryIds].some((entryId) => !dependencyEntryIds.has(entryId))) {
    fail("An applied pronunciation entry omitted its revision dependency.");
  }
  boundedText(value.providerId, "providerId", 128);
  sha256(value.escapedProviderPayloadSha256, "escapedProviderPayloadSha256");
  sha256(value.planFingerprint, "planFingerprint");
  validateProvenance(value.provenance, "pronunciation plan provenance");
}

function validateRole(raw: unknown, expectedProjectId: string): void {
  const value = record(raw, "audition role");
  exactKeys(
    value,
    [
      "roleId",
      "roleType",
      "displayLabel",
      "required",
      "assignmentId",
      "assignmentRevision",
      "voiceProfileId",
      "voiceDisplayLabel",
      "voiceRuntimeBinding",
      "runtimeBindingStatus",
      "runtimeBindingReasonCode",
      "rightsState",
      "latestSessionId",
      "latestClipId",
      "reviewState",
      "sessionEvidence",
      "generationRequest"
    ],
    "audition role"
  );
  identifier(value.roleId, "roleId");
  enumValue(value.roleType, new Set(["narrator", "character"]), "roleType");
  boundedText(value.displayLabel, "displayLabel", 120);
  booleanValue(value.required, "required");
  const assignmentId = identifier(value.assignmentId, "assignmentId");
  const assignmentRevision = positiveInteger(
    value.assignmentRevision,
    "assignmentRevision"
  );
  const voiceProfileId = identifier(value.voiceProfileId, "voiceProfileId");
  boundedText(value.voiceDisplayLabel, "voiceDisplayLabel", 120);
  const runtimeBindingStatus = enumValue(
    value.runtimeBindingStatus,
    new Set(["compatible", "incompatible", "unavailable"]),
    "runtimeBindingStatus"
  );
  let voiceRuntimeBinding: Record<string, unknown> | null = null;
  if (value.voiceRuntimeBinding !== null) {
    voiceRuntimeBinding = validateVoiceRuntimeBinding(value.voiceRuntimeBinding);
  }
  if (runtimeBindingStatus === "compatible") {
    if (
      voiceRuntimeBinding === null ||
      voiceRuntimeBinding.active !== true ||
      voiceRuntimeBinding.voiceProfileId !== voiceProfileId ||
      value.runtimeBindingReasonCode !== null
    ) {
      fail("A compatible role omitted its exact active voice runtime binding.");
    }
  } else {
    const expectedReason =
      runtimeBindingStatus === "incompatible"
        ? "VOICE_RUNTIME_BINDING_INCOMPATIBLE"
        : "VERIFIED_ACTIVE_MODEL_PACKAGE_REQUIRED";
    if (
      voiceRuntimeBinding !== null ||
      value.runtimeBindingReasonCode !== expectedReason ||
      value.sessionEvidence !== null ||
      value.generationRequest !== null
    ) {
      fail("An unavailable role exposed usable or contradictory runtime evidence.");
    }
  }
  enumValue(value.rightsState, new Set(["verified", "restricted"]), "rightsState");
  nullableIdentifier(value.latestSessionId, "latestSessionId");
  nullableIdentifier(value.latestClipId, "latestClipId");
  enumValue(
    value.reviewState,
    new Set([
      "pending",
      "approved",
      "changes_requested",
      "rejected",
      "invalidated",
      "blocked"
    ]),
    "reviewState"
  );
  if (value.sessionEvidence !== null) {
    validateEvidenceBinding(value.sessionEvidence, expectedProjectId);
    validateRoleEvidence(
      value.sessionEvidence,
      assignmentId,
      assignmentRevision,
      voiceProfileId,
      voiceRuntimeBinding
    );
  }
  if (value.generationRequest !== null) {
    validateSpeechPreviewRequest(value.generationRequest, expectedProjectId);
    const request = record(value.generationRequest, "generation request");
    validateRoleEvidence(
      request.evidence,
      assignmentId,
      assignmentRevision,
      voiceProfileId,
      voiceRuntimeBinding
    );
    if (
      value.latestSessionId === null ||
      request.auditionSessionId !== value.latestSessionId
    ) {
      fail("The role generation request did not match its latest session.");
    }
  }
}

function validateRoleEvidence(
  raw: unknown,
  assignmentId: string,
  assignmentRevision: number,
  voiceProfileId: string,
  voiceRuntimeBinding: Record<string, unknown> | null
): void {
  const evidence = record(raw, "role audition evidence");
  if (
    evidence.castAssignmentId !== assignmentId ||
    evidence.castAssignmentRevision !== assignmentRevision ||
    evidence.voiceProfileId !== voiceProfileId ||
    voiceRuntimeBinding === null ||
    evidence.voiceRuntimeBindingId !== voiceRuntimeBinding.bindingId ||
    evidence.voiceRuntimeBindingFingerprint !==
      voiceRuntimeBinding.bindingFingerprint ||
    evidence.providerVoiceId !== voiceRuntimeBinding.providerVoiceId ||
    evidence.providerId !== voiceRuntimeBinding.providerId ||
    evidence.providerVersion !== voiceRuntimeBinding.providerVersion ||
    evidence.modelId !== voiceRuntimeBinding.modelId ||
    evidence.modelVersion !== voiceRuntimeBinding.modelVersion ||
    evidence.modelPackageId !== voiceRuntimeBinding.modelPackageId ||
    evidence.modelPackageFingerprint !==
      voiceRuntimeBinding.modelPackageFingerprint ||
    evidence.runtimeProfileId !== voiceRuntimeBinding.runtimeProfileId ||
    evidence.runtimeProfileFingerprint !==
      voiceRuntimeBinding.runtimeProfileFingerprint
  ) {
    fail("The role audition evidence did not match its exact current binding.");
  }
}

function validatePageResponse(raw: unknown, label: string, requestedLimit?: number): Record<string, unknown> {
  const value = record(raw, label);
  identifier(value.correlationId, "correlationId");
  const maximumPageSize =
    requestedLimit ?? SPEECH_AUDITION_LIMITS.defaultPageSize;
  const pageSize = integerBetween(
    value.pageSize,
    0,
    maximumPageSize,
    "pageSize"
  );
  const total = integerBetween(value.total, 0, 10_000, "total");
  const items = boundedArray(value.items, "items", maximumPageSize);
  if (pageSize !== items.length || items.length > total) {
    fail(`The ${label} metadata did not match its current page.`);
  }
  optionalBoundedText(value.nextCursor, "nextCursor", SPEECH_AUDITION_LIMITS.maximumCursorCodePoints);
  return value;
}

function page(payload: Record<string, unknown>): void {
  optionalBoundedText(payload.cursor, "cursor", SPEECH_AUDITION_LIMITS.maximumCursorCodePoints);
  if (payload.limit !== undefined) integerBetween(payload.limit, 1, SPEECH_AUDITION_LIMITS.maximumPageSize, "limit");
}

function record(raw: unknown, label: string): Record<string, unknown> {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) fail(`The ${label} was invalid.`);
  return raw as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string, optionalUndefined = false): void {
  const allowed = new Set(keys);
  if (Object.keys(value).some((key) => !allowed.has(key))) fail(`The ${label} contained an unknown field.`);
  if (!optionalUndefined) {
    for (const key of keys) if (!(key in value)) fail(`The ${label} omitted ${key}.`);
  }
}

function exactKeysWithOptional(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
  label: string
): void {
  const allowed = new Set([...required, ...optional]);
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    fail(`The ${label} contained an unknown field.`);
  }
  for (const key of required) {
    if (!(key in value)) fail(`The ${label} omitted ${key}.`);
  }
}

function identifier(raw: unknown, label: string): string {
  if (typeof raw !== "string" || !SAFE_ID.test(raw)) fail(`The ${label} was invalid.`);
  return raw;
}
function boundedCode(raw: unknown, label: string): string { return identifier(raw, label); }
function contractVersion(raw: unknown, label: string): void { if (raw !== SPEECH_AUDITION_CONTRACT_VERSION) fail(`The ${label} contract version was invalid.`); }
function optionalIdentifier(raw: unknown, label: string): void { if (raw !== undefined) identifier(raw, label); }
function nullableIdentifier(raw: unknown, label: string): void { if (raw !== null) identifier(raw, label); }
function sha256(raw: unknown, label: string): string { if (typeof raw !== "string" || !SHA256.test(raw)) fail(`The ${label} was invalid.`); return raw; }
function ownedFingerprintOrNull(raw: unknown, label: string): void { if (raw !== null) sha256(raw, label); }
function positiveInteger(raw: unknown, label: string): number { return integerBetween(raw, 1, Number.MAX_SAFE_INTEGER, label); }
function nullablePositiveInteger(raw: unknown, label: string): void { if (raw !== null) positiveInteger(raw, label); }
function integerBetween(raw: unknown, minimum: number, maximum: number, label: string): number { if (!Number.isInteger(raw) || (raw as number) < minimum || (raw as number) > maximum) fail(`The ${label} was out of range.`); return raw as number; }
function finiteBetween(raw: unknown, minimum: number, maximum: number, label: string): number { if (typeof raw !== "number" || !Number.isFinite(raw) || raw < minimum || raw > maximum) fail(`The ${label} was out of range.`); return raw; }
function nullableFinite(raw: unknown, label: string): void { if (raw !== null && (typeof raw !== "number" || !Number.isFinite(raw))) fail(`The ${label} was invalid.`); }
function booleanValue(raw: unknown, label: string): boolean {
  if (typeof raw !== "boolean") fail(`The ${label} was invalid.`);
  return raw;
}
function boundedText(raw: unknown, label: string, maximum: number): string { if (typeof raw !== "string" || raw.trim().length === 0 || [...raw].length > maximum) fail(`The ${label} was invalid.`); return raw; }
function boundedTextAllowEmpty(raw: unknown, label: string, maximum: number): string { if (typeof raw !== "string" || [...raw].length > maximum) fail(`The ${label} was invalid.`); return raw; }
function nullableBoundedText(raw: unknown, label: string, maximum: number): void { if (raw !== null) boundedText(raw, label, maximum); }
function optionalBoundedText(raw: unknown, label: string, maximum: number): void { if (raw !== undefined) boundedText(raw, label, maximum); }
function boundedPrivateText(raw: unknown, label: string): string { const value = boundedText(raw, label, SPEECH_AUDITION_LIMITS.maximumScriptCodePoints); if (/[^\t\n\r\u0020-\uFFFF]/u.test(value)) fail(`The ${label} contained a forbidden control character.`); return value; }
function safePronunciationText(raw: unknown, label: string, maximum: number): void { const value = boundedText(raw, label, maximum); if (MARKUP_OR_CONTROL.test(value)) fail(`The ${label} contained markup or control text.`); }
function nullableSafePronunciationText(raw: unknown, label: string, maximum: number): void { if (raw !== null) safePronunciationText(raw, label, maximum); }
function idempotencyKey(raw: unknown): void { const value = boundedText(raw, "idempotencyKey", SPEECH_AUDITION_LIMITS.maximumIdempotencyKeyCodePoints); if (!SAFE_ID.test(value)) fail("The idempotency key was invalid."); }
function enumValue(
  raw: unknown,
  values: ReadonlySet<string>,
  label: string
): string {
  if (typeof raw !== "string" || !values.has(raw)) {
    fail(`The ${label} was invalid.`);
  }
  return raw;
}
function boundedArray(raw: unknown, label: string, maximum: number): readonly unknown[] { if (!Array.isArray(raw) || raw.length > maximum) fail(`The ${label} exceeded its limit.`); return raw as readonly unknown[]; }
function identifierArray(
  raw: unknown,
  label: string,
  maximum: number
): readonly string[] {
  const values = boundedArray(raw, label, maximum);
  const seen = new Set<string>();
  const identifiers: string[] = [];
  for (const item of values) {
    const value = identifier(item, label);
    if (seen.has(value)) fail(`The ${label} contained duplicates.`);
    seen.add(value);
    identifiers.push(value);
  }
  return identifiers;
}
function validateExactIdentifierSet(
  raw: unknown,
  expectedRaw: readonly string[],
  label: string
): void {
  const actual = identifierArray(
    raw,
    label,
    SPEECH_AUDITION_LIMITS.maximumPronunciationEntries
  );
  const expected = identifierArray(
    expectedRaw,
    `expected ${label}`,
    SPEECH_AUDITION_LIMITS.maximumPronunciationEntries
  );
  const expectedSet = new Set(expected);
  if (
    actual.length !== expected.length ||
    actual.some((value) => !expectedSet.has(value))
  ) {
    fail(`The ${label} did not match the request.`);
  }
}
function canonicalJsonValue(raw: unknown): string {
  if (
    raw === null ||
    typeof raw === "string" ||
    typeof raw === "number" ||
    typeof raw === "boolean"
  ) {
    return JSON.stringify(raw);
  }
  if (Array.isArray(raw)) {
    return `[${raw.map((item) => canonicalJsonValue(item)).join(",")}]`;
  }
  const value = record(raw, "canonical comparison value");
  return `{${Object.keys(value)
    .sort()
    .map(
      (key) =>
        `${JSON.stringify(key)}:${canonicalJsonValue(value[key])}`
    )
    .join(",")}}`;
}
function boundedTextArray(
  raw: unknown,
  label: string,
  maximum: number,
  maximumCodePoints: number
): readonly string[] {
  const values = boundedArray(raw, label, maximum).map((item) =>
    boundedText(item, label, maximumCodePoints)
  );
  if (new Set(values).size !== values.length) {
    fail(`The ${label} contained duplicates.`);
  }
  return values;
}
function optionalIdentifierArray(raw: unknown, label: string, maximum: number): void { if (raw !== undefined) identifierArray(raw, label, maximum); }
function gateArray(raw: unknown, label: string): void { const values = boundedArray(raw, label, AUDITION_GATE_IDS.length); const seen = new Set<string>(); for (const value of values) { enumValue(value, GATES, label); if (seen.has(value as string)) fail(`The ${label} contained duplicates.`); seen.add(value as string); } }
function nullableSpan(raw: unknown, label: string): void { if (raw === null) return; const value = record(raw, label); exactKeys(value, ["start", "end"], label); const start = integerBetween(value.start, 0, SPEECH_AUDITION_LIMITS.maximumScriptCodePoints, `${label}.start`); const end = integerBetween(value.end, 1, SPEECH_AUDITION_LIMITS.maximumScriptCodePoints, `${label}.end`); if (end <= start) fail(`The ${label} was reversed.`); }
function timestamp(raw: unknown, label: string): void { if (typeof raw !== "string" || raw.length > 40 || !ISO_TIME.test(raw) || !Number.isFinite(Date.parse(raw))) fail(`The ${label} was invalid.`); }
function ownedProject(raw: unknown, expected: string): void { if (identifier(raw, "projectId") !== expected) fail("The response did not belong to the active project."); }

function validateTextSpan(
  raw: unknown,
  label: string,
  maximumEnd: number
): { readonly start: number; readonly end: number } {
  const value = record(raw, label);
  exactKeys(value, ["start", "end"], label);
  const start = integerBetween(value.start, 0, maximumEnd, `${label}.start`);
  const end = integerBetween(value.end, 1, maximumEnd, `${label}.end`);
  if (end <= start) fail(`The ${label} was reversed.`);
  return { start, end };
}

type ProviderRequestDetailEvidence =
  | {
      readonly detailKind: "invocation";
      readonly providerLanguage: string;
      readonly providerVoiceId: string;
      readonly voiceRuntimeBindingId: string;
      readonly voiceRuntimeBindingFingerprint: string;
    }
  | {
      readonly detailKind: "retry";
      readonly attempt: number;
      readonly supersedesProviderRequestId: string;
      readonly originalIdempotencyKeyFingerprint: string;
    };

type ProviderRequestExecutionEvidence = (
  | {
      readonly executionClassification: "provider_execution";
      readonly providerDispatchCount: 0 | 1;
      readonly sourceProviderRequestId: null;
    }
  | {
      readonly executionClassification: "verified_cache_lookup";
      readonly providerDispatchCount: 0;
      readonly sourceProviderRequestId: string;
    }
) & ProviderRequestDetailEvidence;

function validateProviderRequestProvenance(
  raw: unknown
): ProviderRequestExecutionEvidence {
  const label = "provider request provenance";
  const value = record(raw, label);
  exactKeysWithOptional(
    value,
    [
      "origin",
      "producerId",
      "producerVersion",
      "recordedAt",
      "inputFingerprint",
      "details"
    ],
    ["reasonCode"],
    label
  );
  validateProvenanceFields(value, label);
  const details = record(value.details, `${label}.details`);
  const invocationShape = "providerLanguage" in details;
  const retryShape = "attempt" in details;
  if (invocationShape === retryShape) {
    fail("The provider request detail shape was invalid.");
  }

  let detailEvidence: ProviderRequestDetailEvidence;
  if (invocationShape) {
    exactKeysWithOptional(
      details,
      [
        "providerLanguage",
        "providerVoiceId",
        "restrictedLocalUseAcknowledged",
        "restrictedLocalUseAcknowledgementEventId",
        "voiceRuntimeBindingFingerprint",
        "voiceRuntimeBindingId",
        "executionClassification",
        "providerDispatchCount"
      ],
      ["sourceProviderRequestId"],
      `${label}.details`
    );
    const providerLanguage = boundedText(
      details.providerLanguage,
      "providerLanguage",
      32
    );
    const providerVoiceId = boundedCode(
      details.providerVoiceId,
      "providerVoiceId"
    );
    const voiceRuntimeBindingId = identifier(
      details.voiceRuntimeBindingId,
      "voiceRuntimeBindingId"
    );
    const voiceRuntimeBindingFingerprint = sha256(
      details.voiceRuntimeBindingFingerprint,
      "voiceRuntimeBindingFingerprint"
    );
    const restrictedLocalUseAcknowledged = booleanValue(
      details.restrictedLocalUseAcknowledged,
      "restrictedLocalUseAcknowledged"
    );
    if (restrictedLocalUseAcknowledged) {
      identifier(
        details.restrictedLocalUseAcknowledgementEventId,
        "restrictedLocalUseAcknowledgementEventId"
      );
    } else if (details.restrictedLocalUseAcknowledgementEventId !== null) {
      fail("The provider request claimed an unacknowledged rights event.");
    }
    detailEvidence = {
      detailKind: "invocation",
      providerLanguage,
      providerVoiceId,
      voiceRuntimeBindingId,
      voiceRuntimeBindingFingerprint
    };
  } else {
    exactKeysWithOptional(
      details,
      [
        "attempt",
        "supersedesProviderRequestId",
        "originalIdempotencyKeyFingerprint",
        "executionClassification",
        "providerDispatchCount"
      ],
      ["sourceProviderRequestId"],
      `${label}.details`
    );
    const attempt = integerBetween(details.attempt, 2, 1000, "attempt");
    const supersedesProviderRequestId = identifier(
      details.supersedesProviderRequestId,
      "supersedesProviderRequestId"
    );
    const originalIdempotencyKeyFingerprint = sha256(
      details.originalIdempotencyKeyFingerprint,
      "originalIdempotencyKeyFingerprint"
    );
    detailEvidence = {
      detailKind: "retry",
      attempt,
      supersedesProviderRequestId,
      originalIdempotencyKeyFingerprint
    };
  }

  if (details.executionClassification === "provider_execution") {
    if (
      details.providerDispatchCount !== 0 &&
      details.providerDispatchCount !== 1
    ) {
      fail("The provider dispatch count was invalid.");
    }
    if (details.sourceProviderRequestId !== undefined) {
      fail("A provider-dispatch request claimed a cache source request.");
    }
    return {
      ...detailEvidence,
      executionClassification: "provider_execution",
      providerDispatchCount: details.providerDispatchCount,
      sourceProviderRequestId: null
    };
  }
  if (details.executionClassification === "verified_cache_lookup") {
    if (details.providerDispatchCount !== 0) {
      fail("A verified cache lookup claimed a provider dispatch.");
    }
    const sourceProviderRequestId = identifier(
      details.sourceProviderRequestId,
      "sourceProviderRequestId"
    );
    return {
      ...detailEvidence,
      executionClassification: "verified_cache_lookup",
      providerDispatchCount: 0,
      sourceProviderRequestId
    };
  }
  fail("The provider request execution classification was invalid.");
}

function validateProvenance(raw: unknown, label: string): void {
  const value = record(raw, label);
  exactKeysWithOptional(
    value,
    ["origin", "producerId", "producerVersion", "recordedAt"],
    ["inputFingerprint", "reasonCode"],
    label
  );
  validateProvenanceFields(value, label);
}

function validateProvenanceFields(
  value: Record<string, unknown>,
  label: string
): void {
  enumValue(value.origin, PROVENANCE_ORIGINS, `${label}.origin`);
  boundedCode(value.producerId, `${label}.producerId`);
  boundedCode(value.producerVersion, `${label}.producerVersion`);
  timestamp(value.recordedAt, `${label}.recordedAt`);
  if (value.inputFingerprint !== undefined) {
    sha256(value.inputFingerprint, `${label}.inputFingerprint`);
  }
  if (value.reasonCode !== undefined) {
    boundedCode(value.reasonCode, `${label}.reasonCode`);
  }
}

function validateFindingCodes(raw: unknown, label: string): readonly string[] {
  const values = boundedArray(
    raw,
    label,
    SPEECH_AUDITION_LIMITS.maximumWarningsPerEntity
  );
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of values) {
    const value = boundedCode(item, `${label}[]`);
    if (seen.has(value)) fail(`The ${label} contained duplicates.`);
    seen.add(value);
    result.push(value);
  }
  return result;
}

function textSha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function fail(message: string): never {
  throw new ValidationError(message);
}
