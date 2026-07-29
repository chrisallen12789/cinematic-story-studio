import { mkdtemp, rm } from "node:fs/promises";
import { once } from "node:events";
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

test.describe("desktop persistence", () => {
  test.skip(
    process.env.CSS_E2E !== "1",
    "Set CSS_E2E=1 after building the desktop and local-service venv."
  );

  test("restores the most recent project after a real service restart", async () => {
    const dataDirectory = await mkdtemp(
      path.join(tmpdir(), "css-desktop-e2e-")
    );
    let first: ElectronApplication | null = null;
    let second: ElectronApplication | null = null;
    try {
      first = await launch(dataDirectory);
      const firstPage = await first.firstWindow();
      await expect(
        firstPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 30_000 });
      await firstPage.getByLabel("New production").fill("Persistence Demo");
      await firstPage.getByRole("button", { name: "Create project" }).click();
      await expect(
        firstPage.getByRole("heading", { name: "Persistence Demo" })
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
      ).toBeVisible({ timeout: 15_000 });
      await firstPage
        .getByRole("button", { name: "Dismiss notification" })
        .click();
      await firstPage.getByRole("button", { name: "Analyze story" }).click();
      await expect
        .poll(
          async () => firstPage.locator(".job-state").allTextContents(),
          { timeout: 30_000 }
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

      const firstSpeaker = firstPage.getByLabel("Speaker").first();
      await firstSpeaker.selectOption({ label: "Mira" });
      await firstPage
        .getByLabel("Correction reason")
        .first()
        .fill("fixture correction");
      await firstPage
        .getByRole("button", { name: "Save correction" })
        .first()
        .click();
      await expect(
        firstPage.getByText("Speaker correction saved as human provenance.")
      ).toBeVisible({ timeout: 15_000 });
      await first.close();
      first = null;

      second = await launch(dataDirectory);
      const secondPage = await second.firstWindow();
      await expect(
        secondPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        secondPage.getByRole("heading", { name: "Persistence Demo" })
      ).toBeVisible({ timeout: 15_000 });
      await secondPage
        .getByRole("button", { name: /Platform Glass/u })
        .click();
      await expect(
        secondPage.getByText("Human correction").first()
      ).toBeVisible({ timeout: 15_000 });
      await second.close();
      second = null;
    } finally {
      await closeElectron(second);
      await closeElectron(first);
      await rm(dataDirectory, {
        recursive: true,
        force: true,
        maxRetries: 20,
        retryDelay: 250
      });
    }
  });
});

async function closeElectron(
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
    delay(12_000).then(() => "timeout" as const)
  ]);
  if (
    outcome !== "closed" &&
    child.exitCode === null &&
    child.signalCode === null
  ) {
    child.kill();
    await Promise.race([once(child, "exit"), delay(3_000)]);
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

async function launch(dataDirectory: string) {
  return electron.launch({
    args: [desktopRoot],
    cwd: desktopRoot,
    env: {
      ...process.env,
      CSS_E2E_DATA_DIR: dataDirectory
    }
  });
}
