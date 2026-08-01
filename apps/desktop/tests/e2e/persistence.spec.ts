import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { once } from "node:events";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  _electron as electron,
  expect,
  test,
  type ElectronApplication,
  type Locator,
  type Page
} from "@playwright/test";
import type { SpeechRuntimeInstance } from "@cinematic-story-studio/contracts";

import { materializeStrictBase64Docx } from "../../src/verification/packaged-e2e-evidence";
import {
  buildPackagedStoryAnalysisEvidence,
  expectPhase2RestartPersistence,
  readPhase2RuntimeSnapshot,
  runPhase2GovernanceWorkflow
} from "./phase2-story-analysis";
import {
  expectPhase3RestartPersistence,
  readPhase3RuntimeSnapshot,
  runPhase3GovernanceWorkflow
} from "./phase3-voice-casting";
import {
  provePhase3bRestartPersistence,
  runPhase3bGovernanceWorkflow
} from "./phase3b-local-speech-auditions";
import {
  observeOwnedProcessNetworkEndpoints,
  queryExactProcessIdentities
} from "../../src/verification/owned-process-network-observation";
import type {
  OwnedProcess,
  ProcessIdentity
} from "../../src/verification/packaged-process-inventory";

const desktopRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const encodedFixturePath = path.resolve(
  desktopRoot,
  "../../fixtures/synthetic-story/sample-story.docx.base64"
);
const correctionReason = "fixture correction";

