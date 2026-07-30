import type {
  DecideImportReviewResponse,
  DeclaredImportFormat,
  DocumentExtractionSummary,
  FfmpegCapabilityResponse,
  HealthResponse,
  ImportReview,
  ImportReviewDecision,
  ImportReviewResponse,
  ImportStoryResponse,
  Job,
  ProjectDetail,
  ProjectPageResponse,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";
import type {
  ApprovalDecision,
  EntityReference,
  Provenance,
  SourceDocument
} from "@cinematic-story-studio/contracts/domain";

import {
  DESKTOP_CONTRACT_VERSION,
  type CorrectSpeakerInput,
  type CreateJobInput,
  type CreateProjectInput,
  type DecideImportReviewInput,
  type DesktopRequest,
  type ImportReviewIdInput,
  type JobEventsInput,
  type JobIdInput,
  type ProjectIdInput
} from "../shared/desktop-api.js";

const IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u;
const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const MAX_COLLECTION_LENGTH = 20_000;
const MAX_IMPORT_PREVIEW_LENGTH = 64 * 1024;

export interface ImportStoryResponseExpectation {
  readonly projectId: string;
  readonly declaredFormat: DeclaredImportFormat;
  readonly sourceSha256: string;
  readonly sourceByteCount: number;
}

export class ValidationError extends Error {
  readonly code = "INVALID_DESKTOP_REQUEST";

  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

export function parseEmptyRequest(value: unknown): DesktopRequest<object> {
  const payload = parseEnvelope(value);
  if (Object.keys(payload).length !== 0) {
    throw new ValidationError("This operation does not accept fields.");
  }
  return { contractVersion: DESKTOP_CONTRACT_VERSION, payload };
}

export function parseProjectIdRequest(
  value: unknown
): DesktopRequest<ProjectIdInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, ["projectId"]);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: { projectId: parseIdentifier(payload.projectId, "projectId") }
  };
}

export function parseImportReviewIdRequest(
  value: unknown
): DesktopRequest<ImportReviewIdInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, [
    "projectId",
    "reviewId",
    "sourceDocumentId",
    "extractionId",
    "candidateStoryId",
    "candidateStoryRevision",
    "evidenceFingerprint"
  ]);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      projectId: parseIdentifier(payload.projectId, "projectId"),
      reviewId: parseIdentifier(payload.reviewId, "reviewId"),
      sourceDocumentId: parseIdentifier(
        payload.sourceDocumentId,
        "sourceDocumentId"
      ),
      extractionId: parseIdentifier(payload.extractionId, "extractionId"),
      candidateStoryId: parseIdentifier(
        payload.candidateStoryId,
        "candidateStoryId"
      ),
      candidateStoryRevision: parsePositiveInteger(
        payload.candidateStoryRevision,
        "candidateStoryRevision"
      ),
      evidenceFingerprint: parseSha256(
        payload.evidenceFingerprint,
        "evidenceFingerprint"
      )
    }
  };
}

export function parseDecideImportReviewRequest(
  value: unknown
): DesktopRequest<DecideImportReviewInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, [
    "projectId",
    "reviewId",
    "sourceDocumentId",
    "extractionId",
    "candidateStoryId",
    "candidateStoryRevision",
    "decision",
    "rationale",
    "expectedRevision",
    "evidenceFingerprint",
    "idempotencyKey"
  ]);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      projectId: parseIdentifier(payload.projectId, "projectId"),
      reviewId: parseIdentifier(payload.reviewId, "reviewId"),
      sourceDocumentId: parseIdentifier(
        payload.sourceDocumentId,
        "sourceDocumentId"
      ),
      extractionId: parseIdentifier(payload.extractionId, "extractionId"),
      candidateStoryId: parseIdentifier(
        payload.candidateStoryId,
        "candidateStoryId"
      ),
      candidateStoryRevision: parsePositiveInteger(
        payload.candidateStoryRevision,
        "candidateStoryRevision"
      ),
      decision: parseImportReviewDecision(payload.decision),
      rationale:
        payload.rationale === undefined
          ? undefined
          : parseBoundedString(payload.rationale, "rationale", 1, 2_000),
      expectedRevision: parsePositiveInteger(
        payload.expectedRevision,
        "expectedRevision"
      ),
      evidenceFingerprint: parseSha256(
        payload.evidenceFingerprint,
        "evidenceFingerprint"
      ),
      idempotencyKey: parseIdempotencyKey(payload.idempotencyKey)
    }
  };
}

export function parseCreateProjectRequest(
  value: unknown
): DesktopRequest<CreateProjectInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, ["name", "idempotencyKey"]);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      name: parseBoundedString(payload.name, "name", 1, 120),
      idempotencyKey: parseIdempotencyKey(payload.idempotencyKey)
    }
  };
}

