import { rename, rm, writeFile } from "node:fs/promises";

export const phase3bPackagedE2eSchemaVersion = "7.0.0" as const;
export const phase3bFixtureEvidenceClassification =
  "deterministic_fixture_lifecycle_only" as const;
export const phase3bPackagedE2eResultEnvironment =
  "CSS_PHASE3B_PACKAGED_E2E_RESULT_PATH" as const;

const SHA256 = /^[a-f0-9]{64}$/u;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/u;
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T/u;
const PROCESS_CREATION_TIME =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z$/u;
const maximumResultBytes = 1024 * 1024;

export const phase3bAssertionKeys = Object.freeze([
  "phase0ThroughPhase3aPrerequisitesCurrent",
  "fixtureProviderClearlyClassified",
  "modelVerifiedAndActivated",
  "pronunciationDecisionApproved",
  "narratorAndTwoCharacterAuditionsGenerated",
  "authenticatedWavLoadsPassed",
  "verifiedCacheHitProven",
  "targetedInvalidationProven",
  "fiveGateTypesApproved",
  "restartPersistenceProven",
  "runtimeNetworkPolicyAndOwnedPidEndpointObservationProven",
  "electronServiceAndProviderWorkerOwnershipProven",
  "allExactOwnedProcessesExited",
  "noUnrelatedProcessInspectedOrTerminated"
] as const);

export type Phase3bAssertionKey = (typeof phase3bAssertionKeys)[number];

export interface Phase3bProviderEvidence {
  readonly providerId: string;
  readonly providerVersion: string;
}

export interface Phase3bRuntimeEvidence {
  readonly profileId: string;
  readonly profileFingerprint: string;
  readonly protocolVersion: "1.0.0";
  readonly runtimeInstanceIds: readonly string[];
  readonly networkPolicy: "python_socket_api_denied";
  readonly observedNetworkRequestCount: null;
  readonly externalNetworkObservation: {
    readonly method: "owned_pid_tcp_endpoint_inventory";
    readonly ownedPidsOnly: true;
    readonly observedNonLoopbackEndpointCount: 0;
  };
}

export interface Phase3bModelEvidence {
  readonly modelPackageId: string;
  readonly manifestVersion: string;
  readonly modelPackageFingerprint: string;
  readonly installationRevision: number;
  readonly verificationId: string;
  readonly verificationFingerprint: string;
  readonly verified: true;
  readonly active: true;
}

export interface Phase3bPronunciationEvidence {
  readonly dictionaryId: string;
  readonly initialRevision: number;
  readonly initialFingerprint: string;
  readonly initialEntryId: string;
  readonly initialEntryFingerprint: string;
  readonly initialDecision: "approved";
  readonly supersedingEntryId: string;
  readonly supersedingEntryFingerprint: string;
  readonly supersedesEntryId: string;
  readonly supersedingDecision: "approved";
  readonly finalRevision: number;
  readonly finalFingerprint: string;
}

export interface Phase3bAudioPropertiesEvidence {
  readonly mediaType: "audio/wav";
  readonly codec: "pcm_s16le";
  readonly sampleRateHz: 24000;
  readonly channels: 1;
  readonly sampleWidthBytes: 2;
  readonly durationMilliseconds: number;
  readonly byteSize: number;
  readonly sha256: string;
  readonly nonSilencePassed: true;
  readonly clippingPassed: true;
  readonly blockingFindingCount: 0;
}

export interface Phase3bAuditionEvidence {
  readonly roleId: string;
  readonly roleType: "narrator" | "character";
  readonly assignmentId: string;
  readonly assignmentRevision: number;
  readonly voiceRuntimeBindingId: string;
  readonly voiceRuntimeBindingFingerprint: string;
  readonly providerVoiceId: string;
  readonly auditionSessionId: string;
  readonly providerRequestId: string;
  readonly requestFingerprint: string;
  readonly executionClassification:
    | "provider_execution"
    | "verified_cache_lookup";
  readonly providerDispatchCount: 0 | 1;
  readonly sourceProviderRequestId: string | null;
  readonly runtimeInstanceId: string | null;
  readonly normalizedTextSha256: string;
  readonly pronunciationPlanFingerprint: string;
  /** Deterministic SHA-256 cache identity; never raw text or key material. */
  readonly cacheKey: string;
  readonly cacheStatus: "miss" | "verified_hit";
  readonly auditionClipId: string;
  readonly clipFingerprint: string;
  readonly audioArtifactId: string;
  readonly audio: Phase3bAudioPropertiesEvidence;
  readonly authenticatedAudioLoaded: true;
  readonly fixtureEvidenceOnly: true;
}

export interface Phase3bCacheHitEvidence {
  readonly originalClipId: string;
  readonly repeatedClipId: string;
  readonly originalRequestFingerprint: string;
  readonly repeatedRequestFingerprint: string;
  readonly originalCacheKey: string;
  readonly repeatedCacheKey: string;
  readonly artifactSha256: string;
  readonly repeatedArtifactSha256: string;
  readonly repeatedCacheStatus: "verified_hit";
  readonly identicalCacheKeyInputsProven: true;
  readonly lookupOnlyNoProviderExecutionProven: true;
}