test.describe("desktop persistence", () => {
  test.skip(
    process.env.CSS_E2E !== "1",
    "Set CSS_E2E=1 after building the desktop and local-service venv."
  );

  test("restores the most recent project after a real service restart", async () => {
    test.setTimeout(900_000);
    const isolationRoot = await mkdtemp(
      path.join(tmpdir(), "css-desktop-e2e-")
    );
    const dataDirectory = path.join(isolationRoot, "Data");
    const fixtureDirectory = path.join(isolationRoot, "Fixture");
    const fixturePath = path.join(fixtureDirectory, "sample-story.docx");
    let fixtureSha256: string;
    let first: ElectronApplication | null = null;
    let second: ElectronApplication | null = null;
    try {
      await Promise.all([
        mkdir(dataDirectory),
        mkdir(fixtureDirectory)
      ]);
      fixtureSha256 = await materializeStrictBase64Docx(
        encodedFixturePath,
        isolationRoot,
        fixturePath
      );
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
      const createdNotice = firstPage.getByText(
        "Created Persistence Demo.",
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
        .getByRole("button", { name: "Import document" })
        .click();
      await expect(
        firstPage.getByText(
          "Queued secure extraction for sample-story.docx."
        )
      ).toBeVisible({ timeout: 20_000 });
      await expect(
        firstPage.getByRole("button", { name: "Analyze story" })
      ).toBeDisabled();
      await firstPage
        .getByRole("button", { name: "Dismiss notification" })
        .click();
      await waitForJobState(
        firstPage,
        "Extract document progress",
        "Succeeded",
        45_000
      );
      const firstReviewCard = firstPage.locator(".import-review-card");
      await expect(
        firstReviewCard.getByRole("heading", {
          name: "sample-story.docx"
        })
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        reviewEvidence(firstReviewCard, "Declared format")
      ).toHaveText("DOCX");
      await expect(
        reviewEvidence(firstReviewCard, "Detected format")
      ).toHaveText("DOCX");
      await expect(
        reviewEvidence(firstReviewCard, "Media type")
      ).toHaveText(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      );
      await expect(
        reviewEvidence(firstReviewCard, "Preservation")
      ).toHaveText("Exact original bytes preserved");
      await expect(
        reviewEvidence(firstReviewCard, "Extraction status")
      ).toHaveText("Complete");
      await expect(
        reviewEvidence(firstReviewCard, "Intervention required")
      ).toHaveText("Yes");
      await expect(
        reviewEvidence(firstReviewCard, "Analysis may continue")
      ).toHaveText("No");
      const pendingSnapshot = await readPersistedImportSnapshot(firstPage);
      expect(pendingSnapshot.sourceSha256).toBe(fixtureSha256);
      expect(pendingSnapshot.extractedTextSha256).toMatch(
        /^[a-f0-9]{64}$/u
      );
      expect(pendingSnapshot.extractionRevision).toBeGreaterThan(0);
      expect(pendingSnapshot.reviewState).toBe("pending");
      expect(pendingSnapshot.analysisAllowed).toBe(false);
      if (pendingSnapshot.warningCount === 0) {
        await expect(
          firstReviewCard.getByText("No extraction warnings", {
            exact: true
          })
        ).toBeVisible();
      } else {
        await expect(
          firstReviewCard.locator(".import-warnings li")
        ).toHaveCount(pendingSnapshot.warningCount);
      }
      await firstPage
        .getByLabel("Review rationale (optional for approval)")
        .clear();
      await firstPage
        .getByRole("button", { name: "Approve import" })
        .click();
      const approvalNotice = firstPage.getByText(
        "Import approved. Story analysis is now available.",
        { exact: true }
      );
      await expect(approvalNotice).toBeVisible({ timeout: 20_000 });
      await expect(
        firstPage.getByRole("button", { name: "Analyze story" })
      ).toBeEnabled();
      await expect(
        firstPage.locator(".import-review-card .review-state")
      ).toHaveText("Approved");
      await firstPage
        .getByRole("button", { name: "Dismiss notification" })
        .click();
      await expect(approvalNotice).toBeHidden();

      await firstPage.getByRole("button", { name: "Analyze story" }).click();
      await waitForJobState(
        firstPage,
        "Analyze story progress",
        "Succeeded",
        45_000
      );
      const analysisNotice = firstPage.getByText("Story analysis queued.", {
        exact: true
      });
      if (await analysisNotice.isVisible()) {
        await firstPage
          .getByRole("button", { name: "Dismiss notification" })
          .click();
      }
      const chapterButtons = firstPage
        .getByRole("navigation", { name: "Chapters" })
        .getByRole("button");
      const sceneButtons = firstPage
        .getByRole("navigation", { name: "Scenes" })
        .getByRole("button");
      await expect(chapterButtons.first()).toBeVisible({
        timeout: 30_000
      });
      await expect(sceneButtons.first()).toBeVisible();
      await sceneButtons.first().click();

      const firstSpeaker = firstPage.getByLabel("Speaker").first();
      await firstSpeaker.selectOption({ label: "Mira" });
      await firstPage
        .getByLabel("Correction reason")
        .first()
        .fill(correctionReason);
      await firstPage
        .getByRole("button", { name: "Save correction" })
        .first()
        .click();
      await expect(
        firstPage.getByText("Speaker correction saved as human provenance.")
      ).toBeVisible({ timeout: 15_000 });
      await expect(
        firstPage.getByText("Human correction").first()
      ).toBeVisible({ timeout: 15_000 });
      const firstSnapshot = await readPersistedImportSnapshot(
        firstPage,
        correctionReason
      );
      expect(firstSnapshot).toMatchObject({
        format: "docx",
        sourceSha256: fixtureSha256,
        extractedTextSha256: pendingSnapshot.extractedTextSha256,
        extractionRevision: pendingSnapshot.extractionRevision,
        warningCount: pendingSnapshot.warningCount,
        reviewState: "approved",
        analysisAllowed: true,
        analysisSucceeded: true
      });
      expect(firstSnapshot.humanCorrection).toMatchObject({
        characterDisplayName: "Mira",
        correctedValue: firstSnapshot.humanCorrection?.effectiveSpeakerId,
        effectiveAuthority: "human",
        reason: correctionReason,
        authoritySource: "human",
        immutable: true,
        lockedAgainstAutomation: true,
        fieldPath: "/effectiveSpeakerId",
        targetEntityType: "DialogueLine"
      });
      const phase2Workflow = await runPhase2GovernanceWorkflow(firstPage);
      const phase3Workflow = await runPhase3GovernanceWorkflow(
        firstPage,
        phase2Workflow.governed
      );
      const phase3bWorkflow = await runPhase3bGovernanceWorkflow(firstPage);
      const firstOwnership = await establishDevelopmentOwnership(
        first,
        phase3bWorkflow.liveRuntimeInstance
      );
      const firstExit = await closeElectron(first, firstOwnership);
      expect(firstExit.graceful).toBe(true);
      expect(firstExit.forced).toBe(false);
      expect(firstExit.exactOwnedPidsGone).toBe(true);
      first = null;

      second = await launch(dataDirectory);
      const secondPage = await second.firstWindow();
      await expect(
        secondPage.getByText("Backend ready", { exact: true }).first()
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        secondPage.getByRole("heading", { name: "Persistence Demo" })
      ).toBeVisible({ timeout: 15_000 });
      const secondReviewCard = secondPage.locator(".import-review-card");
      await expect(
        secondReviewCard.getByRole("heading", {
          name: "sample-story.docx"
        })
      ).toBeVisible({ timeout: 20_000 });
      await expect(
        secondReviewCard.locator(".review-state")
      ).toHaveText("Approved");
      await expect(
        reviewEvidence(secondReviewCard, "Declared format")
      ).toHaveText("DOCX");
      await expect(
        reviewEvidence(secondReviewCard, "Detected format")
      ).toHaveText("DOCX");
      await expect(
        reviewEvidence(secondReviewCard, "Analysis may continue")
      ).toHaveText("Yes");
      const restoredSnapshot =
        await readPersistedImportSnapshot(secondPage, correctionReason);
      expect(restoredSnapshot).toEqual(firstSnapshot);
      if (restoredSnapshot.warningCount === 0) {
        await expect(
          secondReviewCard.getByText("No extraction warnings", {
            exact: true
          })
        ).toBeVisible();
      }
      await secondPage
        .getByRole("navigation", { name: "Scenes" })
        .getByRole("button")
        .first()
        .click();
      await expect(
        secondPage.getByText("Human correction").first()
      ).toBeVisible({ timeout: 15_000 });
      const restoredPhase2 = await readPhase2RuntimeSnapshot(secondPage);
      const phase2Evidence = buildPackagedStoryAnalysisEvidence(
        phase2Workflow,
        restoredPhase2
      );
      expectPhase2RestartPersistence(phase2Evidence);
      await secondPage
        .getByRole("button", { name: "Analysis", exact: true })
        .click();
      await expect(
        secondPage.locator(".analysis-gate header strong", {
          hasText: "Approved"
        })
      ).toHaveCount(4);
      await secondPage
        .getByRole("button", { name: "Corrections", exact: true })
        .click();
      await expect(
        secondPage.locator(".correction-history article")
      ).toHaveCount(4);
      await secondPage
        .getByRole("button", { name: "Casting", exact: true })
        .click();
      await expect(
        secondPage.getByRole("heading", {
          name: "Casting workspace",
          exact: true
        })
      ).toBeVisible({ timeout: 30_000 });
      const restoredPhase3 =
        await readPhase3RuntimeSnapshot(secondPage);
      expectPhase3RestartPersistence(phase3Workflow, restoredPhase3);
      const restoredPhase3b = await provePhase3bRestartPersistence(
        secondPage,
        phase3bWorkflow
      );
      expect(restoredPhase3b.cacheRecordsPersisted).toBe(true);
      expect(restoredPhase3b.authenticatedRestoredAudioLoaded).toBe(true);
      await expect(
        secondPage.locator(".review-card .review-state", {
          hasText: "Approved"
        })
      ).toHaveCount(3);
      const secondOwnership = await establishDevelopmentOwnership(
        second,
        restoredPhase3b.liveRuntimeInstance
      );
      const secondExit = await closeElectron(second, secondOwnership);
      expect(secondExit.graceful).toBe(true);
      expect(secondExit.forced).toBe(false);
      expect(secondExit.exactOwnedPidsGone).toBe(true);
      second = null;
    } finally {
      await closeElectron(second);
      await closeElectron(first);
      await rm(isolationRoot, {
        recursive: true,
        force: true,
        maxRetries: 20,
        retryDelay: 250
      });
    }
  });
});

interface PersistedImportSnapshot {
  readonly format: "docx";
  readonly sourceSha256: string;
  readonly sourceRevision: number;
  readonly extractedTextSha256: string;
  readonly extractionRevision: number;
  readonly extractionStatus: "complete" | "partial";
  readonly warningCount: number;
  readonly reviewState:
    | "pending"
    | "approved"
    | "changes_requested"
    | "rejected"
    | "invalidated";
  readonly analysisAllowed: boolean;
  readonly analysisSucceeded: boolean;
  readonly humanCorrection: PersistedHumanCorrection | null;
}

interface PersistedHumanCorrection {
  readonly correctionId: string;
  readonly attributionId: string;
  readonly attributionRevision: number;
  readonly lineId: string;
  readonly lineRevision: number;
  readonly effectiveSpeakerId: string;
  readonly effectiveAuthority: "human";
  readonly characterDisplayName: "Mira";
  readonly targetEntityType: string;
  readonly targetEntityId: string;
  readonly targetRevision: number;
  readonly fieldPath: string;
  readonly previousValueFingerprint: string | null;
  readonly correctedValue: string;
  readonly reason: string;
  readonly authoritySource: "human";
  readonly authorityActorId: string;
  readonly recordedAt: string;
  readonly immutable: true;
  readonly lockedAgainstAutomation: true;
  readonly supersedesCorrectionId: string | null;
}

async function readPersistedImportSnapshot(
  page: Page,
  expectedCorrectionReason: string | null = null
): Promise<PersistedImportSnapshot> {
  return page.evaluate(async (correctionReasonToFind) => {
    const result = await window.cinematicStory.projects.restoreRecent();
    if (!result.ok) {
      throw new Error(
        `Project evidence read failed with ${result.error.code}.`
      );
    }
    const detail = result.value;
    if (detail === null) {
      throw new Error("The persisted project was unavailable.");
    }
    const extraction = [...detail.extractions].sort((left, right) => {
      const byTime = left.updatedAt.localeCompare(right.updatedAt);
      return byTime === 0
        ? left.revision - right.revision
        : byTime;
    }).at(-1);
    if (
      extraction === undefined ||
      extraction.detectedFormat !== "docx" ||
      extraction.extractedTextSha256 === undefined ||
      (extraction.status !== "complete" &&
        extraction.status !== "partial")
    ) {
      throw new Error("The persisted DOCX extraction was incomplete.");
    }
    const source = detail.sourceDocuments.find(
      (item) => item.documentId === extraction.sourceDocumentId
    );
    const review = [...detail.importReviews]
      .filter((item) => item.extractionId === extraction.extractionId)
      .sort((left, right) => left.revision - right.revision)
      .at(-1);
    if (source === undefined || review === undefined) {
      throw new Error("The persisted import evidence was incomplete.");
    }
    let humanCorrection: PersistedHumanCorrection | null = null;
    if (correctionReasonToFind !== null) {
      const mira = detail.characters.find(
        (character) => character.displayName === "Mira"
      );
      if (mira === undefined) {
        throw new Error("The persisted Mira character was unavailable.");
      }
      const matches = detail.dialogueAttributions.flatMap((attribution) =>
        attribution.humanCorrections
          .filter(
            (correction) =>
              correction.reason === correctionReasonToFind &&
              correction.correctedValue === mira.characterId
          )
          .map((correction) => ({ attribution, correction }))
      );
      if (matches.length !== 1) {
        throw new Error(
          "The exact persisted human speaker correction was ambiguous."
        );
      }
      const match = matches[0];
      if (match === undefined) {
        throw new Error("The persisted human correction was unavailable.");
      }
      const line = detail.dialogueLines.find(
        (candidate) => candidate.lineId === match.attribution.lineId
      );
      if (
        line === undefined ||
        match.attribution.effectiveSpeakerId !== mira.characterId ||
        match.attribution.effectiveAuthority !== "human" ||
        typeof match.correction.correctedValue !== "string" ||
        match.correction.correctedValue !== mira.characterId ||
        match.correction.target.entityType !== "DialogueLine" ||
        match.correction.target.entityId !== line.lineId ||
        match.correction.target.revision !== line.revision ||
        match.correction.fieldPath !== "/effectiveSpeakerId" ||
        match.correction.authority.source !== "human" ||
        match.correction.immutable !== true ||
        match.correction.lockedAgainstAutomation !== true
      ) {
        throw new Error(
          "The persisted human correction lost identity or authority."
        );
      }
      humanCorrection = {
        correctionId: match.correction.correctionId,
        attributionId: match.attribution.attributionId,
        attributionRevision: match.attribution.revision,
        lineId: line.lineId,
        lineRevision: line.revision,
        effectiveSpeakerId: mira.characterId,
        effectiveAuthority: "human",
        characterDisplayName: "Mira",
        targetEntityType: match.correction.target.entityType,
        targetEntityId: match.correction.target.entityId,
        targetRevision: match.correction.target.revision,
        fieldPath: match.correction.fieldPath,
        previousValueFingerprint:
          match.correction.previousValueFingerprint ?? null,
        correctedValue: match.correction.correctedValue,
        reason: match.correction.reason,
        authoritySource: match.correction.authority.source,
        authorityActorId: match.correction.authority.actorId,
        recordedAt: match.correction.recordedAt,
        immutable: match.correction.immutable,
        lockedAgainstAutomation:
          match.correction.lockedAgainstAutomation,
        supersedesCorrectionId:
          match.correction.supersedesCorrectionId ?? null
      };
    }
    return {
      format: "docx",
      sourceSha256: extraction.sourceSha256,
      sourceRevision: source.revision,
      extractedTextSha256: extraction.extractedTextSha256,
      extractionRevision: extraction.revision,
      extractionStatus: extraction.status,
      warningCount: review.warnings.length,
      reviewState: review.state,
      analysisAllowed: detail.analysisAllowed,
      analysisSucceeded: detail.jobs.some(
        (job) =>
          job.type === "analyze_story" && job.state === "succeeded"
      ),
      humanCorrection
    };
  }, expectedCorrectionReason);
}

async function waitForJobState(
  page: Page,
  progressName: string,
  state: string,
  timeout: number
): Promise<void> {
  const progress = page
    .getByRole("progressbar", { name: progressName })
    .last();
  await expect(progress).toBeVisible({ timeout });
  const article = progress.locator("xpath=ancestor::article[1]");
  const jobState = article.locator(".job-state");
  await expect(jobState).toHaveText(
    new RegExp(`^(?:${state}|Failed)$`, "u"),
    { timeout }
  );
  if ((await jobState.textContent())?.trim() === "Failed") {
    const jobError =
      (await article.locator(".job-error").textContent())?.trim() ??
      "No stable job error was rendered.";
    throw new Error(`${progressName} failed: ${jobError}`);
  }
}

function reviewEvidence(card: Locator, label: string): Locator {
  return card
    .getByText(label, { exact: true })
    .locator("..")
    .locator("dd");
}

interface DevelopmentProcessOwnership {
  readonly electron: ProcessIdentity;
  readonly service: ProcessIdentity;
  readonly providerWorker: ProcessIdentity;
}

async function establishDevelopmentOwnership(
  application: ElectronApplication,
  runtime: SpeechRuntimeInstance
): Promise<DevelopmentProcessOwnership> {
  const electronRuntime = await application.evaluate(() => ({
    pid: process.pid,
    executablePath: process.execPath
  }));
  const pids = [
    electronRuntime.pid,
    runtime.parentPid,
    runtime.workerPid
  ];
  if (
    pids.some((pid) => !Number.isSafeInteger(pid) || pid <= 0) ||
    new Set(pids).size !== pids.length
  ) {
    throw new Error(
      "The development Electron, service, and provider-worker PIDs were invalid or ambiguous."
    );
  }
  const identities = await queryExactProcessIdentities(pids);
  const electronIdentity = identities.find(
    (item) => item.pid === electronRuntime.pid
  );
  const serviceIdentity = identities.find(
    (item) => item.pid === runtime.parentPid
  );
  const providerWorkerIdentity = identities.find(
    (item) => item.pid === runtime.workerPid
  );
  if (
    identities.length !== 3 ||
    electronIdentity === undefined ||
    serviceIdentity === undefined ||
    providerWorkerIdentity === undefined ||
    electronIdentity.executablePath === null ||
    serviceIdentity.executablePath === null ||
    providerWorkerIdentity.executablePath === null ||
    !sameWindowsPath(
      electronIdentity.executablePath,
      electronRuntime.executablePath
    ) ||
    serviceIdentity.parentPid !== electronIdentity.pid ||
    providerWorkerIdentity.parentPid !== serviceIdentity.pid ||
    !sameWindowsPath(
      serviceIdentity.executablePath,
      providerWorkerIdentity.executablePath
    ) ||
    path.win32.basename(providerWorkerIdentity.executablePath).toLowerCase() !==
      runtime.executableIdentity.toLowerCase() ||
    serviceIdentity.creationDate < electronIdentity.creationDate ||
    providerWorkerIdentity.creationDate < serviceIdentity.creationDate
  ) {
    throw new Error(
      "Exact development Electron, service, and provider-worker ownership could not be established."
    );
  }
  const ownedPython: readonly OwnedProcess[] = [
    { ...serviceIdentity, kind: "service" },
    { ...providerWorkerIdentity, kind: "provider_worker" }
  ];
  const networkObservation =
    await observeOwnedProcessNetworkEndpoints(ownedPython);
  if (networkObservation.observedNonLoopbackEndpointCount !== 0) {
    throw new Error(
      "An exact owned development Python process exposed or used a non-loopback TCP endpoint."
    );
  }
  return {
    electron: electronIdentity,
    service: serviceIdentity,
    providerWorker: providerWorkerIdentity
  };
}

async function closeElectron(
  application: ElectronApplication | null,
  ownership: DevelopmentProcessOwnership | null = null
): Promise<{
  readonly graceful: boolean;
  readonly forced: boolean;
  readonly exactOwnedPidsGone: boolean;
}> {
  if (application === null) {
    return { graceful: true, forced: false, exactOwnedPidsGone: true };
  }
  const child = application.process();
  const outcome = await Promise.race([
    application.close().then(
      () => "closed" as const,
      () => "failed" as const
    ),
    delay(12_000).then(() => "timeout" as const)
  ]);
  let forced = false;
  if (
    outcome !== "closed" &&
    child.exitCode === null &&
    child.signalCode === null
  ) {
    child.kill();
    forced = true;
    await Promise.race([once(child, "exit"), delay(3_000)]);
  }
  const exactOwnedPidsGone =
    ownership === null
      ? true
      : await waitForExactDevelopmentProcessesGone(ownership);
  return {
    graceful:
      outcome === "closed" &&
      child.exitCode !== null &&
      child.signalCode === null,
    forced,
    exactOwnedPidsGone
  };
}

async function waitForExactDevelopmentProcessesGone(
  ownership: DevelopmentProcessOwnership
): Promise<true> {
  const expected = [
    ownership.electron,
    ownership.service,
    ownership.providerWorker
  ];
  const pids = expected.map((item) => item.pid);
  const deadline = Date.now() + 15_000;
  let consecutiveAbsenceObservations = 0;
  while (Date.now() < deadline) {
    const current = await queryExactProcessIdentities(pids);
    const remaining = expected.filter((item) =>
      current.some((candidate) => sameStableDevelopmentIdentity(item, candidate))
    );
    if (remaining.length === 0) {
      consecutiveAbsenceObservations += 1;
      if (consecutiveAbsenceObservations >= 2) return true;
    } else {
      consecutiveAbsenceObservations = 0;
    }
    await delay(200);
  }
  throw new Error(
    "The exact owned development Electron, service, and provider-worker PIDs did not all exit."
  );
}

function sameStableDevelopmentIdentity(
  expected: ProcessIdentity,
  current: ProcessIdentity
): boolean {
  if (
    expected.pid !== current.pid ||
    expected.name.toLowerCase() !== current.name.toLowerCase() ||
    expected.creationDate !== current.creationDate
  ) {
    return false;
  }
  if (
    expected.executablePath === null ||
    current.executablePath === null
  ) {
    return true;
  }
  return sameWindowsPath(expected.executablePath, current.executablePath);
}

function sameWindowsPath(left: string, right: string): boolean {
  return (
    path.win32.resolve(left).toLowerCase() ===
    path.win32.resolve(right).toLowerCase()
  );
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