export function parseCorrectSpeakerRequest(
  value: unknown
): DesktopRequest<CorrectSpeakerInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, [
    "projectId",
    "lineId",
    "characterId",
    "reason",
    "expectedRevision"
  ]);
  const characterId =
    payload.characterId === null
      ? null
      : parseIdentifier(payload.characterId, "characterId");
  const reason =
    payload.reason === undefined
      ? undefined
      : parseBoundedString(payload.reason, "reason", 1, 500);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      projectId: parseIdentifier(payload.projectId, "projectId"),
      lineId: parseIdentifier(payload.lineId, "lineId"),
      characterId,
      reason,
      expectedRevision: parseNonNegativeInteger(
        payload.expectedRevision,
        "expectedRevision"
      )
    }
  };
}

export function parseCreateJobRequest(
  value: unknown
): DesktopRequest<CreateJobInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, [
    "projectId",
    "type",
    "inputRevision",
    "idempotencyKey"
  ]);
  if (payload.type !== "analyze_story") {
    throw new ValidationError("Unsupported job type.");
  }
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      projectId: parseIdentifier(payload.projectId, "projectId"),
      type: payload.type,
      inputRevision: parseNonNegativeInteger(
        payload.inputRevision,
        "inputRevision"
      ),
      idempotencyKey: parseIdempotencyKey(payload.idempotencyKey)
    }
  };
}

export function parseJobIdRequest(value: unknown): DesktopRequest<JobIdInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, ["jobId"]);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: { jobId: parseIdentifier(payload.jobId, "jobId") }
  };
}

export function parseJobEventsRequest(
  value: unknown
): DesktopRequest<JobEventsInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, ["jobId", "afterSequence"]);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      jobId: parseIdentifier(payload.jobId, "jobId"),
      afterSequence:
        payload.afterSequence === undefined
          ? undefined
          : parseNonNegativeInteger(payload.afterSequence, "afterSequence")
    }
  };
}

export function parseIdentifier(value: unknown, field: string): string {
  if (typeof value !== "string" || !IDENTIFIER_PATTERN.test(value)) {
    throw new ValidationError(`${field} is not a valid opaque identifier.`);
  }
  return value;
}

export function parseReadyLine(
  line: string,
  expectedNonce: string
): { port: number; instanceId: string } {
  if (!line.startsWith("CSS_READY ") || line.length > 4_096) {
    throw new ValidationError("The service readiness record is invalid.");
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(line.slice("CSS_READY ".length));
  } catch {
    throw new ValidationError("The service readiness record is not JSON.");
  }
  const record = expectRecord(decoded, "readiness record");
  rejectUnknownFields(record, [
    "port",
    "instanceId",
    "nonce",
    "protocolVersion"
  ]);
  const port = parsePositiveInteger(record.port, "port");
  if (port > 65_535) {
    throw new ValidationError("The service readiness port is out of range.");
  }
  const instanceId = parseIdentifier(record.instanceId, "instanceId");
  if (
    typeof record.nonce !== "string" ||
    record.nonce.length > 128 ||
    record.nonce !== expectedNonce
  ) {
    throw new ValidationError("The service readiness nonce did not match.");
  }
  if (record.protocolVersion !== DESKTOP_CONTRACT_VERSION) {
    throw new ValidationError("The service protocol version is incompatible.");
  }
  return { port, instanceId };
}

export function validateHealthResponse(value: unknown): HealthResponse {
  const response = expectRecord(value, "health response");
  if (
    response.status !== "starting" &&
    response.status !== "ready" &&
    response.status !== "degraded"
  ) {
    throw new ValidationError("Health response status is invalid.");
  }
  if (response.contractVersion !== DESKTOP_CONTRACT_VERSION) {
    throw new ValidationError("Service contract version is incompatible.");
  }
  parseIdentifier(response.instanceId, "instanceId");
  parseIdentifier(response.correlationId, "correlationId");
  parseBoundedString(response.serviceVersion, "serviceVersion", 1, 80);
  parseIsoDate(response.checkedAt, "checkedAt");
  const database = expectRecord(response.database, "database status");
  if (
    database.status !== "starting" &&
    database.status !== "ready" &&
    database.status !== "degraded" &&
    database.status !== "unavailable"
  ) {
    throw new ValidationError("Database health status is invalid.");
  }
  return value as HealthResponse;
}

export function validateProjectPageResponse(
  value: unknown
): ProjectPageResponse {
  const response = expectRecord(value, "project list response");
  parseIdentifier(response.correlationId, "correlationId");
  const items = parseBoundedArray(response.items, "projects", 1_000);
  for (const item of items) {
    const project = expectRecord(item, "project summary");
    parseIdentifier(project.projectId, "projectId");
    parseBoundedString(project.name, "name", 1, 120);
    parseNonNegativeInteger(project.revision, "revision");
  }
  if (response.nextCursor !== undefined) {
    parseBoundedString(response.nextCursor, "nextCursor", 1, 512);
  }
  return value as ProjectPageResponse;
}