export interface Phase3bTargetedInvalidationEvidence {
  readonly supersededEntryId: string;
  readonly supersedingEntryId: string;
  readonly beforeDictionaryFingerprint: string;
  readonly afterDictionaryFingerprint: string;
  readonly impactedRoleId: string;
  readonly priorRequestFingerprint: string;
  readonly regeneratedRequestFingerprint: string;
  readonly priorCacheKey: string;
  readonly regeneratedCacheKey: string;
  readonly priorArtifactSha256: string;
  readonly regeneratedArtifactSha256: string;
  readonly invalidatedClipIds: readonly string[];
  readonly preservedClipIds: readonly string[];
  readonly persistedInvalidatedGateStates: readonly {
    readonly gateId:
      | "pronunciation_review"
      | "voice_readiness_review";
    readonly reviewId: string;
    readonly state: "blocked" | "pending" | "invalidated";
    readonly evidenceFingerprint: string;
  }[];
  readonly targetedOnly: true;
}

export interface Phase3bGateDecisionEvidence {
  readonly gateId:
    | "per_role_audition_review"
    | "narrator_audition_review"
    | "character_audition_review"
    | "pronunciation_review"
    | "voice_readiness_review";
  readonly reviewId: string;
  readonly decisionId: string;
  readonly roleId: string | null;
  readonly evidenceFingerprint: string;
  readonly decision: "approved";
  readonly immutable: true;
}

export interface Phase3bOwnedProcessEvidence {
  readonly pid: number;
  readonly parentPid: number;
  readonly kind: "electron" | "service" | "provider_worker";
  readonly executableName:
    | "Cinematic Story Studio.exe"
    | "cinematic-story-service.exe";
  readonly creationIdentity: string;
  readonly goneAfterShutdown: true;
}

export interface Phase3bRuntimeExitEvidence {
  readonly runtimeInstanceId: string;
  readonly workerPid: number;
  readonly parentPid: number;
  readonly state: "stopped";
  readonly stoppedAt: string;
  readonly stopReasonCode: "clean";
  readonly handshakeAuthenticated: true;
  readonly shutdownAcknowledged: true;
  readonly gracefulShutdownConfirmed: true;
  readonly exitCode: 0;
  readonly terminatedByParent: false;
  readonly ownershipConfirmed: true;
  readonly confirmedExited: true;
  readonly ownedProcessesConfirmedExited: true;
  readonly jobObjectAssigned: true;
  readonly deniedNetworkAttemptCount: 0;
}

export interface Phase3bLaunchProcessEvidence {
  readonly launch: 1 | 2;
  readonly ownedProcesses: readonly Phase3bOwnedProcessEvidence[];
  readonly providerRuntimeExit: Phase3bRuntimeExitEvidence;
  readonly forcedPids: readonly number[];
  readonly remainingPids: readonly number[];
  readonly unrelatedProcessesInspected: false;
  readonly unrelatedProcessesTerminated: false;
}

export interface Phase3bPackagedE2eResult {
  readonly schemaVersion: typeof phase3bPackagedE2eSchemaVersion;
  readonly completedAt: string;
  readonly status: "passed";
  readonly evidenceClassification: typeof phase3bFixtureEvidenceClassification;
  readonly fixtureClaims: {
    readonly lifecycleEvidenceOnly: true;
    readonly naturalSpeechQualityProven: false;
    readonly productionExportEligible: false;
    readonly humanListeningClaimed: false;
  };
  readonly runtime: Phase3bRuntimeEvidence;
  readonly fixtureProvider: Phase3bProviderEvidence;
  readonly realProviderAdapter: Phase3bProviderEvidence;
  readonly model: Phase3bModelEvidence;
  readonly pronunciation: Phase3bPronunciationEvidence;
  readonly auditions: readonly Phase3bAuditionEvidence[];
  readonly cacheHit: Phase3bCacheHitEvidence;
  readonly targetedInvalidation: Phase3bTargetedInvalidationEvidence;
  readonly gateDecisions: readonly Phase3bGateDecisionEvidence[];
  readonly restart: {
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
  };
  readonly process: {
    readonly launches: readonly Phase3bLaunchProcessEvidence[];
  };
  readonly screenshot: {
    readonly artifactId: "packaged-ui-screenshot";
    readonly captured: true;
  };
  readonly assertions: Readonly<Record<Phase3bAssertionKey, true>>;
}

