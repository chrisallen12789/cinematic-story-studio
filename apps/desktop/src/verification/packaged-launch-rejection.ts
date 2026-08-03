import path from "node:path";
import { performance } from "node:perf_hooks";

import {
  ProcessInventoryError,
  appExecutableName,
  serviceExecutableName,
  type ProcessIdentity,
  type ProcessInventoryFailureCode
} from "./packaged-process-inventory";

export const packagedElectronLaunchTimeoutMs = Object.freeze({
  synthetic_fixture: 45_000,
  phase3b1_real: 120_000
});

export type PackagedElectronLaunchPurpose =
  keyof typeof packagedElectronLaunchTimeoutMs;

export type RejectedLaunchBaselineDeltaStatus =
  | "observed_absent"
  | "observed_present"
  | "unproved";

export type RejectedLaunchBaselineFailureCode =
  | ProcessInventoryFailureCode
  | "POST_REJECTION_RELEVANT_PROCESS_DELTA_REMAINS"
  | "POST_REJECTION_OBSERVATION_DEADLINE_EXCEEDED";

export interface RedactedRelevantProcessIdentity {
  readonly pid: number;
  readonly parentPid: number;
  readonly name: string;
  readonly creationDate: string;
}

export interface RejectedLaunchBaselineObservation {
  readonly inventoryScope:
    "cinematic_story_app_and_service_names_only";
  readonly baselineDeltaStatus: RejectedLaunchBaselineDeltaStatus;
  readonly requiredConsecutiveDeltaFreeObservations: 2;
  readonly consecutiveDeltaFreeObservations: number;
  readonly observationCount: number;
  readonly baselineRelevantProcesses:
    readonly RedactedRelevantProcessIdentity[];
  readonly newRelevantIdentitiesObserved: boolean;
  readonly observedNewRelevantProcesses:
    readonly RedactedRelevantProcessIdentity[];
  readonly finalNewRelevantProcesses:
    readonly RedactedRelevantProcessIdentity[];
  readonly failureCode: RejectedLaunchBaselineFailureCode | null;
  readonly ownershipEstablished: false;
  readonly ownedProcessExitClaimed: false;
  readonly cleanupClaimed: false;
  readonly explicitHarnessTerminationAttempted: false;
  readonly playwrightLaunchCleanupClaimed: false;
}

export interface ObserveRejectedLaunchBaselineInput {
  readonly baseline: readonly ProcessIdentity[];
  readonly queryCurrent: (
    deadlineAt: number
  ) => Promise<readonly ProcessIdentity[]>;
  readonly deadlineAt: number;
  readonly now?: () => number;
  readonly delay?: (milliseconds: number) => Promise<void>;
  readonly pollIntervalMs?: number;
}

const requiredConsecutiveDeltaFreeObservations = 2 as const;
const defaultPollIntervalMs = 250;
const maximumObservedNewProcessIdentities = 256;
const relevantProcessNames = new Set([
  appExecutableName,
  serviceExecutableName
]);

export function packagedElectronLaunchTimeout(
  purpose: PackagedElectronLaunchPurpose
): number {
  return packagedElectronLaunchTimeoutMs[purpose];
}

/**
 * Observes only the allow-listed application and service inventory after
 * Playwright rejects an Electron launch. This function cannot establish
 * ownership or terminate anything; two empty baseline deltas prove only what
 * the two bounded inventory snapshots observed.
 */