export function validateProjectDetail(
  value: unknown,
  expectedProjectId: string
): ProjectDetail {
  const response = expectRecord(value, "project detail response");
  parseIdentifier(response.correlationId, "correlationId");
  const project = expectRecord(response.project, "project");
  const projectId = parseIdentifier(project.projectId, "projectId");
  if (projectId !== expectedProjectId) {
    throw new ValidationError(
      "The project response did not match the requested project."
    );
  }
  parseBoundedString(project.name, "name", 1, 120);
  parseNonNegativeInteger(project.revision, "revision");
  if (typeof response.analysisAllowed !== "boolean") {
    throw new ValidationError("analysisAllowed must be a boolean.");
  }
  for (const field of [
    "sourceDocuments",
    "extractions",
    "importReviews",
    "chapters",
    "scenes",
    "beats",
    "characters",
    "dialogueLines",
    "dialogueAttributions",
    "castingAssignments",
    "castingPlaceholders",
    "approvals",
    "jobs"
  ]) {
    parseBoundedArray(response[field], field, MAX_COLLECTION_LENGTH);
  }
  const sourceById = new Map<string, SourceDocument>();
  const sourceRevisionIds = new Set<number>();
  for (const value of response.sourceDocuments as readonly unknown[]) {
    const source = validateSourceDocument(value);
    if (
      source.projectId !== projectId ||
      sourceById.has(source.documentId) ||
      sourceRevisionIds.has(source.sourceRevision)
    ) {
      throw new ValidationError(
        "Source document project identity is inconsistent."
      );
    }
    sourceById.set(source.documentId, source);
    sourceRevisionIds.add(source.sourceRevision);
  }
  for (const source of sourceById.values()) {
    if (source.supersedesDocumentId === undefined) {
      continue;
    }
    const superseded = sourceById.get(source.supersedesDocumentId);
    if (
      superseded === undefined ||
      superseded.documentId === source.documentId ||
      superseded.projectId !== source.projectId ||
      superseded.sourceRevision >= source.sourceRevision
    ) {
      throw new ValidationError(
        "Source document revision ancestry is inconsistent."
      );
    }
  }
  const extractionById = new Map<string, DocumentExtractionSummary>();
  for (const extraction of response.extractions as readonly unknown[]) {
    const parsed = validateDocumentExtractionSummary(extraction);
    const source = sourceById.get(parsed.sourceDocumentId);
    if (
      parsed.projectId !== projectId ||
      source === undefined ||
      source.projectId !== parsed.projectId ||
      source.declaredFormat !== parsed.declaredFormat ||
      source.mediaType !== parsed.mediaType ||
      source.contentSha256 !== parsed.sourceSha256 ||
      source.byteLength !== parsed.sourceByteCount
    ) {
      throw new ValidationError(
        "Document extraction source identity is inconsistent."
      );
    }
    extractionById.set(parsed.extractionId, parsed);
  }
  const reviews: ImportReview[] = [];
  for (const review of response.importReviews as readonly unknown[]) {
    const parsed = validateImportReview(review);
    const extraction = extractionById.get(parsed.extractionId);
    if (
      parsed.projectId !== projectId ||
      extraction === undefined ||
      parsed.sourceDocumentId !== extraction.sourceDocumentId
    ) {
      throw new ValidationError(
        "Import review extraction identity is inconsistent."
      );
    }
    reviews.push(parsed);
  }
  const approvals = response.approvals as readonly unknown[];
  for (const approval of approvals) {
    const parsed = validateApprovalDecision(approval);
    if (parsed.projectId !== projectId) {
      throw new ValidationError(
        "Approval decision project identity is inconsistent."
      );
    }
  }
  if (response.analysisAllowed) {
    const story = expectRecord(response.story, "story");
    const storyId = parseIdentifier(story.storyId, "storyId");
    const storyRevision = parsePositiveInteger(story.revision, "story revision");
    if (
      !reviews.some(
        (review) =>
          review.candidateStoryId === storyId &&
          review.candidateStoryRevision === storyRevision &&
          review.state === "approved" &&
          review.latestDecision?.decision === "approved"
      )
    ) {
      throw new ValidationError(
        "Analysis cannot be allowed without an approved current import."
      );
    }
  }
  return value as ProjectDetail;
}

export function validateImportStoryResponse(
  value: unknown,
  expected: ImportStoryResponseExpectation
): ImportStoryResponse {
  const response = expectRecord(value, "import response");
  parseIdentifier(response.correlationId, "correlationId");
  const source = validateSourceDocument(response.sourceDocument);
  const extraction = validateDocumentExtractionSummary(response.extraction);
  const job = validateJob(response.job);
  if (
    source.projectId !== expected.projectId ||
    source.contentSha256 !== expected.sourceSha256 ||
    source.byteLength !== expected.sourceByteCount ||
    source.declaredFormat !== expected.declaredFormat ||
    extraction.projectId !== source.projectId ||
    extraction.sourceDocumentId !== source.documentId ||
    extraction.declaredFormat !== source.declaredFormat ||
    extraction.detectedFormat !== expected.declaredFormat ||
    source.mediaType !== importMediaType(expected.declaredFormat) ||
    extraction.mediaType !== source.mediaType ||
    extraction.sourceSha256 !== source.contentSha256 ||
    extraction.sourceByteCount !== source.byteLength ||
    job.projectId !== expected.projectId ||
    job.type !== "extract_document" ||
    job.inputRevision !== extraction.revision ||
    job.inputFingerprint !== extraction.sourceSha256
  ) {
    throw new ValidationError(
      "The import response source identity is inconsistent."
    );
  }
  return value as ImportStoryResponse;
}