export function validatePhase3bPackagedE2eResult(
  raw: unknown
): Phase3bPackagedE2eResult {
  const value = record(raw, "Phase 3B packaged E2E result");
  exactKeys(value, [
    "schemaVersion",
    "completedAt",
    "status",
    "evidenceClassification",
    "fixtureClaims",
    "runtime",
    "fixtureProvider",
    "realProviderAdapter",
    "model",
    "pronunciation",
    "auditions",
    "cacheHit",
    "targetedInvalidation",
    "gateDecisions",
    "restart",
    "process",
    "screenshot",
    "assertions"
  ]);
  if (
    value.schemaVersion !== phase3bPackagedE2eSchemaVersion ||
    value.status !== "passed" ||
    value.evidenceClassification !== phase3bFixtureEvidenceClassification ||
    !isTimestamp(value.completedAt)
  ) fail("The Phase 3B result identity was invalid.");

  validateFixtureClaims(value.fixtureClaims);
  validateRuntime(value.runtime);
  validateProvider(value.fixtureProvider, "fixture provider");
  validateProvider(value.realProviderAdapter, "real provider adapter");
  validateModel(value.model);
  validatePronunciation(value.pronunciation);
  const auditions = array(value.auditions, "auditions", 3, 20);
  auditions.forEach(validateAudition);
  const roleIds = auditions.map((item) => record(item, "audition").roleId);
  const roleAssignmentIdentities = new Map<
    string,
    {
      readonly roleType: unknown;
      readonly assignmentId: unknown;
      readonly assignmentRevision: unknown;
    }
  >();
  for (const rawAudition of auditions) {
    const audition = record(rawAudition, "audition");
    const roleId = id(audition.roleId, "audition role ID");
    const prior = roleAssignmentIdentities.get(roleId);
    if (
      prior !== undefined &&
      (prior.roleType !== audition.roleType ||
        prior.assignmentId !== audition.assignmentId ||
        prior.assignmentRevision !== audition.assignmentRevision)
    ) {
      fail("An audition role changed assignment identity across clips.");
    }
    roleAssignmentIdentities.set(roleId, {
      roleType: audition.roleType,
      assignmentId: audition.assignmentId,
      assignmentRevision: audition.assignmentRevision
    });
  }
  const clipIds = auditions.map(
    (item) => record(item, "audition").auditionClipId
  );
  if (
    new Set(clipIds).size !== clipIds.length ||
    new Set(roleIds).size < 3 ||
    auditions.filter((item) => record(item, "audition").roleType === "narrator").length < 1 ||
    auditions.filter((item) => record(item, "audition").roleType === "character").length < 2
  ) fail("The Phase 3B result did not contain narrator and two character auditions.");
  validateCacheHit(value.cacheHit, auditions);
  validateTargetedInvalidation(
    value.targetedInvalidation,
    auditions,
    value.pronunciation
  );
  validateGateDecisions(value.gateDecisions, roleIds);
  const priorLaunchRuntimeExit = validateRestart(value.restart);
  validateProcess(
    value.process,
    priorLaunchRuntimeExit,
    record(value.runtime, "runtime")
  );
  const screenshot = record(value.screenshot, "screenshot");
  exactKeys(screenshot, ["artifactId", "captured"]);
  if (screenshot.artifactId !== "packaged-ui-screenshot" || screenshot.captured !== true) {
    fail("The Phase 3B screenshot evidence was invalid.");
  }
  validateAllTrueRecord(value.assertions, phase3bAssertionKeys, "assertions");
  rejectPrivateEvidence(value);
  return raw as Phase3bPackagedE2eResult;
}

function validateRestart(raw: unknown): Record<string, unknown> {
  const value = record(raw, "restart evidence");
  const trueKeys = [
    "runtimeProfilePersisted",
    "modelVerificationPersisted",
    "pronunciationDictionaryPersisted",
    "pronunciationDecisionsPersisted",
    "auditionSessionsPersisted",
    "auditionScriptsPersisted",
    "auditionClipsPersisted",
    "cacheRecordsPersisted",
    "audioQualityRecordsPersisted",
    "auditionDecisionsPersisted",
    "voiceReadinessDecisionPersisted",
    "authenticatedRestoredAudioLoaded"
  ] as const;
  exactKeys(value, [...trueKeys, "priorLaunchRuntimeExit"]);
  if (trueKeys.some((key) => value[key] !== true)) {
    fail("The restart evidence was incomplete.");
  }
  return validateRuntimeExit(
    value.priorLaunchRuntimeExit,
    "persisted prior-launch runtime exit"
  );
}

export async function writePhase3bPackagedE2eResult(
  resultPath: string,
  value: Phase3bPackagedE2eResult
): Promise<void> {
  validatePhase3bPackagedE2eResult(value);
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  if (Buffer.byteLength(serialized, "utf8") > maximumResultBytes) {
    fail("The Phase 3B result exceeded its public evidence limit.");
  }
  const temporaryPath = `${resultPath}.${process.pid}.tmp`;
  try {
    await writeFile(temporaryPath, serialized, { encoding: "utf8", mode: 0o600 });
    await rename(temporaryPath, resultPath);
  } finally {
    await rm(temporaryPath, { force: true });
  }
}

function validateFixtureClaims(raw: unknown): void {
  const value = record(raw, "fixture claims");
  exactKeys(value, [
    "lifecycleEvidenceOnly",
    "naturalSpeechQualityProven",
    "productionExportEligible",
    "humanListeningClaimed"
  ]);
  if (
    value.lifecycleEvidenceOnly !== true ||
    value.naturalSpeechQualityProven !== false ||
    value.productionExportEligible !== false ||
    value.humanListeningClaimed !== false
  ) fail("Fixture audio was represented beyond lifecycle evidence.");
}

function validateRuntime(raw: unknown): void {
  const value = record(raw, "runtime");
  exactKeys(value, [
    "profileId",
    "profileFingerprint",
    "protocolVersion",
    "runtimeInstanceIds",
    "networkPolicy",
    "observedNetworkRequestCount",
    "externalNetworkObservation"
  ]);
  id(value.profileId, "runtime profile ID");
  hash(value.profileFingerprint, "runtime profile fingerprint");
  if (
    value.protocolVersion !== "1.0.0" ||
    value.networkPolicy !== "python_socket_api_denied" ||
    value.observedNetworkRequestCount !== null
  ) fail("The runtime protocol or network-policy evidence was invalid.");
  uniqueIds(value.runtimeInstanceIds, "runtime instance IDs", 1, 10);
  const observation = record(
    value.externalNetworkObservation,
    "external runtime network observation"
  );
  exactKeys(observation, [
    "method",
    "ownedPidsOnly",
    "observedNonLoopbackEndpointCount"
  ]);
  if (
    observation.method !== "owned_pid_tcp_endpoint_inventory" ||
    observation.ownedPidsOnly !== true ||
    observation.observedNonLoopbackEndpointCount !== 0
  ) fail("The owned-PID endpoint observation was invalid.");
}

