import { createHash } from "node:crypto";
import {
  lstat,
  readFile,
  rename,
  rm,
  writeFile
} from "node:fs/promises";
import path from "node:path";

import {
  ProcessInventoryError,
  type ProcessInventoryFailureCode
} from "./packaged-process-inventory";

export const packagedE2eSchemaVersion = "3.0.0";
export const packagedFixture =
  "fixtures/synthetic-story/sample-story.docx.base64";
export const packagedFlow = Object.freeze([
  "create",
  "import_synthetic_docx",
  "wait_for_extraction",
  "review_import",
  "approve_import",
  "analyze",
  "correct_speaker",
  "close",
  "restart",
  "restore",
  "verify_import_review_persistence",
  "close"
]);
export const isolatedEnvironmentNames = Object.freeze([
  "APPDATA",
  "LOCALAPPDATA",
  "TEMP",
  "TMP"
]);

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
