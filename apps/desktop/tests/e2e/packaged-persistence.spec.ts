import { once } from "node:events";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";

import {
  _electron as electron,
  expect,
  test,
  type ElectronApplication
} from "@playwright/test";

import {
  isolatedEnvironmentNames,
  packagedE2eSchemaVersion,
  packagedFailureCode,
  packagedFixture,
  packagedFlow,
  runPackagedE2eEvidenceStep,
  writePackagedE2eMachineResult,
  type MachineLaunchEvidence,
  type PackagedFailureCode,
  type PackagedFailureStage
} from "../../src/verification/packaged-e2e-evidence";
import {
  adoptVerifiedProcessTree,
  appExecutableName,
  containsProcessIdentity,
  createPackagedProcessInventory,
  defaultProcessInventoryPolicy,
  matchesPackagedProcessPath,
  remainingOwnedProcesses,
  serviceExecutableName,
  type OwnedProcess,
  type PackagedProcessPaths,
  type ProcessIdentity
} from "../../src/verification/packaged-process-inventory";

const desktopRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const fixturePath = path.resolve(
  desktopRoot,
  "../../fixtures/synthetic-story/sample-story.md"
);
const packagedExecutableEnvironment = "CSS_PACKAGED_E2E_EXECUTABLE";
const evidencePathEnvironment = "CSS_PACKAGED_E2E_EVIDENCE_PATH";
const resultPathEnvironment = "CSS_PACKAGED_E2E_RESULT_PATH";
const processInventory = createPackagedProcessInventory();
const monotonicNow = () => performance.now();