function validateProvider(raw: unknown, label: string): void {
  const value = record(raw, label);
  exactKeys(value, ["providerId", "providerVersion"]);
  id(value.providerId, `${label} ID`);
  id(value.providerVersion, `${label} version`);
}

function validateModel(raw: unknown): void {
  const value = record(raw, "model");
  exactKeys(value, [
    "modelPackageId", "manifestVersion", "modelPackageFingerprint",
    "installationRevision", "verificationId", "verificationFingerprint",
    "verified", "active"
  ]);
  id(value.modelPackageId, "model package ID");
  id(value.manifestVersion, "model manifest version");
  hash(value.modelPackageFingerprint, "model package fingerprint");
  positive(value.installationRevision, "installation revision");
  id(value.verificationId, "verification ID");
  hash(value.verificationFingerprint, "verification fingerprint");
  if (value.verified !== true || value.active !== true) fail("The fixture model was not verified and active.");
}

function validatePronunciation(raw: unknown): void {
  const value = record(raw, "pronunciation");
  exactKeys(value, [
    "dictionaryId", "initialRevision", "initialFingerprint", "initialEntryId",
    "initialEntryFingerprint", "initialDecision", "supersedingEntryId",
    "supersedingEntryFingerprint", "supersedesEntryId", "supersedingDecision",
    "finalRevision", "finalFingerprint"
  ]);
  for (const key of ["dictionaryId", "initialEntryId", "supersedingEntryId", "supersedesEntryId"] as const) id(value[key], key);
  for (const key of ["initialFingerprint", "initialEntryFingerprint", "supersedingEntryFingerprint", "finalFingerprint"] as const) hash(value[key], key);
  const initialRevision = positive(value.initialRevision, "initial dictionary revision");
  const finalRevision = positive(value.finalRevision, "final dictionary revision");
  if (
    value.initialDecision !== "approved" ||
    value.supersedingDecision !== "approved" ||
    value.supersedesEntryId !== value.initialEntryId ||
    value.supersedingEntryId === value.initialEntryId ||
    finalRevision <= initialRevision ||
    value.finalFingerprint === value.initialFingerprint
  ) fail("The pronunciation supersession proof was invalid.");
}

function validateAudition(raw: unknown): void {
  const value = record(raw, "audition");
  exactKeys(value, [
    "roleId", "roleType", "assignmentId", "assignmentRevision",
    "voiceRuntimeBindingId", "voiceRuntimeBindingFingerprint", "providerVoiceId",
    "auditionSessionId", "providerRequestId", "requestFingerprint",
    "executionClassification", "providerDispatchCount",
    "sourceProviderRequestId", "runtimeInstanceId",
    "normalizedTextSha256", "pronunciationPlanFingerprint",
    "cacheKey", "cacheStatus", "auditionClipId", "clipFingerprint", "audioArtifactId",
    "audio", "authenticatedAudioLoaded", "fixtureEvidenceOnly"
  ]);
  for (const key of ["roleId", "assignmentId", "voiceRuntimeBindingId", "providerVoiceId", "auditionSessionId", "providerRequestId", "auditionClipId", "audioArtifactId"] as const) id(value[key], key);
  for (const key of ["voiceRuntimeBindingFingerprint", "requestFingerprint", "normalizedTextSha256", "pronunciationPlanFingerprint", "cacheKey", "clipFingerprint"] as const) hash(value[key], key);
  if (value.sourceProviderRequestId !== null) {
    id(value.sourceProviderRequestId, "source provider request ID");
  }
  if (value.runtimeInstanceId !== null) {
    id(value.runtimeInstanceId, "runtime instance ID");
  }
  if (
    (value.roleType !== "narrator" && value.roleType !== "character") ||
    (value.cacheStatus !== "miss" && value.cacheStatus !== "verified_hit") ||
    (value.cacheStatus === "miss" &&
      (value.executionClassification !== "provider_execution" ||
        value.providerDispatchCount !== 1 ||
        value.sourceProviderRequestId !== null ||
        value.runtimeInstanceId === null)) ||
    (value.cacheStatus === "verified_hit" &&
      (value.executionClassification !== "verified_cache_lookup" ||
        value.providerDispatchCount !== 0 ||
        value.sourceProviderRequestId === null ||
        value.sourceProviderRequestId === value.providerRequestId ||
        value.runtimeInstanceId !== null)) ||
    value.authenticatedAudioLoaded !== true ||
    value.fixtureEvidenceOnly !== true
  ) fail("The audition lifecycle evidence was invalid.");
  positive(value.assignmentRevision, "assignment revision");
  validateAudio(value.audio);
}

function validateAudio(raw: unknown): void {
  const value = record(raw, "audio properties");
  exactKeys(value, [
    "mediaType", "codec", "sampleRateHz", "channels", "sampleWidthBytes",
    "durationMilliseconds", "byteSize", "sha256", "nonSilencePassed",
    "clippingPassed", "blockingFindingCount"
  ]);
  if (
    value.mediaType !== "audio/wav" || value.codec !== "pcm_s16le" ||
    value.sampleRateHz !== 24000 || value.channels !== 1 ||
    value.sampleWidthBytes !== 2 || value.nonSilencePassed !== true ||
    value.clippingPassed !== true || value.blockingFindingCount !== 0
  ) fail("The WAV integrity evidence was invalid.");
  positive(value.durationMilliseconds, "audio duration");
  positive(value.byteSize, "audio byte size");
  hash(value.sha256, "audio SHA-256");
}

