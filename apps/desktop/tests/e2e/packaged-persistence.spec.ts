import { spawn } from "node:child_process";
import { once } from "node:events";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  writeFile
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  _electron as electron,
  expect,
  test,
  type ElectronApplication
} from "@playwright/test";

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
const appExecutableName = "Cinematic Story Studio.exe";
const serviceExecutableName = "cinematic-story-service.exe";
const relevantProcessNames = new Set([
  appExecutableName,
  serviceExecutableName
]);

test.describe("packaged desktop verification", () => {
  test.skip(
    !hasEnvironmentValue(packagedExecutableEnvironment) ||
      !hasEnvironmentValue(evidencePathEnvironment) ||
      !hasEnvironmentValue(resultPathEnvironment),
    `Set ${packagedExecutableEnvironment}, ${evidencePathEnvironment}, and ${resultPathEnvironment} to run the packaged gate.`
  );

  test("runs the synthetic persistence flow in the packaged application", async () => {
    test.setTimeout(180_000);
    const packaged = await requirePackagedExecutable(
      requiredEnvironment(packagedExecutableEnvironment)
    );
    const evidencePath = requireEvidencePath(
      requiredEnvironment(evidencePathEnvironment)
    );
    const resultPath = requireResultPath(
      requiredEnvironment(resultPathEnvironment)
    );
    const preexistingProcesses = await queryRelevantProcesses();
    const isolationRoot = await mkdtemp(
      path.join(tmpdir(), "css-packaged-e2e-")
    );
    const localAppData = path.join(isolationRoot, "LocalAppData");
    const roamingAppData = path.join(isolationRoot, "AppData");
    const temporaryDirectory = path.join(isolationRoot, "Temp");
    await Promise.all([
      mkdir(localAppData, { recursive: true }),
      mkdir(roamingAppData, { recursive: true }),
      mkdir(temporaryDirectory, { recursive: true }),
      mkdir(path.dirname(evidencePath), { recursive: true }),
      mkdir(path.dirname(resultPath), { recursive: true })
    ]);

    let first: ElectronApplication | null = null;
    let second: ElectronApplication | null = null;
    let firstOwnership: LaunchOwnership | null = null;
    let secondOwnership: LaunchOwnership | null = null;
    let operationError: Error | null = null;
    let screenshotCaptured = false;
    const cleanupErrors: unknown[] = [];
    const launchEvidence: LaunchEvidence[] = [];
    try {
      first = await launchPackaged(packaged.executablePath, {
        localAppData,
        roamingAppData,
        temporaryDirectory
      });
      firstOwnership = await establishRootOwnership(
        first,
        preexistingProcesses,
        packaged
      );
      const firstPage = await first.firstWindow();
      await expect(
        firstPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 45_000 });
      firstOwnership = await expandOwnership(
        firstOwnership,
        true
      );
      await firstPage
        .getByLabel("New production")
        .fill("Packaged Persistence Demo");
      await firstPage.getByRole("button", { name: "Create project" }).click();
      await expect(
        firstPage.getByRole("heading", {
          name: "Packaged Persistence Demo"
        })
      ).toBeVisible();

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

      const firstExitProof = await closeOwnedElectron(
        first,
        firstOwnership
      );
      launchEvidence.push(
        machineLaunchEvidence(1, firstOwnership, firstExitProof)
      );
      expect(firstExitProof.graceful).toBe(true);
      first = null;
      firstOwnership = null;

      const beforeSecondLaunch = await queryRelevantProcesses();
      second = await launchPackaged(packaged.executablePath, {
        localAppData,
        roamingAppData,
        temporaryDirectory
      });
      secondOwnership = await establishRootOwnership(
        second,
        beforeSecondLaunch,
        packaged
      );
      const secondPage = await second.firstWindow();
      await expect(
        secondPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 45_000 });
      secondOwnership = await expandOwnership(
        secondOwnership,
        true
      );
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
      await secondPage.screenshot({
        path: evidencePath,
        fullPage: true,
        animations: "disabled"
      });
      const evidence = await lstat(evidencePath);
      expect(evidence.isFile()).toBe(true);
      expect(evidence.size).toBeGreaterThan(0);
      screenshotCaptured = true;

      const secondExitProof = await closeOwnedElectron(
        second,
        secondOwnership
      );
      launchEvidence.push(
        machineLaunchEvidence(2, secondOwnership, secondExitProof)
      );
      expect(secondExitProof.graceful).toBe(true);
      second = null;
      secondOwnership = null;
    } catch (error) {
      operationError =
        error instanceof Error
          ? error
          : new Error("Packaged verification failed.");
    } finally {
      for (const [launch, application, ownership] of [
        [2, second, secondOwnership],
        [1, first, firstOwnership]
      ] as const) {
        try {
          const exitProof = await closeOwnedElectron(
            application,
            ownership
          );
          if (
            ownership !== null &&
            !launchEvidence.some((item) => item.launch === launch)
          ) {
            launchEvidence.push(
              machineLaunchEvidence(launch, ownership, exitProof)
            );
          }
        } catch (error) {
          cleanupErrors.push(error);
        }
      }
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
    try {
      await writeMachineEvidence(resultPath, {
        schemaVersion: "1.0.0",
        completedAt: new Date().toISOString(),
        status:
          operationError === null && cleanupErrors.length === 0
            ? "passed"
            : "failed",
        packagedVersion: packaged.version,
        executable: `release/${packaged.version}/win-unpacked/${appExecutableName}`,
        fixture: "fixtures/synthetic-story/sample-story.md",
        isolationEnvironment: ["APPDATA", "LOCALAPPDATA", "TEMP", "TMP"],
        preexistingRelevantProcesses: preexistingProcesses
          .map(redactPreexistingProcess)
          .sort(compareEvidenceProcess),
        flow: [
          "create",
          "import_synthetic_fixture",
          "analyze",
          "correct_speaker",
          "close",
          "restart",
          "restore",
          "close"
        ],
        screenshot: {
          artifactId: "packaged-ui-screenshot",
          captured: screenshotCaptured
        },
        launches: [...launchEvidence].sort(
          (left, right) => left.launch - right.launch
        ),
        error:
          operationError === null && cleanupErrors.length === 0
            ? null
            : safeEvidenceError(operationError, cleanupErrors)
      });
    } catch (error) {
      cleanupErrors.push(error);
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

interface PackagedPaths {
  readonly executablePath: string;
  readonly serviceExecutablePath: string;
  readonly version: string;
}

interface ProcessIdentity {
  readonly pid: number;
  readonly parentPid: number;
  readonly name: string;
  readonly executablePath: string | null;
  readonly creationDate: string;
}

interface OwnedProcess extends ProcessIdentity {
  readonly kind: "app" | "service";
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

interface LaunchEvidence {
  readonly launch: number;
  readonly preexistingRelevantProcesses: readonly ReturnType<
    typeof redactPreexistingProcess
  >[];
  readonly ownership: ReturnType<typeof evidenceOwnership>;
  readonly exitProof: ExitProof;
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
        false
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

  if (verifiedOwnership === null) {
    throw new Error(
      "Packaged process ownership was not established; descendant termination was not attempted."
    );
  }
  if (ownershipError !== null) {
    throw ownershipError;
  }
  verifiedOwnership = await expandOwnership(verifiedOwnership, false);
  let remaining = await waitForOwnedProcessesGone(
    verifiedOwnership.processes,
    10_000
  );
  if (remaining.length > 0) {
    for (const owned of remaining) {
      if (await terminateExactOwnedProcess(owned)) {
        forcedPids.push(owned.pid);
      }
    }
    remaining = await waitForOwnedProcessesGone(
      verifiedOwnership.processes,
      5_000
    );
  }
  if (remaining.length > 0) {
    throw new Error(
      `Owned packaged processes did not exit: ${remaining
        .map((item) => item.pid)
        .join(", ")}.`
    );
  }
  return {
    ownedPids: verifiedOwnership.processes.map((item) => item.pid),
    graceful: outcome === "closed" && forcedPids.length === 0,
    forcedPids: [...new Set(forcedPids)].sort(
      (left, right) => left - right
    ),
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
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const current = await queryRelevantProcesses();
    const rootMatches = current.filter(
      (item) =>
        item.pid === rootPid &&
        item.executablePath !== null &&
        samePath(item.executablePath, packaged.executablePath) &&
        !containsIdentity(beforeLaunch, item)
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
    await delay(100);
  }
  throw new Error("The packaged Electron root identity could not be proven.");
}

async function expandOwnership(
  ownership: LaunchOwnership,
  requireService: boolean
): Promise<LaunchOwnership> {
  const current = await queryRelevantProcesses();
  const root = ownership.processes.find(
    (item) => item.pid === ownership.rootPid && item.kind === "app"
  );
  if (root === undefined) {
    throw new Error("The packaged Electron root identity was lost.");
  }
  const rootIsCurrent = containsIdentity(current, root);
  const byPid = new Map(current.map((item) => [item.pid, item]));
  const adopted = rootIsCurrent
    ? current
        .filter(
          (item) =>
            item.executablePath !== null &&
            item.creationDate >= root.creationDate &&
            matchesPackagedProcessPath(item, ownership.packaged) &&
            (containsIdentity(ownership.processes, item) ||
              isDescendantOf(
                item,
                root,
                byPid,
                ownership.packaged
              )) &&
            !containsIdentity(ownership.baseline, item)
        )
        .map<OwnedProcess>((item) => ({
          ...item,
          kind:
            item.name === serviceExecutableName ? "service" : "app"
        }))
    : [];
  const owned = [...ownership.processes];
  for (const item of adopted) {
    if (!containsIdentity(owned, item)) {
      owned.push(item);
    }
  }
  owned.sort((left, right) => left.pid - right.pid);
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
  ownership.processes = owned;
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

async function queryRelevantProcesses(): Promise<readonly ProcessIdentity[]> {
  if (process.platform !== "win32") {
    throw new Error("Packaged process ownership verification requires Windows.");
  }
  const script = [
    "$ErrorActionPreference = 'Stop'",
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
    "$records = foreach ($processName in @('Cinematic Story Studio.exe', 'cinematic-story-service.exe')) {",
    "  Get-CimInstance Win32_Process -Filter (\"Name = '\" + $processName + \"'\") | ForEach-Object {",
    "    [PSCustomObject]@{",
    "      pid = [int]$_.ProcessId",
    "      parentPid = [int]$_.ParentProcessId",
    "      name = [string]$_.Name",
    "      executablePath = if ($null -eq $_.ExecutablePath) { $null } else { [string]$_.ExecutablePath }",
    "      creationDate = $_.CreationDate.ToUniversalTime().ToString('O', [Globalization.CultureInfo]::InvariantCulture)",
    "    }",
    "  }",
    "}",
    "[Console]::Out.Write((ConvertTo-Json -InputObject @($records) -Compress))"
  ].join("\n");
  const output = await runBoundedProcess(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
    10_000,
    1024 * 1024
  );
  const parsed = JSON.parse(output.length === 0 ? "[]" : output) as unknown;
  if (!Array.isArray(parsed) || parsed.length > 256) {
    throw new Error("Relevant packaged process inventory was invalid.");
  }
  return parsed.map(parseProcessIdentity);
}

async function waitForOwnedProcessesGone(
  owned: readonly OwnedProcess[],
  timeoutMs: number
): Promise<readonly OwnedProcess[]> {
  const deadline = Date.now() + timeoutMs;
  let remaining: readonly OwnedProcess[] = owned;
  while (Date.now() < deadline) {
    const current = await queryRelevantProcesses();
    remaining = owned.filter((item) => containsIdentity(current, item));
    if (remaining.length === 0) {
      return [];
    }
    await delay(200);
  }
  return remaining;
}

async function terminateExactOwnedProcess(
  owned: OwnedProcess
): Promise<boolean> {
  const current = await queryRelevantProcesses();
  if (!containsIdentity(current, owned)) {
    return false;
  }
  try {
    await runBoundedProcess(
      "taskkill.exe",
      ["/PID", String(owned.pid), "/F"],
      5_000,
      64 * 1024
    );
    return true;
  } catch (error) {
    const after = await queryRelevantProcesses();
    if (!containsIdentity(after, owned)) {
      return false;
    }
    throw error;
  }
}

async function runBoundedProcess(
  command: string,
  arguments_: readonly string[],
  timeoutMs: number,
  maximumOutputBytes: number
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const child = spawn(command, [...arguments_], {
      shell: false,
      stdio: ["ignore", "pipe", "ignore"],
      windowsHide: true
    });
    const chunks: Buffer[] = [];
    let total = 0;
    let settled = false;
    const finish = (error: Error | null, value = "") => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      if (error === null) {
        resolve(value);
      } else {
        reject(error);
      }
    };
    const timeout = setTimeout(() => {
      child.kill();
      finish(new Error("A packaged ownership command timed out."));
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      total += chunk.byteLength;
      if (total > maximumOutputBytes) {
        child.kill();
        finish(new Error("Packaged ownership output exceeded its limit."));
        return;
      }
      chunks.push(chunk);
    });
    child.once("error", () => {
      finish(new Error("A packaged ownership command could not start."));
    });
    child.once("exit", (code) => {
      if (code !== 0) {
        finish(new Error("A packaged ownership command failed."));
        return;
      }
      finish(null, Buffer.concat(chunks, total).toString("utf8").trim());
    });
  });
}

function parseProcessIdentity(value: unknown): ProcessIdentity {
  const record = parseRecord(value, "process identity");
  if (
    !Number.isSafeInteger(record.pid) ||
    (record.pid as number) <= 0 ||
    !Number.isSafeInteger(record.parentPid) ||
    (record.parentPid as number) < 0 ||
    typeof record.name !== "string" ||
    !relevantProcessNames.has(record.name) ||
    (record.executablePath !== null &&
      (typeof record.executablePath !== "string" ||
        !path.isAbsolute(record.executablePath))) ||
    typeof record.creationDate !== "string" ||
    record.creationDate.length === 0 ||
    record.creationDate.length > 128
  ) {
    throw new Error("A relevant packaged process identity was invalid.");
  }
  return {
    pid: record.pid as number,
    parentPid: record.parentPid as number,
    name: record.name,
    executablePath: record.executablePath,
    creationDate: record.creationDate
  };
}

function isDescendantOf(
  candidate: ProcessIdentity,
  root: ProcessIdentity,
  byPid: ReadonlyMap<number, ProcessIdentity>,
  packaged: PackagedPaths
): boolean {
  let child = candidate;
  const visited = new Set<number>();
  while (child.parentPid > 0 && !visited.has(child.parentPid)) {
    const parent = byPid.get(child.parentPid);
    if (
      parent === undefined ||
      parent.executablePath === null ||
      parent.creationDate > child.creationDate ||
      !matchesPackagedProcessPath(parent, packaged)
    ) {
      return false;
    }
    if (sameIdentity(parent, root)) {
      return true;
    }
    visited.add(child.parentPid);
    child = parent;
  }
  return false;
}

function matchesPackagedProcessPath(
  candidate: ProcessIdentity,
  packaged: PackagedPaths
): boolean {
  if (candidate.executablePath === null) {
    return false;
  }
  return candidate.name === appExecutableName
    ? samePath(candidate.executablePath, packaged.executablePath)
    : candidate.name === serviceExecutableName &&
        samePath(
          candidate.executablePath,
          packaged.serviceExecutablePath
        );
}

function containsIdentity(
  values: readonly ProcessIdentity[],
  expected: ProcessIdentity
): boolean {
  return values.some((value) => sameIdentity(value, expected));
}

function sameIdentity(
  left: ProcessIdentity,
  right: ProcessIdentity
): boolean {
  return (
    left.pid === right.pid &&
    left.creationDate === right.creationDate &&
    left.executablePath !== null &&
    right.executablePath !== null &&
    samePath(left.executablePath, right.executablePath)
  );
}

function samePath(left: string, right: string): boolean {
  return (
    path.resolve(left).toLocaleLowerCase() ===
    path.resolve(right).toLocaleLowerCase()
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
  launch: number,
  ownership: LaunchOwnership,
  exitProof: ExitProof
): LaunchEvidence {
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

function safeEvidenceError(
  operationError: Error | null,
  cleanupErrors: readonly unknown[]
) {
  const selected =
    operationError ??
    cleanupErrors.find((item): item is Error => item instanceof Error);
  return {
    name: selected?.name ?? "Error",
    message:
      operationError === null
        ? "Packaged verification cleanup failed."
        : "Packaged verification failed."
  };
}

async function writeMachineEvidence(
  resultPath: string,
  value: unknown
): Promise<void> {
  await writeFile(resultPath, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600
  });
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

function requireEvidencePath(value: string): string {
  const candidate = requireAbsolutePath(value, evidencePathEnvironment);
  if (path.extname(candidate).toLocaleLowerCase() !== ".png") {
    throw new Error(`${evidencePathEnvironment} must end in .png.`);
  }
  return candidate;
}

function requireResultPath(value: string): string {
  const candidate = requireAbsolutePath(value, resultPathEnvironment);
  if (path.extname(candidate).toLocaleLowerCase() !== ".json") {
    throw new Error(`${resultPathEnvironment} must end in .json.`);
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