test.describe("packaged desktop verification", () => {
  test.skip(
    !hasEnvironmentValue(packagedExecutableEnvironment) ||
      !hasEnvironmentValue(evidencePathEnvironment) ||
      !hasEnvironmentValue(resultPathEnvironment),
    `Set ${packagedExecutableEnvironment}, ${evidencePathEnvironment}, and ${resultPathEnvironment} to run the packaged gate.`
  );

  test("runs the synthetic persistence flow in the packaged application", async () => {
    test.setTimeout(240_000);
    const packaged = await requirePackagedExecutable(
      requiredEnvironment(packagedExecutableEnvironment)
    );
    const evidencePath = requireEvidencePath(
      requiredEnvironment(evidencePathEnvironment),
      packaged
    );
    const resultPath = requireResultPath(
      requiredEnvironment(resultPathEnvironment),
      packaged
    );

    let isolationRoot: string | null = null;
    let preexistingProcesses: readonly ProcessIdentity[] | null = null;
    let first: ElectronApplication | null = null;
    let second: ElectronApplication | null = null;
    let firstOwnership: LaunchOwnership | null = null;
    let secondOwnership: LaunchOwnership | null = null;
    let operationError: Error | null = null;
    let failureStage: PackagedFailureStage | null = null;
    let failureCode: PackagedFailureCode | null = null;
    let currentStage: PackagedFailureStage = "isolation_setup";
    let screenshotCaptured = false;
    const cleanupErrors: unknown[] = [];
    const launchEvidence: MachineLaunchEvidence[] = [];
    const begunLaunches = new Set<1 | 2>();
    const ownedLaunches = new Set<1 | 2>();
    const writeMachineResult = async (
      status: "passed" | "failed",
      cleanupCompleted: boolean
    ) => {
      await writePackagedE2eMachineResult(resultPath, {
        schemaVersion: packagedE2eSchemaVersion,
        completedAt: new Date().toISOString(),
        status,
        failureStage: status === "passed" ? null : failureStage,
        failureCode: status === "passed" ? null : failureCode,
        packagedVersion: packaged.version,
        executable: `release/${packaged.version}/win-unpacked/${appExecutableName}`,
        fixture: packagedFixture,
        isolationEnvironment: isolatedEnvironmentNames,
        completedLaunches: launchEvidence
          .map((item) => item.launch)
          .sort((left, right) => left - right),
        applicationLaunchBegan: begunLaunches.size > 0,
        ownershipEstablished:
          begunLaunches.size > 0 &&
          begunLaunches.size === ownedLaunches.size,
        cleanupCompleted,
        preexistingRelevantProcesses:
          preexistingProcesses === null
            ? null
            : preexistingProcesses
                .map(redactPreexistingProcess)
                .sort(compareEvidenceProcess),
        flow: packagedFlow,
        screenshot: {
          artifactId: "packaged-ui-screenshot",
          captured: screenshotCaptured
        },
        launches: [...launchEvidence].sort(
          (left, right) => left.launch - right.launch
        )
      });
    };
    const checkpointStage = async (stage: PackagedFailureStage) => {
      currentStage = stage;
      failureStage = stage;
      failureCode = packagedFailureCode(stage, undefined);
      await writeMachineResult("failed", false);
    };
    try {
      await Promise.all([
        mkdir(path.dirname(evidencePath), { recursive: true }),
        mkdir(path.dirname(resultPath), { recursive: true })
      ]);
      await checkpointStage("prelaunch_inventory_1");
      preexistingProcesses = await runPackagedE2eEvidenceStep(
        "prelaunch_inventory_1",
        () => queryRelevantProcesses(),
        async (stage, code) => {
          failureStage = stage;
          failureCode = code;
          await writeMachineResult("failed", false);
        }
      );
      await checkpointStage("isolation_setup");
      isolationRoot = await mkdtemp(
        path.join(tmpdir(), "css-packaged-e2e-")
      );
      const isolatedPaths: IsolatedPaths = {
        localAppData: path.join(isolationRoot, "LocalAppData"),
        roamingAppData: path.join(isolationRoot, "AppData"),
        temporaryDirectory: path.join(isolationRoot, "Temp")
      };
      await Promise.all([
        mkdir(isolatedPaths.localAppData, { recursive: true }),
        mkdir(isolatedPaths.roamingAppData, { recursive: true }),
        mkdir(isolatedPaths.temporaryDirectory, { recursive: true })
      ]);

      begunLaunches.add(1);
      await checkpointStage("launch_1");
      first = await launchPackaged(packaged.executablePath, {
        ...isolatedPaths
      });
      await checkpointStage("root_ownership_1");
      firstOwnership = await establishRootOwnership(
        first,
        preexistingProcesses,
        packaged
      );
      await checkpointStage("readiness_1");
      const firstPage = await first.firstWindow();
      await expect(
        firstPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 45_000 });
      await checkpointStage("service_ownership_1");
      firstOwnership = await expandOwnership(
        firstOwnership,
        true
      );
      ownedLaunches.add(1);
      await checkpointStage("workflow_1");
      await firstPage
        .getByLabel("New production")
        .fill("Packaged Persistence Demo");
      await firstPage.getByRole("button", { name: "Create project" }).click();
      await expect(
        firstPage.getByRole("heading", {
          name: "Packaged Persistence Demo"
        })
      ).toBeVisible();
      const createdNotice = firstPage.getByText(
        "Created Packaged Persistence Demo.",
        { exact: true }
      );
      await expect(createdNotice).toBeVisible();
      await firstPage
        .getByRole("button", { name: "Dismiss notification" })
        .click();
      await expect(createdNotice).toBeHidden();

      await first.evaluate(({ dialog }, selectedFixture) => {
        dialog.showOpenDialog = () =>
          Promise.resolve({
            canceled: false,
            filePaths: [selectedFixture],
            bookmarks: []
          });
      }, fixturePath);
      await firstPage
        .getByRole("button", { name: "Import TXT / MD" })
        .click();
      await expect(
        firstPage.getByText(
          "Imported sample-story.md without changing its text."
        )
      ).toBeVisible({ timeout: 20_000 });
      await firstPage
        .getByRole("button", { name: "Dismiss notification" })
        .click();

      await firstPage.getByRole("button", { name: "Analyze story" }).click();
      await expect
        .poll(
          async () => firstPage.locator(".job-state").allTextContents(),
          { timeout: 45_000 }
        )
        .toContain("Succeeded");
      const chapterButtons = firstPage
        .getByRole("navigation", { name: "Chapters" })
        .getByRole("button");
      const sceneButtons = firstPage
        .getByRole("navigation", { name: "Scenes" })
        .getByRole("button");
      await expect(chapterButtons).toHaveCount(2, { timeout: 30_000 });
      await expect(sceneButtons).toHaveCount(2);
      await chapterButtons.nth(1).click();
      await expect(sceneButtons).toHaveCount(1);
      await chapterButtons.nth(0).click();
      await firstPage
        .getByRole("button", { name: /Platform Glass/u })
        .click();

      await firstPage
        .getByLabel("Speaker")
        .first()
        .selectOption({ label: "Mira" });
      await firstPage
        .getByLabel("Correction reason")
        .first()
        .fill("packaged fixture correction");
      await firstPage
        .getByRole("button", { name: "Save correction" })
        .first()
        .click();
      await expect(
        firstPage.getByText("Speaker correction saved as human provenance.")
      ).toBeVisible({ timeout: 20_000 });

      await checkpointStage("shutdown_1");
      const firstExitProof = await closeOwnedElectron(
        first,
        firstOwnership
      );
      expect(firstExitProof.graceful).toBe(true);
      launchEvidence.push(
        machineLaunchEvidence(1, firstOwnership, firstExitProof)
      );
      first = null;
      firstOwnership = null;

      await checkpointStage("prelaunch_inventory_2");
      const beforeSecondLaunch = await queryRelevantProcesses();
      begunLaunches.add(2);
      await checkpointStage("launch_2");
      second = await launchPackaged(packaged.executablePath, {
        ...isolatedPaths
      });
      await checkpointStage("root_ownership_2");
      secondOwnership = await establishRootOwnership(
        second,
        beforeSecondLaunch,
        packaged
      );
      await checkpointStage("readiness_2");
      const secondPage = await second.firstWindow();
      await expect(
        secondPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 45_000 });
      await checkpointStage("service_ownership_2");
      secondOwnership = await expandOwnership(
        secondOwnership,
        true
      );
      ownedLaunches.add(2);
      await checkpointStage("restore_2");
      await expect(
        secondPage.getByRole("heading", {
          name: "Packaged Persistence Demo"
        })
      ).toBeVisible({ timeout: 20_000 });
      await secondPage
        .getByRole("button", { name: /Platform Glass/u })
        .click();
      await expect(
        secondPage.getByText("Human correction").first()
      ).toBeVisible({ timeout: 20_000 });
      await checkpointStage("screenshot");
      await secondPage.screenshot({
        path: evidencePath,
        fullPage: true,
        animations: "disabled"
      });
      const evidence = await lstat(evidencePath);
      expect(evidence.isFile()).toBe(true);
      expect(evidence.size).toBeGreaterThan(0);
      screenshotCaptured = true;

      await checkpointStage("shutdown_2");
      const secondExitProof = await closeOwnedElectron(
        second,
        secondOwnership
      );
      expect(secondExitProof.graceful).toBe(true);
      launchEvidence.push(
        machineLaunchEvidence(2, secondOwnership, secondExitProof)
      );
      second = null;
      secondOwnership = null;
    } catch (error) {
      operationError =
        error instanceof Error
          ? error
          : new Error("Packaged verification failed.");
      failureStage = currentStage;
      failureCode = packagedFailureCode(currentStage, error);
      try {
        await writeMachineResult("failed", false);
      } catch (checkpointError) {
        cleanupErrors.push(checkpointError);
      }
    } finally {
      for (const [application, ownership] of [
        [second, secondOwnership],
        [first, firstOwnership]
      ] as const) {
        try {
          await closeOwnedElectron(application, ownership);
        } catch (error) {
          cleanupErrors.push(error);
        }
      }
      if (isolationRoot !== null) {
        try {
          assertOwnedTemporaryRoot(isolationRoot);
          await rm(isolationRoot, {
            recursive: true,
            force: true,
            maxRetries: 40,
            retryDelay: 250
          });
        } catch (error) {
          cleanupErrors.push(error);
        }
      }
      const cleanupCompleted = cleanupErrors.length === 0;
      if (operationError === null && !cleanupCompleted) {
        failureStage = "cleanup";
        failureCode = "CLEANUP_FAILED";
      }
      try {
        await writeMachineResult(
          operationError === null && cleanupCompleted
            ? "passed"
            : "failed",
          cleanupCompleted
        );
      } catch (error) {
        cleanupErrors.push(error);
      }
    }
    if (operationError !== null) {
      if (cleanupErrors.length > 0) {
        throw new AggregateError(
          [operationError, ...cleanupErrors],
          "Packaged verification and cleanup failed."
        );
      }
      throw operationError;
    }
    if (cleanupErrors.length > 0) {
      throw new AggregateError(
        cleanupErrors,
        "Packaged verification cleanup did not complete."
      );
    }
  });
});