function validateCacheHit(raw: unknown, auditions: readonly unknown[]): void {
  const value = record(raw, "cache hit");
  exactKeys(value, [
    "originalClipId", "repeatedClipId", "originalRequestFingerprint",
    "repeatedRequestFingerprint", "originalCacheKey", "repeatedCacheKey",
    "artifactSha256", "repeatedArtifactSha256", "repeatedCacheStatus",
    "identicalCacheKeyInputsProven", "lookupOnlyNoProviderExecutionProven"
  ]);
  id(value.originalClipId, "original clip ID");
  id(value.repeatedClipId, "repeated clip ID");
  for (const key of ["originalRequestFingerprint", "repeatedRequestFingerprint", "originalCacheKey", "repeatedCacheKey", "artifactSha256", "repeatedArtifactSha256"] as const) hash(value[key], key);
  const auditionRecords = auditions.map((item) => record(item, "audition"));
  const original = auditionRecords.find(
    (audition) => audition.auditionClipId === value.originalClipId
  );
  const repeated = auditionRecords.find(
    (audition) => audition.auditionClipId === value.repeatedClipId
  );
  if (
    value.originalClipId === value.repeatedClipId ||
    value.originalRequestFingerprint === value.repeatedRequestFingerprint ||
    value.originalCacheKey !== value.repeatedCacheKey ||
    value.artifactSha256 !== value.repeatedArtifactSha256 ||
    value.repeatedCacheStatus !== "verified_hit" ||
    value.identicalCacheKeyInputsProven !== true ||
    value.lookupOnlyNoProviderExecutionProven !== true ||
    original === undefined ||
    repeated === undefined ||
    original.roleId !== repeated.roleId ||
    original.voiceRuntimeBindingId !== repeated.voiceRuntimeBindingId ||
    original.voiceRuntimeBindingFingerprint !==
      repeated.voiceRuntimeBindingFingerprint ||
    original.providerVoiceId !== repeated.providerVoiceId ||
    original.requestFingerprint !== value.originalRequestFingerprint ||
    original.executionClassification !== "provider_execution" ||
    original.providerDispatchCount !== 1 ||
    original.sourceProviderRequestId !== null ||
    typeof original.runtimeInstanceId !== "string" ||
    original.cacheKey !== value.originalCacheKey ||
    record(original.audio, "audio").sha256 !== value.artifactSha256 ||
    repeated.requestFingerprint !== value.repeatedRequestFingerprint ||
    repeated.executionClassification !== "verified_cache_lookup" ||
    repeated.providerDispatchCount !== 0 ||
    repeated.sourceProviderRequestId !== original.providerRequestId ||
    repeated.runtimeInstanceId !== null ||
    repeated.cacheKey !== value.repeatedCacheKey ||
    repeated.cacheStatus !== "verified_hit" ||
    record(repeated.audio, "audio").sha256 !== value.repeatedArtifactSha256
  ) fail("The verified cache-hit proof was invalid.");
}

