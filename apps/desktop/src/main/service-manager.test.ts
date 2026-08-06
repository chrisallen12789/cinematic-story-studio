// @vitest-environment node

import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { PassThrough, type TransformCallback } from "node:stream";

import { describe, expect, it, vi } from "vitest";

import {
  buildChildEnvironment,
  createServiceBootstrapRecord,
  ServiceManager,
  type ServiceManagerDependencies,
  type ServiceManagerOptions
} from "./service-manager";

describe("service bootstrap", () => {
  it("sends the desktop protocol version before the service opens storage", () => {
    const record = createServiceBootstrapRecord("token-value", "nonce-value");

    expect(record.endsWith("\n")).toBe(true);
    expect(Buffer.byteLength(record, "utf8")).toBeLessThanOrEqual(512);
    expect(JSON.parse(record) as unknown).toEqual({
      token: "token-value",
      nonce: "nonce-value",
      protocolVersion: "1.0.0"
    });
  });

  it("rejects an oversized bootstrap record", () => {
    expect(() =>
      createServiceBootstrapRecord("x".repeat(512), "nonce-value")
    ).toThrow("bootstrap record was invalid");
  });

  it("passes only the exact test-owned runtime shutdown evidence flag", () => {
    const key = "CSS_PHASE3B_RUNTIME_SHUTDOWN_EVIDENCE";
    const prior = process.env[key];
    try {
      delete process.env[key];
      expect(buildChildEnvironment(false)).not.toHaveProperty(key);

      process.env[key] = "true";
      expect(buildChildEnvironment(false)).not.toHaveProperty(key);

      process.env[key] = "1";
      expect(buildChildEnvironment(false)).toMatchObject({ [key]: "1" });
    } finally {
      if (prior === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = prior;
      }
    }
  });
});

