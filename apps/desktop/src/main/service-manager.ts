import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import { EventEmitter } from "node:events";
import { access, lstat, mkdir, realpath } from "node:fs/promises";
import path from "node:path";

import type { HealthResponse } from "@cinematic-story-studio/contracts/api";

import {
  DESKTOP_CONTRACT_VERSION,
  type BackendSnapshot
} from "../shared/desktop-api.js";
import { BackendUnavailableError, DesktopMainError } from "./errors.js";
import { parseReadyLine, validateHealthResponse } from "./validation.js";

const READY_LINE_MAX_BYTES = 4_096;
const BOOTSTRAP_MAX_BYTES = 512;
const STARTUP_TIMEOUT_MS = 20_000;
const HEALTH_TIMEOUT_MS = 2_500;
const HEALTH_INTERVAL_MS = 5_000;
// The service gives its worker up to 15 seconds to finish a checkpoint-safe stop.
const SHUTDOWN_TIMEOUT_MS = 20_000;
const FORCED_TERMINATION_TIMEOUT_MS = 2_000;
const MAX_AUTOMATIC_RESTARTS = 2;
const RESTART_WINDOW_MS = 60_000;

export interface ServiceManagerOptions {
  readonly isPackaged: boolean;
  readonly appPath: string;
  readonly resourcesPath: string;
  readonly userDataPath: string;
}

interface ServiceCredentials {
  readonly port: number;
  readonly token: string;
  readonly instanceId: string;
}

interface LaunchCommand {
  readonly command: string;
  readonly arguments: readonly string[];
  readonly environment: NodeJS.ProcessEnv;
}

type ServiceSpawner = (
  command: string,
  arguments_: readonly string[],
  options: {
    readonly cwd: string;
    readonly environment: NodeJS.ProcessEnv;
  }
) => ChildProcessWithoutNullStreams;

export interface ServiceManagerDependencies {
  readonly spawnService: ServiceSpawner;
  readonly beforeSpawn?: (signal: AbortSignal) => Promise<void>;
  readonly shutdownTimeoutMs: number;
  readonly forcedTerminationTimeoutMs: number;
}

export type ServiceTerminationMethod =
  | "already_exited"
  | "stdin_eof"
  | "force_kill";

export interface ServiceProcessTermination {
  readonly pid: number;
  readonly method: ServiceTerminationMethod;
  readonly exitCode: number | null;
  readonly signalCode: NodeJS.Signals | null;
}

export interface ServiceStopResult {
  readonly processes: readonly ServiceProcessTermination[];
  readonly forceKillUsed: boolean;
  readonly allProcessesExitedGracefully: boolean;
}

class ServiceStartCancelledError extends DesktopMainError {
  constructor() {
    super(
      "SERVICE_START_CANCELLED",
      "The local service startup was cancelled.",
      true
    );
  }
}