function validateTargetedInvalidation(
  raw: unknown,
  auditions: readonly unknown[],
  rawPronunciation: unknown
): void {
  const value = record(raw, "targeted invalidation");
  const pronunciation = record(rawPronunciation, "pronunciation evidence");
  exactKeys(value, [
    "supersededEntryId", "supersedingEntryId", "beforeDictionaryFingerprint",
    "afterDictionaryFingerprint", "impactedRoleId", "priorRequestFingerprint",
    "regeneratedRequestFingerprint", "priorCacheKey", "regeneratedCacheKey", "priorArtifactSha256",
    "regeneratedArtifactSha256", "invalidatedClipIds", "preservedClipIds",
    "persistedInvalidatedGateStates", "targetedOnly"
  ]);
  for (const key of ["supersededEntryId", "supersedingEntryId", "impactedRoleId"] as const) id(value[key], key);
  for (const key of ["beforeDictionaryFingerprint", "afterDictionaryFingerprint", "priorRequestFingerprint", "regeneratedRequestFingerprint", "priorCacheKey", "regeneratedCacheKey", "priorArtifactSha256", "regeneratedArtifactSha256"] as const) hash(value[key], key);
  const invalidated = uniqueIds(value.invalidatedClipIds, "invalidated clip IDs", 1, 2000);
  const preserved = uniqueIds(value.preservedClipIds, "preserved clip IDs", 1, 2000);
  const auditionRecords = auditions.map((item) => record(item, "audition"));
  const impactedAudition = auditionRecords.find(
    (item) =>
      item.roleId === value.impactedRoleId &&
      invalidated.includes(item.auditionClipId as string) &&
      item.requestFingerprint === value.priorRequestFingerprint &&
      item.cacheKey === value.priorCacheKey &&
      record(item.audio, "audio").sha256 === value.priorArtifactSha256
  );
  const regeneratedAudition = auditionRecords.find(
    (item) =>
      item.roleId === value.impactedRoleId &&
      item.requestFingerprint === value.regeneratedRequestFingerprint &&
      item.cacheKey === value.regeneratedCacheKey &&
      item.cacheStatus === "miss" &&
      record(item.audio, "audio").sha256 === value.regeneratedArtifactSha256
  );
  const gateStates = array(
    value.persistedInvalidatedGateStates,
    "persisted invalidated gate states",
    2,
    2
  );
  const observedGateIds = new Set<string>();
  for (const rawGateState of gateStates) {
    const gateState = record(rawGateState, "persisted invalidated gate state");
    exactKeys(gateState, [
      "gateId",
      "reviewId",
      "state",
      "evidenceFingerprint"
    ]);
    if (
      (gateState.gateId !== "pronunciation_review" &&
        gateState.gateId !== "voice_readiness_review") ||
      (gateState.state !== "blocked" &&
        gateState.state !== "pending" &&
        gateState.state !== "invalidated") ||
      observedGateIds.has(gateState.gateId)
    ) fail("A persisted invalidated gate state was invalid.");
    id(gateState.reviewId, "persisted invalidated review ID");
    hash(
      gateState.evidenceFingerprint,
      "persisted invalidated gate evidence fingerprint"
    );
    observedGateIds.add(gateState.gateId);
  }
  if (
    value.supersededEntryId !== pronunciation.initialEntryId ||
    value.supersedingEntryId !== pronunciation.supersedingEntryId ||
    value.beforeDictionaryFingerprint !== pronunciation.initialFingerprint ||
    value.afterDictionaryFingerprint !== pronunciation.finalFingerprint ||
    value.supersededEntryId === value.supersedingEntryId ||
    value.beforeDictionaryFingerprint === value.afterDictionaryFingerprint ||
    value.priorRequestFingerprint === value.regeneratedRequestFingerprint ||
    value.priorCacheKey === value.regeneratedCacheKey ||
    value.priorArtifactSha256 === value.regeneratedArtifactSha256 ||
    invalidated.some((item) => preserved.includes(item)) ||
    invalidated.some(
      (clipId) =>
        !auditionRecords.some(
          (item) =>
            item.auditionClipId === clipId &&
            item.roleId === value.impactedRoleId
        )
    ) ||
    preserved.some(
      (clipId) =>
        !auditionRecords.some(
          (item) => item.auditionClipId === clipId
        )
    ) ||
    value.targetedOnly !== true ||
    !observedGateIds.has("pronunciation_review") ||
    !observedGateIds.has("voice_readiness_review") ||
    impactedAudition === undefined ||
    regeneratedAudition === undefined ||
    impactedAudition.voiceRuntimeBindingId !==
      regeneratedAudition.voiceRuntimeBindingId ||
    impactedAudition.voiceRuntimeBindingFingerprint !==
      regeneratedAudition.voiceRuntimeBindingFingerprint ||
    impactedAudition.providerVoiceId !== regeneratedAudition.providerVoiceId ||
    impactedAudition.auditionClipId === regeneratedAudition.auditionClipId ||
    invalidated.includes(regeneratedAudition.auditionClipId as string) ||
    preserved.includes(regeneratedAudition.auditionClipId as string)
  ) fail("The targeted invalidation proof was invalid.");
}

function validateGateDecisions(
  raw: unknown,
  auditionRoleIds: readonly unknown[]
): void {
  const values = array(raw, "gate decisions", 5, 304);
  const expectedRoleIds = new Set(
    auditionRoleIds.map((roleId) => id(roleId, "audition role ID"))
  );
  const observedRoleIds = new Set<string>();
  const aggregateGateIds = new Set<string>();
  const reviewIds = new Set<string>();
  const decisionIds = new Set<string>();
  const aggregateGates = new Set([
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review"
  ]);
  for (const rawDecision of values) {
    const value = record(rawDecision, "gate decision");
    exactKeys(value, ["gateId", "reviewId", "decisionId", "roleId", "evidenceFingerprint", "decision", "immutable"]);
    const gateId = id(value.gateId, "gate ID");
    const reviewId = id(value.reviewId, "review ID");
    const decisionId = id(value.decisionId, "decision ID");
    hash(value.evidenceFingerprint, "gate evidence fingerprint");
    if (value.decision !== "approved" || value.immutable !== true) fail("A gate decision was not an immutable approval.");
    if (reviewIds.has(reviewId) || decisionIds.has(decisionId)) {
      fail("Phase 3B gate review and decision IDs must be unique.");
    }
    reviewIds.add(reviewId);
    decisionIds.add(decisionId);

    if (gateId === "per_role_audition_review") {
      if (value.roleId === null) {
        fail("The Phase 3B gate decision topology was invalid.");
      }
      const roleId = id(value.roleId, "gate role ID");
      if (!expectedRoleIds.has(roleId) || observedRoleIds.has(roleId)) {
        fail("The Phase 3B gate decision topology was invalid.");
      }
      observedRoleIds.add(roleId);
    } else {
      if (
        !aggregateGates.has(gateId) ||
        value.roleId !== null ||
        aggregateGateIds.has(gateId)
      ) {
        fail("The Phase 3B gate decision topology was invalid.");
      }
      aggregateGateIds.add(gateId);
    }
  }
  if (
    values.length !== expectedRoleIds.size + aggregateGates.size ||
    observedRoleIds.size !== expectedRoleIds.size ||
    aggregateGateIds.size !== aggregateGates.size
  ) {
    fail("The Phase 3B gate decision topology was invalid.");
  }
}

