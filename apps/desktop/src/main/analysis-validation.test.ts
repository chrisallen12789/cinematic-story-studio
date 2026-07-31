// @vitest-environment node

import {
  ANALYSIS_GATE_IDS,
  PHASE_2_RUNTIME_AGENTS,
  WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
  WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
  type AnalysisAgentExecution,
  type AnalysisChapter,
  type AnalysisEntityCollection,
  type AnalysisGateReview,
  type AnalysisScene,
  type CharacterIdentity,
  type CharacterRelationship,
  type HumanEffectiveRegistry,
  type NarrationSpan,
  type StoryAnalysisRun
} from "@cinematic-story-studio/contracts";
import { describe, expect, it } from "vitest";

import {
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
  type AppendAnalysisCorrectionInput,
  type DecideAnalysisReviewInput,
  type ListAnalysisReviewsInput
} from "../shared/analysis-api";
import {
  parseAppendAnalysisCorrectionRequest,
  parseCreateAnalysisRunRequest,
  parseDecideAnalysisReviewRequest,
  parseListAnalysisEntitiesRequest,
  parseListAnalysisReviewsRequest,
  validateAnalysisReviewsResponse,
  validateAppendAnalysisCorrectionResponse,
  validateCreateAnalysisRunResponse,
  validateDecideAnalysisReviewResponse,
  validateAnalysisEntityPageResponse,
  validateAnalysisRunResponse,
  validateProjectAnalysisProjection
} from "./analysis-validation";
import { ValidationError } from "./validation";

const sha = (character: string) => character.repeat(64);

