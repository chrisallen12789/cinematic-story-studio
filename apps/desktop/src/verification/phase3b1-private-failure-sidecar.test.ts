import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  maximumPhase3b1PrivateFailureSidecarBytes,
  phase3b1PrivateFailureSidecarSchemaVersion,
  writePhase3b1PrivateFailureSidecar,
  type RejectedLaunchBaselineObservation,
  type WritePhase3b1PrivateFailureSidecarInput
} from "./phase3b1-private-failure-sidecar";

const temporaryRoots: string[] = [];
const recordedAt = new Date("2026-08-02T23:30:00.000Z");
const fixedToken = "a".repeat(32);

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) =>
      rm(root, { recursive: true, force: true })
    )
  );
});

describe("Phase 3B.1 private failure sidecar", () => {
  it("publishes schema 2 with only the exact governed failure keys", async () => {
    const roots = await createEvidenceRoots();
    const input = sidecarInput(roots);

    const result = await writePhase3b1PrivateFailureSidecar(input, {
      now: () => recordedAt,
      tokenFactory: () => fixedToken
    });
    const sidecarPath = path.join(roots.privateRoot, result.relativePath);
    const serialized = await readFile(sidecarPath, "utf8");
    const value = JSON.parse(serialized) as Record<string, unknown>;

    expect(result.relativePath).toBe(`failures/${result.fileName}`);
    expect(result.fileName).toBe(
      `phase3b1-failure-launch-3-20260802T233000000Z-${fixedToken}.json`
    );
    expect(result.byteSize).toBe(Buffer.byteLength(serialized, "utf8"));
    expect(result.byteSize).toBeLessThanOrEqual(
      maximumPhase3b1PrivateFailureSidecarBytes
    );
    expect(value).toEqual(result.value);
    expect(value.schemaVersion).toBe(
      phase3b1PrivateFailureSidecarSchemaVersion
    );
    expect(Object.keys(value)).toEqual([
      "schemaVersion",
      "result",
      "sourceHeadSha",
      "applicationVersion",
      "executableRelativePath",
      "launch",
      "stage",
      "failureCode",
      "configuredLaunchTimeoutMs",
      "configuredFirstWindowTimeoutMs",
      "startedAt",
      "launchReturnedAt",
      "firstWindowWaitStartedAt",
      "failedAt",
      "recordedAt",
      "startupObservations",
      "syntheticGateCompleted",
      "ownershipEstablished",
      "ownedProcessExitClaimed",
      "cleanupCompleted",
      "rejectedLaunchBaselineObservation",
      "claims"
    ]);
    expect(Object.keys(value.claims as Record<string, unknown>)).toEqual([
      "humanListeningClaimed",
      "naturalnessClaimed",
      "qualityClaimed",
      "consentClaimed",
      "commercialClearanceClaimed",
      "productionReadinessClaimed"
    ]);
    expect(Object.values(value.claims as Record<string, unknown>)).toEqual([
      false,
      false,
      false,
      false,
      false,
      false
    ]);
    expect(
      Object.keys(
        value.rejectedLaunchBaselineObservation as Record<string, unknown>
      )
    ).toEqual([
      "baselineDeltaStatus",
      "baselineRelevantProcesses",
      "cleanupClaimed",
      "consecutiveDeltaFreeObservations",
      "explicitHarnessTerminationAttempted",
      "failureCode",
      "finalNewRelevantProcesses",
      "inventoryScope",
      "newRelevantIdentitiesObserved",
      "observationCount",
      "observedNewRelevantProcesses",
      "ownedProcessExitClaimed",
      "ownershipEstablished",
      "playwrightLaunchCleanupClaimed",
      "requiredConsecutiveDeltaFreeObservations"
    ]);
    expect(await readdir(path.join(roots.privateRoot, "failures"))).toEqual([
      result.fileName
    ]);
  });

  it("records bounded startup probes and the governed startup codes", async () => {
    const roots = await createEvidenceRoots();
    const startupObservations = [
      {
        phase: "after_root_ownership" as const,
        recordedAt: "2026-08-02T23:20:02.000Z",
        appReady: true,
        singleInstanceLockHeld: true,
        browserWindowCount: 0
      },
      {
        phase: "after_first_window_failure" as const,
        recordedAt: "2026-08-02T23:22:02.000Z",
        appReady: true,
        singleInstanceLockHeld: true,
        browserWindowCount: 0
      }
    ];

    const result = await writePhase3b1PrivateFailureSidecar(
      {
        ...sidecarInput(roots),
        stage: "readiness_3",
        failureCode: "first_window_timeout",
        configuredFirstWindowTimeoutMs: 120_000,
        launchReturnedAt: "2026-08-02T23:20:01.000Z",
        firstWindowWaitStartedAt: "2026-08-02T23:20:03.000Z",
        startupObservations,
        rejectedLaunchBaselineObservation: null
      },
      { now: () => recordedAt }
    );

    expect(result.value.configuredFirstWindowTimeoutMs).toBe(120_000);
    expect(result.value.launchReturnedAt).toBe(
      "2026-08-02T23:20:01.000Z"
    );
    expect(result.value.firstWindowWaitStartedAt).toBe(
      "2026-08-02T23:20:03.000Z"
    );
    expect(result.value.startupObservations).toEqual(startupObservations);

    for (const failureCode of [
      "single_instance_lock_not_held",
      "startup_probe_failed"
    ] as const) {
      const codeResult = await writePhase3b1PrivateFailureSidecar(
        {
          ...sidecarInput(roots),
          stage: "root_ownership_3",
          failureCode,
          rejectedLaunchBaselineObservation: null
        },
        { now: () => recordedAt }
      );
      expect(codeResult.value.failureCode).toBe(failureCode);
    }
  });

  it("rejects non-exact or unbounded startup observations", async () => {
    const roots = await createEvidenceRoots();
    const validObservation = {
      phase: "after_root_ownership" as const,
      recordedAt: "2026-08-02T23:20:02.000Z",
      appReady: true,
      singleInstanceLockHeld: true,
      browserWindowCount: 0
    };
    const startupInput = {
      ...sidecarInput(roots),
      launchReturnedAt: "2026-08-02T23:20:01.000Z",
      firstWindowWaitStartedAt: "2026-08-02T23:20:03.000Z",
      rejectedLaunchBaselineObservation: null
    };

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...startupInput,
        startupObservations: [
          validObservation,
          {
            ...validObservation,
            phase: "after_first_window_failure",
            recordedAt: "2026-08-02T23:20:04.000Z"
          },
          {
            ...validObservation,
            phase: "after_first_window_failure",
            recordedAt: "2026-08-02T23:20:05.000Z"
          }
        ]
      })
    ).rejects.toThrow("startup observations were invalid");

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...startupInput,
        startupObservations: [
          { ...validObservation, browserWindowCount: 257 }
        ]
      })
    ).rejects.toThrow("startup observation was invalid");

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...startupInput,
        startupObservations: [
          { ...validObservation, extra: "not governed" }
        ]
      } as unknown as WritePhase3b1PrivateFailureSidecarInput)
    ).rejects.toThrow("observation fields were invalid");
  });

  it("rejects missing or chronologically invalid startup timestamps", async () => {
    const roots = await createEvidenceRoots();
    const validObservation = {
      phase: "after_root_ownership" as const,
      recordedAt: "2026-08-02T23:20:02.000Z",
      appReady: true,
      singleInstanceLockHeld: true,
      browserWindowCount: 0
    };

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        configuredFirstWindowTimeoutMs: undefined
      } as unknown as WritePhase3b1PrivateFailureSidecarInput)
    ).rejects.toThrow("first-window timeout was invalid");

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        launchReturnedAt: "2026-08-02T23:20:02.000Z",
        firstWindowWaitStartedAt: "2026-08-02T23:20:01.000Z"
      })
    ).rejects.toThrow("timestamps were out of order");

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        launchReturnedAt: "2026-08-02T23:20:01.000Z",
        firstWindowWaitStartedAt: "2026-08-02T23:20:03.000Z",
        startupObservations: [
          {
            ...validObservation,
            phase: "after_first_window_failure",
            recordedAt: "2026-08-02T23:20:02.000Z"
          }
        ]
      })
    ).rejects.toThrow("preceded its wait");

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        launchReturnedAt: "2026-08-02T23:20:01.000Z",
        firstWindowWaitStartedAt: "2026-08-02T23:20:03.000Z",
        startupObservations: [
          {
            ...validObservation,
            recordedAt: "2026-08-02T23:20:04.000Z"
          }
        ]
      })
    ).rejects.toThrow("followed the first-window wait");
  });

  it("rejects roots that are not canonical direct children", async () => {
    const roots = await createEvidenceRoots();
    const nestedRoot = path.join(roots.privateRoot, "nested");
    await mkdir(nestedRoot);

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        privateRoot: nestedRoot
      })
    ).rejects.toThrow("direct child");
  });

  it("safely creates an absent direct-child private root", async () => {
    const roots = await createEvidenceRoots();
    const prospectiveRoot = path.join(
      roots.expectedLocalRendersParent,
      "prospective-private-run"
    );

    const result = await writePhase3b1PrivateFailureSidecar(
      {
        ...sidecarInput(roots),
        privateRoot: prospectiveRoot
      },
      { now: () => recordedAt }
    );

    expect(
      JSON.parse(
        await readFile(path.join(prospectiveRoot, result.relativePath), "utf8")
      )
    ).toEqual(result.value);
    expect(await readdir(prospectiveRoot)).toEqual(["failures"]);
  });

  it("rejects a symlinked private root and a symlinked failures child", async () => {
    const roots = await createEvidenceRoots();
    const outside = path.join(roots.testRoot, "outside");
    const linkedPrivateRoot = path.join(
      roots.expectedLocalRendersParent,
      "linked-private"
    );
    await mkdir(outside);
    await symlink(
      outside,
      linkedPrivateRoot,
      process.platform === "win32" ? "junction" : "dir"
    );

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        privateRoot: linkedPrivateRoot
      })
    ).rejects.toThrow("not canonical");

    const failures = path.join(roots.privateRoot, "failures");
    await symlink(
      outside,
      failures,
      process.platform === "win32" ? "junction" : "dir"
    );
    await expect(
      writePhase3b1PrivateFailureSidecar(sidecarInput(roots))
    ).rejects.toThrow("not canonical");
    expect(await readdir(outside)).toEqual([]);
  });

  it("enforces the serialized-byte ceiling before publishing", async () => {
    const roots = await createEvidenceRoots();
    const oversizedObservation = {
      ...rejectedLaunchObservation(),
      boundedPadding: Array.from({ length: 32 }, () => "x".repeat(4096))
    } as unknown as RejectedLaunchBaselineObservation;

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        rejectedLaunchBaselineObservation: oversizedObservation
      })
    ).rejects.toThrow("exceeded its size limit");
    expect(await readdir(path.join(roots.privateRoot, "failures"))).toEqual(
      []
    );
  });

  it("never copies raw errors or personal absolute paths", async () => {
    const roots = await createEvidenceRoots();
    const rawError = new Error(
      "private failure at C:\\Synthetic-Private\\project\\story.db"
    );
    rawError.stack =
      "Error: private failure\n at C:\\Synthetic-Private\\source\\secret.ts:1";
    const inputWithRawError = {
      ...sidecarInput(roots),
      error: rawError,
      message: rawError.message,
      stack: rawError.stack
    } as WritePhase3b1PrivateFailureSidecarInput;

    const result = await writePhase3b1PrivateFailureSidecar(
      inputWithRawError,
      {
        now: () => recordedAt
      }
    );
    const serialized = await readFile(
      path.join(roots.privateRoot, result.relativePath),
      "utf8"
    );
    expect(serialized).not.toContain("Synthetic-Private");
    expect(serialized).not.toContain("story.db");
    expect(serialized).not.toContain(rawError.message);
    expect(serialized).not.toContain("\\source\\secret.ts");
    expect(serialized).not.toContain('"message"');
    expect(serialized).not.toContain('"stack"');

    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        executableRelativePath:
          "C:\\Synthetic-Private\\release\\Cinematic Story Studio.exe"
      })
    ).rejects.toThrow("repository-relative");
    await expect(
      writePhase3b1PrivateFailureSidecar({
        ...sidecarInput(roots),
        rejectedLaunchBaselineObservation: {
          ...rejectedLaunchObservation(),
          message: rawError.message
        } as unknown as RejectedLaunchBaselineObservation
      })
    ).rejects.toThrow("key was unsafe");
  });

  it("uses exclusive atomic publication and never replaces a sidecar", async () => {
    const roots = await createEvidenceRoots();
    const input = sidecarInput(roots);
    const options = {
      now: () => recordedAt,
      tokenFactory: () => fixedToken
    };

    const first = await writePhase3b1PrivateFailureSidecar(input, options);
    const sidecarPath = path.join(roots.privateRoot, first.relativePath);
    const original = await readFile(sidecarPath, "utf8");

    await expect(
      writePhase3b1PrivateFailureSidecar(
        { ...input, cleanupCompleted: true },
        options
      )
    ).rejects.toThrow("target already exists");
    expect(await readFile(sidecarPath, "utf8")).toBe(original);
    expect(await readdir(path.dirname(sidecarPath))).toEqual([
      first.fileName
    ]);
  });
});

