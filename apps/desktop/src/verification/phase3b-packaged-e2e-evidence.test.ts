import { describe, expect, it } from "vitest";

import {
  phase3bAssertionKeys,
  phase3bFixtureEvidenceClassification,
  phase3bPackagedE2eSchemaVersion,
  validatePhase3bPackagedE2eResult,
  type Phase3bAuditionEvidence,
  type Phase3bPackagedE2eResult
} from "./phase3b-packaged-e2e-evidence";

const shaA = "a".repeat(64);
const shaB = "b".repeat(64);
const shaC = "c".repeat(64);
const shaD = "d".repeat(64);
const cacheKeyA = "e".repeat(64);
const completedAt = "2026-07-31T12:00:00.000Z";

describe("Phase 3B packaged E2E evidence", () => {
  it("accepts complete fixture-lifecycle, cache, invalidation, restart, and exit proof", () => {
    const result = fixtureResult();
    expect(validatePhase3bPackagedE2eResult(result)).toBe(result);
  });

  it("rejects quality overclaims, missing worker ownership, and private paths", () => {
    const result = fixtureResult();
    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        fixtureClaims: {
          ...result.fixtureClaims,
          naturalSpeechQualityProven: true
        }
      })
    ).toThrow("lifecycle evidence");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        process: {
          launches: result.process.launches.map((launch) => ({
            ...launch,
            ownedProcesses: launch.ownedProcesses.filter(
              (process) => process.kind !== "provider_worker"
            )
          }))
        }
      })
    ).toThrow();

    const firstLaunch = result.process.launches[0];
    const secondLaunch = result.process.launches[1];
    expect(firstLaunch).toBeDefined();
    expect(secondLaunch).toBeDefined();
    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        process: {
          launches: [
            {
              ...firstLaunch,
              providerRuntimeExit: {
                ...firstLaunch?.providerRuntimeExit,
                shutdownAcknowledged: false
              }
            },
            secondLaunch
          ]
        }
      })
    ).toThrow("authenticated graceful worker exit");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        restart: {
          ...result.restart,
          priorLaunchRuntimeExit: {
            ...result.restart.priorLaunchRuntimeExit,
            workerPid: 9999
          }
        }
      })
    ).toThrow("did not match its sidecar proof");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        runtime: {
          ...result.runtime,
          observedNetworkRequestCount: 0
        }
      })
    ).toThrow("network-policy evidence");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        runtime: {
          ...result.runtime,
          profileId: "C:\\private-runtime\\runtime"
        }
      })
    ).toThrow();
  });

  it("rejects fake cache hits and non-targeted invalidation", () => {
    const result = fixtureResult();
    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        cacheHit: {
          ...result.cacheHit,
          repeatedArtifactSha256: shaB
        }
      })
    ).toThrow("cache-hit proof");
    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        targetedInvalidation: {
          ...result.targetedInvalidation,
          preservedClipIds: ["clip-character-1"]
        }
      })
    ).toThrow("targeted invalidation");
    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        targetedInvalidation: {
          ...result.targetedInvalidation,
          supersedingEntryId: "unrelated-pronunciation-entry"
        }
      })
    ).toThrow("targeted invalidation");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        auditions: result.auditions.filter(
          (item) => item.auditionClipId !== result.cacheHit.repeatedClipId
        )
      })
    ).toThrow("cache-hit proof");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        auditions: result.auditions.filter(
          (item) =>
            item.requestFingerprint !==
            result.targetedInvalidation.regeneratedRequestFingerprint
        )
      })
    ).toThrow("targeted invalidation");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        auditions: result.auditions.map((item) =>
          item.auditionClipId === result.cacheHit.repeatedClipId
            ? { ...item, voiceRuntimeBindingFingerprint: shaD }
            : item
        )
      })
    ).toThrow("cache-hit proof");

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        auditions: result.auditions.map((item) =>
          item.requestFingerprint ===
          result.targetedInvalidation.regeneratedRequestFingerprint
            ? { ...item, providerVoiceId: "fixture-voice-drift" }
            : item
        )
      })
    ).toThrow("targeted invalidation");

    for (const repeatedExecutionTamper of [
      { providerDispatchCount: 1 },
      { runtimeInstanceId: "runtime-instance-cache-lie" },
      { sourceProviderRequestId: "request-unrelated" },
      { executionClassification: "provider_execution" }
    ]) {
      expect(() =>
        validatePhase3bPackagedE2eResult({
          ...result,
          auditions: result.auditions.map((item) =>
            item.auditionClipId === result.cacheHit.repeatedClipId
              ? { ...item, ...repeatedExecutionTamper }
              : item
          )
        })
      ).toThrow();
    }

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        auditions: result.auditions.map((item) =>
          item.auditionClipId === result.cacheHit.originalClipId
            ? { ...item, providerDispatchCount: 0 }
            : item
        )
      })
    ).toThrow();

    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        cacheHit: {
          ...result.cacheHit,
          lookupOnlyNoProviderExecutionProven: false
        }
      })
    ).toThrow("cache-hit proof");
  });

  it("records only lowercase SHA-256 cache identities and rejects raw cache material", () => {
    const result = fixtureResult();
    const serialized = JSON.stringify(
      validatePhase3bPackagedE2eResult(result)
    );
    expect(serialized).toContain('"cacheKey"');
    expect(serialized).toContain(cacheKeyA);
    expect(() =>
      validatePhase3bPackagedE2eResult({
        ...result,
        cacheHit: {
          ...result.cacheHit,
          repeatedCacheKey: "PRIVATE manuscript cache material"
        }
      })
    ).toThrow("invalid");
  });
});

