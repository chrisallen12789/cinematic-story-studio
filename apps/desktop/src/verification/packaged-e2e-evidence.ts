import { createHash } from "node:crypto";
import {
  lstat,
  readFile,
  rename,
  rm,
  writeFile
} from "node:fs/promises";
import path from "node:path";

import type {
  AnalysisCorrectionCategory,
  AnalysisGateId,
  AnalysisJobStage
} from "@cinematic-story-studio/contracts";

import {
  ProcessInventoryError,
  type ProcessInventoryFailureCode
} from "./packaged-process-inventory";

export const packagedE2eSchemaVersion = "4.0.0";
export const packagedFixture =
  "fixtures/synthetic-story/sample-story.docx.base64";
export const packagedPhase2GovernanceFlow = Object.freeze([
  "start_whole_book_analysis",
  "observe_analysis_stages",
  "inspect_structure",
  "inspect_character_registry",
  "correct_character_identity",
  "inspect_dialogue_and_narration",
  "correct_dialogue_speaker",
  "inspect_whole_book_intelligence",
  "disposition_continuity",
  "approve_story_structure_review",
  "approve_character_registry_review",
  "approve_dialogue_attribution_review",
  "approve_whole_book_analysis_review"
]);
export const packagedFlow = Object.freeze([
  "create",
  "import_synthetic_docx",
  "wait_for_extraction",
  "review_import",
  "approve_import",
  "analyze",
  "correct_speaker",
  "start_whole_book_analysis",
  "observe_analysis_stages",
  "inspect_structure",
  "inspect_character_registry",
  "correct_character_identity",
  "inspect_dialogue_and_narration",
  "correct_dialogue_speaker",
  "inspect_whole_book_intelligence",
  "disposition_continuity",
  "approve_story_structure_review",
  "approve_character_registry_review",
  "approve_dialogue_attribution_review",
  "approve_whole_book_analysis_review",
  "close",
  "restart",
  "restore",
  "verify_import_review_persistence",
  "verify_story_analysis_persistence",
  "close"
]);

export interface PackagedFlowRecorder {
  record(step: string): void;
  complete(): readonly string[];
}

export function createPackagedFlowRecorder(
  expected: readonly string[]
): PackagedFlowRecorder {
  const observed: string[] = [];
  return {
    record(step: string): void {
      if (expected[observed.length] !== step) {
        throw new Error("The packaged workflow operation order is invalid.");
      }
      observed.push(step);
    },
    complete(): readonly string[] {
      if (observed.length !== expected.length) {
        throw new Error("The packaged workflow operation sequence is incomplete.");
      }
      return Object.freeze([...observed]);
    }
  };
}
export const isolatedEnvironmentNames = Object.freeze([
  "APPDATA",
  "LOCALAPPDATA",
  "TEMP",
  "TMP"
]);
export const packagedCorrectionReasonFingerprints = Object.freeze({
  characterIdentity:
    "b42b6091d0bde37b4dd15f99a321e86dd965f2272a4ca68da6703b6f5ba2f0da",
  dialogueSpeaker:
    "6db94e679f663d3dfbed36c173eb8445d6a364beb114c7f8c070e30983247300",
  continuityDisposition:
    "d7497ae908f34096c6bbbfdcbf7ea8287967882880cd229af078eee2383c2554"
});

const maximumMachineResultBytes = 1024 * 1024;
const maximumEncodedFixtureBytes = 2 * 1024 * 1024;
const maximumDecodedFixtureBytes = 1024 * 1024;

export type PackagedFailureStage =
  | "prelaunch_inventory_1"
  | "isolation_setup"
  | "launch_1"
  | "root_ownership_1"
  | "readiness_1"
  | "service_ownership_1"
  | "workflow_1"
  | "shutdown_1"
  | "prelaunch_inventory_2"
  | "launch_2"
  | "root_ownership_2"
  | "readiness_2"
  | "service_ownership_2"
  | "restore_2"
  | "screenshot"
  | "shutdown_2"
  | "evidence_generation"
  | "cleanup";

export type PackagedFailureCode =
  | ProcessInventoryFailureCode
  | "ISOLATION_SETUP_FAILED"
  | "APPLICATION_LAUNCH_FAILED"
  | "ROOT_OWNERSHIP_NOT_ESTABLISHED"
  | "APPLICATION_READINESS_FAILED"
  | "SERVICE_OWNERSHIP_NOT_ESTABLISHED"
  | "APPLICATION_WORKFLOW_FAILED"
  | "SHUTDOWN_VERIFICATION_FAILED"
  | "SCREENSHOT_CAPTURE_FAILED"
  | "EVIDENCE_GENERATION_FAILED"
  | "CLEANUP_FAILED";

