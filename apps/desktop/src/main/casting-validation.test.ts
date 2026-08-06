// @vitest-environment node

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
  GOVERNED_VOICE_CASTING_PROFILE_ID,
  LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
  LEGACY_GOVERNED_VOICE_CASTING_PROFILE_ID,
  type CastingReviewDecision,
  VOICE_CASTING_CONTRACT_VERSION,
  VOICE_CASTING_PRODUCER_ID
} from "@cinematic-story-studio/contracts";

import type {
  CastingRunEvidenceInput,
  CreateCustomProductionRoleInput
} from "../shared/casting-api";
import {
  parseAppendCastingCorrectionRequest,
  parseCreateCastingRunRequest,
  parseCreateCustomProductionRoleRequest,
  parseDecideCastingReviewRequest,
  parseListCastingCandidatesRequest,
  parseListVoiceCatalogRequest,
  validateCastAssignmentsResponse,
  validateCastingCandidatesResponse,
  validateCastingConflictsResponse,
  validateCastingReviewsResponse,
  validateCastingRunResponse,
  validateCreateCustomProductionRoleResponse,
  validateVoiceCatalogResponse
} from "./casting-validation";

const digest = "a".repeat(64);

function canonicalJson(value: unknown): string {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    typeof value === "number"
  ) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const item = value as Readonly<Record<string, unknown>>;
  return `{${Object.keys(item)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(item[key])}`)
    .join(",")}}`;
}

function withProjectionFingerprint<T extends Readonly<Record<string, unknown>>>(
  value: T
): T & { readonly outputFingerprint: string } {
  return {
    ...value,
    outputFingerprint: createHash("sha256")
      .update(canonicalJson(value), "utf8")
      .digest("hex")
  };
}

function request(payload: Readonly<Record<string, unknown>>): unknown {
  return {
    contractVersion: "1.0.0",
    payload
  };
}

function runEvidence(): CastingRunEvidenceInput {
  return {
    projectId: "project-1",
    runId: "casting-run-1",
    expectedRunFingerprint: digest,
    expectedCatalogRevisionId: "catalog-1",
    expectedCatalogFingerprint: digest,
    expectedSnapshotId: "analysis-snapshot-1",
    expectedSnapshotRevision: 2,
    expectedSnapshotFingerprint: digest,
    expectedCastingProfileFingerprint:
      GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
  };
}

describe("casting desktop request validation", () => {
  it("binds run creation to every approved Phase 2 and catalog identity", () => {
    const parsed = parseCreateCastingRunRequest(
      request({
        projectId: "project-1",
        expectedAnalysisRunId: "analysis-run-1",
        expectedSnapshotId: "analysis-snapshot-1",
        expectedSnapshotRevision: 2,
        expectedSnapshotFingerprint: digest,
        expectedCorrectionSetFingerprint: "b".repeat(64),
        expectedImportReviewDecisionId: "import-decision-1",
        expectedAnalysisGateDecisionIds: {
          storyStructureReview: "decision-structure",
          characterRegistryReview: "decision-characters",
          dialogueAttributionReview: "decision-dialogue",
          wholeBookAnalysisReview: "decision-whole-book"
        },
        expectedCatalogRevisionId: "catalog-1",
        expectedCatalogFingerprint: "c".repeat(64),
        expectedCastingProfileFingerprint: "d".repeat(64),
        idempotencyKey: "casting-create-1"
      })
    );

    expect(parsed.payload.expectedAnalysisGateDecisionIds).toEqual({
      storyStructureReview: "decision-structure",
      characterRegistryReview: "decision-characters",
      dialogueAttributionReview: "decision-dialogue",
      wholeBookAnalysisReview: "decision-whole-book"
    });
    expect(parsed.payload.expectedSnapshotRevision).toBe(2);
  });

  it("rejects omitted, extra, and partial prerequisite evidence", () => {
    expect(() =>
      parseCreateCastingRunRequest(
        request({
          projectId: "project-1",
          unexpectedApproval: true
        })
      )
    ).toThrow("Unexpected field");
    expect(() =>
      parseCreateCastingRunRequest(
        request({
          projectId: "project-1",
          expectedAnalysisRunId: "analysis-run-1"
        })
      )
    ).toThrow("Missing field");
    expect(() =>
      parseCreateCastingRunRequest(
        request({
          projectId: "project-1",
          expectedAnalysisRunId: "analysis-run-1",
          expectedSnapshotId: "snapshot-1",
          expectedSnapshotRevision: 1,
          expectedSnapshotFingerprint: digest,
          expectedCorrectionSetFingerprint: digest,
          expectedImportReviewDecisionId: "import-decision-1",
          expectedAnalysisGateDecisionIds: {
            storyStructureReview: "decision-1"
          },
          expectedCatalogRevisionId: "catalog-1",
          expectedCatalogFingerprint: digest,
          expectedCastingProfileFingerprint: digest,
          idempotencyKey: "create-1"
        })
      )
    ).toThrow("Missing field");
  });

  it("requires catalog revision and fingerprint expectations together", () => {
    expect(() =>
      parseListVoiceCatalogRequest(
        request({
          projectId: "project-1",
          expectedCatalogRevisionId: "catalog-1"
        })
      )
    ).toThrow("must be supplied together");
  });

  it("accepts the complete canonical synthetic catalog response", () => {
    const catalog = JSON.parse(
      readFileSync(
        new URL(
          "../../../local-service/src/cinematic_story_service/catalogs/synthetic_voice_catalog.v1.json",
          import.meta.url
        ),
        "utf8"
      )
    ) as Readonly<Record<string, unknown>>;
    const voices = catalog.voices as readonly unknown[];

    expect(
      validateVoiceCatalogResponse(
        {
          correlationId: "catalog-correlation",
          catalogRevision: catalog.catalogRevision,
          providers: catalog.providers,
          models: catalog.models,
          items: voices,
          rights: catalog.rights,
          total: voices.length,
          pageSize: voices.length
        },
        { projectId: "project-1", limit: 50 }
      )
    ).toMatchObject({
      correlationId: "catalog-correlation",
      total: voices.length,
      pageSize: voices.length
    });
  });

  it("accepts bounded provider-repository model IDs without widening other IDs", () => {
    const catalog = JSON.parse(
      readFileSync(
        new URL(
          "../../../local-service/src/cinematic_story_service/catalogs/synthetic_voice_catalog.v1.json",
          import.meta.url
        ),
        "utf8"
      )
    ) as Record<string, unknown>;
    const catalogRevision = structuredClone(
      catalog.catalogRevision
    ) as Record<string, unknown>;
    const providers = structuredClone(catalog.providers) as Record<
      string,
      unknown
    >[];
    const models = structuredClone(catalog.models) as Record<string, unknown>[];
    const voices = structuredClone(catalog.voices) as Record<string, unknown>[];
    const firstModel = models[0];
    if (firstModel === undefined || typeof firstModel.modelId !== "string") {
      throw new Error("The model fixture was empty or invalid.");
    }
    const priorModelId = firstModel.modelId;
    const repositoryModelId = "onnx-community/Kokoro-82M-v1.0-ONNX";
    firstModel.modelId = repositoryModelId;
    firstModel.modelVersion = "1.0";
    catalogRevision.modelDescriptorIds = (
      catalogRevision.modelDescriptorIds as unknown[]
    ).map((value) => (value === priorModelId ? repositoryModelId : value));
    for (const voice of voices) {
      if (voice.modelId === priorModelId) voice.modelId = repositoryModelId;
    }

    const response = {
      correlationId: "catalog-provider-repository-model",
      catalogRevision,
      providers,
      models,
      items: voices,
      rights: catalog.rights,
      total: voices.length,
      pageSize: voices.length
    };
    expect(
      validateVoiceCatalogResponse(response, {
        projectId: "project-1",
        limit: 50
      })
    ).toMatchObject({ total: voices.length });

    for (const invalidModelId of [
      "/leading-slash",
      "trailing-slash/",
      "doubled//slash",
      "model path",
      "namespace\\model",
      "namespace/model?query"
    ]) {
      const invalidModels = structuredClone(models);
      if (invalidModels[0] === undefined) throw new Error("The model fixture was empty.");
      invalidModels[0].modelId = invalidModelId;
      expect(() =>
        validateVoiceCatalogResponse(
          { ...response, models: invalidModels },
          { projectId: "project-1", limit: 50 }
        )
      ).toThrow("must be a model identifier");
    }

    const invalidProviders = structuredClone(providers);
    if (invalidProviders[0] === undefined) {
      throw new Error("The provider fixture was empty.");
    }
    invalidProviders[0].providerId = "provider/scoped";
    expect(() =>
      validateVoiceCatalogResponse(
        { ...response, providers: invalidProviders },
        { projectId: "project-1", limit: 50 }
      )
    ).toThrow("must be an identifier");
  });

  it("accepts nullable real-voice age-presentation and energy metadata", () => {
    const catalog = JSON.parse(
      readFileSync(
        new URL(
          "../../../local-service/src/cinematic_story_service/catalogs/synthetic_voice_catalog.v1.json",
          import.meta.url
        ),
        "utf8"
      )
    ) as Record<string, unknown>;
    const voices = structuredClone(catalog.voices) as Record<string, unknown>[];
    const firstVoice = voices[0];
    if (firstVoice === undefined) throw new Error("The voice fixture was empty.");
    firstVoice.agePresentationRange = null;
    firstVoice.energyRange = null;

    expect(
      validateVoiceCatalogResponse(
        {
          correlationId: "catalog-nullable-real-metadata",
          catalogRevision: catalog.catalogRevision,
          providers: catalog.providers,
          models: catalog.models,
          items: voices,
          rights: catalog.rights,
          total: voices.length,
          pageSize: voices.length
        },
        { projectId: "project-1", limit: 50 }
      )
    ).toMatchObject({ total: voices.length });
  });

  it("caps final candidates at twelve and rejects stale ownership fields", () => {
    expect(
      parseListCastingCandidatesRequest(
        request({
          ...runEvidence(),
          roleId: "role-1",
          expectedRoleRevision: 4,
          limit: 12
        })
      ).payload.limit
    ).toBe(12);
    expect(() =>
      parseListCastingCandidatesRequest(
        request({
          ...runEvidence(),
          roleId: "role-1",
          expectedRoleRevision: 4,
          limit: 13
        })
      )
    ).toThrow("1 to 12");
    expect(() =>
      parseListCastingCandidatesRequest(
        request({
          ...runEvidence(),
          expectedSnapshotFingerprint: "not-a-fingerprint",
          roleId: "role-1",
          expectedRoleRevision: 4
        })
      )
    ).toThrow("lowercase SHA-256");
  });

  it("verifies exact candidate and assessment projections separately from base evidence", () => {
    const input = {
      ...runEvidence(),
      roleId: "role-1",
      expectedRoleRevision: 1,
      limit: 12
    };
    const provenance = {
      origin: "runtime_agent",
      producerId: "voice-casting-orchestrator",
      producerVersion: "1.0.0",
      recordedAt: "2026-07-31T12:00:00Z",
      inputFingerprint: digest
    };
    const assessment = withProjectionFingerprint({
      contractVersion: VOICE_CASTING_CONTRACT_VERSION,
      assessmentId: "assessment-1",
      roleId: input.roleId,
      voiceProfileId: "voice-1",
      compatibilityStatus: "compatible",
      compatibilityScore: 0.8,
      confidence: {
        score: 0.8,
        classification: "high",
        basis: "Deterministic declared metadata.",
        calibrationId: "governed-casting-rules-v1"
      },
      hardConstraints: [
        {
          constraintId: "language_support",
          result: "pass",
          explanation: "Declared language support matches."
        }
      ],
      softPreferences: [],
      rightsEligibility: "eligible",
      languageEligibility: "pass",
      providerAvailability: "available",
      modelAvailability: "available",
      longFormSuitability: "suitable",
      explanation: "Deterministic declared-metadata assessment.",
      provenance,
      inputFingerprint: digest,
      baseEvidenceFingerprint: "b".repeat(64)
    });
    const candidate = withProjectionFingerprint({
      contractVersion: VOICE_CASTING_CONTRACT_VERSION,
      candidateId: "candidate-1",
      castingRunId: input.runId,
      roleId: input.roleId,
      voiceProfileId: "voice-1",
      rank: 1,
      preReductionRank: 1,
      assessment,
      conflictIds: [],
      conflictWarnings: [],
      rejectedByCorrectionId: null,
      provenance,
      inputFingerprint: digest,
      baseEvidenceFingerprint: "c".repeat(64)
    });
    const response = {
      correlationId: "correlation-1",
      castingRunId: input.runId,
      pageSize: 1,
      total: 1,
      items: [candidate]
    };

    expect(validateCastingCandidatesResponse(response, input)).toBe(response);

    const tamperedCandidate = structuredClone(response);
    tamperedCandidate.items[0].rank = 2;
    expect(() =>
      validateCastingCandidatesResponse(tamperedCandidate, input)
    ).toThrow("did not bind its exact projection");

    const tamperedAssessment = structuredClone(response);
    tamperedAssessment.items[0].assessment.explanation =
      "A projection mutation not covered by its claimed digest.";
    expect(() =>
      validateCastingCandidatesResponse(tamperedAssessment, input)
    ).toThrow("did not bind its exact projection");
  });

  it("accepts governed 300-role conflict boundaries and verifies the projection", () => {
    const input = { ...runEvidence(), limit: 200 };
    const roleIds = Array.from({ length: 300 }, (_, index) => `role-${index + 1}`);
    const conflict = withProjectionFingerprint({
      contractVersion: VOICE_CASTING_CONTRACT_VERSION,
      conflictId: "conflict-1",
      castingRunId: input.runId,
      category: "voice_reuse_threshold_exceeded",
      severity: "warning",
      roleIds,
      voiceProfileIds: ["voice-1"],
      explanation: "Declared voice reuse exceeds the governed threshold.",
      metadataOnly: true,
      acousticSimilarityClaimed: false,
      resolutionState: "open",
      dispositionCorrectionId: null,
      provenance: {
        origin: "runtime_agent",
        producerId: "voice-casting-orchestrator",
        producerVersion: "1.0.0",
        recordedAt: "2026-07-31T12:00:00Z",
        inputFingerprint: digest
      },
      inputFingerprint: digest,
      baseEvidenceFingerprint: "d".repeat(64)
    });
    const response = {
      correlationId: "correlation-1",
      castingRunId: input.runId,
      pageSize: 1,
      total: 1,
      items: [conflict]
    };

    expect(validateCastingConflictsResponse(response, input)).toBe(response);

    const conflictProjection = Object.fromEntries(
      Object.entries(conflict).filter(([key]) => key !== "outputFingerprint")
    );
    const overLimitConflict = withProjectionFingerprint({
      ...conflictProjection,
      roleIds: [...roleIds, "role-301"]
    });
    expect(() =>
      validateCastingConflictsResponse(
        { ...response, items: [overLimitConflict] },
        input
      )
    ).toThrow("at most 300");
  });

  it("accepts only a complete content-free custom-role definition", () => {
    const parsed = parseCreateCustomProductionRoleRequest(
      request({
        ...runEvidence(),
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
        reason: "A producer-defined role with no manuscript source.",
        expectedCorrectionSetFingerprint: "b".repeat(64),
        expectedCastingProfileFingerprint:
          GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
        idempotencyKey: "custom-role-create-1"
      })
    );

    expect(parsed.payload).toMatchObject({
      definitionId: "custom-role-a",
      label: "Festival announcer",
      performanceRequirements: {
        language: "en",
        locales: ["en-US"],
        longFormRequired: false
      },
      reason: "A producer-defined role with no manuscript source."
    });
    expect(() =>
      parseCreateCustomProductionRoleRequest(
        request({
          ...parsed.payload,
          performanceRequirements: {
            ...parsed.payload.performanceRequirements,
            inferredWordCount: 10
          }
        })
      )
    ).toThrow("Unexpected field");
    expect(() =>
      parseCreateCustomProductionRoleRequest(
        request({
          ...parsed.payload,
          reason: " "
        })
      )
    ).toThrow("outside the allowed bounds");
  });

  it("rejects a custom-role response that carries inferred story ranges", () => {
    const input = customRoleInput();
    const response = customRoleResponse(input);

    expect(
      validateCreateCustomProductionRoleResponse(response, input)
    ).toBe(response);

    const malformed = structuredClone(response) as {
      role: {
        range: {
          firstSceneOrdinal: number | null;
        };
      };
    };
    malformed.role.range.firstSceneOrdinal = 0;
    expect(() =>
      validateCreateCustomProductionRoleResponse(malformed, input)
    ).toThrow("did not match its definition");
  });

  it("accepts only exact current or legacy governed casting-profile pairs", () => {
    const customInput = customRoleInput();
    const current = {
      correlationId: "correlation-current-casting-run",
      run: customRoleResponse(customInput).run
    };
    const input = {
      projectId: customInput.projectId,
      runId: customInput.runId
    };
    expect(validateCastingRunResponse(current, input)).toBe(current);

    if (current.run.approvedCastSnapshot === null) {
      throw new Error("The casting-run fixture omitted its snapshot.");
    }
    const legacy = {
      ...current,
      run: {
        ...current.run,
        profile: {
          profileId: LEGACY_GOVERNED_VOICE_CASTING_PROFILE_ID,
          fingerprint: LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
        },
        approvedCastSnapshot: {
          ...current.run.approvedCastSnapshot,
          castingProfileFingerprint:
            LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
        }
      }
    };
    expect(validateCastingRunResponse(legacy, input)).toBe(legacy);

    for (const profile of [
      {
        profileId: GOVERNED_VOICE_CASTING_PROFILE_ID,
        fingerprint: LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
      },
      {
        profileId: LEGACY_GOVERNED_VOICE_CASTING_PROFILE_ID,
        fingerprint: GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
      },
      {
        profileId: "governed-voice-casting-v1@9.9.9",
        fingerprint: GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
      },
      {
        profileId: GOVERNED_VOICE_CASTING_PROFILE_ID,
        fingerprint: "f".repeat(64)
      }
    ]) {
      expect(() =>
        validateCastingRunResponse(
          {
            ...current,
            run: { ...current.run, profile }
          },
          input
        )
      ).toThrow("profile or producer was not governed");
    }
  });

  it("loads a full legacy assignment and review workspace without accepting crossed evidence", () => {
    const legacyEvidence = {
      ...runEvidence(),
      expectedCastingProfileFingerprint:
        LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
    };
    const assignment = castAssignment(
      LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
    );
    const assignmentResponse = {
      correlationId: "correlation-legacy-assignments",
      castingRunId: legacyEvidence.runId,
      pageSize: 1,
      total: 1,
      items: [assignment]
    };
    expect(
      validateCastAssignmentsResponse(assignmentResponse, {
        ...legacyEvidence,
        limit: 200
      })
    ).toBe(assignmentResponse);

    const currentReviewResponse = castingReviewsResponse("approved", "human");
    const legacyReviewResponse = {
      ...currentReviewResponse,
      items: currentReviewResponse.items.map((review) => ({
        ...review,
        evidence: {
          ...review.evidence,
          castingProfileFingerprint:
            LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
        }
      }))
    };
    const legacyReviewInput = {
      ...castingReviewsInput(),
      expectedCastingProfileFingerprint:
        LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
    };
    expect(
      validateCastingReviewsResponse(
        legacyReviewResponse,
        legacyReviewInput
      )
    ).toBe(legacyReviewResponse);

    expect(() =>
      validateCastAssignmentsResponse(
        {
          ...assignmentResponse,
          items: [
            {
              ...assignment,
              castingProfileFingerprint:
                GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
            }
          ]
        },
        { ...legacyEvidence, limit: 200 }
      )
    ).toThrow("another casting profile");
    expect(() =>
      validateCastingReviewsResponse(
        {
          ...legacyReviewResponse,
          items: legacyReviewResponse.items.map((review, index) =>
            index === 0
              ? {
                  ...review,
                  evidence: {
                    ...review.evidence,
                    castingProfileFingerprint:
                      GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
                  }
                }
              : review
          )
        },
        legacyReviewInput
      )
    ).toThrow("did not match requested ownership");
  });

  it("accepts a matching human voice selection and rejects ambiguous selection data", () => {
    const parsed = parseAppendCastingCorrectionRequest(
      request({
        projectId: "project-1",
        runId: "casting-run-1",
        operation: "select_voice",
        targetRoleId: "role-1",
        expectedRoleRevision: 4,
        expectedRunFingerprint: digest,
        expectedCatalogFingerprint: digest,
        expectedSnapshotFingerprint: digest,
        expectedCorrectionSetFingerprint: digest,
        previousEffectiveFingerprint: digest,
        voiceProfileId: "voice-amber",
        correctedValue: {
          voiceProfileId: "voice-amber"
        },
        reason: "Selected after reviewing declared metadata.",
        supersedesCorrectionId: null,
        idempotencyKey: "correction-select-1"
      })
    );
    expect(parsed.payload.voiceProfileId).toBe("voice-amber");
    expect(() =>
      parseAppendCastingCorrectionRequest(
        request({
          ...parsed.payload,
          correctedValue: {
            voiceProfileId: "voice-other"
          }
        })
      )
    ).toThrow("matching voice");
  });

  it.each([
    ["clear_assignment", { expectedAssignmentId: "assignment-1" }],
    ["lock_assignment", { assignmentId: "assignment-1" }],
    ["unlock_assignment", { lockedAssignmentId: "assignment-1" }],
    ["mark_intentionally_uncast", { intentionallyUncast: true }],
    ["change_role_label", { effectiveDisplayLabel: "Archivist" }],
    [
      "acknowledge_restricted_rights",
      { rightsRecordId: "rights-1", rightsRecordRevision: 2 }
    ],
    [
      "approve_voice_reuse",
      { conflictId: "conflict-1", approvedRoleIds: ["role-1", "role-2"] }
    ],
    ["reject_candidate", { candidateId: "candidate-1" }],
    [
      "record_custom_rationale",
      { rationale: "Keep the voices distinct." }
    ]
  ] as const)("validates the %s correction overlay", (operation, value) => {
    const parsed = parseAppendCastingCorrectionRequest(
      request({
        projectId: "project-1",
        runId: "casting-run-1",
        operation,
        targetRoleId: "role-1",
        expectedRoleRevision: 4,
        expectedRunFingerprint: digest,
        expectedCatalogFingerprint: digest,
        expectedSnapshotFingerprint: digest,
        expectedCorrectionSetFingerprint: digest,
        previousEffectiveFingerprint: digest,
        voiceProfileId:
          operation === "acknowledge_restricted_rights" ||
          operation === "reject_candidate"
            ? "voice-amber"
            : null,
        correctedValue: value,
        reason: "Human casting correction.",
        supersedesCorrectionId: null,
        idempotencyKey: `correction-${operation}`
      })
    );
    expect(parsed.payload.operation).toBe(operation);
  });

  it("aligns voice-reuse correction role IDs with the 300-role governed cap", () => {
    const approvedRoleIds = Array.from(
      { length: 300 },
      (_, index) => `role-${index + 1}`
    );
    const payload = {
      projectId: "project-1",
      runId: "casting-run-1",
      operation: "approve_voice_reuse",
      targetRoleId: "role-1",
      expectedRoleRevision: 4,
      expectedRunFingerprint: digest,
      expectedCatalogFingerprint: digest,
      expectedSnapshotFingerprint: digest,
      expectedCorrectionSetFingerprint: digest,
      previousEffectiveFingerprint: digest,
      voiceProfileId: null,
      correctedValue: {
        conflictId: "conflict-1",
        approvedRoleIds
      },
      reason: "Human casting correction.",
      supersedesCorrectionId: null,
      idempotencyKey: "correction-approve-large-reuse"
    };

    expect(
      parseAppendCastingCorrectionRequest(request(payload)).payload
        .correctedValue
    ).toEqual(payload.correctedValue);
    expect(() =>
      parseAppendCastingCorrectionRequest(
        request({
          ...payload,
          correctedValue: {
            ...payload.correctedValue,
            approvedRoleIds: [...approvedRoleIds, "role-301"]
          }
        })
      )
    ).toThrow("at most 300");
  });

  it("requires a bounded human rationale and exact review evidence", () => {
    const parsed = parseDecideCastingReviewRequest(
      request({
        projectId: "project-1",
        runId: "casting-run-1",
        gateId: "complete_cast_review",
        decision: "approve",
        expectedRevision: 3,
        expectedEvidenceFingerprint: digest,
        expectedRunFingerprint: digest,
        expectedApprovedCastSnapshotId: "cast-snapshot-1",
        expectedApprovedCastSnapshotRevision: 2,
        warningAcknowledgementIds: ["restricted-rights-1"],
        rationale: "Reviewed assignments, rights, and open conflicts.",
        supersedesDecisionId: null,
        idempotencyKey: "review-complete-1"
      })
    );
    expect(parsed.payload.gateId).toBe("complete_cast_review");
    expect(parsed.payload.warningAcknowledgementIds).toEqual([
      "restricted-rights-1"
    ]);
    expect(() =>
      parseDecideCastingReviewRequest(
        request({
          ...parsed.payload,
          rationale: " "
        })
      )
    ).toThrow("outside the allowed bounds");
  });

  it("accepts only system-authored invalidation decisions", () => {
    expect(() =>
      validateCastingReviewsResponse(
        castingReviewsResponse("invalidated", "system"),
        castingReviewsInput()
      )
    ).not.toThrow();
    expect(() =>
      validateCastingReviewsResponse(
        castingReviewsResponse("invalidated", "human"),
        castingReviewsInput()
      )
    ).toThrow("Only the system may invalidate");
  });

  it.each([
    "approved",
    "changes_requested",
    "rejected"
  ] as const)(
    "accepts human-authored %s decisions and rejects system authority",
    (decision) => {
      expect(() =>
        validateCastingReviewsResponse(
          castingReviewsResponse(decision, "human"),
          castingReviewsInput()
        )
      ).not.toThrow();
      expect(() =>
        validateCastingReviewsResponse(
          castingReviewsResponse(decision, "system"),
          castingReviewsInput()
        )
      ).toThrow("all other decisions require human authority");
    }
  );
});

function castingReviewsInput() {
  return {
    ...runEvidence(),
    expectedApprovedCastSnapshotId: "cast-snapshot-2",
    expectedApprovedCastSnapshotRevision: 2
  };
}

function castAssignment(castingProfileFingerprint: string) {
  return {
    contractVersion: VOICE_CASTING_CONTRACT_VERSION,
    assignmentId: "assignment-role-1",
    projectId: "project-1",
    roleId: "role-1",
    voiceProfileId: "voice-1",
    voiceProfileVersion: "1.0.0",
    voiceEvidenceFingerprint: digest,
    rightsRecordId: "rights-1",
    rightsRecordRevision: 1,
    rightsEvidenceFingerprint: digest,
    catalogRevisionId: "catalog-1",
    castingRunId: "casting-run-1",
    castingProfileFingerprint,
    phase2SnapshotFingerprint: digest,
    effectiveCorrectionSetFingerprint: digest,
    authority: "machine_proposal",
    rationale: "Deterministic governed casting proposal.",
    warnings: [],
    rightsState: "verified",
    revision: 1,
    provenance: {
      origin: "system",
      producerId: VOICE_CASTING_PRODUCER_ID,
      producerVersion: "1.0.0",
      recordedAt: "2026-07-31T12:00:00Z"
    },
    supersedesAssignmentId: null,
    effective: true
  };
}

function castingReviewsResponse(
  decision: CastingReviewDecision["decision"],
  classification: CastingReviewDecision["actor"]["classification"]
) {
  const input = customRoleInput();
  type ReviewFixture = Omit<
    ReturnType<typeof customRoleResponse>["reviews"][number],
    "gateId" | "latestDecision" | "state"
  > & {
    gateId: CastingReviewDecision["gateId"];
    latestDecision: CastingReviewDecision | null;
    state: CastingReviewDecision["decision"] | "pending";
  };
  const items = structuredClone(
    customRoleResponse(input).reviews
  ) as unknown as ReviewFixture[];
  const review = items[0];
  if (review === undefined) {
    throw new Error("The casting review fixture was incomplete.");
  }
  const decidedAt = "2026-07-31T12:05:00Z";
  review.state = decision;
  review.latestDecision = {
    contractVersion: VOICE_CASTING_CONTRACT_VERSION,
    decisionId: `decision-${decision}-${classification}`,
    reviewId: review.reviewId,
    gateId: review.gateId,
    projectId: input.projectId,
    castingRunId: input.runId,
    approvedCastSnapshotId: "cast-snapshot-2",
    approvedCastSnapshotRevision: 2,
    evidenceFingerprint: digest,
    decision,
    actor: {
      classification,
      actorId:
        classification === "system"
          ? "voice-casting-orchestrator"
          : "local-user"
    },
    acknowledgedWarningIds: [],
    rationale:
      decision === "invalidated"
        ? "Catalog or rights evidence changed."
        : "Human reviewed the governed casting evidence.",
    decidedAt,
    provenance: {
      origin: classification,
      producerId:
        classification === "system"
          ? "voice-casting-orchestrator"
          : "local-user",
      producerVersion: "1.0.0",
      recordedAt: decidedAt
    },
    immutable: true,
    supersedesDecisionId: null
  } satisfies CastingReviewDecision;

  return {
    correlationId: "correlation-casting-reviews",
    castingRunId: input.runId,
    items
  };
}

function customRoleInput(): CreateCustomProductionRoleInput {
  return {
    ...runEvidence(),
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
    reason: "A producer-defined role with no manuscript source.",
    expectedCorrectionSetFingerprint: digest,
    expectedCastingProfileFingerprint:
      GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
    idempotencyKey: "custom-role-create-1"
  };
}

function customRoleResponse(input: CreateCustomProductionRoleInput) {
  const recordedAt = "2026-07-31T12:00:00Z";
  const provenance = {
    origin: "system",
    producerId: "voice-casting-orchestrator",
    producerVersion: "1.0.0",
    recordedAt
  };
  const summary = {
    productionRoles: 1,
    narratorRoles: 0,
    characterRoles: 1,
    preReductionCandidates: 1,
    finalCandidates: 1,
    conflicts: 0,
    assignments: 1,
    corrections: 0
  };
  const snapshot = {
    contractVersion: VOICE_CASTING_CONTRACT_VERSION,
    snapshotId: "cast-snapshot-2",
    castingRunId: input.runId,
    projectId: input.projectId,
    revision: 2,
    phase2SnapshotFingerprint: input.expectedSnapshotFingerprint,
    catalogRevisionId: input.expectedCatalogRevisionId,
    catalogFingerprint: input.expectedCatalogFingerprint,
    castingProfileFingerprint:
      GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
    effectiveCorrectionSetFingerprint:
      input.expectedCorrectionSetFingerprint,
    assignmentIds: ["assignment-custom-a"],
    intentionallyUncastRoleIds: [],
    unresolvedConflictIds: [],
    counts: summary,
    snapshotFingerprint: digest,
    reviewEligible: false,
    createdAt: recordedAt,
    immutable: true
  };
  const run = {
    contractVersion: VOICE_CASTING_CONTRACT_VERSION,
    castingRunId: input.runId,
    projectId: input.projectId,
    prerequisites: {
      projectId: input.projectId,
      sourceDocumentId: "source-1",
      sourceRevision: 1,
      extractionId: "extraction-1",
      extractionRevision: 1,
      extractedTextSha256: digest,
      importReviewDecisionId: "import-decision-1",
      analysisRunId: "analysis-run-1",
      analysisSnapshotId: input.expectedSnapshotId,
      analysisSnapshotRevision: input.expectedSnapshotRevision,
      analysisSnapshotFingerprint: input.expectedSnapshotFingerprint,
      analysisCorrectionSetFingerprint: digest,
      characterRegistryFingerprint: digest,
      phase2GateDecisionIds: {
        storyStructureReview: "decision-structure",
        characterRegistryReview: "decision-characters",
        dialogueAttributionReview: "decision-dialogue",
        wholeBookAnalysisReview: "decision-whole-book"
      },
      evidenceFingerprint: digest
    },
    profile: {
      profileId: GOVERNED_VOICE_CASTING_PROFILE_ID,
      fingerprint: GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
    },
    producerId: VOICE_CASTING_PRODUCER_ID,
    catalogRevisionId: input.expectedCatalogRevisionId,
    catalogFingerprint: input.expectedCatalogFingerprint,
    effectiveCorrectionSetFingerprint:
      input.expectedCorrectionSetFingerprint,
    inputFingerprint: digest,
    outputFingerprint: digest,
    idempotencyFingerprint: digest,
    jobId: "job-casting-1",
    status: "succeeded",
    currentStage: "complete",
    progress: 1,
    checkpoint: null,
    attempt: 1,
    retryPolicy: {
      maxAttempts: 3,
      retryableFailureCodes: []
    },
    failurePolicy: "fail_closed_preserve_effective_cast_snapshot",
    resumeOfCastingRunId: null,
    retryOfCastingRunId: null,
    retryClassification: "retryable",
    cancellationRequested: false,
    warnings: [],
    summary,
    approvedCastSnapshot: snapshot,
    createdAt: recordedAt,
    updatedAt: recordedAt,
    completedAt: recordedAt,
    failure: null
  };
  const role = {
    contractVersion: VOICE_CASTING_CONTRACT_VERSION,
    roleId: "role-custom-a",
    projectId: input.projectId,
    roleType: "custom",
    phase2EntityId: null,
    effectiveDisplayLabel: input.label,
    effectiveFingerprint: digest,
    analysisRunId: run.prerequisites.analysisRunId,
    analysisSnapshotId: input.expectedSnapshotId,
    analysisSnapshotRevision: input.expectedSnapshotRevision,
    analysisSnapshotFingerprint: input.expectedSnapshotFingerprint,
    dialogueLineCount: 0,
    narrationSpanCount: 0,
    approximateWordCount: 0,
    range: {
      firstChapterOrdinal: null,
      lastChapterOrdinal: null,
      firstSceneOrdinal: null,
      lastSceneOrdinal: null
    },
    languageRequirements: [input.performanceRequirements.language],
    performanceRequirements: input.performanceRequirements,
    warnings: [
      {
        code: "CUSTOM_ROLE_CONTENT_FREE",
        severity: "warning",
        message: "This explicit custom role has no manuscript source.",
        requiresHumanReview: true,
        relatedEntityIds: [],
        evidence: []
      }
    ],
    provenance: {
      origin: "human",
      producerId: "local_user",
      producerVersion: "1.0.0",
      recordedAt,
      inputFingerprint: digest,
      sourceRevisionId: input.definitionId,
      reason: input.reason
    },
    status: "active",
    revision: 1,
    characterId: null,
    roleImportance: "supporting",
    unresolvedMaterialExplicitlyRepresented: false
  };
  const reviews = [
    "narrator_casting_review",
    "character_casting_review",
    "complete_cast_review"
  ].map((gateId) => ({
    contractVersion: VOICE_CASTING_CONTRACT_VERSION,
    reviewId: `review-${gateId}`,
    gateId,
    projectId: input.projectId,
    castingRunId: input.runId,
    state: "pending",
    revision: 2,
    prerequisiteGateIds:
      gateId === "complete_cast_review"
        ? ["narrator_casting_review", "character_casting_review"]
        : [],
    evidence: {
      projectId: input.projectId,
      castingRunId: input.runId,
      approvedCastSnapshotId: snapshot.snapshotId,
      approvedCastSnapshotRevision: snapshot.revision,
      approvedCastSnapshotFingerprint: snapshot.snapshotFingerprint,
      phase2SnapshotFingerprint: input.expectedSnapshotFingerprint,
      catalogRevisionId: input.expectedCatalogRevisionId,
      catalogFingerprint: input.expectedCatalogFingerprint,
      castingProfileFingerprint:
        GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
      effectiveCorrectionSetFingerprint:
        input.expectedCorrectionSetFingerprint,
      evidenceFingerprint: digest
    },
    openWarningIds: [],
    acknowledgedWarningIds: [],
    latestDecision: null,
    provenance,
    updatedAt: recordedAt
  }));
  return {
    correlationId: "correlation-custom-role",
    role,
    invalidatedGateIds: [
      "narrator_casting_review",
      "character_casting_review",
      "complete_cast_review"
    ],
    run,
    reviews
  };
}