function fixtureResult(): Phase3bPackagedE2eResult {
  const initialAuditions = [
    audition("narrator", "role-narrator", "clip-narrator", "artifact-narrator", shaA),
    audition("character", "role-character-1", "clip-character-1", "artifact-character-1", shaB),
    audition("character", "role-character-2", "clip-character-2", "artifact-character-2", shaC)
  ] as const;
  const repeatedAudition = {
    ...audition(
      "narrator",
      "role-narrator",
      "clip-narrator-cache-hit",
      "artifact-narrator-cache-hit",
      shaA
    ),
    providerRequestId: "request-role-narrator-cache-hit",
    requestFingerprint: shaD,
    executionClassification: "verified_cache_lookup" as const,
    providerDispatchCount: 0 as const,
    sourceProviderRequestId: initialAuditions[0].providerRequestId,
    runtimeInstanceId: null,
    cacheKey: initialAuditions[0].cacheKey,
    cacheStatus: "verified_hit" as const
  };
  const regeneratedAudition = {
    ...audition(
      "character",
      "role-character-1",
      "clip-character-1-regenerated",
      "artifact-character-1-regenerated",
      shaD
    ),
    requestFingerprint: shaB,
    cacheKey: cacheKeyA
  };
  const auditions = [
    ...initialAuditions,
    repeatedAudition,
    regeneratedAudition
  ] as const;
  return {
    schemaVersion: phase3bPackagedE2eSchemaVersion,
    completedAt,
    status: "passed",
    evidenceClassification: phase3bFixtureEvidenceClassification,
    fixtureClaims: {
      lifecycleEvidenceOnly: true,
      naturalSpeechQualityProven: false,
      productionExportEligible: false,
      humanListeningClaimed: false
    },
    runtime: {
      profileId: "deterministic-pcm-wav-fixture-windows",
      profileFingerprint: shaA,
      protocolVersion: "1.0.0",
      runtimeInstanceIds: ["runtime-instance-1", "runtime-instance-2"],
      networkPolicy: "python_socket_api_denied",
      observedNetworkRequestCount: null,
      externalNetworkObservation: {
        method: "owned_pid_tcp_endpoint_inventory",
        ownedPidsOnly: true,
        observedNonLoopbackEndpointCount: 0
      }
    },
    fixtureProvider: {
      providerId: "deterministic-pcm-wav-fixture",
      providerVersion: "1.0.0"
    },
    realProviderAdapter: {
      providerId: "kokoro-onnx-local",
      providerVersion: "1.0.0"
    },
    model: {
      modelPackageId: "deterministic-pcm-wav-fixture-package",
      manifestVersion: "1.0.0",
      modelPackageFingerprint: shaA,
      installationRevision: 2,
      verificationId: "verification-1",
      verificationFingerprint: shaB,
      verified: true,
      active: true
    },
    pronunciation: {
      dictionaryId: "dictionary-1",
      initialRevision: 2,
      initialFingerprint: shaA,
      initialEntryId: "pronunciation-1",
      initialEntryFingerprint: shaB,
      initialDecision: "approved",
      supersedingEntryId: "pronunciation-2",
      supersedingEntryFingerprint: shaC,
      supersedesEntryId: "pronunciation-1",
      supersedingDecision: "approved",
      finalRevision: 4,
      finalFingerprint: shaD
    },
    auditions,
    cacheHit: {
      originalClipId: "clip-narrator",
      repeatedClipId: "clip-narrator-cache-hit",
      originalRequestFingerprint: auditions[0].requestFingerprint,
      repeatedRequestFingerprint: shaD,
      originalCacheKey: auditions[0].cacheKey,
      repeatedCacheKey: auditions[0].cacheKey,
      artifactSha256: auditions[0].audio.sha256,
      repeatedArtifactSha256: auditions[0].audio.sha256,
      repeatedCacheStatus: "verified_hit",
      identicalCacheKeyInputsProven: true,
      lookupOnlyNoProviderExecutionProven: true
    },
    targetedInvalidation: {
      supersededEntryId: "pronunciation-1",
      supersedingEntryId: "pronunciation-2",
      beforeDictionaryFingerprint: shaA,
      afterDictionaryFingerprint: shaD,
      impactedRoleId: "role-character-1",
      priorRequestFingerprint: shaA,
      regeneratedRequestFingerprint: shaB,
      priorCacheKey: auditions[1].cacheKey,
      regeneratedCacheKey: cacheKeyA,
      priorArtifactSha256: shaB,
      regeneratedArtifactSha256: shaD,
      invalidatedClipIds: ["clip-character-1"],
      preservedClipIds: ["clip-narrator", "clip-character-2"],
      persistedInvalidatedGateStates: [
        {
          gateId: "pronunciation_review",
          reviewId: "review-pronunciation-invalidated",
          state: "pending",
          evidenceFingerprint: shaA
        },
        {
          gateId: "voice_readiness_review",
          reviewId: "review-readiness-invalidated",
          state: "blocked",
          evidenceFingerprint: shaB
        }
      ],
      targetedOnly: true
    },
    gateDecisions: [
      gate("per_role_audition_review", "role-narrator", 1),
      gate("per_role_audition_review", "role-character-1", 2),
      gate("per_role_audition_review", "role-character-2", 3),
      gate("narrator_audition_review", null, 4),
      gate("character_audition_review", null, 5),
      gate("pronunciation_review", null, 6),
      gate("voice_readiness_review", null, 7)
    ],
    restart: {
      runtimeProfilePersisted: true,
      modelVerificationPersisted: true,
      pronunciationDictionaryPersisted: true,
      pronunciationDecisionsPersisted: true,
      auditionSessionsPersisted: true,
      auditionScriptsPersisted: true,
      auditionClipsPersisted: true,
      cacheRecordsPersisted: true,
      audioQualityRecordsPersisted: true,
      auditionDecisionsPersisted: true,
      voiceReadinessDecisionPersisted: true,
      authenticatedRestoredAudioLoaded: true,
      priorLaunchRuntimeExit: runtimeExit(1, 4002, 4001)
    },
    process: {
      launches: [launch(1, 4000), launch(2, 5000)]
    },
    screenshot: {
      artifactId: "packaged-ui-screenshot",
      captured: true
    },
    assertions: Object.fromEntries(
      phase3bAssertionKeys.map((key) => [key, true])
    ) as unknown as Phase3bPackagedE2eResult["assertions"]
  };
}

