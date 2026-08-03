import { createHash, randomBytes } from "node:crypto";
import {
  lstat,
  mkdir,
  realpath,
  rename,
  writeFile
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  GOVERNED_PRIVATE_AUDITION_WARNING,
  GOVERNED_PRIVATE_AUDITION_WARNING_SHA256,
  type AuditionClip,
  type AuditionRoleStatus,
  type AuditionSession,
  type AuditionWorkspaceSnapshot,
  type GovernedLocalVoiceInventoryRecord,
  type ModelInstallationRecord,
  type ModelPackageManifest,
  type ModelVerificationRecord,
  type PronunciationDictionary,
  type PronunciationEntry,
  type SpeechProviderRequest,
  type SpeechRuntimeInstance
} from "@cinematic-story-studio/contracts";
import {
  expect,
  type ElectronApplication,
  type Locator,
  type Page
} from "@playwright/test";

import {
  Phase3b1RendererError
} from "../../src/verification/phase3b1-renderer-error-evidence";

import {
  assignPhase3b1RealVoiceForPrivateAuditions,
  type Phase3b1RealVoiceAssignmentEvidence
} from "./phase3-voice-casting";

export const phase3b1RealModelPackageZipEnvironment =
  "CSS_PHASE3B1_REAL_MODEL_PACKAGE_ZIP";
export const phase3b1PrivateEvidenceRootEnvironment =
  "CSS_PHASE3B1_PRIVATE_EVIDENCE_ROOT";
export const phase3b1SourceHeadEnvironment =
  "CSS_PHASE3B1_SOURCE_HEAD_SHA";

const desktopRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const repositoryRoot = path.resolve(desktopRoot, "../..");
const expectedModelPackageZip = path.join(
  repositoryRoot,
  "local-models",
  "kokoro-phase3b-package.zip"
);
const expectedPrivateEvidenceRoot = path.join(
  repositoryRoot,
  "local-renders",
  "phase3b1-real-product-path"
);
const realProviderId = "kokoro-local-onnx";
const realProviderVersion = "1.0.0";
const realModelId = "onnx-community/Kokoro-82M-v1.0-ONNX";
const realModelVersion = "1.0";
const realModelPackageId = "kokoro-82m-v1.0-onnx-q8-af-heart";
const realModelPackageFingerprint =
  "03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0";
const realVoiceProfileId = "kokoro-local-voice-001";
const realVoiceProfileFingerprint =
  "dd81588a36a17b429e90ee9b21a80187c10368bab6bd5b8fa584ea01c455a210";
const realRightsRecordFingerprint =
  "e801171e684b1125b54bfc4317ae17dac4ca5b92c1500b82b333dc6da357c038";
const realVoiceTensorSha256 =
  "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b";
const governedCatalogRevisionId =
  "governed-local-voice-catalog-v2@2.0.0";
const governedCatalogFingerprint =
  "994a2f77daed881cc4e24201d628ef32a732aa6ee0ff0815745a19772d2828cc";
const governedInventoryFingerprint =
  "cb5657779b22d422cd7d8b9b81e09491aae1a82795e9e6af8a781c5f4c47c9bc";
const pronunciationTerm = "Harbor";
const supersedingPronunciation = "HAR-bore";
const generationTimeoutMs = 300_000;
const projectContextReadRetryLimit = 5;
const projectContextReadRetryDelayMs = 250;
const retryableProjectContextReadCodes = new Set([
  "PROJECT_CONTEXT_CHANGED",
  "PROJECT_CONTEXT_MISMATCH"
]);

type ProjectScopedReadAttempt<T> =
  | {
      readonly outcome: "succeeded";
      readonly value: T;
    }
  | {
      readonly outcome: "failed";
      readonly code: string;
      readonly message: string;
    };

const listeningScripts = [
  {
    rolePurpose: "narrator_candidate_1",
    roleType: "narrator",
    text: "The moonlit harbor rested beneath a calm evening sky."
  },
  {
    rolePurpose: "narrator_candidate_2",
    roleType: "narrator",
    text: "Beyond the breakwater, dawn revealed a narrow channel home."
  },
  {
    rolePurpose: "character_candidate_1",
    roleType: "character",
    text: "A lantern glowed beside the quiet water."
  },
  {
    rolePurpose: "character_candidate_2",
    roleType: "character",
    text: "We will meet beside the harbor after sunset."
  },
  {
    rolePurpose: "character_candidate_3",
    roleType: "character",
    text: "The watch changed beneath a copper moon."
  },
  {
    rolePurpose: "character_candidate_4",
    roleType: "character",
    text: "Keep the lantern near while I check the gate."
  }
] as const;

export interface Phase3b1LocalInputs {
  readonly modelPackageZip: string;
  readonly privateEvidenceRoot: string;
}

export interface Phase3b1PrivateClipEvidence {
  readonly opaqueFileName: string;
  readonly rolePurpose: (typeof listeningScripts)[number]["rolePurpose"];
  readonly roleId: string;
  readonly roleType: "narrator" | "character";
  readonly neutralDisplayLabel: string;
  readonly auditionSessionId: string;
  readonly auditionSessionRevision: number;
  readonly auditionClipId: string;
  readonly auditionClipRevision: number;
  readonly auditionClipFingerprint: string;
  readonly providerRequestId: string;
  readonly providerRequestFingerprint: string;
  readonly runtimeInstanceId: string | null;
  readonly executionClassification:
    | "provider_execution"
    | "verified_cache_lookup";
  readonly providerDispatchCount: 0 | 1;
  readonly sourceProviderRequestId: string | null;
  readonly voiceProfileId: string;
  readonly voiceProfileVersion: string;
  readonly voiceProfileFingerprint: string;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly modelId: string;
  readonly modelVersion: string;
  readonly modelPackageId: string;
  readonly modelPackageFingerprint: string;
  readonly voiceTensorSha256: string;
  readonly rightsRecordId: string;
  readonly rightsRecordRevision: number;
  readonly rightsRecordFingerprint: string;
  readonly rightsState: "restricted";
  readonly consentStatus: "unknown";
  readonly commercialUseClassification: "restricted";
  readonly redistributionClassification: "restricted";
  readonly acknowledgementId: string;
  readonly acknowledgementFingerprint: string;
  readonly castAssignmentId: string;
  readonly castAssignmentRevision: number;
  readonly approvedCastSnapshotId: string;
  readonly approvedCastSnapshotRevision: number;
  readonly approvedCastSnapshotFingerprint: string;
  readonly normalizedTextSha256: string;
  readonly pronunciationPlanFingerprint: string;
  readonly cacheKey: string;
  readonly cacheStatus: "miss" | "verified_hit" | "corrupt_miss";
  readonly audioArtifactId: string;
  readonly audioSha256: string;
  readonly byteSize: number;
  readonly durationMilliseconds: number;
  readonly sampleRateHz: 24000;
  readonly channels: 1;
  readonly codec: "pcm_s16le";
  readonly validWav: true;
  readonly nonSilent: true;
  readonly clippedSampleCount: 0;
  readonly blockingFindingCodes: readonly [];
  readonly warningCodes: readonly string[];
  readonly subjectiveQualityClaimed: false;
  readonly productionExportEligible: false;
  readonly humanListeningStatus: "pending";
}