export interface RedactedRelevantProcess {
  readonly pid: number;
  readonly name: string;
  readonly creationDate: string;
}

export interface MachineOwnedProcess {
  readonly pid: number;
  readonly parentPid: number;
  readonly kind: "app" | "service";
  readonly executableName: string;
  readonly creationDate: string;
}

export interface MachineLaunchEvidence {
  readonly launch: 1 | 2;
  readonly preexistingRelevantProcesses: readonly RedactedRelevantProcess[];
  readonly ownership: {
    readonly launcherPid: number;
    readonly rootPid: number;
    readonly processes: readonly MachineOwnedProcess[];
  };
  readonly exitProof: {
    readonly ownedPids: readonly number[];
    readonly graceful: boolean;
    readonly forcedPids: readonly number[];
    readonly remainingPids: readonly number[];
  };
}

export interface PackagedImportReviewEvidence {
  readonly format: "docx";
  readonly sourceSha256: string;
  readonly extractedTextSha256: string;
  readonly extractionRevision: number;
  readonly warningCount: number;
  readonly approvalDecision: "approved";
  readonly approvalPersistedAfterRestart: boolean;
  readonly extractionPersistedAfterRestart: boolean;
  readonly analysisPersistedAfterRestart: boolean;
}

export interface PackagedStoryAnalysisProfileEvidence {
  readonly profileId: "whole-book-intelligence-v1";
  readonly semanticVersion: "1.0.0";
  readonly profileFingerprint: string;
  readonly producerId: "whole-book-analysis-orchestrator";
  readonly producerVersion: "1.0.0";
}

export interface PackagedAnalysisAgentEvidence {
  readonly agentId: string;
  readonly agentVersion: "1.0.0";
  readonly executionId: string;
  readonly status: "succeeded";
  readonly outputFingerprint: string;
}

export interface PackagedApprovedAnalysisInputEvidence {
  readonly sourceDocumentId: string;
  readonly sourceRevision: number;
  readonly sourceSha256: string;
  readonly extractionId: string;
  readonly extractionRevision: number;
  readonly extractedTextSha256: string;
  readonly importReviewId: string;
  readonly importReviewRevision: number;
  readonly importReviewDecisionId: string;
  readonly approvedEvidenceFingerprint: string;
  readonly storyId: string;
  readonly storyRevision: number;
  readonly storyFingerprint: string;
}

export interface PackagedAnalysisRunEvidence {
  readonly runId: string;
  readonly inputFingerprint: string;
  readonly runFingerprint: string;
  readonly jobId: string;
  readonly status: "succeeded";
  readonly snapshotId: string;
  readonly snapshotRevision: number;
  readonly snapshotFingerprint: string;
  readonly correctionSetFingerprint: string;
}

export interface PackagedAnalysisCountsEvidence {
  readonly agentExecutions: number;
  readonly chapters: number;
  readonly scenes: number;
  readonly beats: number;
  readonly characters: number;
  readonly mentions: number;
  readonly dialogueLines: number;
  readonly narrationSpans: number;
  readonly povSegments: number;
  readonly locations: number;
  readonly timelineEvents: number;
  readonly temporalConstraints: number;
  readonly relationships: number;
  readonly emotionalStates: number;
  readonly dramaticIntents: number;
  readonly continuityFindings: number;
  readonly corrections: number;
}

export interface PackagedAnalysisAssertionsEvidence {
  readonly structureDetected: boolean;
  readonly characterRegistryDetected: boolean;
  readonly ambiguousIdentityPreserved: boolean;
  readonly ambiguousDialoguePreserved: boolean;
  readonly narrationDistinctionDetected: boolean;
  readonly povShiftDetected: boolean;
  readonly locationsDetected: boolean;
  readonly timelineFlashbackDetected: boolean;
  readonly relationshipChangeDetected: boolean;
  readonly emotionalProgressionDetected: boolean;
  readonly continuityAnomalyDetected: boolean;
}