function validateRuntimeExit(
  raw: unknown,
  label: string
): Record<string, unknown> {
  const value = record(raw, label);
  exactKeys(value, [
    "runtimeInstanceId",
    "workerPid",
    "parentPid",
    "state",
    "stoppedAt",
    "stopReasonCode",
    "handshakeAuthenticated",
    "shutdownAcknowledged",
    "gracefulShutdownConfirmed",
    "exitCode",
    "terminatedByParent",
    "ownershipConfirmed",
    "confirmedExited",
    "ownedProcessesConfirmedExited",
    "jobObjectAssigned",
    "deniedNetworkAttemptCount"
  ]);
  id(value.runtimeInstanceId, `${label} runtime instance ID`);
  positive(value.workerPid, `${label} worker PID`);
  positive(value.parentPid, `${label} parent PID`);
  if (
    value.workerPid === value.parentPid ||
    value.state !== "stopped" ||
    !isTimestamp(value.stoppedAt) ||
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
    fail(`The ${label} was not an authenticated graceful worker exit.`);
  }
  return value;
}

function validateProcess(
  raw: unknown,
  priorLaunchRuntimeExit: Record<string, unknown>,
  runtime: Record<string, unknown>
): void {
  const value = record(raw, "process evidence");
  exactKeys(value, ["launches"]);
  const launches = array(value.launches, "process launches", 2, 2);
  const runtimeInstanceIds = new Set(
    uniqueIds(runtime.runtimeInstanceIds, "runtime instance IDs", 1, 10)
  );
  const exitRuntimeInstanceIds = new Set<string>();
  for (const [index, rawLaunch] of launches.entries()) {
    const launch = record(rawLaunch, "process launch");
    exactKeys(launch, ["launch", "ownedProcesses", "providerRuntimeExit", "forcedPids", "remainingPids", "unrelatedProcessesInspected", "unrelatedProcessesTerminated"]);
    if (launch.launch !== index + 1 || launch.unrelatedProcessesInspected !== false || launch.unrelatedProcessesTerminated !== false) fail("The process launch proof was invalid.");
    if (array(launch.forcedPids, "forced PIDs", 0, 0).length !== 0 || array(launch.remainingPids, "remaining PIDs", 0, 0).length !== 0) fail("An owned process required force or remained alive.");
    const processes = array(launch.ownedProcesses, "owned processes", 3, 64);
    const processRecords: Record<string, unknown>[] = [];
    const kinds = new Set<string>();
    const pids = new Set<number>();
    for (const rawProcess of processes) {
      const processValue = record(rawProcess, "owned process");
      exactKeys(processValue, ["pid", "parentPid", "kind", "executableName", "creationIdentity", "goneAfterShutdown"]);
      const pid = positive(processValue.pid, "owned PID");
      positive(processValue.parentPid, "owned parent PID");
      if (pids.has(pid)) fail("An owned PID was duplicated.");
      pids.add(pid);
      if (!new Set(["electron", "service", "provider_worker"]).has(processValue.kind as string)) fail("An owned process kind was invalid.");
      if (
        (processValue.kind === "electron" && processValue.executableName !== "Cinematic Story Studio.exe") ||
        (processValue.kind !== "electron" && processValue.executableName !== "cinematic-story-service.exe") ||
        !isInvariantProcessCreationTimestamp(processValue.creationIdentity) ||
        processValue.goneAfterShutdown !== true
      ) fail("An owned process identity was invalid.");
      kinds.add(processValue.kind as string);
      processRecords.push(processValue);
    }
    if (!hasSingleRootedOwnedProcessTree(processRecords)) {
      fail("The owned process evidence was not one rooted Electron tree.");
    }
    if (["electron", "service", "provider_worker"].some((kind) => !kinds.has(kind))) fail("Electron, service, and provider-worker ownership was not proven.");
    const runtimeExit = validateRuntimeExit(
      launch.providerRuntimeExit,
      `launch ${index + 1} provider runtime exit`
    );
    const runtimeInstanceId = runtimeExit.runtimeInstanceId as string;
    if (
      !runtimeInstanceIds.has(runtimeInstanceId) ||
      exitRuntimeInstanceIds.has(runtimeInstanceId) ||
      !hasOwnedProviderWorkerLineage(
        processRecords,
        runtimeExit.workerPid,
        runtimeExit.parentPid
      )
    ) {
      fail("The provider runtime exit did not bind the exact owned worker tree.");
    }
    exitRuntimeInstanceIds.add(runtimeInstanceId);
    if (
      index === 0 &&
      Object.keys(runtimeExit).some(
        (key) => runtimeExit[key] !== priorLaunchRuntimeExit[key]
      )
    ) {
      fail("The persisted prior-launch runtime exit did not match its sidecar proof.");
    }
  }
}

