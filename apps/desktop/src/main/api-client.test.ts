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

describe("BackendApiClient legacy speaker correction boundary", () => {
  it.each(["character-bob", null] as const)(
    "binds an exact human speaker correction for %s",
    async (characterId) => {
      await withJsonResponse(
        validSpeakerCorrectionWire(characterId),
        async (client) => {
          const response = await client.correctSpeaker({
            projectId: "project-1",
            lineId: "line-1",
            characterId,
            reason: "Fixture correction.",
            expectedRevision: 2
          });
          expect(response.attribution.effectiveSpeakerId).toBe(characterId);
          expect(response.lineRevision).toBe(3);
          expect(response.appendedCorrection.correctedValue).toBe(
            characterId
          );
        }
      );
    }
  );

  it("binds the service's canonical reason when the request omits one", async () => {
    const defaultReason = "Speaker corrected by the local user.";
    await withJsonResponse(
      validSpeakerCorrectionWire("character-bob", defaultReason),
      async (client) => {
        const response = await client.correctSpeaker({
          projectId: "project-1",
          lineId: "line-1",
          characterId: "character-bob",
          expectedRevision: 2
        });
        expect(response.appendedCorrection.reason).toBe(defaultReason);
      }
    );
  });

  it.each([
    {
      label: "foreign project",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          projectId: "project-foreign"
        }
      })
    },
    {
      label: "foreign line",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          lineId: "line-foreign"
        }
      })
    },
    {
      label: "different requested speaker",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          effectiveSpeakerId: "character-foreign"
        }
      })
    },
    {
      label: "nonincrementing line revision",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        lineRevision: 2
      })
    },
    {
      label: "nonincrementing attribution revision",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          revision: 2
        }
      })
    },
    {
      label: "foreign correction target",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        appendedCorrection: {
          ...wire.appendedCorrection,
          target: {
            ...wire.appendedCorrection.target,
            entityId: "line-foreign"
          }
        }
      })
    },
    {
      label: "nonincrementing correction target revision",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        appendedCorrection: {
          ...wire.appendedCorrection,
          target: {
            ...wire.appendedCorrection.target,
            revision: 2
          }
        }
      })
    },
    {
      label: "different corrected value",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        appendedCorrection: {
          ...wire.appendedCorrection,
          correctedValue: "character-foreign"
        }
      })
    },
    {
      label: "different reason",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        appendedCorrection: {
          ...wire.appendedCorrection,
          reason: "A response-controlled reason."
        }
      })
    },
    {
      label: "nonhuman authority",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        appendedCorrection: {
          ...wire.appendedCorrection,
          authority: {
            ...wire.appendedCorrection.authority,
            source: "system"
          }
        }
      })
    },
    {
      label: "mutable correction",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        appendedCorrection: {
          ...wire.appendedCorrection,
          immutable: false
        }
      })
    },
    {
      label: "automation-unlocked correction",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        appendedCorrection: {
          ...wire.appendedCorrection,
          lockedAgainstAutomation: false
        }
      })
    },
    {
      label: "different projected prior speaker",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          humanCorrections: [
            {
              ...wire.appendedCorrection,
              previousCharacterId: "character-foreign"
            }
          ]
        }
      })
    },
    {
      label: "different projected human actor",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          humanCorrections: [
            {
              ...wire.appendedCorrection,
              authority: {
                ...wire.appendedCorrection.authority,
                actorId: "other_local_user"
              }
            }
          ]
        }
      })
    },
    {
      label: "provenance actor different from correction actor",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          provenance: {
            ...wire.attribution.provenance,
            actorId: "other_local_user"
          }
        }
      })
    },
    {
      label: "provenance time different from correction time",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          provenance: {
            ...wire.attribution.provenance,
            recordedAt: "2026-07-30T12:00:01Z"
          }
        }
      })
    },
    {
      label: "attribution update time different from correction time",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        attribution: {
          ...wire.attribution,
          updatedAt: "2026-07-30T12:00:01Z"
        }
      })
    },
    {
      label: "unsupported response payload",
      mutate: (wire: ReturnType<typeof validSpeakerCorrectionWire>) => ({
        ...wire,
        manuscriptText: "private story text"
      })
    }
  ])("rejects $label", async ({ mutate }) => {
    await withJsonResponse(
      mutate(validSpeakerCorrectionWire("character-bob")),
      async (client) => {
        await expect(
          client.correctSpeaker({
            projectId: "project-1",
            lineId: "line-1",
            characterId: "character-bob",
            reason: "Fixture correction.",
            expectedRevision: 2
          })
        ).rejects.toThrow();
      }
    );
  });
});

