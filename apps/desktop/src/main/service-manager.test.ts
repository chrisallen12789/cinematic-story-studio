// @vitest-environment node

import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";

import { describe, expect, it, vi } from "vitest";

import {
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
      await manager.stop(true);

      expect(spawnService).not.toHaveBeenCalled();
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
      await stopping;

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
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  readonly kill = vi.fn(() => true);

  asProcess(): ChildProcessWithoutNullStreams {
    return this as unknown as ChildProcessWithoutNullStreams;
  }

  confirmExit(code: number): void {
    if (this.exitCode !== null || this.signalCode !== null) {
      return;
    }
    this.exitCode = code;
    this.emit("exit", code, null);
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
