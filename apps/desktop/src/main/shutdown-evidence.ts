import {
  lstat,
  mkdir,
  readFile,
  rename,
  rm,
  writeFile
} from "node:fs/promises";
import path from "node:path";

import type {
  ServiceProcessTermination,
  ServiceStopResult
} from "./service-manager.js";

export const packagedShutdownEvidenceEnvironment =
  "CSS_PACKAGED_E2E_SHUTDOWN_EVIDENCE_PATH";
export const packagedShutdownEvidenceSchemaVersion = "1.0.0" as const;
export const phase3bRuntimeShutdownEvidenceEnvironment =
  "CSS_PHASE3B_RUNTIME_SHUTDOWN_EVIDENCE" as const;
export const phase3bRuntimeShutdownEvidenceFileName =
  "phase3b-runtime-shutdown-evidence.json" as const;

const maximumShutdownEvidenceBytes = 16 * 1024;
const maximumPhase3bRuntimeShutdownEvidenceBytes = 256 * 1024;
const shutdownEvidenceFileNames = new Set([
  "packaged-e2e-service-shutdown-1.json",
  "packaged-e2e-service-shutdown-2.json",
  "phase3b1-real-service-shutdown-3.json",
  "phase3b1-real-service-shutdown-4.json"
]);

export interface PackagedServiceShutdownEvidence {
  readonly schemaVersion: typeof packagedShutdownEvidenceSchemaVersion;
  readonly status: "succeeded" | "failed";
  readonly processes: readonly ServiceProcessTermination[];
  readonly forceKillUsed: boolean | null;
  readonly allProcessesExitedGracefully: boolean | null;
}

export interface Phase3bRuntimeShutdownExitEvidence {
  readonly runtimeInstanceId: string;
  readonly workerPid: number;
  readonly state: "stopped" | "failed";
  readonly stoppedAt: string;
  readonly stopReasonCode:
    | "clean"
    | "idle"
    | "deadline"
    | "protocol_error"
    | "process_error";
  readonly exitCode: number | null;
  readonly shutdownAcknowledged: boolean;
  readonly gracefulShutdownConfirmed: boolean;
  readonly terminatedByParent: boolean;
  readonly ownershipConfirmed: boolean;
  readonly ownedProcessesConfirmedExited: boolean;
  readonly jobObjectAssigned: boolean;
  readonly deniedNetworkAttemptCount: number;
}

export interface Phase3bRuntimeShutdownEvidence {
  readonly contractVersion: "1.0.0";
  readonly serviceInstanceId: string;
  readonly ownedRuntimeCount: number;
  readonly runtimeExits: readonly Phase3bRuntimeShutdownExitEvidence[];
  readonly allGracefulShutdownsConfirmed: boolean;
  readonly writtenAt: string;
}

export function resolvePackagedShutdownEvidencePath(
  value: string | undefined,
  userDataPath: string
): string | null {
  if (value === undefined || value.length === 0) {
    return null;
  }
  if (
    value.length > 2_048 ||
    value.includes("\0") ||
    !path.isAbsolute(value)
  ) {
    throw new Error("The packaged shutdown evidence path is invalid.");
  }
  const canonicalUserData = path.resolve(userDataPath);
  const target = path.resolve(value);
  if (
    !samePath(path.dirname(target), canonicalUserData) ||
    !shutdownEvidenceFileNames.has(path.basename(target))
  ) {
    throw new Error(
      "The packaged shutdown evidence path escaped the isolated user-data directory."
    );
  }
  return target;
}

export function createPackagedServiceShutdownEvidence(
  result: ServiceStopResult | null
): PackagedServiceShutdownEvidence {
  if (result === null) {
    return {
      schemaVersion: packagedShutdownEvidenceSchemaVersion,
      status: "failed",
      processes: [],
      forceKillUsed: null,
      allProcessesExitedGracefully: null
    };
  }
  return {
    schemaVersion: packagedShutdownEvidenceSchemaVersion,
    status: "succeeded",
    processes: result.processes.map((value) => ({ ...value })),
    forceKillUsed: result.forceKillUsed,
    allProcessesExitedGracefully:
      result.allProcessesExitedGracefully
  };
}