describe("Phase 2 desktop analysis boundary", () => {
  it("accepts only the exact approved input and canonical profile binding", () => {
    const parsed = parseCreateAnalysisRunRequest({
      contractVersion: "1.0.0",
      payload: createRunInput()
    });

    expect(parsed.payload.profile).toEqual({
      profileId: WHOLE_BOOK_ANALYSIS_PROFILE_ID,
      semanticVersion: WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
      fingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
    });
    expect(parsed.payload.expectedProfileFingerprint).toBe(
      WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
    );

    expect(() =>
      parseCreateAnalysisRunRequest({
        contractVersion: "1.0.0",
        payload: {
          ...createRunInput(),
          expectedProfileFingerprint: sha("f")
        }
      })
    ).toThrow("profile fingerprint");
    expect(() =>
      parseCreateAnalysisRunRequest({
        contractVersion: "1.0.0",
        payload: {
          ...createRunInput(),
          manuscriptPath: "C:\\private\\story.docx"
        }
      })
    ).toThrow("unknown field");
  });

  it("bounds cursor pages and restricts only speaker-state to dialogue", () => {
    expect(
      parseListAnalysisEntitiesRequest({
        contractVersion: "1.0.0",
        payload: {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "dialogue-lines",
          limit: 200,
          confidenceMax: 0.75,
          requiresReview: true,
          speakerState: "ambiguous"
        }
      }).payload
    ).toMatchObject({
      collection: "dialogue-lines",
      limit: 200,
      confidenceMax: 0.75,
      requiresReview: true,
      speakerState: "ambiguous"
    });

    expect(() =>
      parseListAnalysisEntitiesRequest({
        contractVersion: "1.0.0",
        payload: {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "narration-spans",
          speakerState: "proposed"
        }
      })
    ).toThrow("Speaker state");
    expect(() =>
      parseListAnalysisEntitiesRequest({
        contractVersion: "1.0.0",
        payload: {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "dialogue-lines",
          limit: 201
        }
      })
    ).toThrow(ValidationError);
  });

  it("passes effective service fingerprints verbatim for typed corrections", () => {
    const parsed = parseAppendAnalysisCorrectionRequest({
      contractVersion: "1.0.0",
      payload: {
        projectId: "project-1",
        runId: "run-1",
        category: "dialogue_speaker",
        targetCollection: "dialogue-lines",
        targetEntityId: "dialogue-1",
        expectedTargetRevision: 2,
        expectedRunFingerprint: sha("a"),
        previousValueFingerprint: sha("b"),
        patch: {
          speakerCharacterId: "character-mira",
          selectedCandidateId: null,
          requiresHumanReview: false
        },
        reason: "The adjacent attribution names Mira.",
        idempotencyKey: "correction-1"
      }
    });

    expect(parsed.payload.previousValueFingerprint).toBe(sha("b"));
    expect(parsed.payload.patch).toEqual({
      speakerCharacterId: "character-mira",
      selectedCandidateId: null,
      requiresHumanReview: false
    });
    expect(parsed.payload.reason).toBe(
      "The adjacent attribution names Mira."
    );
    for (const reason of [undefined, "", "   "]) {
      expect(() =>
        parseAppendAnalysisCorrectionRequest({
          contractVersion: "1.0.0",
          payload: {
            ...parsed.payload,
            reason
          }
        })
      ).toThrow(ValidationError);
    }
    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...parsed.payload,
          category: "dialogue_speaker",
          targetCollection: "characters"
        }
      })
    ).toThrow("cannot target");

    const splitPayload = {
      projectId: "project-1",
      runId: "run-1",
      category: "character_split",
      targetCollection: "characters",
      targetEntityId: "character-mira",
      expectedTargetRevision: 2,
      expectedRunFingerprint: sha("a"),
      previousValueFingerprint: sha("b"),
      patch: {
        newRegistryCharacterId: "character-mira-reviewed",
        canonicalName: "Mira Reviewed",
        normalizedCanonicalName: "mira reviewed",
        mentionIds: ["mention-1"]
      },
      reason: "The evidence separates this mention into another identity.",
      idempotencyKey: "correction-split-1"
    } as const;
    expect(
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: splitPayload
      }).payload.patch
    ).toEqual(splitPayload.patch);
    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...splitPayload,
          patch: {
            ...splitPayload.patch,
            mentionIds: []
          }
        }
      })
    ).toThrow("at least 1 identifier");
  });

  it("accepts bounded changed structure spans and rejects invalid ranges", () => {
    const payload = {
      projectId: "project-1",
      runId: "run-1",
      category: "structure_boundary",
      targetCollection: "scenes",
      targetEntityId: "scene-1",
      expectedTargetRevision: 2,
      expectedRunFingerprint: sha("a"),
      previousValueFingerprint: sha("b"),
      patch: {
        operation: "move",
        parentEntityId: "chapter-2",
        ordinal: 3,
        sourceSpan: {
          sourceDocumentId: "document-1",
          extractionId: "extraction-1",
          extractionRevision: 2,
          offsetUnit: "unicode-code-point",
          startOffset: 48,
          endOffset: 96
        },
        boundaryKind: "heading"
      },
      reason: "The reviewed heading moves the scene boundary.",
      idempotencyKey: "structure-span-change"
    } as const;
    expect(
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload
      }).payload.patch
    ).toMatchObject({
      operation: "move",
      sourceSpan: {
        startOffset: 48,
        endOffset: 96
      }
    });
    expect(
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload
      }).payload.patch
    ).not.toHaveProperty("sourceSpan.textSha256");
    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...payload,
          patch: {
            ...payload.patch,
            sourceSpan: {
              ...payload.patch.sourceSpan,
              textSha256: sha("c")
            }
          }
        }
      })
    ).toThrow("unknown field");
    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...payload,
          patch: {
            ...payload.patch,
            sourceSpan: {
              ...payload.patch.sourceSpan,
              endOffset: 48
            }
          }
        }
      })
    ).toThrow("endOffset");
  });

  it("rejects malicious unknown, cross-category, oversized, and stale correction IPC", () => {
    const base = {
      projectId: "project-1",
      runId: "run-1",
      category: "character_identity",
      targetCollection: "characters",
      targetEntityId: "character-mira",
      expectedTargetRevision: 2,
      expectedRunFingerprint: sha("a"),
      previousValueFingerprint: sha("b"),
      patch: {
        canonicalName: "Mira Vale",
        normalizedCanonicalName: "mira vale",
        identityStatus: "resolved"
      },
      reason: "The approved source uses the full name.",
      idempotencyKey: "correction-security-1"
    } as const;

    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...base,
          patch: {
            ...base.patch,
            overwriteMachineEvidence: true
          }
        }
      })
    ).toThrow("unknown field");

    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...base,
          patch: {
            speakerCharacterId: "character-mira",
            selectedCandidateId: null,
            requiresHumanReview: false
          }
        }
      })
    ).toThrow("unknown field");

    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...base,
          patch: {
            ...base.patch,
            canonicalName: "M".repeat(513)
          }
        }
      })
    ).toThrow("text limit");

    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...base,
          expectedTargetRevision: 0
        }
      })
    ).toThrow("integer range");
    expect(() =>
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          ...base,
          previousValueFingerprint: sha("B")
        }
      })
    ).toThrow("SHA-256");
  });

  it("requires explicit warning acknowledgements and rationale for every decision", () => {
    const approval = parseDecideAnalysisReviewRequest({
      contractVersion: "1.0.0",
      payload: {
        projectId: "project-1",
        runId: "run-1",
        gateId: "dialogue_attribution_review",
        decision: "approve",
        expectedRevision: 3,
        expectedArtifactFingerprint: sha("c"),
        expectedEvidenceFingerprint: sha("d"),
        acknowledgedWarningIds: ["warning-1"],
        rationale: "The exact evidence supports approval.",
        idempotencyKey: "gate-decision-1"
      }
    });
    expect(approval.payload.acknowledgedWarningIds).toEqual(["warning-1"]);

    expect(() =>
      parseDecideAnalysisReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          ...approval.payload,
          rationale: undefined
        }
      })
    ).toThrow("rationale");
    expect(() =>
      parseDecideAnalysisReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          ...approval.payload,
          decision: "request_changes",
          rationale: undefined
        }
      })
    ).toThrow("rationale");
    expect(() =>
      parseDecideAnalysisReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          ...approval.payload,
          acknowledgedWarningIds: ["warning-1", "warning-1"]
        }
      })
    ).toThrow("duplicate");
  });

  it("validates complete run identity, producer, stage, and immutable snapshot", () => {
    const run = validRun();
    expect(
      validateAnalysisRunResponse(
        { correlationId: "correlation-1", run },
        { projectId: "project-1", runId: "run-1" }
      ).run.currentStage
    ).toBe("complete");

    expect(() =>
      validateAnalysisRunResponse(
        {
          correlationId: "correlation-1",
          run: {
            ...run,
            producer: {
              ...run.producer,
              producerId: "untrusted-producer"
            }
          }
        },
        { projectId: "project-1", runId: "run-1" }
      )
    ).toThrow("producer");
  });

  it("validates narration as its own bounded pageable collection", () => {
    const narration = validNarration();
    const response = validateAnalysisEntityPageResponse(
      {
        correlationId: "correlation-1",
        runId: "run-1",
        snapshotId: "snapshot-1",
        collection: "narration-spans",
        pageSize: 1,
        total: 1,
        items: [narration]
      },
      {
        projectId: "project-1",
        runId: "run-1",
        expectedSnapshotId: "snapshot-1",
        collection: "narration-spans",
        limit: 50
      }
    );
    expect(
      (response.items[0] as NarrationSpan | undefined)?.classification
    ).toBe("internal_thought");

    expect(() =>
      validateAnalysisEntityPageResponse(
        {
          correlationId: "correlation-1",
          runId: "run-1",
          snapshotId: "snapshot-1",
          collection: "narration-spans",
          pageSize: 1,
          total: 1,
          items: [
            {
              ...narration,
              effectiveValueFingerprint: "not-a-fingerprint"
            }
          ]
        },
        {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "narration-spans"
        }
      )
    ).toThrow("SHA-256");
  });

  it("rejects a stale snapshot page even when the run identity is unchanged", () => {
    const narration = {
      ...validNarration(),
      snapshotId: "snapshot-stale"
    };
    expect(() =>
      validateAnalysisEntityPageResponse(
        {
          correlationId: "correlation-stale-snapshot",
          runId: "run-1",
          snapshotId: "snapshot-stale",
          collection: "narration-spans",
          pageSize: 1,
          total: 1,
          items: [narration]
        },
        {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "narration-spans"
        }
      )
    ).toThrow("page identity");
  });

  it("accepts canonical execution governance and unresolved relationships", () => {
    const execution = validAgentExecution();
    expect(
      validateAnalysisEntityPageResponse(
        {
          correlationId: "correlation-execution",
          runId: "run-1",
          snapshotId: "snapshot-1",
          collection: "agent-executions",
          pageSize: 1,
          total: 1,
          items: [execution]
        },
        {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "agent-executions"
        }
      ).items
    ).toHaveLength(1);

    const relationship = validRelationship();
    expect(
      validateAnalysisEntityPageResponse(
        {
          correlationId: "correlation-relationship",
          runId: "run-1",
          snapshotId: "snapshot-1",
          collection: "relationships",
          pageSize: 1,
          total: 1,
          items: [relationship]
        },
        {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "relationships"
        }
      ).items
    ).toHaveLength(1);

    expect(() =>
      validateAnalysisEntityPageResponse(
        {
          correlationId: "correlation-relationship",
          runId: "run-1",
          snapshotId: "snapshot-1",
          collection: "relationships",
          pageSize: 1,
          total: 1,
          items: [
            {
              ...relationship,
              resolution: "resolved"
            }
          ]
        },
        {
          projectId: "project-1",
          runId: "run-1",
          expectedSnapshotId: "snapshot-1",
          collection: "relationships"
        }
      )
    ).toThrow("requires both character identities");
  });

  it("accepts strict human-controlled boundary and registry projections", () => {
    const chapter = validEffectiveChapter();
    const chapterResponse = validateEntityPage("chapters", chapter);
    expect(
      (chapterResponse.items[0] as AnalysisChapter | undefined)
        ?.effectiveBoundary
    ).toMatchObject({
      authority: "human",
      correctionId: "correction-boundary-1",
      operation: "move",
      included: true
    });

    const scene = validEffectiveScene();
    expect(
      (validateEntityPage("scenes", scene).items[0] as
        | AnalysisScene
        | undefined)?.effectiveBoundary
    ).toMatchObject({
      authority: "human",
      correctionId: "correction-boundary-remove-1",
      operation: "remove",
      included: false
    });

    const mergeCharacter = validEffectiveCharacter({
      authority: "human",
      correctionId: "correction-merge-1",
      operation: "merge",
      mergeIntoCharacterId: "character-rowan"
    });
    expect(
      (validateEntityPage("characters", mergeCharacter).items[0] as
        | CharacterIdentity
        | undefined)?.effectiveRegistry
    ).toEqual(mergeCharacter.effectiveRegistry);
    const inactiveMergedCharacter: CharacterIdentity = {
      ...mergeCharacter,
      firstMentionId: null,
      lastMentionId: null,
      namedMentionIds: [],
      ambiguousMentionIds: [],
      firstEvidence: [],
      lastEvidence: [],
      mentionCount: 0
    };
    expect(
      (validateEntityPage("characters", inactiveMergedCharacter).items[0] as
        | CharacterIdentity
        | undefined)?.mentionCount
    ).toBe(0);
    const scaleMentionIds = Array.from(
      { length: 6_000 },
      (_, index) => `mention-scale-${index}`
    );
    const scaleCharacter: CharacterIdentity = {
      ...mergeCharacter,
      firstMentionId: scaleMentionIds[0] ?? null,
      lastMentionId: scaleMentionIds.at(-1) ?? null,
      namedMentionIds: scaleMentionIds,
      mentionCount: scaleMentionIds.length
    };
    expect(
      (validateEntityPage("characters", scaleCharacter).items[0] as
        | CharacterIdentity
        | undefined)?.namedMentionIds
    ).toHaveLength(6_000);

    const splitCharacter = validEffectiveCharacter({
      authority: "human",
      correctionId: "correction-split-1",
      operation: "split",
      splitIdentity: {
        registryCharacterId: "registry-mira-reviewed",
        canonicalName: "Mira Reviewed",
        normalizedCanonicalName: "mira reviewed",
        mentionIds: ["mention-2"]
      }
    });
    expect(
      (validateEntityPage("characters", splitCharacter).items[0] as
        | CharacterIdentity
        | undefined)?.effectiveRegistry
    ).toEqual(splitCharacter.effectiveRegistry);
  });

  it("rejects contradictory or malicious effective-view projections", () => {
    const chapter = validEffectiveChapter();
    expect(() =>
      validateEntityPage("chapters", {
        ...chapter,
        effectiveBoundary: {
          ...chapter.effectiveBoundary,
          included: false
        }
      })
    ).toThrow("invalid inclusion state");
    expect(() =>
      validateEntityPage("chapters", {
        ...chapter,
        effectiveAuthority: "runtime_agent"
      })
    ).toThrow("requires human effective authority");
    expect(() =>
      validateEntityPage("chapters", {
        ...chapter,
        effectiveBoundary: {
          ...chapter.effectiveBoundary,
          overwriteMachineBoundary: true
        }
      })
    ).toThrow("unknown field");

    const mergeCharacter = validEffectiveCharacter({
      authority: "human",
      correctionId: "correction-merge-1",
      operation: "merge",
      mergeIntoCharacterId: "character-rowan"
    });
    expect(() =>
      validateEntityPage("characters", {
        ...mergeCharacter,
        effectiveRegistry: {
          ...mergeCharacter.effectiveRegistry,
          splitIdentity: {
            registryCharacterId: "registry-forged",
            canonicalName: "Forged",
            normalizedCanonicalName: "forged",
            mentionIds: []
          }
        }
      })
    ).toThrow("unknown field");
    expect(() =>
      validateEntityPage("characters", {
        ...mergeCharacter,
        firstMentionId: null,
        lastMentionId: null,
        namedMentionIds: [],
        ambiguousMentionIds: [],
        firstEvidence: [],
        lastEvidence: [],
        mentionCount: 0
      })
    ).not.toThrow();
    expect(() =>
      validateEntityPage("characters", {
        ...mergeCharacter,
        lastMentionId: null,
        firstEvidence: [],
        lastEvidence: []
      })
    ).toThrow("must have mention bounds and boundary evidence");
    expect(() =>
      validateEntityPage("characters", {
        ...mergeCharacter,
        firstMentionId: "mention-1",
        lastMentionId: null,
        namedMentionIds: [],
        ambiguousMentionIds: [],
        firstEvidence: [],
        lastEvidence: [],
        mentionCount: 0
      })
    ).toThrow("must have null mention bounds");

    const splitCharacter = validEffectiveCharacter({
      authority: "human",
      correctionId: "correction-split-1",
      operation: "split",
      splitIdentity: {
        registryCharacterId: "registry-mira-reviewed",
        canonicalName: "Mira Reviewed",
        normalizedCanonicalName: "mira reviewed",
        mentionIds: ["mention-2"]
      }
    });
    expect(() =>
      validateEntityPage("characters", {
        ...splitCharacter,
        effectiveRegistry: {
          ...splitCharacter.effectiveRegistry,
          splitIdentity: {
            registryCharacterId: "registry-mira-reviewed",
            canonicalName: "Mira Reviewed",
            normalizedCanonicalName: "mira reviewed",
            mentionIds: ["mention-2", "mention-2"]
          }
        }
      })
    ).toThrow("duplicate identities");
  });

  it("binds create, correction, and gate mutation responses to exact requests", () => {
    const createInput = createRunInput();
    const createdRun = {
      ...validRun(),
      approvedEvidenceFingerprint:
        createInput.expectedEvidenceFingerprint
    };
    const created = {
      correlationId: "correlation-create",
      run: createdRun,
      job: validAnalysisJob(createdRun)
    };
    expect(
      validateCreateAnalysisRunResponse(created, createInput).run.runId
    ).toBe("run-1");
    expect(() =>
      validateCreateAnalysisRunResponse(
        {
          ...created,
          run: {
            ...createdRun,
            extractionRevision:
              createInput.expectedExtractionRevision + 1
          }
        },
        createInput
      )
    ).toThrow("exact approved input");
    expect(() =>
      validateCreateAnalysisRunResponse(
        {
          ...created,
          job: { ...created.job, type: "analyze_story" }
        },
        createInput
      )
    ).toThrow("job type");

    const correctionInput =
      parseAppendAnalysisCorrectionRequest({
        contractVersion: "1.0.0",
        payload: {
          projectId: "project-1",
          runId: "run-1",
          category: "dialogue_speaker",
          targetCollection: "dialogue-lines",
          targetEntityId: "entity-dialogue-1",
          expectedTargetRevision: 2,
          expectedRunFingerprint: sha("5"),
          previousValueFingerprint: sha("8"),
          patch: {
            speakerCharacterId: "character-mira",
            selectedCandidateId: null,
            requiresHumanReview: false
          },
          reason: "The source attribution explicitly identifies Mira.",
          supersedesCorrectionId: "correction-prior",
          idempotencyKey: "correction-exact-response"
        }
      }).payload;
    const correction = validCorrection(correctionInput);
    const correctedRun = validRun();
    const correctionReviews = validGateReviews(correctedRun, [
      "dialogue_attribution_review"
    ]);
    const correctionResponse = {
      correlationId: "correlation-correction",
      correction,
      invalidatedGateIds: ["dialogue_attribution_review"],
      run: correctedRun,
      reviews: correctionReviews
    };
    expect(
      validateAppendAnalysisCorrectionResponse(
        correctionResponse,
        correctionInput
      ).correction.reason
    ).toBe(correctionInput.reason);
    expect(() =>
      validateAppendAnalysisCorrectionResponse(
        {
          ...correctionResponse,
          correction: {
            ...correction,
            patch: {
              ...correction.patch,
              requiresHumanReview: true
            }
          }
        },
        correctionInput
      )
    ).toThrow("exact request preconditions");
    expect(() =>
      validateAppendAnalysisCorrectionResponse(
        {
          ...correctionResponse,
          correction: {
            ...correction,
            snapshotId: "snapshot-stale"
          }
        },
        correctionInput
      )
    ).toThrow("run and snapshot identity");
    expect(() =>
      validateAppendAnalysisCorrectionResponse(
        {
          ...correctionResponse,
          reviews: correctionReviews.slice(0, 3)
        },
        correctionInput
      )
    ).toThrow("omitted an analysis gate");
    expect(() =>
      validateAppendAnalysisCorrectionResponse(
        {
          ...correctionResponse,
          reviews: [
            ...correctionReviews.slice(0, 3),
            correctionReviews[0]
          ]
        },
        correctionInput
      )
    ).toThrow("repeated an analysis gate");
    expect(() =>
      validateAppendAnalysisCorrectionResponse(
        {
          ...correctionResponse,
          reviews: correctionReviews.map((review) =>
            review.gateId === "dialogue_attribution_review"
              ? { ...review, state: "pending" }
              : review
          )
        },
        correctionInput
      )
    ).toThrow("invalidated gate evidence");

    const gateInput =
      parseDecideAnalysisReviewRequest({
        contractVersion: "1.0.0",
        payload: {
          projectId: "project-1",
          runId: "run-1",
          gateId: "story_structure_review",
          decision: "approve",
          expectedRevision: 3,
          expectedArtifactFingerprint: sha("c"),
          expectedEvidenceFingerprint: sha("d"),
          acknowledgedWarningIds: ["warning-1"],
          rationale: "The exact evidence supports approval.",
          idempotencyKey: "gate-exact-response"
        }
      }).payload;
    const gateResponse = validGateDecisionResponse(gateInput);
    expect(
      validateDecideAnalysisReviewResponse(gateResponse, gateInput)
        .review.revision
    ).toBe(4);
    for (const rationale of [
      undefined,
      "   ",
      42,
      "R".repeat(4_001)
    ]) {
      const invalidDecision = {
        ...gateResponse.decision,
        rationale
      };
      expect(() =>
        validateDecideAnalysisReviewResponse(
          {
            ...gateResponse,
            decision: invalidDecision,
            review: {
              ...gateResponse.review,
              latestDecision: invalidDecision
            }
          },
          gateInput
        )
      ).toThrow("decision rationale");
    }
    expect(() =>
      validateDecideAnalysisReviewResponse(
        {
          ...gateResponse,
          decision: {
            ...gateResponse.decision,
            manuscriptExcerpt: "must never cross the desktop boundary"
          }
        },
        gateInput
      )
    ).toThrow("unknown field");
    expect(() =>
      validateDecideAnalysisReviewResponse(
        {
          ...gateResponse,
          decision: {
            ...gateResponse.decision,
            evidence: {
              ...gateResponse.decision.evidence,
              snapshotFingerprint: sha("f")
            }
          }
        },
        gateInput
      )
    ).toThrow("cross-link");
  });

  it("binds review lists and project projections to the exact selected run evidence", () => {
    const run = validRun();
    const input = reviewListInput(run);
    expect(
      parseListAnalysisReviewsRequest({
        contractVersion: "1.0.0",
        payload: input
      }).payload
    ).toEqual(input);

    const decisionInput: DecideAnalysisReviewInput = {
      projectId: run.projectId,
      runId: run.runId,
      gateId: "story_structure_review",
      decision: "approve",
      expectedRevision: 3,
      expectedArtifactFingerprint: sha("c"),
      expectedEvidenceFingerprint: sha("d"),
      acknowledgedWarningIds: ["warning-1"],
      rationale: "The exact evidence supports approval.",
      idempotencyKey: "gate-review-list-response"
    };
    const approved = validGateDecisionResponse(decisionInput).review;
    const pending = {
      ...approved,
      state: "pending" as const,
      revision: 1,
      latestDecisionId: undefined,
      latestDecision: null
    };
    const pendingReviews = completeGateReviews(pending);
    const list = {
      correlationId: "correlation-review-list",
      runId: run.runId,
      items: pendingReviews
    };
    expect(
      validateAnalysisReviewsResponse(list, input).items[0]
        ?.latestDecision
    ).toBeNull();
    expect(() =>
      validateAnalysisReviewsResponse(
        {
          ...list,
          items: pendingReviews.map((review, index) =>
            index === 0
              ? {
                  ...review,
                  evidence: {
                    ...review.evidence,
                    sourceDocumentId: "document-foreign"
                  }
                }
              : review
          )
        },
        input
      )
    ).toThrow("returned run and snapshot");

    const approvedReviews = completeGateReviews(approved);
    expect(() =>
      validateAnalysisReviewsResponse(
        {
          ...list,
          items: approvedReviews.map((review, index) =>
            index === 0
              ? {
                  ...review,
                  latestDecision: {
                    ...review.latestDecision,
                    decisionId: "decision-foreign"
                  }
                }
              : review
          )
        },
        input
      )
    ).toThrow("latest review decision");
    expect(() =>
      validateAnalysisReviewsResponse(
        {
          ...list,
          items: pendingReviews.slice(0, 3)
        },
        input
      )
    ).toThrow("omitted an analysis gate");
    expect(() =>
      validateAnalysisReviewsResponse(
        {
          ...list,
          items: [
            ...pendingReviews.slice(0, 3),
            pendingReviews[0]
          ]
        },
        input
      )
    ).toThrow("repeated a gate");

    expect(() =>
      validateProjectAnalysisProjection(
        {
          currentAnalysisRun: run,
          analysisGateReviews: [
            {
              ...pending,
              evidence: {
                ...pending.evidence,
                storyId: "story-foreign"
              }
            }
          ]
        },
        run.projectId
      )
    ).toThrow("returned run and snapshot");
  });

  it("accepts an invalidated review with a prior immutable latest decision", () => {
    const run = validRun();
    const input = reviewListInput(run);
    const decisionInput: DecideAnalysisReviewInput = {
      projectId: run.projectId,
      runId: run.runId,
      gateId: "story_structure_review",
      decision: "approve",
      expectedRevision: 3,
      expectedArtifactFingerprint: sha("c"),
      expectedEvidenceFingerprint: sha("d"),
      acknowledgedWarningIds: [],
      rationale: "The prior snapshot evidence supported approval.",
      idempotencyKey: "historical-gate-decision"
    };
    const prior = validGateDecisionResponse(decisionInput).review;
    const invalidated = {
      ...prior,
      state: "invalidated" as const,
      revision: prior.revision + 1,
      artifactFingerprint: sha("a"),
      evidenceFingerprint: sha("b"),
      evidence: {
        ...prior.evidence,
        artifactFingerprint: sha("a"),
        evidenceFingerprint: sha("b")
      },
      provenance: {
        origin: "human_correction" as const,
        recordedAt: "2026-07-30T12:03:00Z",
        inputFingerprint: sha("b"),
        correctionId: "correction-1",
        deterministic: false
      },
      updatedAt: "2026-07-30T12:03:00Z"
    };
    expect(
      validateAnalysisReviewsResponse(
        {
          correlationId: "correlation-invalidated-review",
          runId: run.runId,
          items: completeGateReviews(invalidated)
        },
        input
      ).items[0]?.latestDecision?.decision
    ).toBe("approved");
  });

  it.each(strictEntityCases())(
    "strictly validates $collection fields and rejects $invalidField",
    ({ collection, entity, invalidField, invalidValue }) => {
      expect(validateEntityPage(collection, entity).items).toHaveLength(1);
      expect(() =>
        validateEntityPage(collection, {
          ...entity,
          [invalidField]: invalidValue
        })
      ).toThrow(ValidationError);
    }
  );
});