function hasOwnedProviderWorkerLineage(
  processes: readonly Record<string, unknown>[],
  workerPid: unknown,
  logicalParentPid: unknown
): boolean {
  if (
    !Number.isSafeInteger(workerPid) ||
    !Number.isSafeInteger(logicalParentPid)
  ) return false;
  const workerPidValue = workerPid as number;
  const logicalParentPidValue = logicalParentPid as number;
  const byPid = new Map<number, Record<string, unknown>>(
    processes.map((item) => [item.pid as number, item])
  );
  const logicalParent = byPid.get(logicalParentPidValue);
  const worker = byPid.get(workerPidValue);
  const providerBoundaries = processes.filter((item) => {
    const parent = byPid.get(item.parentPid as number);
    return item.kind === "provider_worker" && parent?.kind === "service";
  });
  if (
    logicalParent?.kind !== "service" ||
    worker?.kind !== "provider_worker" ||
    providerBoundaries.length !== 1 ||
    providerBoundaries[0]?.parentPid !== logicalParentPidValue
  ) return false;
  if ((worker.parentPid as number) === logicalParentPidValue) {
    return processCreatedNotEarlier(worker, logicalParent);
  }
  const intermediary = byPid.get(worker.parentPid as number);
  return (
    intermediary?.kind === "provider_worker" &&
    intermediary.parentPid === logicalParentPidValue &&
    processCreatedNotEarlier(intermediary, logicalParent) &&
    processCreatedNotEarlier(worker, intermediary)
  );
}

function hasSingleRootedOwnedProcessTree(
  processes: readonly Record<string, unknown>[]
): boolean {
  const byPid = new Map<number, Record<string, unknown>>(
    processes.map((item) => [item.pid as number, item])
  );
  const roots = processes.filter(
    (item) => !byPid.has(item.parentPid as number)
  );
  const root = roots[0];
  if (roots.length !== 1 || root?.kind !== "electron") return false;
  const serviceBoundaries = processes.filter((item) => {
    const parent = byPid.get(item.parentPid as number);
    return item.kind === "service" && parent?.kind === "electron";
  });
  if (serviceBoundaries.length !== 1) return false;
  const rootPid = root.pid as number;
  for (const processIdentity of processes) {
    if ((processIdentity.pid as number) === rootPid) continue;
    const visited = new Set<number>();
    let child = processIdentity;
    while ((child.pid as number) !== rootPid) {
      const childPid = child.pid as number;
      const parent = byPid.get(child.parentPid as number);
      if (
        visited.has(childPid) ||
        parent === undefined ||
        !validOwnedProcessKindTransition(parent.kind, child.kind) ||
        !processCreatedNotEarlier(child, parent)
      ) return false;
      visited.add(childPid);
      child = parent;
    }
  }
  return true;
}

function validOwnedProcessKindTransition(
  parentKind: unknown,
  childKind: unknown
): boolean {
  return (
    (parentKind === "electron" &&
      (childKind === "electron" || childKind === "service")) ||
    (parentKind === "service" &&
      (childKind === "service" || childKind === "provider_worker")) ||
    (parentKind === "provider_worker" && childKind === "provider_worker")
  );
}

function processCreatedNotEarlier(
  child: Record<string, unknown>,
  parent: Record<string, unknown>
): boolean {
  return (
    (child.creationIdentity as string) >=
    (parent.creationIdentity as string)
  );
}

function validateAllTrueRecord(raw: unknown, keys: readonly string[], label: string): void {
  const value = record(raw, label);
  exactKeys(value, keys);
  if (keys.some((key) => value[key] !== true)) fail(`The ${label} was incomplete.`);
}

function rejectPrivateEvidence(value: Record<string, unknown>): void {
  const serialized = JSON.stringify(value);
  if (
    /(?:[A-Za-z]:\\|\\\\|\/Users\/|\/home\/)/u.test(serialized) ||
    /"(?:text|script|manuscript|absolutePath|filePath|token|credential)"\s*:/iu.test(serialized)
  ) fail("The Phase 3B public evidence contained private or path-shaped data.");
}

function record(raw: unknown, label: string): Record<string, unknown> {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) fail(`The ${label} was invalid.`);
  return raw as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): void {
  const expected = new Set(keys);
  if (Object.keys(value).length !== expected.size || Object.keys(value).some((key) => !expected.has(key))) fail("The evidence contained an unknown or omitted field.");
}

function array(raw: unknown, label: string, minimum: number, maximum: number): readonly unknown[] {
  if (!Array.isArray(raw) || raw.length < minimum || raw.length > maximum) fail(`The ${label} was out of bounds.`);
  return raw as readonly unknown[];
}

function uniqueIds(raw: unknown, label: string, minimum: number, maximum: number): readonly string[] {
  const values = array(raw, label, minimum, maximum).map((value) => id(value, label));
  if (new Set(values).size !== values.length) fail(`The ${label} contained duplicates.`);
  return values;
}

function id(raw: unknown, label: string): string {
  if (typeof raw !== "string" || !SAFE_ID.test(raw)) fail(`The ${label} was invalid.`);
  return raw;
}

function hash(raw: unknown, label: string): string {
  if (typeof raw !== "string" || !SHA256.test(raw)) fail(`The ${label} was invalid.`);
  return raw;
}

function positive(raw: unknown, label: string): number {
  if (!Number.isSafeInteger(raw) || (raw as number) <= 0) fail(`The ${label} was invalid.`);
  return raw as number;
}

function isTimestamp(raw: unknown): raw is string {
  return typeof raw === "string" && ISO_TIME.test(raw) && Number.isFinite(Date.parse(raw));
}

function isInvariantProcessCreationTimestamp(raw: unknown): raw is string {
  if (typeof raw !== "string" || !PROCESS_CREATION_TIME.test(raw)) return false;
  const milliseconds = `${raw.slice(0, 23)}Z`;
  const parsed = new Date(milliseconds);
  return (
    Number.isFinite(parsed.valueOf()) && parsed.toISOString() === milliseconds
  );
}

function fail(message: string): never {
  throw new Error(message);
}
