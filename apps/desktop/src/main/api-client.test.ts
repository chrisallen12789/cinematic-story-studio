// @vitest-environment node

import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import {
  mkdtemp,
  open,
  rm,
  utimes,
  writeFile
} from "node:fs/promises";
import {
  createServer,
  type Server,
  type ServerResponse
} from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  BackendApiClient,
  IMPORT_LIMIT_BYTES,
  PROJECT_RESPONSE_LIMIT_BYTES
} from "./api-client";
import type { ServiceManager } from "./service-manager";

describe("BackendApiClient project responses", () => {
  it("keeps the maximum import and reopen response boundaries aligned", () => {
    expect(IMPORT_LIMIT_BYTES).toBe(8 * 1024 * 1024);
    expect(PROJECT_RESPONSE_LIMIT_BYTES).toBe(IMPORT_LIMIT_BYTES * 24);
  });

  it(
    "streams the maximum accepted import and can reopen its project",
    async () => {
      const directory = await mkdtemp(path.join(tmpdir(), "css-api-client-"));
      const storyPath = path.join(directory, "maximum-story.txt");
      const file = await open(storyPath, "w");
      const textChunk = Buffer.alloc(64 * 1024, "a");
      const storyDigest = createHash("sha256");
      for (
        let offset = 0;
        offset < IMPORT_LIMIT_BYTES;
        offset += textChunk.byteLength
      ) {
        await file.write(textChunk, 0, textChunk.byteLength, offset);
        storyDigest.update(textChunk);
      }
      await file.close();
      const storySha256 = storyDigest.digest("hex");
      let receivedImportBytes = 0;
      const server = createServer((request, response) => {
        request.resume();
        request.once("end", () => {
          if (request.method === "POST") {
            receivedImportBytes = Number(
              request.headers["content-length"] ?? 0
            );
            sendJson(
              response,
              minimalImportResponse(IMPORT_LIMIT_BYTES, {
                sha256: storySha256
              })
            );
            return;
          }
          sendJson(response, minimalProjectDetail());
        });
      });
      await new Promise<void>((resolve) => {
        server.listen(0, "127.0.0.1", resolve);
      });
      const client = new BackendApiClient(serviceForServer(server));

      try {
        const imported = await client.importSelectedFile(
          "project-1",
          storyPath,
          "txt"
        );
        const reopened = await client.openProject("project-1");
        expect(imported.sourceDocument.byteLength).toBe(IMPORT_LIMIT_BYTES);
        expect(receivedImportBytes).toBeGreaterThan(IMPORT_LIMIT_BYTES);
        expect(reopened.project.name).toBe("Large manuscript");
      } finally {
        await closeServer(server);
        await rm(directory, { recursive: true, force: true });
      }
    },
    15_000
  );

  it(
    "rejects one byte above the desktop import limit before transport",
    async () => {
      const directory = await mkdtemp(path.join(tmpdir(), "css-too-large-"));
      const selectedPath = path.join(directory, "too-large.txt");
      await writeFile(
        selectedPath,
        Buffer.alloc(IMPORT_LIMIT_BYTES + 1, "a")
      );
      let requestCount = 0;
      const server = createServer((_request, response) => {
        requestCount += 1;
        response.destroy();
      });
      await new Promise<void>((resolve) => {
        server.listen(0, "127.0.0.1", resolve);
      });

      try {
        await expect(
          new BackendApiClient(serviceForServer(server)).importSelectedFile(
            "project-1",
            selectedPath,
            "txt"
          )
        ).rejects.toMatchObject({ code: "IMPORT_TOO_LARGE" });
        expect(requestCount).toBe(0);
      } finally {
        await closeServer(server);
        await rm(directory, { recursive: true, force: true });
      }
    },
    15_000
  );

  it.each([
    [
      "markdown",
      "story.md",
      "text/markdown"
    ],
    [
      "docx",
      "story.docx",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ],
    ["epub", "story.epub", "application/epub+zip"],
    ["pdf", "story.pdf", "application/pdf"]
  ] as const)(
    "streams %s with its exact declared format and media type",
    async (format, filename, mediaType) => {
      const directory = await mkdtemp(path.join(tmpdir(), "css-format-"));
      const selectedPath = path.join(directory, filename);
      const selectedBytes = Buffer.from("synthetic document bytes", "utf8");
      const selectedSha256 = sha256(selectedBytes);
      await writeFile(selectedPath, selectedBytes);
      let requestBody = "";
      const server = createServer((request, response) => {
        const chunks: Buffer[] = [];
        request.on("data", (chunk: Buffer) => chunks.push(chunk));
        request.once("end", () => {
          requestBody = Buffer.concat(chunks).toString("utf8");
          sendJson(
            response,
            minimalImportResponse(selectedBytes.byteLength, {
              sha256: selectedSha256,
              format,
              mediaType
            })
          );
        });
      });
      await new Promise<void>((resolve) => {
        server.listen(0, "127.0.0.1", resolve);
      });

      try {
        await new BackendApiClient(
          serviceForServer(server)
        ).importSelectedFile("project-1", selectedPath, format);
        expect(requestBody).toContain(
          `name="declaredFormat"\r\n\r\n${format}\r\n`
        );
        expect(requestBody).toContain(`Content-Type: ${mediaType}\r\n`);
        expect(requestBody).toContain(`filename="${filename}"`);
      } finally {
        await closeServer(server);
        await rm(directory, { recursive: true, force: true });
      }
    }
  );

  it("rejects a service import response that does not match the local byte snapshot", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-integrity-"));
    const selectedPath = path.join(directory, "story.txt");
    const selectedBytes = Buffer.from("immutable synthetic bytes", "utf8");
    await writeFile(selectedPath, selectedBytes);
    const server = createServer((request, response) => {
      request.resume();
      request.once("end", () => {
        sendJson(
          response,
          minimalImportResponse(selectedBytes.byteLength, {
            sha256: "f".repeat(64)
          })
        );
      });
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });

    try {
      await expect(
        new BackendApiClient(serviceForServer(server)).importSelectedFile(
          "project-1",
          selectedPath,
          "txt"
        )
      ).rejects.toThrow("source identity");
    } finally {
      await closeServer(server);
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("rejects an analyze job returned from the import route", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-import-job-"));
    const selectedPath = path.join(directory, "story.txt");
    const selectedBytes = Buffer.from("synthetic job bytes", "utf8");
    await writeFile(selectedPath, selectedBytes);
    const sourceSha256 = sha256(selectedBytes);
    const server = createServer((request, response) => {
      request.resume();
      request.once("end", () => {
        const imported = minimalImportResponse(selectedBytes.byteLength, {
          sha256: sourceSha256
        });
        sendJson(response, {
          ...imported,
          job: {
            ...imported.job,
            type: "analyze_story"
          }
        });
      });
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });

    try {
      await expect(
        new BackendApiClient(serviceForServer(server)).importSelectedFile(
          "project-1",
          selectedPath,
          "txt"
        )
      ).rejects.toThrow("source identity");
    } finally {
      await closeServer(server);
      await rm(directory, { recursive: true, force: true });
    }
  });

  it(
    "rejects a selected file that changes while its opened bytes are streaming",
    async () => {
      const directory = await mkdtemp(path.join(tmpdir(), "css-mutation-"));
      const selectedPath = path.join(directory, "story.txt");
      const original = Buffer.alloc(IMPORT_LIMIT_BYTES, "a");
      const replacement = Buffer.alloc(IMPORT_LIMIT_BYTES, "b");
      await writeFile(selectedPath, original);
      await utimes(selectedPath, new Date(0), new Date(0));
      let mutated = false;
      const server = createServer((request, response) => {
        if (!mutated) {
          mutated = true;
          writeFileSync(selectedPath, replacement);
        }
        request.resume();
        request.once("end", () => {
          sendJson(
            response,
            minimalImportResponse(replacement.byteLength, {
              sha256: sha256(replacement)
            })
          );
        });
      });
      await new Promise<void>((resolve) => {
        server.listen(0, "127.0.0.1", resolve);
      });

      try {
        await expect(
          new BackendApiClient(serviceForServer(server)).importSelectedFile(
            "project-1",
            selectedPath,
            "txt"
          )
        ).rejects.toMatchObject({ code: "IMPORT_FILE_CHANGED" });
      } finally {
        await closeServer(server);
        await rm(directory, { recursive: true, force: true });
      }
    },
    15_000
  );

  it("reopens a project response larger than the general 16 MiB cap", async () => {
    const responseBody = Buffer.from(
      JSON.stringify({
        ...minimalProjectDetail(),
        responsePadding: "x".repeat(17 * 1024 * 1024)
      }),
      "utf8"
    );
    const server = createServer((_request, response) => {
      response.writeHead(200, {
        "Content-Type": "application/json",
        "Content-Length": responseBody.byteLength
      });
      response.end(responseBody);
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (address === null || typeof address === "string") {
      throw new Error("The test server did not bind to a TCP port.");
    }

    try {
      const detail = await new BackendApiClient(
        serviceForServer(server)
      ).openProject("project-1");
      expect(responseBody.byteLength).toBeGreaterThan(16 * 1024 * 1024);
      expect(detail.project.name).toBe("Large manuscript");
    } finally {
      await closeServer(server);
    }
  });

  it("loads and decides an import review through fixed typed routes", async () => {
    const requests: Array<{
      readonly method: string | undefined;
      readonly url: string | undefined;
      readonly idempotencyKey: string | undefined;
      readonly body: string;
    }> = [];
    const server = createServer((request, response) => {
      const chunks: Buffer[] = [];
      request.on("data", (chunk: Buffer) => chunks.push(chunk));
      request.once("end", () => {
        requests.push({
          method: request.method,
          url: request.url,
          idempotencyKey: request.headers["idempotency-key"] as
            | string
            | undefined,
          body: Buffer.concat(chunks).toString("utf8")
        });
        if (request.method === "GET") {
          sendJson(response, {
            correlationId: "review-correlation",
            review: minimalImportReview()
          });
          return;
        }
        sendJson(response, {
          correlationId: "decision-correlation",
          review: {
            ...minimalImportReview(),
            revision: 2,
            state: "approved",
            latestDecision: minimalApprovalDecision()
          },
          decision: minimalApprovalDecision(),
          projectRevision: 3,
          analysisAllowed: true
        });
      });
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    const client = new BackendApiClient(serviceForServer(server));

    try {
      const reviewIdentity = minimalReviewIdentity();
      const loaded = await client.getImportReview(reviewIdentity);
      expect(loaded.review.evidenceFingerprint).toBe("c".repeat(64));
      const decided = await client.decideImportReview({
        projectId: "project-1",
        reviewId: "review-1",
        sourceDocumentId: "document-1",
        extractionId: "extraction-1",
        candidateStoryId: "story-1",
        candidateStoryRevision: 1,
        decision: "approved",
        rationale: "Reviewed exact extraction.",
        expectedRevision: 1,
        evidenceFingerprint: "c".repeat(64),
        idempotencyKey: "decision-request-1"
      });
      expect(decided.analysisAllowed).toBe(true);
      expect(requests).toHaveLength(2);
      expect(requests[0]?.url).toBe(
        "/api/v1/projects/project-1/imports/review-1/review"
      );
      expect(requests[1]).toMatchObject({
        method: "POST",
        url: "/api/v1/projects/project-1/imports/review-1/review/decision",
        idempotencyKey: "decision-request-1"
      });
      expect(JSON.parse(requests[1]?.body ?? "{}")).toMatchObject({
        reviewId: "review-1",
        decision: "approved",
        expectedRevision: 1,
        evidenceFingerprint: "c".repeat(64)
      });
    } finally {
      await closeServer(server);
    }
  });
});

function minimalProjectDetail() {
  return {
    correlationId: "correlation-1",
    project: {
      projectId: "project-1",
      name: "Large manuscript",
      revision: 1
    },
    sourceDocuments: [],
    extractions: [],
    importReviews: [],
    analysisAllowed: false,
    story: null,
    chapters: [],
    scenes: [],
    beats: [],
    characters: [],
    dialogueLines: [],
    dialogueAttributions: [],
    castingAssignments: [],
    castingPlaceholders: [],
    approvals: [],
    jobs: []
  };
}

function minimalImportResponse(
  byteLength: number,
  {
    sha256: sourceSha256 = "a".repeat(64),
    format = "txt",
    mediaType = "text/plain"
  }: {
    readonly sha256?: string;
    readonly format?: "txt" | "markdown" | "docx" | "epub" | "pdf";
    readonly mediaType?: string;
  } = {}
) {
  const now = "2026-07-29T12:00:00Z";
  const provenance = {
    origin: "system",
    recordedAt: now,
    actorId: "document-ingest"
  };
  return {
    correlationId: "import-correlation",
    sourceDocument: {
      schemaVersion: "1.0.0",
      revision: 1,
      provenance,
      documentId: "document-1",
      projectId: "project-1",
      displayName: "maximum-story.txt",
      mediaType,
      declaredFormat: format,
      contentSha256: sourceSha256,
      byteLength,
      importedAt: now,
      originalTextPreserved: true,
      originalBytesPreserved: true,
      storageKey: "sources/document-1",
      extractionStatus: "pending",
      sourceRevision: 1,
      warnings: []
    },
    extraction: {
      schemaVersion: "1.0.0",
      revision: 1,
      provenance,
      extractionId: "extraction-1",
      projectId: "project-1",
      sourceDocumentId: "document-1",
      declaredFormat: format,
      detectedFormat: format,
      mediaType,
      status: "pending",
      adapterId: "plain-text",
      adapterVersion: "1.0.0",
      parserDependency: "python-standard-library",
      parserVersion: "3.13.0",
      sourceSha256,
      sourceByteCount: byteLength,
      warnings: [],
      quality: {
        classification: "pending",
        confidence: 0
      },
      retryability: "not_retryable",
      reviewRequired: true,
      originalPreserved: true,
      createdAt: now,
      updatedAt: now
    },
    job: {
      jobId: "job-1",
      projectId: "project-1",
      type: "extract_document",
      state: "queued",
      inputRevision: 1,
      inputFingerprint: sourceSha256,
      attempt: 1,
      stage: "extract_document",
      progress: 0,
      checkpointAvailable: false,
      cancellationRequested: false,
      warnings: [],
      createdAt: now,
      updatedAt: now
    }
  };
}

function minimalImportReview() {
  const now = "2026-07-29T12:01:00Z";
  return {
    schemaVersion: "1.0.0",
    revision: 1,
    provenance: {
      origin: "system",
      recordedAt: now,
      actorId: "document-extractor"
    },
    reviewId: "review-1",
    projectId: "project-1",
    sourceDocumentId: "document-1",
    extractionId: "extraction-1",
    candidateStoryId: "story-1",
    candidateStoryRevision: 1,
    state: "pending",
    evidenceFingerprint: "c".repeat(64),
    previewText: "Synthetic preview.",
    previewTruncated: false,
    warnings: [],
    createdAt: now,
    updatedAt: now
  };
}

function minimalReviewIdentity() {
  return {
    projectId: "project-1",
    reviewId: "review-1",
    sourceDocumentId: "document-1",
    extractionId: "extraction-1",
    candidateStoryId: "story-1",
    candidateStoryRevision: 1,
    evidenceFingerprint: "c".repeat(64)
  };
}

function minimalApprovalDecision() {
  const now = "2026-07-29T12:02:00Z";
  return {
    schemaVersion: "1.0.0",
    revision: 2,
    provenance: {
      origin: "human",
      recordedAt: now,
      actorId: "local_user",
      inputFingerprint: "c".repeat(64)
    },
    decisionId: "decision-1",
    projectId: "project-1",
    gateId: "import_review",
    scope: {
      entityType: "DocumentExtraction",
      entityId: "extraction-1",
      revision: 1
    },
    decision: "approved",
    actor: {
      type: "human",
      actorId: "local_user"
    },
    rationale: "Reviewed exact extraction.",
    evidenceFingerprint: "c".repeat(64),
    decidedAt: now,
    immutable: true
  };
}

function sha256(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function serviceForServer(server: Server): ServiceManager {
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("The test server did not bind to a TCP port.");
  }
  return {
    connection: () => ({
      port: address.port,
      token: "test-token",
      instanceId: "test-instance"
    })
  } as unknown as ServiceManager;
}

function sendJson(
  response: ServerResponse,
  value: unknown
): void {
  const body = Buffer.from(JSON.stringify(value), "utf8");
  response.writeHead(200, {
    "Content-Type": "application/json",
    "Content-Length": body.byteLength
  });
  response.end(body);
}

async function closeServer(server: Server): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => {
      if (error === undefined) {
        resolve();
      } else {
        reject(error);
      }
    });
  });
}