export interface Phase3b1RealProductPathEvidence {
  readonly schemaVersion: 1;
  readonly evidenceClassification: "private_local_real_provider_product_path";
  readonly projectId: string;
  readonly inventory: {
    readonly inventoryId: string;
    readonly inventoryRevision: number;
    readonly inventoryFingerprint: string;
    readonly technicallyEligibleVoiceCount: 1;
    readonly restrictedVoiceCount: 1;
    readonly unknownRightsVoiceCount: 0;
    readonly prohibitedOrUnavailableVoiceCount: 0;
    readonly exactPackageVoiceCountLimitation: true;
  };
  readonly catalog: {
    readonly catalogRevisionId: typeof governedCatalogRevisionId;
    readonly catalogRevisionFingerprint: typeof governedCatalogFingerprint;
  };
  readonly model: {
    readonly modelPackageId: typeof realModelPackageId;
    readonly modelPackageFingerprint: typeof realModelPackageFingerprint;
    readonly installationId: string;
    readonly installationRevision: number;
    readonly installationStatus: "active";
    readonly active: true;
    readonly verificationId: string;
    readonly verificationFingerprint: string;
    readonly verificationStatus: "verified";
    readonly verifiedFileCount: 5;
    readonly verifiedByteSize: 92887010;
  };
  readonly assignment: Phase3b1RealVoiceAssignmentEvidence;
  readonly targetedInvalidation: {
    readonly probeClipId: string;
    readonly probeClipFingerprint: string;
    readonly priorDictionaryFingerprint: string;
    readonly currentDictionaryFingerprint: string;
    readonly supersedingEntryId: string;
    readonly invalidatedClipCount: number;
    readonly invalidatedProbeClip: true;
    readonly invalidatedClipIdsTruncated: false;
  };
  readonly auditions: readonly Phase3b1PrivateClipEvidence[];
  readonly cache: {
    readonly verifiedHitProven: true;
    readonly sourceClipId: string;
    readonly repeatedClipId: string;
    readonly sourceProviderRequestId: string;
    readonly repeatedProviderRequestId: string;
    readonly sourceRuntimeInstanceId: string;
    readonly sourceExecutionClassification: "provider_execution";
    readonly sourceProviderDispatchCount: 1;
    readonly repeatedExecutionClassification: "verified_cache_lookup";
    readonly repeatedProviderDispatchCount: 0;
    readonly cacheKey: string;
    readonly artifactSha256: string;
  };
  readonly authenticatedDesktopPlayback: true;
  readonly humanListening: {
    readonly state: "pending";
    readonly decisionCount: 0;
    readonly claimed: false;
    readonly voiceReadinessApproved: false;
  };
  readonly productionExportEligible: false;
  readonly humanPerceivedQualityClaimed: false;
  readonly realSynthesisProven: true;
  readonly externalNetworkObservationScope:
    "owned_service_and_provider_worker_tcp_endpoint_inventory";
}

export interface Phase3b1ListeningPackageStaging {
  readonly stagingDirectory: string;
  readonly finalDirectory: string;
  readonly directoryName: string;
  readonly replayStateDirectory: string;
  readonly replayStateDirectoryName: string;
  readonly replayLauncherFileName: "replay-private-listening.ps1";
  readonly indexSha256: string;
  readonly scorecardSha256: string;
}

export interface Phase3b1RealProductPathWorkflow {
  readonly evidence: Phase3b1RealProductPathEvidence;
  readonly listeningPackage: Phase3b1ListeningPackageStaging;
  readonly liveRuntimeInstance: SpeechRuntimeInstance;
}

export interface Phase3b1RestartEvidence {
  readonly projectId: string;
  readonly restoredClipIds: readonly string[];
  readonly restoredClipFingerprints: readonly string[];
  readonly authenticatedAudioRestored: true;
  readonly realVoiceInventoryRestored: true;
  readonly exactActivationBindingsRestored: true;
  readonly humanListeningState: "pending";
  readonly voiceReadinessApproved: false;
  readonly productionExportEligible: false;
}