interface EvidenceRoots {
  readonly testRoot: string;
  readonly expectedLocalRendersParent: string;
  readonly privateRoot: string;
}

async function createEvidenceRoots(): Promise<EvidenceRoots> {
  const testRoot = await mkdtemp(
    path.join(tmpdir(), "css-phase3b1-private-failure-test-")
  );
  temporaryRoots.push(testRoot);
  const expectedLocalRendersParent = path.join(testRoot, "local-renders");
  const privateRoot = path.join(
    expectedLocalRendersParent,
    "phase3b1-private-run"
  );
  await mkdir(expectedLocalRendersParent);
  await mkdir(privateRoot);
  return { testRoot, expectedLocalRendersParent, privateRoot };
}

function sidecarInput(
  roots: Pick<
    EvidenceRoots,
    "expectedLocalRendersParent" | "privateRoot"
  >
): WritePhase3b1PrivateFailureSidecarInput {
  return {
    expectedLocalRendersParent: roots.expectedLocalRendersParent,
    privateRoot: roots.privateRoot,
    sourceHeadSha: "a".repeat(40),
    applicationVersion: "0.1.0",
    executableRelativePath:
      "apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
    launch: 3,
    stage: "launch_3",
    failureCode: "launch_timeout",
    configuredLaunchTimeoutMs: 120_000,
    configuredFirstWindowTimeoutMs: 120_000,
    startedAt: "2026-08-02T23:20:00.000Z",
    launchReturnedAt: null,
    firstWindowWaitStartedAt: null,
    failedAt: "2026-08-02T23:29:59.000Z",
    startupObservations: [],
    syntheticGateCompleted: true,
    ownershipEstablished: false,
    ownedProcessExitClaimed: false,
    cleanupCompleted: false,
    rejectedLaunchBaselineObservation: rejectedLaunchObservation()
  };
}

function rejectedLaunchObservation(): RejectedLaunchBaselineObservation {
  return {
    inventoryScope: "cinematic_story_app_and_service_names_only",
    baselineDeltaStatus: "observed_absent",
    requiredConsecutiveDeltaFreeObservations: 2,
    consecutiveDeltaFreeObservations: 2,
    observationCount: 2,
    baselineRelevantProcesses: [
      {
        pid: 400,
        parentPid: 40,
        name: "Cinematic Story Studio.exe",
        creationDate: "2026-08-02T22:00:00.0000000Z"
      }
    ],
    newRelevantIdentitiesObserved: false,
    observedNewRelevantProcesses: [],
    finalNewRelevantProcesses: [],
    failureCode: null,
    ownershipEstablished: false,
    ownedProcessExitClaimed: false,
    cleanupClaimed: false,
    explicitHarnessTerminationAttempted: false,
    playwrightLaunchCleanupClaimed: false
  };
}
