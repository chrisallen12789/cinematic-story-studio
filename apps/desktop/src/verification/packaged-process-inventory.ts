import { spawn } from "node:child_process";
import path from "node:path";
import { performance } from "node:perf_hooks";

export const appExecutableName = "Cinematic Story Studio.exe";
export const serviceExecutableName = "cinematic-story-service.exe";

const relevantProcessNames = new Set([
  appExecutableName,
  serviceExecutableName
]);
const maximumInventoryRecords = 256;

export const defaultProcessInventoryPolicy = Object.freeze({
  perAttemptTimeoutMs: 15_000,
  maximumAttempts: 3,
  totalDeadlineMs: 44_000,
  backoffMs: Object.freeze([250, 750]),
  maximumOutputBytes: 1024 * 1024
});

export type ProcessInventoryFailureCode =
  | "PROCESS_INVENTORY_TIMEOUT"
  | "PROCESS_INVENTORY_DEADLINE_EXCEEDED"
  | "PROCESS_INVENTORY_COMMAND_FAILED"
  | "PROCESS_INVENTORY_COMMAND_START_FAILED"
  | "PROCESS_INVENTORY_HELPER_EXIT_UNCONFIRMED"
  | "PROCESS_INVENTORY_OUTPUT_LIMIT"
  | "PROCESS_INVENTORY_MALFORMED_OUTPUT"
  | "PROCESS_INVENTORY_INVALID_IDENTITY"
  | "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
  | "PROCESS_INVENTORY_UNSUPPORTED_PLATFORM";

export class ProcessInventoryError extends Error {
  readonly code: ProcessInventoryFailureCode;
  readonly retryable: boolean;

  constructor(code: ProcessInventoryFailureCode, retryable: boolean) {
    super(processInventoryMessage(code));
    this.name = "ProcessInventoryError";
    this.code = code;
    this.retryable = retryable;
  }
}

export interface ProcessIdentity {
  readonly pid: number;
  readonly parentPid: number;
  readonly name: string;
  readonly executablePath: string | null;
  readonly creationDate: string;
}

export interface OwnedProcess extends ProcessIdentity {
  readonly kind: "app" | "service";
}

export interface PackagedProcessPaths {
  readonly executablePath: string;
  readonly serviceExecutablePath: string;
}

export interface ProcessCommandRequest {
  readonly command: string;
  readonly arguments: readonly string[];
  readonly timeoutMs: number;
  readonly maximumOutputBytes: number;
}

export type ProcessCommandRunner = (
  request: ProcessCommandRequest
) => Promise<string>;

export interface ProcessInventoryPolicy {
  readonly perAttemptTimeoutMs: number;
  readonly maximumAttempts: number;
  readonly totalDeadlineMs: number;
  readonly backoffMs: readonly number[];
  readonly maximumOutputBytes: number;
}

export interface ProcessInventoryDependencies {
  readonly run?: ProcessCommandRunner;
  readonly now?: () => number;
  readonly delay?: (milliseconds: number) => Promise<void>;
  readonly platform?: NodeJS.Platform;
  readonly policy?: ProcessInventoryPolicy;
}

export interface ProcessInventoryQuery {
  readonly deadlineAt?: number;
}

export interface PackagedProcessInventory {
  query(options?: ProcessInventoryQuery): Promise<readonly ProcessIdentity[]>;
}

export interface AdoptProcessTreeInput {
  readonly current: readonly ProcessIdentity[];
  readonly baseline: readonly ProcessIdentity[];
  readonly owned: readonly OwnedProcess[];
  readonly rootPid: number;
  readonly packaged: PackagedProcessPaths;
}

const processInventoryScript = [
  "$ErrorActionPreference = 'Stop'",
  "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
  "$records = Get-CimInstance -ClassName Win32_Process -Filter \"Name = 'Cinematic Story Studio.exe' OR Name = 'cinematic-story-service.exe'\" -Property ProcessId,ParentProcessId,Name,ExecutablePath,CreationDate | ForEach-Object {",
  "  [PSCustomObject]@{",
  "    pid = [int]$_.ProcessId",
  "    parentPid = [int]$_.ParentProcessId",
  "    name = [string]$_.Name",
  "    executablePath = if ($null -eq $_.ExecutablePath) { $null } else { [string]$_.ExecutablePath }",
  "    creationDate = $_.CreationDate.ToUniversalTime().ToString('O', [Globalization.CultureInfo]::InvariantCulture)",
  "  }",
  "}",
  "[Console]::Out.Write((ConvertTo-Json -InputObject @($records) -Compress))"
].join("\n");

const processInventoryArguments = Object.freeze([
  "-NoLogo",
  "-NoProfile",
  "-NonInteractive",
  "-Command",
  processInventoryScript
]);