function createRunInput() {
  return {
    projectId: "project-1",
    expectedExtractionId: "extraction-1",
    expectedExtractionRevision: 2,
    expectedReviewId: "review-1",
    expectedReviewRevision: 3,
    expectedEvidenceFingerprint: sha("e"),
    expectedProfileFingerprint:
      WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
    profile: {
      profileId: WHOLE_BOOK_ANALYSIS_PROFILE_ID,
      semanticVersion: WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
      fingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
    },
    idempotencyKey: "analysis-run-request-1"
  };
}

function reviewListInput(
  run: StoryAnalysisRun
): ListAnalysisReviewsInput {
  const snapshot = run.currentSnapshot;
  if (snapshot === null) {
    throw new Error("Test run requires a snapshot.");
  }
  return {
    projectId: run.projectId,
    runId: run.runId,
    expectedSourceDocumentId: run.sourceDocumentId,
    expectedExtractionId: run.extractionId,
    expectedExtractionRevision: run.extractionRevision,
    expectedStoryId: run.storyId,
    expectedProfileId: run.profile.profileId,
    expectedProfileFingerprint: run.profile.fingerprint,
    expectedRunFingerprint: run.runFingerprint,
    expectedSnapshotId: snapshot.snapshotId,
    expectedSnapshotRevision: snapshot.revision,
    expectedSnapshotFingerprint: snapshot.snapshotFingerprint
  };
}