export class ServiceManager {
  readonly #events = new EventEmitter();
  readonly #expectedExits = new WeakSet<ChildProcessWithoutNullStreams>();
  readonly #terminationPromises = new WeakMap<
    ChildProcessWithoutNullStreams,
    Promise<ServiceProcessTermination>
  >();
  readonly #recordedTerminations =
    new WeakSet<ChildProcessWithoutNullStreams>();
  readonly #terminationHistory: ServiceProcessTermination[] = [];
  readonly #dependencies: ServiceManagerDependencies;
  readonly #options: ServiceManagerOptions;
  #snapshot: BackendSnapshot = createSnapshot(
    "starting",
    "Starting the local service..."
  );
  #child: ChildProcessWithoutNullStreams | null = null;
  #credentials: ServiceCredentials | null = null;
  #startPromise: Promise<BackendSnapshot> | null = null;
  #startAbortController: AbortController | null = null;
  #stopPromise: Promise<ServiceStopResult> | null = null;
  #healthTimer: NodeJS.Timeout | null = null;
  #restartTimer: NodeJS.Timeout | null = null;
  #restartHistory: number[] = [];
  #shuttingDown = false;
  #generation = 0;

  constructor(
    options: ServiceManagerOptions,
    dependencies: Partial<ServiceManagerDependencies> = {}
  ) {
    this.#options = options;
    this.#dependencies = {
      spawnService:
        dependencies.spawnService ??
        ((command, arguments_, spawnOptions) =>
          spawn(command, [...arguments_], {
            cwd: spawnOptions.cwd,
            env: spawnOptions.environment,
            shell: false,
            windowsHide: true,
            stdio: ["pipe", "pipe", "pipe"]
          })),
      beforeSpawn: dependencies.beforeSpawn,
      shutdownTimeoutMs:
        dependencies.shutdownTimeoutMs ?? SHUTDOWN_TIMEOUT_MS,
      forcedTerminationTimeoutMs:
        dependencies.forcedTerminationTimeoutMs ??
        FORCED_TERMINATION_TIMEOUT_MS
    };
  }

  get snapshot(): BackendSnapshot {
    return this.#snapshot;
  }

  onStatus(listener: (snapshot: BackendSnapshot) => void): () => void {
    this.#events.on("status", listener);
    return () => {
      this.#events.off("status", listener);
    };
  }

  connection(): ServiceCredentials {
    if (this.#credentials === null) {
      throw new BackendUnavailableError();
    }
    return this.#credentials;
  }

  async start(): Promise<BackendSnapshot> {
    const pendingStop = this.#stopPromise;
    if (pendingStop !== null) {
      await pendingStop;
    }
    if (this.#shuttingDown) {
      throw new DesktopMainError(
        "SERVICE_STOPPED",
        "The local service cannot start during final shutdown.",
        false
      );
    }
    if (this.#startPromise !== null) {
      return this.#startPromise;
    }
    if (
      this.#credentials !== null &&
      this.#child !== null &&
      !hasChildExited(this.#child)
    ) {
      return this.#snapshot;
    }
    if (this.#child !== null) {
      if (hasChildExited(this.#child)) {
        this.#child = null;
      } else {
        throw terminationFailedError();
      }
    }

    const generation = ++this.#generation;
    const controller = new AbortController();
    this.#startAbortController = controller;
    const operation = this.#startOwnedService(generation, controller.signal);
    this.#startPromise = operation;
    try {
      return await operation;
    } finally {
      if (this.#startPromise === operation) {
        this.#startPromise = null;
      }
      if (this.#startAbortController === controller) {
        this.#startAbortController = null;
      }
    }
  }

  async reconnect(): Promise<BackendSnapshot> {
    this.#clearRestartTimer();
    await this.stop(false);
    return this.start();
  }

  async stop(finalShutdown = true): Promise<ServiceStopResult> {
    if (finalShutdown) {
      this.#shuttingDown = true;
    }
    ++this.#generation;
    this.#startAbortController?.abort();
    this.#clearHealthTimer();
    this.#clearRestartTimer();
    this.#credentials = null;
    if (this.#stopPromise !== null) {
      return this.#stopPromise;
    }

    const operation = this.#stopOwnedService();
    this.#stopPromise = operation;
    try {
      return await operation;
    } finally {
      if (this.#stopPromise === operation) {
        this.#stopPromise = null;
      }
    }
  }

  async #stopOwnedService(): Promise<ServiceStopResult> {
    if (this.#child !== null || this.#startPromise !== null) {
      this.#setSnapshot(
        createSnapshot("stopping", "Stopping the local service...")
      );
    }
    try {
      for (;;) {
        const child = this.#child;
        if (child !== null) {
          await this.#terminateChild(child);
        }

        const startup = this.#startPromise;
        if (startup !== null) {
          await startup.catch(() => undefined);
        }

        if (this.#child === null && this.#startPromise === null) {
          break;
        }
        if (this.#child === child && this.#startPromise === startup) {
          throw terminationFailedError();
        }
      }
    } catch (error) {
      this.#setSnapshot(
        createSnapshot(
          "unavailable",
          "The local service process could not be terminated."
        )
      );
      throw error instanceof DesktopMainError
        ? error
        : terminationFailedError();
    }

    this.#setSnapshot(
      createSnapshot(
        this.#shuttingDown ? "stopped" : "disconnected",
        this.#shuttingDown
          ? "Local service stopped."
          : "Local service disconnected."
      )
    );
    return serviceStopResult(this.#terminationHistory);
  }

  async #startOwnedService(
    generation: number,
    signal: AbortSignal
  ): Promise<BackendSnapshot> {
    this.#setSnapshotForAttempt(
      generation,
      signal,
      createSnapshot("starting", "Starting the local service...")
    );
    const token = randomBytes(32).toString("base64url");
    const nonce = randomBytes(24).toString("base64url");
    const runtimeDirectory = path.join(
      this.#options.userDataPath,
      "service-runtime"
    );
    let child: ChildProcessWithoutNullStreams | null = null;
    let phase: "installation" | "spawn" | "verification" = "installation";
    try {
      await abortable(
        mkdir(runtimeDirectory, { recursive: true, mode: 0o700 }),
        signal
      );
      const launch = await abortable(this.#resolveLaunchCommand(), signal);
      phase = "spawn";
      if (this.#dependencies.beforeSpawn !== undefined) {
        await abortable(this.#dependencies.beforeSpawn(signal), signal);
      }
      this.#assertAttemptActive(generation, signal);

      child = this.#dependencies.spawnService(
        launch.command,
        launch.arguments,
        {
          cwd: runtimeDirectory,
          environment: launch.environment
        }
      );
      this.#child = child;
      child.stderr.resume();
      const spawnedChild = child;
      child.once("exit", () => {
        this.#handleChildExit(spawnedChild);
      });
      phase = "verification";
      const readiness = this.#waitForReadiness(child, nonce, signal);
      const bootstrap = createServiceBootstrapRecord(token, nonce);
      await abortable(writeToStdin(child, bootstrap), signal);
      const ready = await withTimeout(
        readiness,
        STARTUP_TIMEOUT_MS,
        "SERVICE_START_TIMEOUT"
      );
      this.#assertAttemptActive(generation, signal);
      child.stdout.resume();
      const credentials: ServiceCredentials = {
        port: ready.port,
        instanceId: ready.instanceId,
        token
      };

      const health = await this.#waitForAuthenticatedHealth(
        credentials,
        generation,
        signal
      );
      this.#assertAttemptActive(generation, signal);
      this.#credentials = credentials;
      this.#setSnapshotForAttempt(
        generation,
        signal,
        createSnapshot(
          health.status === "degraded" ? "degraded" : "ready",
          health.status === "degraded"
            ? "Backend connected with limited capability."
            : "Backend ready.",
          health
        )
      );
      this.#startHealthMonitor();
      return this.#snapshot;
    } catch (error) {
      this.#credentials = null;
      let failure = this.#startupFailure(error, phase);
      if (child !== null && !hasChildExited(child)) {
        try {
          await this.#terminateChild(child);
        } catch (terminationError) {
          failure =
            terminationError instanceof DesktopMainError
              ? terminationError
              : terminationFailedError();
        }
      }
      if (!this.#isAttemptActive(generation, signal)) {
        throw failure.code === "SERVICE_TERMINATION_FAILED"
          ? failure
          : new ServiceStartCancelledError();
      }
      this.#setSnapshotForAttempt(
        generation,
        signal,
        createSnapshot(
          "unavailable",
          phase === "installation"
            ? "The local service installation could not be found."
            : failure.code === "SERVICE_START_TIMEOUT"
              ? "The local service did not become ready in time."
              : "The local service could not be verified."
        )
      );
      throw failure;
    }
  }

  #startupFailure(
    error: unknown,
    phase: "installation" | "spawn" | "verification"
  ): DesktopMainError {
    if (error instanceof DesktopMainError) {
      return error;
    }
    if (phase === "installation") {
      return new DesktopMainError(
        "SERVICE_NOT_INSTALLED",
        "The local service installation could not be found.",
        true
      );
    }
    return new DesktopMainError(
      "SERVICE_START_FAILED",
      phase === "spawn"
        ? "The local service could not be started."
        : "The local service could not be verified.",
      true
    );
  }

  #isAttemptActive(generation: number, signal: AbortSignal): boolean {
    return (
      generation === this.#generation &&
      !signal.aborted &&
      !this.#shuttingDown &&
      this.#stopPromise === null
    );
  }

  #assertAttemptActive(generation: number, signal: AbortSignal): void {
    if (!this.#isAttemptActive(generation, signal)) {
      throw new ServiceStartCancelledError();
    }
  }

  #setSnapshotForAttempt(
    generation: number,
    signal: AbortSignal,
    snapshot: BackendSnapshot
  ): void {
    this.#assertAttemptActive(generation, signal);
    this.#setSnapshot(snapshot);
  }

  async #terminateChild(
    child: ChildProcessWithoutNullStreams
  ): Promise<ServiceProcessTermination> {
    if (hasChildExited(child)) {
      if (this.#child === child) {
        this.#child = null;
      }
      return this.#recordTermination(
        child,
        serviceProcessTermination(child, "already_exited")
      );
    }
    const existing = this.#terminationPromises.get(child);
    if (existing !== undefined) {
      return existing;
    }

    const operation = this.#terminateOwnedChild(child);
    this.#terminationPromises.set(child, operation);
    try {
      return this.#recordTermination(child, await operation);
    } finally {
      if (this.#terminationPromises.get(child) === operation) {
        this.#terminationPromises.delete(child);
      }
    }
  }

  async #terminateOwnedChild(
    child: ChildProcessWithoutNullStreams
  ): Promise<ServiceProcessTermination> {
    this.#expectedExits.add(child);
    if (!child.stdin.destroyed && !child.stdin.writableEnded) {
      child.stdin.end();
    }
    let method: ServiceTerminationMethod = "stdin_eof";
    if (
      !(await waitForChildExit(
        child,
        this.#dependencies.shutdownTimeoutMs
      ))
    ) {
      method = "force_kill";
      child.kill();
      if (
        !(await waitForChildExit(
          child,
          this.#dependencies.forcedTerminationTimeoutMs
        ))
      ) {
        throw terminationFailedError();
      }
    }
    if (this.#child === child) {
      this.#child = null;
    }
    return serviceProcessTermination(child, method);
  }

  #recordTermination(
    child: ChildProcessWithoutNullStreams,
    value: ServiceProcessTermination
  ): ServiceProcessTermination {
    if (!this.#recordedTerminations.has(child)) {
      this.#recordedTerminations.add(child);
      this.#terminationHistory.push(value);
    }
    return value;
  }

  async #resolveLaunchCommand(): Promise<LaunchCommand> {
    if (this.#options.isPackaged) {
      const candidate = path.resolve(
        this.#options.resourcesPath,
        "service",
        "cinematic-story-service.exe"
      );
      const [canonicalResources, metadata] = await Promise.all([
        realpath(this.#options.resourcesPath),
        lstat(candidate)
      ]);
      if (!metadata.isFile() || metadata.isSymbolicLink()) {
        throw new DesktopMainError(
          "SERVICE_PATH_INVALID",
          "The packaged service path was invalid.",
          false
        );
      }
      const executable = await realpath(candidate);
      ensurePathInside(canonicalResources, executable);
      return {
        command: executable,
        arguments: ["--data-dir", this.#options.userDataPath],
        environment: buildChildEnvironment(false)
      };
    }

    const serviceRoot = path.resolve(
      this.#options.appPath,
      "..",
      "local-service"
    );
    const sourceRoot = path.join(serviceRoot, "src");
    const configuredPython =
      boundedEnvironmentPath(process.env.CSS_PYTHON) ??
      boundedEnvironmentPath(process.env.CSS_SERVICE_PYTHON);
    const virtualEnvironmentPython = path.join(
      serviceRoot,
      ".venv",
      "Scripts",
      "python.exe"
    );
    const python =
      configuredPython ??
      ((await pathExists(virtualEnvironmentPython))
        ? virtualEnvironmentPython
        : "python");

    return {
      command: python,
      arguments: [
        "-m",
        "cinematic_story_service.launcher",
        "--data-dir",
        this.#options.userDataPath
      ],
      environment: {
        ...buildChildEnvironment(true),
        PYTHONPATH: sourceRoot
      }
    };
  }

  #waitForReadiness(
    child: ChildProcessWithoutNullStreams,
    nonce: string,
    signal: AbortSignal
  ): Promise<{ port: number; instanceId: string }> {
    return new Promise((resolve, reject) => {
      let pending = Buffer.alloc(0);
      const cleanup = () => {
        child.stdout.off("data", onData);
        child.off("error", onError);
        child.off("exit", onExit);
        signal.removeEventListener("abort", onAbort);
      };
      const fail = (error: Error) => {
        cleanup();
        reject(error);
      };
      const onData = (chunk: Buffer) => {
        pending = Buffer.concat([pending, chunk]);
        if (pending.byteLength > READY_LINE_MAX_BYTES) {
          fail(
            new DesktopMainError(
              "SERVICE_READY_INVALID",
              "The local service readiness record exceeded its limit.",
              false
            )
          );
          return;
        }
        const newline = pending.indexOf(0x0a);
        if (newline < 0) {
          return;
        }
        const line = pending.subarray(0, newline).toString("utf8").trimEnd();
        try {
          const ready = parseReadyLine(line, nonce);
          cleanup();
          resolve(ready);
        } catch (error) {
          fail(
            error instanceof Error
              ? error
              : new Error("Invalid service readiness record.")
          );
        }
      };
      const onError = () => {
        fail(
          new DesktopMainError(
            "SERVICE_START_FAILED",
            "The local service process failed during startup.",
            true
          )
        );
      };
      const onExit = () => {
        fail(
          new DesktopMainError(
            "SERVICE_EXITED_EARLY",
            "The local service exited during startup.",
            true
          )
        );
      };
      const onAbort = () => {
        fail(new ServiceStartCancelledError());
      };
      if (signal.aborted) {
        onAbort();
        return;
      }
      child.stdout.on("data", onData);
      child.once("error", onError);
      child.once("exit", onExit);
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  async #waitForAuthenticatedHealth(
    credentials: ServiceCredentials,
    generation: number,
    signal: AbortSignal
  ): Promise<HealthResponse> {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS;
    let lastError: unknown;
    while (Date.now() < deadline) {
      this.#assertAttemptActive(generation, signal);
      try {
        const health = await abortable(this.#fetchHealth(credentials), signal);
        this.#assertAttemptActive(generation, signal);
        if (health.instanceId !== credentials.instanceId) {
          throw new DesktopMainError(
            "SERVICE_INSTANCE_MISMATCH",
            "The local service identity could not be verified.",
            false
          );
        }
        if (health.status === "ready" || health.status === "degraded") {
          return health;
        }
      } catch (error) {
        if (!this.#isAttemptActive(generation, signal)) {
          throw new ServiceStartCancelledError();
        }
        lastError = error;
      }
      await abortable(delay(180), signal);
    }
    if (lastError instanceof DesktopMainError) {
      throw lastError;
    }
    throw new DesktopMainError(
      "SERVICE_HEALTH_TIMEOUT",
      "The local service did not pass its authenticated health check.",
      true
    );
  }

  async #fetchHealth(
    credentials: ServiceCredentials
  ): Promise<HealthResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
    try {
      const response = await fetch(
        `http://127.0.0.1:${credentials.port}/api/v1/health`,
        {
          method: "GET",
          headers: {
            Authorization: `Bearer ${credentials.token}`,
            Accept: "application/json",
            "Cache-Control": "no-store"
          },
          cache: "no-store",
          signal: controller.signal
        }
      );
      if (!response.ok) {
        throw new DesktopMainError(
          "SERVICE_HEALTH_REJECTED",
          "The local service rejected its health check.",
          true
        );
      }
      const contentLength = Number(response.headers.get("content-length") ?? 0);
      if (contentLength > 64 * 1024) {
        throw new DesktopMainError(
          "SERVICE_RESPONSE_TOO_LARGE",
          "The local service health response exceeded its limit.",
          false
        );
      }
      const bytes = await readFetchBodyLimited(response, 64 * 1024);
      return validateHealthResponse(JSON.parse(bytes.toString("utf8")) as unknown);
    } catch (error) {
      if (error instanceof DesktopMainError) {
        throw error;
      }
      throw new DesktopMainError(
        "SERVICE_HEALTH_UNAVAILABLE",
        "The local service health check was unavailable.",
        true
      );
    } finally {
      clearTimeout(timeout);
    }
  }

  #startHealthMonitor(): void {
    this.#clearHealthTimer();
    this.#healthTimer = setInterval(() => {
      void this.#checkConnectedHealth();
    }, HEALTH_INTERVAL_MS);
  }

  async #checkConnectedHealth(): Promise<void> {
    const credentials = this.#credentials;
    if (credentials === null || this.#shuttingDown) {
      return;
    }
    try {
      const health = await this.#fetchHealth(credentials);
      if (
        this.#credentials !== credentials ||
        this.#shuttingDown ||
        this.#stopPromise !== null
      ) {
        return;
      }
      if (health.instanceId !== credentials.instanceId) {
        throw new Error("Service identity changed.");
      }
      this.#setSnapshot(
        createSnapshot(
          health.status === "degraded" ? "degraded" : "ready",
          health.status === "degraded"
            ? "Backend connected with limited capability."
            : "Backend ready.",
          health
        )
      );
    } catch {
      if (
        this.#credentials !== credentials ||
        this.#shuttingDown ||
        this.#stopPromise !== null
      ) {
        return;
      }
      this.#setSnapshot(
        createSnapshot(
          "disconnected",
          "Backend connection interrupted. Your unsaved form values are preserved."
        )
      );
    }
  }

  #handleChildExit(child: ChildProcessWithoutNullStreams): void {
    if (this.#child !== child) {
      return;
    }
    this.#child = null;
    this.#credentials = null;
    this.#clearHealthTimer();
    if (this.#expectedExits.has(child) || this.#shuttingDown) {
      return;
    }
    ++this.#generation;
    this.#startAbortController?.abort();
    this.#setSnapshot(
      createSnapshot(
        "disconnected",
        "The local service stopped unexpectedly. Reconnecting..."
      )
    );
    this.#scheduleAutomaticRestart();
  }

  #scheduleAutomaticRestart(): void {
    const now = Date.now();
    this.#restartHistory = this.#restartHistory.filter(
      (timestamp) => now - timestamp < RESTART_WINDOW_MS
    );
    if (this.#restartHistory.length >= MAX_AUTOMATIC_RESTARTS) {
      this.#setSnapshot(
        createSnapshot(
          "unavailable",
          "Automatic reconnect paused. Use Retry connection when ready."
        )
      );
      return;
    }
    this.#restartHistory.push(now);
    this.#clearRestartTimer();
    this.#restartTimer = setTimeout(() => {
      this.#restartTimer = null;
      void this.start().catch(() => undefined);
    }, 750 * this.#restartHistory.length);
  }

  #setSnapshot(snapshot: BackendSnapshot): void {
    this.#snapshot = snapshot;
    this.#events.emit("status", snapshot);
  }

  #clearHealthTimer(): void {
    if (this.#healthTimer !== null) {
      clearInterval(this.#healthTimer);
      this.#healthTimer = null;
    }
  }

  #clearRestartTimer(): void {
    if (this.#restartTimer !== null) {
      clearTimeout(this.#restartTimer);
      this.#restartTimer = null;
    }
  }
}