function audition(
  roleType: "narrator" | "character",
  roleId: string,
  clipId: string,
  artifactId: string,
  artifactSha256: string
): Phase3bAuditionEvidence {
  return {
    roleId,
    roleType,
    assignmentId: `assignment-${roleId}`,
    assignmentRevision: 1,
    voiceRuntimeBindingId: `binding-${roleId}`,
    voiceRuntimeBindingFingerprint: shaB,
    providerVoiceId: `fixture-voice-${roleId}`,
    auditionSessionId: `session-${roleId}`,
    providerRequestId: `request-${roleId}`,
    requestFingerprint: shaA,
    executionClassification: "provider_execution",
    providerDispatchCount: 1,
    sourceProviderRequestId: null,
    runtimeInstanceId: "runtime-instance-1",
    normalizedTextSha256: shaB,
    pronunciationPlanFingerprint: shaC,
    cacheKey: artifactSha256,
    cacheStatus: "miss",
    auditionClipId: clipId,
    clipFingerprint: shaA,
    audioArtifactId: artifactId,
    audio: {
      mediaType: "audio/wav",
      codec: "pcm_s16le",
      sampleRateHz: 24000,
      channels: 1,
      sampleWidthBytes: 2,
      durationMilliseconds: 1000,
      byteSize: 48044,
      sha256: artifactSha256,
      nonSilencePassed: true,
      clippingPassed: true,
      blockingFindingCount: 0
    },
    authenticatedAudioLoaded: true,
    fixtureEvidenceOnly: true
  };
}