function validRun(): StoryAnalysisRun {
  return {
    contractVersion: "2.0.0",
    runId: "run-1",
    projectId: "project-1",
    storyId: "story-1",
    storyRevision: 4,
    storyFingerprint: sha("1"),
    sourceDocumentId: "document-1",
    sourceRevision: 2,
    sourceSha256: sha("0"),
    extractionId: "extraction-1",
    extractionRevision: 2,
    extractedTextSha256: sha("2"),
    importReviewId: "review-1",
    importReviewRevision: 3,
    importReviewDecisionId: "import-decision-1",
    approvedEvidenceFingerprint: sha("3"),
    inputFingerprint: sha("4"),
    runFingerprint: sha("5"),
    profile: {
      profileId: WHOLE_BOOK_ANALYSIS_PROFILE_ID,
      semanticVersion: WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
      fingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
    },
    producer: {
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION
    },
    agentVersions: PHASE_2_RUNTIME_AGENTS,
    jobId: "job-1",
    status: "succeeded",
    currentStage: "complete",
    progress: 1,
    warnings: [],
    snapshotCount: 1,
    currentSnapshot: {
      contractVersion: "2.0.0",
      snapshotId: "snapshot-1",
      runId: "run-1",
      revision: 1,
      inputFingerprint: sha("4"),
      snapshotFingerprint: sha("6"),
      correctionSetFingerprint: sha("7"),
      counts: analysisCounts(),
      collections: [],
      createdAt: "2026-07-30T12:00:00Z",
      immutable: true
    },
    latestExecution: validAgentExecution(),
    summary: analysisCounts(),
    reviewEligibility: "ready",
    createdAt: "2026-07-30T11:59:00Z",
    updatedAt: "2026-07-30T12:00:00Z",
    completedAt: "2026-07-30T12:00:00Z"
  };
}