export async function writePackagedServiceShutdownEvidence(
  targetPath: string,
  value: PackagedServiceShutdownEvidence
): Promise<void> {
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  if (
    Buffer.byteLength(serialized, "utf8") >
    maximumShutdownEvidenceBytes
  ) {
    throw new Error("The packaged shutdown evidence exceeded its limit.");
  }
  await mkdir(path.dirname(targetPath), {
    recursive: true,
    mode: 0o700
  });
  const temporaryPath = `${targetPath}.${process.pid}.tmp`;
  try {
    await writeFile(temporaryPath, serialized, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx"
    });
    await rename(temporaryPath, targetPath);
  } finally {
    await rm(temporaryPath, { force: true });
  }
}

export async function readPackagedServiceShutdownEvidence(
  targetPath: string
): Promise<PackagedServiceShutdownEvidence> {
  const metadata = await lstat(targetPath);
  if (
    !metadata.isFile() ||
    metadata.isSymbolicLink() ||
    metadata.size < 1 ||
    metadata.size > maximumShutdownEvidenceBytes
  ) {
    throw new Error("The packaged shutdown evidence file is invalid.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(targetPath, "utf8"));
  } catch {
    throw new Error("The packaged shutdown evidence is invalid JSON.");
  }
  return parsePackagedServiceShutdownEvidence(parsed);
}

export async function readPhase3bRuntimeShutdownEvidence(
  isolatedUserDataPath: string
): Promise<Phase3bRuntimeShutdownEvidence> {
  const canonicalUserData = path.resolve(isolatedUserDataPath);
  const targetPath = path.resolve(
    canonicalUserData,
    phase3bRuntimeShutdownEvidenceFileName
  );
  if (
    !samePath(path.dirname(targetPath), canonicalUserData) ||
    path.basename(targetPath) !== phase3bRuntimeShutdownEvidenceFileName
  ) {
    throw new Error(
      "The Phase 3B runtime shutdown evidence path escaped isolated user data."
    );
  }
  const metadata = await lstat(targetPath);
  if (
    !metadata.isFile() ||
    metadata.isSymbolicLink() ||
    metadata.size < 1 ||
    metadata.size > maximumPhase3bRuntimeShutdownEvidenceBytes
  ) {
    throw new Error("The Phase 3B runtime shutdown evidence file is invalid.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(targetPath, "utf8"));
  } catch {
    throw new Error("The Phase 3B runtime shutdown evidence is invalid JSON.");
  }
  return parsePhase3bRuntimeShutdownEvidence(parsed);
}

function parsePhase3bRuntimeShutdownEvidence(
  raw: unknown
): Phase3bRuntimeShutdownEvidence {
  if (
    !isRecord(raw) ||
    !hasExactKeys(raw, [
      "contractVersion",
      "serviceInstanceId",
      "ownedRuntimeCount",
      "runtimeExits",
      "allGracefulShutdownsConfirmed",
      "writtenAt"
    ]) ||
    raw.contractVersion !== "1.0.0" ||
    !isSafeId(raw.serviceInstanceId) ||
    !Number.isSafeInteger(raw.ownedRuntimeCount) ||
    Number(raw.ownedRuntimeCount) < 0 ||
    Number(raw.ownedRuntimeCount) > 200 ||
    !Array.isArray(raw.runtimeExits) ||
    raw.runtimeExits.length !== raw.ownedRuntimeCount ||
    typeof raw.allGracefulShutdownsConfirmed !== "boolean" ||
    !isTimestamp(raw.writtenAt)
  ) {
    throw new Error(
      "The Phase 3B runtime shutdown evidence contract is invalid."
    );
  }
  const runtimeExits = raw.runtimeExits.map(parsePhase3bRuntimeShutdownExit);
  if (
    new Set(runtimeExits.map((item) => item.runtimeInstanceId)).size !==
      runtimeExits.length ||
    new Set(runtimeExits.map((item) => item.workerPid)).size !==
      runtimeExits.length
  ) {
    throw new Error(
      "The Phase 3B runtime shutdown identities were duplicated."
    );
  }
  const allGracefulShutdownsConfirmed =
    runtimeExits.length > 0 &&
    runtimeExits.every(
      (item) =>
        item.state === "stopped" &&
        (item.stopReasonCode === "clean" ||
          item.stopReasonCode === "idle") &&
        item.exitCode === 0 &&
        item.shutdownAcknowledged &&
        item.gracefulShutdownConfirmed &&
        !item.terminatedByParent &&
        item.ownershipConfirmed &&
        item.ownedProcessesConfirmedExited &&
        item.jobObjectAssigned &&
        item.deniedNetworkAttemptCount === 0
    );
  if (
    raw.allGracefulShutdownsConfirmed !== allGracefulShutdownsConfirmed
  ) {
    throw new Error(
      "The Phase 3B runtime shutdown summary was contradictory."
    );
  }
  return {
    contractVersion: "1.0.0",
    serviceInstanceId: raw.serviceInstanceId,
    ownedRuntimeCount: Number(raw.ownedRuntimeCount),
    runtimeExits,
    allGracefulShutdownsConfirmed,
    writtenAt: raw.writtenAt
  };
}

function parsePhase3bRuntimeShutdownExit(
  raw: unknown
): Phase3bRuntimeShutdownExitEvidence {
  const reasonCodes = new Set([
    "clean",
    "idle",
    "deadline",
    "protocol_error",
    "process_error"
  ]);
  if (
    !isRecord(raw) ||
    !hasExactKeys(raw, [
      "runtimeInstanceId",
      "workerPid",
      "state",
      "stoppedAt",
      "stopReasonCode",
      "exitCode",
      "shutdownAcknowledged",
      "gracefulShutdownConfirmed",
      "terminatedByParent",
      "ownershipConfirmed",
      "ownedProcessesConfirmedExited",
      "jobObjectAssigned",
      "deniedNetworkAttemptCount"
    ]) ||
    !isSafeId(raw.runtimeInstanceId) ||
    !Number.isSafeInteger(raw.workerPid) ||
    Number(raw.workerPid) <= 0 ||
    (raw.state !== "stopped" && raw.state !== "failed") ||
    !isTimestamp(raw.stoppedAt) ||
    typeof raw.stopReasonCode !== "string" ||
    !reasonCodes.has(raw.stopReasonCode) ||
    (raw.exitCode !== null && !Number.isSafeInteger(raw.exitCode)) ||
    typeof raw.shutdownAcknowledged !== "boolean" ||
    typeof raw.gracefulShutdownConfirmed !== "boolean" ||
    typeof raw.terminatedByParent !== "boolean" ||
    typeof raw.ownershipConfirmed !== "boolean" ||
    typeof raw.ownedProcessesConfirmedExited !== "boolean" ||
    typeof raw.jobObjectAssigned !== "boolean" ||
    !Number.isSafeInteger(raw.deniedNetworkAttemptCount) ||
    Number(raw.deniedNetworkAttemptCount) < 0 ||
    Number(raw.deniedNetworkAttemptCount) > 1_000_000
  ) {
    throw new Error("A Phase 3B runtime shutdown record was invalid.");
  }
  return {
    runtimeInstanceId: raw.runtimeInstanceId,
    workerPid: Number(raw.workerPid),
    state: raw.state,
    stoppedAt: raw.stoppedAt,
    stopReasonCode:
      raw.stopReasonCode as Phase3bRuntimeShutdownExitEvidence["stopReasonCode"],
    exitCode: raw.exitCode === null ? null : Number(raw.exitCode),
    shutdownAcknowledged: raw.shutdownAcknowledged,
    gracefulShutdownConfirmed: raw.gracefulShutdownConfirmed,
    terminatedByParent: raw.terminatedByParent,
    ownershipConfirmed: raw.ownershipConfirmed,
    ownedProcessesConfirmedExited: raw.ownedProcessesConfirmedExited,
    jobObjectAssigned: raw.jobObjectAssigned,
    deniedNetworkAttemptCount: Number(raw.deniedNetworkAttemptCount)
  };
}

function parsePackagedServiceShutdownEvidence(
  value: unknown
): PackagedServiceShutdownEvidence {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "schemaVersion",
      "status",
      "processes",
      "forceKillUsed",
      "allProcessesExitedGracefully"
    ]) ||
    value.schemaVersion !== packagedShutdownEvidenceSchemaVersion ||
    (value.status !== "succeeded" && value.status !== "failed") ||
    !Array.isArray(value.processes) ||
    value.processes.length > 16
  ) {
    throw new Error("The packaged shutdown evidence contract is invalid.");
  }
  const processes = value.processes.map(parseServiceProcessTermination);
  if (new Set(processes.map((item) => item.pid)).size !== processes.length) {
    throw new Error("The packaged shutdown process identities are invalid.");
  }
  const succeeded = value.status === "succeeded";
  if (
    (succeeded &&
      (typeof value.forceKillUsed !== "boolean" ||
        typeof value.allProcessesExitedGracefully !== "boolean")) ||
    (!succeeded &&
      (value.forceKillUsed !== null ||
        value.allProcessesExitedGracefully !== null ||
        processes.length !== 0))
  ) {
    throw new Error("The packaged shutdown result is contradictory.");
  }
  const forceKillUsed = processes.some(
    (item) => item.method === "force_kill"
  );
  const allProcessesExitedGracefully = processes.every(
    (item) =>
      item.method === "stdin_eof" &&
      item.exitCode === 0 &&
      item.signalCode === null
  );
  if (
    succeeded &&
    (value.forceKillUsed !== forceKillUsed ||
      value.allProcessesExitedGracefully !==
        allProcessesExitedGracefully)
  ) {
    throw new Error("The packaged shutdown summary is invalid.");
  }
  return {
    schemaVersion: packagedShutdownEvidenceSchemaVersion,
    status: value.status,
    processes,
    forceKillUsed: succeeded ? forceKillUsed : null,
    allProcessesExitedGracefully: succeeded
      ? allProcessesExitedGracefully
      : null
  };
}

