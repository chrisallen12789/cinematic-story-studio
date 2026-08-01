import { createHash } from "node:crypto";

import { expect, type Page } from "@playwright/test";

import type {
  AuditionClip,
  AuditionReview,
  AuditionRoleStatus,
  AuditionScriptDetail,
  AuditionScriptKind,
  AuditionWorkspaceSnapshot,
  CompiledPronunciationPlan,
  ModelPackageManifest,
  PronunciationDictionary,
  PronunciationEntry,
  SpeechPreviewRequest,
  SpeechProviderRequest,
  SpeechRuntimeInstance,
  TextNormalizationPlan
} from "@cinematic-story-studio/contracts";

import type {
  Phase3bAuditionEvidence,
  Phase3bCacheHitEvidence,
  Phase3bGateDecisionEvidence,
  Phase3bModelEvidence,
  Phase3bPronunciationEvidence,
  Phase3bProviderEvidence,
  Phase3bRuntimeExitEvidence,
  Phase3bRuntimeEvidence,
  Phase3bTargetedInvalidationEvidence
} from "../../src/verification/phase3b-packaged-e2e-evidence";

const fixtureProviderId = "deterministic-pcm-wav-fixture";
const fixturePackageId = "deterministic-pcm-wav-fixture-package";
const realProviderId = "kokoro-local-onnx";
const initialPronunciationText = "Harbor";
const initialPronunciationValue = "HAR-bor";
const supersedingPronunciationValue = "HAR-bur";
const scripts = {
  narrator: "Harbor lanterns glowed beside the quiet water.",
  character1: "A lantern glowed beside the quiet water.",
  character2: "The watch changed beneath a copper moon."
} as const;

export interface Phase3bWorkflowEvidence {
  readonly projectId: string;
  readonly runtime: Phase3bRuntimeEvidence;
  readonly liveRuntimeInstance: SpeechRuntimeInstance;
  readonly fixtureProvider: Phase3bProviderEvidence;
  readonly realProviderAdapter: Phase3bProviderEvidence;
  readonly model: Phase3bModelEvidence;
  readonly pronunciation: Phase3bPronunciationEvidence;
  readonly auditions: readonly Phase3bAuditionEvidence[];
  readonly cacheHit: Phase3bCacheHitEvidence;
  readonly targetedInvalidation: Phase3bTargetedInvalidationEvidence;
  readonly gateDecisions: readonly Phase3bGateDecisionEvidence[];
  readonly persistedReviewDecisionIds: readonly string[];
  readonly persistedSessionIds: readonly string[];
  readonly persistedClipIds: readonly string[];
  readonly restoredAudioClipId: string;
  /** Private in-memory proof input. This is deliberately excluded from the
   * public machine-readable result assembled by packaged-persistence.spec.ts. */
  readonly persistedScript: Phase3bPersistedScriptEvidence;
}

interface Phase3bPersistedScriptEvidence {
  readonly text: string;
  readonly script: AuditionScriptDetail;
  readonly normalizationPlan: TextNormalizationPlan;
  readonly pronunciationPlan: CompiledPronunciationPlan;
  readonly generationRequest: SpeechPreviewRequest;
  readonly cacheKey: string;
  readonly artifactSha256: string;
}

export interface Phase3bRestartEvidence {
  readonly runtimeProfilePersisted: true;
  readonly modelVerificationPersisted: true;
  readonly pronunciationDictionaryPersisted: true;
  readonly pronunciationDecisionsPersisted: true;
  readonly auditionSessionsPersisted: true;
  readonly auditionScriptsPersisted: true;
  readonly auditionClipsPersisted: true;
  readonly cacheRecordsPersisted: true;
  readonly audioQualityRecordsPersisted: true;
  readonly auditionDecisionsPersisted: true;
  readonly voiceReadinessDecisionPersisted: true;
  readonly authenticatedRestoredAudioLoaded: true;
  readonly priorLaunchRuntimeExit: Phase3bRuntimeExitEvidence;
  readonly liveRuntimeInstance: SpeechRuntimeInstance;
}

