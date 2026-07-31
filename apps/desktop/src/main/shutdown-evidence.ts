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

const maximumShutdownEvidenceBytes = 16 * 1024;
const shutdownEvidenceFileName =
  /^packaged-e2e-service-shutdown-[12]\.json$/u;

export interface PackagedServiceShutdownEvidence {
  readonly schemaVersion: typeof packagedShutdownEvidenceSchemaVersion;
  readonly status: "succeeded" | "failed";
  readonly processes: readonly ServiceProcessTermination[];
  readonly forceKillUsed: boolean | null;
  readonly allProcessesExitedGracefully: boolean | null;
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
    !shutdownEvidenceFileName.test(path.basename(target))
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
