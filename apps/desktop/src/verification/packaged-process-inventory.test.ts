import { describe, expect, it } from "vitest";

import {
  ProcessInventoryError,
  adoptVerifiedProcessTree,
  adoptVerifiedProcessTreeWithPathConfirmation,
  appExecutableName,
  bindProviderWorkerProcessTree,
  createPackagedProcessInventory,
  defaultProcessInventoryPolicy,
  parseProcessInventory,
  remainingOwnedProcesses,
  runBoundedProcess,
  sameProcessIdentity,
  serviceExecutableName,
  type OwnedProcess,
  type ProcessCommandRequest,
  type ProcessIdentity
} from "./packaged-process-inventory";

const packaged = Object.freeze({
  executablePath:
    "C:\\Program Files\\Cinematic Story Studio\\Cinematic Story Studio.exe",
  serviceExecutablePath:
    "C:\\Program Files\\Cinematic Story Studio\\resources\\service\\cinematic-story-service.exe"
});

describe("packaged process inventory", () => {
  it("uses one fixed targeted CIM query and succeeds on the first attempt", async () => {
    const requests: ProcessCommandRequest[] = [];
    const inventory = createPackagedProcessInventory({
      platform: "win32",
      run: async (request) => {
        requests.push(request);
        return "[]";
      }
    });

    await expect(inventory.query()).resolves.toEqual([]);
    expect(requests).toHaveLength(1);
    expect(requests[0]?.command).toBe("powershell.exe");
    expect(requests[0]?.timeoutMs).toBe(15_000);
    expect(requests[0]?.maximumOutputBytes).toBe(1024 * 1024);
    const script = requests[0]?.arguments.at(-1) ?? "";
    expect(script.match(/Get-CimInstance/gu)).toHaveLength(1);
    expect(script).toContain(
      "Name = 'Cinematic Story Studio.exe' OR Name = 'cinematic-story-service.exe'"
    );
    expect(script).toContain(
      "-Property ProcessId,ParentProcessId,Name,ExecutablePath,CreationDate"
    );
  });

  it("retries one timed-out attempt and succeeds on the second", async () => {
    const clock = createVirtualClock();
    const timeouts: number[] = [];
    let attempts = 0;
    const inventory = createPackagedProcessInventory({
      platform: "win32",
      now: clock.now,
      delay: clock.delay,
      run: async (request) => {
        attempts += 1;
        timeouts.push(request.timeoutMs);
        if (attempts === 1) {
          clock.advance(request.timeoutMs);
          throw new ProcessInventoryError(
            "PROCESS_INVENTORY_TIMEOUT",
            true
          );
        }
        return "[]";
      }
    });

    await expect(inventory.query()).resolves.toEqual([]);
    expect(attempts).toBe(2);
    expect(timeouts).toEqual([15_000, 15_000]);
    expect(clock.delays).toEqual([250]);
    expect(clock.now()).toBe(15_250);
  });

  it("bounds three timed-out attempts by the total deadline", async () => {
    const clock = createVirtualClock();
    const timeouts: number[] = [];
    const inventory = createPackagedProcessInventory({
      platform: "win32",
      now: clock.now,
      delay: clock.delay,
      run: async (request) => {
        timeouts.push(request.timeoutMs);
        clock.advance(request.timeoutMs);
        throw new ProcessInventoryError(
          "PROCESS_INVENTORY_TIMEOUT",
          true
        );
      }
    });

    await expect(inventory.query()).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_TIMEOUT"
    });
    expect(timeouts).toEqual([15_000, 15_000, 13_000]);
    expect(clock.delays).toEqual([250, 750]);
    expect(clock.now()).toBe(
      defaultProcessInventoryPolicy.totalDeadlineMs
    );
  });

  it("retries nonzero provider exits without exceeding three attempts", async () => {
    const clock = createVirtualClock();
    let attempts = 0;
    const inventory = createPackagedProcessInventory({
      platform: "win32",
      now: clock.now,
      delay: clock.delay,
      run: async () => {
        attempts += 1;
        throw new ProcessInventoryError(
          "PROCESS_INVENTORY_COMMAND_FAILED",
          true
        );
      }
    });

    await expect(inventory.query()).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_COMMAND_FAILED"
    });
    expect(attempts).toBe(3);
    expect(clock.delays).toEqual([250, 750]);
  });

  it("clips an attempt to the caller's remaining deadline", async () => {
    const clock = createVirtualClock(1_000);
    const timeouts: number[] = [];
    const inventory = createPackagedProcessInventory({
      platform: "win32",
      now: clock.now,
      delay: clock.delay,
      run: async (request) => {
        timeouts.push(request.timeoutMs);
        return "[]";
      }
    });

    await inventory.query({ deadlineAt: 6_000 });
    expect(timeouts).toEqual([5_000]);
  });

  it("rejects malformed JSON without retrying", async () => {
    let attempts = 0;
    const inventory = createPackagedProcessInventory({
      platform: "win32",
      run: async () => {
        attempts += 1;
        return "{";
      }
    });

    await expect(inventory.query()).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_MALFORMED_OUTPUT"
    });
    expect(attempts).toBe(1);
  });

  it("rejects blank provider output instead of treating it as empty inventory", () => {
    expect(() => parseProcessInventory(" \r\n")).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_MALFORMED_OUTPUT"
      })
    );
  });

  it("rejects oversized output without retrying", async () => {
    let attempts = 0;
    const inventory = createPackagedProcessInventory({
      platform: "win32",
      run: async () => {
        attempts += 1;
        return "x".repeat(
          defaultProcessInventoryPolicy.maximumOutputBytes + 1
        );
      }
    });

    await expect(inventory.query()).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_OUTPUT_LIMIT"
    });
    expect(attempts).toBe(1);
  });

  it("waits for a timed-out helper to exit before rejecting", async () => {
    await expect(
      runBoundedProcess({
        command: process.execPath,
        arguments: [
          "-e",
          "setInterval(() => undefined, 1000)"
        ],
        timeoutMs: 800,
        maximumOutputBytes: 1024
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_TIMEOUT"
    });
  });

  it("drains successful helper stdout before resolving", async () => {
    const expected = "x".repeat(256 * 1024);

    await expect(
      runBoundedProcess({
        command: process.execPath,
        arguments: [
          "-e",
          `process.stdout.write("x".repeat(${expected.length}))`
        ],
        timeoutMs: 10_000,
        maximumOutputBytes: 512 * 1024
      })
    ).resolves.toBe(expected);
  });

  it("rejects duplicate or ambiguous process identities", () => {
    const first = processIdentity({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath
    });
    const duplicate = {
      ...first,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    };

    expect(() =>
      parseProcessInventory(JSON.stringify([first, duplicate]))
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("rejects PID reuse with a different creation timestamp", () => {
    const original = processIdentity({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath
    });
    const reused = {
      ...original,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    };

    expect(sameProcessIdentity(original, reused)).toBe(false);
  });

  it("rejects the same PID with a different executable path", () => {
    const original = processIdentity({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath
    });
    const unrelated = {
      ...original,
      executablePath: "C:\\Unrelated\\Cinematic Story Studio.exe"
    };

    expect(sameProcessIdentity(original, unrelated)).toBe(false);
  });

  it("rejects the same PID when its parent identity changes", () => {
    const original = processIdentity({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath
    });
    const reparented = {
      ...original,
      parentPid: 6100
    };

    expect(sameProcessIdentity(original, reparented)).toBe(false);
  });

  it("excludes a preexisting matching-name process from ownership", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const service = processIdentity({
      pid: 4101,
      parentPid: 4100,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    const preexisting = processIdentity({
      pid: 3900,
      parentPid: 1,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:14:00.0000000Z"
    });

    const adopted = adoptVerifiedProcessTree({
      current: [preexisting, root, service],
      baseline: [preexisting],
      owned: [root],
      rootPid: root.pid,
      packaged
    });

    expect(adopted.map((item) => item.pid)).toEqual([4100, 4101]);
  });

  it("fails closed instead of retaining a reused owned PID", () => {
    const expected = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const reused = {
      ...expected,
      executablePath: "C:\\Unrelated\\Cinematic Story Studio.exe"
    };

    expect(() =>
      remainingOwnedProcesses([reused], [expected])
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("retains an owned identity through a partial exit observation", () => {
    const expected = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partialObservation = {
      ...expected,
      executablePath: null
    };

    expect(
      adoptVerifiedProcessTree({
        current: [partialObservation],
        baseline: [],
        owned: [expected],
        rootPid: expected.pid,
        packaged
      })
    ).toEqual([expected]);
    expect(
      remainingOwnedProcesses([partialObservation], [expected])
    ).toEqual([expected]);
  });

  it("still rejects a different observed path for an owned PID", () => {
    const expected = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const contradictoryObservation = {
      ...expected,
      executablePath: "C:\\Unrelated\\Cinematic Story Studio.exe"
    };

    expect(() =>
      remainingOwnedProcesses(
        [contradictoryObservation],
        [expected]
      )
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("retains a previously adopted process after shutdown re-parenting", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:15:20.0000000Z",
      kind: "app"
    });
    const service = ownedProcess({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "service"
    });
    const reparentedService = {
      ...service,
      parentPid: 6100
    };

    const adopted = adoptVerifiedProcessTree({
      current: [reparentedService],
      baseline: [],
      owned: [root, service],
      rootPid: root.pid,
      packaged
    });

    expect(adopted).toEqual([root, service]);
    expect(
      remainingOwnedProcesses([reparentedService], adopted)
    ).toEqual([service]);
  });

  it("accepts only the verified application and embedded-service tree", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const child = processIdentity({
      pid: 4101,
      parentPid: 4100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    const service = processIdentity({
      pid: 4102,
      parentPid: 4101,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });

    const adopted = adoptVerifiedProcessTree({
      current: [root, child, service],
      baseline: [],
      owned: [root],
      rootPid: root.pid,
      packaged
    });

    expect(adopted).toEqual([
      root,
      { ...child, kind: "app" },
      { ...service, kind: "service" }
    ]);
  });

  it("rejects an application-image descendant below the service boundary", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const service = ownedProcess({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "service"
    });
    const invalidAppChild = processIdentity({
      pid: 4102,
      parentPid: service.pid,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [root, service, invalidAppChild],
        baseline: [],
        owned: [root, service],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("adopts a transient service-image child only from an owned service", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const service = ownedProcess({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "service"
    });
    const parserChild = processIdentity({
      pid: 4102,
      parentPid: service.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });

    expect(
      adoptVerifiedProcessTree({
        current: [root, service, parserChild],
        baseline: [],
        owned: [root, service],
        rootPid: root.pid,
        packaged
      })
    ).toEqual([
      root,
      service,
      { ...parserChild, kind: "service" }
    ]);
  });

  it("binds only an already-owned service descendant to the reported provider worker", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const service = ownedProcess({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "service"
    });
    const workerLauncher = ownedProcess({
      pid: 4102,
      parentPid: service.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z",
      kind: "service"
    });
    const worker = ownedProcess({
      pid: 4103,
      parentPid: workerLauncher.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:23.0000000Z",
      kind: "service"
    });

    expect(
      bindProviderWorkerProcessTree({
        owned: [root, service, workerLauncher, worker],
        rootPid: root.pid,
        workerPid: worker.pid,
        reportedParentPid: service.pid
      })
    ).toEqual([
      root,
      service,
      { ...workerLauncher, kind: "provider_worker" },
      { ...worker, kind: "provider_worker" }
    ]);
  });

  it("refuses to manufacture provider-worker ownership from an unrelated PID", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const service = ownedProcess({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "service"
    });
    const unrelated = ownedProcess({
      pid: 4102,
      parentPid: 9999,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z",
      kind: "service"
    });

    expect(() =>
      bindProviderWorkerProcessTree({
        owned: [root, service, unrelated],
        rootPid: root.pid,
        workerPid: unrelated.pid,
        reportedParentPid: service.pid
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("rejects a later service-image child spawned outside the service tree", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const service = ownedProcess({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "service"
    });
    const unrelatedService = processIdentity({
      pid: 4102,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [root, service, unrelatedService],
        baseline: [],
        owned: [root, service],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("fails closed on an ancestry-linked child with an unavailable path", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const candidate = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [root, candidate],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        retryable: true,
        ambiguityReason: "NEW_CHILD_PATH_UNAVAILABLE",
        candidate: {
          pid: candidate.pid,
          parentPid: candidate.parentPid,
          name: candidate.name,
          creationDate: candidate.creationDate
        }
      })
    );
  });

  it("adopts a new child only after the same identity exposes the exact path", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    const exact = {
      ...partial,
      executablePath: packaged.serviceExecutablePath
    };
    const delays: number[] = [];
    const deadlines: number[] = [];
    let queries = 0;

    const adopted = await adoptVerifiedProcessTreeWithPathConfirmation({
      current: [root, partial],
      baseline: [],
      owned: [root],
      rootPid: root.pid,
      packaged,
      deadlineAt: 10_000,
      now: () => 0,
      delay: async (milliseconds) => {
        delays.push(milliseconds);
      },
      queryCurrent: async (deadlineAt) => {
        deadlines.push(deadlineAt);
        queries += 1;
        return [root, exact];
      }
    });

    expect(adopted).toEqual({
      ownedProcesses: [root, { ...exact, kind: "service" }],
      confirmedExitedTransientProcesses: [],
      observedProcesses: [root, exact]
    });
    expect(delays).toEqual([100]);
    expect(deadlines).toEqual([5_000]);
    expect(queries).toBe(1);
  });

  it("records an Electron child that exits before CIM exposes its path", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: appExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    let queries = 0;

    const adopted = await adoptVerifiedProcessTreeWithPathConfirmation({
      current: [root, partial],
      baseline: [],
      owned: [root],
      rootPid: root.pid,
      packaged,
      deadlineAt: 10_000,
      now: () => 0,
      delay: async () => undefined,
      queryCurrent: async () => {
        queries += 1;
        return [root];
      }
    });

    expect(adopted).toEqual({
      ownedProcesses: [root],
      confirmedExitedTransientProcesses: [
        {
          ...partial,
          executablePath: null,
          kind: "app",
          pathStatus: "unavailable_before_exit",
          verifiedParentCreationDate: root.creationDate,
          absenceObservations: 2
        }
      ],
      observedProcesses: [root]
    });
    expect(queries).toBe(2);
  });

  it("fails if a confirmed-exited transient Electron PID reappears", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: appExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    const first = await adoptVerifiedProcessTreeWithPathConfirmation({
      current: [root, partial],
      baseline: [],
      owned: [root],
      rootPid: root.pid,
      packaged,
      deadlineAt: 10_000,
      now: () => 0,
      delay: async () => undefined,
      queryCurrent: async () => [root]
    });

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [
          root,
          { ...partial, executablePath: packaged.executablePath }
        ],
        baseline: [],
        owned: first.ownedProcesses,
        confirmedExitedTransientProcesses:
          first.confirmedExitedTransientProcesses,
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => []
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST"
    });
  });

  it("rejects duplicate, orphaned, or provenance-drifted transient ledgers", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const transient = {
      pid: 4101,
      parentPid: root.pid,
      name: appExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "app" as const,
      pathStatus: "unavailable_before_exit" as const,
      verifiedParentCreationDate: root.creationDate,
      absenceObservations: 2 as const
    };
    const invalidLedgers = [
      [transient, transient],
      [{ ...transient, parentPid: 9999 }],
      [
        {
          ...transient,
          verifiedParentCreationDate: "2026-07-29T18:15:19.0000000Z"
        }
      ]
    ];

    for (const confirmedExitedTransientProcesses of invalidLedgers) {
      await expect(
        adoptVerifiedProcessTreeWithPathConfirmation({
          current: [root],
          baseline: [],
          owned: [root],
          confirmedExitedTransientProcesses,
          rootPid: root.pid,
          packaged,
          deadlineAt: 10_000,
          now: () => 0,
          delay: async () => undefined,
          queryCurrent: async () => []
        })
      ).rejects.toMatchObject({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        retryable: false,
        ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST"
      });
    }
  });

  it("fails if the exact Electron parent disappears with its partial child", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: appExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, partial],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => []
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST"
    });
  });

  it("fails if two unproven path identities appear in one inventory", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const first = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: appExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    const second = processIdentity({
      pid: 4102,
      parentPid: root.pid,
      name: appExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });
    let queries = 0;

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, first, second],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => {
          queries += 1;
          return [root];
        }
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST"
    });
    expect(queries).toBe(0);
  });

  it.each([
    {
      label: "Electron",
      name: appExecutableName,
      executablePath: packaged.executablePath
    },
    {
      label: "service",
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath
    }
  ])(
    "fails if the initial pending identity hides a later exact $label identity",
    async ({ name, executablePath }) => {
      const root = ownedProcess({
        pid: 4100,
        parentPid: 5100,
        name: appExecutableName,
        executablePath: packaged.executablePath,
        kind: "app"
      });
      const pending = processIdentity({
        pid: 4101,
        parentPid: root.pid,
        name: appExecutableName,
        executablePath: null,
        creationDate: "2026-07-29T18:15:21.0000000Z"
      });
      const hidden = processIdentity({
        pid: 4102,
        parentPid: root.pid,
        name,
        executablePath,
        creationDate: "2026-07-29T18:15:22.0000000Z"
      });
      let queries = 0;

      await expect(
        adoptVerifiedProcessTreeWithPathConfirmation({
          current: [root, pending, hidden],
          baseline: [],
          owned: [root],
          rootPid: root.pid,
          packaged,
          deadlineAt: 10_000,
          now: () => 0,
          delay: async () => undefined,
          queryCurrent: async () => {
            queries += 1;
            return [root];
          }
        })
      ).rejects.toMatchObject({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        retryable: false,
        ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST",
        candidate: {
          pid: pending.pid,
          parentPid: pending.parentPid,
          name: pending.name,
          creationDate: pending.creationDate
        }
      });
      expect(queries).toBe(0);
    }
  );

  it.each([
    {
      label: "Electron",
      name: appExecutableName,
      executablePath: packaged.executablePath
    },
    {
      label: "service",
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath
    }
  ])(
    "fails if another $label identity appears while a transient exit is being confirmed",
    async ({ name, executablePath }) => {
      const root = ownedProcess({
        pid: 4100,
        parentPid: 5100,
        name: appExecutableName,
        executablePath: packaged.executablePath,
        kind: "app"
      });
      const pending = processIdentity({
        pid: 4101,
        parentPid: root.pid,
        name: appExecutableName,
        executablePath: null,
        creationDate: "2026-07-29T18:15:21.0000000Z"
      });
      const intervening = processIdentity({
        pid: 4102,
        parentPid: root.pid,
        name,
        executablePath,
        creationDate: "2026-07-29T18:15:22.0000000Z"
      });
      let queries = 0;

      await expect(
        adoptVerifiedProcessTreeWithPathConfirmation({
          current: [root, pending],
          baseline: [],
          owned: [root],
          rootPid: root.pid,
          packaged,
          deadlineAt: 10_000,
          now: () => 0,
          delay: async () => undefined,
          queryCurrent: async () => {
            queries += 1;
            return [root, intervening];
          }
        })
      ).rejects.toMatchObject({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
        retryable: false,
        ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST",
        candidate: {
          pid: intervening.pid,
          parentPid: intervening.parentPid,
          name: intervening.name,
          creationDate: intervening.creationDate
        }
      });
      expect(queries).toBe(1);
    }
  );

  it("fails if a partial service child disappears before its path is proven", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, partial],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => [root]
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST"
    });
  });

  it("fails if a partial new child changes identity before confirmation", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    const changed = {
      ...partial,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    };

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, partial],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => [root, changed]
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_LOST"
    });
  });

  it("fails immediately when a partial child resolves to the wrong path", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, partial],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => [
          root,
          {
            ...partial,
            executablePath: "C:\\unrelated\\cinematic-story-service.exe"
          }
        ]
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_MISMATCH"
    });
  });

  it("confirms multiple partial descendants across separate bounded adoptions", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partialParent = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    const partialChild = processIdentity({
      pid: 4102,
      parentPid: partialParent.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });
    const exactParent = {
      ...partialParent,
      executablePath: packaged.serviceExecutablePath
    };
    const exactChild = {
      ...partialChild,
      executablePath: packaged.serviceExecutablePath
    };
    let query = 0;

    const parentAdoption = await adoptVerifiedProcessTreeWithPathConfirmation({
      current: [root, partialParent],
      baseline: [],
      owned: [root],
      rootPid: root.pid,
      packaged,
      deadlineAt: 10_000,
      now: () => 0,
      delay: async () => undefined,
      queryCurrent: async () => {
        query += 1;
        return [root, exactParent];
      }
    });
    const adopted = await adoptVerifiedProcessTreeWithPathConfirmation({
      current: [root, exactParent, partialChild],
      baseline: [],
      owned: parentAdoption.ownedProcesses,
      confirmedExitedTransientProcesses:
        parentAdoption.confirmedExitedTransientProcesses,
      rootPid: root.pid,
      packaged,
      deadlineAt: 10_000,
      now: () => 0,
      delay: async () => undefined,
      queryCurrent: async () => {
        query += 1;
        return [root, exactParent, exactChild];
      }
    });

    expect(adopted).toEqual({
      ownedProcesses: [
        root,
        { ...exactParent, kind: "service" },
        { ...exactChild, kind: "service" }
      ],
      confirmedExitedTransientProcesses: [],
      observedProcesses: [root, exactParent, exactChild]
    });
    expect(query).toBe(2);
  });

  it("fails if the verified parent identity changes during confirmation", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, partial],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => [
          {
            ...root,
            creationDate: "2026-07-29T18:15:22.0000000Z"
          },
          {
            ...partial,
            executablePath: packaged.serviceExecutablePath
          }
        ]
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false
    });
  });

  it("enforces the confirmation deadline after a slow inventory query", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    let clock = 0;

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, partial],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => clock,
        delay: async (milliseconds) => {
          clock += milliseconds;
        },
        queryCurrent: async () => {
          clock = 6_000;
          return [
            root,
            {
              ...partial,
              executablePath: packaged.serviceExecutablePath
            }
          ];
        }
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_TIMEOUT"
    });
  });

  it("fails if a new child path remains unavailable through the bound", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const partial = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });
    let clock = 0;

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, partial],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => clock,
        delay: async (milliseconds) => {
          clock += milliseconds;
        },
        confirmationWindowMs: 200,
        confirmationPollMs: 100,
        queryCurrent: async () => [root, partial]
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PATH_CONFIRMATION_TIMEOUT"
    });
  });

  it("fails closed on an ancestry-linked child with the wrong path", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const candidate = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath:
        "C:\\unrelated\\cinematic-story-service.exe",
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [root, candidate],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("does not retry when an ancestry-linked child predates its parent", async () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "app"
    });
    const candidate = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:20.0000000Z"
    });

    let queries = 0;

    await expect(
      adoptVerifiedProcessTreeWithPathConfirmation({
        current: [root, candidate],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged,
        deadlineAt: 10_000,
        now: () => 0,
        delay: async () => undefined,
        queryCurrent: async () => {
          queries += 1;
          return [root, candidate];
        }
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
      retryable: false,
      ambiguityReason: "NEW_CHILD_PREDATES_ROOT"
    });
    expect(queries).toBe(0);
  });

  it("preserves already verified identities after the root exits", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const service = ownedProcess({
      pid: 4101,
      parentPid: 4100,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z",
      kind: "service"
    });

    const afterExit = adoptVerifiedProcessTree({
      current: [],
      baseline: [],
      owned: [root, service],
      rootPid: root.pid,
      packaged
    });

    expect(afterExit).toEqual([root, service]);
  });

  it("fails closed on a late exact-path candidate after the root exits", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const lateService = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [lateService],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("fails closed on an exact-path orphan created through an unobserved intermediary", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:15:20.0000000Z",
      kind: "app"
    });
    const orphanedService = processIdentity({
      pid: 4102,
      parentPid: 4101,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [orphanedService],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("fails closed on a launch-window orphan whose path is unavailable", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:15:20.0000000Z",
      kind: "app"
    });
    const orphanedService = processIdentity({
      pid: 4102,
      parentPid: 4101,
      name: serviceExecutableName,
      executablePath: null,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [orphanedService],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("does not adopt an older exact-path identity outside the launch window", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      creationDate: "2026-07-29T18:15:20.0000000Z",
      kind: "app"
    });
    const olderUnrelated = processIdentity({
      pid: 3900,
      parentPid: 1,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:14:59.0000000Z"
    });

    expect(
      adoptVerifiedProcessTree({
        current: [olderUnrelated, root],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toEqual([root]);
  });

  it("fails closed when a parent PID is reused before a candidate appears", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const reusedRoot = {
      ...root,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    };
    const candidate = processIdentity({
      pid: 4101,
      parentPid: root.pid,
      name: serviceExecutableName,
      executablePath: packaged.serviceExecutablePath,
      creationDate: "2026-07-29T18:15:22.0000000Z"
    });

    expect(() =>
      adoptVerifiedProcessTree({
        current: [reusedRoot, candidate],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });

  it("fails closed when an already-owned PID is reused within one launch", () => {
    const root = ownedProcess({
      pid: 4100,
      parentPid: 5100,
      name: appExecutableName,
      executablePath: packaged.executablePath,
      kind: "app"
    });
    const reusedRoot = {
      ...root,
      creationDate: "2026-07-29T18:15:21.0000000Z"
    };

    expect(() =>
      adoptVerifiedProcessTree({
        current: [reusedRoot],
        baseline: [],
        owned: [root],
        rootPid: root.pid,
        packaged
      })
    ).toThrowError(
      expect.objectContaining({
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
  });
});

function processIdentity(
  overrides: Partial<ProcessIdentity> &
    Pick<
      ProcessIdentity,
      "pid" | "parentPid" | "name" | "executablePath"
    >
): ProcessIdentity {
  return {
    creationDate: "2026-07-29T18:15:20.0000000Z",
    ...overrides
  };
}

function ownedProcess(
  overrides: Partial<OwnedProcess> &
    Pick<
      OwnedProcess,
      | "pid"
      | "parentPid"
      | "name"
      | "executablePath"
      | "kind"
    >
): OwnedProcess {
  return {
    creationDate: "2026-07-29T18:15:20.0000000Z",
    ...overrides
  };
}

function createVirtualClock(initial = 0) {
  let current = initial;
  const delays: number[] = [];
  const now = () => current;
  const advance = (milliseconds: number) => {
    current += milliseconds;
  };
  const delay = async (milliseconds: number) => {
    delays.push(milliseconds);
    current += milliseconds;
  };
  return {
    delays,
    now,
    advance,
    delay
  };
}