export async function requirePhase3b1LocalInputs(
  modelPackageZipValue: string,
  privateEvidenceRootValue: string
): Promise<Phase3b1LocalInputs> {
  const modelPackageZip = path.resolve(modelPackageZipValue);
  const privateEvidenceRoot = path.resolve(privateEvidenceRootValue);
  if (!samePath(modelPackageZip, expectedModelPackageZip)) {
    throw new Error(
      `${phase3b1RealModelPackageZipEnvironment} must identify the repository-owned ignored Kokoro package ZIP.`
    );
  }
  if (!samePath(privateEvidenceRoot, expectedPrivateEvidenceRoot)) {
    throw new Error(
      `${phase3b1PrivateEvidenceRootEnvironment} must identify the repository-owned ignored Phase 3B.1 render root.`
    );
  }
  const privateEvidenceParent = path.join(repositoryRoot, "local-renders");
  const [
    zipMetadata,
    canonicalZip,
    canonicalPackageRoot,
    privateParentMetadata,
    canonicalPrivateParent
  ] = await Promise.all([
    lstat(modelPackageZip),
    realpath(modelPackageZip),
    realpath(path.join(repositoryRoot, "local-models")),
    lstat(privateEvidenceParent),
    realpath(privateEvidenceParent)
  ]);
  if (!zipMetadata.isFile() || zipMetadata.isSymbolicLink()) {
    throw new Error("The exact local Kokoro model package ZIP must be a regular file.");
  }
  const relativeZip = path.relative(canonicalPackageRoot, canonicalZip);
  if (
    relativeZip.length === 0 ||
    relativeZip === ".." ||
    relativeZip.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relativeZip)
  ) {
    throw new Error("The local Kokoro ZIP escaped the ignored model-package root.");
  }
  if (
    !privateParentMetadata.isDirectory() ||
    privateParentMetadata.isSymbolicLink() ||
    !samePath(canonicalPrivateParent, privateEvidenceParent)
  ) {
    throw new Error("The ignored private-evidence parent was not canonical.");
  }
  try {
    const [privateRootMetadata, canonicalPrivateRoot] = await Promise.all([
      lstat(privateEvidenceRoot),
      realpath(privateEvidenceRoot)
    ]);
    if (
      !privateRootMetadata.isDirectory() ||
      privateRootMetadata.isSymbolicLink() ||
      !samePath(canonicalPrivateRoot, privateEvidenceRoot) ||
      !isStrictChild(canonicalPrivateParent, canonicalPrivateRoot)
    ) {
      throw new Error("The ignored private-evidence root was not canonical.");
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  return {
    modelPackageZip: canonicalZip,
    privateEvidenceRoot: path.join(
      canonicalPrivateParent,
      path.basename(expectedPrivateEvidenceRoot)
    )
  };
}

export async function runPhase3b1RealProductPathWorkflow(
  page: Page,
  application: ElectronApplication,
  inputs: Phase3b1LocalInputs
): Promise<Phase3b1RealProductPathWorkflow> {
  await openAuditions(page);
  const projectId = await activeProjectId(page);
  let workspace = await readWorkspace(page, projectId);
  const inventoryRecord = assertGovernedInventory(workspace);
  await expect(
    page.getByRole("heading", { name: "Real Local Voices", exact: true })
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(GOVERNED_PRIVATE_AUDITION_WARNING, { exact: true }))
    .toBeVisible({ timeout: 30_000 });

  const { manifest, installation, verification } =
    await installVerifyAndActivateExactPackage(
      page,
      application,
      projectId,
      inputs.modelPackageZip
    );
  const assignment = await assignPhase3b1RealVoiceForPrivateAuditions(page);
  await openAuditions(page);
  workspace = await readWorkspace(page, projectId);
  assertGovernedInventory(workspace);

  const narrator = requiredRealRole(
    workspace,
    assignment.narratorRoleId,
    "narrator"
  );
  requiredRealRole(
    workspace,
    assignment.characterRoleId,
    "character"
  );

  const probe = await generateRealClip(
    page,
    projectId,
    narrator,
    "Harbor lanterns glowed beside the quiet water.",
    "phase3b1-invalidation-probe",
    workspace,
    inventoryRecord
  );
  const targetedInvalidation = await supersedeProbePronunciation(
    page,
    projectId,
    probe.clip
  );

  const generated: Array<{
    readonly rolePurpose: (typeof listeningScripts)[number]["rolePurpose"];
    readonly role: AuditionRoleStatus;
    readonly session: AuditionSession;
    readonly clip: AuditionClip;
    readonly providerRequest: SpeechProviderRequest;
    readonly bytes: Uint8Array;
  }> = [];
  for (const [index, script] of listeningScripts.entries()) {
    workspace = await readWorkspace(page, projectId);
    const role = requiredRealRole(
      workspace,
      script.roleType === "narrator"
        ? assignment.narratorRoleId
        : assignment.characterRoleId,
      script.roleType
    );
    const result = await generateRealClip(
      page,
      projectId,
      role,
      script.text,
      `phase3b1-listening-${String(index + 1).padStart(2, "0")}`,
      workspace,
      inventoryRecord
    );
    generated.push({ rolePurpose: script.rolePurpose, role, ...result });
  }

  if (generated.length !== 6) {
    throw new Error("The bounded private listening set did not contain six clips.");
  }
  const firstNarrator = generated[0];
  const secondNarrator = generated[1];
  if (
    firstNarrator === undefined ||
    secondNarrator === undefined ||
    firstNarrator.clip.normalizedTextSha256 ===
      secondNarrator.clip.normalizedTextSha256 ||
    firstNarrator.clip.cacheKey === secondNarrator.clip.cacheKey
  ) {
    throw new Error(
      "The private listening package did not contain two distinct narrator samples."
    );
  }
  workspace = await readWorkspace(page, projectId);
  const cacheRepeat = await generateRealClip(
    page,
    projectId,
    requiredRealRole(workspace, assignment.narratorRoleId, "narrator"),
    listeningScripts[0].text,
    "phase3b1-cache-repeat",
    workspace,
    inventoryRecord
  );
  const firstNarratorExecution =
    firstNarrator?.providerRequest.provenance.details;
  const repeatedNarratorExecution = cacheRepeat.providerRequest.provenance.details;
  if (
    firstNarrator === undefined ||
    firstNarrator.clip.cacheStatus === "verified_hit" ||
    cacheRepeat.clip.cacheStatus !== "verified_hit" ||
    cacheRepeat.clip.cacheKey !== firstNarrator.clip.cacheKey ||
    cacheRepeat.clip.audioArtifact.sha256 !==
      firstNarrator.clip.audioArtifact.sha256 ||
    firstNarratorExecution?.executionClassification !== "provider_execution" ||
    firstNarratorExecution.providerDispatchCount !== 1 ||
    firstNarrator.providerRequest.runtimeInstanceId === null ||
    repeatedNarratorExecution?.executionClassification !==
      "verified_cache_lookup" ||
    repeatedNarratorExecution.providerDispatchCount !== 0 ||
    repeatedNarratorExecution.sourceProviderRequestId !==
      firstNarrator.providerRequest.providerRequestId ||
    cacheRepeat.providerRequest.runtimeInstanceId !== null
  ) {
    throw new Error("The exact real-provider cache-hit behavior was not proven.");
  }

  workspace = await readWorkspace(page, projectId);
  const allRealProviderRequests = [
    probe.providerRequest,
    ...generated.map((item) => item.providerRequest),
    cacheRepeat.providerRequest
  ];
  const dispatchedRealProviderRequests = allRealProviderRequests.filter(
    (item) =>
      item.provenance.details.executionClassification === "provider_execution" &&
      item.provenance.details.providerDispatchCount === 1
  );
  if (
    allRealProviderRequests.length !== 8 ||
    dispatchedRealProviderRequests.length !== 7 ||
    allRealProviderRequests.filter(
      (item) =>
        item.provenance.details.executionClassification ===
          "verified_cache_lookup" &&
        item.provenance.details.providerDispatchCount === 0 &&
        item.runtimeInstanceId === null
    ).length !== 1 ||
    dispatchedRealProviderRequests.some((item) => item.runtimeInstanceId === null)
  ) {
    throw new Error(
      "Every real-provider request must identify its exact owned runtime instance."
    );
  }
  const runtimeInstanceIds = new Set(
    dispatchedRealProviderRequests.map((item) => item.runtimeInstanceId as string)
  );
  if (runtimeInstanceIds.size !== 1) {
    throw new Error(
      "The bounded real-provider run unexpectedly used more than one worker instance."
    );
  }
  const liveRuntimeInstance = workspace.runtimeInstances.find(
    (item) =>
      runtimeInstanceIds.has(item.runtimeInstanceId) &&
      ["ready", "busy", "idle"].includes(item.state)
  );
  if (
    liveRuntimeInstance === undefined ||
    liveRuntimeInstance.providerId !== realProviderId ||
    liveRuntimeInstance.handshakeAuthenticated !== true ||
    liveRuntimeInstance.jobObjectAssigned !== true ||
    liveRuntimeInstance.networkPolicy !== "python_socket_api_denied" ||
    liveRuntimeInstance.deniedNetworkAttemptCount !== 0 ||
    liveRuntimeInstance.stoppedAt !== null ||
    liveRuntimeInstance.restartReconciliation !== null
  ) {
    throw new Error("The exact governed Kokoro worker instance was not live and owned.");
  }

  await proveComparisonPlaybackUi(page);
  workspace = await readWorkspace(page, projectId);
  assertHumanListeningPending(workspace, assignment);

  const auditionEvidence = generated.map((item, index) =>
    buildClipEvidence(
      item.rolePurpose,
      item.role,
      item.session,
      item.clip,
      item.providerRequest,
      inventoryRecord,
      `clip-${String(index + 1).padStart(2, "0")}.wav`
    )
  );
  const evidence: Phase3b1RealProductPathEvidence = {
    schemaVersion: 1,
    evidenceClassification: "private_local_real_provider_product_path",
    projectId,
    inventory: {
      inventoryId: requiredInventory(workspace).inventoryId,
      inventoryRevision: requiredInventory(workspace).inventoryRevision,
      inventoryFingerprint: requiredInventory(workspace).inventoryFingerprint,
      technicallyEligibleVoiceCount: 1,
      restrictedVoiceCount: 1,
      unknownRightsVoiceCount: 0,
      prohibitedOrUnavailableVoiceCount: 0,
      exactPackageVoiceCountLimitation: true
    },
    catalog: {
      catalogRevisionId: governedCatalogRevisionId,
      catalogRevisionFingerprint: governedCatalogFingerprint
    },
    model: buildModelEvidence(manifest, installation, verification),
    assignment,
    targetedInvalidation,
    auditions: auditionEvidence,
    cache: {
      verifiedHitProven: true,
      sourceClipId: firstNarrator.clip.auditionClipId,
      repeatedClipId: cacheRepeat.clip.auditionClipId,
      sourceProviderRequestId: firstNarrator.providerRequest.providerRequestId,
      repeatedProviderRequestId: cacheRepeat.providerRequest.providerRequestId,
      sourceRuntimeInstanceId: firstNarrator.providerRequest.runtimeInstanceId,
      sourceExecutionClassification: "provider_execution",
      sourceProviderDispatchCount: 1,
      repeatedExecutionClassification: "verified_cache_lookup",
      repeatedProviderDispatchCount: 0,
      cacheKey: firstNarrator.clip.cacheKey,
      artifactSha256: firstNarrator.clip.audioArtifact.sha256
    },
    authenticatedDesktopPlayback: true,
    humanListening: {
      state: "pending",
      decisionCount: 0,
      claimed: false,
      voiceReadinessApproved: false
    },
    productionExportEligible: false,
    humanPerceivedQualityClaimed: false,
    realSynthesisProven: true,
    externalNetworkObservationScope:
      "owned_service_and_provider_worker_tcp_endpoint_inventory"
  };
  const listeningPackage = await stagePrivateListeningPackage(
    inputs.privateEvidenceRoot,
    evidence,
    generated.map((item) => item.bytes)
  );
  return { evidence, listeningPackage, liveRuntimeInstance };
}

export async function provePhase3b1RestartPersistence(
  page: Page,
  expected: Phase3b1RealProductPathEvidence
): Promise<Phase3b1RestartEvidence> {
  await openAuditions(page);
  const projectId = await activeProjectId(page);
  if (projectId !== expected.projectId) {
    throw new Error("The Phase 3B.1 project identity did not persist across restart.");
  }
  const workspace = await readWorkspace(page, projectId);
  assertGovernedInventory(workspace);
  const clips = await listClips(page, projectId);
  for (const expectedClip of expected.auditions) {
    const restored = clips.find(
      (item) => item.auditionClipId === expectedClip.auditionClipId
    );
    if (
      restored === undefined ||
      restored.clipFingerprint !== expectedClip.auditionClipFingerprint ||
      restored.audioArtifact.sha256 !== expectedClip.audioSha256 ||
      restored.governedLocalVoiceActivation?.bindingFingerprint === undefined
    ) {
      throw new Error(
        `The governed real clip ${expectedClip.auditionClipId} did not restore exactly.`
      );
    }
    await loadAuthenticatedAudio(page, projectId, restored);
  }
  assertHumanListeningPending(workspace, expected.assignment);
  await proveComparisonPlaybackUi(page);
  return {
    projectId,
    restoredClipIds: expected.auditions.map((item) => item.auditionClipId),
    restoredClipFingerprints: expected.auditions.map(
      (item) => item.auditionClipFingerprint
    ),
    authenticatedAudioRestored: true,
    realVoiceInventoryRestored: true,
    exactActivationBindingsRestored: true,
    humanListeningState: "pending",
    voiceReadinessApproved: false,
    productionExportEligible: false
  };
}

export async function completePrivateListeningPackage(
  staging: Phase3b1ListeningPackageStaging,
  evidence: Readonly<Record<string, unknown>>
): Promise<string> {
  await assertCanonicalPrivateStaging(staging);
  await writeExclusiveJson(
    path.join(staging.stagingDirectory, "product-path-evidence.json"),
    evidence
  );
  await rename(staging.stagingDirectory, staging.finalDirectory);
  return staging.finalDirectory;
}

export async function preservePhase3b1PrivateReplayState(
  isolationRootValue: string,
  staging: Phase3b1ListeningPackageStaging,
  packagedVersion: string
): Promise<void> {
  await assertCanonicalPrivateStaging(staging);
  const isolationRoot = path.resolve(isolationRootValue);
  if (
    !/^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$/u.test(packagedVersion) ||
    path.basename(isolationRoot).startsWith("css-packaged-e2e-") === false
  ) {
    throw new Error("The private replay state inputs were invalid.");
  }
  const [rootMetadata, appDataMetadata, localAppDataMetadata] = await Promise.all([
    lstat(isolationRoot),
    lstat(path.join(isolationRoot, "AppData")),
    lstat(path.join(isolationRoot, "LocalAppData"))
  ]);
  if (
    !rootMetadata.isDirectory() ||
    rootMetadata.isSymbolicLink() ||
    !appDataMetadata.isDirectory() ||
    appDataMetadata.isSymbolicLink() ||
    !localAppDataMetadata.isDirectory() ||
    localAppDataMetadata.isSymbolicLink()
  ) {
    throw new Error("The private replay state was not an owned directory tree.");
  }
  await mkdir(staging.replayStateDirectory, { recursive: false });
  await rename(
    path.join(isolationRoot, "AppData"),
    path.join(staging.replayStateDirectory, "AppData")
  );
  await rename(
    path.join(isolationRoot, "LocalAppData"),
    path.join(staging.replayStateDirectory, "LocalAppData")
  );
  await mkdir(path.join(staging.replayStateDirectory, "Temp"), {
    recursive: false
  });
  const launcher = `$ErrorActionPreference = "Stop"
$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $packageRoot "..\\..\\..")).Path
$stateRoot = (Resolve-Path -LiteralPath (Join-Path $packageRoot "..\\${staging.replayStateDirectoryName}")).Path
$executable = Join-Path $repositoryRoot "apps\\desktop\\release\\${packagedVersion}\\win-unpacked\\Cinematic Story Studio.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "The exact packaged executable is unavailable." }
$env:APPDATA = Join-Path $stateRoot "AppData"
$env:LOCALAPPDATA = Join-Path $stateRoot "LocalAppData"
$env:TEMP = Join-Path $stateRoot "Temp"
$env:TMP = $env:TEMP
& $executable
`;
  await writeFile(
    path.join(staging.stagingDirectory, staging.replayLauncherFileName),
    launcher,
    { encoding: "utf8", flag: "wx" }
  );
}

async function installVerifyAndActivateExactPackage(
  page: Page,
  application: ElectronApplication,
  projectId: string,
  modelPackageZip: string
): Promise<{
  readonly manifest: ModelPackageManifest;
  readonly installation: ModelInstallationRecord;
  readonly verification: ModelVerificationRecord;
}> {
  const before = await listModelPackages(page, projectId);
  const exact = before.find(
    (item) =>
      item.manifest.modelPackageId === realModelPackageId &&
      item.manifest.manifestFingerprint === realModelPackageFingerprint
  );
  if (exact === undefined) {
    throw new Error("The exact allow-listed Kokoro package manifest was unavailable.");
  }
  if (exact.installation === null || exact.installation.status === "removed") {
    const modelRow = await findExactModelPackageRow(
      page,
      realModelId,
      realModelPackageFingerprint
    );
    const installButton = modelRow.getByRole("button", {
      name: "Choose ZIP & install",
      exact: true
    });
    await expect(installButton).toBeVisible({ timeout: 30_000 });
    const installControls = page.getByRole("group", {
      name: "Local model ZIP install & repair",
      exact: true
    });
    const restrictedUseAcknowledgement = installControls.getByRole(
      "checkbox",
      {
        name: /model and voice bytes may have restricted or unknown usage rights/u
      }
    );
    const installReason = installControls.getByRole("textbox", {
      name: "Install or repair reason",
      exact: true
    });
    await expect(restrictedUseAcknowledgement).toBeVisible({ timeout: 30_000 });
    await expect(installReason).toBeVisible({ timeout: 30_000 });
    await application.evaluate(({ dialog }, selectedPackage) => {
      dialog.showOpenDialog = () =>
        Promise.resolve({
          canceled: false,
          filePaths: [selectedPackage],
          bookmarks: []
        });
    }, modelPackageZip);
    await restrictedUseAcknowledgement.check();
    await installReason.fill(
      "Install the exact allow-listed package for a bounded private local audition."
    );
    await expect(installReason).toHaveValue(
      "Install the exact allow-listed package for a bounded private local audition."
    );
    await expect(installButton).toBeEnabled({ timeout: 30_000 });
    await installButton.click();
    await expect(
      page.getByText(
        "The local model install completed from verified ZIP bytes.",
        { exact: true }
      )
    ).toBeVisible({ timeout: 300_000 });
  }

  let current = requiredModelPackage(
    await listModelPackages(page, projectId),
    realModelPackageId
  );
  if (current.verification?.status !== "verified") {
    current = await performModelAction(
      page,
      projectId,
      current,
      "verify",
      "phase3b1-real-model-verify"
    );
  }
  if (current.verification?.status !== "verified") {
    throw new Error("The exact local Kokoro package did not verify.");
  }
  if (current.installation?.active !== true) {
    current = await performModelAction(
      page,
      projectId,
      current,
      "activate",
      "phase3b1-real-model-activate"
    );
  }
  if (
    current.installation === null ||
    current.installation.status !== "active" ||
    current.installation.active !== true ||
    current.verification?.status !== "verified"
  ) {
    throw new Error("The exact local Kokoro package was not active and verified.");
  }
  return {
    manifest: current.manifest,
    installation: current.installation,
    verification: current.verification
  };
}

async function findExactModelPackageRow(
  page: Page,
  modelId: string,
  manifestFingerprint: string
): Promise<Locator> {
  const modelTable = page.getByRole("table", {
    name: "Installed model packages and verification",
    exact: true
  });
  await expect(modelTable).toBeVisible({ timeout: 30_000 });
  await expect
    .poll(() => modelTable.locator("tbody tr").count(), {
      message: "Wait for the first bounded model-package page.",
      timeout: 120_000
    })
    .toBeGreaterThan(0);

  const pager = page.getByLabel("Model Packages pagination", {
    exact: true
  });
  await expect(pager).toBeVisible({ timeout: 30_000 });
  for (let pageIndex = 0; pageIndex < 200; pageIndex += 1) {
    const matchingRows = modelTable
      .locator("tbody tr")
      .filter({
        has: page.getByText(modelId, { exact: true })
      })
      .filter({
        has: page.getByTitle(manifestFingerprint, { exact: true })
      });
    const matchingCount = await matchingRows.count();
    if (matchingCount === 1) {
      return matchingRows;
    }
    if (matchingCount > 1) {
      throw new Error(
        "The governed model-package table rendered a duplicate exact package identity."
      );
    }

    const next = pager.getByRole("button", {
      name: "Next model packages",
      exact: true
    });
    if (await next.isDisabled()) {
      break;
    }
    const priorPageSummary = await pager.locator("span").innerText();
    await next.click();
    await expect
      .poll(() => pager.locator("span").innerText(), {
        message: "Wait for the next bounded model-package page.",
        timeout: 30_000
      })
      .not.toBe(priorPageSummary);
    await expect
      .poll(() => modelTable.locator("tbody tr").count(), {
        timeout: 30_000
      })
      .toBeGreaterThan(0);
  }
  throw new Error(
    "The exact governed model-package row was unavailable through bounded renderer pagination."
  );
}

async function performModelAction(
  page: Page,
  projectId: string,
  item: Awaited<ReturnType<typeof listModelPackages>>[number],
  action: "verify" | "activate",
  idempotencyKey: string
) {
  const response = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.performModelPackageAction(input);
    if (!result.ok) {
      throw new Error(
        `Model ${input.action} failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value;
  }, {
    projectId,
    modelPackageId: item.manifest.modelPackageId,
    expectedManifestFingerprint: item.manifest.manifestFingerprint,
    expectedInstallationRevision:
      item.installation?.installationRevision ?? null,
    action,
    reason: `Perform the exact governed ${action} operation for the bounded private audition.`,
    idempotencyKey
  });
  return {
    manifest: item.manifest,
    installation: response.installation,
    verification: response.verification
  };
}

async function generateRealClip(
  page: Page,
  projectId: string,
  role: AuditionRoleStatus,
  text: string,
  idempotencyPrefix: string,
  workspace: AuditionWorkspaceSnapshot,
  inventoryRecord: GovernedLocalVoiceInventoryRecord
): Promise<{
  readonly session: AuditionSession;
  readonly clip: AuditionClip;
  readonly providerRequest: SpeechProviderRequest;
  readonly bytes: Uint8Array;
}> {
  if (role.sessionEvidence === null) {
    throw new Error(`The current ${role.roleType} session evidence was unavailable.`);
  }
  const inventory = requiredInventory(workspace);
  const session = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.createSession(input);
    if (!result.ok) {
      throw new Error(
        `Real audition session failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value.session;
  }, {
    projectId,
    roleId: role.roleId,
    evidence: role.sessionEvidence,
    restrictedLocalAuditionActivation: {
      expectedInventoryFingerprint: inventory.inventoryFingerprint,
      expectedWarningFingerprint: inventory.warningFingerprint,
      reason: "Authorize this exact restricted voice for one bounded private local audition."
    },
    idempotencyKey: `${idempotencyPrefix}-session`
  });
  if (
    session.governedLocalVoiceActivation?.acknowledgement.inventoryRecordId !==
      inventoryRecord.inventoryRecordId ||
    session.governedLocalVoiceActivation.productionExportEligible !== false
  ) {
    throw new Error("The real audition session lacked its exact restricted activation binding.");
  }
  await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.createScript(input);
    if (!result.ok) {
      throw new Error(
        `Real audition script failed: ${result.error.code}: ${result.error.message}`
      );
    }
  }, {
    projectId,
    auditionSessionId: session.auditionSessionId,
    expectedSessionRevision: session.revision,
    kind: "standardized_synthetic" as const,
    text,
    sourceDocumentId: null,
    sourceRevision: null,
    sourceSpan: null,
    sourceTextSha256: sha256(text),
    acceptedOptionalNormalizationIds: [],
    idempotencyKey: `${idempotencyPrefix}-script`
  });
  const refreshed = await readWorkspace(page, projectId);
  const currentRole = requiredRealRole(refreshed, role.roleId, role.roleType);
  if (currentRole.generationRequest === null) {
    throw new Error("The server-issued real-provider generation request was unavailable.");
  }
  const queued = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.generate(input);
    if (!result.ok) {
      throw new Error(
        `Real audition generation failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value;
  }, { projectId, preview: currentRole.generationRequest });
  await waitForAuditionJob(page, queued.jobId);
  const completed = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.generate(input);
    if (!result.ok) {
      throw new Error(
        `Real audition result replay failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value;
  }, { projectId, preview: currentRole.generationRequest });
  if (
    completed.jobId !== queued.jobId ||
    completed.providerRequest.state !== "succeeded" ||
    completed.providerRequest.providerId !== realProviderId
  ) {
    throw new Error("The real provider request did not reach its exact terminal success.");
  }
  const clips = await listClips(page, projectId, session.auditionSessionId);
  const clip = clips.find(
    (item) =>
      item.providerRequestId === completed.providerRequest.providerRequestId
  );
  if (clip === undefined) {
    throw new Error("The atomically published real-provider clip was unavailable.");
  }
  assertRealClipBindings(
    role,
    session,
    clip,
    completed.providerRequest,
    inventoryRecord
  );
  const bytes = await loadAuthenticatedAudio(page, projectId, clip);
  return { session, clip, providerRequest: completed.providerRequest, bytes };
}

