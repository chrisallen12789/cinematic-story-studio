import { spawnSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  buildPhase3b1PrivateReplayLauncher,
  buildPhase3b1PrivateReplayInspectionArgumentsPowerShell,
  observePhase3b1PrivateReplayDuplicate,
  phase3b1PrivateReplayMaximumPathLength,
  requirePhase3b1PrivateReplayPathBudget,
  resolvePhase3b1PrivateReplayStateDirectory,
  validatePhase3b1PrivateReplayContract,
  type Phase3b1PrivateReplayContract,
  type Phase3b1PrivateReplayDuplicateObservation,
  type Phase3b1PrivateReplayProcessIdentity
} from "./phase3b1-private-replay";

const hashA = "a".repeat(64);
const hashB = "b".repeat(64);
const hashC = "c".repeat(64);

describe("Phase 3B.1 private replay contract", () => {
  it("derives the opaque retained state beneath short local application data", () => {
    expect(
      resolvePhase3b1PrivateReplayStateDirectory(
        "C:\\Synthetic\\LocalData",
        "012345abcdef"
      )
    ).toBe(
      path.join(
        "C:\\Synthetic\\LocalData",
        "CSS-P3B1",
        "012345abcdef"
      )
    );
    expect(() =>
      resolvePhase3b1PrivateReplayStateDirectory("relative", "012345abcdef")
    ).toThrow("must be absolute");
    expect(() =>
      resolvePhase3b1PrivateReplayStateDirectory(
        "C:\\Synthetic\\LocalData",
        "../escape"
      )
    ).toThrow("identifier was invalid");
  });

  it("accepts a conservative retained path budget and rejects overflow", () => {
    const root = "C:\\Synthetic\\LocalData\\CSS-P3B1\\012345abcdef";
    expect(
      requirePhase3b1PrivateReplayPathBudget(root, [
        "LocalAppData\\Cinematic Story Studio\\projects\\project-id\\auditions\\scripts\\script.utf8",
        "LocalAppData\\Cinematic Story Studio\\models\\packages\\model\\version\\model.onnx"
      ])
    ).toBeLessThanOrEqual(phase3b1PrivateReplayMaximumPathLength);
    expect(() =>
      requirePhase3b1PrivateReplayPathBudget(root, [
        `LocalAppData\\${"x".repeat(phase3b1PrivateReplayMaximumPathLength)}`
      ])
    ).toThrow("exceeding the enforced");
    expect(() =>
      requirePhase3b1PrivateReplayPathBudget(root, ["..\\escape"])
    ).toThrow("escaped its state root");
  });

  it("rejects the reproduced deep sibling layout and accepts the short root", () => {
    const managedRelativePath = path.join(
      "LocalAppData",
      "Cinematic Story Studio",
      "projects",
      "11111111-2222-4333-8444-555555555555",
      "auditions",
      "scripts",
      "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.utf8"
    );
    const legacySibling = path.join(
      "C:\\Synthetic\\source\\repos\\cinematic-story-studio",
      "local-renders",
      "phase3b1-real-product-path",
      "run-2035-01-02T03-04-05.678Z-012345abcdef-desktop-state"
    );
    const bounded = path.join(
      "C:\\Synthetic\\LocalData",
      "CSS-P3B1",
      "012345abcdef"
    );

    expect(path.join(legacySibling, managedRelativePath).length).toBeGreaterThan(
      phase3b1PrivateReplayMaximumPathLength
    );
    expect(() =>
      requirePhase3b1PrivateReplayPathBudget(legacySibling, [
        managedRelativePath
      ])
    ).toThrow("exceeding the enforced");
    expect(
      requirePhase3b1PrivateReplayPathBudget(bounded, [managedRelativePath])
    ).toBeLessThanOrEqual(phase3b1PrivateReplayMaximumPathLength);
  });

  it("generates a fixed launcher with state, hash, cwd, and owned PID checks", () => {
    const contract = replayContract();
    expect(validatePhase3b1PrivateReplayContract(contract)).toBe(contract);
    const launcher = buildPhase3b1PrivateReplayLauncher(contract, hashC);
    expect(launcher).toContain("GetFolderPath");
    expect(launcher).toContain("CSS-P3B1");
    expect(launcher).toContain("Get-FileHash");
    expect(launcher).toContain("resources\\app.asar");
    expect(launcher).toContain("$contract.applicationArchive");
    expect(launcher).toContain("The listening package identity changed.");
    expect(launcher).toContain("The retained replay-state identity is invalid.");
    expect(launcher).toContain("The retained replay project binding is invalid.");
    expect(launcher).toContain("A retained replay clip binding is invalid.");
    expect(launcher).toContain("[Collections.Generic.Stack[string]]::new()");
    expect(launcher).toContain("The retained replay state contained a link.");
    expect(launcher).toContain("The retained replay state exceeded its enforced path budget.");
    expect(launcher).toContain("CSS_REPLAY_MAXIMUM_RETAINED_PATH_LENGTH=");
    expect(launcher).toContain("Get-CimInstance Win32_Process");
    expect(launcher).toContain("An exact replay process is already running");
    expect(launcher).toContain("CSS_REPLAY_PREEXISTING_RELEVANT_PIDS=none");
    expect(launcher).toContain("CSS_PHASE3B1_PRIVATE_REPLAY_E2E");
    expect(launcher).toContain('$e2eRunner -cne "1"');
    expect(launcher).toContain(
      "-not [string]::IsNullOrWhiteSpace($e2eRunner)"
    );
    expect(launcher).toContain("--remote-debugging-address=127.0.0.1");
    expect(launcher).toContain("$parsedDebugPort -lt 49152");
    expect(launcher).toContain('"ELECTRON_RUN_AS_NODE"');
    expect(launcher).toContain('"NODE_OPTIONS"');
    expect(launcher).toContain('"CSS_DESKTOP_DEV_URL"');
    expect(launcher).toContain('"CSS_PACKAGED_E2E_SHUTDOWN_EVIDENCE_PATH"');
    expect(launcher).toContain('"CSS_PHASE3B_RUNTIME_SHUTDOWN_EVIDENCE"');
    expect(launcher).toContain('"CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER"');
    expect(launcher).toContain("[EnvironmentVariableTarget]::Process");
    expect(launcher).toContain("Start-Process -FilePath $executable");
    expect(launcher).toContain("-WorkingDirectory");
    expect(launcher).toContain("-PassThru");
    expect(launcher).toContain("CSS_REPLAY_LAUNCHER_PID=");
    expect(launcher).toContain("$application.WaitForExit()");
    expect(launcher).not.toContain("taskkill");
    expect(launcher).not.toContain("Stop-Process");
    expect(launcher).not.toContain("Remove-Item");
    expect(launcher).not.toContain("C:\\Synthetic");
  });

  it("rejects drifted executable identities and path evidence", () => {
    expect(() => validatePhase3b1PrivateReplayContract(null)).toThrow(
      "contract was invalid"
    );
    expect(() =>
      validatePhase3b1PrivateReplayContract({
        ...replayContract(),
        executable: {
          ...replayContract().executable,
          relativePath: "elsewhere.exe"
        }
      })
    ).toThrow("fingerprint was invalid");
    expect(() =>
      validatePhase3b1PrivateReplayContract({
        ...replayContract(),
        applicationArchive: {
          ...replayContract().applicationArchive,
          sha256: "not-a-hash"
        }
      })
    ).toThrow("fingerprint was invalid");
    expect(() =>
      buildPhase3b1PrivateReplayLauncher(replayContract(), "not-a-hash")
    ).toThrow("contract hash was invalid");
  });

  it.skipIf(process.platform !== "win32")(
    "emits syntactically valid Windows PowerShell",
    () => {
      const launcher = buildPhase3b1PrivateReplayLauncher(
        replayContract(),
        hashC
      );
      const result = spawnSync(
        "powershell.exe",
        [
          "-NoLogo",
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseInput($env:CSS_REPLAY_SOURCE,[ref]$tokens,[ref]$errors) > $null; if($errors.Count -ne 0){$errors | ForEach-Object {[Console]::Error.WriteLine($_.Message)}; exit 1}; [Console]::Out.Write('VALID')"
        ],
        {
          encoding: "utf8",
          env: { ...process.env, CSS_REPLAY_SOURCE: launcher },
          shell: false,
          timeout: 15_000,
          windowsHide: true
        }
      );
      expect(result.error).toBeUndefined();
      expect(result.status, result.stderr).toBe(0);
      expect(result.stdout).toBe("VALID");
    },
    20_000
  );

  it.skipIf(process.platform !== "win32")(
    "rejects stale or partial replay inspection environment combinations",
    () => {
      const names = [
        "CSS_PHASE3B1_PRIVATE_REPLAY_E2E",
        "CSS_PHASE3B1_REPLAY_E2E_REMOTE_DEBUGGING_PORT",
        "CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER"
      ] as const;
      const cases = [
        [{}, 0, ""],
        [{ CSS_PHASE3B1_PRIVATE_REPLAY_E2E: "1" }, 1, ""],
        [{ CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER: "1" }, 1, ""],
        [
          {
            CSS_PHASE3B1_PRIVATE_REPLAY_E2E: "1",
            CSS_PHASE3B1_REPLAY_E2E_REMOTE_DEBUGGING_PORT: "60000"
          },
          1,
          ""
        ],
        [
          {
            CSS_PHASE3B1_PRIVATE_REPLAY_E2E: "1",
            CSS_PHASE3B1_REPLAY_E2E_REMOTE_DEBUGGING_PORT: "49151",
            CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER: "1"
          },
          1,
          ""
        ],
        [
          {
            CSS_PHASE3B1_PRIVATE_REPLAY_E2E: "1",
            CSS_PHASE3B1_REPLAY_E2E_REMOTE_DEBUGGING_PORT: "60000",
            CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER: "1"
          },
          0,
          "--remote-debugging-address=127.0.0.1|--remote-debugging-port=60000"
        ]
      ] as const;
      for (const [overrides, expectedStatus, expectedOutput] of cases) {
        const environment = { ...process.env };
        for (const name of names) delete environment[name];
        Object.assign(environment, overrides);
        const result = spawnSync(
          "powershell.exe",
          [
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            `${buildPhase3b1PrivateReplayInspectionArgumentsPowerShell()}\n[Console]::Out.Write(($applicationArguments -join '|'))`
          ],
          {
            encoding: "utf8",
            env: environment,
            shell: false,
            timeout: 15_000,
            windowsHide: true
          }
        );
        expect(result.error).toBeUndefined();
        expect(result.status === 0 ? 0 : 1, result.stderr).toBe(expectedStatus);
        if (expectedStatus === 0) expect(result.stdout).toBe(expectedOutput);
      }
    },
    20_000
  );
});

