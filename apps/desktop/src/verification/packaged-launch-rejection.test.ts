import { describe, expect, it } from "vitest";

import {
  packagedElectronFirstWindowTimeout,
  packagedElectronFirstWindowTimeoutMs,
  packagedElectronLaunchTimeout,
  packagedElectronLaunchTimeoutMs,
  observeRelevantProcessBaselineAfterRejectedLaunch
} from "./packaged-launch-rejection";
import {
  ProcessInventoryError,
  appExecutableName,
  serviceExecutableName,
  type ProcessIdentity
} from "./packaged-process-inventory";

const applicationPath =
  "C:\\Release\\Cinematic Story Studio.exe";
const servicePath =
  "C:\\Release\\resources\\service\\cinematic-story-service.exe";

describe("packaged launch rejection evidence", () => {
  it("keeps fixture launches at 45 seconds and real launches at 120 seconds", () => {
    expect(packagedElectronLaunchTimeoutMs).toEqual({
      synthetic_fixture: 45_000,
      phase3b1_real: 120_000
    });
    expect(packagedElectronLaunchTimeout("synthetic_fixture")).toBe(45_000);
    expect(packagedElectronLaunchTimeout("phase3b1_real")).toBe(120_000);
  });

  it("keeps fixture first windows at 45 seconds and real first windows at 120 seconds", () => {
    expect(packagedElectronFirstWindowTimeoutMs).toEqual({
      synthetic_fixture: 45_000,
      phase3b1_real: 120_000
    });
    expect(packagedElectronFirstWindowTimeout("synthetic_fixture")).toBe(
      45_000
    );
    expect(packagedElectronFirstWindowTimeout("phase3b1_real")).toBe(120_000);
  });

  it("proves only two consecutive delta-free allow-listed observations", async () => {
    const clock = createVirtualClock();
    const baseline = [application(4100, 100)];
    let queries = 0;

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline,
      deadlineAt: 1_000,
      now: clock.now,
      delay: clock.delay,
      pollIntervalMs: 25,
      queryCurrent: async () => {
        queries += 1;
        return [
          {
            ...baseline[0],
            parentPid: 999,
            executablePath: null
          }
        ];
      }
    });

    expect(queries).toBe(2);
    expect(evidence).toEqual({
      inventoryScope: "cinematic_story_app_and_service_names_only",
      baselineDeltaStatus: "observed_absent",
      requiredConsecutiveDeltaFreeObservations: 2,
      consecutiveDeltaFreeObservations: 2,
      observationCount: 2,
      baselineRelevantProcesses: [redacted(baseline[0])],
      newRelevantIdentitiesObserved: false,
      observedNewRelevantProcesses: [],
      finalNewRelevantProcesses: [],
      failureCode: null,
      ownershipEstablished: false,
      ownedProcessExitClaimed: false,
      cleanupClaimed: false,
      explicitHarnessTerminationAttempted: false,
      playwrightLaunchCleanupClaimed: false
    });
    expect(JSON.stringify(evidence)).not.toContain(applicationPath);
  });

  it("reports a persistent new relevant-process delta without claiming cleanup", async () => {
    const clock = createVirtualClock();
    const baseline = [application(4100, 100)];
    const leakedService = service(4200, 4100);

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline,
      deadlineAt: 100,
      now: clock.now,
      delay: clock.delay,
      pollIntervalMs: 50,
      queryCurrent: async () => [...baseline, leakedService]
    });

    expect(evidence.baselineDeltaStatus).toBe("observed_present");
    expect(evidence.failureCode).toBe(
      "POST_REJECTION_RELEVANT_PROCESS_DELTA_REMAINS"
    );
    expect(evidence.observationCount).toBe(2);
    expect(evidence.consecutiveDeltaFreeObservations).toBe(0);
    expect(evidence.newRelevantIdentitiesObserved).toBe(true);
    expect(evidence.observedNewRelevantProcesses).toEqual([
      redacted(leakedService)
    ]);
    expect(evidence.finalNewRelevantProcesses).toEqual([
      redacted(leakedService)
    ]);
    expect(evidence.ownershipEstablished).toBe(false);
    expect(evidence.ownedProcessExitClaimed).toBe(false);
    expect(evidence.cleanupClaimed).toBe(false);
    expect(evidence.explicitHarnessTerminationAttempted).toBe(false);
    expect(evidence.playwrightLaunchCleanupClaimed).toBe(false);
  });

  it("retains that a new identity was seen before two clean observations", async () => {
    const clock = createVirtualClock();
    const baseline = [application(4100, 100)];
    const transient = application(4200, 4100, "2026-08-03T00:00:01.0000000Z");
    const inventories = [
      [...baseline, transient],
      baseline,
      baseline
    ];

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline,
      deadlineAt: 1_000,
      now: clock.now,
      delay: clock.delay,
      pollIntervalMs: 25,
      queryCurrent: async () => inventories.shift() ?? baseline
    });

    expect(evidence.baselineDeltaStatus).toBe("observed_absent");
    expect(evidence.observationCount).toBe(3);
    expect(evidence.consecutiveDeltaFreeObservations).toBe(2);
    expect(evidence.newRelevantIdentitiesObserved).toBe(true);
    expect(evidence.observedNewRelevantProcesses).toEqual([
      redacted(transient)
    ]);
    expect(evidence.finalNewRelevantProcesses).toEqual([]);
    expect(evidence.ownedProcessExitClaimed).toBe(false);
  });

  it("fails closed on PID reuse with a different creation identity", async () => {
    const baseline = [application(4100, 100)];
    const reused = application(
      4100,
      200,
      "2026-08-03T00:00:02.0000000Z"
    );

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline,
      deadlineAt: 1_000,
      now: () => 0,
      queryCurrent: async () => [reused]
    });

    expect(evidence.baselineDeltaStatus).toBe("unproved");
    expect(evidence.failureCode).toBe(
      "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
    );
    expect(evidence.observationCount).toBe(0);
    expect(evidence.cleanupClaimed).toBe(false);
  });

  it("fails closed when an exact baseline PID resolves to another path", async () => {
    const baseline = [application(4100, 100)];
    const wrongPath = {
      ...baseline[0],
      executablePath: "C:\\Other\\Cinematic Story Studio.exe"
    };

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline,
      deadlineAt: 1_000,
      now: () => 0,
      queryCurrent: async () => [wrongPath]
    });

    expect(evidence.baselineDeltaStatus).toBe("unproved");
    expect(evidence.failureCode).toBe(
      "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
    );
    expect(evidence.ownershipEstablished).toBe(false);
  });

  it("preserves a bounded process-inventory failure code", async () => {
    const baseline = [application(4100, 100)];

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline,
      deadlineAt: 1_000,
      now: () => 0,
      queryCurrent: async () => {
        throw new ProcessInventoryError(
          "PROCESS_INVENTORY_TIMEOUT",
          false
        );
      }
    });

    expect(evidence.baselineDeltaStatus).toBe("unproved");
    expect(evidence.failureCode).toBe("PROCESS_INVENTORY_TIMEOUT");
    expect(evidence.observationCount).toBe(0);
    expect(evidence.cleanupClaimed).toBe(false);
  });

  it("does not turn one clean observation into an exit or cleanup claim", async () => {
    const clock = createVirtualClock();
    const baseline = [application(4100, 100)];

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline,
      deadlineAt: 50,
      now: clock.now,
      delay: clock.delay,
      pollIntervalMs: 50,
      queryCurrent: async () => baseline
    });

    expect(evidence.baselineDeltaStatus).toBe("unproved");
    expect(evidence.failureCode).toBe(
      "POST_REJECTION_OBSERVATION_DEADLINE_EXCEEDED"
    );
    expect(evidence.observationCount).toBe(1);
    expect(evidence.consecutiveDeltaFreeObservations).toBe(1);
    expect(evidence.ownedProcessExitClaimed).toBe(false);
    expect(evidence.cleanupClaimed).toBe(false);
  });

  it("bounds cumulative new-identity evidence and fails closed", async () => {
    const clock = createVirtualClock();
    const firstInventory = Array.from({ length: 256 }, (_, index) =>
      application(
        5_000 + index,
        100,
        `2026-08-03T00:00:${String(index % 60).padStart(2, "0")}.${String(index).padStart(7, "0")}Z`
      )
    );
    const nextIdentity = service(
      6_000,
      100,
      "2026-08-03T00:01:00.0000000Z"
    );
    const inventories: readonly ProcessIdentity[][] = [
      firstInventory,
      [nextIdentity]
    ];
    let queryIndex = 0;

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline: [],
      deadlineAt: 1_000,
      now: clock.now,
      delay: clock.delay,
      pollIntervalMs: 25,
      queryCurrent: async () => inventories[queryIndex++] ?? [nextIdentity]
    });

    expect(evidence.baselineDeltaStatus).toBe("unproved");
    expect(evidence.failureCode).toBe("PROCESS_INVENTORY_OUTPUT_LIMIT");
    expect(evidence.observationCount).toBe(2);
    expect(evidence.observedNewRelevantProcesses).toHaveLength(256);
    expect(evidence.cleanupClaimed).toBe(false);
    expect(evidence.ownedProcessExitClaimed).toBe(false);
  });

  it("rejects duplicate baseline PIDs before querying", async () => {
    let queried = false;
    const duplicate = application(4100, 100);

    const evidence = await observeRelevantProcessBaselineAfterRejectedLaunch({
      baseline: [duplicate, duplicate],
      deadlineAt: 1_000,
      now: () => 0,
      queryCurrent: async () => {
        queried = true;
        return [];
      }
    });

    expect(queried).toBe(false);
    expect(evidence.baselineDeltaStatus).toBe("unproved");
    expect(evidence.failureCode).toBe(
      "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
    );
  });
});

function application(
  pid: number,
  parentPid: number,
  creationDate = "2026-08-03T00:00:00.0000000Z"
): ProcessIdentity {
  return {
    pid,
    parentPid,
    name: appExecutableName,
    executablePath: applicationPath,
    creationDate
  };
}

function service(
  pid: number,
  parentPid: number,
  creationDate = "2026-08-03T00:00:00.0000000Z"
): ProcessIdentity {
  return {
    pid,
    parentPid,
    name: serviceExecutableName,
    executablePath: servicePath,
    creationDate
  };
}

function redacted(value: ProcessIdentity) {
  return {
    pid: value.pid,
    parentPid: value.parentPid,
    name: value.name,
    creationDate: value.creationDate
  };
}

function createVirtualClock() {
  let current = 0;
  return {
    now: () => current,
    delay: async (milliseconds: number) => {
      current += milliseconds;
    }
  };
}