async function supersedeProbePronunciation(
  page: Page,
  projectId: string,
  probeClip: AuditionClip
): Promise<Phase3b1RealProductPathEvidence["targetedInvalidation"]> {
  const workspace = await readWorkspace(page, projectId);
  const dictionary = requiredDictionary(workspace.currentDictionary);
  const entries = await listPronunciations(page, projectId, dictionary);
  const currentEntry = entries.find(
    (item) =>
      item.writtenForm === pronunciationTerm &&
      item.verificationState === "approved" &&
      item.supersededByEntryId === null
  );
  if (currentEntry === undefined) {
    throw new Error("The current repository-owned Harbor pronunciation was unavailable.");
  }
  const pending = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.appendPronunciation(input);
    if (!result.ok) {
      throw new Error(
        `Pronunciation supersession failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value;
  }, {
    projectId,
    expectedDictionaryRevision: dictionary.revision,
    expectedDictionaryFingerprint: dictionary.dictionaryFingerprint,
    writtenForm: pronunciationTerm,
    language: "en",
    locale: null,
    scope: "project" as const,
    scopeId: null,
    representation: "provider_neutral" as const,
    pronunciation: supersedingPronunciation,
    ipa: null,
    providerId: null,
    providerCompiledValue: null,
    caseSensitive: false,
    matchRule: "whole_word" as const,
    priority: 100,
    reason: "Supersede the synthetic pronunciation to prove targeted real-clip invalidation.",
    supersedesEntryId: currentEntry.entryId,
    idempotencyKey: "phase3b1-targeted-pronunciation-supersession"
  });
  const approved = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.decidePronunciation(input);
    if (!result.ok) {
      throw new Error(
        `Pronunciation approval failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value;
  }, {
    projectId,
    entryId: pending.entry.entryId,
    expectedEntryRevision: pending.entry.revision,
    expectedEntryFingerprint: pending.entry.entryFingerprint,
    expectedDictionaryRevision: pending.dictionary.revision,
    expectedDictionaryFingerprint: pending.dictionary.dictionaryFingerprint,
    decision: "approve" as const,
    rationale: "Approve the reviewed synthetic pronunciation solely for local invalidation evidence.",
    idempotencyKey: "phase3b1-targeted-pronunciation-approval"
  });
  if (
    approved.entry.verificationState !== "approved" ||
    !approved.invalidatedClipIds.includes(probeClip.auditionClipId) ||
    approved.invalidatedClipIdsTruncated ||
    approved.invalidatedClipCount !== approved.invalidatedClipIds.length
  ) {
    throw new Error("The exact real-provider probe clip was not targeted for invalidation.");
  }
  return {
    probeClipId: probeClip.auditionClipId,
    probeClipFingerprint: probeClip.clipFingerprint,
    priorDictionaryFingerprint: dictionary.dictionaryFingerprint,
    currentDictionaryFingerprint: approved.dictionary.dictionaryFingerprint,
    supersedingEntryId: approved.entry.entryId,
    invalidatedClipCount: approved.invalidatedClipCount,
    invalidatedProbeClip: true,
    invalidatedClipIdsTruncated: false
  };
}