describe("Phase 3B.1 private replay duplicate observation", () => {
  it("retains an exact spawn-bound identity through two absent inventories", () => {
    let observation: Phase3b1PrivateReplayDuplicateObservation | null =
      observeDuplicate(null, [duplicateBaseline, exactDuplicate], 100);
    expect(observation).toMatchObject({
      pathStatus: "exact_path_confirmed",
      executablePath: duplicateExecutablePath,
      absenceObservations: 0
    });

    observation = observeDuplicate(observation, [duplicateBaseline], 200);
    expect(observation?.absenceObservations).toBe(1);
    observation = observeDuplicate(observation, [duplicateBaseline], 300);
    expect(observation).toMatchObject({
      pathStatus: "exact_path_confirmed",
      executablePath: duplicateExecutablePath,
      absenceObservations: 2
    });
  });

  it("accepts a pathless spawn-bound identity only after two absences", () => {
    let observation: Phase3b1PrivateReplayDuplicateObservation | null =
      observeDuplicate(null, [duplicateBaseline, pathlessDuplicate], 100);
    expect(observation).toMatchObject({
      pathStatus: "pending_exact_path",
      executablePath: null,
      absenceObservations: 0
    });

    observation = observeDuplicate(observation, [duplicateBaseline], 200);
    expect(observation).toMatchObject({
      pathStatus: "pending_exact_path",
      absenceObservations: 1
    });
    observation = observeDuplicate(observation, [duplicateBaseline], 300);
    expect(observation).toMatchObject({
      pathStatus: "unavailable_before_exit",
      executablePath: null,
      absenceObservations: 2
    });
  });

  it("promotes the same pathless identity when its exact path becomes available", () => {
    const pending = observeDuplicate(
      null,
      [duplicateBaseline, pathlessDuplicate],
      100
    );
    expect(
      observeDuplicate(
        pending,
        [duplicateBaseline, exactDuplicate],
        200
      )
    ).toMatchObject({
      pathStatus: "exact_path_confirmed",
      executablePath: duplicateExecutablePath,
      absenceObservations: 0
    });
  });

  it("retains exact-path authority when CIM loses the path during exit", () => {
    const exact = observeDuplicate(
      null,
      [duplicateBaseline, exactDuplicate],
      100
    );
    expect(
      observeDuplicate(
        exact,
        [duplicateBaseline, pathlessDuplicate],
        200
      )
    ).toMatchObject({
      pathStatus: "exact_path_confirmed",
      executablePath: duplicateExecutablePath,
      absenceObservations: 0
    });
  });

  it("allows one missed inventory before the same identity exposes its exact path", () => {
    const pending = observeDuplicate(
      null,
      [duplicateBaseline, pathlessDuplicate],
      100
    );
    const absent = observeDuplicate(pending, [duplicateBaseline], 200);
    expect(
      observeDuplicate(
        absent,
        [duplicateBaseline, exactDuplicate],
        300
      )
    ).toMatchObject({
      pathStatus: "exact_path_confirmed",
      executablePath: duplicateExecutablePath,
      absenceObservations: 0
    });
  });

  it("fails closed if a confirmed-exited pathless identity reappears", () => {
    const pending = observeDuplicate(
      null,
      [duplicateBaseline, pathlessDuplicate],
      100
    );
    const firstAbsence = observeDuplicate(
      pending,
      [duplicateBaseline],
      200
    );
    const confirmedExited = observeDuplicate(
      firstAbsence,
      [duplicateBaseline],
      300
    );
    expect(() =>
      observeDuplicate(
        confirmedExited,
        [duplicateBaseline, pathlessDuplicate],
        400
      )
    ).toThrowError(
      expect.objectContaining({
        reason: "EXPECTED_PID_REAPPEARED_AFTER_ABSENCE"
      })
    );
  });

  it("bounds path confirmation to five seconds", () => {
    const pending = observeDuplicate(
      null,
      [duplicateBaseline, pathlessDuplicate],
      100
    );
    expect(() =>
      observeDuplicate(
        pending,
        [duplicateBaseline, pathlessDuplicate],
        5_100
      )
    ).toThrowError(
      expect.objectContaining({
        reason: "EXPECTED_PID_PATH_CONFIRMATION_TIMEOUT"
      })
    );
    expect(() =>
      observeDuplicate(
        pending,
        [duplicateBaseline, exactDuplicate],
        5_100
      )
    ).toThrowError(
      expect.objectContaining({
        reason: "EXPECTED_PID_PATH_CONFIRMATION_TIMEOUT"
      })
    );
  });

  it("does not accept late absence evidence beyond the path deadline", () => {
    const pending = observeDuplicate(
      null,
      [duplicateBaseline, pathlessDuplicate],
      100
    );
    const firstAbsence = observeDuplicate(
      pending,
      [duplicateBaseline],
      4_900
    );
    expect(() =>
      observeDuplicate(firstAbsence, [duplicateBaseline], 5_100)
    ).toThrowError(
      expect.objectContaining({
        reason: "EXPECTED_PID_PATH_CONFIRMATION_TIMEOUT"
      })
    );
  });

  it("rejects wrong path, parent, name, creation, and launch chronology", () => {
    const cases: readonly [
      Phase3b1PrivateReplayProcessIdentity,
      string
    ][] = [
      [
        { ...exactDuplicate, executablePath: "C:\\Other\\Cinematic Story Studio.exe" },
        "EXPECTED_PID_PATH_MISMATCH"
      ],
      [
        { ...exactDuplicate, parentPid: duplicateParentPid + 1 },
        "EXPECTED_PID_PARENT_MISMATCH"
      ],
      [
        { ...exactDuplicate, name: "cinematic-story-service.exe" },
        "EXPECTED_PID_NAME_MISMATCH"
      ],
      [
        { ...exactDuplicate, creationDate: "not-a-date" },
        "EXPECTED_PID_CREATION_INVALID"
      ],
      [
        { ...exactDuplicate, creationDate: "2026-08-06T14:59:58.000Z" },
        "EXPECTED_PID_PREDATES_LAUNCH"
      ]
    ];

    for (const [candidate, reason] of cases) {
      expect(() =>
        observeDuplicate(null, [duplicateBaseline, candidate], 100)
      ).toThrowError(expect.objectContaining({ reason }));
    }
  });

  it("rejects PID reuse or changed creation identity", () => {
    const exact = observeDuplicate(
      null,
      [duplicateBaseline, exactDuplicate],
      100
    );
    expect(() =>
      observeDuplicate(
        exact,
        [
          duplicateBaseline,
          {
            ...exactDuplicate,
            creationDate: "2026-08-06T15:00:01.000Z"
          }
        ],
        200
      )
    ).toThrowError(
      expect.objectContaining({ reason: "EXPECTED_PID_CREATION_CHANGED" })
    );
  });

  it("rejects every extra application or service identity", () => {
    const extraApp = {
      ...exactDuplicate,
      pid: duplicatePid + 1
    };
    const extraService = {
      ...exactDuplicate,
      pid: duplicatePid + 2,
      name: "cinematic-story-service.exe",
      executablePath:
        "C:\\Program Files\\Cinematic Story Studio\\resources\\service\\cinematic-story-service.exe"
    };
    expect(() =>
      observeDuplicate(
        null,
        [duplicateBaseline, exactDuplicate, extraApp],
        100
      )
    ).toThrowError(
      expect.objectContaining({ reason: "UNEXPECTED_APPLICATION_PROCESS" })
    );
    expect(() =>
      observeDuplicate(
        null,
        [duplicateBaseline, exactDuplicate, extraService],
        100
      )
    ).toThrowError(
      expect.objectContaining({ reason: "UNEXPECTED_SERVICE_PROCESS" })
    );
  });

  it("rejects multiple records for the spawn-bound PID", () => {
    expect(() =>
      observeDuplicate(
        null,
        [
          duplicateBaseline,
          exactDuplicate,
          { ...pathlessDuplicate, creationDate: "2026-08-06T15:00:01.000Z" }
        ],
        100
      )
    ).toThrowError(
      expect.objectContaining({ reason: "MULTIPLE_EXPECTED_PID_IDENTITIES" })
    );
  });

  it("allows baseline reparenting and path loss but rejects path drift", () => {
    expect(
      observeDuplicate(
        null,
        [{ ...duplicateBaseline, parentPid: 0, executablePath: null }],
        100
      )
    ).toBeNull();
    expect(() =>
      observeDuplicate(
        null,
        [{ ...duplicateBaseline, executablePath: "C:\\Other\\service.exe" }],
        100
      )
    ).toThrowError(
      expect.objectContaining({ reason: "UNEXPECTED_SERVICE_PROCESS" })
    );
  });

  it("keeps a never-observed direct child distinguishable from transient identity proof", () => {
    expect(observeDuplicate(null, [duplicateBaseline], 100)).toBeNull();
    expect(observeDuplicate(null, [duplicateBaseline], 200)).toBeNull();
  });

  it("rejects impossible prior observation authority", () => {
    const invalid = {
      ...pathlessDuplicate,
      pathStatus: "exact_path_confirmed",
      firstObservedAtMs: 100,
      absenceObservations: 0
    } as unknown as Phase3b1PrivateReplayDuplicateObservation;
    expect(() =>
      observeDuplicate(invalid, [duplicateBaseline], 200)
    ).toThrow("observation policy was invalid");
  });
});