export function createServiceBootstrapRecord(token: string, nonce: string): string {
  const bootstrap = `${JSON.stringify({
    token,
    nonce,
    protocolVersion: DESKTOP_CONTRACT_VERSION
  })}\n`;
  if (Buffer.byteLength(bootstrap, "utf8") > BOOTSTRAP_MAX_BYTES) {
    throw new DesktopMainError(
      "SERVICE_BOOTSTRAP_INVALID",
      "The local service bootstrap record was invalid.",
      false
    );
  }
  return bootstrap;
}

function createSnapshot(
  state: BackendSnapshot["state"],
  message: string,
  health?: HealthResponse
): BackendSnapshot {
  return {
    state,
    message,
    checkedAt: new Date().toISOString(),
    health
  };
}

function buildChildEnvironment(includePath: boolean): NodeJS.ProcessEnv {
  const keys = [
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
    "LOCALAPPDATA",
    "APPDATA",
    "PATHEXT"
  ] as const;
  const environment: NodeJS.ProcessEnv = {
    PYTHONIOENCODING: "utf-8",
    PYTHONUNBUFFERED: "1"
  };
  for (const key of keys) {
    const value = process.env[key];
    if (value !== undefined) {
      environment[key] = value;
    }
  }
  if (includePath && process.env.PATH !== undefined) {
    environment.PATH = process.env.PATH;
  }
  return environment;
}