function validNarration(): NarrationSpan {
  return {
    contractVersion: "2.0.0",
    entityId: "entity-narration-1",
    stableSemanticId: "semantic-narration-1",
    narrationSpanId: "narration-1",
    runId: "run-1",
    snapshotId: "snapshot-1",
    revision: 1,
    effectiveRevision: 1,
    machineEntityFingerprint: sha("8"),
    effectiveValueFingerprint: sha("8"),
    effectiveAuthority: "runtime_agent",
    ordinal: 0,
    chapterId: "chapter-1",
    sceneId: "scene-1",
    exactText: {
      sourceDocumentId: "document-1",
      extractionId: "extraction-1",
      extractionRevision: 2,
      offsetUnit: "unicode-code-point",
      startOffset: 10,
      endOffset: 31,
      textSha256: sha("9"),
      exactText: "I should never say it.",
      exactTextSha256: sha("a"),
      originalCodePointCount: 21,
      exactTextTruncated: false,
      originalTextPreserved: true
    },
    classification: "internal_thought",
    narratorCharacterId: "character-mira",
    confidence: {
      score: 0.9,
      classification: "high",
      basis: "Narrative syntax and surrounding attribution.",
      calibrationId: "governed-local-rules-v1"
    },
    warnings: [],
    provenance: {
      origin: "runtime_agent",
      recordedAt: "2026-07-30T12:00:00Z",
      inputFingerprint: sha("4"),
      agentExecutionId: "execution-1",
      agentId: "story-beats",
      agentVersion: "1.0.0",
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
      deterministic: true
    },
    evidence: []
  };
}