const duplicatePid = 8_200;
const duplicateParentPid = 7_100;
const duplicateExecutablePath =
  "C:\\Program Files\\Cinematic Story Studio\\Cinematic Story Studio.exe";
const duplicateStartedAtUnixMs = Date.parse("2026-08-06T15:00:00.000Z");
const duplicateBaseline: Phase3b1PrivateReplayProcessIdentity = {
  pid: 8_100,
  parentPid: 8_000,
  name: "cinematic-story-service.exe",
  executablePath:
    "C:\\Program Files\\Cinematic Story Studio\\resources\\service\\cinematic-story-service.exe",
  creationDate: "2026-08-06T14:55:00.000Z"
};
const exactDuplicate: Phase3b1PrivateReplayProcessIdentity = {
  pid: duplicatePid,
  parentPid: duplicateParentPid,
  name: "Cinematic Story Studio.exe",
  executablePath: duplicateExecutablePath,
  creationDate: "2026-08-06T15:00:00.500Z"
};
const pathlessDuplicate: Phase3b1PrivateReplayProcessIdentity = {
  ...exactDuplicate,
  executablePath: null
};

function observeDuplicate(
  previous: Phase3b1PrivateReplayDuplicateObservation | null,
  current: readonly Phase3b1PrivateReplayProcessIdentity[],
  observedAtMs: number
): Phase3b1PrivateReplayDuplicateObservation | null {
  return observePhase3b1PrivateReplayDuplicate({
    current,
    baseline: [duplicateBaseline],
    previous,
    expectedPid: duplicatePid,
    expectedParentPid: duplicateParentPid,
    expectedName: "Cinematic Story Studio.exe",
    expectedExecutablePath: duplicateExecutablePath,
    startedAtUnixMs: duplicateStartedAtUnixMs,
    observedAtMs,
    serviceExecutableName: "cinematic-story-service.exe"
  });
}

function replayContract(): Phase3b1PrivateReplayContract {
  return {
    schemaVersion: 1,
    evidenceClassification: "private_local_replay_contract",
    stateStorage: "local_application_data",
    stateDirectoryName: "CSS-P3B1",
    stateId: "012345abcdef",
    packageDirectoryName: "run-2035-01-02T03-04-05.678Z-012345abcdef",
    listeningIndexSha256: hashA,
    stateSentinelSha256: hashB,
    packagedVersion: "0.1.0",
    executable: {
      relativePath:
        "apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
      byteSize: 225_613_824,
      sha256: hashA
    },
    applicationArchive: {
      relativePath:
        "apps/desktop/release/0.1.0/win-unpacked/resources/app.asar",
      byteSize: 9_123_456,
      sha256: hashC
    },
    service: {
      relativePath:
        "apps/desktop/release/0.1.0/win-unpacked/resources/service/cinematic-story-service.exe",
      byteSize: 51_930_414,
      sha256: hashB
    },
    maximumRetainedPathLength: 215,
    enforcedMaximumPathLength: 240
  };
}