export function validateImportReviewResponse(
  value: unknown,
  expected: ImportReviewIdInput
): ImportReviewResponse {
  const response = expectRecord(value, "import review response");
  parseIdentifier(response.correlationId, "correlationId");
  const review = validateImportReview(response.review);
  assertReviewIdentity(review, expected);
  return value as ImportReviewResponse;
}

export function validateDecideImportReviewResponse(
  value: unknown,
  expected: DecideImportReviewInput
): DecideImportReviewResponse {
  const response = expectRecord(value, "import review decision response");
  parseIdentifier(response.correlationId, "correlationId");
  const review = validateImportReview(response.review);
  assertReviewIdentity(review, expected);
  const decision = validateApprovalDecision(response.decision);
  parsePositiveInteger(response.projectRevision, "projectRevision");
  if (typeof response.analysisAllowed !== "boolean") {
    throw new ValidationError("analysisAllowed must be a boolean.");
  }
  const latestDecision = review.latestDecision;
  if (
    review.revision !== expected.expectedRevision + 1 ||
    review.state !== expected.decision ||
    review.evidenceFingerprint !== expected.evidenceFingerprint ||
    decision.projectId !== expected.projectId ||
    decision.gateId !== "import_review" ||
    decision.scope.entityType !== "DocumentExtraction" ||
    decision.scope.entityId !== expected.extractionId ||
    decision.scope.revision === undefined ||
    decision.decision !== expected.decision ||
    decision.evidenceFingerprint !== expected.evidenceFingerprint ||
    decision.revision !== review.revision ||
    decision.actor.type !== "human" ||
    latestDecision === undefined ||
    latestDecision.decisionId !== decision.decisionId ||
    latestDecision.revision !== decision.revision ||
    latestDecision.projectId !== decision.projectId ||
    latestDecision.gateId !== decision.gateId ||
    latestDecision.scope.entityType !== decision.scope.entityType ||
    latestDecision.scope.entityId !== decision.scope.entityId ||
    latestDecision.scope.revision !== decision.scope.revision ||
    latestDecision.decision !== decision.decision ||
    latestDecision.actor.type !== decision.actor.type ||
    latestDecision.actor.actorId !== decision.actor.actorId ||
    latestDecision.rationale !== decision.rationale ||
    latestDecision.evidenceFingerprint !== decision.evidenceFingerprint ||
    latestDecision.decidedAt !== decision.decidedAt ||
    response.analysisAllowed !== (expected.decision === "approved") ||
    (expected.rationale !== undefined &&
      decision.rationale !== expected.rationale)
  ) {
    throw new ValidationError(
      "The import review decision evidence is inconsistent."
    );
  }
  return value as DecideImportReviewResponse;
}

export function validateProviderHealthResponse(
  value: unknown
): ProviderHealthResponse {
  const response = expectRecord(value, "provider health response");
  parseIdentifier(response.correlationId, "correlationId");
  const providers = parseBoundedArray(response.providers, "providers", 100);
  for (const item of providers) {
    const provider = expectRecord(item, "provider health");
    parseBoundedString(provider.providerId, "providerId", 1, 128);
    parseBoundedArray(provider.capabilities, "capabilities", 100);
    if (provider.redactedReason !== undefined) {
      parseBoundedString(
        provider.redactedReason,
        "redactedReason",
        1,
        500
      );
    }
  }
  return value as ProviderHealthResponse;
}

export function validateFfmpegCapabilityResponse(
  value: unknown
): FfmpegCapabilityResponse {
  const response = expectRecord(value, "FFmpeg capability response");
  parseIdentifier(response.correlationId, "correlationId");
  if (
    response.status !== "available" &&
    response.status !== "missing" &&
    response.status !== "incompatible" &&
    response.status !== "failed"
  ) {
    throw new ValidationError("FFmpeg capability status is invalid.");
  }
  parseBoundedArray(response.capabilities, "capabilities", 100);
  parseBoundedArray(response.missingCapabilities, "missingCapabilities", 100);
  return value as FfmpegCapabilityResponse;
}

