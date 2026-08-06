import { createHash } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackendApiClient } from "./api-client";
import type { ServiceManager } from "./service-manager";

describe("BackendApiClient authenticated audition audio", () => {
  const bytes = pcmWave();
  const sha256 = createHash("sha256").update(bytes).digest("hex");

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(responseBody(bytes), {
          status: 200,
          headers: {
            "content-type": "audio/wav",
            "cache-control": "no-store",
            "content-length": String(bytes.byteLength)
          }
        })
      )
    );
  });

  it("retrieves exact project-owned bytes with main-only authentication", async () => {
    const client = new BackendApiClient(service());
    const result = await client.loadAuditionAudio(input(sha256));

    expect(Buffer.from(result.bytes)).toEqual(bytes);
    expect(result).toMatchObject({
      projectId: "project-1",
      auditionClipId: "clip-1",
      audioArtifactId: "artifact-1",
      mediaType: "audio/wav",
      byteSize: bytes.byteLength,
      sha256
    });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    const requestUrl =
      typeof url === "string"
        ? url
        : url instanceof URL
          ? url.href
          : url.url;
    expect(requestUrl).toContain(
      "/api/v1/projects/project-1/audition-clips/clip-1/audio?"
    );
    expect(requestUrl).not.toContain("file:");
    expect(new Headers(init?.headers).get("authorization")).toBe(
      "Bearer main-only-token"
    );
    expect(new Headers(init?.headers).get("accept")).toBe("audio/wav");
  });

  it("rejects hash, media type, no-store, and PCM format mismatches", async () => {
    const client = new BackendApiClient(service());
    await expect(client.loadAuditionAudio(input("b".repeat(64))))
      .rejects.toMatchObject({ code: "AUDITION_AUDIO_INTEGRITY_FAILED" });

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(responseBody(bytes), {
        status: 200,
        headers: {
          "content-type": "application/octet-stream",
          "cache-control": "no-store"
        }
      })
    );
    await expect(client.loadAuditionAudio(input(sha256)))
      .rejects.toMatchObject({ code: "AUDITION_AUDIO_RESPONSE_INVALID" });

    const invalidBytes = Buffer.alloc(bytes.byteLength, 1);
    const invalidHash = createHash("sha256").update(invalidBytes).digest("hex");
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(responseBody(invalidBytes), {
        status: 200,
        headers: {
          "content-type": "audio/wav",
          "cache-control": "no-store"
        }
      })
    );
    await expect(client.loadAuditionAudio(input(invalidHash)))
      .rejects.toMatchObject({ code: "AUDITION_AUDIO_FORMAT_INVALID" });
  });

  it("fails before transport for an over-bound declared artifact", async () => {
    const client = new BackendApiClient(service());
    await expect(
      client.loadAuditionAudio({
        ...input(sha256),
        byteSize: 24 * 1024 * 1024 + 1
      })
    ).rejects.toThrow("size was invalid");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("requests one bounded review-history scope with an opaque cursor", async () => {
    const client = new BackendApiClient(service());
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          correlationId: "correlation-review-history",
          projectId: "project-1",
          gateId: "per_role_audition_review",
          roleId: "role-1",
          pageSize: 1,
          total: 2,
          nextCursor: "opaque-page-3",
          items: [
            {
              contractVersion: "1.0.0",
              decisionId: "decision-2",
              reviewId: "review-role-1",
              projectId: "project-1",
              gateId: "per_role_audition_review",
              roleId: "role-1",
              decision: "approved",
              actor: { classification: "human", actorId: "local-user" },
              expectedReviewRevision: 2,
              evidenceFingerprint: "a".repeat(64),
              rationale: "Approved this exact audition evidence.",
              decidedAt: "2026-07-31T12:05:00Z",
              immutable: true,
              supersedesDecisionId: "decision-1",
              provenance: {
                origin: "human",
                producerId: "local-user",
                producerVersion: "1.0.0",
                recordedAt: "2026-07-31T12:05:00Z"
              }
            }
          ]
        }),
        { status: 200, headers: { "content-type": "application/json" } }
      )
    );

    const result = await client.listAuditionReviewDecisions({
      projectId: "project-1",
      gateId: "per_role_audition_review",
      roleId: "role-1",
      cursor: "opaque-page-2",
      limit: 1
    });

    expect(result.items.map((item) => item.decisionId)).toEqual(["decision-2"]);
    const requestUrl = fetchCallUrl(vi.mocked(fetch).mock.calls.at(-1));
    expect(requestUrl.pathname).toBe(
      "/api/v1/projects/project-1/audition-review-decisions"
    );
    expect(Object.fromEntries(requestUrl.searchParams)).toEqual({
      cursor: "opaque-page-2",
      limit: "1",
      gateId: "per_role_audition_review",
      roleId: "role-1"
    });

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          correlationId: "correlation-aggregate-history",
          projectId: "project-1",
          gateId: "voice_readiness_review",
          roleId: null,
          pageSize: 0,
          total: 0,
          items: []
        }),
        { status: 200, headers: { "content-type": "application/json" } }
      )
    );
    await client.listAuditionReviewDecisions({
      projectId: "project-1",
      gateId: "voice_readiness_review",
      roleId: null,
      limit: 50
    });
    const aggregateUrl = fetchCallUrl(vi.mocked(fetch).mock.calls.at(-1));
    expect(aggregateUrl.searchParams.has("roleId")).toBe(false);
  });

  it("sends only the canonical model-action and review-decision bodies", async () => {
    const client = new BackendApiClient(service());
    vi.mocked(fetch).mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );

    await expect(
      client.performModelPackageAction({
        projectId: "project-1",
        modelPackageId: "fixture-package",
        expectedManifestFingerprint: "a".repeat(64),
        expectedInstallationRevision: null,
        action: "verify",
        reason: "Verify the repository fixture for governed E2E.",
        idempotencyKey: "model-action-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      modelPackageId: "fixture-package",
      expectedManifestFingerprint: "a".repeat(64),
      expectedInstallationRevision: null,
      action: "verify",
      reason: "Verify the repository fixture for governed E2E.",
      idempotencyKey: "model-action-1"
    });

    await expect(
      client.createAuditionSession({
        projectId: "project-1",
        roleId: "role-1",
        evidence: auditionEvidence(),
        idempotencyKey: "fixture-session-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      roleId: "role-1",
      evidence: auditionEvidence(),
      idempotencyKey: "fixture-session-1"
    });

    const restrictedLocalAuditionActivation = {
      expectedInventoryFingerprint: "f".repeat(64),
      expectedWarningFingerprint:
        "13b8747ea2ced9de9cc1d0f67b5c018b25de7de02359a1480744db4a37939645",
      reason: "Create this exact bounded private local audition."
    } as const;
    await expect(
      client.createAuditionSession({
        projectId: "project-1",
        roleId: "role-1",
        evidence: auditionEvidence(),
        restrictedLocalAuditionActivation,
        idempotencyKey: "real-session-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      roleId: "role-1",
      evidence: auditionEvidence(),
      restrictedLocalAuditionActivation,
      idempotencyKey: "real-session-1"
    });

    await expect(
      client.decideAuditionReview({
        projectId: "project-1",
        gateId: "per_role_audition_review",
        reviewId: "review-1",
        roleId: "role-1",
        expectedReviewRevision: 1,
        expectedEvidenceFingerprint: "b".repeat(64),
        decision: "approve",
        rationale: "Approve the deterministic fixture audition lifecycle.",
        supersedesDecisionId: null,
        idempotencyKey: "review-decision-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      expectedReviewRevision: 1,
      expectedEvidenceFingerprint: "b".repeat(64),
      decision: "approve",
      rationale: "Approve the deterministic fixture audition lifecycle.",
      supersedesDecisionId: null,
      idempotencyKey: "review-decision-1"
    });

    const listeningAttestation = {
      auditionClipId: "clip-1",
      auditionClipRevision: 2,
      auditionClipFingerprint: "c".repeat(64),
      audioArtifactId: "artifact-1",
      audioArtifactSha256: "d".repeat(64),
      listened: true,
      disposition: "undecided"
    } as const;
    await expect(
      client.decideAuditionReview({
        projectId: "project-1",
        gateId: "per_role_audition_review",
        reviewId: "review-1",
        roleId: "role-1",
        expectedReviewRevision: 1,
        expectedEvidenceFingerprint: "b".repeat(64),
        decision: "request_changes",
        rationale: "I listened to this exact private real-local audition.",
        supersedesDecisionId: null,
        listeningAttestation,
        idempotencyKey: "real-review-decision-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      expectedReviewRevision: 1,
      expectedEvidenceFingerprint: "b".repeat(64),
      decision: "request_changes",
      rationale: "I listened to this exact private real-local audition.",
      supersedesDecisionId: null,
      listeningAttestation,
      idempotencyKey: "real-review-decision-1"
    });

    await expect(
      client.decidePronunciationEntry({
        projectId: "project-1",
        entryId: "pronunciation-1",
        expectedEntryRevision: 2,
        expectedEntryFingerprint: "c".repeat(64),
        expectedDictionaryRevision: 3,
        expectedDictionaryFingerprint: "d".repeat(64),
        decision: "approve",
        rationale: "Approve the reviewed pronunciation entry.",
        idempotencyKey: "pronunciation-decision-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      expectedEntryRevision: 2,
      expectedEntryFingerprint: "c".repeat(64),
      expectedDictionaryRevision: 3,
      expectedDictionaryFingerprint: "d".repeat(64),
      decision: "approve",
      rationale: "Approve the reviewed pronunciation entry.",
      idempotencyKey: "pronunciation-decision-1"
    });

    await expect(
      client.clearAuditionCache({
        projectId: "project-1",
        expectedProjectRevision: 7,
        reason: "Remove only project-owned private audition cache artifacts.",
        idempotencyKey: "cache-clear-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      expectedProjectRevision: 7,
      reason: "Remove only project-owned private audition cache artifacts.",
      idempotencyKey: "cache-clear-1"
    });

    await expect(
      client.createAuditionScript({
        projectId: "project-1",
        auditionSessionId: "session-1",
        expectedSessionRevision: 2,
        kind: "standardized_synthetic",
        text: "A bounded synthetic audition line.",
        sourceDocumentId: null,
        sourceRevision: null,
        sourceSpan: null,
        sourceTextSha256: "e".repeat(64),
        acceptedOptionalNormalizationIds: [],
        customPronunciationScopeIds: ["festival-names"],
        idempotencyKey: "script-1"
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      auditionSessionId: "session-1",
      expectedSessionRevision: 2,
      kind: "standardized_synthetic",
      text: "A bounded synthetic audition line.",
      sourceDocumentId: null,
      sourceRevision: null,
      sourceSpan: null,
      sourceTextSha256: "e".repeat(64),
      acceptedOptionalNormalizationIds: [],
      customPronunciationScopeIds: ["festival-names"],
      idempotencyKey: "script-1"
    });

    await expect(
      client.previewAuditionNormalization({
        projectId: "project-1",
        auditionSessionId: "session-1",
        expectedSessionRevision: 2,
        text: "A bounded synthetic audition line.",
        sourceTextSha256: "e".repeat(64),
        acceptedOptionalNormalizationIds: [],
        customPronunciationScopeIds: ["festival-names"]
      })
    ).rejects.toThrow();
    expect(requestJsonBody(vi.mocked(fetch).mock.calls.at(-1))).toEqual({
      auditionSessionId: "session-1",
      expectedSessionRevision: 2,
      text: "A bounded synthetic audition line.",
      sourceTextSha256: "e".repeat(64),
      acceptedOptionalNormalizationIds: [],
      customPronunciationScopeIds: ["festival-names"]
    });
  });

  it("streams a bounded selected ZIP without exposing its absolute path", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-model-upload-"));
    const selectedPath = path.join(directory, "kokoro-local.zip");
    const archiveBytes = Buffer.from("PK\u0003\u0004bounded-model-fixture", "binary");
    await writeFile(selectedPath, archiveBytes);
    let receivedBody = Buffer.alloc(0);
    let receivedPath = "";
    let receivedContentType = "";
    const server = createServer((request, response) => {
      receivedPath = request.url ?? "";
      receivedContentType = String(request.headers["content-type"] ?? "");
      const chunks: Buffer[] = [];
      request.on("data", (chunk: Buffer) => chunks.push(Buffer.from(chunk)));
      request.on("end", () => {
        receivedBody = Buffer.concat(chunks);
        response.writeHead(200, { "content-type": "application/json" });
        response.end(
          JSON.stringify({
            correlationId: "correlation-model-install",
            installation: {
              contractVersion: "1.0.0",
              installationId: "installation-1",
              modelPackageId: "kokoro-package-1",
              manifestFingerprint: "a".repeat(64),
              installationRevision: 1,
              storageKey: "model-storage-1",
              status: "installed",
              active: false,
              installedAt: "2026-07-31T12:00:00Z",
              updatedAt: "2026-07-31T12:00:00Z",
              lastAction: "install",
              actionReasonCode:
                "Install exact local bytes for restricted audition use only.",
              immutableEventId: "model-installation-event-1",
              provenance: {
                origin: "application",
                producerId: "local-model-package-manager",
                producerVersion: "1.0.0",
                recordedAt: "2026-07-31T12:00:00Z"
              }
            },
            verification: {
              contractVersion: "1.0.0",
              verificationId: "verification-install-1",
              installationId: "installation-1",
              modelPackageId: "kokoro-package-1",
              manifestFingerprint: "a".repeat(64),
              verificationFingerprint: "b".repeat(64),
              status: "verified",
              verifiedFileCount: 1,
              verifiedByteSize: archiveBytes.byteLength,
              unexpectedFileCount: 0,
              symlinkOrReparsePointDetected: false,
              checkedAt: "2026-07-31T12:00:00Z",
              blockingReasonCodes: [],
              provenance: {
                origin: "application",
                producerId: "local-model-package-manager",
                producerVersion: "1.0.0",
                recordedAt: "2026-07-31T12:00:00Z"
              }
            }
          })
        );
      });
    });

    try {
      await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
      });
      const address = server.address();
      if (address === null || typeof address === "string") {
        throw new Error("The upload test server did not bind a TCP port.");
      }
      const client = new BackendApiClient(service(address.port));
      const result = await client.applySelectedLocalModelPackage(
        {
          projectId: "project-1",
          modelPackageId: "kokoro-package-1",
          expectedManifestFingerprint: "a".repeat(64),
          expectedInstallationRevision: null,
          operation: "install",
          acknowledgeRestrictedLocalUse: true,
          reason: "Install exact local bytes for restricted audition use only.",
          idempotencyKey: "model-install-1"
        },
        selectedPath
      );

      expect(result.installation.modelPackageId).toBe("kokoro-package-1");
      expect(receivedPath).toBe(
        "/api/v1/projects/project-1/speech/model-packages/kokoro-package-1/install"
      );
      expect(receivedContentType).toMatch(/^multipart\/form-data; boundary=/u);
      const multipart = receivedBody.toString("binary");
      expect(multipart).toContain('name="acknowledgeRestrictedLocalUse"');
      expect(multipart).toContain("\r\n\r\ntrue\r\n");
      expect(multipart).toContain('filename="kokoro-local.zip"');
      expect(multipart).toContain(archiveBytes.toString("binary"));
      expect(multipart).not.toContain(directory);
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
      await rm(directory, { recursive: true, force: true });
    }
  });
});

function requestJsonBody(
  call: readonly [URL | RequestInfo, RequestInit?] | undefined
): unknown {
  const body = call?.[1]?.body;
  if (typeof body !== "string") {
    throw new Error("The request did not contain a JSON body.");
  }
  return JSON.parse(body) as unknown;
}

function fetchCallUrl(
  call: readonly [URL | RequestInfo, RequestInit?] | undefined
): URL {
  const value = call?.[0];
  if (typeof value === "string") return new URL(value);
  if (value instanceof URL) return value;
  if (value instanceof Request) return new URL(value.url);
  throw new Error("The request did not contain a URL.");
}

function input(sha256: string) {
  return {
    projectId: "project-1",
    auditionClipId: "clip-1",
    auditionSessionId: "session-1",
    audioArtifactId: "artifact-1",
    expectedClipRevision: 1,
    expectedClipFingerprint: "a".repeat(64),
    expectedArtifactSha256: sha256,
    mediaType: "audio/wav" as const,
    byteSize: pcmWave().byteLength
  };
}

function auditionEvidence() {
  return {
    projectId: "project-1",
    sourceDocumentId: "source-1",
    sourceRevision: 1,
    extractionId: "extraction-1",
    extractionRevision: 1,
    extractedTextSha256: "a".repeat(64),
    phase2RunId: "phase2-run-1",
    phase2SnapshotId: "phase2-snapshot-1",
    phase2SnapshotRevision: 1,
    phase2SnapshotFingerprint: "a".repeat(64),
    phase2CorrectionSetFingerprint: "a".repeat(64),
    castingRunId: "casting-run-1",
    approvedCastSnapshotId: "cast-snapshot-1",
    approvedCastSnapshotRevision: 1,
    approvedCastSnapshotFingerprint: "a".repeat(64),
    castAssignmentId: "assignment-1",
    castAssignmentRevision: 1,
    voiceProfileId: "voice-1",
    voiceProfileVersion: "1.0.0",
    voiceRuntimeBindingId: "binding-1",
    voiceRuntimeBindingFingerprint: "a".repeat(64),
    providerVoiceId: "provider-voice-1",
    providerId: "provider-1",
    providerVersion: "1.0.0",
    modelId: "model-1",
    modelVersion: "1.0.0",
    catalogRevisionId: "catalog-1",
    catalogFingerprint: "a".repeat(64),
    rightsRecordId: "rights-1",
    rightsRecordRevision: 1,
    rightsRecordFingerprint: "a".repeat(64),
    pronunciationDictionaryId: "dictionary-1",
    pronunciationDictionaryRevision: 1,
    pronunciationDictionaryFingerprint: "a".repeat(64),
    runtimeProfileId: "runtime-1",
    runtimeProfileFingerprint: "a".repeat(64),
    modelPackageId: "package-1",
    modelPackageFingerprint: "a".repeat(64),
    producerVersion: "1.0.0"
  };
}

function service(port = 43_210): ServiceManager {
  return {
    connection: () => ({ port, token: "main-only-token" })
  } as unknown as ServiceManager;
}

function pcmWave(): Buffer {
  const value = Buffer.alloc(48);
  value.write("RIFF", 0, "ascii");
  value.writeUInt32LE(40, 4);
  value.write("WAVE", 8, "ascii");
  value.write("fmt ", 12, "ascii");
  value.writeUInt32LE(16, 16);
  value.writeUInt16LE(1, 20);
  value.writeUInt16LE(1, 22);
  value.writeUInt32LE(24_000, 24);
  value.writeUInt32LE(48_000, 28);
  value.writeUInt16LE(2, 32);
  value.writeUInt16LE(16, 34);
  value.write("data", 36, "ascii");
  value.writeUInt32LE(4, 40);
  value.writeInt16LE(1_000, 44);
  value.writeInt16LE(-1_000, 46);
  return value;
}

function responseBody(value: Buffer): ArrayBuffer {
  return Uint8Array.from(value).buffer;
}