describe("BackendApiClient job response boundary", () => {
  it("binds Phase 0 and Phase 2 job responses to exact requested work", async () => {
    await withJsonResponse(validJobWire("analyze_story"), async (client) => {
      const response = await client.createJob({
        projectId: "project-1",
        type: "analyze_story",
        inputRevision: 4,
        idempotencyKey: "phase0-job-create"
      });
      expect(response.job.jobId).toBe("job-1");
    });

    await withJsonResponse(
      validJobWire("analyze_whole_book"),
      async (client) => {
        const response = await client.getJob("job-1", {
          projectId: "project-1",
          type: "analyze_whole_book",
          inputRevision: 4,
          inputFingerprint: "a".repeat(64)
        });
        expect(response.job.type).toBe("analyze_whole_book");
      }
    );

    await withJsonResponse(
      validJobWire("analyze_casting"),
      async (client) => {
        const response = await client.getJob("job-1", {
          projectId: "project-1",
          type: "analyze_casting",
          inputRevision: 4,
          inputFingerprint: "a".repeat(64)
        });
        expect(response.job.target.type).toBe("casting_run");
      }
    );
  });

  it.each([
    ["jobId", "job-foreign"],
    ["projectId", "project-foreign"],
    ["type", "analyze_story"],
    ["inputRevision", 5],
    ["inputFingerprint", "f".repeat(64)]
  ] as const)(
    "rejects a job with tampered %s",
    async (field, value) => {
      const response = validJobWire("analyze_whole_book");
      const job = response.job as Record<string, unknown>;
      await withJsonResponse(
        {
          ...response,
          job: {
            ...job,
            [field]: value,
            ...(field === "type"
              ? { target: { type: "story", id: "story-1" } }
              : {})
          }
        },
        async (client) => {
          await expect(
            client.getJob("job-1", {
              projectId: "project-1",
              type: "analyze_whole_book",
              inputRevision: 4,
              inputFingerprint: "a".repeat(64)
            })
          ).rejects.toThrow();
        }
      );
    }
  );

  it("validates ordered, redacted job events after the requested sequence", async () => {
    const response = {
      correlationId: "correlation-events",
      events: [
        {
          jobId: "job-1",
          attempt: 1,
          sequence: 5,
          type: "warning",
          state: "running",
          stage: "analyze_structure",
          progress: 0.5,
          warning: {
            code: "LOW_CONFIDENCE",
            severity: "warning",
            message: "A redacted bounded warning.",
            requiresHumanReview: true,
            relatedEntities: [
              {
                entityType: "analysis_run",
                entityId: "run-1",
                revision: 1
              }
            ]
          },
          createdAt: "2026-07-30T12:00:00Z"
        }
      ],
      lastSequence: 5
    };
    await withJsonResponse(response, async (client) => {
      const events = await client.getJobEvents("job-1", 4);
      expect(events.events).toHaveLength(1);
    });
  });

  it.each([
    {
      label: "foreign job",
      mutate: (event: Record<string, unknown>) => ({
        ...event,
        jobId: "job-foreign"
      })
    },
    {
      label: "sequence at the afterSequence boundary",
      mutate: (event: Record<string, unknown>) => ({
        ...event,
        sequence: 4
      })
    },
    {
      label: "unredacted extra payload",
      mutate: (event: Record<string, unknown>) => ({
        ...event,
        manuscriptText: "private story text"
      })
    },
    {
      label: "warning with extra payload",
      mutate: (event: Record<string, unknown>) => ({
        ...event,
        warning: {
          ...(event.warning as Record<string, unknown>),
          providerPayload: "private"
        }
      })
    }
  ])("rejects $label in a job event", async ({ mutate }) => {
    const validEvent = {
      jobId: "job-1",
      attempt: 1,
      sequence: 5,
      type: "warning",
      warning: {
        code: "LOW_CONFIDENCE",
        severity: "warning",
        message: "A redacted bounded warning.",
        requiresHumanReview: true
      },
      createdAt: "2026-07-30T12:00:00Z"
    };
    await withJsonResponse(
      {
        correlationId: "correlation-events",
        events: [mutate(validEvent)],
        lastSequence: 5
      },
      async (client) => {
        await expect(client.getJobEvents("job-1", 4)).rejects.toThrow();
      }
    );
  });
});