interface IsolatedPaths {
  readonly localAppData: string;
  readonly roamingAppData: string;
  readonly temporaryDirectory: string;
}

interface PackagedPaths extends PackagedProcessPaths {
  readonly version: string;
}

interface LaunchOwnership {
  readonly launcherPid: number;
  readonly rootPid: number;
  processes: readonly OwnedProcess[];
  readonly baseline: readonly ProcessIdentity[];
  readonly packaged: PackagedPaths;
}

interface ExitProof {
  readonly ownedPids: readonly number[];
  readonly graceful: boolean;
  readonly forcedPids: readonly number[];
  readonly remainingPids: readonly number[];
}

async function launchPackaged(
  executablePath: string,
  isolatedPaths: IsolatedPaths
): Promise<ElectronApplication> {
  const environment: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) {
      environment[key] = value;
    }
  }
  environment.LOCALAPPDATA = isolatedPaths.localAppData;
  environment.APPDATA = isolatedPaths.roamingAppData;
  environment.TEMP = isolatedPaths.temporaryDirectory;
  environment.TMP = isolatedPaths.temporaryDirectory;
  delete environment.CSS_DESKTOP_DEV_URL;
  delete environment.CSS_E2E_DATA_DIR;

  return electron.launch({
    executablePath,
    cwd: path.dirname(executablePath),
    env: environment,
    timeout: 45_000
  });
}