function validateSourceDocument(value: unknown): SourceDocument {
  const source = expectRecord(value, "source document");
  rejectUnknownResponseFields(source, "source document", [
    "schemaVersion",
    "revision",
    "provenance",
    "documentId",
    "projectId",
    "displayName",
    "mediaType",
    "declaredFormat",
    "contentSha256",
    "byteLength",
    "importedAt",
    "originalTextPreserved",
    "originalBytesPreserved",
    "storageKey",
    "extractionStatus",
    "sourceRevision",
    "supersedesDocumentId",
    "textSha256",
    "encoding",
    "newlineStyle",
    "warnings"
  ]);
  validateVersionedEntity(source, "source document");
  parseIdentifier(source.documentId, "documentId");
  parseIdentifier(source.projectId, "source projectId");
  parseBoundedString(source.displayName, "displayName", 1, 260);
  validateSourceMediaType(source.mediaType, "source mediaType");
  if (
    !isImportFormat(source.declaredFormat) ||
    source.mediaType !== importMediaType(source.declaredFormat)
  ) {
    throw new ValidationError("Source document format is invalid.");
  }
  parseSha256(source.contentSha256, "contentSha256");
  parsePositiveInteger(source.byteLength, "byteLength");
  parseIsoDate(source.importedAt, "importedAt");
  if (source.originalTextPreserved !== true) {
    throw new ValidationError("Source text preservation state is invalid.");
  }
  if (source.originalBytesPreserved !== true) {
    throw new ValidationError("Source byte preservation state is invalid.");
  }
  parseStorageKey(source.storageKey);
  if (
    source.extractionStatus !== "pending" &&
    source.extractionStatus !== "running" &&
    source.extractionStatus !== "complete" &&
    source.extractionStatus !== "partial" &&
    source.extractionStatus !== "failed"
  ) {
    throw new ValidationError("Source extraction status is invalid.");
  }
  parsePositiveInteger(source.sourceRevision, "sourceRevision");
  if (source.supersedesDocumentId !== undefined) {
    const supersedesDocumentId = parseIdentifier(
      source.supersedesDocumentId,
      "supersedesDocumentId"
    );
    if (supersedesDocumentId === source.documentId) {
      throw new ValidationError(
        "A source document cannot supersede itself."
      );
    }
  }
  if (source.textSha256 !== undefined) {
    parseSha256(source.textSha256, "textSha256");
  }
  if (source.encoding !== undefined) {
    parseBoundedString(source.encoding, "source encoding", 1, 24);
  }
  if (
    source.newlineStyle !== undefined &&
    source.newlineStyle !== "none" &&
    source.newlineStyle !== "mixed" &&
    source.newlineStyle !== "crlf" &&
    source.newlineStyle !== "lf" &&
    source.newlineStyle !== "cr"
  ) {
    throw new ValidationError("Source newline style is invalid.");
  }
  const warnings = parseBoundedArray(
    source.warnings,
    "source warnings",
    1_000
  );
  for (const warning of warnings) {
    validateContractWarning(warning);
  }
  return value as SourceDocument;
}

function validateDocumentExtractionSummary(
  value: unknown
): DocumentExtractionSummary {
  const extraction = expectRecord(value, "document extraction");
  validateVersionedEntity(extraction, "document extraction");
  parseIdentifier(extraction.extractionId, "extractionId");
  parseIdentifier(extraction.projectId, "projectId");
  parseIdentifier(extraction.sourceDocumentId, "sourceDocumentId");
  if (
    !isImportFormat(extraction.declaredFormat) ||
    !isImportFormat(extraction.detectedFormat)
  ) {
    throw new ValidationError("Document extraction format is invalid.");
  }
  validateSourceMediaType(extraction.mediaType, "extraction mediaType");
  if (
    extraction.status !== "pending" &&
    extraction.status !== "running" &&
    extraction.status !== "complete" &&
    extraction.status !== "partial" &&
    extraction.status !== "failed"
  ) {
    throw new ValidationError("Document extraction status is invalid.");
  }
  parseBoundedString(extraction.adapterId, "adapterId", 1, 128);
  parseBoundedString(
    extraction.adapterVersion,
    "adapterVersion",
    1,
    80
  );
  parseBoundedString(
    extraction.parserDependency,
    "parserDependency",
    1,
    128
  );
  parseBoundedString(extraction.parserVersion, "parserVersion", 1, 80);
  parseSha256(extraction.sourceSha256, "sourceSha256");
  parsePositiveInteger(extraction.sourceByteCount, "sourceByteCount");
  if (extraction.extractedTitle !== undefined) {
    parseBoundedString(
      extraction.extractedTitle,
      "extractedTitle",
      1,
      300
    );
  }
  if (extraction.extractedTextSha256 !== undefined) {
    parseSha256(extraction.extractedTextSha256, "extractedTextSha256");
  }
  if (extraction.extractedCharacterCount !== undefined) {
    parseNonNegativeInteger(
      extraction.extractedCharacterCount,
      "extractedCharacterCount"
    );
  }
  if (extraction.sectionCount !== undefined) {
    parseNonNegativeInteger(extraction.sectionCount, "sectionCount");
  }
  if (extraction.pageCount !== undefined) {
    parsePositiveInteger(extraction.pageCount, "pageCount");
  }
  const warnings = parseBoundedArray(
    extraction.warnings,
    "extraction warnings",
    1_000
  );
  for (const warning of warnings) {
    validateContractWarning(warning);
  }
  const quality = expectRecord(extraction.quality, "extraction quality");
  if (
    quality.classification !== "pending" &&
    quality.classification !== "exact_text_decode" &&
    quality.classification !== "structured_extraction" &&
    quality.classification !== "page_text_extraction" &&
    quality.classification !== "low_text_density" &&
    quality.classification !== "review_required"
  ) {
    throw new ValidationError("Extraction quality classification is invalid.");
  }
  if (
    typeof quality.confidence !== "number" ||
    !Number.isFinite(quality.confidence) ||
    quality.confidence < 0 ||
    quality.confidence > 1
  ) {
    throw new ValidationError("Extraction quality confidence is invalid.");
  }
  if (
    extraction.retryability !== "retryable" &&
    extraction.retryability !== "not_retryable"
  ) {
    throw new ValidationError("Extraction retryability is invalid.");
  }
  if (
    typeof extraction.reviewRequired !== "boolean" ||
    extraction.originalPreserved !== true
  ) {
    throw new ValidationError("Extraction review state is invalid.");
  }
  parseIsoDate(extraction.createdAt, "createdAt");
  parseIsoDate(extraction.updatedAt, "updatedAt");
  if (extraction.completedAt !== undefined) {
    parseIsoDate(extraction.completedAt, "completedAt");
  }
  return value as DocumentExtractionSummary;
}