function assertRealClipBindings(
  role: AuditionRoleStatus,
  session: AuditionSession,
  clip: AuditionClip,
  providerRequest: SpeechProviderRequest,
  inventoryRecord: GovernedLocalVoiceInventoryRecord
): void {
  const activation = clip.governedLocalVoiceActivation;
  if (
    activation === null ||
    activation === undefined ||
    session.governedLocalVoiceActivation?.bindingFingerprint !==
      activation.bindingFingerprint ||
    activation.acknowledgement.warningText !== GOVERNED_PRIVATE_AUDITION_WARNING ||
    activation.acknowledgement.warningFingerprint !==
      GOVERNED_PRIVATE_AUDITION_WARNING_SHA256 ||
    activation.acknowledgement.inventoryRecordId !==
      inventoryRecord.inventoryRecordId ||
    activation.acknowledgement.voiceTensorSha256 !== realVoiceTensorSha256 ||
    activation.acknowledgement.rightsRecordFingerprint !==
      realRightsRecordFingerprint ||
    activation.acknowledgement.productionExportAuthorized !== false ||
    activation.productionExportEligible !== false ||
    clip.providerClass !== "real_local" ||
    clip.providerId !== realProviderId ||
    clip.providerVersion !== realProviderVersion ||
    clip.modelId !== realModelId ||
    clip.modelVersion !== realModelVersion ||
    clip.modelPackageFingerprint !== realModelPackageFingerprint ||
    clip.castAssignmentId !== role.assignmentId ||
    clip.castAssignmentRevision !== role.assignmentRevision ||
    clip.audioArtifact.mediaType !== "audio/wav" ||
    clip.audioArtifact.codec !== "pcm_s16le" ||
    clip.audioArtifact.sampleRateHz !== 24_000 ||
    clip.audioArtifact.channels !== 1 ||
    clip.audioArtifact.sampleWidthBytes !== 2 ||
    clip.audioArtifact.byteSize <= 44 ||
    clip.audioArtifact.frameCount <= 0 ||
    clip.audioArtifact.durationMilliseconds <= 0 ||
    clip.audioArtifact.availability !== "present" ||
    !clip.audioArtifact.playbackEligible ||
    !clip.audioQuality.validWav ||
    !clip.audioQuality.nonSilent ||
    clip.audioQuality.clippedSampleCount !== 0 ||
    clip.audioQuality.blockingFindingCodes.length !== 0 ||
    clip.audioQuality.subjectiveQualityClaimed !== false ||
    clip.productionExportEligible !== false ||
    providerRequest.state !== "succeeded" ||
    providerRequest.providerId !== clip.providerId ||
    providerRequest.providerVersion !== clip.providerVersion ||
    providerRequest.modelId !== clip.modelId ||
    providerRequest.modelVersion !== clip.modelVersion ||
    providerRequest.modelPackageFingerprint !== clip.modelPackageFingerprint ||
    providerRequest.voiceProfileId !== realVoiceProfileId ||
    providerRequest.voiceProfileVersion !== inventoryRecord.voiceProfileVersion ||
    providerRequest.providerVoiceId !== inventoryRecord.providerVoiceId ||
    providerRequest.castAssignmentId !== clip.castAssignmentId ||
    providerRequest.castAssignmentRevision !== clip.castAssignmentRevision ||
    providerRequest.normalizedTextSha256 !== clip.normalizedTextSha256 ||
    providerRequest.pronunciationPlanFingerprint !==
      clip.pronunciationPlanFingerprint ||
    providerRequest.cacheKey !== clip.cacheKey
  ) {
    throw new Error("The real-provider clip did not preserve its exact governed chain.");
  }
}