function validEffectiveChapter(): AnalysisChapter {
  return {
    ...validHumanEntityHeader("entity-chapter-1", sha("8")),
    chapterId: "chapter-1",
    title: "Chapter One",
    sourceSpan: validSourceSpan(0, 160),
    firstSceneId: "scene-1",
    lastSceneId: "scene-1",
    sceneCount: 1,
    effectiveBoundary: {
      parentEntityId: "story-1",
      ordinal: 1,
      sourceSpan: validSourceSpan(12, 160),
      authority: "human",
      correctionId: "correction-boundary-1",
      operation: "move",
      included: true
    }
  };
}

function validEffectiveScene(): AnalysisScene {
  return {
    ...validHumanEntityHeader("entity-scene-1", sha("a")),
    sceneId: "scene-1",
    chapterId: "chapter-1",
    heading: "Removed machine boundary",
    sourceSpan: validSourceSpan(12, 160),
    boundaryKind: "inferred",
    firstBeatId: "beat-1",
    lastBeatId: "beat-1",
    beatCount: 1,
    effectiveBoundary: {
      parentEntityId: "chapter-1",
      ordinal: 0,
      sourceSpan: validSourceSpan(12, 160),
      authority: "human",
      correctionId: "correction-boundary-remove-1",
      operation: "remove",
      included: false
    }
  };
}

function validEffectiveCharacter(
  effectiveRegistry: HumanEffectiveRegistry
): CharacterIdentity {
  return {
    ...validHumanEntityHeader("entity-character-mira", sha("b")),
    characterId: "character-mira",
    registryCharacterId: "registry-mira",
    projectId: "project-1",
    storyId: "story-1",
    registryScope: "project_story",
    stableAcrossCompatibleRuns: true,
    canonicalName: "Mira Vale",
    normalizedCanonicalName: "mira vale",
    kind: "person",
    identityStatus: "resolved",
    aliases: [],
    honorifics: [],
    pronounEvidence: [],
    firstMentionId: "mention-1",
    lastMentionId: "mention-2",
    namedMentionIds: ["mention-1", "mention-2"],
    ambiguousMentionIds: [],
    firstEvidence: [validEvidence(10, 14, "Mira")],
    lastEvidence: [validEvidence(20, 24, "Vale")],
    mentionCount: 2,
    effectiveRegistry
  };
}

function validHumanEntityHeader(entityId: string, machineFingerprint: string) {
  return {
    contractVersion: "2.0.0" as const,
    entityId,
    stableSemanticId: `semantic-${entityId}`,
    runId: "run-1",
    snapshotId: "snapshot-1",
    revision: 1,
    effectiveRevision: 2,
    machineEntityFingerprint: machineFingerprint,
    effectiveValueFingerprint: sha("c"),
    effectiveAuthority: "human" as const,
    ordinal: 0,
    confidence: {
      score: 0.9,
      classification: "high" as const,
      basis: "Deterministic source evidence."
    },
    warnings: [],
    provenance: {
      origin: "runtime_agent" as const,
      recordedAt: "2026-07-30T12:00:00Z",
      inputFingerprint: sha("4"),
      agentExecutionId: "execution-1",
      agentId: "story-structure" as const,
      agentVersion: "1.0.0" as const,
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
      deterministic: true
    },
    evidence: []
  };
}

function validSourceSpan(startOffset: number, endOffset: number) {
  return {
    sourceDocumentId: "document-1",
    extractionId: "extraction-1",
    extractionRevision: 2,
    offsetUnit: "unicode-code-point" as const,
    startOffset,
    endOffset,
    textSha256: sha("9")
  };
}

function validEvidence(
  startOffset: number,
  endOffset: number,
  excerptText: string
) {
  return {
    ...validSourceSpan(startOffset, endOffset),
    excerptStartOffset: startOffset,
    excerptEndOffset: endOffset,
    excerptText,
    excerptSha256: sha("e"),
    excerptTruncated: false
  };
}

function validateEntityPage(
  collection: AnalysisEntityCollection,
  item: unknown
) {
  return validateAnalysisEntityPageResponse(
    {
      correlationId: `correlation-${collection}`,
      runId: "run-1",
      snapshotId: "snapshot-1",
      collection,
      pageSize: 1,
      total: 1,
      items: [item]
    },
    {
      projectId: "project-1",
      runId: "run-1",
      expectedSnapshotId: "snapshot-1",
      collection
    }
  );
}

function validAgentExecution(): AnalysisAgentExecution {
  return {
    contractVersion: "2.0.0",
    executionId: "execution-story-structure",
    runId: "run-1",
    snapshotId: "snapshot-1",
    ordinal: 0,
    agentId: "story-structure",
    agentVersion: "1.0.0",
    status: "succeeded",
    attempt: 1,
    progress: 1,
    currentStage: "analyze_structure",
    checkpoint: {
      checkpointId: "checkpoint-story-structure",
      checkpointFingerprint: sha("b"),
      stage: "analyze_structure",
      schemaVersion: "2.0.0",
      recordedAt: "2026-07-30T12:00:00Z"
    },
    retryClassification: "not_retryable",
    retryPolicy: {
      maxAttempts: 3,
      retryableFailureCodes: ["provider_unavailable"]
    },
    failurePolicy: "fail_closed_without_partial_publication",
    inputFingerprint: sha("4"),
    outputFingerprint: sha("c"),
    outputCollections: ["chapters", "scenes"],
    outputArtifactId: "artifact-story-structure",
    confidence: {
      score: 0.9,
      classification: "high",
      basis: "Deterministic source structure evidence."
    },
    warnings: [],
    provenance: {
      origin: "runtime_agent",
      recordedAt: "2026-07-30T12:00:00Z",
      inputFingerprint: sha("4"),
      agentExecutionId: "execution-story-structure",
      agentId: "story-structure",
      agentVersion: "1.0.0",
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
      deterministic: true
    },
    startedAt: "2026-07-30T11:59:59Z",
    finishedAt: "2026-07-30T12:00:00Z",
    failure: null
  };
}

function validRelationship(): CharacterRelationship {
  return {
    contractVersion: "2.0.0",
    entityId: "entity-relationship-1",
    stableSemanticId: "semantic-relationship-1",
    relationshipId: "relationship-1",
    runId: "run-1",
    snapshotId: "snapshot-1",
    revision: 1,
    effectiveRevision: 1,
    machineEntityFingerprint: sha("d"),
    effectiveValueFingerprint: sha("d"),
    effectiveAuthority: "runtime_agent",
    ordinal: 0,
    sourceCharacterId: null,
    targetCharacterId: "character-mira",
    sourceCandidateCharacterIds: ["character-tovin"],
    targetCandidateCharacterIds: [],
    resolution: "ambiguous",
    sceneId: "scene-1",
    chapterId: "chapter-1",
    scope: {
      kind: "scene",
      firstSceneId: "scene-1",
      lastSceneId: "scene-1",
      sourceRange: {
        sourceDocumentId: "document-1",
        extractionId: "extraction-1",
        extractionRevision: 2,
        offsetUnit: "unicode-code-point",
        startOffset: 10,
        endOffset: 20,
        textSha256: sha("e")
      }
    },
    validFromEventId: null,
    validThroughEventId: null,
    kind: "unknown",
    state: "An unresolved relationship is explicitly preserved.",
    change: "uncertain",
    confidence: {
      score: 0,
      classification: "unknown",
      basis: "The source evidence does not resolve both identities."
    },
    warnings: [],
    provenance: {
      origin: "runtime_agent",
      recordedAt: "2026-07-30T12:00:00Z",
      inputFingerprint: sha("4"),
      agentExecutionId: "execution-relationships",
      agentId: "character-relationships",
      agentVersion: "1.0.0",
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
      deterministic: true
    },
    evidence: []
  };
}