export async function runPhase3bGovernanceWorkflow(
  page: Page
): Promise<Phase3bWorkflowEvidence> {
  await page.getByRole("button", { name: "Auditions", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Auditions & Pronunciation" })
  ).toBeVisible({ timeout: 30_000 });

  const projectId = await activeProjectId(page);
  let workspace = await readWorkspace(page, projectId);
  if (
    workspace.prerequisites.length === 0 ||
    workspace.prerequisites.some((item) => !item.current) ||
    workspace.approvedCastSnapshot === null
  ) {
    throw new Error("The Phase 0 through Phase 3A audition prerequisites were not current.");
  }

  const packagePage = await page.evaluate(async ({ projectId: id }) => {
    const result = await window.cinematicStory.auditions.listModelPackages({
      projectId: id,
      limit: 50
    });
    if (!result.ok) throw new Error(`Model package list failed: ${result.error.code}`);
    return result.value;
  }, { projectId });
  const fixturePackage = packagePage.items.find(
    (item) => item.manifest.modelPackageId === fixturePackageId
  );
  if (fixturePackage === undefined) {
    throw new Error("The deterministic fixture model package was unavailable.");
  }
  const verified = await performModelAction(
    page,
    projectId,
    fixturePackage.manifest,
    null,
    "verify",
    "phase3b-e2e-fixture-verify"
  );
  if (verified.verification?.status !== "verified") {
    throw new Error("The deterministic fixture model verification did not succeed.");
  }
  const activated = await performModelAction(
    page,
    projectId,
    fixturePackage.manifest,
    verified.installation.installationRevision,
    "activate",
    "phase3b-e2e-fixture-activate"
  );
  if (!activated.installation.active || activated.verification?.status !== "verified") {
    throw new Error("The deterministic fixture model was not active and verified.");
  }

  workspace = await readWorkspace(page, projectId);
  const dictionary = requiredDictionary(workspace.currentDictionary);
  const initialPending = await appendPronunciation(page, projectId, dictionary, {
    pronunciation: initialPronunciationValue,
    supersedesEntryId: null,
    idempotencyKey: "phase3b-e2e-pronunciation-initial"
  });
  const initialApproved = await decidePronunciation(
    page,
    projectId,
    initialPending.entry,
    initialPending.dictionary,
    "phase3b-e2e-pronunciation-initial-approve"
  );
  if (initialApproved.entry.verificationState !== "approved") {
    throw new Error("The initial pronunciation decision was not approved.");
  }

  workspace = await readWorkspace(page, projectId);
  const selectedRoles = selectThreeRoles(workspace.roles.items);
  const roleScripts = new Map<string, string>([
    [selectedRoles[0].roleId, scripts.narrator],
    [selectedRoles[1].roleId, scripts.character1],
    [selectedRoles[2].roleId, scripts.character2]
  ]);
  const initialAuditions: Phase3bAuditionEvidence[] = [];
  const privateCacheKeys = new Map<string, string>();
  const sessionIds: string[] = [];
  const clipIds: string[] = [];
  for (const role of selectedRoles) {
    const session = await createSession(page, projectId, role);
    sessionIds.push(session.auditionSessionId);
    if (role.roleType === "narrator") {
      const reviewRequiredPlan = await previewUnsupportedNormalization(
        page,
        projectId,
        session.auditionSessionId,
        session.revision
      );
      if (
        !reviewRequiredPlan.unsupportedCharacterCodePoints.includes("U+2603") ||
        !reviewRequiredPlan.warnings.includes("NORMALIZATION_REVIEW_REQUIRED")
      ) {
        throw new Error(
          "The real service did not surface the provider-aware U+2603 normalization review warning."
        );
      }
      const previewOnlyRole = (await readWorkspace(page, projectId)).roles.items.find(
        (item) => item.roleId === role.roleId
      );
      if (previewOnlyRole?.generationRequest !== null) {
        throw new Error(
          "A review-required normalization preview was silently made eligible for synthesis."
        );
      }
    }
    const scriptKind: AuditionScriptKind =
      role.roleType === "narrator"
        ? "pronunciation_test"
        : "standardized_synthetic";
    const createdScript = await createScript(
      page,
      projectId,
      session.auditionSessionId,
      session.revision,
      roleScripts.get(role.roleId) ?? scripts.character2,
      `phase3b-e2e-script-${role.roleId}`,
      scriptKind
    );
    const generated = await generateCurrentRole(page, projectId, role.roleId);
    if (
      createdScript.script.kind !== scriptKind ||
      generated.clip.auditionScriptId !== createdScript.script.auditionScriptId ||
      generated.auditionScriptId !== createdScript.script.auditionScriptId
    ) {
      throw new Error("The generated audition did not use the exact created script.");
    }
    if (
      role.roleType === "narrator" &&
      (
        createdScript.script.kind !== "pronunciation_test" ||
        !createdScript.pronunciationPlan.appliedEntries.some(
          (entry) => entry.entryId === initialApproved.entry.entryId
        ) ||
        !createdScript.pronunciationPlan.dependencyEntryRevisions.some(
          (entry) => entry.entryId === initialApproved.entry.entryId
        )
      )
    ) {
      throw new Error(
        "The generated pronunciation-test script did not apply the approved Harbor entry."
      );
    }
    initialAuditions.push(
      toAuditionEvidence(role, generated.clip, generated.providerRequest)
    );
    privateCacheKeys.set(role.roleId, generated.clip.cacheKey);
    clipIds.push(generated.clip.auditionClipId);
    await assertAuthenticatedAudio(page, projectId, generated.clip);
  }

  const narratorRole = selectedRoles[0];
  const narratorOriginal = initialAuditions[0];
  const narratorOriginalCacheKey = privateCacheKeys.get(narratorRole.roleId);
  if (narratorOriginal === undefined || narratorOriginalCacheKey === undefined) {
    throw new Error("Narrator evidence was unavailable.");
  }
  const repeatedSession = await createSession(page, projectId, narratorRole);
  sessionIds.push(repeatedSession.auditionSessionId);
  const repeatedScript = await createScript(
    page,
    projectId,
    repeatedSession.auditionSessionId,
    repeatedSession.revision,
    scripts.narrator,
    "phase3b-e2e-script-narrator-cache-hit",
    "pronunciation_test"
  );
  const repeated = await generateCurrentRole(page, projectId, narratorRole.roleId);
  await assertAuthenticatedAudio(page, projectId, repeated.clip);
  const repeatedAudition = toAuditionEvidence(
    narratorRole,
    repeated.clip,
    repeated.providerRequest
  );
  clipIds.push(repeated.clip.auditionClipId);
  if (
    repeated.clip.cacheStatus !== "verified_hit" ||
    repeated.clip.auditionScriptId !== repeatedScript.script.auditionScriptId ||
    repeated.auditionScriptId !== repeatedScript.script.auditionScriptId ||
    repeated.clip.cacheKey !== narratorOriginalCacheKey ||
    repeated.clip.audioArtifact.sha256 !== narratorOriginal.audio.sha256 ||
    repeatedAudition.executionClassification !== "verified_cache_lookup" ||
    repeatedAudition.providerDispatchCount !== 0 ||
    repeatedAudition.runtimeInstanceId !== null ||
    repeatedAudition.sourceProviderRequestId !== narratorOriginal.providerRequestId ||
    narratorOriginal.executionClassification !== "provider_execution" ||
    narratorOriginal.providerDispatchCount !== 1 ||
    narratorOriginal.runtimeInstanceId === null ||
    narratorOriginal.sourceProviderRequestId !== null
  ) {
    throw new Error(
      "The repeated synthesis inputs did not produce a verified cache hit " +
      `(status=${repeated.clip.cacheStatus}, ` +
      `cacheKeyMatched=${repeated.clip.cacheKey === narratorOriginalCacheKey}, ` +
      `artifactMatched=${repeated.clip.audioArtifact.sha256 === narratorOriginal.audio.sha256}).`
    );
  }
  const cacheHit: Phase3bCacheHitEvidence = {
    originalClipId: narratorOriginal.auditionClipId,
    repeatedClipId: repeated.clip.auditionClipId,
    originalRequestFingerprint: narratorOriginal.requestFingerprint,
    repeatedRequestFingerprint: repeated.requestFingerprint,
    originalCacheKey: narratorOriginal.cacheKey,
    repeatedCacheKey: repeated.clip.cacheKey,
    artifactSha256: narratorOriginal.audio.sha256,
    repeatedArtifactSha256: repeated.clip.audioArtifact.sha256,
    repeatedCacheStatus: "verified_hit",
    identicalCacheKeyInputsProven: true,
    lookupOnlyNoProviderExecutionProven: true
  };

  const beforeSupersession = initialApproved.dictionary;
  const initialGateDecisions = await approveAllGates(
    page,
    projectId,
    selectedRoles,
    "initial"
  );
  workspace = await readWorkspace(page, projectId);
  const initialReadinessReview = requiredReview(
    workspace.reviews.find(
      (item) => item.gateId === "voice_readiness_review"
    )
  );
  const initialReadinessDecision = initialGateDecisions.find(
    (item) => item.gateId === "voice_readiness_review"
  );
  if (
    initialReadinessDecision === undefined ||
    initialReadinessReview.state !== "approved" ||
    initialReadinessReview.latestDecision?.decisionId !==
      initialReadinessDecision.decisionId ||
    initialReadinessReview.evidence.pronunciationDictionaryFingerprint !==
      beforeSupersession.dictionaryFingerprint
  ) {
    throw new Error(
      "The initial voice-readiness approval was not bound to the pre-supersession dictionary."
    );
  }

  const supersedingPending = await appendPronunciation(
    page,
    projectId,
    beforeSupersession,
    {
      pronunciation: supersedingPronunciationValue,
      supersedesEntryId: initialApproved.entry.entryId,
      idempotencyKey: "phase3b-e2e-pronunciation-superseding"
    }
  );
  const supersedingApproved = await decidePronunciation(
    page,
    projectId,
    supersedingPending.entry,
    supersedingPending.dictionary,
    "phase3b-e2e-pronunciation-superseding-approve"
  );
  const narratorClipIds = [
    narratorOriginal.auditionClipId,
    repeated.clip.auditionClipId
  ];
  const missingNarratorInvalidations = narratorClipIds.filter(
    (id) => !supersedingApproved.invalidatedClipIds.includes(id)
  );
  if (
    supersedingApproved.entry.verificationState !== "approved" ||
    missingNarratorInvalidations.length !== 0 ||
    supersedingApproved.invalidatedClipIdsTruncated ||
    supersedingApproved.invalidatedClipCount !==
      supersedingApproved.invalidatedClipIds.length
  ) {
    throw new Error(
      "The pronunciation supersession did not target the narrator evidence " +
      `(entryState=${supersedingApproved.entry.verificationState}, ` +
      `invalidatedCount=${supersedingApproved.invalidatedClipIds.length}, ` +
      `missingExpectedCount=${missingNarratorInvalidations.length}).`
    );
  }
  const characterClipIds = initialAuditions.slice(1).map((item) => item.auditionClipId);
  if (
    characterClipIds.some((id) => !supersedingApproved.preservedClipIds.includes(id)) ||
    supersedingApproved.preservedClipIdsTruncated ||
    supersedingApproved.preservedClipCount !==
      supersedingApproved.preservedClipIds.length
  ) {
    throw new Error("Unrelated character audition clips were not preserved.");
  }

  workspace = await readWorkspace(page, projectId);
  const pronunciationReview = workspace.reviews.find(
    (item) => item.gateId === "pronunciation_review"
  );
  if (
    pronunciationReview === undefined ||
    pronunciationReview.evidence.pronunciationDictionaryFingerprint !==
      supersedingApproved.dictionary.dictionaryFingerprint ||
    (pronunciationReview.state !== "blocked" &&
      pronunciationReview.state !== "pending" &&
      pronunciationReview.state !== "invalidated") ||
    pronunciationReview.latestDecision?.decision === "approved"
  ) {
    throw new Error(
      "The current pronunciation review did not bind the superseding dictionary " +
      `(present=${pronunciationReview !== undefined}, ` +
      `state=${pronunciationReview?.state ?? "missing"}, ` +
      `dictionaryFingerprintMatched=${
        pronunciationReview?.evidence.pronunciationDictionaryFingerprint ===
        supersedingApproved.dictionary.dictionaryFingerprint
      }, latestDecision=${pronunciationReview?.latestDecision?.decision ?? "none"}).`
    );
  }
  const invalidatedReadinessReview = workspace.reviews.find(
    (item) => item.gateId === "voice_readiness_review"
  );
  if (
    invalidatedReadinessReview === undefined ||
    invalidatedReadinessReview.state !== "invalidated" ||
    invalidatedReadinessReview.latestDecision?.decision !== "invalidated" ||
    invalidatedReadinessReview.latestDecision.supersedesDecisionId !==
      initialReadinessDecision.decisionId ||
    invalidatedReadinessReview.evidence.pronunciationDictionaryFingerprint !==
      beforeSupersession.dictionaryFingerprint ||
    invalidatedReadinessReview.evidence.pronunciationDictionaryFingerprint ===
      supersedingApproved.dictionary.dictionaryFingerprint
  ) {
    throw new Error(
      "The prior voice-readiness review did not retain and invalidate its exact old evidence " +
      `(present=${invalidatedReadinessReview !== undefined}, ` +
      `state=${invalidatedReadinessReview?.state ?? "missing"}, ` +
      `dictionaryWasPrior=${
        invalidatedReadinessReview?.evidence.pronunciationDictionaryFingerprint ===
        beforeSupersession.dictionaryFingerprint
      }, latestDecision=${invalidatedReadinessReview?.latestDecision?.decision ?? "none"}).`
    );
  }
  const persistedInvalidatedGateStates = [
    {
      gateId: "pronunciation_review" as const,
      reviewId: pronunciationReview.reviewId,
      state: pronunciationReview.state,
      evidenceFingerprint: pronunciationReview.evidence.evidenceFingerprint
    },
    {
      gateId: "voice_readiness_review" as const,
      reviewId: invalidatedReadinessReview.reviewId,
      state: invalidatedReadinessReview.state,
      evidenceFingerprint: invalidatedReadinessReview.evidence.evidenceFingerprint
    }
  ];
  const refreshedNarrator = workspace.roles.items.find(
    (role) => role.roleId === narratorRole.roleId
  );
  if (refreshedNarrator === undefined) throw new Error("The narrator role was unavailable after supersession.");
  const regeneratedSession = await createSession(page, projectId, refreshedNarrator);
  sessionIds.push(regeneratedSession.auditionSessionId);
  const regeneratedScript = await createScript(
    page,
    projectId,
    regeneratedSession.auditionSessionId,
    regeneratedSession.revision,
    scripts.narrator,
    "phase3b-e2e-script-narrator-regenerated",
    "pronunciation_test"
  );
  const regenerated = await generateCurrentRole(page, projectId, narratorRole.roleId);
  clipIds.push(regenerated.clip.auditionClipId);
  await assertAuthenticatedAudio(page, projectId, regenerated.clip);
  const regeneratedAudition = toAuditionEvidence(
    refreshedNarrator,
    regenerated.clip,
    regenerated.providerRequest
  );
  if (
    regenerated.requestFingerprint === narratorOriginal.requestFingerprint ||
    regenerated.clip.auditionScriptId !== regeneratedScript.script.auditionScriptId ||
    regenerated.auditionScriptId !== regeneratedScript.script.auditionScriptId ||
    regenerated.clip.cacheKey === narratorOriginal.cacheKey ||
    regenerated.clip.audioArtifact.sha256 === narratorOriginal.audio.sha256
  ) {
    throw new Error("The changed pronunciation did not produce new request and artifact identities.");
  }
  const persistedScript: Phase3bPersistedScriptEvidence = {
    text: scripts.narrator,
    script: regeneratedScript.script,
    normalizationPlan: regeneratedScript.normalizationPlan,
    pronunciationPlan: regeneratedScript.pronunciationPlan,
    generationRequest: regenerated.generationRequest,
    cacheKey: regenerated.clip.cacheKey,
    artifactSha256: regenerated.clip.audioArtifact.sha256
  };
  assertPersistedScriptFixture(persistedScript);

  const gateDecisions = await approveAllGates(
    page,
    projectId,
    selectedRoles,
    "regenerated"
  );
  workspace = await readWorkspace(page, projectId);
  const finalReadinessReview = requiredReview(
    workspace.reviews.find(
      (item) => item.gateId === "voice_readiness_review"
    )
  );
  if (
    finalReadinessReview.state !== "approved" ||
    finalReadinessReview.evidence.pronunciationDictionaryFingerprint !==
      supersedingApproved.dictionary.dictionaryFingerprint ||
    workspace.voiceReadinessSnapshot?.snapshotFingerprint !==
      finalReadinessReview.evidence.evidenceFingerprint
  ) {
    throw new Error(
      "The regenerated voice-readiness approval did not bind the superseding dictionary."
    );
  }
  const persistedReviewDecisionIds = [
    ...(await readCompleteReviewDecisionHistory(
    page,
    projectId,
    [...initialGateDecisions, ...gateDecisions]
    ))
  ];
  const liveRuntimeInstance = requireLiveRuntime(workspace.runtimeInstances);
  const runtimeProfile = workspace.runtimeProfiles.find(
    (profile) => profile.runtimeProfileId === liveRuntimeInstance.runtimeProfileId
  );
  if (runtimeProfile === undefined || liveRuntimeInstance.observedNetworkRequestCount !== null) {
    throw new Error("The runtime profile or nullable network observation was invalid.");
  }
  const fixtureProvider = workspace.providers.find(
    (provider) => provider.providerId === fixtureProviderId
  );
  const realProvider = workspace.providers.find(
    (provider) => provider.providerId === realProviderId
  );
  if (
    fixtureProvider?.providerClass !== "deterministic_fixture" ||
    realProvider?.providerClass !== "real_local"
  ) {
    throw new Error("The fixture and real provider classifications were unavailable.");
  }

  return {
    projectId,
    runtime: {
      profileId: runtimeProfile.runtimeProfileId,
      profileFingerprint: runtimeProfile.profileFingerprint,
      protocolVersion: "1.0.0",
      runtimeInstanceIds: unique(workspace.runtimeInstances.map((item) => item.runtimeInstanceId)),
      networkPolicy: "python_socket_api_denied",
      observedNetworkRequestCount: null,
      externalNetworkObservation: {
        method: "owned_pid_tcp_endpoint_inventory",
        ownedPidsOnly: true,
        observedNonLoopbackEndpointCount: 0
      }
    },
    liveRuntimeInstance,
    fixtureProvider: {
      providerId: fixtureProvider.providerId,
      providerVersion: fixtureProvider.providerVersion
    },
    realProviderAdapter: {
      providerId: realProvider.providerId,
      providerVersion: realProvider.providerVersion
    },
    model: {
      modelPackageId: fixturePackage.manifest.modelPackageId,
      manifestVersion: fixturePackage.manifest.manifestVersion,
      modelPackageFingerprint: fixturePackage.manifest.manifestFingerprint,
      installationRevision: activated.installation.installationRevision,
      verificationId: activated.verification.verificationId,
      verificationFingerprint: activated.verification.verificationFingerprint,
      verified: true,
      active: true
    },
    pronunciation: {
      dictionaryId: beforeSupersession.dictionaryId,
      initialRevision: beforeSupersession.revision,
      initialFingerprint: beforeSupersession.dictionaryFingerprint,
      initialEntryId: initialApproved.entry.entryId,
      initialEntryFingerprint: initialApproved.entry.entryFingerprint,
      initialDecision: "approved",
      supersedingEntryId: supersedingApproved.entry.entryId,
      supersedingEntryFingerprint: supersedingApproved.entry.entryFingerprint,
      supersedesEntryId: initialApproved.entry.entryId,
      supersedingDecision: "approved",
      finalRevision: supersedingApproved.dictionary.revision,
      finalFingerprint: supersedingApproved.dictionary.dictionaryFingerprint
    },
    auditions: [...initialAuditions, repeatedAudition, regeneratedAudition],
    cacheHit,
    targetedInvalidation: {
      supersededEntryId: initialApproved.entry.entryId,
      supersedingEntryId: supersedingApproved.entry.entryId,
      beforeDictionaryFingerprint: beforeSupersession.dictionaryFingerprint,
      afterDictionaryFingerprint: supersedingApproved.dictionary.dictionaryFingerprint,
      impactedRoleId: narratorRole.roleId,
      priorRequestFingerprint: narratorOriginal.requestFingerprint,
      regeneratedRequestFingerprint: regenerated.requestFingerprint,
      priorCacheKey: narratorOriginal.cacheKey,
      regeneratedCacheKey: regenerated.clip.cacheKey,
      priorArtifactSha256: narratorOriginal.audio.sha256,
      regeneratedArtifactSha256: regenerated.clip.audioArtifact.sha256,
      invalidatedClipIds: supersedingApproved.invalidatedClipIds,
      preservedClipIds: supersedingApproved.preservedClipIds,
      persistedInvalidatedGateStates,
      targetedOnly: true
    },
    gateDecisions,
    persistedReviewDecisionIds,
    persistedSessionIds: unique(sessionIds),
    persistedClipIds: unique(clipIds),
    restoredAudioClipId: initialAuditions[1]?.auditionClipId ?? narratorOriginal.auditionClipId,
    persistedScript
  };
}

export async function provePhase3bRestartPersistence(
  page: Page,
  expected: Phase3bWorkflowEvidence
): Promise<Phase3bRestartEvidence> {
  await page.getByRole("button", { name: "Auditions", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Auditions & Pronunciation" })
  ).toBeVisible({ timeout: 30_000 });
  const workspace = await readWorkspace(page, expected.projectId);
  const priorLaunchRuntime = workspace.runtimeInstances.find(
    (item) =>
      item.runtimeInstanceId === expected.liveRuntimeInstance.runtimeInstanceId
  );
  if (priorLaunchRuntime === undefined) {
    throw new Error("The prior launch runtime exit record did not persist.");
  }
  const priorLaunchRuntimeExit = requireGracefulRuntimeExit(
    priorLaunchRuntime,
    expected.liveRuntimeInstance
  );
  const sessions = await listSessions(page, expected.projectId);
  const clips = await listClips(page, expected.projectId);
  const pronunciations = await listPronunciations(
    page,
    expected.projectId,
    requiredDictionary(workspace.currentDictionary)
  );
  const restoredAudio = clips.find(
    (clip) => clip.auditionClipId === expected.restoredAudioClipId
  );
  if (restoredAudio === undefined) throw new Error("The restored audio clip was unavailable.");
  await assertAuthenticatedAudio(page, expected.projectId, restoredAudio);

  const dictionary = requiredDictionary(workspace.currentDictionary);
  const allSessionIds = new Set(sessions.map((item) => item.auditionSessionId));
  const allClipIds = new Set(clips.map((item) => item.auditionClipId));
  const allDecisionIds = await readCompleteReviewDecisionHistory(
    page,
    expected.projectId,
    expected.gateDecisions
  );
  if (
    dictionary.revision !== expected.pronunciation.finalRevision ||
    dictionary.dictionaryFingerprint !== expected.pronunciation.finalFingerprint ||
    !pronunciations.some(
      (entry) =>
        entry.entryId === expected.pronunciation.supersedingEntryId &&
        entry.entryFingerprint === expected.pronunciation.supersedingEntryFingerprint &&
        entry.verificationState === "approved"
    ) ||
    expected.persistedSessionIds.some((id) => !allSessionIds.has(id)) ||
    expected.persistedClipIds.some((id) => !allClipIds.has(id)) ||
    expected.persistedReviewDecisionIds.some((id) => !allDecisionIds.has(id)) ||
    expected.gateDecisions.some((decision) => !allDecisionIds.has(decision.decisionId)) ||
    workspace.voiceReadinessSnapshot?.reviewEligible !== true
  ) {
    throw new Error("The durable Phase 3B evidence did not restore exactly.");
  }
  const installation = workspace.modelInstallations.find(
    (item) => item.modelPackageId === expected.model.modelPackageId
  );
  const verification = workspace.modelVerifications.find(
    (item) => item.verificationId === expected.model.verificationId
  );
  if (
    installation?.active !== true ||
    verification?.verificationFingerprint !== expected.model.verificationFingerprint
  ) {
    throw new Error("The model activation or verification did not persist.");
  }

  const role = workspace.roles.items.find(
    (item) => item.roleId === expected.targetedInvalidation.impactedRoleId
  );
  if (role?.generationRequest === null || role === undefined) {
    throw new Error("A restored request was unavailable for cache persistence proof.");
  }
  assertRestoredScriptBinding(role.generationRequest, expected.persistedScript);
  const replayPreview = withFreshIdempotencyKey(
    role.generationRequest,
    `phase3b-e2e-restart-cache-hit-${Date.now()}`
  );
  const cacheReplay = await generateExactPreview(
    page,
    expected.projectId,
    replayPreview
  );
  if (
    cacheReplay.clip.cacheStatus !== "verified_hit" ||
    cacheReplay.clip.auditionSessionId !==
      expected.persistedScript.script.auditionSessionId ||
    cacheReplay.clip.auditionScriptId !==
      expected.persistedScript.script.auditionScriptId ||
    cacheReplay.auditionScriptId !==
      expected.persistedScript.script.auditionScriptId ||
    cacheReplay.clip.cacheKey !== expected.persistedScript.cacheKey ||
    cacheReplay.clip.audioArtifact.sha256 !==
      expected.persistedScript.artifactSha256 ||
    cacheReplay.clip.voiceRuntimeBindingId !==
      expected.persistedScript.generationRequest.evidence.voiceRuntimeBindingId ||
    cacheReplay.clip.voiceRuntimeBindingFingerprint !==
      expected.persistedScript.generationRequest.evidence.voiceRuntimeBindingFingerprint ||
    cacheReplay.clip.providerVoiceId !==
      expected.persistedScript.generationRequest.evidence.providerVoiceId
  ) {
    throw new Error(
      "The exact persisted audition script and private cache did not verify on reuse."
    );
  }
  const refreshed = await readWorkspace(page, expected.projectId);
  const liveRuntimeInstance = requireLiveRuntime(refreshed.runtimeInstances);
  return {
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
    priorLaunchRuntimeExit,
    liveRuntimeInstance
  };
}

function requireGracefulRuntimeExit(
  value: SpeechRuntimeInstance,
  expectedLiveInstance: SpeechRuntimeInstance
): Phase3bRuntimeExitEvidence {
  if (
    value.runtimeInstanceId !== expectedLiveInstance.runtimeInstanceId ||
    value.workerPid !== expectedLiveInstance.workerPid ||
    value.parentPid !== expectedLiveInstance.parentPid ||
    value.state !== "stopped" ||
    value.stoppedAt === null ||
    value.stopReasonCode !== "clean" ||
    value.handshakeAuthenticated !== true ||
    value.shutdownAcknowledged !== true ||
    value.gracefulShutdownConfirmed !== true ||
    value.exitCode !== 0 ||
    value.terminatedByParent !== false ||
    value.ownershipConfirmed !== true ||
    value.confirmedExited !== true ||
    value.ownedProcessesConfirmedExited !== true ||
    value.jobObjectAssigned !== true ||
    value.deniedNetworkAttemptCount !== 0
  ) {
    throw new Error(
      "The prior launch runtime lacked authenticated graceful-shutdown evidence."
    );
  }
  return {
    runtimeInstanceId: value.runtimeInstanceId,
    workerPid: value.workerPid,
    parentPid: value.parentPid,
    state: "stopped",
    stoppedAt: value.stoppedAt,
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

async function activeProjectId(page: Page): Promise<string> {
  return page.evaluate(async () => {
    const result = await window.cinematicStory.projects.restoreRecent();
    if (!result.ok || result.value === null) throw new Error("The active project was unavailable.");
    return result.value.project.projectId;
  });
}

async function readWorkspace(page: Page, projectId: string): Promise<AuditionWorkspaceSnapshot> {
  return page.evaluate(async (id) => {
    const result = await window.cinematicStory.auditions.getWorkspace({
      projectId: id,
      roleLimit: 50
    });
    if (!result.ok) {
      throw new Error(
        `Audition workspace failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value.workspace;
  }, projectId);
}

async function performModelAction(
  page: Page,
  projectId: string,
  manifest: ModelPackageManifest,
  expectedInstallationRevision: number | null,
  action: "verify" | "activate",
  idempotencyKey: string
) {
  return page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.performModelPackageAction(input);
    if (!result.ok) throw new Error(`Model action failed: ${result.error.code}`);
    return result.value;
  }, {
    projectId,
    modelPackageId: manifest.modelPackageId,
    expectedManifestFingerprint: manifest.manifestFingerprint,
    expectedInstallationRevision,
    action,
    reason: `Run the governed deterministic fixture ${action} operation.`,
    idempotencyKey
  });
}

async function appendPronunciation(
  page: Page,
  projectId: string,
  dictionary: PronunciationDictionary,
  input: {
    readonly pronunciation: string;
    readonly supersedesEntryId: string | null;
    readonly idempotencyKey: string;
  }
) {
  return page.evaluate(async (request) => {
    const result = await window.cinematicStory.auditions.appendPronunciation(request);
    if (!result.ok) throw new Error(`Pronunciation append failed: ${result.error.code}`);
    return result.value;
  }, {
    projectId,
    expectedDictionaryRevision: dictionary.revision,
    expectedDictionaryFingerprint: dictionary.dictionaryFingerprint,
    writtenForm: initialPronunciationText,
    language: "en",
    locale: null,
    scope: "project" as const,
    scopeId: null,
    representation: "provider_neutral" as const,
    pronunciation: input.pronunciation,
    ipa: null,
    providerId: null,
    providerCompiledValue: null,
    caseSensitive: false,
    matchRule: "whole_word" as const,
    priority: 100,
    reason: "Review repository-owned synthetic pronunciation evidence.",
    supersedesEntryId: input.supersedesEntryId,
    idempotencyKey: input.idempotencyKey
  });
}

async function decidePronunciation(
  page: Page,
  projectId: string,
  entry: PronunciationEntry,
  dictionary: PronunciationDictionary,
  idempotencyKey: string
) {
  return page.evaluate(async (request) => {
    const result = await window.cinematicStory.auditions.decidePronunciation(request);
    if (!result.ok) throw new Error(`Pronunciation decision failed: ${result.error.code}`);
    return result.value;
  }, {
    projectId,
    entryId: entry.entryId,
    expectedEntryRevision: entry.revision,
    expectedEntryFingerprint: entry.entryFingerprint,
    expectedDictionaryRevision: dictionary.revision,
    expectedDictionaryFingerprint: dictionary.dictionaryFingerprint,
    decision: "approve" as const,
    rationale: "Approve the reviewed repository-owned synthetic pronunciation.",
    idempotencyKey
  });
}

function selectThreeRoles(roles: readonly AuditionRoleStatus[]): readonly [AuditionRoleStatus, AuditionRoleStatus, AuditionRoleStatus] {
  const narrator = roles.find((role) => role.roleType === "narrator");
  const characters = roles.filter((role) => role.roleType === "character").slice(0, 2);
  if (
    narrator?.sessionEvidence === null ||
    narrator === undefined ||
    characters.length !== 2 ||
    characters.some((role) => role.sessionEvidence === null)
  ) {
    throw new Error("One narrator and two character session bindings were not available.");
  }
  return [narrator, characters[0], characters[1]];
}

async function createSession(page: Page, projectId: string, role: AuditionRoleStatus) {
  if (role.sessionEvidence === null) throw new Error("The server-issued session evidence was missing.");
  return page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.createSession(input);
    if (!result.ok) throw new Error(`Audition session creation failed: ${result.error.code}`);
    return result.value.session;
  }, {
    projectId,
    roleId: role.roleId,
    evidence: role.sessionEvidence,
    idempotencyKey: `phase3b-e2e-session-${role.roleId}-${Date.now()}`
  });
}

async function createScript(
  page: Page,
  projectId: string,
  auditionSessionId: string,
  expectedSessionRevision: number,
  text: string,
  idempotencyKey: string,
  kind: AuditionScriptKind = "standardized_synthetic"
) {
  const sourceTextSha256 = sha256(text);
  return page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.createScript(input);
    if (!result.ok) throw new Error(`Audition script creation failed: ${result.error.code}`);
    return result.value;
  }, {
    projectId,
    auditionSessionId,
    expectedSessionRevision,
    kind,
    text,
    sourceDocumentId: null,
    sourceRevision: null,
    sourceSpan: null,
    sourceTextSha256,
    acceptedOptionalNormalizationIds: [],
    idempotencyKey
  });
}

async function previewUnsupportedNormalization(
  page: Page,
  projectId: string,
  auditionSessionId: string,
  expectedSessionRevision: number
) {
  const text = "Harbor \u2603";
  return page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.previewNormalization(
      input
    );
    if (!result.ok) {
      throw new Error(
        `Audition normalization preview failed: ${result.error.code}`
      );
    }
    return result.value.plan;
  }, {
    projectId,
    auditionSessionId,
    expectedSessionRevision,
    text,
    sourceTextSha256: sha256(text),
    acceptedOptionalNormalizationIds: []
  });
}

async function generateCurrentRole(page: Page, projectId: string, roleId: string) {
  const workspace = await readWorkspace(page, projectId);
  const role = workspace.roles.items.find((item) => item.roleId === roleId);
  if (role?.generationRequest === null || role === undefined) {
    throw new Error("The server-issued generation request was unavailable.");
  }
  const generated = await generateExactPreview(
    page,
    projectId,
    role.generationRequest
  );
  return {
    ...generated,
    generationRequest: role.generationRequest
  };
}

async function generateExactPreview(
  page: Page,
  projectId: string,
  preview: SpeechPreviewRequest
) {
  const queued = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.generate(input);
    if (!result.ok) throw new Error(`Audition generation failed: ${result.error.code}`);
    return result.value;
  }, { projectId, preview });
  await waitForAuditionJob(page, queued.jobId);
  const completed = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.generate(input);
    if (!result.ok) {
      throw new Error(
        `Completed audition request replay failed: ${result.error.code}`
      );
    }
    return result.value;
  }, { projectId, preview });
  if (
    completed.jobId !== queued.jobId ||
    completed.session.auditionSessionId !== queued.session.auditionSessionId ||
    completed.providerRequest.providerRequestId !==
      queued.providerRequest.providerRequestId ||
    completed.providerRequest.state !== "succeeded"
  ) {
    throw new Error(
      "The completed provider-request replay did not return the exact terminal request."
    );
  }
  const clips = await listClips(page, projectId, queued.session.auditionSessionId);
  const clip = clips.find(
    (item) => item.providerRequestId === completed.providerRequest.providerRequestId
  );
  if (clip === undefined) throw new Error("The generated audition clip was unavailable.");
  return {
    clip,
    providerRequest: completed.providerRequest,
    requestFingerprint: completed.providerRequest.requestFingerprint,
    auditionScriptId: preview.auditionScriptId
  };
}

async function waitForAuditionJob(page: Page, jobId: string): Promise<void> {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    const state = await page.evaluate(async (id) => {
      const result = await window.cinematicStory.jobs.get(id);
      if (!result.ok) throw new Error(`Audition job read failed: ${result.error.code}: ${result.error.message}`);
      return { state: result.value.job.state, error: result.value.job.error?.code ?? null };
    }, jobId);
    if (state.state === "succeeded") return;
    if (["failed", "cancelled"].includes(state.state)) {
      throw new Error(`Audition job ${state.state}: ${state.error ?? "unknown"}.`);
    }
    await page.waitForTimeout(200);
  }
  throw new Error("The audition generation job timed out.");
}

async function listSessions(page: Page, projectId: string) {
  return page.evaluate(async (id) => {
    const result = await window.cinematicStory.auditions.listSessions({ projectId: id, limit: 200 });
    if (!result.ok) throw new Error(`Audition session list failed: ${result.error.code}`);
    return result.value.items;
  }, projectId);
}

async function listClips(page: Page, projectId: string, auditionSessionId?: string) {
  return page.evaluate(async ({ id, sessionId }) => {
    const result = await window.cinematicStory.auditions.listClips({
      projectId: id,
      limit: 200,
      ...(sessionId === undefined ? {} : { auditionSessionId: sessionId })
    });
    if (!result.ok) throw new Error(`Audition clip list failed: ${result.error.code}: ${result.error.message}`);
    return result.value.items;
  }, { id: projectId, sessionId: auditionSessionId });
}

async function listPronunciations(page: Page, projectId: string, dictionary: PronunciationDictionary) {
  return page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.listPronunciations(input);
    if (!result.ok) throw new Error(`Pronunciation list failed: ${result.error.code}`);
    return result.value.items;
  }, {
    projectId,
    limit: 200,
    expectedDictionaryRevision: dictionary.revision,
    expectedDictionaryFingerprint: dictionary.dictionaryFingerprint
  });
}

async function assertAuthenticatedAudio(page: Page, projectId: string, clip: AuditionClip): Promise<void> {
  const loaded = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.loadAudio(input);
    if (!result.ok) throw new Error(`Authenticated audio load failed: ${result.error.code}`);
    const bytes = new Uint8Array(result.value.bytes);
    return {
      byteSize: bytes.byteLength,
      riff: String.fromCharCode(...bytes.slice(0, 4)),
      wave: String.fromCharCode(...bytes.slice(8, 12)),
      sha256: result.value.sha256
    };
  }, {
    projectId,
    auditionClipId: clip.auditionClipId,
    auditionSessionId: clip.auditionSessionId,
    audioArtifactId: clip.audioArtifact.audioArtifactId,
    expectedClipRevision: clip.revision,
    expectedClipFingerprint: clip.clipFingerprint,
    expectedArtifactSha256: clip.audioArtifact.sha256,
    mediaType: "audio/wav" as const,
    byteSize: clip.audioArtifact.byteSize
  });
  if (
    loaded.byteSize !== clip.audioArtifact.byteSize ||
    loaded.sha256 !== clip.audioArtifact.sha256 ||
    loaded.riff !== "RIFF" ||
    loaded.wave !== "WAVE"
  ) {
    throw new Error("The authenticated WAV bytes failed integrity validation.");
  }
}

function toAuditionEvidence(
  role: AuditionRoleStatus,
  clip: AuditionClip,
  providerRequest: SpeechProviderRequest
): Phase3bAuditionEvidence {
  const execution = providerRequest.provenance.details;
  const cacheStatus = clip.cacheStatus === "verified_hit" ? "verified_hit" : "miss";
  if (
    role.voiceRuntimeBinding === null ||
    role.runtimeBindingStatus !== "compatible" ||
    role.runtimeBindingReasonCode !== null ||
    role.voiceRuntimeBinding.bindingId !== clip.voiceRuntimeBindingId ||
    role.voiceRuntimeBinding.bindingFingerprint !==
      clip.voiceRuntimeBindingFingerprint ||
    role.voiceRuntimeBinding.providerVoiceId !== clip.providerVoiceId ||
    clip.providerClass !== "deterministic_fixture" ||
    clip.audioArtifact.mediaType !== "audio/wav" ||
    clip.audioArtifact.codec !== "pcm_s16le" ||
    clip.audioArtifact.sampleRateHz !== 24_000 ||
    clip.audioArtifact.channels !== 1 ||
    clip.audioArtifact.sampleWidthBytes !== 2 ||
    !clip.audioQuality.validWav ||
    !clip.audioQuality.nonSilent ||
    clip.audioQuality.clippedSampleCount !== 0 ||
    clip.audioQuality.blockingFindingCodes.length !== 0 ||
    providerRequest.state !== "succeeded" ||
    providerRequest.finishedAt === null ||
    providerRequest.providerRequestId !== clip.providerRequestId ||
    providerRequest.auditionSessionId !== clip.auditionSessionId ||
    providerRequest.providerId !== clip.providerId ||
    providerRequest.providerVersion !== clip.providerVersion ||
    providerRequest.modelId !== clip.modelId ||
    providerRequest.modelVersion !== clip.modelVersion ||
    providerRequest.modelPackageFingerprint !== clip.modelPackageFingerprint ||
    providerRequest.runtimeProfileFingerprint !== clip.runtimeProfileFingerprint ||
    providerRequest.voiceRuntimeBindingId !== clip.voiceRuntimeBindingId ||
    providerRequest.voiceRuntimeBindingFingerprint !==
      clip.voiceRuntimeBindingFingerprint ||
    providerRequest.providerVoiceId !== clip.providerVoiceId ||
    providerRequest.castAssignmentId !== clip.castAssignmentId ||
    providerRequest.castAssignmentRevision !== clip.castAssignmentRevision ||
    providerRequest.normalizedTextSha256 !== clip.normalizedTextSha256 ||
    providerRequest.pronunciationPlanFingerprint !==
      clip.pronunciationPlanFingerprint ||
    providerRequest.providerControlFingerprint !== clip.providerControlFingerprint ||
    providerRequest.cacheKey !== clip.cacheKey ||
    (cacheStatus === "verified_hit"
      ? execution.executionClassification !== "verified_cache_lookup" ||
        execution.providerDispatchCount !== 0 ||
        execution.sourceProviderRequestId === providerRequest.providerRequestId ||
        providerRequest.runtimeInstanceId !== null ||
        providerRequest.startedAt === null
      : execution.executionClassification !== "provider_execution" ||
        execution.providerDispatchCount !== 1 ||
        providerRequest.runtimeInstanceId === null ||
        providerRequest.startedAt === null)
  ) {
    throw new Error(
      "The deterministic fixture WAV or provider-execution evidence was invalid."
    );
  }
  return {
    roleId: role.roleId,
    roleType: role.roleType,
    assignmentId: role.assignmentId,
    assignmentRevision: role.assignmentRevision,
    voiceRuntimeBindingId: clip.voiceRuntimeBindingId,
    voiceRuntimeBindingFingerprint: clip.voiceRuntimeBindingFingerprint,
    providerVoiceId: clip.providerVoiceId,
    auditionSessionId: clip.auditionSessionId,
    providerRequestId: clip.providerRequestId,
    requestFingerprint: providerRequest.requestFingerprint,
    executionClassification: execution.executionClassification,
    providerDispatchCount: execution.providerDispatchCount,
    sourceProviderRequestId: execution.sourceProviderRequestId ?? null,
    runtimeInstanceId: providerRequest.runtimeInstanceId,
    normalizedTextSha256: clip.normalizedTextSha256,
    pronunciationPlanFingerprint: clip.pronunciationPlanFingerprint,
    cacheKey: clip.cacheKey,
    cacheStatus,
    auditionClipId: clip.auditionClipId,
    clipFingerprint: clip.clipFingerprint,
    audioArtifactId: clip.audioArtifact.audioArtifactId,
    audio: {
      mediaType: "audio/wav",
      codec: "pcm_s16le",
      sampleRateHz: 24_000,
      channels: 1,
      sampleWidthBytes: 2,
      durationMilliseconds: clip.audioArtifact.durationMilliseconds,
      byteSize: clip.audioArtifact.byteSize,
      sha256: clip.audioArtifact.sha256,
      nonSilencePassed: true,
      clippingPassed: true,
      blockingFindingCount: 0
    },
    authenticatedAudioLoaded: true,
    fixtureEvidenceOnly: true
  };
}

async function approveAllGates(
  page: Page,
  projectId: string,
  roles: readonly AuditionRoleStatus[],
  approvalRound: string
): Promise<readonly Phase3bGateDecisionEvidence[]> {
  const result: Phase3bGateDecisionEvidence[] = [];
  for (const role of roles) {
    const workspace = await readWorkspace(page, projectId);
    const review = workspace.reviews.find(
      (item) => item.gateId === "per_role_audition_review" && item.roleId === role.roleId
    );
    result.push(
      await approveReview(
        page,
        projectId,
        requiredReview(review),
        approvalRound
      )
    );
  }
  for (const gateId of [
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review"
  ] as const) {
    const workspace = await readWorkspace(page, projectId);
    result.push(
      await approveReview(
        page,
        projectId,
        requiredReview(workspace.reviews.find((item) => item.gateId === gateId)),
        approvalRound
      )
    );
  }
  return result;
}

async function approveReview(
  page: Page,
  projectId: string,
  review: AuditionReview,
  approvalRound: string
): Promise<Phase3bGateDecisionEvidence> {
  if (review.state === "blocked") {
    throw new Error(`The ${review.gateId} review was blocked: ${review.blockerCodes.join(",")}.`);
  }
  if (
    review.state === "approved" &&
    review.latestDecision?.decision === "approved" &&
    review.latestDecision.evidenceFingerprint ===
      review.evidence.evidenceFingerprint
  ) {
    return {
      gateId: review.gateId,
      reviewId: review.reviewId,
      decisionId: review.latestDecision.decisionId,
      roleId: review.roleId,
      evidenceFingerprint: review.evidence.evidenceFingerprint,
      decision: "approved",
      immutable: true
    };
  }
  const response = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.decideReview(input);
    if (!result.ok) throw new Error(`Audition review failed: ${result.error.code}`);
    return result.value;
  }, {
    projectId,
    gateId: review.gateId,
    roleId: review.roleId,
    reviewId: review.reviewId,
    expectedReviewRevision: review.revision,
    expectedEvidenceFingerprint: review.evidence.evidenceFingerprint,
    decision: "approve" as const,
    rationale: "Approve deterministic fixture lifecycle evidence after local review.",
    supersedesDecisionId: review.latestDecision?.decisionId ?? null,
    idempotencyKey:
      `phase3b-e2e-review-${approvalRound}-${review.gateId}-${review.roleId ?? "global"}`
  });
  if (response.decision.decision !== "approved" || response.decision.immutable !== true) {
    throw new Error("The audition review decision was not an immutable approval.");
  }
  return {
    gateId: review.gateId,
    reviewId: review.reviewId,
    decisionId: response.decision.decisionId,
    roleId: review.roleId,
    evidenceFingerprint: review.evidence.evidenceFingerprint,
    decision: "approved",
    immutable: true
  };
}

async function readCompleteReviewDecisionHistory(
  page: Page,
  projectId: string,
  expected: readonly Phase3bGateDecisionEvidence[]
): Promise<ReadonlySet<string>> {
  const allDecisionIds = new Set<string>();
  for (const gate of expected) {
    let cursor: string | undefined;
    let total: number | null = null;
    const scopedDecisionIds: string[] = [];
    for (let pageIndex = 0; pageIndex < 100; pageIndex += 1) {
      const result = await page.evaluate(async (input) => {
        const response = await window.cinematicStory.auditions.listReviewDecisions(
          input
        );
        if (!response.ok) {
          throw new Error(
            `Audition review history failed: ${response.error.code}`
          );
        }
        return response.value;
      }, {
        projectId,
        gateId: gate.gateId,
        roleId: gate.roleId,
        limit: 1,
        ...(cursor === undefined ? {} : { cursor })
      });
      total = result.total;
      for (const decision of result.items) {
        scopedDecisionIds.push(decision.decisionId);
        allDecisionIds.add(decision.decisionId);
      }
      if (result.nextCursor === undefined) break;
      cursor = result.nextCursor;
      if (pageIndex === 99) {
        throw new Error("The bounded review-decision history exceeded 100 pages.");
      }
    }
    if (
      total === null ||
      scopedDecisionIds.length !== total ||
      new Set(scopedDecisionIds).size !== scopedDecisionIds.length ||
      !scopedDecisionIds.includes(gate.decisionId)
    ) {
      throw new Error(
        `The complete immutable ${gate.gateId} history did not restore exactly.`
      );
    }
  }
  return allDecisionIds;
}

function requiredDictionary(value: PronunciationDictionary | null): PronunciationDictionary {
  if (value === null) throw new Error("The pronunciation dictionary was unavailable.");
  return value;
}

function assertPersistedScriptFixture(
  value: Phase3bPersistedScriptEvidence
): void {
  const { script, normalizationPlan, pronunciationPlan, generationRequest } = value;
  if (
    value.text !== scripts.narrator ||
    script.text !== value.text ||
    script.kind !== "pronunciation_test" ||
    script.sourceDocumentId !== null ||
    script.sourceRevision !== null ||
    script.sourceSpan !== null ||
    script.sourceAnalysisEntity !== null ||
    script.sourceTextSha256 !== sha256(value.text) ||
    normalizationPlan.originalTextSha256 !== script.sourceTextSha256 ||
    normalizationPlan.normalizedTextSha256 !== script.normalizedTextSha256 ||
    normalizationPlan.normalizationPlanId !== script.normalizationPlanId ||
    pronunciationPlan.pronunciationPlanId !== script.pronunciationPlanId ||
    pronunciationPlan.sourceTextSha256 !== script.normalizedTextSha256 ||
    generationRequest.auditionSessionId !== script.auditionSessionId ||
    generationRequest.auditionScriptId !== script.auditionScriptId ||
    generationRequest.auditionScriptFingerprint !== script.scriptFingerprint ||
    generationRequest.normalizedTextSha256 !== script.normalizedTextSha256 ||
    generationRequest.normalizationPlanFingerprint !==
      normalizationPlan.planFingerprint ||
    generationRequest.pronunciationPlanFingerprint !==
      pronunciationPlan.planFingerprint
  ) {
    throw new Error(
      "The pre-restart synthetic script, plans, and governed request were not exact."
    );
  }
}

function assertRestoredScriptBinding(
  restored: SpeechPreviewRequest,
  expected: Phase3bPersistedScriptEvidence
): void {
  assertPersistedScriptFixture(expected);
  if (
    restored.auditionSessionId !== expected.script.auditionSessionId ||
    restored.auditionScriptId !== expected.script.auditionScriptId ||
    restored.auditionScriptFingerprint !== expected.script.scriptFingerprint ||
    canonicalJson(persistedPreviewProjection(restored)) !==
      canonicalJson(persistedPreviewProjection(expected.generationRequest))
  ) {
    throw new Error(
      "The restored generation request did not bind the exact pre-restart script, plans, and evidence."
    );
  }
}

function persistedPreviewProjection(value: SpeechPreviewRequest): unknown {
  return omitObjectKeys(value, [
    "auditionSessionRevision",
    "idempotencyKey",
    "requestFingerprint"
  ] as const);
}

function withFreshIdempotencyKey(
  value: SpeechPreviewRequest,
  idempotencyKey: string
): SpeechPreviewRequest {
  const priorMaterial = omitObjectKeys(value, ["requestFingerprint"] as const);
  const material = { ...priorMaterial, idempotencyKey };
  return {
    ...material,
    requestFingerprint: sha256(canonicalJson(material))
  };
}

function omitObjectKeys<
  T extends object,
  K extends readonly (keyof T)[]
>(value: T, keys: K): Omit<T, K[number]> {
  const excluded = new Set<PropertyKey>(keys);
  return Object.fromEntries(
    Object.entries(value).filter(([key]) => !excluded.has(key))
  ) as Omit<T, K[number]>;
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("Canonical JSON cannot encode a non-finite number.");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (typeof value !== "object") {
    throw new Error("Canonical JSON received an unsupported value.");
  }
  const item = value as Record<string, unknown>;
  return `{${Object.keys(item)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(item[key])}`)
    .join(",")}}`;
}

function requiredReview(value: AuditionReview | undefined): AuditionReview {
  if (value === undefined) throw new Error("A required audition review was unavailable.");
  return value;
}

function requireLiveRuntime(values: readonly SpeechRuntimeInstance[]): SpeechRuntimeInstance {
  const live = [...values]
    .filter((item) => ["ready", "busy", "idle"].includes(item.state))
    .sort((left, right) => right.startedAt.localeCompare(left.startedAt));
  const value = live[0];
  if (
    value === undefined ||
    value.networkPolicy !== "python_socket_api_denied" ||
    value.observedNetworkRequestCount !== null ||
    value.restartReconciliation !== null ||
    value.stoppedAt !== null ||
    value.stopReasonCode !== null ||
    value.shutdownAcknowledged !== null ||
    value.gracefulShutdownConfirmed !== null ||
    value.exitCode !== null ||
    value.terminatedByParent !== null ||
    value.ownershipConfirmed !== null ||
    value.confirmedExited !== null ||
    value.ownedProcessesConfirmedExited !== null ||
    value.jobObjectAssigned !== true ||
    value.deniedNetworkAttemptCount !== 0
  ) {
    throw new Error("A live owned provider-worker identity was unavailable.");
  }
  return value;
}

function unique(values: readonly string[]): readonly string[] {
  return [...new Set(values)];
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}