function buildClipEvidence(
  rolePurpose: Phase3b1PrivateClipEvidence["rolePurpose"],
  role: AuditionRoleStatus,
  session: AuditionSession,
  clip: AuditionClip,
  providerRequest: SpeechProviderRequest,
  inventoryRecord: GovernedLocalVoiceInventoryRecord,
  opaqueFileName: string
): Phase3b1PrivateClipEvidence {
  const activation = clip.governedLocalVoiceActivation;
  if (activation === null || activation === undefined) {
    throw new Error("The governed activation binding was unavailable for evidence.");
  }
  return {
    opaqueFileName,
    rolePurpose,
    roleId: role.roleId,
    roleType: role.roleType,
    neutralDisplayLabel: inventoryRecord.neutralDisplayLabel,
    auditionSessionId: session.auditionSessionId,
    auditionSessionRevision: session.revision,
    auditionClipId: clip.auditionClipId,
    auditionClipRevision: clip.revision,
    auditionClipFingerprint: clip.clipFingerprint,
    providerRequestId: providerRequest.providerRequestId,
    providerRequestFingerprint: providerRequest.requestFingerprint,
    runtimeInstanceId: providerRequest.runtimeInstanceId,
    executionClassification:
      providerRequest.provenance.details.executionClassification,
    providerDispatchCount:
      providerRequest.provenance.details.providerDispatchCount,
    sourceProviderRequestId:
      providerRequest.provenance.details.sourceProviderRequestId ?? null,
    voiceProfileId: inventoryRecord.voiceProfileId,
    voiceProfileVersion: inventoryRecord.voiceProfileVersion,
    voiceProfileFingerprint: inventoryRecord.voiceProfileFingerprint,
    providerId: clip.providerId,
    providerVersion: clip.providerVersion,
    modelId: clip.modelId,
    modelVersion: clip.modelVersion,
    modelPackageId: inventoryRecord.modelPackageId,
    modelPackageFingerprint: clip.modelPackageFingerprint,
    voiceTensorSha256: inventoryRecord.voiceTensor.sha256,
    rightsRecordId: activation.acknowledgement.rightsRecordId,
    rightsRecordRevision: activation.acknowledgement.rightsRecordRevision,
    rightsRecordFingerprint:
      activation.acknowledgement.rightsRecordFingerprint,
    rightsState: "restricted",
    consentStatus: "unknown",
    commercialUseClassification: "restricted",
    redistributionClassification: "restricted",
    acknowledgementId: activation.acknowledgement.acknowledgementId,
    acknowledgementFingerprint:
      activation.acknowledgement.acknowledgementFingerprint,
    castAssignmentId: clip.castAssignmentId,
    castAssignmentRevision: clip.castAssignmentRevision,
    approvedCastSnapshotId: activation.approvedCastSnapshotId,
    approvedCastSnapshotRevision: activation.approvedCastSnapshotRevision,
    approvedCastSnapshotFingerprint:
      activation.approvedCastSnapshotFingerprint,
    normalizedTextSha256: clip.normalizedTextSha256,
    pronunciationPlanFingerprint: clip.pronunciationPlanFingerprint,
    cacheKey: clip.cacheKey,
    cacheStatus: clip.cacheStatus,
    audioArtifactId: clip.audioArtifact.audioArtifactId,
    audioSha256: clip.audioArtifact.sha256,
    byteSize: clip.audioArtifact.byteSize,
    durationMilliseconds: clip.audioArtifact.durationMilliseconds,
    sampleRateHz: 24_000,
    channels: 1,
    codec: "pcm_s16le",
    validWav: true,
    nonSilent: true,
    clippedSampleCount: 0,
    blockingFindingCodes: [],
    warningCodes: clip.audioQuality.warningCodes,
    subjectiveQualityClaimed: false,
    productionExportEligible: false,
    humanListeningStatus: "pending"
  };
}

function buildModelEvidence(
  manifest: ModelPackageManifest,
  installation: ModelInstallationRecord,
  verification: ModelVerificationRecord
): Phase3b1RealProductPathEvidence["model"] {
  if (
    manifest.modelPackageId !== realModelPackageId ||
    manifest.manifestFingerprint !== realModelPackageFingerprint ||
    installation.status !== "active" ||
    installation.active !== true ||
    verification.status !== "verified" ||
    verification.verifiedFileCount !== 5 ||
    verification.verifiedByteSize !== 92_887_010
  ) {
    throw new Error("The installed package evidence did not match the exact manifest.");
  }
  return {
    modelPackageId: realModelPackageId,
    modelPackageFingerprint: realModelPackageFingerprint,
    installationId: installation.installationId,
    installationRevision: installation.installationRevision,
    installationStatus: "active",
    active: true,
    verificationId: verification.verificationId,
    verificationFingerprint: verification.verificationFingerprint,
    verificationStatus: "verified",
    verifiedFileCount: 5,
    verifiedByteSize: 92_887_010
  };
}

async function stagePrivateListeningPackage(
  privateEvidenceRoot: string,
  evidence: Phase3b1RealProductPathEvidence,
  audioBytes: readonly Uint8Array[]
): Promise<Phase3b1ListeningPackageStaging> {
  if (audioBytes.length !== evidence.auditions.length || audioBytes.length !== 6) {
    throw new Error("The private audio bytes did not match the six governed clip records.");
  }
  await mkdir(privateEvidenceRoot, { recursive: true });
  const [privateRootMetadata, canonicalPrivateRoot, canonicalPrivateParent] =
    await Promise.all([
      lstat(privateEvidenceRoot),
      realpath(privateEvidenceRoot),
      realpath(path.join(repositoryRoot, "local-renders"))
    ]);
  if (
    !privateRootMetadata.isDirectory() ||
    privateRootMetadata.isSymbolicLink() ||
    !samePath(canonicalPrivateRoot, privateEvidenceRoot) ||
    !isStrictChild(canonicalPrivateParent, canonicalPrivateRoot)
  ) {
    throw new Error("The private listening root escaped its canonical ignored root.");
  }
  const stamp = new Date().toISOString().replaceAll(":", "-");
  const directoryName = `run-${stamp}-${randomBytes(6).toString("hex")}`;
  const replayStateDirectoryName = `${directoryName}-desktop-state`;
  const stagingDirectory = path.join(
    privateEvidenceRoot,
    `.${directoryName}.staging`
  );
  const finalDirectory = path.join(privateEvidenceRoot, directoryName);
  await mkdir(stagingDirectory, { recursive: false });
  for (const [index, bytes] of audioBytes.entries()) {
    const clip = evidence.auditions[index];
    if (clip === undefined || sha256(bytes) !== clip.audioSha256) {
      throw new Error("A private listening WAV did not match its authenticated artifact hash.");
    }
    await writeFile(path.join(stagingDirectory, clip.opaqueFileName), bytes, {
      flag: "wx"
    });
  }
  const index = {
    schemaVersion: 1,
    evidenceClassification: "private_human_listening_package",
    generatedAt: new Date().toISOString(),
    humanListeningStatus: "pending",
    humanListeningClaimed: false,
    productionExportEligible: false,
    exactPackageEligibleVoiceCount: 1,
    replayStateDirectoryName,
    replayLauncherFileName: "replay-private-listening.ps1",
    limitation:
      "The exact allow-listed package contains one technically compatible voice tensor, so all six role-purpose clips use that one governed profile.",
    restriction: GOVERNED_PRIVATE_AUDITION_WARNING,
    clips: evidence.auditions,
    desktopReplaySteps: [
      "Keep this directory beside its matching private desktop-state directory.",
      "From this directory, run replay-private-listening.ps1 in PowerShell; it launches the exact repository build with only the retained isolated APPDATA, LOCALAPPDATA, TEMP, and TMP state.",
      "Open the restored synthetic project and choose Auditions.",
      "In Clip history & cache, locate a Real-provider clip by its role and clip ID from this index.",
      "Choose Load audio; the desktop fetches the exact artifact through authenticated Electron IPC and creates a private Blob URL.",
      "Use the Audition clip player controls to play, pause, seek, replay, or stop. Nothing autoplays.",
      "Only after actually listening, use the scorecard and the exact-clip checkbox before recording an acceptable, unacceptable, needs-changes, or undecided disposition."
    ]
  } as const;
  const indexBytes = Buffer.from(`${JSON.stringify(index, null, 2)}\n`, "utf8");
  const scorecardBytes = Buffer.from(buildListeningScorecard(evidence), "utf8");
  await Promise.all([
    writeFile(path.join(stagingDirectory, "listening-index.json"), indexBytes, {
      flag: "wx"
    }),
    writeFile(
      path.join(stagingDirectory, "listening-scorecard.md"),
      scorecardBytes,
      { flag: "wx" }
    )
  ]);
  return {
    stagingDirectory,
    finalDirectory,
    directoryName,
    replayStateDirectory: path.join(
      privateEvidenceRoot,
      replayStateDirectoryName
    ),
    replayStateDirectoryName,
    replayLauncherFileName: "replay-private-listening.ps1",
    indexSha256: sha256(indexBytes),
    scorecardSha256: sha256(scorecardBytes)
  };
}