function validAnalysisJob(run: StoryAnalysisRun) {
  return {
    jobId: run.jobId,
    projectId: run.projectId,
    type: "analyze_whole_book" as const,
    state: "queued" as const,
    target: {
      type: "analysis_run",
      id: run.runId
    },
    inputRevision: run.storyRevision,
    inputFingerprint: run.storyFingerprint,
    attempt: 1,
    stage: "queued",
    progress: 0,
    checkpointAvailable: false,
    cancellationRequested: false,
    warnings: [],
    createdAt: "2026-07-30T11:59:00Z",
    updatedAt: "2026-07-30T11:59:00Z"
  };
}

function validCorrection(input: AppendAnalysisCorrectionInput) {
  return {
    contractVersion: "2.0.0" as const,
    correctionId: "correction-dialogue-1",
    projectId: input.projectId,
    runId: input.runId,
    snapshotId: "snapshot-1",
    category: input.category,
    targetCollection: input.targetCollection,
    targetEntityId: input.targetEntityId,
    expectedTargetRevision: input.expectedTargetRevision,
    expectedRunFingerprint: input.expectedRunFingerprint,
    previousValueFingerprint: input.previousValueFingerprint,
    correctedValueFingerprint: sha("9"),
    patch: input.patch,
    actor: {
      classification: "human" as const,
      actorId: "desktop-user"
    },
    reason: input.reason,
    recordedAt: "2026-07-30T12:01:00Z",
    immutable: true as const,
    lockedAgainstAutomation: true as const,
    ...(input.supersedesCorrectionId === undefined
      ? {}
      : { supersedesCorrectionId: input.supersedesCorrectionId }),
    idempotencyFingerprint: sha("a")
  };
}

function validGateReviews(
  run: StoryAnalysisRun,
  invalidatedGateIds: readonly AnalysisGateReview["gateId"][] = []
): readonly AnalysisGateReview[] {
  const snapshot = run.currentSnapshot;
  if (snapshot === null) {
    throw new Error("Test run requires a snapshot.");
  }
  return ANALYSIS_GATE_IDS.map((gateId, index) => {
    const invalidated = invalidatedGateIds.includes(gateId);
    const artifactFingerprint = sha("c");
    const evidenceFingerprint = sha("d");
    return {
      contractVersion: "2.0.0",
      reviewId: `review-${index + 1}`,
      projectId: run.projectId,
      gateId,
      runId: run.runId,
      snapshotId: snapshot.snapshotId,
      state: invalidated ? "invalidated" : "pending",
      revision: invalidated ? 2 : 1,
      artifactFingerprint,
      evidenceFingerprint,
      evidence: {
        projectId: run.projectId,
        sourceDocumentId: run.sourceDocumentId,
        extractionId: run.extractionId,
        extractionRevision: run.extractionRevision,
        storyId: run.storyId,
        profileId: run.profile.profileId,
        profileFingerprint: run.profile.fingerprint,
        runId: run.runId,
        runFingerprint: run.runFingerprint,
        snapshotId: snapshot.snapshotId,
        snapshotRevision: snapshot.revision,
        snapshotFingerprint: snapshot.snapshotFingerprint,
        artifactFingerprint,
        evidenceFingerprint
      },
      openWarningIds: [],
      acknowledgedWarningIds: [],
      latestDecision: null,
      provenance: invalidated
        ? {
            origin: "human_correction",
            recordedAt: "2026-07-30T12:01:00Z",
            inputFingerprint: evidenceFingerprint,
            correctionId: "correction-dialogue-1",
            deterministic: false
          }
        : {
            origin: "analysis_synthesis",
            recordedAt: "2026-07-30T12:00:00Z",
            inputFingerprint: run.inputFingerprint,
            producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
            producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
            deterministic: true
          },
      updatedAt: invalidated
        ? "2026-07-30T12:01:00Z"
        : "2026-07-30T12:00:00Z"
    };
  });
}

function completeGateReviews(
  template: AnalysisGateReview
): readonly AnalysisGateReview[] {
  return ANALYSIS_GATE_IDS.map((gateId, index) => {
    const reviewId = `review-${gateId}`;
    if (template.latestDecision === null) {
      return {
        ...template,
        reviewId,
        gateId,
        latestDecisionId: undefined,
        latestDecision: null
      };
    }
    const decisionId = `decision-${index + 1}`;
    const latestDecision = {
      ...template.latestDecision,
      decisionId,
      reviewId,
      gateId
    };
    return {
      ...template,
      reviewId,
      gateId,
      latestDecisionId: decisionId,
      latestDecision
    };
  });
}

function validGateDecisionResponse(input: DecideAnalysisReviewInput) {
  const run = {
    ...validRun(),
    status: "succeeded" as const,
    currentStage: "complete" as const,
    progress: 1,
    completedAt: "2026-07-30T12:00:00Z"
  };
  const snapshot = run.currentSnapshot;
  if (snapshot === null) {
    throw new Error("Test run requires a snapshot.");
  }
  const evidence = {
    projectId: run.projectId,
    sourceDocumentId: run.sourceDocumentId,
    extractionId: run.extractionId,
    extractionRevision: run.extractionRevision,
    storyId: run.storyId,
    profileId: run.profile.profileId,
    profileFingerprint: run.profile.fingerprint,
    runId: run.runId,
    runFingerprint: run.runFingerprint,
    snapshotId: snapshot.snapshotId,
    snapshotRevision: snapshot.revision,
    snapshotFingerprint: snapshot.snapshotFingerprint,
    artifactFingerprint: input.expectedArtifactFingerprint,
    evidenceFingerprint: input.expectedEvidenceFingerprint
  };
  const provenance = {
    origin: "human_review" as const,
    recordedAt: "2026-07-30T12:02:00Z",
    inputFingerprint: input.expectedEvidenceFingerprint,
    deterministic: false
  };
  const reviewBase = {
    contractVersion: "2.0.0" as const,
    reviewId: "review-structure-1",
    projectId: run.projectId,
    gateId: input.gateId,
    runId: run.runId,
    snapshotId: snapshot.snapshotId,
    state: "approved" as const,
    revision: input.expectedRevision + 1,
    artifactFingerprint: input.expectedArtifactFingerprint,
    evidenceFingerprint: input.expectedEvidenceFingerprint,
    evidence,
    openWarningIds: ["warning-1"],
    acknowledgedWarningIds: [...input.acknowledgedWarningIds],
    provenance,
    updatedAt: "2026-07-30T12:02:00Z"
  };
  const decision = {
    contractVersion: "2.0.0" as const,
    decisionId: "decision-structure-1",
    reviewId: reviewBase.reviewId,
    projectId: run.projectId,
    gateId: input.gateId,
    runId: run.runId,
    snapshotId: snapshot.snapshotId,
    decision: "approved" as const,
    artifactFingerprint: input.expectedArtifactFingerprint,
    evidenceFingerprint: input.expectedEvidenceFingerprint,
    evidence,
    actor: {
      classification: "human" as const,
      actorId: "desktop-user"
    },
    rationale: input.rationale,
    acknowledgedWarningIds: [...input.acknowledgedWarningIds],
    provenance,
    decidedAt: "2026-07-30T12:02:00Z",
    immutable: true as const,
    supersedesDecisionId: "decision-structure-prior"
  };
  const review = {
    ...reviewBase,
    latestDecisionId: decision.decisionId,
    latestDecision: decision
  };
  return {
    correlationId: "correlation-gate-decision",
    review,
    decision,
    run
  };
}