function boundedEnvironmentPath(value: string | undefined): string | null {
  if (
    value === undefined ||
    value.trim().length === 0 ||
    value.length > 1_024 ||
    value.includes("\0")
  ) {
    return null;
  }
  return value.trim();
}

async function pathExists(filePath: string): Promise<boolean> {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

function ensurePathInside(root: string, candidate: string): void {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  if (
    relative.length === 0 ||
    relative.startsWith(`..${path.sep}`) ||
    relative === ".." ||
    path.isAbsolute(relative)
  ) {
    throw new DesktopMainError(
      "SERVICE_PATH_INVALID",
      "The packaged service path was invalid.",
      false
    );
  }
}

async function writeToStdin(
  child: ChildProcessWithoutNullStreams,
  value: string
): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    child.stdin.write(value, "utf8", (error) => {
      if (error) {
        reject(error);
      } else {
        resolve();
      }
    });
  });
}

async function withTimeout<T>(
  operation: Promise<T>,
  timeoutMs: number,
  code: string
): Promise<T> {
  let timeout: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(() => {
          reject(
            new DesktopMainError(
              code,
              "The local service startup timed out.",
              true
            )
          );
        }, timeoutMs);
      })
    ]);
  } finally {
    if (timeout !== undefined) {
      clearTimeout(timeout);
    }
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function abortable<T>(
  operation: Promise<T>,
  signal: AbortSignal
): Promise<T> {
  if (signal.aborted) {
    return Promise.reject(new ServiceStartCancelledError());
  }
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      reject(new ServiceStartCancelledError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    void operation.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error instanceof Error ? error : new Error("Operation failed."));
      }
    );
  });
}