export async function observeRelevantProcessBaselineAfterRejectedLaunch({
  baseline,
  queryCurrent,
  deadlineAt,
  now = () => performance.now(),
  delay = boundedDelay,
  pollIntervalMs = defaultPollIntervalMs
}: ObserveRejectedLaunchBaselineInput): Promise<RejectedLaunchBaselineObservation> {
  if (
    !Number.isFinite(deadlineAt) ||
    !Number.isSafeInteger(pollIntervalMs) ||
    pollIntervalMs <= 0
  ) {
    throw new Error("The rejected-launch observation policy is invalid.");
  }

  const baselineRelevantProcesses = redactProcesses(
    baseline.slice(0, maximumObservedNewProcessIdentities)
  );
  const observedNewProcesses = new Map<
    string,
    RedactedRelevantProcessIdentity
  >();
  let observationCount = 0;
  let consecutiveDeltaFreeObservations = 0;
  let finalNewRelevantProcesses: readonly RedactedRelevantProcessIdentity[] =
    [];

  try {
    validateIdentitySet(baseline);
  } catch (error) {
    return buildObservation({
      baselineRelevantProcesses,
      observationCount,
      consecutiveDeltaFreeObservations,
      observedNewProcesses,
      finalNewRelevantProcesses,
      baselineDeltaStatus: "unproved",
      failureCode: inventoryFailureCode(error)
    });
  }

  while (now() < deadlineAt) {
    let current: readonly ProcessIdentity[];
    try {
      current = await queryCurrent(deadlineAt);
    } catch (error) {
      return buildObservation({
        baselineRelevantProcesses,
        observationCount,
        consecutiveDeltaFreeObservations,
        observedNewProcesses,
        finalNewRelevantProcesses,
        baselineDeltaStatus: "unproved",
        failureCode: inventoryFailureCode(error)
      });
    }
    if (now() > deadlineAt) {
      return buildObservation({
        baselineRelevantProcesses,
        observationCount,
        consecutiveDeltaFreeObservations,
        observedNewProcesses,
        finalNewRelevantProcesses,
        baselineDeltaStatus: "unproved",
        failureCode: "POST_REJECTION_OBSERVATION_DEADLINE_EXCEEDED"
      });
    }

    let delta: readonly ProcessIdentity[];
    try {
      validateIdentitySet(current);
      delta = relevantProcessBaselineDelta(baseline, current);
    } catch (error) {
      return buildObservation({
        baselineRelevantProcesses,
        observationCount,
        consecutiveDeltaFreeObservations,
        observedNewProcesses,
        finalNewRelevantProcesses,
        baselineDeltaStatus: "unproved",
        failureCode: inventoryFailureCode(error)
      });
    }

    observationCount += 1;
    finalNewRelevantProcesses = redactProcesses(delta);
    for (const item of finalNewRelevantProcesses) {
      if (
        !observedNewProcesses.has(stableEvidenceKey(item)) &&
        observedNewProcesses.size >= maximumObservedNewProcessIdentities
      ) {
        return buildObservation({
          baselineRelevantProcesses,
          observationCount,
          consecutiveDeltaFreeObservations,
          observedNewProcesses,
          finalNewRelevantProcesses,
          baselineDeltaStatus: "unproved",
          failureCode: "PROCESS_INVENTORY_OUTPUT_LIMIT"
        });
      }
      observedNewProcesses.set(stableEvidenceKey(item), item);
    }
    if (delta.length === 0) {
      consecutiveDeltaFreeObservations += 1;
      if (
        consecutiveDeltaFreeObservations ===
        requiredConsecutiveDeltaFreeObservations
      ) {
        return buildObservation({
          baselineRelevantProcesses,
          observationCount,
          consecutiveDeltaFreeObservations,
          observedNewProcesses,
          finalNewRelevantProcesses,
          baselineDeltaStatus: "observed_absent",
          failureCode: null
        });
      }
    } else {
      consecutiveDeltaFreeObservations = 0;
    }

    const remainingMilliseconds = deadlineAt - now();
    if (remainingMilliseconds <= 0) {
      break;
    }
    await delay(Math.min(pollIntervalMs, remainingMilliseconds));
  }

  const deltaRemains = finalNewRelevantProcesses.length > 0;
  return buildObservation({
    baselineRelevantProcesses,
    observationCount,
    consecutiveDeltaFreeObservations,
    observedNewProcesses,
    finalNewRelevantProcesses,
    baselineDeltaStatus: deltaRemains ? "observed_present" : "unproved",
    failureCode: deltaRemains
      ? "POST_REJECTION_RELEVANT_PROCESS_DELTA_REMAINS"
      : "POST_REJECTION_OBSERVATION_DEADLINE_EXCEEDED"
  });
}

interface ObservationParts {
  readonly baselineRelevantProcesses:
    readonly RedactedRelevantProcessIdentity[];
  readonly observationCount: number;
  readonly consecutiveDeltaFreeObservations: number;
  readonly observedNewProcesses: ReadonlyMap<
    string,
    RedactedRelevantProcessIdentity
  >;
  readonly finalNewRelevantProcesses:
    readonly RedactedRelevantProcessIdentity[];
  readonly baselineDeltaStatus: RejectedLaunchBaselineDeltaStatus;
  readonly failureCode: RejectedLaunchBaselineFailureCode | null;
}