function strictEntityCases(): readonly {
  readonly collection: AnalysisEntityCollection;
  readonly entity: Readonly<Record<string, unknown>>;
  readonly invalidField: string;
  readonly invalidValue: unknown;
}[] {
  const span = validSourceSpan(0, 24);
  const exactText = {
    ...span,
    exactText: "Mira entered the room.",
    exactTextSha256: sha("a"),
    originalCodePointCount: 22,
    exactTextTruncated: false,
    originalTextPreserved: true
  };
  return [
    {
      collection: "chapters",
      entity: {
        ...validMachineEntityHeader("entity-chapter-strict"),
        chapterId: "chapter-1",
        title: "Chapter One",
        sourceSpan: span,
        firstSceneId: "scene-1",
        lastSceneId: "scene-1",
        sceneCount: 1
      },
      invalidField: "sceneCount",
      invalidValue: "1"
    },
    {
      collection: "scenes",
      entity: {
        ...validMachineEntityHeader("entity-scene-strict"),
        sceneId: "scene-1",
        chapterId: "chapter-1",
        heading: "The room",
        sourceSpan: span,
        boundaryKind: "heading",
        firstBeatId: "beat-1",
        lastBeatId: "beat-1",
        beatCount: 1
      },
      invalidField: "boundaryKind",
      invalidValue: "forged"
    },
    {
      collection: "beats",
      entity: {
        ...validMachineEntityHeader("entity-beat-strict"),
        beatId: "beat-1",
        chapterId: "chapter-1",
        sceneId: "scene-1",
        kind: "narration",
        sourceSpan: span,
        summary: "Mira enters."
      },
      invalidField: "kind",
      invalidValue: "unbounded_payload"
    },
    {
      collection: "mentions",
      entity: {
        ...validMachineEntityHeader("entity-mention-strict"),
        mentionId: "mention-1",
        chapterId: "chapter-1",
        sceneId: "scene-1",
        exactText,
        mentionKind: "proper_name",
        resolution: "resolved",
        effectiveCharacterId: "character-mira",
        candidateCharacterIds: ["character-mira"]
      },
      invalidField: "mentionKind",
      invalidValue: "prompt_injection"
    },
    {
      collection: "dialogue-lines",
      entity: {
        ...validMachineEntityHeader("entity-dialogue-strict"),
        dialogueLineId: "dialogue-1",
        chapterId: "chapter-1",
        sceneId: "scene-1",
        beatId: "beat-1",
        exactText,
        distinction: "spoken_dialogue",
        candidates: [],
        speakerState: "unknown",
        effectiveAttribution: {
          speakerCharacterId: null,
          selectedCandidateId: null,
          authority: "unresolved",
          confidence: {
            score: 0,
            classification: "unknown",
            basis: "No candidate was available."
          },
          requiresHumanReview: true
        }
      },
      invalidField: "speakerState",
      invalidValue: "proposed"
    },
    {
      collection: "pov-segments",
      entity: {
        ...validMachineEntityHeader("entity-pov-strict"),
        povSegmentId: "pov-1",
        chapterId: "chapter-1",
        sceneId: "scene-1",
        sourceSpan: span,
        mode: "third_person_limited",
        viewpointCharacterId: "character-mira",
        narratorCharacterId: null,
        shiftKind: "initial"
      },
      invalidField: "shiftKind",
      invalidValue: "silent_override"
    },
    {
      collection: "timeline-events",
      entity: {
        ...validMachineEntityHeader("entity-timeline-strict"),
        timelineEventId: "timeline-1",
        chapterId: "chapter-1",
        sceneId: "scene-1",
        kind: "present_action",
        label: "Mira enters.",
        narrativeOrdinal: 0,
        chronologicalOrdinal: 0,
        locationId: null,
        participantCharacterIds: ["character-mira"]
      },
      invalidField: "chronologicalOrdinal",
      invalidValue: "0"
    },
    {
      collection: "continuity-findings",
      entity: {
        ...validMachineEntityHeader("entity-continuity-strict"),
        continuityFindingId: "continuity-1",
        category: "other",
        severity: "info",
        machineStatus: "open",
        explanation: "A bounded synthetic continuity claim.",
        suggestedReviewAction: "Review the exact evidence.",
        relatedEntityIds: ["scene-1"],
        requiresHumanReview: true
      },
      invalidField: "category",
      invalidValue: "execute_external_tool"
    }
  ];
}

function validMachineEntityHeader(entityId: string) {
  return {
    contractVersion: "2.0.0" as const,
    entityId,
    stableSemanticId: `semantic-${entityId}`,
    runId: "run-1",
    snapshotId: "snapshot-1",
    revision: 1,
    effectiveRevision: 1,
    machineEntityFingerprint: sha("8"),
    effectiveValueFingerprint: sha("8"),
    effectiveAuthority: "runtime_agent" as const,
    ordinal: 0,
    confidence: {
      score: 0.9,
      classification: "high" as const,
      basis: "Deterministic source evidence."
    },
    warnings: [],
    provenance: {
      origin: "runtime_agent" as const,
      recordedAt: "2026-07-30T12:00:00Z",
      inputFingerprint: sha("4"),
      agentExecutionId: "execution-1",
      agentId: "story-structure" as const,
      agentVersion: "1.0.0" as const,
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
      deterministic: true
    },
    evidence: []
  };
}

function analysisCounts() {
  return {
    agentExecutions: 11,
    chapters: 2,
    scenes: 4,
    beats: 12,
    characters: 4,
    mentions: 15,
    dialogueLines: 6,
    narrationSpans: 8,
    povSegments: 4,
    locations: 3,
    timelineEvents: 5,
    temporalConstraints: 2,
    relationships: 3,
    emotionalStates: 6,
    dramaticIntents: 5,
    continuityFindings: 2,
    corrections: 0
  };
}