function buildListeningScorecard(
  evidence: Phase3b1RealProductPathEvidence
): string {
  const rows = evidence.auditions
    .map(
      (clip) =>
        `| ${clip.opaqueFileName} | ${clip.rolePurpose} |  |  |  |  |  |  |  |  |  |  |  |`
    )
    .join("\n");
  return `# Private Phase 3B.1 human listening scorecard

Status: human listening pending. Automated WAV integrity and signal checks do not establish intelligibility, naturalness, artistic quality, consent, commercial clearance, or production readiness.

Restriction: ${GOVERNED_PRIVATE_AUDITION_WARNING}

Use one of: acceptable, unacceptable, needs changes, or undecided. Add a bounded rationale for every non-undecided disposition.

| Opaque file | Role purpose | Intelligibility | Naturalness | Robotic artifacts | Pronunciation | Pacing | Narrator suitability | Dialogue suitability | Emotional range | Long-form fatigue risk | Voice differentiation | Overall / rationale |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
${rows}

Human listener: ____________________

Listening completed at: ____________________

Final decision: ____________________
`;
}

async function proveComparisonPlaybackUi(page: Page): Promise<void> {
  await openAuditions(page);
  const refresh = page.getByRole("button", {
    name: "Refresh evidence",
    exact: true
  });
  try {
    await expect(refresh).toBeEnabled({ timeout: 30_000 });
    await throwIfVisibleRendererError(page);
    await refresh.click();
    const realCards = page.locator(".clip-card").filter({
      hasText: "Real-provider clip"
    });
    await expect(realCards).toHaveCount(8, { timeout: 30_000 });
    await expect(realCards.first()).toBeVisible({ timeout: 30_000 });
    await realCards
      .first()
      .getByRole("button", { name: "Load audio", exact: true })
      .click();
    await expect(
      page.getByText("The authenticated audition audio is ready.", { exact: true })
    ).toBeVisible({ timeout: 30_000 });
    const player = page.getByLabel("Audition clip player", { exact: true });
    await expect(player).toHaveAttribute("src", /^blob:/u);
    for (const control of ["Pause", "Seek +5s", "Replay", "Stop"] as const) {
      await expect(
        page.getByRole("button", { name: control, exact: true })
      ).toBeEnabled();
    }
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await page.getByRole("button", { name: "Seek +5s", exact: true }).click();
    await page.getByRole("button", { name: "Replay", exact: true }).click();
    await page.getByRole("button", { name: "Stop", exact: true }).click();
    await expect(
      realCards.first().getByTestId(/^listened-exact-clip-/u)
    ).not.toBeChecked();
  } catch (error) {
    await throwIfVisibleRendererError(page);
    throw error;
  }
}

async function throwIfVisibleRendererError(page: Page): Promise<void> {
  const alerts = page.locator('.notice.error[role="alert"]:visible');
  const alertCount = await alerts.count();
  if (alertCount === 0) return;
  if (alertCount !== 1) {
    throw new Error(
      "Phase 3B.1 comparison playback found an ambiguous renderer error alert."
    );
  }
  const codes = alerts.first().locator("strong");
  if ((await codes.count()) !== 1) {
    throw new Error(
      "Phase 3B.1 comparison playback did not expose exactly one typed renderer error code."
    );
  }
  const code = (await codes.first().textContent())?.trim();
  throw new Phase3b1RendererError(code);
}

function assertHumanListeningPending(
  workspace: AuditionWorkspaceSnapshot,
  assignment: Phase3b1RealVoiceAssignmentEvidence
): void {
  const governedRoleIds = new Set([
    assignment.narratorRoleId,
    assignment.characterRoleId
  ]);
  const governedReviews = workspace.reviews.filter(
    (review) => review.roleId !== null && governedRoleIds.has(review.roleId)
  );
  if (
    workspace.voiceReadinessSnapshot !== null ||
    governedReviews.length < 2 ||
    governedReviews.some(
      (review) =>
        review.state === "approved" ||
        review.latestDecision?.listeningAttestation != null
    )
  ) {
    throw new Error("Human listening or voice readiness was claimed before the mandatory checkpoint.");
  }
}

function assertGovernedInventory(
  workspace: AuditionWorkspaceSnapshot
): GovernedLocalVoiceInventoryRecord {
  const inventory = requiredInventory(workspace);
  if (
    inventory.inventoryFingerprint !== governedInventoryFingerprint ||
    inventory.warningText !== GOVERNED_PRIVATE_AUDITION_WARNING ||
    inventory.warningFingerprint !== GOVERNED_PRIVATE_AUDITION_WARNING_SHA256 ||
    inventory.items.length !== 1
  ) {
    throw new Error("The exact governed local voice inventory was not projected.");
  }
  const item = inventory.items[0];
  if (
    item === undefined ||
    item.voiceProfileId !== realVoiceProfileId ||
    item.voiceProfileFingerprint !== realVoiceProfileFingerprint ||
    item.catalogRevisionId !== governedCatalogRevisionId ||
    item.catalogRevisionFingerprint !== governedCatalogFingerprint ||
    item.providerId !== realProviderId ||
    item.providerVersion !== realProviderVersion ||
    item.modelId !== realModelId ||
    item.modelVersion !== realModelVersion ||
    item.modelPackageId !== realModelPackageId ||
    item.modelPackageFingerprint !== realModelPackageFingerprint ||
    item.voiceTensor.sha256 !== realVoiceTensorSha256 ||
    item.voiceTensor.byteSize !== 522_240 ||
    item.voiceTensor.scalarFormat !== "float32_le" ||
    item.voiceTensor.elementCount !== 130_560 ||
    item.voiceTensor.shape.join(",") !== "510,256" ||
    item.rights.rightsRecordFingerprint !== realRightsRecordFingerprint ||
    item.rights.rightsState !== "restricted" ||
    item.rights.consentStatus !== "unknown" ||
    item.rights.commercialUseClassification !== "restricted" ||
    item.rights.redistributionClassification !== "restricted" ||
    item.activationEligibility !== "restricted_private_audition" ||
    item.technicalCompatibility !== "compatible" ||
    item.productionExportEligible !== false
  ) {
    throw new Error("The governed real voice inventory record did not match its exact binding.");
  }
  return item;
}

function requiredInventory(workspace: AuditionWorkspaceSnapshot) {
  if (workspace.voiceInventory === undefined) {
    throw new Error("The governed local voice inventory was unavailable.");
  }
  return workspace.voiceInventory;
}

function requiredRealRole(
  workspace: AuditionWorkspaceSnapshot,
  roleId: string,
  roleType: "narrator" | "character"
): AuditionRoleStatus {
  const role = workspace.roles.items.find((item) => item.roleId === roleId);
  if (
    role === undefined ||
    role.roleType !== roleType ||
    role.voiceProfileId !== realVoiceProfileId ||
    role.governedLocalVoice?.voiceProfileFingerprint !==
      realVoiceProfileFingerprint ||
    role.voiceRuntimeBinding?.providerId !== realProviderId ||
    role.runtimeBindingStatus !== "compatible" ||
    role.runtimeBindingReasonCode !== null ||
    role.sessionEvidence === null
  ) {
    throw new Error(`The governed real ${roleType} binding was not current.`);
  }
  return role;
}

