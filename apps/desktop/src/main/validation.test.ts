// @vitest-environment node

import { describe, expect, it } from "vitest";

import {
  parseCreateProjectRequest,
  parseDecideImportReviewRequest,
  parseReadyLine,
  validateDecideImportReviewResponse,
  validateImportReviewResponse,
  validateProjectDetail,
  ValidationError
} from "./validation";

describe("desktop boundary validation", () => {
  it("accepts the bounded service readiness record", () => {
    expect(
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"nonce-1","protocolVersion":"1.0.0"}',
        "nonce-1"
      )
    ).toEqual({ port: 43129, instanceId: "instance-1" });
  });

  it("rejects a readiness record with the wrong nonce", () => {
    expect(() =>
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"other","protocolVersion":"1.0.0"}',
        "nonce-1"
      )
    ).toThrow(ValidationError);
  });

  it("rejects missing or incompatible readiness protocol versions", () => {
    expect(() =>
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"nonce-1"}',
        "nonce-1"
      )
    ).toThrow("protocol version");

    expect(() =>
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"nonce-1","protocolVersion":"2.0.0"}',
        "nonce-1"
      )
    ).toThrow("protocol version");
  });

  it("rejects unknown IPC fields and oversized project names", () => {
    expect(() =>
      parseCreateProjectRequest({
        contractVersion: "1.0.0",
        payload: {
          name: "Synthetic Demo",
          idempotencyKey: "request-1",
          arbitraryPath: "C:\\private\\story.md"
        }
      })
    ).toThrow("unknown field");

    expect(() =>
      parseCreateProjectRequest({
        contractVersion: "1.0.0",
        payload: {
          name: "x".repeat(121),
          idempotencyKey: "request-1"
        }
      })
    ).toThrow("invalid length");
  });

  it("accepts only an exact import-review decision envelope", () => {
    const parsed = parseDecideImportReviewRequest({
      contractVersion: "1.0.0",
      payload: {
        projectId: "project-1",
        reviewId: "review-1",
        sourceDocumentId: "document-1",
        extractionId: "extraction-1",
        candidateStoryId: "story-1",
        candidateStoryRevision: 1,
        decision: "approved",
        rationale: "Reviewed the exact extraction.",
        expectedRevision: 1,
        evidenceFingerprint: "a".repeat(64),
        idempotencyKey: "decision-1"
      }
    });
    expect(parsed.payload.decision).toBe("approved");
    expect(parsed.payload.evidenceFingerprint).toHaveLength(64);
    expect(
      parseDecideImportReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          ...parsed.payload,
          rationale: "x".repeat(2_000)
        }
      }).payload.rationale
    ).toHaveLength(2_000);

    expect(() =>
      parseDecideImportReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          ...parsed.payload,
          arbitraryPath: "C:\\private\\story.docx"
        }
      })
    ).toThrow("unknown field");
    expect(() =>
      parseDecideImportReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          ...parsed.payload,
          evidenceFingerprint: "A".repeat(64)
        }
      })
    ).toThrow("lowercase SHA-256");
    expect(() =>
      parseDecideImportReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          ...parsed.payload,
          rationale: "x".repeat(2_001)
        }
      })
    ).toThrow("invalid length");
  });

  it("validates extraction/review projections and analysis gating", () => {
    const review = validImportReview();
    expect(
      validateImportReviewResponse({
        correlationId: "correlation-1",
        review
      }, reviewExpectation()).review.reviewId
    ).toBe("review-1");

    expect(
      validateProjectDetail(
        {
          correlationId: "correlation-1",
          project: {
            projectId: "project-1",
            name: "Synthetic Demo",
            revision: 2
          },
          sourceDocuments: [validSourceDocument()],
          extractions: [validExtraction()],
          importReviews: [review],
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
        },
        "project-1"
      ).analysisAllowed
    ).toBe(false);

    expect(() =>
      validateImportReviewResponse({
        correlationId: "correlation-1",
        review: {
          ...review,
          previewText: "x".repeat(64 * 1024 + 1)
        }
      }, reviewExpectation())
    ).toThrow("preview");
  });

  it("rejects import reviews that do not match the requested evidence", () => {
    expect(() =>
      validateImportReviewResponse(
        {
          correlationId: "correlation-1",
          review: {
            ...validImportReview(),
            extractionId: "extraction-other"
          }
        },
        reviewExpectation()
      )
    ).toThrow("requested evidence");
  });

  it("validates the exact runtime source-document shape and ancestry", () => {
    const first = {
      ...validSourceDocument(),
      extractionStatus: "running",
      textSha256: "d".repeat(64),
      encoding: "utf-8",
      newlineStyle: "crlf"
    };
    const second = {
      ...validSourceDocument(),
      documentId: "document-2",
      displayName: "sample-story-revision.docx",
      contentSha256: "b".repeat(64),
      sourceRevision: 2,
      supersedesDocumentId: "document-1"
    };

    expect(
      validateProjectDetail(
        projectDetailWithSources([first, second]),
        "project-1"
      ).sourceDocuments[1]?.supersedesDocumentId
    ).toBe("document-1");

    expect(() =>
      validateProjectDetail(
        projectDetailWithSources([
          { ...first, pageCount: 1 }
        ]),
        "project-1"
      )
    ).toThrow("unknown field");
    expect(() =>
      validateProjectDetail(
        projectDetailWithSources([
          { ...first, originalBytesPreserved: undefined }
        ]),
        "project-1"
      )
    ).toThrow("byte preservation");
    expect(() =>
      validateProjectDetail(
        projectDetailWithSources([
          first,
          { ...second, supersedesDocumentId: "document-missing" }
        ]),
        "project-1"
      )
    ).toThrow("revision ancestry");
    expect(() =>
      validateProjectDetail(
        projectDetailWithSources([
          first,
          { ...second, sourceRevision: 1 }
        ]),
        "project-1"
      )
    ).toThrow("project identity");
  });

  it("requires a complete and request-bound import approval decision", () => {
    const expected = {
      ...reviewExpectation(),
      decision: "approved" as const,
      rationale: "Reviewed the exact extraction.",
      expectedRevision: 1,
      idempotencyKey: "decision-1"
    };
    const decision = validApprovalDecision();
    const review = {
      ...validImportReview(),
      revision: 2,
      state: "approved",
      latestDecision: decision
    };

    expect(
      validateDecideImportReviewResponse(
        {
          correlationId: "decision-correlation",
          review,
          decision,
          projectRevision: 3,
          analysisAllowed: true
        },
        expected
      ).decision.decision
    ).toBe("approved");

    const oversizedRationale = "x".repeat(2_001);
    const oversizedDecision = {
      ...decision,
      rationale: oversizedRationale
    };
    expect(() =>
      validateDecideImportReviewResponse(
        {
          correlationId: "decision-correlation",
          review: {
            ...review,
            latestDecision: oversizedDecision
          },
          decision: oversizedDecision,
          projectRevision: 3,
          analysisAllowed: true
        },
        {
          ...expected,
          rationale: oversizedRationale
        }
      )
    ).toThrow("invalid length");

    expect(() =>
      validateDecideImportReviewResponse(
        {
          correlationId: "decision-correlation",
          review,
          decision: {
            decisionId: "decision-1",
            decision: "approved",
            rationale: "Reviewed the exact extraction.",
            evidenceFingerprint: "c".repeat(64),
            immutable: true
          },
          projectRevision: 3,
          analysisAllowed: true
        },
        expected
      )
    ).toThrow("schemaVersion");

    expect(() =>
      validateDecideImportReviewResponse(
        {
          correlationId: "decision-correlation",
          review,
          decision,
          projectRevision: 3,
          analysisAllowed: false
        },
        expected
      )
    ).toThrow("decision evidence");
  });
});