function buildObservation({
  baselineRelevantProcesses,
  observationCount,
  consecutiveDeltaFreeObservations,
  observedNewProcesses,
  finalNewRelevantProcesses,
  baselineDeltaStatus,
  failureCode
}: ObservationParts): RejectedLaunchBaselineObservation {
  const observedNewRelevantProcesses = [...observedNewProcesses.values()].sort(
    compareEvidenceIdentity
  );
  return {
    inventoryScope: "cinematic_story_app_and_service_names_only",
    baselineDeltaStatus,
    requiredConsecutiveDeltaFreeObservations,
    consecutiveDeltaFreeObservations,
    observationCount,
    baselineRelevantProcesses,
    newRelevantIdentitiesObserved: observedNewRelevantProcesses.length > 0,
    observedNewRelevantProcesses,
    finalNewRelevantProcesses,
    failureCode,
    ownershipEstablished: false,
    ownedProcessExitClaimed: false,
    cleanupClaimed: false,
    explicitHarnessTerminationAttempted: false,
    playwrightLaunchCleanupClaimed: false
  };
}

function relevantProcessBaselineDelta(
  baseline: readonly ProcessIdentity[],
  current: readonly ProcessIdentity[]
): readonly ProcessIdentity[] {
  const baselineByPid = new Map(baseline.map((item) => [item.pid, item]));
  const delta: ProcessIdentity[] = [];
  for (const item of current) {
    const prior = baselineByPid.get(item.pid);
    if (prior === undefined) {
      delta.push(item);
      continue;
    }
    if (
      prior.name !== item.name ||
      prior.creationDate !== item.creationDate ||
      (prior.executablePath !== null &&
        item.executablePath !== null &&
        !samePath(prior.executablePath, item.executablePath))
    ) {
      throw new ProcessInventoryError(
        "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        false
      );
    }
    /*
     * Parentage may change during process exit. A null path is conservative
     * continuity only: it suppresses a false "new process" classification and
     * never upgrades the identity to owned.
     */
  }
  return delta.sort(compareProcessIdentity);
}

function validateIdentitySet(values: readonly ProcessIdentity[]): void {
  if (values.length > maximumObservedNewProcessIdentities) {
    throw new ProcessInventoryError(
      "PROCESS_INVENTORY_OUTPUT_LIMIT",
      false
    );
  }
  const seenPids = new Set<number>();
  for (const item of values) {
    if (
      !Number.isSafeInteger(item.pid) ||
      item.pid <= 0 ||
      !Number.isSafeInteger(item.parentPid) ||
      item.parentPid < 0 ||
      !relevantProcessNames.has(item.name) ||
      (item.executablePath !== null &&
        (!path.win32.isAbsolute(item.executablePath) ||
          item.executablePath.length === 0)) ||
      !isInvariantUtcTimestamp(item.creationDate)
    ) {
      throw new ProcessInventoryError(
        "PROCESS_INVENTORY_INVALID_IDENTITY",
        false
      );
    }
    if (seenPids.has(item.pid)) {
      throw new ProcessInventoryError(
        "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        false
      );
    }
    seenPids.add(item.pid);
  }
}

function inventoryFailureCode(error: unknown): ProcessInventoryFailureCode {
  return error instanceof ProcessInventoryError
    ? error.code
    : "PROCESS_INVENTORY_COMMAND_FAILED";
}

function redactProcesses(
  values: readonly ProcessIdentity[]
): readonly RedactedRelevantProcessIdentity[] {
  return values
    .map((item) => ({
      pid: item.pid,
      parentPid: item.parentPid,
      name: item.name,
      creationDate: item.creationDate
    }))
    .sort(compareEvidenceIdentity);
}

function stableEvidenceKey(value: RedactedRelevantProcessIdentity): string {
  return `${value.pid}\u0000${value.name}\u0000${value.creationDate}`;
}

function compareProcessIdentity(
  left: ProcessIdentity,
  right: ProcessIdentity
): number {
  return left.pid - right.pid || left.creationDate.localeCompare(right.creationDate);
}

function compareEvidenceIdentity(
  left: RedactedRelevantProcessIdentity,
  right: RedactedRelevantProcessIdentity
): number {
  return left.pid - right.pid || left.creationDate.localeCompare(right.creationDate);
}

function samePath(left: string, right: string): boolean {
  return (
    path.win32.resolve(left).toLowerCase() ===
    path.win32.resolve(right).toLowerCase()
  );
}

function isInvariantUtcTimestamp(value: string): boolean {
  const match =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{7})Z$/u.exec(value);
  if (match === null) {
    return false;
  }
  const parsed = new Date(`${match[1]}.${match[2].slice(0, 3)}Z`);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString() === `${match[1]}.${match[2].slice(0, 3)}Z`
  );
}

function boundedDelay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}