export function createPackagedProcessInventory(
  dependencies: ProcessInventoryDependencies = {}
): PackagedProcessInventory {
  const run = dependencies.run ?? runBoundedProcess;
  const now = dependencies.now ?? (() => performance.now());
  const delay = dependencies.delay ?? boundedDelay;
  const platform = dependencies.platform ?? process.platform;
  const policy = dependencies.policy ?? defaultProcessInventoryPolicy;
  validatePolicy(policy);

  return {
    async query(
      options: ProcessInventoryQuery = {}
    ): Promise<readonly ProcessIdentity[]> {
      if (platform !== "win32") {
        throw new ProcessInventoryError(
          "PROCESS_INVENTORY_UNSUPPORTED_PLATFORM",
          false
        );
      }
      const startedAt = now();
      const operationDeadline = Math.min(
        options.deadlineAt ?? Number.POSITIVE_INFINITY,
        startedAt + policy.totalDeadlineMs
      );
      let lastFailure: ProcessInventoryError | null = null;

      for (
        let attempt = 0;
        attempt < policy.maximumAttempts;
        attempt += 1
      ) {
        const remaining = operationDeadline - now();
        if (remaining <= 0) {
          break;
        }
        const attemptTimeout = Math.min(
          policy.perAttemptTimeoutMs,
          remaining
        );
        try {
          const output = await run({
            command: "powershell.exe",
            arguments: processInventoryArguments,
            timeoutMs: attemptTimeout,
            maximumOutputBytes: policy.maximumOutputBytes
          });
          if (
            Buffer.byteLength(output, "utf8") >
            policy.maximumOutputBytes
          ) {
            throw new ProcessInventoryError(
              "PROCESS_INVENTORY_OUTPUT_LIMIT",
              false
            );
          }
          return parseProcessInventory(output);
        } catch (error) {
          const failure = normalizeInventoryFailure(error);
          if (!failure.retryable) {
            throw failure;
          }
          lastFailure = failure;
        }

        if (attempt + 1 >= policy.maximumAttempts) {
          break;
        }
        const remainingAfterAttempt = operationDeadline - now();
        const backoff = policy.backoffMs[attempt] ?? 0;
        if (remainingAfterAttempt <= backoff) {
          break;
        }
        if (backoff > 0) {
          await delay(backoff);
        }
      }

      if (lastFailure !== null) {
        throw lastFailure;
      }
      throw new ProcessInventoryError(
        "PROCESS_INVENTORY_DEADLINE_EXCEEDED",
        false
      );
    }
  };
}