function hasChildExited(child: ChildProcessWithoutNullStreams): boolean {
  return child.exitCode !== null || child.signalCode !== null;
}

function serviceProcessTermination(
  child: ChildProcessWithoutNullStreams,
  method: ServiceTerminationMethod
): ServiceProcessTermination {
  if (
    child.pid === undefined ||
    !Number.isSafeInteger(child.pid) ||
    child.pid <= 0
  ) {
    throw terminationFailedError();
  }
  return {
    pid: child.pid,
    method,
    exitCode: child.exitCode,
    signalCode: child.signalCode
  };
}

function serviceStopResult(
  values: readonly ServiceProcessTermination[]
): ServiceStopResult {
  const processes = values.map((value) => ({ ...value }));
  return {
    processes,
    forceKillUsed: processes.some(
      (value) => value.method === "force_kill"
    ),
    allProcessesExitedGracefully: processes.every(
      (value) =>
        value.method === "stdin_eof" &&
        value.exitCode === 0 &&
        value.signalCode === null
    )
  };
}

async function waitForChildExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number
): Promise<boolean> {
  if (hasChildExited(child)) {
    return true;
  }
  return new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (exited: boolean) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      child.off("exit", onExit);
      resolve(exited);
    };
    const onExit = () => {
      finish(true);
    };
    const timeout = setTimeout(() => {
      finish(hasChildExited(child));
    }, timeoutMs);
    child.once("exit", onExit);
    if (hasChildExited(child)) {
      finish(true);
    }
  });
}

function terminationFailedError(): DesktopMainError {
  return new DesktopMainError(
    "SERVICE_TERMINATION_FAILED",
    "The owned local service process did not terminate in time.",
    true
  );
}

async function readFetchBodyLimited(
  response: Response,
  maximumBytes: number
): Promise<Buffer> {
  if (response.body === null) {
    return Buffer.alloc(0);
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const result = await reader.read();
    if (result.done) {
      break;
    }
    total += result.value.byteLength;
    if (total > maximumBytes) {
      await reader.cancel();
      throw new DesktopMainError(
        "SERVICE_RESPONSE_TOO_LARGE",
        "The local service response exceeded its limit.",
        false
      );
    }
    chunks.push(result.value);
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)), total);
}
