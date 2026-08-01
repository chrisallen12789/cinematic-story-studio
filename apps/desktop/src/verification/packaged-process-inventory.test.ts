import { describe, expect, it } from "vitest";

import {
  ProcessInventoryError,
  adoptVerifiedProcessTree,
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
        code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
      })
    );
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

  it("fails closed when an ancestry-linked child predates its parent", () => {
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