function parseServiceProcessTermination(
  value: unknown
): ServiceProcessTermination {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "pid",
      "method",
      "exitCode",
      "signalCode"
    ]) ||
    !Number.isSafeInteger(value.pid) ||
    Number(value.pid) <= 0 ||
    !["already_exited", "stdin_eof", "force_kill"].includes(
      String(value.method)
    ) ||
    (value.exitCode !== null &&
      (!Number.isSafeInteger(value.exitCode) ||
        Number(value.exitCode) < 0)) ||
    (value.signalCode !== null &&
      (typeof value.signalCode !== "string" ||
        value.signalCode.length < 1 ||
        value.signalCode.length > 40))
  ) {
    throw new Error("A packaged shutdown process record is invalid.");
  }
  return {
    pid: Number(value.pid),
    method: value.method as ServiceProcessTermination["method"],
    exitCode:
      value.exitCode === null ? null : Number(value.exitCode),
    signalCode:
      value.signalCode as ServiceProcessTermination["signalCode"]
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  );
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[]
): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
}

function samePath(left: string, right: string): boolean {
  return (
    path.win32.resolve(left).toLowerCase() ===
    path.win32.resolve(right).toLowerCase()
  );
}

function isSafeId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/u.test(value)
  );
}

function isTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T/u.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}
