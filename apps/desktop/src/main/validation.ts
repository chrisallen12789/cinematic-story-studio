import type {
  FfmpegCapabilityResponse,
  HealthResponse,
  ProjectDetail,
  ProjectPageResponse,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";

import {
  DESKTOP_CONTRACT_VERSION,
  type CorrectSpeakerInput,
  type CreateJobInput,
  type CreateProjectInput,
  type DesktopRequest,
  type JobEventsInput,
  type JobIdInput,
  type ProjectIdInput
} from "../shared/desktop-api.js";

const IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u;
const MAX_COLLECTION_LENGTH = 20_000;

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

export function validateProjectDetail(value: unknown): ProjectDetail {
  const response = expectRecord(value, "project detail response");
  parseIdentifier(response.correlationId, "correlationId");
  const project = expectRecord(response.project, "project");
  parseIdentifier(project.projectId, "projectId");
  parseBoundedString(project.name, "name", 1, 120);
  parseNonNegativeInteger(project.revision, "revision");
  for (const field of [
    "sourceDocuments",
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
  return value as ProjectDetail;
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