function gate(
  gateId: Phase3bPackagedE2eResult["gateDecisions"][number]["gateId"],
  roleId: string | null,
  index: number
): Phase3bPackagedE2eResult["gateDecisions"][number] {
  return {
    gateId,
    reviewId: `review-${index}`,
    decisionId: `decision-${index}`,
    roleId,
    evidenceFingerprint: shaA,
    decision: "approved",
    immutable: true
  };
}

function launch(
  launchNumber: 1 | 2,
  rootPid: number
): Phase3bPackagedE2eResult["process"]["launches"][number] {
  return {
    launch: launchNumber,
    ownedProcesses: [
      {
        pid: rootPid,
        parentPid: 100,
        kind: "electron",
        executableName: "Cinematic Story Studio.exe",
        creationIdentity: completedAt,
        goneAfterShutdown: true
      },
      {
        pid: rootPid + 1,
        parentPid: rootPid,
        kind: "service",
        executableName: "cinematic-story-service.exe",
        creationIdentity: completedAt,
        goneAfterShutdown: true
      },
      {
        pid: rootPid + 2,
        parentPid: rootPid + 1,
        kind: "provider_worker",
        executableName: "cinematic-story-service.exe",
        creationIdentity: completedAt,
        goneAfterShutdown: true
      }
    ],
    providerRuntimeExit: runtimeExit(
      launchNumber,
      rootPid + 2,
      rootPid + 1
    ),
    forcedPids: [],
    remainingPids: [],
    unrelatedProcessesInspected: false,
    unrelatedProcessesTerminated: false
  };
}

function runtimeExit(
  launchNumber: 1 | 2,
  workerPid: number,
  parentPid: number
): Phase3bPackagedE2eResult["process"]["launches"][number]["providerRuntimeExit"] {
  return {
    runtimeInstanceId: `runtime-instance-${launchNumber}`,
    workerPid,
    parentPid,
    state: "stopped",
    stoppedAt: completedAt,
    stopReasonCode: "clean",
    handshakeAuthenticated: true,
    shutdownAcknowledged: true,
    gracefulShutdownConfirmed: true,
    exitCode: 0,
    terminatedByParent: false,
    ownershipConfirmed: true,
    confirmedExited: true,
    ownedProcessesConfirmedExited: true,
    jobObjectAssigned: true,
    deniedNetworkAttemptCount: 0
  };
}