async function closeOwnedElectron(
  application: ElectronApplication | null,
  ownership: LaunchOwnership | null
): Promise<ExitProof> {
  if (application === null) {
    return {
      ownedPids: [],
      graceful: true,
      forcedPids: [],
      remainingPids: []
    };
  }
  const child = application.process();
  let verifiedOwnership = ownership;
  let ownershipError: Error | null = null;
  if (verifiedOwnership !== null) {
    try {
      verifiedOwnership = await expandOwnership(
        verifiedOwnership,
        false,
        monotonicNow() +
          defaultProcessInventoryPolicy.totalDeadlineMs
      );
    } catch (error) {
      ownershipError =
        error instanceof Error
          ? error
          : new Error("Packaged process ownership refresh failed.");
    }
  }
  const forcedPids: number[] = [];
  if (
    verifiedOwnership !== null &&
    (child.pid === undefined ||
      verifiedOwnership.launcherPid !== child.pid)
  ) {
    throw new Error("Packaged process ownership no longer matches its root.");
  }
  const outcome = await Promise.race([
    application.close().then(
      () => "closed" as const,
      () => "failed" as const
    ),
    delay(15_000).then(() => "timeout" as const)
  ]);
  if (
    outcome !== "closed" &&
    child.exitCode === null &&
    child.signalCode === null
  ) {
    child.kill();
    if (child.pid !== undefined) {
      forcedPids.push(child.pid);
    }
  }
  if (child.exitCode === null && child.signalCode === null) {
    await Promise.race([
      once(child, "exit").then(() => undefined),
      delay(5_000)
    ]);
  }
  if (child.exitCode === null && child.signalCode === null) {
    throw new Error("The packaged Electron process did not exit.");
  }

  if (ownership === null || verifiedOwnership === null) {
    throw new Error(
      "Packaged process ownership was not established; owned-process exit could not be verified."
    );
  }
  if (ownershipError !== null) {
    throw ownershipError;
  }
  const shutdownInventoryDeadline =
    monotonicNow() +
    defaultProcessInventoryPolicy.totalDeadlineMs;
  verifiedOwnership = await expandOwnership(
    verifiedOwnership,
    false,
    shutdownInventoryDeadline
  );
  const exitObservation = await waitForOwnedProcessesGone(
    verifiedOwnership,
    20_000,
    shutdownInventoryDeadline
  );
  verifiedOwnership = {
    ...verifiedOwnership,
    processes: exitObservation.processes
  };
  ownership.processes = verifiedOwnership.processes;
  const remaining = exitObservation.remaining;
  if (remaining.length > 0) {
    throw new Error(
      `Owned packaged processes did not exit: ${remaining
        .map((item) => item.pid)
        .join(", ")}.`
    );
  }
  if (outcome !== "closed" || forcedPids.length > 0) {
    throw new Error(
      "The packaged Electron launcher did not complete graceful shutdown."
    );
  }
  return {
    ownedPids: verifiedOwnership.processes.map((item) => item.pid),
    graceful: true,
    forcedPids: [],
    remainingPids: []
  };
}