export function parseProcessInventory(
  output: string
): readonly ProcessIdentity[] {
  if (output.trim().length === 0) {
    throw new ProcessInventoryError(
      "PROCESS_INVENTORY_MALFORMED_OUTPUT",
      false
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch {
    throw new ProcessInventoryError(
      "PROCESS_INVENTORY_MALFORMED_OUTPUT",
      false
    );
  }
  if (
    !Array.isArray(parsed) ||
    parsed.length > maximumInventoryRecords
  ) {
    throw new ProcessInventoryError(
      "PROCESS_INVENTORY_MALFORMED_OUTPUT",
      false
    );
  }

  const identities = parsed.map(parseProcessIdentity);
  const seenPids = new Set<number>();
  for (const identity of identities) {
    if (seenPids.has(identity.pid)) {
      throw new ProcessInventoryError(
        "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        false
      );
    }
    seenPids.add(identity.pid);
  }
  return identities.sort((left, right) => left.pid - right.pid);
}

export function adoptVerifiedProcessTree({
  current,
  baseline,
  owned,
  rootPid,
  packaged
}: AdoptProcessTreeInput): readonly OwnedProcess[] {
  const root = owned.find(
    (item) => item.pid === rootPid && item.kind === "app"
  );
  if (root === undefined) {
    return [];
  }
  const result = [...owned];
  let adoptedInPass = true;
  while (adoptedInPass) {
    adoptedInPass = false;
    for (const item of current) {
      const priorPidIdentity = result.find(
        (candidate) => candidate.pid === item.pid
      );
      if (
        priorPidIdentity !== undefined &&
        !sameProcessIdentity(priorPidIdentity, item)
      ) {
        throw new ProcessInventoryError(
          "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
          false
        );
      }
      if (
        containsProcessIdentity(baseline, item) ||
        containsProcessIdentity(result, item)
      ) {
        continue;
      }
      const verifiedParent = result.find(
        (candidate) =>
          candidate.pid === item.parentPid &&
          candidate.pid !== item.pid
      );
      if (verifiedParent === undefined) {
        continue;
      }
      if (!containsProcessIdentity(current, verifiedParent)) {
        throw new ProcessInventoryError(
          "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
          false
        );
      }
      if (
        item.creationDate < root.creationDate ||
        item.creationDate < verifiedParent.creationDate ||
        item.executablePath === null ||
        !matchesPackagedProcessPath(item, packaged)
      ) {
        throw new ProcessInventoryError(
          "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
          false
        );
      }
      result.push({
        ...item,
        kind:
          item.name === serviceExecutableName ? "service" : "app"
      });
      adoptedInPass = true;
    }
  }
  return result.sort((left, right) => left.pid - right.pid);
}

export function containsProcessIdentity(
  values: readonly ProcessIdentity[],
  expected: ProcessIdentity
): boolean {
  return values.some((value) => sameProcessIdentity(value, expected));
}

export function sameProcessIdentity(
  left: ProcessIdentity,
  right: ProcessIdentity
): boolean {
  return (
    left.pid === right.pid &&
    left.parentPid === right.parentPid &&
    left.name === right.name &&
    left.creationDate === right.creationDate &&
    left.executablePath !== null &&
    right.executablePath !== null &&
    samePath(left.executablePath, right.executablePath)
  );
}

export function matchesPackagedProcessPath(
  candidate: ProcessIdentity,
  packaged: PackagedProcessPaths
): boolean {
  if (candidate.executablePath === null) {
    return false;
  }
  return candidate.name === appExecutableName
    ? samePath(candidate.executablePath, packaged.executablePath)
    : candidate.name === serviceExecutableName &&
        samePath(
          candidate.executablePath,
          packaged.serviceExecutablePath
        );
}

export function remainingOwnedProcesses(
  current: readonly ProcessIdentity[],
  owned: readonly OwnedProcess[]
): readonly OwnedProcess[] {
  return owned.filter((item) => {
    const samePid = current.find(
      (candidate) => candidate.pid === item.pid
    );
    if (
      samePid !== undefined &&
      !sameProcessIdentity(samePid, item)
    ) {
      throw new ProcessInventoryError(
        "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        false
      );
    }
    return samePid !== undefined;
  });
}

export async function runBoundedProcess(
  request: ProcessCommandRequest
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const child = spawn(request.command, [...request.arguments], {
      shell: false,
      stdio: ["ignore", "pipe", "ignore"],
      windowsHide: true
    });
    const chunks: Buffer[] = [];
    let total = 0;
    let settled = false;
    let abortError: ProcessInventoryError | null = null;
    let exitObserved = false;
    let helperExitTimer: NodeJS.Timeout | null = null;
    const helperExitGraceMs = Math.min(
      2_000,
      Math.max(0, Math.floor(request.timeoutMs / 4))
    );
    const commandTimeoutMs = Math.max(
      1,
      request.timeoutMs - helperExitGraceMs
    );
    const finish = (error: ProcessInventoryError | null, value = "") => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(commandTimer);
      if (helperExitTimer !== null) {
        clearTimeout(helperExitTimer);
      }
      if (error === null) {
        resolve(value);
      } else {
        reject(error);
      }
    };
    const abortAndConfirmExit = (error: ProcessInventoryError) => {
      if (settled || abortError !== null) {
        return;
      }
      abortError = error;
      if (exitObserved) {
        finish(error);
        return;
      }
      child.kill();
      helperExitTimer = setTimeout(
        () =>
          finish(
            new ProcessInventoryError(
              "PROCESS_INVENTORY_HELPER_EXIT_UNCONFIRMED",
              false
            )
          ),
        helperExitGraceMs
      );
    };
    const commandTimer = setTimeout(
      () =>
        abortAndConfirmExit(
          new ProcessInventoryError(
            "PROCESS_INVENTORY_TIMEOUT",
            true
          )
        ),
      commandTimeoutMs
    );
    child.stdout.on("data", (chunk: Buffer) => {
      if (abortError !== null) {
        return;
      }
      total += chunk.byteLength;
      if (total > request.maximumOutputBytes) {
        abortAndConfirmExit(
          new ProcessInventoryError(
            "PROCESS_INVENTORY_OUTPUT_LIMIT",
            false
          )
        );
        return;
      }
      chunks.push(chunk);
    });
    child.once("error", () => {
      if (abortError !== null) {
        finish(
          new ProcessInventoryError(
            "PROCESS_INVENTORY_HELPER_EXIT_UNCONFIRMED",
            false
          )
        );
        return;
      }
      finish(
        new ProcessInventoryError(
          "PROCESS_INVENTORY_COMMAND_START_FAILED",
          true
        )
      );
    });
    child.once("exit", () => {
      exitObserved = true;
      if (abortError !== null) {
        finish(abortError);
      }
    });
    child.once("close", (code) => {
      if (abortError !== null) {
        finish(abortError);
        return;
      }
      if (code !== 0) {
        finish(
          new ProcessInventoryError(
            "PROCESS_INVENTORY_COMMAND_FAILED",
            true
          )
        );
        return;
      }
      finish(
        null,
        Buffer.concat(chunks, total).toString("utf8").trim()
      );
    });
  });
}