describe("BackendApiClient casting routes", () => {
  it("uses the fixed project catalog and role-candidate routes with bounded queries", async () => {
    const requests: string[] = [];
    const server = createServer((request, response) => {
      requests.push(`${request.method} ${request.url}`);
      request.resume();
      request.once("end", () => sendJson(response, {}));
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    const client = new BackendApiClient(serviceForServer(server));
    const evidence = {
      projectId: "project-1",
      runId: "casting-run-1",
      expectedRunFingerprint: "a".repeat(64),
      expectedCatalogRevisionId: "catalog-1",
      expectedCatalogFingerprint: "b".repeat(64),
      expectedSnapshotId: "snapshot-1",
      expectedSnapshotRevision: 2,
      expectedSnapshotFingerprint: "c".repeat(64)
    };

    try {
      await client
        .getVoiceCatalog({
          projectId: "project-1",
          expectedCatalogRevisionId: "catalog-1",
          expectedCatalogFingerprint: "b".repeat(64),
          limit: 50
        })
        .catch(() => undefined);
      await client
        .listCastingCandidates({
          ...evidence,
          roleId: "role-primary-narrator",
          expectedRoleRevision: 3,
          limit: 12
        })
        .catch(() => undefined);
      await client
        .listCastingReviews({
          ...evidence,
          expectedApprovedCastSnapshotId: "cast-snapshot-1",
          expectedApprovedCastSnapshotRevision: 4
        })
        .catch(() => undefined);
      expect(requests).toEqual([
        `GET /api/v1/projects/project-1/casting/catalog?limit=50&expectedCatalogRevisionId=catalog-1&expectedCatalogFingerprint=${"b".repeat(64)}`,
        `GET /api/v1/projects/project-1/casting-runs/casting-run-1/roles/role-primary-narrator/candidates?limit=12&expectedRunFingerprint=${"a".repeat(64)}&expectedCatalogRevisionId=catalog-1&expectedCatalogFingerprint=${"b".repeat(64)}&expectedSnapshotId=snapshot-1&expectedSnapshotRevision=2&expectedSnapshotFingerprint=${"c".repeat(64)}&expectedRoleRevision=3`,
        `GET /api/v1/projects/project-1/casting-runs/casting-run-1/reviews?expectedRunFingerprint=${"a".repeat(64)}&expectedCatalogRevisionId=catalog-1&expectedCatalogFingerprint=${"b".repeat(64)}&expectedSnapshotId=snapshot-1&expectedSnapshotRevision=2&expectedSnapshotFingerprint=${"c".repeat(64)}&expectedApprovedCastSnapshotId=cast-snapshot-1&expectedApprovedCastSnapshotRevision=4`
      ]);
    } finally {
      await closeServer(server);
    }
  });

  it("posts an idempotent run with only the frozen casting prerequisites", async () => {
    let observedPath = "";
    let observedAuthorization = "";
    let observedIdempotency = "";
    let observedBody: unknown;
    const server = createServer((request, response) => {
      observedPath = `${request.method} ${request.url}`;
      observedAuthorization = String(request.headers.authorization ?? "");
      observedIdempotency = String(
        request.headers["idempotency-key"] ?? ""
      );
      const chunks: Buffer[] = [];
      request.on("data", (chunk: Buffer) => chunks.push(chunk));
      request.once("end", () => {
        observedBody = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        sendJson(response, {});
      });
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    const client = new BackendApiClient(serviceForServer(server));

    try {
      await client
        .createCastingRun({
          projectId: "project-1",
          expectedAnalysisRunId: "analysis-run-1",
          expectedSnapshotId: "snapshot-1",
          expectedSnapshotRevision: 2,
          expectedSnapshotFingerprint: "a".repeat(64),
          expectedCorrectionSetFingerprint: "b".repeat(64),
          expectedImportReviewDecisionId: "import-decision-1",
          expectedAnalysisGateDecisionIds: {
            storyStructureReview: "structure-decision-1",
            characterRegistryReview: "character-decision-1",
            dialogueAttributionReview: "dialogue-decision-1",
            wholeBookAnalysisReview: "whole-book-decision-1"
          },
          expectedCatalogRevisionId: "catalog-1",
          expectedCatalogFingerprint: "c".repeat(64),
          expectedCastingProfileFingerprint: "d".repeat(64),
          idempotencyKey: "create-casting-run-1"
        })
        .catch(() => undefined);

      expect(observedPath).toBe(
        "POST /api/v1/projects/project-1/casting-runs"
      );
      expect(observedAuthorization).toBe("Bearer test-token");
      expect(observedIdempotency).toBe("create-casting-run-1");
      expect(observedBody).toEqual({
        expectedAnalysisRunId: "analysis-run-1",
        expectedSnapshotId: "snapshot-1",
        expectedSnapshotRevision: 2,
        expectedSnapshotFingerprint: "a".repeat(64),
        expectedCorrectionSetFingerprint: "b".repeat(64),
        expectedImportReviewDecisionId: "import-decision-1",
        expectedAnalysisGateDecisionIds: {
          storyStructureReview: "structure-decision-1",
          characterRegistryReview: "character-decision-1",
          dialogueAttributionReview: "dialogue-decision-1",
          wholeBookAnalysisReview: "whole-book-decision-1"
        },
        expectedCatalogRevisionId: "catalog-1",
        expectedCatalogFingerprint: "c".repeat(64),
        expectedCastingProfileFingerprint: "d".repeat(64),
        idempotencyKey: "create-casting-run-1"
      });
    } finally {
      await closeServer(server);
    }
  });

  it("posts custom roles, corrections, and reviews with canonical backend payloads", async () => {
    const observed: Array<{
      readonly path: string;
      readonly idempotency: string;
      readonly body: unknown;
    }> = [];
    const server = createServer((request, response) => {
      const chunks: Buffer[] = [];
      request.on("data", (chunk: Buffer) => chunks.push(chunk));
      request.once("end", () => {
        observed.push({
          path: `${request.method} ${request.url}`,
          idempotency: String(
            request.headers["idempotency-key"] ?? ""
          ),
          body: JSON.parse(Buffer.concat(chunks).toString("utf8"))
        });
        sendJson(response, {});
      });
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    const client = new BackendApiClient(serviceForServer(server));

    try {
      await client
        .createCustomProductionRole({
          projectId: "project-1",
          runId: "casting-run-1",
          definitionId: "custom-role-a",
          label: "Festival announcer",
          performanceRequirements: {
            language: "en",
            locales: ["en-US"],
            agePresentationRange: null,
            vocalPresentations: ["neutral"],
            preferredTextures: ["clear"],
            speakingRateRange: null,
            requiredExpressiveRange: ["authoritative"],
            longFormRequired: false
          },
          reason: "Producer-defined role with no manuscript source.",
          expectedRunFingerprint: "a".repeat(64),
          expectedCatalogRevisionId: "catalog-1",
          expectedCatalogFingerprint: "b".repeat(64),
          expectedSnapshotId: "snapshot-1",
          expectedSnapshotRevision: 2,
          expectedSnapshotFingerprint: "c".repeat(64),
          expectedCorrectionSetFingerprint: "d".repeat(64),
          expectedCastingProfileFingerprint: "e".repeat(64),
          idempotencyKey: "custom-role-create-1"
        })
        .catch(() => undefined);
      await client
        .appendCastingCorrection({
          projectId: "project-1",
          runId: "casting-run-1",
          operation: "select_voice",
          targetRoleId: "role-1",
          expectedRoleRevision: 3,
          expectedRunFingerprint: "a".repeat(64),
          expectedCatalogFingerprint: "b".repeat(64),
          expectedSnapshotFingerprint: "c".repeat(64),
          expectedCorrectionSetFingerprint: "d".repeat(64),
          previousEffectiveFingerprint: "e".repeat(64),
          voiceProfileId: "voice-1",
          correctedValue: { voiceProfileId: "voice-1" },
          reason: "Human-selected fixture voice.",
          supersedesCorrectionId: null,
          idempotencyKey: "casting-correction-1"
        })
        .catch(() => undefined);
      await client
        .decideCastingReview({
          projectId: "project-1",
          runId: "casting-run-1",
          gateId: "narrator_casting_review",
          decision: "approve",
          expectedRevision: 2,
          expectedEvidenceFingerprint: "f".repeat(64),
          expectedRunFingerprint: "a".repeat(64),
          expectedApprovedCastSnapshotId: "cast-snapshot-1",
          expectedApprovedCastSnapshotRevision: 4,
          warningAcknowledgementIds: ["warning-1"],
          rationale: "Reviewed the narrator cast and rights.",
          supersedesDecisionId: null,
          idempotencyKey: "casting-review-1"
        })
        .catch(() => undefined);

      expect(observed).toEqual([
        {
          path:
            "POST /api/v1/projects/project-1/casting-runs/casting-run-1/roles",
          idempotency: "custom-role-create-1",
          body: {
            definitionId: "custom-role-a",
            label: "Festival announcer",
            performanceRequirements: {
              language: "en",
              locales: ["en-US"],
              agePresentationRange: null,
              vocalPresentations: ["neutral"],
              preferredTextures: ["clear"],
              speakingRateRange: null,
              requiredExpressiveRange: ["authoritative"],
              longFormRequired: false
            },
            reason: "Producer-defined role with no manuscript source.",
            expectedRunFingerprint: "a".repeat(64),
            expectedCatalogRevisionId: "catalog-1",
            expectedCatalogFingerprint: "b".repeat(64),
            expectedSnapshotId: "snapshot-1",
            expectedSnapshotRevision: 2,
            expectedSnapshotFingerprint: "c".repeat(64),
            expectedCorrectionSetFingerprint: "d".repeat(64),
            expectedCastingProfileFingerprint: "e".repeat(64),
            idempotencyKey: "custom-role-create-1"
          }
        },
        {
          path:
            "POST /api/v1/projects/project-1/casting-runs/casting-run-1/corrections",
          idempotency: "casting-correction-1",
          body: {
            operation: "select_voice",
            targetRoleId: "role-1",
            expectedRoleRevision: 3,
            expectedRunFingerprint: "a".repeat(64),
            expectedCatalogFingerprint: "b".repeat(64),
            expectedSnapshotFingerprint: "c".repeat(64),
            expectedCorrectionSetFingerprint: "d".repeat(64),
            previousEffectiveFingerprint: "e".repeat(64),
            voiceProfileId: "voice-1",
            correctedValue: { voiceProfileId: "voice-1" },
            reason: "Human-selected fixture voice.",
            supersedesCorrectionId: null,
            idempotencyKey: "casting-correction-1"
          }
        },
        {
          path:
            "POST /api/v1/projects/project-1/casting-runs/casting-run-1/reviews/narrator_casting_review/decisions",
          idempotency: "casting-review-1",
          body: {
            decision: "approve",
            expectedRevision: 2,
            expectedEvidenceFingerprint: "f".repeat(64),
            expectedRunFingerprint: "a".repeat(64),
            expectedApprovedCastSnapshotId: "cast-snapshot-1",
            expectedApprovedCastSnapshotRevision: 4,
            warningAcknowledgementIds: ["warning-1"],
            rationale: "Reviewed the narrator cast and rights.",
            supersedesDecisionId: null,
            idempotencyKey: "casting-review-1"
          }
        }
      ]);
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
    currentAnalysisRun: null,
    analysisGateReviews: [],
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

function validJobWire(
  type: "analyze_story" | "analyze_whole_book" | "analyze_casting"
): Record<string, unknown> {
  const target =
    type === "analyze_story"
      ? { type: "story", id: "story-1" }
      : type === "analyze_casting"
        ? { type: "casting_run", id: "casting-run-1" }
        : { type: "analysis_run", id: "run-1" };
  return {
    correlationId: "correlation-job",
    job: {
      jobId: "job-1",
      projectId: "project-1",
      type,
      state: "running",
      target,
      inputRevision: 4,
      inputFingerprint: "a".repeat(64),
      attempt: 1,
      stage:
        type === "analyze_story"
          ? "analyze_story"
          : type === "analyze_casting"
            ? "evaluate_role_constraints"
            : "analyze_structure",
      progress: 0.5,
      checkpointAvailable: false,
      cancellationRequested: false,
      warnings: [],
      createdAt: "2026-07-30T12:00:00Z",
      updatedAt: "2026-07-30T12:00:01Z"
    }
  };
}

function validSpeakerCorrectionWire(
  characterId: string | null,
  reason = "Fixture correction."
) {
  const recordedAt = "2026-07-30T12:00:00Z";
  const correction = {
    correctionId: "correction-1",
    target: {
      entityType: "DialogueLine",
      entityId: "line-1",
      revision: 3
    },
    fieldPath: "/effectiveSpeakerId",
    previousValueFingerprint: "a".repeat(64),
    previousCharacterId: "character-alice",
    correctedValue: characterId,
    correctedCharacterId: characterId,
    reason,
    authority: {
      source: "human",
      actorId: "local_user"
    },
    recordedAt,
    immutable: true,
    lockedAgainstAutomation: true
  };
  return {
    correlationId: "correlation-correction",
    attribution: {
      schemaVersion: "1.0.0",
      revision: 3,
      provenance: {
        origin: "human",
        recordedAt,
        actorId: "local_user",
        sourceReferences: [
          {
            entityType: "DialogueLine",
            entityId: "line-1",
            revision: 3
          }
        ],
        notes: "Protected human speaker correction."
      },
      attributionId: "attribution-1",
      projectId: "project-1",
      lineId: "line-1",
      proposedSpeakerId: "character-alice",
      effectiveSpeakerId: characterId,
      effectiveAuthority: "human",
      evidence: [],
      confidence: {
        score: 1,
        basis: "durable_human_correction",
        calibrationId: "human-authority"
      },
      warnings: [],
      humanCorrections: [correction],
      updatedAt: recordedAt
    },
    appendedCorrection: correction,
    projectRevision: 4,
    lineRevision: 3
  };
}

async function withJsonResponse(
  value: unknown,
  operation: (client: BackendApiClient) => Promise<void>
): Promise<void> {
  const server = createServer((request, response) => {
    request.resume();
    request.once("end", () => {
      sendJson(response, value);
    });
  });
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  try {
    await operation(new BackendApiClient(serviceForServer(server)));
  } finally {
    await closeServer(server);
  }
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