function validateImportReview(value: unknown): ImportReview {
  const review = expectRecord(value, "import review");
  validateVersionedEntity(review, "import review");
  const reviewRevision = parsePositiveInteger(
    review.revision,
    "review revision"
  );
  parseIdentifier(review.reviewId, "reviewId");
  parseIdentifier(review.projectId, "projectId");
  parseIdentifier(review.sourceDocumentId, "sourceDocumentId");
  parseIdentifier(review.extractionId, "extractionId");
  parseIdentifier(review.candidateStoryId, "candidateStoryId");
  parsePositiveInteger(
    review.candidateStoryRevision,
    "candidateStoryRevision"
  );
  if (
    review.state !== "pending" &&
    review.state !== "approved" &&
    review.state !== "changes_requested" &&
    review.state !== "rejected" &&
    review.state !== "invalidated"
  ) {
    throw new ValidationError("Import review state is invalid.");
  }
  parseSha256(review.evidenceFingerprint, "evidenceFingerprint");
  if (
    typeof review.previewText !== "string" ||
    review.previewText.length > MAX_IMPORT_PREVIEW_LENGTH ||
    Buffer.byteLength(review.previewText, "utf8") >
      MAX_IMPORT_PREVIEW_LENGTH * 4
  ) {
    throw new ValidationError("Import review preview is invalid.");
  }
  if (typeof review.previewTruncated !== "boolean") {
    throw new ValidationError("previewTruncated must be a boolean.");
  }
  const warnings = parseBoundedArray(
    review.warnings,
    "review warnings",
    1_000
  );
  for (const warning of warnings) {
    validateContractWarning(warning);
  }
  if (review.latestDecision !== undefined) {
    const decision = validateApprovalDecision(review.latestDecision);
    if (
      decision.projectId !== review.projectId ||
      decision.gateId !== "import_review" ||
      decision.scope.entityType !== "DocumentExtraction" ||
      decision.scope.entityId !== review.extractionId ||
      decision.evidenceFingerprint !== review.evidenceFingerprint ||
      decision.revision > reviewRevision ||
      (review.state === "approved" && decision.decision !== "approved") ||
      (review.state === "changes_requested" &&
        decision.decision !== "changes_requested") ||
      (review.state === "rejected" && decision.decision !== "rejected") ||
      (review.state === "invalidated" && decision.decision !== "revoked")
    ) {
      throw new ValidationError(
        "Import review approval evidence is inconsistent."
      );
    }
  } else if (
    review.state === "approved" ||
    review.state === "changes_requested" ||
    review.state === "rejected"
  ) {
    throw new ValidationError(
      "A decided import review must include its approval evidence."
    );
  }
  parseIsoDate(review.createdAt, "createdAt");
  parseIsoDate(review.updatedAt, "updatedAt");
  return value as ImportReview;
}

function validateContractWarning(value: unknown): void {
  const warning = expectRecord(value, "contract warning");
  parseBoundedString(warning.code, "warning code", 1, 80);
  parseBoundedString(warning.message, "warning message", 1, 1_000);
  if (
    warning.severity !== "info" &&
    warning.severity !== "warning" &&
    warning.severity !== "error"
  ) {
    throw new ValidationError("Warning severity is invalid.");
  }
  if (typeof warning.requiresHumanReview !== "boolean") {
    throw new ValidationError("Warning review state is invalid.");
  }
  if (warning.relatedEntities !== undefined) {
    const related = parseBoundedArray(
      warning.relatedEntities,
      "warning relatedEntities",
      100
    );
    for (const reference of related) {
      validateEntityReference(reference, "warning related entity");
    }
  }
}