export interface PackagedAnalysisCorrectionEvidence {
  readonly correctionId: string;
  readonly category: AnalysisCorrectionCategory;
  readonly targetEntityId: string;
  readonly reasonFingerprint: string;
  readonly previousValueFingerprint: string;
  readonly correctedValueFingerprint: string;
  readonly effectiveValueFingerprintBeforeRestart: string;
  readonly effectiveValueFingerprintAfterRestart: string;
  readonly effectiveAuthorityBeforeRestart: "human";
  readonly effectiveAuthorityAfterRestart: "human";
  readonly immutable: true;
  readonly lockedAgainstAutomation: true;
  readonly persistedAfterRestart: boolean;
}

export interface PackagedAnalysisGateStateEvidence {
  readonly reviewId: string;
  readonly decisionId: string;
  readonly state: "approved";
  readonly profileFingerprint: string;
  readonly runFingerprint: string;
  readonly snapshotId: string;
  readonly snapshotRevision: number;
  readonly snapshotFingerprint: string;
  readonly decisionRecordFingerprint: string;
  readonly artifactFingerprint: string;
  readonly evidenceFingerprint: string;
}

export interface PackagedAnalysisGateEvidence {
  readonly gateId: AnalysisGateId;
  readonly beforeRestart: PackagedAnalysisGateStateEvidence;
  readonly afterRestart: PackagedAnalysisGateStateEvidence;
  readonly immutable: true;
}

export interface PackagedAnalysisRestartEvidence {
  readonly runPersisted: boolean;
  readonly snapshotPersisted: boolean;
  readonly correctionSetPersisted: boolean;
  readonly gateDecisionsPersisted: boolean;
  readonly agentExecutionsPersisted: boolean;
}

export interface PackagedStoryAnalysisEvidence {
  readonly profile: PackagedStoryAnalysisProfileEvidence;
  readonly agents: readonly PackagedAnalysisAgentEvidence[];
  readonly approvedInput: PackagedApprovedAnalysisInputEvidence;
  readonly run: PackagedAnalysisRunEvidence;
  readonly observedStages: readonly AnalysisJobStage[];
  readonly counts: PackagedAnalysisCountsEvidence;
  readonly assertions: PackagedAnalysisAssertionsEvidence;
  readonly corrections: {
    readonly characterIdentity: PackagedAnalysisCorrectionEvidence & {
      readonly category: "character_identity";
    };
    readonly dialogueSpeaker: PackagedAnalysisCorrectionEvidence & {
      readonly category: "dialogue_speaker";
    };
    readonly continuityDisposition: PackagedAnalysisCorrectionEvidence & {
      readonly category: "continuity_disposition";
    };
  };
  readonly gates: readonly PackagedAnalysisGateEvidence[];
  readonly restart: PackagedAnalysisRestartEvidence;
}

export interface PackagedE2eMachineResult {
  readonly schemaVersion: typeof packagedE2eSchemaVersion;
  readonly completedAt: string;
  readonly status: "passed" | "failed";
  readonly failureStage: PackagedFailureStage | null;
  readonly failureCode: PackagedFailureCode | null;
  readonly packagedVersion: string;
  readonly executable: string;
  readonly fixture: typeof packagedFixture;
  readonly isolationEnvironment: typeof isolatedEnvironmentNames;
  readonly completedLaunches: readonly (1 | 2)[];
  readonly applicationLaunchBegan: boolean;
  readonly ownershipEstablished: boolean;
  readonly cleanupCompleted: boolean;
  readonly preexistingRelevantProcesses:
    | readonly RedactedRelevantProcess[]
    | null;
  readonly flow: typeof packagedFlow;
  readonly screenshot: {
    readonly artifactId: "packaged-ui-screenshot";
    readonly captured: boolean;
  };
  readonly importReview: PackagedImportReviewEvidence | null;
  readonly storyAnalysis: PackagedStoryAnalysisEvidence | null;
  readonly launches: readonly MachineLaunchEvidence[];
}

export function packagedFailureCode(
  stage: PackagedFailureStage,
  error: unknown
): PackagedFailureCode {
  if (error instanceof ProcessInventoryError) {
    return error.code;
  }
  switch (stage) {
    case "prelaunch_inventory_1":
    case "prelaunch_inventory_2":
      return "PROCESS_INVENTORY_COMMAND_FAILED";
    case "isolation_setup":
      return "ISOLATION_SETUP_FAILED";
    case "launch_1":
    case "launch_2":
      return "APPLICATION_LAUNCH_FAILED";
    case "root_ownership_1":
    case "root_ownership_2":
      return "ROOT_OWNERSHIP_NOT_ESTABLISHED";
    case "readiness_1":
    case "readiness_2":
      return "APPLICATION_READINESS_FAILED";
    case "service_ownership_1":
    case "service_ownership_2":
      return "SERVICE_OWNERSHIP_NOT_ESTABLISHED";
    case "workflow_1":
    case "restore_2":
      return "APPLICATION_WORKFLOW_FAILED";
    case "shutdown_1":
    case "shutdown_2":
      return "SHUTDOWN_VERIFICATION_FAILED";
    case "screenshot":
      return "SCREENSHOT_CAPTURE_FAILED";
    case "evidence_generation":
      return "EVIDENCE_GENERATION_FAILED";
    case "cleanup":
      return "CLEANUP_FAILED";
  }
}

