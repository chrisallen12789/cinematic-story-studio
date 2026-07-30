import { rename, rm, writeFile } from "node:fs/promises";

import {
  ProcessInventoryError,
  type ProcessInventoryFailureCode
} from "./packaged-process-inventory";

export const packagedE2eSchemaVersion = "2.0.0";
export const packagedFixture =
  "fixtures/synthetic-story/sample-story.md";
export const packagedFlow = Object.freeze([
  "create",
  "import_synthetic_fixture",
  "analyze",
  "correct_speaker",
  "close",
  "restart",
  "restore",
  "close"
]);
export const isolatedEnvironmentNames = Object.freeze([
  "APPDATA",
  "LOCALAPPDATA",
  "TEMP",
  "TMP"
]);

const maximumMachineResultBytes = 1024 * 1024;

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