function validateApprovalDecision(value: unknown): ApprovalDecision {
  const decision = expectRecord(value, "approval decision");
  validateVersionedEntity(decision, "approval decision");
  parseIdentifier(decision.decisionId, "decisionId");
  parseIdentifier(decision.projectId, "approval projectId");
  if (
    decision.gateId !== "import_review" &&
    decision.gateId !== "scene_segmentation_review" &&
    decision.gateId !== "character_review" &&
    decision.gateId !== "dialogue_attribution_review" &&
    decision.gateId !== "casting_approval" &&
    decision.gateId !== "performance_direction_approval" &&
    decision.gateId !== "sound_design_approval" &&
    decision.gateId !== "first_scene_render_approval" &&
    decision.gateId !== "chapter_approval" &&
    decision.gateId !== "final_master_approval"
  ) {
    throw new ValidationError("Approval gate is invalid.");
  }
  validateEntityReference(decision.scope, "approval scope");
  if (
    decision.decision !== "pending" &&
    decision.decision !== "approved" &&
    decision.decision !== "changes_requested" &&
    decision.decision !== "rejected" &&
    decision.decision !== "revoked"
  ) {
    throw new ValidationError("Approval decision is invalid.");
  }
  const actor = expectRecord(decision.actor, "approval actor");
  if (actor.type !== "human" && actor.type !== "system") {
    throw new ValidationError("Approval actor type is invalid.");
  }
  parseBoundedString(actor.actorId, "approval actorId", 1, 128);
  parseBoundedString(decision.rationale, "approval rationale", 0, 2_000);
  parseSha256(decision.evidenceFingerprint, "evidenceFingerprint");
  parseIsoDate(decision.decidedAt, "decidedAt");
  if (decision.immutable !== true) {
    throw new ValidationError("Approval decision must be immutable.");
  }
  if (decision.supersedesDecisionId !== undefined) {
    parseIdentifier(
      decision.supersedesDecisionId,
      "supersedesDecisionId"
    );
  }
  if (decision.invalidatesEntityRevisions !== undefined) {
    const invalidated = parseBoundedArray(
      decision.invalidatesEntityRevisions,
      "invalidatesEntityRevisions",
      1_000
    );
    for (const reference of invalidated) {
      validateEntityReference(reference, "invalidated entity");
    }
  }
  return value as ApprovalDecision;
}

function validateJob(value: unknown): Job {
  const job = expectRecord(value, "job");
  parseIdentifier(job.jobId, "jobId");
  parseIdentifier(job.projectId, "projectId");
  if (job.type !== "extract_document" && job.type !== "analyze_story") {
    throw new ValidationError("Job type is invalid.");
  }
  if (
    job.state !== "queued" &&
    job.state !== "running" &&
    job.state !== "cancel_requested" &&
    job.state !== "cancelled" &&
    job.state !== "failed" &&
    job.state !== "interrupted" &&
    job.state !== "paused" &&
    job.state !== "succeeded"
  ) {
    throw new ValidationError("Job state is invalid.");
  }
  parseNonNegativeInteger(job.inputRevision, "inputRevision");
  parseSha256(job.inputFingerprint, "inputFingerprint");
  parsePositiveInteger(job.attempt, "attempt");
  parseBoundedString(job.stage, "stage", 1, 128);
  if (
    typeof job.progress !== "number" ||
    !Number.isFinite(job.progress) ||
    job.progress < 0 ||
    job.progress > 1
  ) {
    throw new ValidationError("Job progress is invalid.");
  }
  if (
    typeof job.checkpointAvailable !== "boolean" ||
    typeof job.cancellationRequested !== "boolean"
  ) {
    throw new ValidationError("Job control state is invalid.");
  }
  parseBoundedArray(job.warnings, "job warnings", 1_000);
  parseIsoDate(job.createdAt, "createdAt");
  parseIsoDate(job.updatedAt, "updatedAt");
  return value as Job;
}

function assertReviewIdentity(
  review: ImportReview,
  expected: ImportReviewIdInput
): void {
  if (
    review.projectId !== expected.projectId ||
    review.reviewId !== expected.reviewId ||
    review.sourceDocumentId !== expected.sourceDocumentId ||
    review.extractionId !== expected.extractionId ||
    review.candidateStoryId !== expected.candidateStoryId ||
    review.candidateStoryRevision !== expected.candidateStoryRevision ||
    review.evidenceFingerprint !== expected.evidenceFingerprint
  ) {
    throw new ValidationError(
      "The import review did not match the requested evidence."
    );
  }
}

function validateVersionedEntity(
  value: Record<string, unknown>,
  field: string
): void {
  if (value.schemaVersion !== DESKTOP_CONTRACT_VERSION) {
    throw new ValidationError(`${field} schemaVersion is incompatible.`);
  }
  parsePositiveInteger(value.revision, `${field} revision`);
  validateProvenance(value.provenance, `${field} provenance`);
}

function validateProvenance(value: unknown, field: string): Provenance {
  const provenance = expectRecord(value, field);
  if (
    provenance.origin !== "import" &&
    provenance.origin !== "runtime_agent" &&
    provenance.origin !== "human" &&
    provenance.origin !== "system"
  ) {
    throw new ValidationError(`${field} origin is invalid.`);
  }
  parseIsoDate(provenance.recordedAt, `${field} recordedAt`);
  parseBoundedString(provenance.actorId, `${field} actorId`, 1, 128);
  if (provenance.agentExecutionId !== undefined) {
    parseIdentifier(
      provenance.agentExecutionId,
      `${field} agentExecutionId`
    );
  }
  if (provenance.sourceReferences !== undefined) {
    const references = parseBoundedArray(
      provenance.sourceReferences,
      `${field} sourceReferences`,
      1_000
    );
    for (const reference of references) {
      validateEntityReference(reference, `${field} source reference`);
    }
  }
  if (provenance.inputFingerprint !== undefined) {
    parseSha256(
      provenance.inputFingerprint,
      `${field} inputFingerprint`
    );
  }
  if (provenance.notes !== undefined) {
    parseBoundedString(provenance.notes, `${field} notes`, 0, 4_000);
  }
  return value as Provenance;
}

