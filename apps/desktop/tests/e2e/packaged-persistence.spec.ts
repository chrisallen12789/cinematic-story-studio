import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import {
  lstat,
  mkdir,
  mkdtemp,
  realpath,
  rm
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

test.describe("packaged desktop verification", () => {
  test.skip(
    !hasEnvironmentValue(packagedExecutableEnvironment) ||
      !hasEnvironmentValue(evidencePathEnvironment),
    `Set ${packagedExecutableEnvironment} and ${evidencePathEnvironment} to run the packaged gate.`
  );

  test("runs the synthetic persistence flow in the packaged application", async () => {
    test.setTimeout(180_000);
    const executablePath = await requirePackagedExecutable(
      requiredEnvironment(packagedExecutableEnvironment)
    );
    const evidencePath = requireEvidencePath(
      requiredEnvironment(evidencePathEnvironment)
    );
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
      mkdir(path.dirname(evidencePath), { recursive: true })
    ]);

    let first: ElectronApplication | null = null;
    let second: ElectronApplication | null = null;
    let operationError: Error | null = null;
    const cleanupErrors: unknown[] = [];
    try {
      first = await launchPackaged(executablePath, {
        localAppData,
        roamingAppData,
        temporaryDirectory
      });
      const firstPage = await first.firstWindow();
      await expect(
        firstPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 45_000 });
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

      await closeOwnedElectron(first);
      first = null;

      second = await launchPackaged(executablePath, {
        localAppData,
        roamingAppData,
        temporaryDirectory
      });
      const secondPage = await second.firstWindow();
      await expect(
        secondPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 45_000 });
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

      await closeOwnedElectron(second);
      second = null;
    } catch (error) {
      operationError =
        error instanceof Error
          ? error
          : new Error("Packaged verification failed.");
    } finally {
      for (const application of [second, first]) {
        try {
          await closeOwnedElectron(application);
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
  application: ElectronApplication | null
): Promise<void> {
  if (application === null) {
    return;
  }
  const child = application.process();
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
    await terminateOwnedProcessTree(child);
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
}

async function terminateOwnedProcessTree(child: ChildProcess): Promise<void> {
  if (process.platform === "win32" && child.pid !== undefined) {
    const terminator = spawn(
      "taskkill.exe",
      ["/PID", String(child.pid), "/T", "/F"],
      {
        shell: false,
        stdio: "ignore",
        windowsHide: true
      }
    );
    await Promise.race([
      once(terminator, "exit").then(() => undefined),
      delay(5_000)
    ]);
    return;
  }
  child.kill();
}

async function requirePackagedExecutable(value: string): Promise<string> {
  const candidate = requireAbsolutePath(value, packagedExecutableEnvironment);
  if (path.extname(candidate).toLocaleLowerCase() !== ".exe") {
    throw new Error(
      `${packagedExecutableEnvironment} must name the packaged application executable.`
    );
  }
  const metadata = await lstat(candidate);
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new Error(
      `${packagedExecutableEnvironment} must name a regular, non-symlink file.`
    );
  }
  return realpath(candidate);
}

function requireEvidencePath(value: string): string {
  const candidate = requireAbsolutePath(value, evidencePathEnvironment);
  if (path.extname(candidate).toLocaleLowerCase() !== ".png") {
    throw new Error(`${evidencePathEnvironment} must end in .png.`);
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