async function establishRootOwnership(
  application: ElectronApplication,
  beforeLaunch: readonly ProcessIdentity[],
  packaged: PackagedPaths
): Promise<LaunchOwnership> {
  const launcherPid = application.process().pid;
  if (launcherPid === undefined) {
    throw new Error("Playwright did not expose the packaged launcher PID.");
  }
  const rootPid = await application.evaluate(() => process.pid);
  if (!Number.isSafeInteger(rootPid) || rootPid <= 0) {
    throw new Error("Electron did not expose a valid packaged root PID.");
  }
  const deadline =
    monotonicNow() +
    defaultProcessInventoryPolicy.totalDeadlineMs;
  while (monotonicNow() < deadline) {
    const current = await queryRelevantProcesses(deadline);
    const rootMatches = current.filter(
      (item) =>
        item.pid === rootPid &&
        item.parentPid === launcherPid &&
        item.name === appExecutableName &&
        matchesPackagedProcessPath(item, packaged) &&
        !containsProcessIdentity(beforeLaunch, item)
    );
    if (rootMatches.length > 1) {
      throw new Error("The packaged Electron root identity was ambiguous.");
    }
    const root = rootMatches[0];
    if (root !== undefined) {
      return {
        launcherPid,
        rootPid,
        processes: [{ ...root, kind: "app" }],
        baseline: [...beforeLaunch],
        packaged
      };
    }
    await delayWithinDeadline(100, deadline);
  }
  throw new Error("The packaged Electron root identity could not be proven.");
}

async function expandOwnership(
  ownership: LaunchOwnership,
  requireService: boolean,
  deadlineAt = monotonicNow() +
    defaultProcessInventoryPolicy.totalDeadlineMs
): Promise<LaunchOwnership> {
  const current = await queryRelevantProcesses(deadlineAt);
  const owned = adoptVerifiedProcessTree({
    current,
    baseline: ownership.baseline,
    owned: ownership.processes,
    rootPid: ownership.rootPid,
    packaged: ownership.packaged
  });
  if (owned.length === 0) {
    throw new Error("The packaged Electron root identity was lost.");
  }
  ownership.processes = owned;
  if (
    requireService &&
    (!owned.some((item) => item.kind === "service") ||
      !owned.some(
        (item) =>
          item.kind === "service" &&
          item.executablePath !== null &&
          samePath(
            item.executablePath,
            ownership.packaged.serviceExecutablePath
          )
      ))
  ) {
    throw new Error("The owned packaged service process was not identified.");
  }
  return ownership;
}

async function requirePackagedExecutable(
  value: string
): Promise<PackagedPaths> {
  const candidate = requireAbsolutePath(value, packagedExecutableEnvironment);
  const packageRecord = parseRecord(
    JSON.parse(
      await readFile(path.join(desktopRoot, "package.json"), "utf8")
    ) as unknown,
    "desktop package"
  );
  if (
    typeof packageRecord.version !== "string" ||
    !/^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$/u.test(
      packageRecord.version
    )
  ) {
    throw new Error("The desktop package version is invalid.");
  }
  const version = packageRecord.version;
  const unpackedDirectory = await realpath(
    path.join(desktopRoot, "release", version, "win-unpacked")
  );
  const expectedExecutable = path.join(
    unpackedDirectory,
    appExecutableName
  );
  if (!samePath(await realpath(candidate), expectedExecutable)) {
    throw new Error(
      `${packagedExecutableEnvironment} must be the exact release/${version}/win-unpacked application executable.`
    );
  }
  const serviceExecutablePath = path.join(
    unpackedDirectory,
    "resources",
    "service",
    serviceExecutableName
  );
  for (const executable of [candidate, serviceExecutablePath]) {
    const metadata = await lstat(executable);
    if (!metadata.isFile() || metadata.isSymbolicLink()) {
      throw new Error("A packaged executable was not a regular file.");
    }
  }
  return {
    executablePath: await realpath(candidate),
    serviceExecutablePath: await realpath(serviceExecutablePath),
    version
  };
}

async function queryRelevantProcesses(
  deadlineAt?: number
): Promise<readonly ProcessIdentity[]> {
  return processInventory.query({ deadlineAt });
}

async function waitForOwnedProcessesGone(
  ownership: LaunchOwnership,
  timeoutMs: number,
  callerDeadline: number
): Promise<{
  readonly processes: readonly OwnedProcess[];
  readonly remaining: readonly OwnedProcess[];
}> {
  const deadline = Math.min(
    callerDeadline,
    monotonicNow() + timeoutMs
  );
  let processes = ownership.processes;
  let remaining: readonly OwnedProcess[] = processes;
  let consecutiveAbsenceObservations = 0;
  while (monotonicNow() < deadline) {
    const current = await queryRelevantProcesses(deadline);
    processes = adoptVerifiedProcessTree({
      current,
      baseline: ownership.baseline,
      owned: processes,
      rootPid: ownership.rootPid,
      packaged: ownership.packaged
    });
    ownership.processes = processes;
    remaining = remainingOwnedProcesses(current, processes);
    if (remaining.length === 0) {
      consecutiveAbsenceObservations += 1;
      if (consecutiveAbsenceObservations >= 2) {
        return { processes, remaining: [] };
      }
    } else {
      consecutiveAbsenceObservations = 0;
    }
    await delayWithinDeadline(200, deadline);
  }
  if (remaining.length === 0) {
    throw new Error(
      "Owned packaged process absence was not confirmed by two inventories."
    );
  }
  return { processes, remaining };
}