describe("ServiceManager lifecycle", () => {
  it("cancels a final stop before spawn and never publishes a later child", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
    const child = new FakeChild();
    const spawnService = vi.fn(createSpawner(child));
    let signalEntered: (() => void) | undefined;
    const entered = new Promise<void>((resolve) => {
      signalEntered = resolve;
    });
    const manager = new ServiceManager(options(directory), {
      spawnService,
      beforeSpawn: (signal) =>
        new Promise<void>((resolve) => {
          signalEntered?.();
          if (signal.aborted) {
            resolve();
            return;
          }
          signal.addEventListener("abort", () => resolve(), { once: true });
        }),
      shutdownTimeoutMs: 10,
      forcedTerminationTimeoutMs: 50
    });

    try {
      const startup = captureError(manager.start());
      await entered;
      const result = await manager.stop(true);

      expect(spawnService).not.toHaveBeenCalled();
      expect(result).toEqual({
        processes: [],
        forceKillUsed: false,
        allProcessesExitedGracefully: true
      });
      expect(manager.snapshot.state).toBe("stopped");
      expect(await startup).toMatchObject({
        code: "SERVICE_START_CANCELLED"
      });
      expect(await captureError(manager.start())).toMatchObject({
        code: "SERVICE_STOPPED"
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it.each([
    [
      "storage_locked",
      "STORAGE_LOCKED",
      "The project storage is already in use by another local service.",
      true
    ],
    [
      "incompatible_schema",
      "DATABASE_SCHEMA_UNSUPPORTED",
      "The project database schema is not supported by this service.",
      false
    ],
    [
      "startup_failed",
      "SERVICE_START_FAILED",
      "The local service could not be verified.",
      true
    ]
  ] as const)(
    "maps the bounded %s launcher diagnostic without reporting cancellation",
    async (diagnostic, code, message, retryable) => {
      const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
      const child = new FakeChild();
      const spawnService = vi.fn(createSpawner(child));
      const manager = new ServiceManager(options(directory), {
        spawnService,
        shutdownTimeoutMs: 100,
        forcedTerminationTimeoutMs: 50
      });

      try {
        const startup = captureError(manager.start());
        await vi.waitFor(() => {
          expect(spawnService).toHaveBeenCalledOnce();
        });
        child.stderr.write(`CSS_ERROR ${diagnostic}\n`);
        child.confirmExit(1);

        expect(await startup).toMatchObject({
          code,
          message,
          retryable,
          details: {
            startupStage: "readiness",
            exitCode: 1,
            signalCode: "none",
            stderrDiagnosticCode: diagnostic,
            stderrLimitExceeded: false
          }
        });
        expect(manager.snapshot).toMatchObject({
          state: "unavailable",
          message
        });
        expect(child.kill).not.toHaveBeenCalled();
      } finally {
        child.confirmExit(1);
        await manager.stop(true).catch(() => undefined);
        await rm(directory, { recursive: true, force: true });
      }
    }
  );

  it.each([
    [
      "unknown",
      "CSS_ERROR future_code\n"
    ],
    [
      "multiline",
      "CSS_ERROR storage_locked\nC:\\private\\project-token\n"
    ],
    [
      "oversized",
      `CSS_ERROR storage_locked\n${"private-project-token".repeat(64)}\n`
    ]
  ] as const)(
    "uses a generic early-exit failure for %s startup stderr",
    async (testCase, diagnostic) => {
      const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
      const child = new FakeChild();
      const spawnService = vi.fn(createSpawner(child));
      const manager = new ServiceManager(options(directory), {
        spawnService,
        shutdownTimeoutMs: 100,
        forcedTerminationTimeoutMs: 50
      });

      try {
        const startup = captureError(manager.start());
        await vi.waitFor(() => {
          expect(spawnService).toHaveBeenCalledOnce();
        });
        child.stderr.write(diagnostic);
        child.confirmExit(1);

        const failure = await startup;
        expect(failure).toMatchObject({
          code: "SERVICE_EXITED_EARLY",
          message: "The local service exited during startup.",
          retryable: true,
          details: {
            startupStage: "readiness",
            exitCode: 1,
            signalCode: "none",
            stderrDiagnosticCode: "unavailable",
            stderrLimitExceeded: testCase === "oversized"
          }
        });
        expect((failure as Error).message).not.toContain("private");
        expect((failure as Error).message).not.toContain("project-token");
        expect(manager.snapshot).toMatchObject({
          state: "unavailable",
          message: "The local service could not be verified."
        });
      } finally {
        child.confirmExit(1);
        await manager.stop(true).catch(() => undefined);
        await rm(directory, { recursive: true, force: true });
      }
    }
  );

  it("preserves an unexpected empty-stderr early exit", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
    const child = new FakeChild();
    const spawnService = vi.fn(createSpawner(child));
    const manager = new ServiceManager(options(directory), {
      spawnService,
      shutdownTimeoutMs: 100,
      forcedTerminationTimeoutMs: 50
    });

    try {
      const startup = captureError(manager.start());
      await vi.waitFor(() => {
        expect(spawnService).toHaveBeenCalledOnce();
      });
      child.confirmExit(1);

      expect(await startup).toMatchObject({
        code: "SERVICE_EXITED_EARLY",
        message: "The local service exited during startup.",
        details: {
          startupStage: "readiness",
          exitCode: 1,
          signalCode: "none",
          stderrDiagnosticCode: "unavailable",
          stderrLimitExceeded: false
        }
      });
      expect(manager.snapshot.state).toBe("unavailable");
    } finally {
      child.confirmExit(1);
      await manager.stop(true).catch(() => undefined);
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("observes an early readiness rejection while the bootstrap write is pending", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
    const stdin = new DeferredPassThrough();
    const child = new FakeChild(stdin);
    const spawnService = vi.fn(createSpawner(child));
    const manager = new ServiceManager(options(directory), {
      spawnService,
      shutdownTimeoutMs: 100,
      forcedTerminationTimeoutMs: 50
    });
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandled);

    try {
      const startup = captureError(manager.start());
      await vi.waitFor(() => {
        expect(spawnService).toHaveBeenCalledOnce();
        expect(stdin.writePending).toBe(true);
      });
      child.stderr.write("CSS_ERROR startup_failed\n");
      child.confirmExit(1);
      await new Promise<void>((resolve) => setImmediate(resolve));
      expect(unhandled).toEqual([]);

      stdin.release();
      expect(await startup).toMatchObject({
        code: "SERVICE_START_FAILED",
        details: {
          startupStage: "readiness",
          exitCode: 1,
          stderrDiagnosticCode: "startup_failed"
        }
      });
      await new Promise<void>((resolve) => setImmediate(resolve));
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", onUnhandled);
      stdin.release();
      child.confirmExit(1);
      await manager.stop(true).catch(() => undefined);
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("keeps an explicit stop authoritative over buffered startup stderr", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
    const child = new FakeChild();
    const spawnService = vi.fn(createSpawner(child));
    const manager = new ServiceManager(options(directory), {
      spawnService,
      shutdownTimeoutMs: 100,
      forcedTerminationTimeoutMs: 50
    });

    try {
      const startup = captureError(manager.start());
      await vi.waitFor(() => {
        expect(spawnService).toHaveBeenCalledOnce();
      });
      child.stderr.write("CSS_ERROR storage_locked\n");
      child.stdin.once("finish", () => {
        child.confirmExit(0);
      });

      const result = await manager.stop(false);

      expect(await startup).toMatchObject({
        code: "SERVICE_START_CANCELLED"
      });
      expect(result).toMatchObject({
        forceKillUsed: false,
        allProcessesExitedGracefully: true
      });
      expect(manager.snapshot.state).toBe("disconnected");
    } finally {
      child.confirmExit(0);
      await manager.stop(false).catch(() => undefined);
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("records a zero-code stdin-EOF shutdown as graceful", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
    const child = new FakeChild();
    const spawnService = vi.fn(createSpawner(child));
    const manager = new ServiceManager(options(directory), {
      spawnService,
      shutdownTimeoutMs: 100,
      forcedTerminationTimeoutMs: 50
    });

    try {
      const startup = captureError(manager.start());
      await vi.waitFor(() => {
        expect(spawnService).toHaveBeenCalledOnce();
      });
      child.stdin.once("finish", () => {
        child.confirmExit(0);
      });

      const result = await manager.stop(false);

      expect(result).toEqual({
        processes: [
          {
            pid: child.pid,
            method: "stdin_eof",
            exitCode: 0,
            signalCode: null
          }
        ],
        forceKillUsed: false,
        allProcessesExitedGracefully: true
      });
      expect(child.kill).not.toHaveBeenCalled();
      expect(manager.snapshot.state).toBe("disconnected");
      expect(await startup).toMatchObject({
        code: "SERVICE_START_CANCELLED"
      });
    } finally {
      child.confirmExit(0);
      await manager.stop(false).catch(() => undefined);
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("keeps stopping state until the owned child confirms a slow exit", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
    const child = new FakeChild();
    const spawnService = vi.fn(createSpawner(child));
    const manager = new ServiceManager(options(directory), {
      spawnService,
      shutdownTimeoutMs: 10,
      forcedTerminationTimeoutMs: 100
    });

    try {
      const startup = captureError(manager.start());
      await vi.waitFor(() => {
        expect(spawnService).toHaveBeenCalledOnce();
      });
      const stopping = manager.stop(false);
      await vi.waitFor(() => {
        expect(child.kill).toHaveBeenCalledOnce();
      });
      expect(manager.snapshot.state).toBe("stopping");

      child.confirmExit(0);
      const result = await stopping;

      expect(manager.snapshot.state).toBe("disconnected");
      expect(result).toEqual({
        processes: [
          {
            pid: child.pid,
            method: "force_kill",
            exitCode: 0,
            signalCode: null
          }
        ],
        forceKillUsed: true,
        allProcessesExitedGracefully: false
      });
      expect(await startup).toMatchObject({
        code: "SERVICE_START_CANCELLED"
      });
    } finally {
      child.confirmExit(0);
      await manager.stop(false).catch(() => undefined);
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("retains ownership and surfaces a bounded failure without a confirmed exit", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "css-service-race-"));
    const child = new FakeChild();
    const spawnService = vi.fn(createSpawner(child));
    const manager = new ServiceManager(options(directory), {
      spawnService,
      shutdownTimeoutMs: 5,
      forcedTerminationTimeoutMs: 5
    });

    try {
      const startup = captureError(manager.start());
      await vi.waitFor(() => {
        expect(spawnService).toHaveBeenCalledOnce();
      });
      const stopError = await captureError(manager.stop(false));

      expect(stopError).toMatchObject({
        code: "SERVICE_TERMINATION_FAILED"
      });
      expect(await startup).toMatchObject({
        code: "SERVICE_TERMINATION_FAILED"
      });
      expect(manager.snapshot.state).toBe("unavailable");
      expect(child.kill).toHaveBeenCalledOnce();

      expect(await captureError(manager.start())).toMatchObject({
        code: "SERVICE_TERMINATION_FAILED"
      });
      expect(spawnService).toHaveBeenCalledOnce();
    } finally {
      child.confirmExit(1);
      await manager.stop(false).catch(() => undefined);
      await rm(directory, { recursive: true, force: true });
    }
  });
});

class FakeChild extends EventEmitter {
  readonly pid = 42_424;
  readonly stdin: PassThrough;
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  readonly kill = vi.fn(() => true);

  constructor(stdin = new PassThrough()) {
    super();
    this.stdin = stdin;
  }

  asProcess(): ChildProcessWithoutNullStreams {
    return this as unknown as ChildProcessWithoutNullStreams;
  }

  confirmExit(code: number): void {
    if (this.exitCode !== null || this.signalCode !== null) {
      return;
    }
    this.exitCode = code;
    this.emit("exit", code, null);
    this.emit("close", code, null);
  }
}

class DeferredPassThrough extends PassThrough {
  #pending: TransformCallback | null = null;

  get writePending(): boolean {
    return this.#pending !== null;
  }

  override _transform(
    _chunk: Buffer,
    _encoding: BufferEncoding,
    callback: TransformCallback
  ): void {
    this.#pending = callback;
  }

  release(): void {
    const callback = this.#pending;
    this.#pending = null;
    callback?.(null);
  }
}

function createSpawner(
  child: FakeChild
): ServiceManagerDependencies["spawnService"] {
  return () => child.asProcess();
}

function options(userDataPath: string): ServiceManagerOptions {
  return {
    isPackaged: false,
    appPath: process.cwd(),
    resourcesPath: process.cwd(),
    userDataPath
  };
}

async function captureError(operation: Promise<unknown>): Promise<unknown> {
  try {
    return await operation;
  } catch (error) {
    return error;
  }
}
