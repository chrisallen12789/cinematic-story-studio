import { randomBytes, randomUUID } from "node:crypto";
import { constants, type Stats } from "node:fs";
import { lstat, open } from "node:fs/promises";
import http, { type IncomingMessage } from "node:http";
import path from "node:path";

import type {
  ApiErrorResponse,
  CorrectDialogueSpeakerResponse,
  CreateProjectResponse,
  FfmpegCapabilityResponse,
  ImportStoryResponse,
  JobEventsResponse,
  JobResponse,
  ProjectDetail,
  ProjectPageResponse,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";

import type {
  CorrectSpeakerInput,
  CreateJobInput,
  CreateProjectInput
} from "../shared/desktop-api.js";
import { BackendUnavailableError, DesktopMainError } from "./errors.js";
import type { ServiceManager } from "./service-manager.js";
import {
  validateFfmpegCapabilityResponse,
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
const REQUEST_TIMEOUT_MS = 12_000;
const IMPORT_TIMEOUT_MS = 45_000;

type DeclaredTextFormat = "txt" | "markdown";

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
    return validateProjectDetail(
      await this.#jsonRequest(
        "GET",
        `/api/v1/projects/${encodeURIComponent(projectId)}`,
        undefined,
        undefined,
        PROJECT_RESPONSE_LIMIT_BYTES
      )
    );
  }

  async importSelectedFile(
    projectId: string,
    selectedPath: string,
    declaredFormat: DeclaredTextFormat
  ): Promise<ImportStoryResponse> {
    const response = await this.#multipartImport(
      `/api/v1/projects/${encodeURIComponent(projectId)}/imports`,
      selectedPath,
      declaredFormat
    );
    validateImportResponse(response);
    return response as ImportStoryResponse;
  }

  async correctSpeaker(
    input: CorrectSpeakerInput
  ): Promise<CorrectDialogueSpeakerResponse> {
    const response = await this.#jsonRequest(
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
    );
    validateCorrectionResponse(response);
    return response as CorrectDialogueSpeakerResponse;
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
    validateJobResponse(response);
    return response as JobResponse;
  }

  async getJob(jobId: string): Promise<JobResponse> {
    const response = await this.#jsonRequest(
      "GET",
      `/api/v1/jobs/${encodeURIComponent(jobId)}`
    );
    validateJobResponse(response);
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
    validateJobEventsResponse(response);
    return response as JobEventsResponse;
  }

  async cancelJob(jobId: string): Promise<JobResponse> {
    return this.#jobAction(jobId, "cancel");
  }

  async retryJob(jobId: string): Promise<JobResponse> {
    return this.#jobAction(jobId, "retry");
  }

  async resumeJob(jobId: string): Promise<JobResponse> {
    return this.#jobAction(jobId, "resume");
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
    action: "cancel" | "retry" | "resume"
  ): Promise<JobResponse> {
    const response = await this.#jsonRequest(
      "POST",
      `/api/v1/jobs/${encodeURIComponent(jobId)}/${action}`,
      undefined,
      randomUUID()
    );
    validateJobResponse(response);
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

  async #multipartImport(
    route: string,
    selectedPath: string,
    declaredFormat: DeclaredTextFormat
  ): Promise<unknown> {
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
          ? "The selected story exceeds the 8 MiB desktop import limit."
          : "The selected story is not a supported regular file.",
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
            ? "The selected story exceeds the 8 MiB desktop import limit."
            : "The selected story changed before it could be imported.",
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
          `Content-Type: ${
            declaredFormat === "markdown" ? "text/markdown" : "text/plain"
          }\r\n\r\n`,
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
              "Idempotency-Key": randomUUID(),
              "Content-Type": `multipart/form-data; boundary=${boundary}`,
              "Content-Length": contentLength
            },
            timeout: IMPORT_TIMEOUT_MS
          },
          (response) => {
            void parseNodeResponse(
              response,
              PROJECT_RESPONSE_LIMIT_BYTES
            ).then(resolve, reject);
          }
        );
        request.once("timeout", () => {
          request.destroy(
            new DesktopMainError(
              "IMPORT_TIMEOUT",
              "The selected story import timed out.",
              true
            )
          );
        });
        request.once("error", (error) => {
          source.destroy();
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
              "The selected story could not be read.",
              false
            )
          );
        });
        source.once("end", () => {
          request.end(suffix);
        });
        source.pipe(request, { end: false });
      });
    } finally {
      await fileHandle.close().catch(() => undefined);
    }
  }
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
    if (!(chunk instanceof Uint8Array)) {
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
    route.length > 512 ||
    route.includes("\\") ||
    route.includes("\0") ||
    route.includes("..")
  ) {
    throw new ValidationError("The service route was invalid.");
  }
}

function sanitizeMultipartFilename(filename: string): string {
  const sanitized = filename
    .replace(/[\r\n"]/gu, "_")
    .replace(/[^\p{L}\p{N}._ -]/gu, "_")
    .slice(0, 160);
  if (sanitized.length === 0 || sanitized === "." || sanitized === "..") {
    return "selected-story.txt";
  }
  return sanitized;
}

function sameFileIdentity(opened: Stats, current: Stats): boolean {
  return (
    opened.dev === current.dev &&
    opened.ino === current.ino &&
    opened.size === current.size &&
    opened.mtimeMs === current.mtimeMs
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

function validateImportResponse(value: unknown): void {
  const record = requireRecord(value, "import response");
  requireIdentifier(record.correlationId, "correlationId");
  const source = requireRecord(record.sourceDocument, "sourceDocument");
  const story = requireRecord(record.story, "story");
  requireIdentifier(source.documentId, "documentId");
  requireIdentifier(story.storyId, "storyId");
  requireInteger(source.byteLength, "byteLength");
}

function validateCorrectionResponse(value: unknown): void {
  const record = requireRecord(value, "speaker correction response");
  requireIdentifier(record.correlationId, "correlationId");
  const attribution = requireRecord(record.attribution, "attribution");
  if (attribution.effectiveAuthority !== "human") {
    throw new ValidationError("The saved correction authority is invalid.");
  }
  requireInteger(record.projectRevision, "projectRevision");
  requireInteger(record.lineRevision, "lineRevision");
  requireRecord(record.appendedCorrection, "appendedCorrection");
}

function validateJobResponse(value: unknown): void {
  const record = requireRecord(value, "job response");
  requireIdentifier(record.correlationId, "correlationId");
  const job = requireRecord(record.job, "job");
  requireIdentifier(job.jobId, "jobId");
  requireIdentifier(job.projectId, "projectId");
  requireInteger(job.attempt, "attempt");
  if (
    typeof job.progress !== "number" ||
    !Number.isFinite(job.progress) ||
    job.progress < 0 ||
    job.progress > 1
  ) {
    throw new ValidationError("Job progress is invalid.");
  }
}

function validateJobEventsResponse(value: unknown): void {
  const record = requireRecord(value, "job events response");
  requireIdentifier(record.correlationId, "correlationId");
  if (!Array.isArray(record.events) || record.events.length > 10_000) {
    throw new ValidationError("Job events are invalid.");
  }
  let previous = -1;
  for (const item of record.events) {
    const event = requireRecord(item, "job event");
    const sequence = requireInteger(event.sequence, "sequence");
    if (sequence <= previous) {
      throw new ValidationError("Job event ordering is invalid.");
    }
    previous = sequence;
  }
  requireInteger(record.lastSequence, "lastSequence");
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

function requireIdentifier(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(value)
  ) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
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
