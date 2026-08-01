import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import type { SpeechPreviewRequest } from "@cinematic-story-studio/contracts";

import {
  parseAppendPronunciationEntryRequest,
  parseClearAuditionCacheRequest,
  parseCreateAuditionScriptRequest,
  parseDecidePronunciationEntryRequest,
  parseGenerateAuditionRequest,
  parseLoadAuditionAudioRequest,
  parseListAuditionReviewDecisionsRequest,
  parseModelPackageActionRequest,
  parsePreviewAuditionNormalizationRequest,
  parseSelectLocalModelPackageRequest,
  validateAuditionClipsResponse,
  validateAuditionReviewDecisionsResponse,
  validateAuditionWorkspaceResponse,
  validateClearAuditionCacheResponse,
  validateCreateAuditionScriptResponse,
  validateCreateAuditionSessionResponse,
  validateCreatePronunciationEntryResponse,
  validateDecideAuditionReviewResponse,
  validateDecidePronunciationEntryResponse,
  validateGenerateAuditionResponse,
  validateModelPackageActionResponse,
  validateModelPackagesResponse,
  validatePreviewNormalizationResponse
} from "./audition-validation";

const sha = "a".repeat(64);

describe("Phase 3B desktop audition validation", () => {
  it("accepts only bounded unique explicit custom pronunciation scope IDs", () => {
    const common = {
      projectId: "project-1",
      auditionSessionId: "session-1",
      expectedSessionRevision: 1,
      text: "A bounded synthetic audition line.",
      sourceTextSha256: digest("A bounded synthetic audition line."),
      acceptedOptionalNormalizationIds: [] as string[],
      customPronunciationScopeIds: ["festival-names", "fictional-places"]
    };
    const script = envelope({
      ...common,
      kind: "standardized_synthetic",
      sourceDocumentId: null,
      sourceRevision: null,
      sourceSpan: null,
      idempotencyKey: "script-1"
    });
    const previewRequest = envelope(common);

    expect(parseCreateAuditionScriptRequest(script)).toEqual(script);
    expect(parsePreviewAuditionNormalizationRequest(previewRequest))
      .toEqual(previewRequest);
    expect(() =>
      parsePreviewAuditionNormalizationRequest(
        envelope({ ...common, sourceTextSha256: sha })
      )
    ).toThrow("declared SHA-256");
    expect(() =>
      parseCreateAuditionScriptRequest(
        envelope({
          ...script.payload,
          customPronunciationScopeIds: ["duplicate", "duplicate"]
        })
      )
    ).toThrow("duplicates");
    expect(() =>
      parsePreviewAuditionNormalizationRequest(
        envelope({
          ...common,
          customPronunciationScopeIds: Array.from(
            { length: 51 },
            (_, index) => `scope-${index}`
          )
        })
      )
    ).toThrow("limit");
    expect(() =>
      parsePreviewAuditionNormalizationRequest(
        envelope({ ...common, customPronunciationScopeIds: ["x".repeat(129)] })
      )
    ).toThrow("invalid");

    const withoutOptionalScopes = envelope({
      ...common,
      customPronunciationScopeIds: undefined
    });
    delete (withoutOptionalScopes.payload as Record<string, unknown>)
      .customPronunciationScopeIds;
    expect(parsePreviewAuditionNormalizationRequest(withoutOptionalScopes))
      .toEqual(withoutOptionalScopes);
  });

  it("accepts only exact project-owned binary audio descriptors", () => {
    const request = envelope({
      projectId: "project-1",
      auditionClipId: "clip-1",
      auditionSessionId: "session-1",
      audioArtifactId: "artifact-1",
      expectedClipRevision: 1,
      expectedClipFingerprint: sha,
      expectedArtifactSha256: sha,
      mediaType: "audio/wav",
      byteSize: 48
    });
    expect(parseLoadAuditionAudioRequest(request)).toEqual(request);
    expect(() =>
      parseLoadAuditionAudioRequest(
        envelope({
          ...request.payload,
          filePath: "C:\\private\\clip.wav"
        })
      )
    ).toThrow("unknown field");
    expect(() =>
      parseLoadAuditionAudioRequest(
        envelope({ ...request.payload, mediaType: "application/json" })
      )
    ).toThrow("PCM WAV");
  });

  it("rejects pronunciation markup, controls, wrong scope ownership, and unknown keys", () => {
    const payload = {
      projectId: "project-1",
      expectedDictionaryRevision: 1,
      expectedDictionaryFingerprint: sha,
      writtenForm: "Harbor",
      language: "en",
      locale: "en-US",
      scope: "project",
      scopeId: null,
      representation: "provider_neutral",
      pronunciation: "HAR-bor",
      ipa: null,
      providerId: null,
      providerCompiledValue: null,
      caseSensitive: false,
      matchRule: "whole_word",
      priority: 0,
      reason: "Reviewed locally.",
      supersedesEntryId: null,
      idempotencyKey: "pronunciation-1"
    } as const;
    expect(parseAppendPronunciationEntryRequest(envelope(payload)).payload)
      .toEqual(payload);
    const response = pronunciationMutationResponse({
      entryId: "pronunciation-created-1",
      entryRevision: 1,
      dictionaryRevision: 2,
      verificationState: "pending",
      reason: payload.reason
    });
    expect(validateCreatePronunciationEntryResponse(response, payload)).toBe(response);
    expect(() =>
      validateCreatePronunciationEntryResponse(
        {
          ...response,
          entry: { ...response.entry, writtenForm: "Unrelated" }
        },
        payload
      )
    ).toThrow("requested mutation");
    expect(() =>
      parseAppendPronunciationEntryRequest(
        envelope({ ...payload, pronunciation: "<phoneme>unsafe</phoneme>" })
      )
    ).toThrow("markup or control");
    expect(() =>
      parseAppendPronunciationEntryRequest(
        envelope({ ...payload, scope: "scene", scopeId: null })
      )
    ).toThrow("scope ID");
  });

  it("binds pronunciation decisions to exact entry and dictionary evidence", () => {
    const payload = {
      projectId: "project-1",
      entryId: "pronunciation-1",
      expectedEntryRevision: 1,
      expectedEntryFingerprint: sha,
      expectedDictionaryRevision: 2,
      expectedDictionaryFingerprint: sha,
      decision: "approve",
      rationale: "Reviewed the local pronunciation evidence.",
      idempotencyKey: "pronunciation-decision-1"
    } as const;
    expect(parseDecidePronunciationEntryRequest(envelope(payload)).payload)
      .toEqual(payload);
    const response = pronunciationMutationResponse({
      entryId: payload.entryId,
      entryRevision: 2,
      dictionaryRevision: 3,
      verificationState: "approved",
      reason: payload.rationale
    });
    expect(validateDecidePronunciationEntryResponse(response, payload)).toBe(response);
    expect(() =>
      validateDecidePronunciationEntryResponse(
        {
          ...response,
          entry: { ...response.entry, verificationState: "changes_requested" }
        },
        payload
      )
    ).toThrow("requested entry");
    expect(() =>
      parseDecidePronunciationEntryRequest(
        envelope({ ...payload, roleId: "renderer-invented-role" })
      )
    ).toThrow("unknown field");
    expect(() =>
      parseDecidePronunciationEntryRequest(
        envelope({ ...payload, expectedEntryFingerprint: "stale" })
      )
    ).toThrow("expectedEntryFingerprint");
  });

  it("validates an explicit revision-bound cache clear without path authority", () => {
    const payload = {
      projectId: "project-1",
      expectedProjectRevision: 7,
      reason: "Remove only project-owned private audition cache artifacts.",
      idempotencyKey: "cache-clear-1"
    };
    expect(parseClearAuditionCacheRequest(envelope(payload)).payload)
      .toEqual(payload);
    expect(() =>
      parseClearAuditionCacheRequest(
        envelope({ ...payload, cachePath: "C:\\private\\audio" })
      )
    ).toThrow("unknown field");

    const response = {
      correlationId: "correlation-cache-clear",
      projectId: "project-1",
      clearedRecordCount: 2,
      alreadyClearedRecordCount: 1,
      projectRevision: 8,
      purgedArtifactCount: 2
    };
    expect(validateClearAuditionCacheResponse(response, payload)).toBe(response);
    expect(() =>
      validateClearAuditionCacheResponse(
        { ...response, projectId: "project-unrelated" },
        payload
      )
    ).toThrow("active project");
    expect(() =>
      validateClearAuditionCacheResponse(
        { ...response, deletedUnrelatedRecordCount: 1 },
        payload
      )
    ).toThrow("unknown field");
  });

  it("binds generation to the complete hash-only evidence set and active project", () => {
    const request = envelope({
      projectId: "project-1",
      preview: preview("project-1")
    });
    expect(parseGenerateAuditionRequest(request)).toEqual(request);
    expect(() =>
      parseGenerateAuditionRequest(
        envelope({ projectId: "project-2", preview: preview("project-1") })
      )
    ).toThrow("active project");
    expect(() =>
      parseGenerateAuditionRequest(
        envelope({
          projectId: "project-1",
          preview: {
            ...preview("project-1"),
            evidence: {
              ...preview("project-1").evidence,
              sourceText: "private manuscript"
            }
          }
        })
      )
    ).toThrow("unknown field");
  });

  it("accepts only the authenticated script detail and exact compiled plans", () => {
    const expected = {
      projectId: "project-1",
      auditionSessionId: "session-1",
      expectedSessionRevision: 1,
      kind: "standardized_synthetic" as const,
      text: "Line\r\nbreak",
      sourceDocumentId: null,
      sourceRevision: null,
      sourceSpan: null,
      sourceTextSha256: digest("Line\r\nbreak"),
      acceptedOptionalNormalizationIds: [] as string[],
      idempotencyKey: "script-detail-1"
    };
    const normalizedTextSha256 = digest("Line\nbreak");
    const response = {
      correlationId: "correlation-script-detail",
      script: {
        contractVersion: "1.0.0",
        auditionScriptId: "script-1",
        auditionSessionId: "session-1",
        projectId: "project-1",
        roleId: "role-1",
        kind: "standardized_synthetic",
        sourceTextSha256: expected.sourceTextSha256,
        sourceSpan: null,
        sourceDocumentId: null,
        sourceAnalysisEntity: null,
        sourceRevision: null,
        normalizedTextSha256,
        normalizationPlanId: "normalization-plan-1",
        pronunciationPlanId: "pronunciation-plan-1",
        localOnly: true,
        scriptFingerprint: sha,
        createdAt: "2026-07-31T12:00:00Z",
        text: expected.text
      },
      normalizationPlan: normalizationResponse().plan,
      pronunciationPlan: {
        contractVersion: "1.0.0",
        pronunciationPlanId: "pronunciation-plan-1",
        projectId: "project-1",
        dictionaryId: "dictionary-1",
        dictionaryRevision: 1,
        dictionaryFingerprint: sha,
        sourceTextSha256: normalizedTextSha256,
        locale: "en-US",
        roleId: "role-1",
        scopeContext: {
          chapterId: null,
          sceneId: null,
          customScopeIds: []
        },
        appliedEntries: [],
        dependencyEntryRevisions: [],
        providerId: "fixture-provider",
        escapedProviderPayloadSha256: sha,
        planFingerprint: sha,
        provenance: provenance()
      },
      session: sessionResponse(2)
    };

    expect(validateCreateAuditionScriptResponse(response, expected)).toBe(response);
    expect(() =>
      validateCreateAuditionScriptResponse(
        {
          ...response,
          script: {
            ...response.script,
            absolutePath: "C:\\private\\audition-script.txt"
          }
        },
        expected
      )
    ).toThrow("unknown field");
    expect(() =>
      validateCreateAuditionScriptResponse(
        {
          ...response,
          session: { ...response.session, revision: 3 }
        },
        expected
      )
    ).toThrow("returned session");
    expect(() =>
      validateCreateAuditionScriptResponse(
        {
          ...response,
          normalizationPlan: {
            ...response.normalizationPlan,
            transformations: [
              {
                ...response.normalizationPlan.transformations[0],
                requiredByProvider: false,
                humanApprovalRequired: true,
                approved: true
              }
            ]
          }
        },
        expected
      )
    ).toThrow("approval evidence");
    expect(() =>
      validateCreateAuditionScriptResponse(
        response,
        {
          ...expected,
          acceptedOptionalNormalizationIds: ["missing-normalization-edit"]
        }
      )
    ).toThrow("absent from the plan");
    expect(() =>
      validateCreateAuditionScriptResponse(
        {
          ...response,
          pronunciationPlan: {
            ...response.pronunciationPlan,
            scopeContext: {
              ...response.pronunciationPlan.scopeContext,
              customScopeIds: ["unrequested-scope"]
            }
          }
        },
        expected
      )
    ).toThrow("custom scope evidence");
    expect(() =>
      validateCreateAuditionScriptResponse(
        {
          ...response,
          pronunciationPlan: {
            ...response.pronunciationPlan,
            appliedEntries: [
              {
                sourceSpan: { start: 0, end: 4 },
                entryId: "entry-1",
                entryRevision: 1,
                writtenFormSha256: sha,
                compiledValueSha256: sha,
                representation: "provider_neutral",
                privateText: "Line"
              }
            ],
            dependencyEntryRevisions: [{ entryId: "entry-1", revision: 1 }]
          }
        },
        expected
      )
    ).toThrow("unknown field");
  });

  it("binds queued provider requests to the public runtime profile evidence", () => {
    const governedPreview = preview("project-1");
    const response = {
      correlationId: "correlation-generate",
      session: {
        contractVersion: "1.0.0",
        auditionSessionId: "session-1",
        projectId: "project-1",
        roleId: "role-1",
        castAssignmentId: governedPreview.evidence.castAssignmentId,
        castAssignmentRevision: governedPreview.evidence.castAssignmentRevision,
        approvedCastSnapshotId: governedPreview.evidence.approvedCastSnapshotId,
        approvedCastSnapshotRevision:
          governedPreview.evidence.approvedCastSnapshotRevision,
        approvedCastSnapshotFingerprint:
          governedPreview.evidence.approvedCastSnapshotFingerprint,
        voiceRuntimeBindingId:
          governedPreview.evidence.voiceRuntimeBindingId,
        voiceRuntimeBindingFingerprint:
          governedPreview.evidence.voiceRuntimeBindingFingerprint,
        providerVoiceId: governedPreview.evidence.providerVoiceId,
        voiceRuntimeBinding: voiceRuntimeBinding(),
        providerId: governedPreview.evidence.providerId,
        providerVersion: governedPreview.evidence.providerVersion,
        modelPackageFingerprint:
          governedPreview.evidence.modelPackageFingerprint,
        runtimeProfileFingerprint:
          governedPreview.evidence.runtimeProfileFingerprint,
        pronunciationDictionaryRevision:
          governedPreview.evidence.pronunciationDictionaryRevision,
        pronunciationDictionaryFingerprint:
          governedPreview.evidence.pronunciationDictionaryFingerprint,
        state: "queued",
        revision: 1,
        scriptCount: 1,
        clipCount: 0,
        approvedClipId: null,
        jobId: "job-1",
        sessionFingerprint: sha,
        createdAt: "2026-07-31T12:00:00Z",
        updatedAt: "2026-07-31T12:00:00Z",
        provenance: provenance()
      },
      providerRequest: {
        contractVersion: "1.0.0",
        providerRequestId: "provider-request-1",
        speechPreviewRequestId: governedPreview.requestId,
        providerId: governedPreview.evidence.providerId,
        providerVersion: governedPreview.evidence.providerVersion,
        modelId: governedPreview.evidence.modelId,
        modelVersion: governedPreview.evidence.modelVersion,
        modelPackageFingerprint: governedPreview.evidence.modelPackageFingerprint,
        runtimeProfileId: governedPreview.evidence.runtimeProfileId,
        runtimeProfileFingerprint: governedPreview.evidence.runtimeProfileFingerprint,
        voiceRuntimeBindingId:
          governedPreview.evidence.voiceRuntimeBindingId,
        voiceRuntimeBindingFingerprint:
          governedPreview.evidence.voiceRuntimeBindingFingerprint,
        providerVoiceId: governedPreview.evidence.providerVoiceId,
        runtimeInstanceId: null,
        voiceProfileId: governedPreview.evidence.voiceProfileId,
        voiceProfileVersion: governedPreview.evidence.voiceProfileVersion,
        castAssignmentId: governedPreview.evidence.castAssignmentId,
        castAssignmentRevision: governedPreview.evidence.castAssignmentRevision,
        auditionSessionId: governedPreview.auditionSessionId,
        normalizedTextSha256: governedPreview.normalizedTextSha256,
        pronunciationPlanFingerprint: governedPreview.pronunciationPlanFingerprint,
        providerControlFingerprint: governedPreview.providerControls.controlsFingerprint,
        cacheKey: sha,
        state: "queued",
        startedAt: null,
        finishedAt: null,
        retryable: false,
        warnings: [],
        requestFingerprint: governedPreview.requestFingerprint,
        provenance: {
          ...provenance(),
          inputFingerprint: governedPreview.requestFingerprint,
          details: {
            providerLanguage: "en-US",
            providerVoiceId: governedPreview.evidence.providerVoiceId,
            restrictedLocalUseAcknowledged: false,
            restrictedLocalUseAcknowledgementEventId: null,
            voiceRuntimeBindingFingerprint:
              governedPreview.evidence.voiceRuntimeBindingFingerprint,
            voiceRuntimeBindingId:
              governedPreview.evidence.voiceRuntimeBindingId,
            executionClassification: "provider_execution",
            providerDispatchCount: 0
          }
        }
      },
      jobId: "job-1"
    };
    expect(
      validateGenerateAuditionResponse(response, {
        projectId: "project-1",
        preview: governedPreview
      })
    ).toBe(response);
    const completedProviderExecution = {
      ...response,
      providerRequest: {
        ...response.providerRequest,
        state: "succeeded",
        runtimeInstanceId: "runtime-instance-1",
        startedAt: "2026-07-31T12:00:01Z",
        finishedAt: "2026-07-31T12:00:02Z",
        provenance: {
          ...response.providerRequest.provenance,
          details: {
            ...response.providerRequest.provenance.details,
            executionClassification: "provider_execution",
            providerDispatchCount: 1
          }
        }
      }
    };
    expect(
      validateGenerateAuditionResponse(completedProviderExecution, {
        projectId: "project-1",
        preview: governedPreview
      })
    ).toBe(completedProviderExecution);
    const verifiedCacheLookup = {
      ...response,
      providerRequest: {
        ...response.providerRequest,
        state: "succeeded",
        startedAt: "2026-07-31T12:00:03Z",
        finishedAt: "2026-07-31T12:00:03Z",
        provenance: {
          ...response.providerRequest.provenance,
          details: {
            ...response.providerRequest.provenance.details,
            executionClassification: "verified_cache_lookup",
            providerDispatchCount: 0,
            sourceProviderRequestId: "provider-request-source"
          }
        }
      }
    };
    expect(
      validateGenerateAuditionResponse(verifiedCacheLookup, {
        projectId: "project-1",
        preview: governedPreview
      })
    ).toBe(verifiedCacheLookup);
    const preDispatchRetry = {
      ...response,
      providerRequest: {
        ...response.providerRequest,
        providerRequestId: "provider-request-retry-2",
        state: "running",
        provenance: {
          ...response.providerRequest.provenance,
          origin: "application",
          details: {
            attempt: 2,
            supersedesProviderRequestId: response.providerRequest.providerRequestId,
            originalIdempotencyKeyFingerprint: sha,
            executionClassification: "provider_execution",
            providerDispatchCount: 0
          }
        }
      }
    };
    expect(
      validateGenerateAuditionResponse(preDispatchRetry, {
        projectId: "project-1",
        preview: governedPreview
      })
    ).toBe(preDispatchRetry);

    for (const state of ["failed", "cancelled"] as const) {
      const preDispatchTerminal = {
        ...response,
        providerRequest: {
          ...response.providerRequest,
          state,
          finishedAt: "2026-07-31T12:00:02Z",
          retryable: state === "failed"
        }
      };
      expect(
        validateGenerateAuditionResponse(preDispatchTerminal, {
          projectId: "project-1",
          preview: governedPreview
        })
      ).toBe(preDispatchTerminal);
    }

    for (const state of ["running", "failed", "cancelled"] as const) {
      const invokedProviderState = {
        ...completedProviderExecution,
        providerRequest: {
          ...completedProviderExecution.providerRequest,
          state,
          finishedAt:
            state === "running" ? null : "2026-07-31T12:00:02Z",
          retryable: state === "failed"
        }
      };
      expect(
        validateGenerateAuditionResponse(invokedProviderState, {
          projectId: "project-1",
          preview: governedPreview
        })
      ).toBe(invokedProviderState);
    }

    for (const state of ["running", "failed", "cancelled"] as const) {
      const cacheLookupState = {
        ...verifiedCacheLookup,
        providerRequest: {
          ...verifiedCacheLookup.providerRequest,
          state,
          finishedAt:
            state === "running" ? null : "2026-07-31T12:00:04Z",
          retryable: state === "failed"
        }
      };
      expect(
        validateGenerateAuditionResponse(cacheLookupState, {
          projectId: "project-1",
          preview: governedPreview
        })
      ).toBe(cacheLookupState);
    }

    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...preDispatchRetry,
          providerRequest: {
            ...preDispatchRetry.providerRequest,
            provenance: {
              ...preDispatchRetry.providerRequest.provenance,
              details: {
                ...preDispatchRetry.providerRequest.provenance.details,
                attempt: 1
              }
            }
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("attempt");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...preDispatchRetry,
          providerRequest: {
            ...preDispatchRetry.providerRequest,
            provenance: {
              ...preDispatchRetry.providerRequest.provenance,
              details: {
                ...preDispatchRetry.providerRequest.provenance.details,
                supersedesProviderRequestId:
                  preDispatchRetry.providerRequest.providerRequestId
              }
            }
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("retry lineage referenced itself");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          providerRequest: {
            ...response.providerRequest,
            state: "succeeded",
            finishedAt: "2026-07-31T12:00:02Z"
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("state contradicted its dispatch count");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...verifiedCacheLookup,
          providerRequest: {
            ...verifiedCacheLookup.providerRequest,
            state: "queued",
            startedAt: null,
            finishedAt: null
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("queued provider request");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...verifiedCacheLookup,
          providerRequest: {
            ...verifiedCacheLookup.providerRequest,
            runtimeInstanceId: "runtime-instance-cache-hit"
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("cache lookup claimed provider or runtime execution");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...verifiedCacheLookup,
          providerRequest: {
            ...verifiedCacheLookup.providerRequest,
            provenance: {
              ...verifiedCacheLookup.providerRequest.provenance,
              details: {
                ...verifiedCacheLookup.providerRequest.provenance.details,
                providerDispatchCount: 1
              }
            }
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("cache lookup claimed a provider dispatch");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...completedProviderExecution,
          providerRequest: {
            ...completedProviderExecution.providerRequest,
            runtimeInstanceId: null
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("provider dispatch omitted its exact runtime identity");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          providerRequest: {
            ...response.providerRequest,
            runtimeProfileId: "internal-database-row-id"
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("governed audition evidence");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          providerRequest: {
            ...response.providerRequest,
            runtimeInstanceId: "runtime-instance-before-dispatch"
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("queued provider request");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          providerRequest: { ...response.providerRequest, retryable: true }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("Only a failed provider request");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          providerRequest: {
            ...response.providerRequest,
            state: "running",
            runtimeInstanceId: "runtime-instance-1",
            startedAt: "2026-07-31T12:00:01Z",
            finishedAt: "2026-07-31T12:00:02Z",
            provenance: {
              ...response.providerRequest.provenance,
              details: {
                ...response.providerRequest.provenance.details,
                executionClassification: "provider_execution",
                providerDispatchCount: 1
              }
            }
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("non-terminal provider request");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          providerRequest: {
            ...response.providerRequest,
            state: "failed",
            retryable: true
          }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("terminal provider request");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          session: { ...response.session, revision: 2 }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("governed preview evidence");
    expect(() =>
      validateGenerateAuditionResponse(
        {
          ...response,
          session: { ...response.session, absolutePath: "C:\\private\\session" }
        },
        { projectId: "project-1", preview: governedPreview }
      )
    ).toThrow("unknown field");
  });

  it.each([
    ["per_role_audition_review", "role-1"],
    ["voice_readiness_review", null]
  ] as const)(
    "accepts an idempotent historical %s replay after a newer decision",
    (gateId, roleId) => {
      const replay = historicalReviewReplay(gateId, roleId);
      expect(
        validateDecideAuditionReviewResponse(replay.response, replay.request)
      ).toBe(replay.response);
      expect(() =>
        validateDecideAuditionReviewResponse(
          {
            ...replay.response,
            review: {
              ...replay.response.review,
              state: "changes_requested",
              latestDecision: replay.newer,
              updatedAt: replay.newer.decidedAt
            }
          },
          replay.request
        )
      ).toThrow(/requested review|project the requested/u);
    }
  );

  it.each([
    "per_role_audition_review",
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review"
  ] as const)(
    "binds a returned voice-readiness snapshot to the exact %s decision evidence",
    (gateId) => {
      const fixture = boundReviewDecisionResponse(gateId);
      expect(
        validateDecideAuditionReviewResponse(
          fixture.response,
          fixture.request
        )
      ).toBe(fixture.response);
    }
  );

  it.each([
    ["approvedCastSnapshotFingerprint", "b".repeat(64)],
    ["runtimeProfileFingerprint", "c".repeat(64)],
    ["modelVerificationFingerprint", "d".repeat(64)],
    ["rightsEvidenceFingerprint", "e".repeat(64)]
  ] as const)(
    "rejects a decision response whose snapshot has a stale %s",
    (field, staleValue) => {
      const fixture = boundReviewDecisionResponse("per_role_audition_review");
      expect(() =>
        validateDecideAuditionReviewResponse(
          {
            ...fixture.response,
            voiceReadinessSnapshot: {
              ...fixture.response.voiceReadinessSnapshot,
              [field]: staleValue
            }
          },
          fixture.request
        )
      ).toThrow("did not match the returned review evidence");
    }
  );

  it.each([
    ["narrator_audition_review", "narratorAuditionDecisionIds", ["other-decision"]],
    ["character_audition_review", "characterAuditionDecisionIds", ["other-decision"]],
    ["pronunciation_review", "pronunciationReviewDecisionId", "other-decision"]
  ] as const)(
    "rejects a %s snapshot that omits the returned decision",
    (gateId, field, staleValue) => {
      const fixture = boundReviewDecisionResponse(gateId);
      expect(() =>
        validateDecideAuditionReviewResponse(
          {
            ...fixture.response,
            voiceReadinessSnapshot: {
              ...fixture.response.voiceReadinessSnapshot,
              [field]: staleValue
            }
          },
          fixture.request
        )
      ).toThrow("did not bind the returned review decision");
    }
  );

  it.each([
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review"
  ] as const)(
    "accepts a refreshed %s snapshot that excludes a non-approved decision",
    (gateId) => {
      const fixture = boundReviewDecisionResponse(gateId);
      const decision = {
        ...fixture.response.decision,
        decision: "changes_requested" as const
      };
      const voiceReadinessSnapshot = {
        ...fixture.response.voiceReadinessSnapshot,
        narratorAuditionDecisionIds: ["approved-narrator-decision"],
        characterAuditionDecisionIds: ["approved-character-decision"],
        pronunciationReviewDecisionId: "approved-pronunciation-decision"
      };
      const response = {
        ...fixture.response,
        review: {
          ...fixture.response.review,
          state: "changes_requested" as const,
          latestDecision: decision
        },
        decision,
        voiceReadinessSnapshot
      };
      expect(
        validateDecideAuditionReviewResponse(response, {
          ...fixture.request,
          decision: "request_changes"
        })
      ).toBe(response);
    }
  );

  it.each([
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review"
  ] as const)(
    "rejects a refreshed %s snapshot that claims a non-approved decision",
    (gateId) => {
      const fixture = boundReviewDecisionResponse(gateId);
      const decision = {
        ...fixture.response.decision,
        decision: "rejected" as const
      };
      expect(() =>
        validateDecideAuditionReviewResponse(
          {
            ...fixture.response,
            review: {
              ...fixture.response.review,
              state: "rejected",
              latestDecision: decision
            },
            decision
          },
          { ...fixture.request, decision: "reject" }
        )
      ).toThrow("did not bind the returned review decision");
    }
  );

  it("requires the exact voice-readiness snapshot for a readiness decision", () => {
    const fixture = boundReviewDecisionResponse("voice_readiness_review");
    expect(() =>
      validateDecideAuditionReviewResponse(
        { ...fixture.response, voiceReadinessSnapshot: null },
        fixture.request
      )
    ).toThrow("omitted its exact snapshot evidence");
    expect(() =>
      validateDecideAuditionReviewResponse(
        {
          ...fixture.response,
          voiceReadinessSnapshot: {
            ...fixture.response.voiceReadinessSnapshot,
            snapshotFingerprint: "f".repeat(64)
          }
        },
        fixture.request
      )
    ).toThrow("did not bind the returned review decision");
  });

  it("permits only non-install lifecycle actions through renderer IPC", () => {
    const payload = {
      projectId: "project-1",
      modelPackageId: "model-package-1",
      expectedManifestFingerprint: sha,
      expectedInstallationRevision: 1,
      action: "verify",
      reason: "Verify the installed package.",
      idempotencyKey: "model-verify-1"
    } as const;
    expect(parseModelPackageActionRequest(envelope(payload)).payload).toEqual(payload);
    expect(() =>
      parseModelPackageActionRequest(envelope({ ...payload, action: "install" }))
    ).toThrow("action");

    const response = {
      correlationId: "correlation-model-action",
      installation: {
        contractVersion: "1.0.0",
        installationId: "installation-1",
        modelPackageId: "model-package-1",
        manifestFingerprint: sha,
        installationRevision: 2,
        storageKey: "model-storage-1",
        status: "active",
        active: true,
        installedAt: "2026-07-31T12:00:00Z",
        updatedAt: "2026-07-31T12:00:00Z",
        lastAction: "verify",
        actionReasonCode: payload.reason,
        immutableEventId: "model-event-1",
        provenance: provenance()
      },
      verification: {
        contractVersion: "1.0.0",
        verificationId: "verification-1",
        installationId: "installation-1",
        modelPackageId: "model-package-1",
        manifestFingerprint: sha,
        verificationFingerprint: sha,
        status: "verified",
        verifiedFileCount: 1,
        verifiedByteSize: 1,
        unexpectedFileCount: 0,
        symlinkOrReparsePointDetected: false,
        checkedAt: "2026-07-31T12:00:00Z",
        blockingReasonCodes: [],
        provenance: provenance()
      }
    };
    expect(validateModelPackageActionResponse(response, payload)).toBe(response);
    expect(() =>
      validateModelPackageActionResponse(
        {
          ...response,
          installation: {
            ...response.installation,
            absolutePath: "C:\\private\\models\\active"
          }
        },
        payload
      )
    ).toThrow("unknown field");
    expect(() =>
      validateModelPackageActionResponse(
        {
          ...response,
          installation: {
            ...response.installation,
            actionReasonCode: "A different operation rationale."
          }
        },
        payload
      )
    ).toThrow("requested package operation");
    expect(() =>
      validateModelPackageActionResponse(
        {
          ...response,
          verification: {
            ...response.verification,
            unexpectedFileCount: 1
          }
        },
        payload
      )
    ).toThrow("blocking or incomplete evidence");
  });

  it("requires an explicit pathless restricted-use acknowledgement before archive selection", () => {
    const payload = {
      projectId: "project-1",
      modelPackageId: "kokoro-package-1",
      expectedManifestFingerprint: sha,
      expectedInstallationRevision: null,
      operation: "install",
      acknowledgeRestrictedLocalUse: true,
      reason: "Install exact local bytes for restricted audition use only.",
      idempotencyKey: "model-install-1"
    } as const;
    expect(parseSelectLocalModelPackageRequest(envelope(payload)).payload)
      .toEqual(payload);
    expect(
      "sourcePath" in parseSelectLocalModelPackageRequest(envelope(payload)).payload
    ).toBe(false);
    expect(() =>
      parseSelectLocalModelPackageRequest(
        envelope({ ...payload, acknowledgeRestrictedLocalUse: false })
      )
    ).toThrow("acknowledgement");
    expect(() =>
      parseSelectLocalModelPackageRequest(
        envelope({ ...payload, sourcePath: "C:\\private\\model.zip" })
      )
    ).toThrow("unknown field");
  });

  it("distinguishes maintainer-referenced converted model bytes from official releases", () => {
    const response = {
      correlationId: "correlation-models",
      projectId: "project-1",
      pageSize: 1,
      total: 1,
      items: [
        {
          manifest: {
            contractVersion: "1.0.0",
            manifestVersion: "1.0.0",
            modelPackageId: "kokoro-onnx-package",
            providerId: "kokoro-onnx",
            modelId: "kokoro-v1",
            modelVersion: "1.0.0",
            runtimeVersion: "1.0.0",
            platform: "windows",
            architecture: "x64",
            sourceClassification: "maintainer_referenced_conversion",
            officialSourceReference: "maintainer-release-v1",
            licenseIdentifier: "Apache-2.0",
            commercialUseClassification: "allowed",
            attributionRequirements: [],
            manifestFingerprint: sha,
            files: [
              {
                relativePath: "model.onnx",
                byteSize: 1,
                sha256: sha,
                mediaClassification: "onnx",
                executable: false
              }
            ],
            totalExpandedByteSize: 1,
            requiredRuntimeDependencies: [],
            compatibilityConstraints: [],
            state: "active",
            provenance: provenance()
          },
          installation: null,
          verification: null
        }
      ]
    };
    expect(validateModelPackagesResponse(response, { projectId: "project-1" }))
      .toBe(response);
    expect(() =>
      validateModelPackagesResponse(
        { ...response, pageSize: 0 },
        { projectId: "project-1" }
      )
    ).toThrow("current page");
    expect(() =>
      validateModelPackagesResponse(
        {
          ...response,
          items: [
            {
              ...response.items[0],
              manifest: {
                ...response.items[0].manifest,
                sourceClassification: "official_model_repository"
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("sourceClassification");
    expect(() =>
      validateModelPackagesResponse(
        {
          ...response,
          items: [
            {
              ...response.items[0],
              manifest: {
                ...response.items[0].manifest,
                absolutePath: "C:\\private\\model.onnx"
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("unknown field");
    expect(() =>
      validateModelPackagesResponse(
        {
          ...response,
          items: [
            {
              ...response.items[0],
              verification: modelVerificationRecord(
                "installation-kokoro",
                "kokoro-onnx-package"
              )
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("omitted its installation evidence");
    expect(() =>
      validateModelPackagesResponse(
        {
          ...response,
          items: [
            {
              ...response.items[0],
              installation: modelInstallationRecord(
                "installation-kokoro",
                "another-package"
              )
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("did not match its package manifest");
  });

  it("validates exact normalization text, hashes, spans, approval types, and provenance", () => {
    const response = normalizationResponse();
    const expected = {
      projectId: "project-1",
      auditionSessionId: "session-1",
      expectedSessionRevision: 1,
      text: "Line\r\nbreak",
      sourceTextSha256: digest("Line\r\nbreak"),
      acceptedOptionalNormalizationIds: [],
      customPronunciationScopeIds: []
    };

    expect(validatePreviewNormalizationResponse(response, expected)).toBe(response);
    expect(() =>
      validatePreviewNormalizationResponse(
        { ...response, auditionSessionRevision: 2 },
        expected
      )
    ).toThrow("requested session revision");
    expect(() =>
      validatePreviewNormalizationResponse(
        { ...response, providerId: "unrelated-provider" },
        expected
      )
    ).toThrow("provider did not match");
    expect(() =>
      validatePreviewNormalizationResponse(
        {
          ...response,
          acceptedOptionalNormalizationIds: ["unrequested-edit"]
        },
        expected
      )
    ).toThrow("did not match the request");
    expect(() =>
      validatePreviewNormalizationResponse(
        { ...response, customPronunciationScopeIds: ["unrequested-scope"] },
        expected
      )
    ).toThrow("did not match the request");
    expect(() =>
      validatePreviewNormalizationResponse(
        {
          ...response,
          plan: { ...response.plan, privateSourceText: "Line\r\nbreak" }
        },
        expected
      )
    ).toThrow("unknown field");
    expect(() =>
      validatePreviewNormalizationResponse(
        {
          ...response,
          plan: {
            ...response.plan,
            transformations: [
              {
                ...response.plan.transformations[0],
                originalTextSha256: sha
              }
            ]
          }
        },
        expected
      )
    ).toThrow("SHA-256");
    expect(() =>
      validatePreviewNormalizationResponse(
        {
          ...response,
          plan: {
            ...response.plan,
            transformations: [
              {
                ...response.plan.transformations[0],
                sourceSpan: { start: Number.NaN, end: 6 }
              }
            ]
          }
        },
        expected
      )
    ).toThrow("out of range");
    expect(() =>
      validatePreviewNormalizationResponse(
        {
          ...response,
          plan: {
            ...response.plan,
            transformations: [
              {
                ...response.plan.transformations[0],
                destinationSpan: { start: 4, end: 6 }
              }
            ]
          }
        },
        expected
      )
    ).toThrow("destination text");
    expect(() =>
      validatePreviewNormalizationResponse(
        {
          ...response,
          plan: {
            ...response.plan,
            provenance: { ...response.plan.provenance, details: {} }
          }
        },
        expected
      )
    ).toThrow("unknown field");
  });

  it("rejects network-enabled providers and path-shaped audio storage keys", () => {
    const workspace = workspaceResponse();
    expect(validateAuditionWorkspaceResponse(workspace, { projectId: "project-1" }))
      .toBe(workspace);
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...workspace,
          workspace: {
            ...workspace.workspace,
            providers: [
              { ...workspace.workspace.providers[0], networkRequired: true }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("local-only boundary");

    const clips = clipResponse();
    expect(validateAuditionClipsResponse(clips, { projectId: "project-1" }))
      .toBe(clips);
    expect(() =>
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              audioArtifact: {
                ...clips.items[0].audioArtifact,
                storageKey: "private/path.wav"
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow(/storageKey|reveal paths/u);
    expect(() =>
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              state: "reviewable",
              audioArtifact: {
                ...clips.items[0].audioArtifact,
                availability: "purged",
                playbackEligible: true
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow(/availability|playback eligibility/u);
    expect(
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              state: "invalidated",
              audioArtifact: {
                ...clips.items[0].audioArtifact,
                availability: "quarantined",
                playbackEligible: false
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toBeDefined();

    expect(() =>
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              audioQuality: {
                ...clips.items[0].audioQuality,
                peakDbfs: Number.NaN
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("peakDbfs");
    expect(() =>
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              audioQuality: {
                ...clips.items[0].audioQuality,
                silenceRatio: 1.01
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("silenceRatio");
    expect(() =>
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              audioQuality: {
                ...clips.items[0].audioQuality,
                audioArtifactId: "artifact-unrelated"
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("did not belong");
    expect(() =>
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              audioQuality: {
                ...clips.items[0].audioQuality,
                warningCodes: ["not a code"]
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("invalid");
    expect(() =>
      validateAuditionClipsResponse(
        {
          ...clips,
          items: [
            {
              ...clips.items[0],
              audioQuality: {
                ...clips.items[0].audioQuality,
                unexpectedMeasurement: 1
              }
            }
          ]
        },
        { projectId: "project-1" }
      )
    ).toThrow("unknown field");
  });

  it("accepts server-issued session evidence and rejects assignment drift", () => {
    const workspace = workspaceResponse();
    const generationRequest = preview("project-1");
    const role = {
      roleId: "role-1",
      roleType: "narrator",
      displayLabel: "Narrator",
      required: true,
      assignmentId: "assignment-1",
      assignmentRevision: 1,
      voiceProfileId: "voice-1",
      voiceDisplayLabel: "Fixture neutral",
      voiceRuntimeBinding: voiceRuntimeBinding(),
      runtimeBindingStatus: "compatible",
      runtimeBindingReasonCode: null,
      rightsState: "verified",
      latestSessionId: generationRequest.auditionSessionId,
      latestClipId: null,
      reviewState: "pending",
      sessionEvidence: generationRequest.evidence,
      generationRequest
    };
    const response = {
      ...workspace,
      workspace: {
        ...workspace.workspace,
        roles: { items: [role], pageSize: 1, total: 1 }
      }
    };
    expect(validateAuditionWorkspaceResponse(response, { projectId: "project-1" }))
      .toBe(response);
    const createdSession = sessionResponse(1);
    const createInput = {
      projectId: "project-1",
      roleId: role.roleId,
      evidence: generationRequest.evidence,
      idempotencyKey: "create-session-exact-evidence"
    };
    expect(
      validateCreateAuditionSessionResponse(
        {
          correlationId: "correlation-create-session",
          session: createdSession
        },
        createInput
      )
    ).toBeDefined();
    expect(() =>
      validateCreateAuditionSessionResponse(
        {
          correlationId: "correlation-create-session-substitution",
          session: {
            ...createdSession,
            providerVoiceId: "fixture-voice-substituted",
            voiceRuntimeBinding: {
              ...createdSession.voiceRuntimeBinding,
              providerVoiceId: "fixture-voice-substituted"
            }
          }
        },
        createInput
      )
    ).toThrow("exact governed evidence");
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            roles: {
              items: [{ ...role, assignmentRevision: 2 }],
              pageSize: 1,
              total: 1
            }
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("current binding");
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            roles: {
              items: [{ ...role, latestSessionId: "session-unrelated" }],
              pageSize: 1,
              total: 1
            }
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("latest session");
  });

  it("validates hash-only runtime process identities and rejects exposed paths", () => {
    const workspace = workspaceResponse();
    const runtimeInstance = {
      contractVersion: "1.0.0",
      runtimeInstanceId: "runtime-instance-1",
      runtimeProfileId: "runtime-profile-1",
      runtimeProfileFingerprint: sha,
      providerId: "fixture-provider",
      modelPackageFingerprint: sha,
      workerPid: 4102,
      parentPid: 4101,
      executableIdentity: "cinematic-story-service.exe",
      executableSha256: sha,
      creationIdentity: sha,
      protocolVersion: "1.0.0",
      handshakeAuthenticated: true,
      state: "ready",
      startedAt: "2026-07-31T12:00:00Z",
      lastActivityAt: "2026-07-31T12:00:01Z",
      stoppedAt: null,
      stopReasonCode: null,
      shutdownAcknowledged: null,
      gracefulShutdownConfirmed: null,
      exitCode: null,
      terminatedByParent: null,
      ownershipConfirmed: null,
      confirmedExited: null,
      ownedProcessesConfirmedExited: null,
      jobObjectAssigned: true,
      deniedNetworkAttemptCount: 0,
      networkPolicy: "python_socket_api_denied",
      observedNetworkRequestCount: null,
      restartReconciliation: null,
      provenance: provenance()
    };
    const response = {
      ...workspace,
      workspace: {
        ...workspace.workspace,
        runtimeProfiles: [
          {
            contractVersion: "1.0.0",
            runtimeProfileId: "runtime-profile-1",
            revision: 1,
            runtimeDescriptorId: "runtime-descriptor-1",
            profileFingerprint: sha,
            providerIds: ["fixture-provider"],
            compatibleModelPackageIds: ["model-package-1"],
            protocolVersion: "1.0.0",
            startupDeadlineMilliseconds: 10_000,
            requestDeadlineMilliseconds: 30_000,
            idleShutdownMilliseconds: 60_000,
            maximumRetryAttempts: 0,
            maximumConcurrentRequests: 1,
            shellUsed: false,
            networkAccessDuringSynthesis: false,
            active: true,
            provenance: provenance()
          }
        ],
        runtimeInstances: [runtimeInstance]
      }
    };
    expect(validateAuditionWorkspaceResponse(response, { projectId: "project-1" }))
      .toBe(response);
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [
              {
                ...runtimeInstance,
                executableIdentity: "C:\\private\\cinematic-story-service.exe"
              }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("exposed a path");
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [
              {
                ...runtimeInstance,
                runtimeProfileId: "internal-runtime-profile-row"
              }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("public runtime profile");

    const stoppedRuntime = {
      ...runtimeInstance,
      state: "stopped",
      stoppedAt: "2026-07-31T12:00:02Z",
      stopReasonCode: "clean",
      shutdownAcknowledged: true,
      gracefulShutdownConfirmed: true,
      exitCode: 0,
      terminatedByParent: false,
      ownershipConfirmed: true,
      confirmedExited: true,
      ownedProcessesConfirmedExited: true
    };
    expect(
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [stoppedRuntime]
          }
        },
        { projectId: "project-1" }
      )
    ).toBeTruthy();
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [
              { ...stoppedRuntime, shutdownAcknowledged: false }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("authenticated graceful-exit evidence");

    const failedRuntime = {
      ...runtimeInstance,
      state: "failed",
      stoppedAt: "2026-07-31T12:00:02Z",
      stopReasonCode: "process_error",
      shutdownAcknowledged: false,
      gracefulShutdownConfirmed: false,
      exitCode: 1,
      terminatedByParent: false,
      ownershipConfirmed: true,
      confirmedExited: true,
      ownedProcessesConfirmedExited: true
    };
    expect(
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: { ...response.workspace, runtimeInstances: [failedRuntime] }
        },
        { projectId: "project-1" }
      )
    ).toBeTruthy();
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [
              { ...failedRuntime, gracefulShutdownConfirmed: true }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("contradictory graceful-exit evidence");

    const reconciledRuntime = {
      ...runtimeInstance,
      state: "failed",
      restartReconciliation: {
        contractVersion: "1.0.0",
        reasonCode: "SERVICE_RESTART_INTERRUPTED",
        priorState: "idle",
        observedAt: "2026-07-31T12:00:01Z",
        observerServiceInstanceId: "service-instance-2",
        ownershipConfirmed: false,
        gracefulShutdownConfirmed: false,
        processExitConfirmed: false
      }
    };
    expect(
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [reconciledRuntime]
          }
        },
        { projectId: "project-1" }
      )
    ).toBeTruthy();
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [
              { ...reconciledRuntime, restartReconciliation: null }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("terminal evidence");
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeInstances: [
              {
                ...reconciledRuntime,
                restartReconciliation: {
                  ...reconciledRuntime.restartReconciliation,
                  ownershipConfirmed: true
                }
              }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("overstated process-exit proof");

    const health = {
      contractVersion: "1.0.0",
      runtimeProfileId: runtimeInstance.runtimeProfileId,
      runtimeProfileFingerprint: runtimeInstance.runtimeProfileFingerprint,
      runtimeInstanceId: runtimeInstance.runtimeInstanceId,
      providerId: runtimeInstance.providerId,
      status: "available",
      reasonCode: "RUNTIME_READY",
      checkedAt: "2026-07-31T12:00:01Z",
      expiresAt: "2026-07-31T12:01:01Z",
      modelPackageFingerprint: runtimeInstance.modelPackageFingerprint,
      protocolVersion: "1.0.0"
    };
    expect(
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeHealth: [health],
            runtimeInstances: [runtimeInstance]
          }
        },
        { projectId: "project-1" }
      )
    ).toBeTruthy();
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            runtimeHealth: [
              { ...health, runtimeInstanceId: "runtime-instance-unrelated" }
            ],
            runtimeInstances: [runtimeInstance]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("emitted runtime instance");
  });

  it("cross-links workspace model verification evidence to its installation", () => {
    const workspace = workspaceResponse();
    const installation = modelInstallationRecord(
      "installation-workspace-1",
      "model-package-1"
    );
    const verification = modelVerificationRecord(
      installation.installationId,
      installation.modelPackageId
    );
    const response = {
      ...workspace,
      workspace: {
        ...workspace.workspace,
        modelInstallations: [installation],
        modelVerifications: [verification]
      }
    };
    expect(validateAuditionWorkspaceResponse(response, { projectId: "project-1" }))
      .toBeTruthy();
    expect(() =>
      validateAuditionWorkspaceResponse(
        {
          ...response,
          workspace: {
            ...response.workspace,
            modelVerifications: [
              { ...verification, installationId: "installation-unrelated" }
            ]
          }
        },
        { projectId: "project-1" }
      )
    ).toThrow("did not match its installation");
  });

  it("binds bounded immutable review history to one exact gate scope", () => {
    const request = envelope({
      projectId: "project-1",
      gateId: "per_role_audition_review",
      roleId: "role-1",
      cursor: "opaque-page-2",
      limit: 2
    } as const);
    expect(parseListAuditionReviewDecisionsRequest(request)).toEqual(request);
    expect(() =>
      parseListAuditionReviewDecisionsRequest(
        envelope({ ...request.payload, roleId: null })
      )
    ).toThrow("requires a role ID");
    expect(() =>
      parseListAuditionReviewDecisionsRequest(
        envelope({
          ...request.payload,
          gateId: "voice_readiness_review",
          roleId: "role-1"
        })
      )
    ).toThrow("cannot carry a role ID");

    const older = reviewDecision({
      decisionId: "decision-1",
      decidedAt: "2026-07-31T12:00:00Z",
      supersedesDecisionId: null
    });
    const newer = reviewDecision({
      decisionId: "decision-2",
      decidedAt: "2026-07-31T12:05:00Z",
      supersedesDecisionId: "decision-1"
    });
    const response = {
      correlationId: "correlation-review-history",
      projectId: "project-1",
      gateId: "per_role_audition_review",
      roleId: "role-1",
      pageSize: 2,
      total: 2,
      items: [newer, older]
    };
    expect(
      validateAuditionReviewDecisionsResponse(response, request.payload)
    ).toBe(response);
    expect(() =>
      validateAuditionReviewDecisionsResponse(
        { ...response, items: [older, newer] },
        request.payload
      )
    ).toThrow("newest first");
    expect(() =>
      validateAuditionReviewDecisionsResponse(
        { ...response, roleId: "role-2" },
        request.payload
      )
    ).toThrow("requested scope");
  });
});

function envelope<T>(payload: T) {
  return { contractVersion: "1.0.0" as const, payload };
}

function reviewDecision(overrides: {
  readonly decisionId: string;
  readonly decidedAt: string;
  readonly supersedesDecisionId: string | null;
}) {
  return {
    contractVersion: "1.0.0",
    decisionId: overrides.decisionId,
    reviewId: "review-role-1",
    projectId: "project-1",
    gateId: "per_role_audition_review",
    roleId: "role-1",
    decision: "approved",
    actor: { classification: "human", actorId: "local-user" },
    expectedReviewRevision: 1,
    evidenceFingerprint: sha,
    rationale: "Reviewed this exact local audition evidence.",
    decidedAt: overrides.decidedAt,
    immutable: true,
    supersedesDecisionId: overrides.supersedesDecisionId,
    provenance: provenance()
  };
}

function boundReviewDecisionResponse(
  gateId:
    | "per_role_audition_review"
    | "narrator_audition_review"
    | "character_audition_review"
    | "pronunciation_review"
    | "voice_readiness_review"
) {
  const roleId = gateId === "per_role_audition_review" ? "role-1" : null;
  const evidence = {
    projectId: "project-1",
    gateId,
    roleId,
    auditionSessionId: roleId === null ? null : "session-1",
    auditionClipId: roleId === null ? null : "clip-1",
    auditionClipRevision: roleId === null ? null : 1,
    approvedCastSnapshotFingerprint: "1".repeat(64),
    castAssignmentFingerprint: roleId === null ? null : "2".repeat(64),
    rightsRecordFingerprint: "3".repeat(64),
    runtimeProfileFingerprint: "4".repeat(64),
    modelVerificationFingerprint: "5".repeat(64),
    pronunciationDictionaryFingerprint: "6".repeat(64),
    pronunciationDependencyFingerprint: "7".repeat(64),
    audioQualityFingerprint: roleId === null ? null : "8".repeat(64),
    evidenceFingerprint: "9".repeat(64)
  };
  const decision = {
    contractVersion: "1.0.0" as const,
    decisionId: `decision-${gateId}`,
    reviewId: `review-${gateId}`,
    projectId: "project-1",
    gateId,
    roleId,
    decision: "approved" as const,
    actor: { classification: "human" as const, actorId: "local-user" },
    expectedReviewRevision: 1,
    evidenceFingerprint: evidence.evidenceFingerprint,
    rationale: "Approve the exact governed audition evidence.",
    decidedAt: "2026-07-31T12:00:00Z",
    immutable: true as const,
    supersedesDecisionId: null,
    provenance: provenance()
  };
  const request = {
    projectId: "project-1",
    reviewId: decision.reviewId,
    gateId,
    roleId,
    expectedReviewRevision: 1,
    expectedEvidenceFingerprint: evidence.evidenceFingerprint,
    decision: "approve" as const,
    rationale: decision.rationale,
    supersedesDecisionId: null,
    idempotencyKey: `decision-${gateId}`
  };
  const response = {
    correlationId: `correlation-${gateId}`,
    review: {
      contractVersion: "1.0.0" as const,
      reviewId: decision.reviewId,
      projectId: "project-1",
      gateId,
      roleId,
      state: "approved" as const,
      revision: 1,
      prerequisiteGateIds: [],
      evidence,
      blockerCodes: [],
      warningCodes: [],
      latestDecision: decision,
      updatedAt: decision.decidedAt
    },
    decision,
    voiceReadinessSnapshot: voiceReadinessSnapshotFor(
      evidence,
      gateId,
      decision.decisionId
    )
  };
  return { request, response };
}

function voiceReadinessSnapshotFor(
  evidence: {
    readonly approvedCastSnapshotFingerprint: string;
    readonly rightsRecordFingerprint: string;
    readonly runtimeProfileFingerprint: string;
    readonly modelVerificationFingerprint: string;
    readonly evidenceFingerprint: string;
  },
  gateId:
    | "per_role_audition_review"
    | "narrator_audition_review"
    | "character_audition_review"
    | "pronunciation_review"
    | "voice_readiness_review",
  decisionId: string
) {
  return {
    contractVersion: "1.0.0" as const,
    snapshotId: "voice-readiness-snapshot-1",
    projectId: "project-1",
    revision: 1,
    approvedCastSnapshotId: "cast-snapshot-1",
    approvedCastSnapshotRevision: 1,
    approvedCastSnapshotFingerprint: evidence.approvedCastSnapshotFingerprint,
    runtimeProfileFingerprint: evidence.runtimeProfileFingerprint,
    modelVerificationFingerprint: evidence.modelVerificationFingerprint,
    rightsEvidenceFingerprint: evidence.rightsRecordFingerprint,
    narratorAuditionDecisionIds:
      gateId === "narrator_audition_review"
        ? [decisionId]
        : ["narrator-decision-1"],
    characterAuditionDecisionIds:
      gateId === "character_audition_review"
        ? [decisionId]
        : ["character-decision-1"],
    pronunciationReviewDecisionId:
      gateId === "pronunciation_review" ? decisionId : "pronunciation-decision-1",
    requiredRoleCount: 2,
    approvedRoleCount: 2,
    blockingFindingCodes: [],
    snapshotFingerprint:
      gateId === "voice_readiness_review"
        ? evidence.evidenceFingerprint
        : "a".repeat(64),
    reviewEligible: true,
    authorizes: "later_performance_direction_only" as const,
    authorizesFullBookRendering: false as const,
    createdAt: "2026-07-31T12:00:00Z",
    immutable: true as const
  };
}

function historicalReviewReplay(
  gateId: "per_role_audition_review" | "voice_readiness_review",
  roleId: string | null
) {
  const evidence = {
    projectId: "project-1",
    gateId,
    roleId,
    auditionSessionId: roleId === null ? null : "session-1",
    auditionClipId: roleId === null ? null : "clip-1",
    auditionClipRevision: roleId === null ? null : 1,
    approvedCastSnapshotFingerprint: sha,
    castAssignmentFingerprint: roleId === null ? null : sha,
    rightsRecordFingerprint: sha,
    runtimeProfileFingerprint: sha,
    modelVerificationFingerprint: sha,
    pronunciationDictionaryFingerprint: sha,
    pronunciationDependencyFingerprint: sha,
    audioQualityFingerprint: roleId === null ? null : sha,
    evidenceFingerprint: sha
  };
  const approved = {
    contractVersion: "1.0.0" as const,
    decisionId: `decision-${gateId}-a`,
    reviewId: `review-${gateId}`,
    projectId: "project-1",
    gateId,
    roleId,
    decision: "approved" as const,
    actor: { classification: "human" as const, actorId: "local-user" },
    expectedReviewRevision: 1,
    evidenceFingerprint: sha,
    rationale: "Approve the exact governed audition evidence.",
    decidedAt: "2026-07-31T12:00:00Z",
    immutable: true as const,
    supersedesDecisionId: null,
    provenance: provenance()
  };
  const newer = {
    ...approved,
    decisionId: `decision-${gateId}-b`,
    decision: "changes_requested" as const,
    expectedReviewRevision: 2,
    rationale: "Request changes after a second human review.",
    decidedAt: "2026-07-31T12:05:00Z",
    supersedesDecisionId: approved.decisionId
  };
  return {
    newer,
    request: {
      projectId: "project-1",
      reviewId: approved.reviewId,
      gateId,
      roleId,
      expectedReviewRevision: approved.expectedReviewRevision,
      expectedEvidenceFingerprint: sha,
      decision: "approve" as const,
      rationale: approved.rationale,
      supersedesDecisionId: null,
      idempotencyKey: `replay-${gateId}-a`
    },
    response: {
      correlationId: `correlation-${gateId}-replay-a`,
      review: {
        contractVersion: "1.0.0" as const,
        reviewId: approved.reviewId,
        projectId: "project-1",
        gateId,
        roleId,
        state: "approved" as const,
        revision: 1,
        prerequisiteGateIds: [],
        evidence,
        blockerCodes: [],
        warningCodes: [],
        latestDecision: approved,
        updatedAt: approved.decidedAt
      },
      decision: approved,
      voiceReadinessSnapshot:
        gateId === "voice_readiness_review"
          ? voiceReadinessSnapshotFor(evidence, gateId, approved.decisionId)
          : null
    }
  };
}

function sessionResponse(revision: number) {
  return {
    contractVersion: "1.0.0",
    auditionSessionId: "session-1",
    projectId: "project-1",
    roleId: "role-1",
    castAssignmentId: "assignment-1",
    castAssignmentRevision: 1,
    approvedCastSnapshotId: "cast-snapshot-1",
    approvedCastSnapshotRevision: 1,
    approvedCastSnapshotFingerprint: sha,
    voiceRuntimeBindingId: "voice-runtime-binding-1",
    voiceRuntimeBindingFingerprint: sha,
    providerVoiceId: "fixture-voice-1",
    voiceRuntimeBinding: voiceRuntimeBinding(),
    providerId: "fixture-provider",
    providerVersion: "1.0.0",
    modelPackageFingerprint: sha,
    runtimeProfileFingerprint: sha,
    pronunciationDictionaryRevision: 1,
    pronunciationDictionaryFingerprint: sha,
    state: "draft",
    revision,
    scriptCount: 1,
    clipCount: 0,
    approvedClipId: null,
    jobId: null,
    sessionFingerprint: sha,
    createdAt: "2026-07-31T12:00:00Z",
    updatedAt: "2026-07-31T12:00:00Z",
    provenance: provenance()
  };
}

function pronunciationMutationResponse(input: {
  readonly entryId: string;
  readonly entryRevision: number;
  readonly dictionaryRevision: number;
  readonly verificationState:
    | "pending"
    | "approved"
    | "changes_requested"
    | "rejected";
  readonly reason: string;
}) {
  const dictionaryFingerprint = digest(
    `dictionary-${input.dictionaryRevision}`
  );
  return {
    correlationId: `correlation-${input.entryId}-${input.entryRevision}`,
    entry: {
      contractVersion: "1.0.0",
      entryId: input.entryId,
      projectId: "project-1",
      dictionaryId: "dictionary-1",
      dictionaryRevision: input.dictionaryRevision,
      revision: input.entryRevision,
      writtenForm: "Harbor",
      normalizedLookupForm: "harbor",
      language: "en",
      locale: "en-US",
      scope: "project",
      scopeId: null,
      representation: "provider_neutral",
      pronunciation: "HAR-bor",
      ipa: null,
      providerId: null,
      providerCompiledValue: null,
      caseSensitive: false,
      matchRule: "whole_word",
      priority: 0,
      actor: { classification: "human", actorId: "local-user" },
      reason: input.reason,
      verificationState: input.verificationState,
      entryFingerprint: digest(
        `entry-${input.entryId}-${input.entryRevision}`
      ),
      supersedesEntryId: null,
      supersededByEntryId: null,
      immutable: true,
      provenance: provenance()
    },
    dictionary: {
      contractVersion: "1.0.0",
      dictionaryId: "dictionary-1",
      projectId: "project-1",
      revision: input.dictionaryRevision,
      entryCount: 1,
      currentEntryCount: 1,
      dictionaryFingerprint,
      createdAt: "2026-07-31T12:00:00Z",
      updatedAt: "2026-07-31T12:00:00Z",
      provenance: provenance()
    },
    invalidatedClipIds: [],
    invalidatedClipCount: 0,
    invalidatedClipIdsTruncated: false,
    preservedClipIds: [],
    preservedClipCount: 0,
    preservedClipIdsTruncated: false,
    invalidatedGateIds: []
  };
}

function preview(projectId: string): SpeechPreviewRequest {
  return {
    contractVersion: "1.0.0",
    requestId: "preview-1",
    auditionSessionId: "session-1",
    auditionSessionRevision: 1,
    auditionScriptId: "script-1",
    auditionScriptFingerprint: sha,
    evidence: {
      projectId,
      sourceDocumentId: "source-1",
      sourceRevision: 1,
      extractionId: "extraction-1",
      extractionRevision: 1,
      extractedTextSha256: sha,
      phase2RunId: "analysis-run-1",
      phase2SnapshotId: "analysis-snapshot-1",
      phase2SnapshotRevision: 1,
      phase2SnapshotFingerprint: sha,
      phase2CorrectionSetFingerprint: sha,
      castingRunId: "casting-run-1",
      approvedCastSnapshotId: "cast-snapshot-1",
      approvedCastSnapshotRevision: 1,
      approvedCastSnapshotFingerprint: sha,
      castAssignmentId: "assignment-1",
      castAssignmentRevision: 1,
      voiceProfileId: "voice-1",
      voiceProfileVersion: "1.0.0",
      voiceRuntimeBindingId: "voice-runtime-binding-1",
      voiceRuntimeBindingFingerprint: sha,
      providerVoiceId: "fixture-voice-1",
      providerId: "fixture-provider",
      providerVersion: "1.0.0",
      modelId: "fixture-model",
      modelVersion: "1.0.0",
      catalogRevisionId: "catalog-1",
      catalogFingerprint: sha,
      rightsRecordId: "rights-1",
      rightsRecordRevision: 1,
      rightsRecordFingerprint: sha,
      pronunciationDictionaryId: "dictionary-1",
      pronunciationDictionaryRevision: 1,
      pronunciationDictionaryFingerprint: sha,
      runtimeProfileId: "runtime-profile-1",
      runtimeProfileFingerprint: sha,
      modelPackageId: "model-package-1",
      modelPackageFingerprint: sha,
      producerVersion: "1.0.0"
    },
    normalizedTextSha256: sha,
    normalizationPlanFingerprint: sha,
    pronunciationPlanFingerprint: sha,
    providerControls: {
      speakingRate: 1,
      pitch: null,
      style: null,
      energy: null,
      controlsFingerprint: sha
    },
    outputFormat: "pcm_s16le_wav",
    sampleRateHz: 24_000,
    channels: 1,
    idempotencyKey: "generate-1",
    requestFingerprint: sha
  };
}

function workspaceResponse() {
  return {
    correlationId: "correlation-1",
    workspace: {
      contractVersion: "1.0.0",
      projectId: "project-1",
      prerequisites: [],
      approvedCastSnapshot: null,
      providers: [
        {
          contractVersion: "1.0.0",
          providerId: "fixture-provider",
          providerVersion: "1.0.0",
          adapterId: "fixture-adapter",
          adapterVersion: "1.0.0",
          providerClass: "deterministic_fixture",
          displayName: "Fixture provider",
          synthesisImplemented: true,
          localOnly: true,
          networkRequired: false,
          credentialsRequired: false,
          deterministic: true,
          productionExportEligible: false,
          supportedLanguages: ["en-US"],
          outputFormats: ["pcm_s16le_wav"],
          supportedSampleRatesHz: [24_000],
          licenseIdentifier: "fixture-only",
          commercialUseClassification: "fixture_only",
          attributionRequired: false,
          status: "available",
          statusReasonCode: null,
          descriptorFingerprint: sha,
          provenance: provenance()
        }
      ],
      runtimeProfiles: [],
      runtimeHealth: [],
      runtimeInstances: [],
      modelInstallations: [],
      modelVerifications: [],
      currentDictionary: null,
      roles: { items: [], pageSize: 0, total: 0 },
      reviews: [],
      voiceReadinessSnapshot: null,
      updatedAt: "2026-07-31T12:00:00Z"
    }
  };
}

function provenance() {
  return {
    origin: "application",
    producerId: "local-speech-audition-orchestrator@1.0.0",
    producerVersion: "1.0.0",
    recordedAt: "2026-07-31T12:00:00Z"
  };
}

function voiceRuntimeBinding() {
  return {
    contractVersion: "1.0.0",
    bindingId: "voice-runtime-binding-1",
    bindingKind: "declared_fixture_adapter",
    voiceProfileId: "voice-1",
    voiceProfileVersion: "1.0.0",
    voiceProfileFingerprint: sha,
    sourceProviderId: "fixture-source-provider",
    sourceProviderVersion: "1.0.0",
    sourceProviderFingerprint: sha,
    sourceModelId: "fixture-source-model",
    sourceModelVersion: "1.0.0",
    sourceModelFingerprint: sha,
    providerId: "fixture-provider",
    providerVersion: "1.0.0",
    providerVoiceId: "fixture-voice-1",
    modelId: "fixture-model",
    modelVersion: "1.0.0",
    modelPackageId: "model-package-1",
    modelPackageFingerprint: sha,
    runtimeProfileId: "runtime-profile-1",
    runtimeProfileFingerprint: sha,
    bindingFingerprint: sha,
    active: true,
    provenance: provenance(),
    createdAt: "2026-07-31T12:00:00Z"
  };
}

function modelInstallationRecord(
  installationId: string,
  modelPackageId: string
) {
  return {
    contractVersion: "1.0.0",
    installationId,
    modelPackageId,
    manifestFingerprint: sha,
    installationRevision: 1,
    storageKey: `model-storage-${installationId}`,
    status: "active",
    active: true,
    installedAt: "2026-07-31T12:00:00Z",
    updatedAt: "2026-07-31T12:00:00Z",
    lastAction: "activate",
    actionReasonCode: "Activate exact verified package.",
    immutableEventId: `event-${installationId}`,
    provenance: provenance()
  };
}

function modelVerificationRecord(
  installationId: string,
  modelPackageId: string
) {
  return {
    contractVersion: "1.0.0",
    verificationId: `verification-${installationId}`,
    installationId,
    modelPackageId,
    manifestFingerprint: sha,
    verificationFingerprint: sha,
    status: "verified",
    verifiedFileCount: 1,
    verifiedByteSize: 1,
    unexpectedFileCount: 0,
    symlinkOrReparsePointDetected: false,
    checkedAt: "2026-07-31T12:00:00Z",
    blockingReasonCodes: [],
    provenance: provenance()
  };
}

function normalizationResponse() {
  return {
    correlationId: "correlation-normalization",
    projectId: "project-1",
    auditionSessionId: "session-1",
    auditionSessionRevision: 1,
    providerId: "fixture-provider",
    acceptedOptionalNormalizationIds: [],
    customPronunciationScopeIds: [],
    plan: {
      contractVersion: "1.0.0",
      normalizationPlanId: "normalization-plan-1",
      projectId: "project-1",
      originalTextSha256: digest("Line\r\nbreak"),
      normalizedTextSha256: digest("Line\nbreak"),
      providerId: "fixture-provider",
      profileId: "audition-text-normalization",
      profileVersion: "1.0.0",
      transformations: [
        {
          transformationId: "normalization-edit-1",
          kind: "line_ending",
          sourceSpan: { start: 4, end: 6 },
          destinationSpan: { start: 4, end: 5 },
          originalTextSha256: digest("\r\n"),
          replacementTextSha256: digest("\n"),
          originalText: "\r\n",
          replacementText: "\n",
          reasonCode: "NORMALIZATION_LINE_ENDING",
          requiredByProvider: true,
          humanApprovalRequired: false,
          approved: true
        }
      ],
      appliedPronunciationEntryIds: [],
      unsupportedCharacterCodePoints: [],
      warnings: [],
      humanReviewRequired: false,
      planFingerprint: sha,
      provenance: provenance()
    }
  };
}

function clipResponse() {
  return {
    correlationId: "correlation-clip",
    projectId: "project-1",
    pageSize: 1,
    total: 1,
    items: [
      {
        contractVersion: "1.0.0",
        projectId: "project-1",
        auditionClipId: "clip-1",
        auditionSessionId: "session-1",
        auditionScriptId: "script-1",
        roleId: "role-1",
        castAssignmentId: "assignment-1",
        castAssignmentRevision: 1,
        providerRequestId: "provider-request-1",
        providerId: "fixture-provider",
        providerVersion: "1.0.0",
        voiceRuntimeBindingId: "voice-runtime-binding-1",
        voiceRuntimeBindingFingerprint: sha,
        providerVoiceId: "fixture-voice-1",
        providerClass: "deterministic_fixture",
        modelId: "fixture-model",
        modelVersion: "1.0.0",
        modelPackageFingerprint: sha,
        runtimeProfileFingerprint: sha,
        normalizedTextSha256: sha,
        pronunciationPlanFingerprint: sha,
        providerControlFingerprint: sha,
        cacheKey: sha,
        cacheStatus: "miss",
        cacheProof: {
          cacheRecordId: "cache-record-1",
          cacheKey: sha,
          voiceRuntimeBindingId: "voice-runtime-binding-1",
          voiceRuntimeBindingFingerprint: sha,
          providerVoiceId: "fixture-voice-1",
          verificationFingerprint: sha
        },
        state: "reviewable",
        revision: 1,
        clipFingerprint: sha,
        createdAt: "2026-07-31T12:00:00Z",
        provenance: provenance(),
        audioArtifact: {
          contractVersion: "1.0.0",
          projectId: "project-1",
          audioArtifactId: "artifact-1",
          storageKey: "audition-artifact:artifact-1",
          mediaType: "audio/wav",
          codec: "pcm_s16le",
          sampleRateHz: 24_000,
          channels: 1,
          sampleWidthBytes: 2,
          frameCount: 2,
          durationMilliseconds: 2 / 24,
          byteSize: 48,
          sha256: sha,
          availability: "present",
          playbackEligible: true,
          publishedAtomically: true,
          createdAt: "2026-07-31T12:00:00Z",
          immutable: true
        },
        audioQuality: {
          contractVersion: "1.0.0",
          qualityRecordId: "quality-1",
          projectId: "project-1",
          audioArtifactId: "artifact-1",
          profileId: "audition-audio-qc",
          profileVersion: "1.0.0",
          validWav: true,
          nonSilent: true,
          peakDbfs: -12,
          silenceRatio: 0,
          clippedSampleCount: 0,
          subjectiveQualityClaimed: false,
          warningCodes: [],
          blockingFindingCodes: [],
          qualityFingerprint: sha,
          measuredAt: "2026-07-31T12:00:00Z",
          provenance: provenance()
        }
      }
    ]
  };
}

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}