export async function writePackagedE2eMachineResult(
  resultPath: string,
  value: PackagedE2eMachineResult
): Promise<void> {
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  if (
    Buffer.byteLength(serialized, "utf8") > maximumMachineResultBytes
  ) {
    throw new Error("The packaged E2E machine result exceeded its limit.");
  }
  const temporaryPath = `${resultPath}.${process.pid}.tmp`;
  try {
    await writeFile(temporaryPath, serialized, {
      encoding: "utf8",
      mode: 0o600
    });
    await rename(temporaryPath, resultPath);
  } finally {
    await rm(temporaryPath, { force: true });
  }
}

export async function materializeStrictBase64Docx(
  encodedPath: string,
  isolatedRoot: string,
  destinationPath: string
): Promise<string> {
  const resolvedRoot = path.resolve(isolatedRoot);
  const resolvedDestination = path.resolve(destinationPath);
  const relativeDestination = path.relative(
    resolvedRoot,
    resolvedDestination
  );
  if (
    !path.isAbsolute(isolatedRoot) ||
    relativeDestination.length === 0 ||
    relativeDestination === ".." ||
    relativeDestination.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relativeDestination) ||
    path.extname(resolvedDestination).toLowerCase() !== ".docx"
  ) {
    throw new Error(
      "The synthetic DOCX destination is outside its isolated root."
    );
  }
  const [encodedMetadata, rootMetadata, destinationParentMetadata] =
    await Promise.all([
      lstat(encodedPath),
      lstat(resolvedRoot),
      lstat(path.dirname(resolvedDestination))
    ]);
  if (
    !encodedMetadata.isFile() ||
    encodedMetadata.isSymbolicLink() ||
    encodedMetadata.size <= 0 ||
    encodedMetadata.size > maximumEncodedFixtureBytes ||
    !rootMetadata.isDirectory() ||
    rootMetadata.isSymbolicLink() ||
    !destinationParentMetadata.isDirectory() ||
    destinationParentMetadata.isSymbolicLink()
  ) {
    throw new Error("The synthetic DOCX fixture location is invalid.");
  }
  const encodedBytes = await readFile(encodedPath);
  if (
    encodedBytes.length !== encodedMetadata.size ||
    encodedBytes.some((value) => value > 0x7f)
  ) {
    throw new Error("The synthetic DOCX fixture encoding is invalid.");
  }
  const encodedText = encodedBytes.toString("ascii");
  if (
    encodedText.includes("\0") ||
    /\r(?!\n)/u.test(encodedText) ||
    /[^A-Za-z0-9+/=\r\n]/u.test(encodedText)
  ) {
    throw new Error("The synthetic DOCX fixture is not strict base64.");
  }
  const compact = encodedText.replace(/\r?\n/gu, "");
  if (
    compact.length === 0 ||
    compact.length % 4 !== 0 ||
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(
      compact
    )
  ) {
    throw new Error("The synthetic DOCX fixture is not strict base64.");
  }
  const decoded = Buffer.from(compact, "base64");
  if (
    decoded.length === 0 ||
    decoded.length > maximumDecodedFixtureBytes ||
    decoded.toString("base64") !== compact ||
    decoded.subarray(0, 4).compare(Buffer.from([0x50, 0x4b, 0x03, 0x04])) !==
      0
  ) {
    throw new Error("The synthetic DOCX fixture bytes are invalid.");
  }
  await writeFile(resolvedDestination, decoded, {
    flag: "wx",
    mode: 0o600
  });
  return createHash("sha256").update(decoded).digest("hex");
}

export async function runPackagedE2eEvidenceStep<T>(
  stage: PackagedFailureStage,
  operation: () => Promise<T>,
  writeFailure: (
    stage: PackagedFailureStage,
    code: PackagedFailureCode
  ) => Promise<void>
): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    await writeFailure(stage, packagedFailureCode(stage, error));
    throw error;
  }
}