function projectDetailWithSources(
  sourceDocuments: readonly unknown[]
) {
  return {
    correlationId: "correlation-1",
    project: {
      projectId: "project-1",
      name: "Synthetic Demo",
      revision: 2
    },
    sourceDocuments,
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

function validExtraction() {
  return {
    schemaVersion: "1.0.0",
    revision: 1,
    provenance: {
      origin: "system",
      recordedAt: "2026-07-29T12:00:00Z",
      actorId: "document-extractor"
    },
    extractionId: "extraction-1",
    projectId: "project-1",
    sourceDocumentId: "document-1",
    declaredFormat: "docx",
    detectedFormat: "docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "complete",
    adapterId: "secure-ooxml",
    adapterVersion: "1.0.0",
    parserDependency: "lxml",
    parserVersion: "6.0.0",
    sourceSha256: "a".repeat(64),
    sourceByteCount: 512,
    extractedTextSha256: "b".repeat(64),
    extractedCharacterCount: 42,
    sectionCount: 2,
    warnings: [],
    quality: {
      classification: "structured_extraction",
      confidence: 0.99
    },
    retryability: "not_retryable",
    reviewRequired: true,
    originalPreserved: true,
    createdAt: "2026-07-29T12:00:00Z",
    updatedAt: "2026-07-29T12:01:00Z",
    completedAt: "2026-07-29T12:01:00Z"
  };
}

function validSourceDocument() {
  return {
    schemaVersion: "1.0.0",
    revision: 1,
    provenance: {
      origin: "import",
      recordedAt: "2026-07-29T12:00:00Z",
      actorId: "document-ingest",
      inputFingerprint: "a".repeat(64)
    },
    documentId: "document-1",
    projectId: "project-1",
    displayName: "sample-story.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    declaredFormat: "docx",
    contentSha256: "a".repeat(64),
    byteLength: 512,
    importedAt: "2026-07-29T12:00:00Z",
    originalTextPreserved: true,
    originalBytesPreserved: true,
    storageKey: "projects/project-1/sources/source-1",
    extractionStatus: "complete",
    sourceRevision: 1,
    warnings: []
  };
}

function validImportReview() {
  return {
    schemaVersion: "1.0.0",
    revision: 1,
    provenance: {
      origin: "system",
      recordedAt: "2026-07-29T12:01:00Z",
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
    previewText: "A bounded synthetic preview.",
    previewTruncated: false,
    warnings: [],
    createdAt: "2026-07-29T12:01:00Z",
    updatedAt: "2026-07-29T12:01:00Z"
  };
}

function reviewExpectation() {
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

function validApprovalDecision() {
  return {
    schemaVersion: "1.0.0",
    revision: 2,
    provenance: {
      origin: "human",
      recordedAt: "2026-07-29T12:02:00Z",
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
    rationale: "Reviewed the exact extraction.",
    evidenceFingerprint: "c".repeat(64),
    decidedAt: "2026-07-29T12:02:00Z",
    immutable: true
  };
}