async function activeProjectId(page: Page): Promise<string> {
  return page.evaluate(async () => {
    // Selection operations advance the guarded project epoch and can reject
    // the Auditions renderer's in-flight collection hydration. Discover the
    // sole isolated project without mutating active-project state.
    const result = await window.cinematicStory.projects.list();
    if (!result.ok) {
      throw new Error(
        `The isolated project discovery failed: ${result.error.code}: ${result.error.message}`
      );
    }
    const project = result.value.items[0];
    if (
      project === undefined ||
      result.value.items.length !== 1 ||
      result.value.nextCursor !== undefined
    ) {
      throw new Error(
        "The isolated Phase 3B.1 product path did not contain exactly one project."
      );
    }
    return project.projectId;
  });
}

async function openAuditions(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Auditions", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Auditions & Pronunciation", exact: true })
  ).toBeVisible({ timeout: 30_000 });
}

async function readWorkspace(
  page: Page,
  projectId: string
): Promise<AuditionWorkspaceSnapshot> {
  return boundedProjectScopedRead(page, "Audition workspace", () =>
    page.evaluate(async (id) => {
      const result = await window.cinematicStory.auditions.getWorkspace({
        projectId: id,
        roleLimit: 50
      });
      return result.ok
        ? ({ outcome: "succeeded", value: result.value.workspace } as const)
        : ({
            outcome: "failed",
            code: result.error.code,
            message: result.error.message
          } as const);
    }, projectId)
  );
}

async function listModelPackages(page: Page, projectId: string) {
  return boundedProjectScopedRead(page, "Model package list", () =>
    page.evaluate(async (id) => {
      const result = await window.cinematicStory.auditions.listModelPackages({
        projectId: id,
        limit: 50
      });
      return result.ok
        ? ({ outcome: "succeeded", value: result.value.items } as const)
        : ({
            outcome: "failed",
            code: result.error.code,
            message: result.error.message
          } as const);
    }, projectId)
  );
}

async function boundedProjectScopedRead<T>(
  page: Page,
  label: string,
  read: () => Promise<ProjectScopedReadAttempt<T>>
): Promise<T> {
  let latestContextCode = "PROJECT_CONTEXT_UNAVAILABLE";
  for (let attempt = 1; attempt <= projectContextReadRetryLimit; attempt += 1) {
    const result = await read();
    if (result.outcome === "succeeded") return result.value;
    if (!retryableProjectContextReadCodes.has(result.code)) {
      throw new Error(`${label} failed: ${result.code}: ${result.message}`);
    }
    latestContextCode = result.code;
    if (attempt < projectContextReadRetryLimit) {
      // A same-project renderer refresh can advance the guarded epoch while a
      // long-running local model read completes. Discard the entire response
      // and retry only the exact typed context failures.
      await page.waitForTimeout(projectContextReadRetryDelayMs * attempt);
    }
  }
  throw new Error(
    `${label} remained unavailable after bounded project-context retries (${latestContextCode}).`
  );
}

function requiredModelPackage(
  items: Awaited<ReturnType<typeof listModelPackages>>,
  packageId: string
) {
  const item = items.find((candidate) => candidate.manifest.modelPackageId === packageId);
  if (item === undefined) {
    throw new Error(`The model package ${packageId} was unavailable.`);
  }
  return item;
}

async function listPronunciations(
  page: Page,
  projectId: string,
  dictionary: PronunciationDictionary
): Promise<readonly PronunciationEntry[]> {
  return page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.listPronunciations(input);
    if (!result.ok) {
      throw new Error(
        `Pronunciation list failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value.items;
  }, {
    projectId,
    limit: 200,
    expectedDictionaryRevision: dictionary.revision,
    expectedDictionaryFingerprint: dictionary.dictionaryFingerprint
  });
}

async function listClips(
  page: Page,
  projectId: string,
  auditionSessionId?: string
): Promise<readonly AuditionClip[]> {
  return page.evaluate(async ({ id, sessionId }) => {
    const result = await window.cinematicStory.auditions.listClips({
      projectId: id,
      limit: 200,
      ...(sessionId === undefined ? {} : { auditionSessionId: sessionId })
    });
    if (!result.ok) {
      throw new Error(
        `Audition clip list failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return result.value.items;
  }, { id: projectId, sessionId: auditionSessionId });
}

async function waitForAuditionJob(page: Page, jobId: string): Promise<void> {
  const deadline = Date.now() + generationTimeoutMs;
  while (Date.now() < deadline) {
    const job = await page.evaluate(async (id) => {
      const result = await window.cinematicStory.jobs.get(id);
      if (!result.ok) {
        throw new Error(`Audition job read failed: ${result.error.code}`);
      }
      return {
        state: result.value.job.state,
        errorCode: result.value.job.error?.code ?? null,
        errorMessage: result.value.job.error?.message ?? null
      };
    }, jobId);
    if (job.state === "succeeded") return;
    if (["failed", "cancelled", "interrupted"].includes(job.state)) {
      throw new Error(
        `The real audition job reached ${job.state}: ${job.errorCode ?? "unknown"}: ${job.errorMessage ?? "no detail"}.`
      );
    }
    await page.waitForTimeout(250);
  }
  throw new Error("The bounded real-provider audition job timed out.");
}

async function loadAuthenticatedAudio(
  page: Page,
  projectId: string,
  clip: AuditionClip
): Promise<Uint8Array> {
  const loaded = await page.evaluate(async (input) => {
    const result = await window.cinematicStory.auditions.loadAudio(input);
    if (!result.ok) {
      throw new Error(
        `Authenticated audio load failed: ${result.error.code}: ${result.error.message}`
      );
    }
    return {
      bytes: [...new Uint8Array(result.value.bytes)],
      mediaType: result.value.mediaType,
      byteSize: result.value.byteSize,
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
  const bytes = Uint8Array.from(loaded.bytes);
  if (
    loaded.mediaType !== "audio/wav" ||
    loaded.byteSize !== bytes.byteLength ||
    loaded.byteSize !== clip.audioArtifact.byteSize ||
    loaded.sha256 !== clip.audioArtifact.sha256 ||
    sha256(bytes) !== clip.audioArtifact.sha256 ||
    Buffer.from(bytes.subarray(0, 4)).toString("ascii") !== "RIFF" ||
    Buffer.from(bytes.subarray(8, 12)).toString("ascii") !== "WAVE"
  ) {
    throw new Error("The authenticated real-provider WAV failed exact integrity checks.");
  }
  return bytes;
}

function requiredDictionary(
  value: PronunciationDictionary | null
): PronunciationDictionary {
  if (value === null) {
    throw new Error("The current pronunciation dictionary was unavailable.");
  }
  return value;
}

async function writeExclusiveJson(
  target: string,
  value: Readonly<Record<string, unknown>>
): Promise<void> {
  await writeFile(target, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx"
  });
}

function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function samePath(left: string, right: string): boolean {
  return (
    path.win32.resolve(left).toLowerCase() ===
    path.win32.resolve(right).toLowerCase()
  );
}

function isStrictChild(parent: string, child: string): boolean {
  const relative = path.relative(parent, child);
  return (
    relative.length > 0 &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}

async function assertCanonicalPrivateStaging(
  staging: Phase3b1ListeningPackageStaging
): Promise<void> {
  const privateRoot = path.dirname(staging.stagingDirectory);
  const approvedRoot = path.join(repositoryRoot, "local-renders");
  const [
    approvedMetadata,
    canonicalApprovedRoot,
    privateMetadata,
    canonicalPrivateRoot,
    stagingMetadata,
    canonicalStagingDirectory
  ] = await Promise.all([
    lstat(approvedRoot),
    realpath(approvedRoot),
    lstat(privateRoot),
    realpath(privateRoot),
    lstat(staging.stagingDirectory),
    realpath(staging.stagingDirectory)
  ]);
  if (
    !approvedMetadata.isDirectory() ||
    approvedMetadata.isSymbolicLink() ||
    !samePath(canonicalApprovedRoot, approvedRoot) ||
    !privateMetadata.isDirectory() ||
    privateMetadata.isSymbolicLink() ||
    !samePath(canonicalPrivateRoot, privateRoot) ||
    !isStrictChild(canonicalApprovedRoot, canonicalPrivateRoot) ||
    !stagingMetadata.isDirectory() ||
    stagingMetadata.isSymbolicLink() ||
    !samePath(canonicalStagingDirectory, staging.stagingDirectory) ||
    !isStrictChild(canonicalPrivateRoot, canonicalStagingDirectory) ||
    !samePath(path.dirname(staging.finalDirectory), canonicalPrivateRoot) ||
    !samePath(
      path.dirname(staging.replayStateDirectory),
      canonicalPrivateRoot
    )
  ) {
    throw new Error("The private listening staging paths were not canonical.");
  }
}