function samePath(left: string, right: string): boolean {
  return (
    path.win32.resolve(left).toLowerCase() ===
    path.win32.resolve(right).toLowerCase()
  );
}

function evidenceOwnership(ownership: LaunchOwnership) {
  return {
    launcherPid: ownership.launcherPid,
    rootPid: ownership.rootPid,
    processes: ownership.processes.map((item) => ({
      pid: item.pid,
      parentPid: item.parentPid,
      kind: item.kind,
      executableName: item.name,
      creationDate: item.creationDate
    }))
  };
}

function machineLaunchEvidence(
  launch: 1 | 2,
  ownership: LaunchOwnership,
  exitProof: ExitProof
): MachineLaunchEvidence {
  return {
    launch,
    preexistingRelevantProcesses: ownership.baseline
      .map(redactPreexistingProcess)
      .sort(compareEvidenceProcess),
    ownership: evidenceOwnership(ownership),
    exitProof
  };
}

function redactPreexistingProcess(item: ProcessIdentity) {
  return {
    pid: item.pid,
    name: item.name,
    creationDate: item.creationDate
  };
}

function compareEvidenceProcess(
  left: ReturnType<typeof redactPreexistingProcess>,
  right: ReturnType<typeof redactPreexistingProcess>
): number {
  return left.pid - right.pid;
}

function parseRecord(
  value: unknown,
  field: string
): Record<string, unknown> {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    throw new Error(`${field} was invalid.`);
  }
  return value as Record<string, unknown>;
}

function requireEvidencePath(
  value: string,
  packaged: PackagedPaths
): string {
  const candidate = requireAbsolutePath(value, evidencePathEnvironment);
  const expected = path.join(
    path.dirname(path.dirname(packaged.executablePath)),
    "packaged-e2e.png"
  );
  if (!samePath(candidate, expected)) {
    throw new Error(
      `${evidencePathEnvironment} must be the exact release evidence path.`
    );
  }
  return candidate;
}

function requireResultPath(
  value: string,
  packaged: PackagedPaths
): string {
  const candidate = requireAbsolutePath(value, resultPathEnvironment);
  const expected = path.join(
    path.dirname(path.dirname(packaged.executablePath)),
    "packaged-e2e-result.json"
  );
  if (!samePath(candidate, expected)) {
    throw new Error(
      `${resultPathEnvironment} must be the exact release result path.`
    );
  }
  return candidate;
}

function requireAbsolutePath(value: string, environmentName: string): string {
  if (
    value.length === 0 ||
    value.length > 2_048 ||
    value.includes("\0") ||
    !path.isAbsolute(value)
  ) {
    throw new Error(`${environmentName} must be a bounded absolute path.`);
  }
  return path.resolve(value);
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.trim().length === 0) {
    throw new Error(`${name} is required.`);
  }
  return value.trim();
}

function hasEnvironmentValue(name: string): boolean {
  return (
    process.env[name] !== undefined &&
    process.env[name]?.trim().length !== 0
  );
}

function assertOwnedTemporaryRoot(value: string): void {
  const resolvedRoot = path.resolve(value);
  const resolvedTemporaryDirectory = path.resolve(tmpdir());
  const relative = path.relative(resolvedTemporaryDirectory, resolvedRoot);
  if (
    relative.length === 0 ||
    relative.startsWith(`..${path.sep}`) ||
    relative === ".." ||
    path.isAbsolute(relative) ||
    !path.basename(resolvedRoot).startsWith("css-packaged-e2e-")
  ) {
    throw new Error("Refusing to remove an unowned packaged-test directory.");
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

async function delayWithinDeadline(
  milliseconds: number,
  deadlineAt: number
): Promise<void> {
  const remaining = deadlineAt - monotonicNow();
  if (remaining > 0) {
    await delay(Math.min(milliseconds, remaining));
  }
}