function parseProcessIdentity(value: unknown): ProcessIdentity {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    throw new ProcessInventoryError(
      "PROCESS_INVENTORY_INVALID_IDENTITY",
      false
    );
  }
  const record = value as Record<string, unknown>;
  if (
    !Number.isSafeInteger(record.pid) ||
    (record.pid as number) <= 0 ||
    !Number.isSafeInteger(record.parentPid) ||
    (record.parentPid as number) < 0 ||
    typeof record.name !== "string" ||
    !relevantProcessNames.has(record.name) ||
    (record.executablePath !== null &&
      (typeof record.executablePath !== "string" ||
        !path.win32.isAbsolute(record.executablePath))) ||
    typeof record.creationDate !== "string" ||
    !isInvariantUtcTimestamp(record.creationDate)
  ) {
    throw new ProcessInventoryError(
      "PROCESS_INVENTORY_INVALID_IDENTITY",
      false
    );
  }
  return {
    pid: record.pid as number,
    parentPid: record.parentPid as number,
    name: record.name,
    executablePath: record.executablePath,
    creationDate: record.creationDate
  };
}

function samePath(left: string, right: string): boolean {
  return (
    path.win32.resolve(left).toLowerCase() ===
    path.win32.resolve(right).toLowerCase()
  );
}

function normalizeInventoryFailure(
  error: unknown
): ProcessInventoryError {
  return error instanceof ProcessInventoryError
    ? error
    : new ProcessInventoryError(
        "PROCESS_INVENTORY_COMMAND_FAILED",
        true
      );
}

function validatePolicy(policy: ProcessInventoryPolicy): void {
  if (
    !Number.isSafeInteger(policy.perAttemptTimeoutMs) ||
    policy.perAttemptTimeoutMs <= 0 ||
    !Number.isSafeInteger(policy.maximumAttempts) ||
    policy.maximumAttempts <= 0 ||
    policy.maximumAttempts > 3 ||
    !Number.isSafeInteger(policy.totalDeadlineMs) ||
    policy.totalDeadlineMs <= 0 ||
    policy.totalDeadlineMs > 45_000 ||
    !Number.isSafeInteger(policy.maximumOutputBytes) ||
    policy.maximumOutputBytes <= 0 ||
    policy.backoffMs.length < policy.maximumAttempts - 1 ||
    policy.backoffMs.some(
      (value) => !Number.isSafeInteger(value) || value < 0
    )
  ) {
    throw new Error("The packaged process inventory policy is invalid.");
  }
}

function isInvariantUtcTimestamp(value: string): boolean {
  const match =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{7})Z$/u.exec(
      value
    );
  if (match === null) {
    return false;
  }
  const parsed = new Date(`${match[1]}.${match[2].slice(0, 3)}Z`);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString() === `${match[1]}.${match[2].slice(0, 3)}Z`
  );
}

function processInventoryMessage(
  code: ProcessInventoryFailureCode
): string {
  switch (code) {
    case "PROCESS_INVENTORY_TIMEOUT":
      return "The packaged process inventory attempt timed out.";
    case "PROCESS_INVENTORY_DEADLINE_EXCEEDED":
      return "The packaged process inventory deadline was exceeded.";
    case "PROCESS_INVENTORY_COMMAND_FAILED":
      return "The packaged process inventory command failed.";
    case "PROCESS_INVENTORY_COMMAND_START_FAILED":
      return "The packaged process inventory command could not start.";
    case "PROCESS_INVENTORY_HELPER_EXIT_UNCONFIRMED":
      return "The packaged process inventory helper exit could not be confirmed.";
    case "PROCESS_INVENTORY_OUTPUT_LIMIT":
      return "The packaged process inventory output exceeded its limit.";
    case "PROCESS_INVENTORY_MALFORMED_OUTPUT":
      return "The packaged process inventory output was malformed.";
    case "PROCESS_INVENTORY_INVALID_IDENTITY":
      return "The packaged process inventory identity was invalid.";
    case "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY":
      return "The packaged process inventory identity was ambiguous.";
    case "PROCESS_INVENTORY_UNSUPPORTED_PLATFORM":
      return "Packaged process inventory requires Windows.";
  }
}

function boundedDelay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}