function validateEntityReference(
  value: unknown,
  field: string
): EntityReference {
  const reference = expectRecord(value, field);
  parseBoundedString(reference.entityType, `${field} entityType`, 1, 128);
  parseIdentifier(reference.entityId, `${field} entityId`);
  if (reference.revision !== undefined) {
    parsePositiveInteger(reference.revision, `${field} revision`);
  }
  return value as EntityReference;
}

function validateSourceMediaType(value: unknown, field: string): void {
  if (
    value !== "text/plain" &&
    value !== "text/markdown" &&
    value !==
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document" &&
    value !== "application/epub+zip" &&
    value !== "application/pdf"
  ) {
    throw new ValidationError(`${field} is invalid.`);
  }
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

function parseStorageKey(value: unknown): string {
  const storageKey = parseBoundedString(value, "storageKey", 1, 512);
  if (
    storageKey.startsWith("/") ||
    storageKey.startsWith("\\") ||
    storageKey.includes("\\") ||
    /^[A-Za-z]:/u.test(storageKey) ||
    storageKey.split("/").some((part) => part === "" || part === "..")
  ) {
    throw new ValidationError("storageKey must be a relative managed key.");
  }
  return storageKey;
}

function parseEnvelope(value: unknown): Record<string, unknown> {
  const envelope = expectRecord(value, "desktop request");
  rejectUnknownFields(envelope, ["contractVersion", "payload"]);
  if (envelope.contractVersion !== DESKTOP_CONTRACT_VERSION) {
    throw new ValidationError("Desktop contract version is incompatible.");
  }
  return expectRecord(envelope.payload, "desktop request payload");
}

function expectRecord(
  value: unknown,
  field: string
): Record<string, unknown> {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    throw new ValidationError(`${field} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function rejectUnknownFields(
  value: Record<string, unknown>,
  allowedFields: readonly string[]
): void {
  const allowed = new Set(allowedFields);
  if (Object.keys(value).some((field) => !allowed.has(field))) {
    throw new ValidationError("The request contained an unknown field.");
  }
}

function rejectUnknownResponseFields(
  value: Record<string, unknown>,
  field: string,
  allowedFields: readonly string[]
): void {
  const allowed = new Set(allowedFields);
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new ValidationError(`${field} contained an unknown field.`);
  }
}

function parseBoundedString(
  value: unknown,
  field: string,
  minimumLength: number,
  maximumLength: number
): string {
  if (typeof value !== "string") {
    throw new ValidationError(`${field} must be a string.`);
  }
  const trimmed = value.trim();
  if (
    trimmed.length < minimumLength ||
    trimmed.length > maximumLength ||
    Buffer.byteLength(trimmed, "utf8") > maximumLength * 4
  ) {
    throw new ValidationError(`${field} has an invalid length.`);
  }
  return trimmed;
}

function parseIdempotencyKey(value: unknown): string {
  return parseIdentifier(value, "idempotencyKey");
}

function parseImportReviewDecision(value: unknown): ImportReviewDecision {
  if (
    value !== "approved" &&
    value !== "changes_requested" &&
    value !== "rejected"
  ) {
    throw new ValidationError("Unsupported import review decision.");
  }
  return value;
}

function isImportFormat(
  value: unknown
): value is "txt" | "markdown" | "docx" | "epub" | "pdf" {
  return (
    value === "txt" ||
    value === "markdown" ||
    value === "docx" ||
    value === "epub" ||
    value === "pdf"
  );
}

function parseSha256(value: unknown, field: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    throw new ValidationError(`${field} must be a lowercase SHA-256 digest.`);
  }
  return value;
}

function parseNonNegativeInteger(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new ValidationError(`${field} must be a non-negative integer.`);
  }
  return value as number;
}

function parsePositiveInteger(value: unknown, field: string): number {
  const parsed = parseNonNegativeInteger(value, field);
  if (parsed === 0) {
    throw new ValidationError(`${field} must be greater than zero.`);
  }
  return parsed;
}

function parseBoundedArray(
  value: unknown,
  field: string,
  maximumLength: number
): readonly unknown[] {
  if (!Array.isArray(value) || value.length > maximumLength) {
    throw new ValidationError(`${field} must be a bounded collection.`);
  }
  return value;
}

function parseIsoDate(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.length > 40 ||
    !Number.isFinite(Date.parse(value))
  ) {
    throw new ValidationError(`${field} must be an ISO date-time.`);
  }
  return value;
}
