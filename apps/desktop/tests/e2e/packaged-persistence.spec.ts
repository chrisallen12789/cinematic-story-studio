import { once } from "node:events";
import { createHash } from "node:crypto";
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
  type ElectronApplication,
  type Locator,
  type Page
} from "@playwright/test";

import {
  isolatedEnvironmentNames,
  packagedE2eSchemaVersion,
  packagedFailureCode,
  packagedFixture,
  packagedFlow,
  materializeStrictBase64Docx,
  runPackagedE2eEvidenceStep,
  writePackagedE2eMachineResult,
  type MachineLaunchEvidence,
  type PackagedFailureCode,
  type PackagedFailureStage,
  type PackagedImportReviewEvidence,
  type PackagedStoryAnalysisEvidence
} from "../../src/verification/packaged-e2e-evidence";
import {
  packagedShutdownEvidenceEnvironment,
  readPackagedServiceShutdownEvidence,
  type PackagedServiceShutdownEvidence
} from "../../src/main/shutdown-evidence";
import {
  phase3PackagedE2eResultEnvironment,
  phase3PackagedE2eSchemaVersion,
  phase3PackagedFixture,
  phase3PackagedFlow,
  phase3VoiceCastingEvidenceEnvironment,
  voiceCastingContractVersion,
  writePhase3PackagedE2eResult,
  writePhase3VoiceCastingEvidence,
  type Phase3LaunchShutdownProof,
  type Phase3PackagedE2eResult
} from "../../src/verification/phase3-packaged-e2e-evidence";
import {
  buildPackagedStoryAnalysisEvidence,
  expectPhase2RestartPersistence,
  readPhase2RuntimeSnapshot,
  runPhase2GovernanceWorkflow,
  type Phase2WorkflowEvidence
} from "./phase2-story-analysis";
import {
  buildPhase3PersistenceEvidence,
  buildPhase3VoiceCastingEvidence,
  expectPhase3RestartPersistence,
  readPhase3RuntimeSnapshot,
  runPhase3GovernanceWorkflow,
  type Phase3PersistenceEvidence,
  type Phase3WorkflowEvidence
} from "./phase3-voice-casting";
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
const encodedFixturePath = path.resolve(
  desktopRoot,
  "../../fixtures/synthetic-story/sample-story.docx.base64"
);
const packagedExecutableEnvironment = "CSS_PACKAGED_E2E_EXECUTABLE";
const evidencePathEnvironment = "CSS_PACKAGED_E2E_EVIDENCE_PATH";
const resultPathEnvironment = "CSS_PACKAGED_E2E_RESULT_PATH";
const processInventory = createPackagedProcessInventory();
const monotonicNow = () => performance.now();
const correctionReason = "packaged fixture correction";
const ownershipSampleIntervalMs = 100;
const ownershipSampleDeadlineMs = 10_000;
const gracefulApplicationShutdownTimeoutMs = 30_000;

test.describe("packaged desktop verification", () => {
  test.skip(
    !hasEnvironmentValue(packagedExecutableEnvironment) ||
      !hasEnvironmentValue(evidencePathEnvironment) ||
      !hasEnvironmentValue(resultPathEnvironment) ||
      !hasEnvironmentValue(phase3PackagedE2eResultEnvironment) ||
      !hasEnvironmentValue(phase3VoiceCastingEvidenceEnvironment),
    `Set ${packagedExecutableEnvironment}, ${evidencePathEnvironment}, ${resultPathEnvironment}, ${phase3PackagedE2eResultEnvironment}, and ${phase3VoiceCastingEvidenceEnvironment} to run the packaged gate.`
  );

  test("runs the synthetic persistence flow in the packaged application", async () => {
    test.setTimeout(600_000);
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
    const phase3ResultPath = requireReleaseEvidencePath(
      requiredEnvironment(phase3PackagedE2eResultEnvironment),
      packaged,
      "phase-3-packaged-e2e-result.json",
      phase3PackagedE2eResultEnvironment
    );
    const phase3VoiceCastingEvidencePath = requireReleaseEvidencePath(
      requiredEnvironment(phase3VoiceCastingEvidenceEnvironment),
      packaged,
      "phase-3-voice-casting-evidence.json",
      phase3VoiceCastingEvidenceEnvironment
    );

    let isolationRoot: string | null = null;
    let preexistingProcesses: readonly ProcessIdentity[] | null = null;
    let first: ElectronApplication | null = null;
    let second: ElectronApplication | null = null;
    let firstOwnership: LaunchOwnership | null = null;
    let secondOwnership: LaunchOwnership | null = null;
    let firstOwnershipSampler: OwnershipSampler | null = null;
    let secondOwnershipSampler: OwnershipSampler | null = null;
    let firstShutdownEvidencePath: string | null = null;
    let secondShutdownEvidencePath: string | null = null;
    let operationError: Error | null = null;
    let failureStage: PackagedFailureStage | null = null;
    let failureCode: PackagedFailureCode | null = null;
    let currentStage: PackagedFailureStage = "isolation_setup";
    let screenshotCaptured = false;
    let importReviewEvidence: PackagedImportReviewEvidence | null = null;
    let storyAnalysisEvidence: PackagedStoryAnalysisEvidence | null = null;
    let phase2Workflow: Phase2WorkflowEvidence;
    let phase3Workflow: Phase3WorkflowEvidence;
    let phase3PersistenceEvidence: Phase3PersistenceEvidence | null = null;
    const cleanupErrors: unknown[] = [];
    const launchEvidence: MachineLaunchEvidence[] = [];
    const phase3LaunchShutdowns: Phase3LaunchShutdownProof[] = [];
    const begunLaunches = new Set<1 | 2>();
    const ownedLaunches = new Set<1 | 2>();
    const writeMachineResult = async (
      status: "passed" | "failed",
      cleanupCompleted: boolean,
      completedAt = new Date().toISOString()
    ) => {
      await writePackagedE2eMachineResult(resultPath, {
        schemaVersion: packagedE2eSchemaVersion,
        completedAt,
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
        importReview: importReviewEvidence,
        storyAnalysis: storyAnalysisEvidence,
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
        mkdir(path.dirname(resultPath), { recursive: true }),
        mkdir(path.dirname(phase3ResultPath), { recursive: true }),
        mkdir(path.dirname(phase3VoiceCastingEvidencePath), {
          recursive: true
        })
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
      const isolatedUserDataPath = path.join(
        isolatedPaths.localAppData,
        "Cinematic Story Studio"
      );
      firstShutdownEvidencePath = path.join(
        isolatedUserDataPath,
        "packaged-e2e-service-shutdown-1.json"
      );
      secondShutdownEvidencePath = path.join(
        isolatedUserDataPath,
        "packaged-e2e-service-shutdown-2.json"
      );
      await Promise.all([
        mkdir(isolatedPaths.localAppData, { recursive: true }),
        mkdir(isolatedPaths.roamingAppData, { recursive: true }),
        mkdir(isolatedPaths.temporaryDirectory, { recursive: true })
      ]);
      const fixturePath = path.join(
        isolatedPaths.temporaryDirectory,
        "sample-story.docx"
      );
      const importedFixtureSha256 = await materializeStrictBase64Docx(
        encodedFixturePath,
        isolationRoot,
        fixturePath
      );

      begunLaunches.add(1);
      await checkpointStage("launch_1");
      first = await launchPackaged(
        packaged.executablePath,
        { ...isolatedPaths },
        firstShutdownEvidencePath
      );
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
      firstOwnershipSampler = startOwnershipSampler(firstOwnership);
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
        .getByRole("button", { name: "Import document" })
        .click();
      await expect(
        firstPage.getByText(
          "Queued secure extraction for sample-story.docx."
        )
      ).toBeVisible({ timeout: 30_000 });
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
        60_000
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
      const pendingSnapshot =
        await readPersistedImportSnapshot(firstPage);
      expect(pendingSnapshot).toMatchObject({
        format: "docx",
        sourceSha256: importedFixtureSha256,
        reviewState: "pending",
        analysisAllowed: false,
        analysisSucceeded: false,
        humanCorrection: null
      });
      expect(pendingSnapshot.extractedTextSha256).toMatch(
        /^[a-f0-9]{64}$/u
      );
      expect(pendingSnapshot.sourceRevision).toBeGreaterThan(0);
      expect(pendingSnapshot.extractionRevision).toBeGreaterThan(0);
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
      await expect(approvalNotice).toBeVisible({ timeout: 30_000 });
      await expect(
        firstPage.locator(".import-review-card .review-state")
      ).toHaveText("Approved");
      await expect(
        firstPage.getByRole("button", { name: "Analyze story" })
      ).toBeEnabled();
      await firstPage
        .getByRole("button", { name: "Dismiss notification" })
        .click();
      await expect(approvalNotice).toBeHidden();

      await firstPage.getByRole("button", { name: "Analyze story" }).click();
      await waitForJobState(
        firstPage,
        "Analyze story progress",
        "Succeeded",
        60_000
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

      await firstPage
        .getByLabel("Speaker")
        .first()
        .selectOption({ label: "Mira" });
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
      ).toBeVisible({ timeout: 20_000 });
      await expect(
        firstPage.getByText("Human correction").first()
      ).toBeVisible({ timeout: 20_000 });
      const firstSnapshot = await readPersistedImportSnapshot(
        firstPage,
        correctionReason
      );
      expect(firstSnapshot).toMatchObject({
        format: "docx",
        sourceSha256: importedFixtureSha256,
        sourceRevision: pendingSnapshot.sourceRevision,
        extractedTextSha256: pendingSnapshot.extractedTextSha256,
        extractionRevision: pendingSnapshot.extractionRevision,
        extractionStatus: pendingSnapshot.extractionStatus,
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
      phase2Workflow = await runPhase2GovernanceWorkflow(firstPage);
      phase3Workflow = await runPhase3GovernanceWorkflow(
        firstPage,
        phase2Workflow.governed
      );

      await checkpointStage("shutdown_1");
      await firstOwnershipSampler.stop();
      firstOwnershipSampler = null;
      const firstExitProof = await closeOwnedElectron(
        first,
        firstOwnership,
        firstShutdownEvidencePath
      );
      expect(firstExitProof.graceful).toBe(true);
      launchEvidence.push(
        machineLaunchEvidence(1, firstOwnership, firstExitProof)
      );
      phase3LaunchShutdowns.push(
        phase3LaunchShutdownProof(1, firstOwnership, firstExitProof)
      );
      phase3Workflow.flowRecorder.record("close_application");
      phase3Workflow.flowRecorder.record(
        "verify_first_shutdown_owned_process_exit"
      );
      first = null;
      firstOwnership = null;

      await checkpointStage("prelaunch_inventory_2");
      const beforeSecondLaunch = await queryRelevantProcesses();
      begunLaunches.add(2);
      await checkpointStage("launch_2");
      second = await launchPackaged(
        packaged.executablePath,
        { ...isolatedPaths },
        secondShutdownEvidencePath
      );
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
      phase3Workflow.flowRecorder.record("restart_same_application");
      await checkpointStage("service_ownership_2");
      secondOwnership = await expandOwnership(
        secondOwnership,
        true
      );
      ownedLaunches.add(2);
      secondOwnershipSampler =
        startOwnershipSampler(secondOwnership);
      await checkpointStage("restore_2");
      await expect(
        secondPage.getByRole("heading", {
          name: "Packaged Persistence Demo"
        })
      ).toBeVisible({ timeout: 20_000 });
      const secondReviewCard = secondPage.locator(".import-review-card");
      await expect(
        secondReviewCard.getByRole("heading", {
          name: "sample-story.docx"
        })
      ).toBeVisible({ timeout: 30_000 });
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
        await readPersistedImportSnapshot(
          secondPage,
          correctionReason
        );
      if (restoredSnapshot.warningCount === 0) {
        await expect(
          secondReviewCard.getByText("No extraction warnings", {
            exact: true
          })
        ).toBeVisible();
      }
      const extractionPersistedAfterRestart =
        restoredSnapshot.sourceSha256 === firstSnapshot.sourceSha256 &&
        restoredSnapshot.sourceRevision === firstSnapshot.sourceRevision &&
        restoredSnapshot.extractedTextSha256 ===
          firstSnapshot.extractedTextSha256 &&
        restoredSnapshot.extractionRevision ===
          firstSnapshot.extractionRevision &&
        restoredSnapshot.extractionStatus ===
          firstSnapshot.extractionStatus &&
        restoredSnapshot.warningCount === firstSnapshot.warningCount;
      const approvalPersistedAfterRestart =
        restoredSnapshot.reviewState === "approved" &&
        restoredSnapshot.analysisAllowed;
      const analysisPersistedAfterRestart =
        restoredSnapshot.analysisSucceeded;
      expect(extractionPersistedAfterRestart).toBe(true);
      expect(approvalPersistedAfterRestart).toBe(true);
      expect(analysisPersistedAfterRestart).toBe(true);
      expect(restoredSnapshot.humanCorrection).toEqual(
        firstSnapshot.humanCorrection
      );
      const restoredPhase2 = await readPhase2RuntimeSnapshot(secondPage);
      storyAnalysisEvidence = buildPackagedStoryAnalysisEvidence(
        phase2Workflow,
        restoredPhase2
      );
      expectPhase2RestartPersistence(storyAnalysisEvidence);
      phase3Workflow.flowRecorder.record(
        "restore_phase_0_through_phase_2_evidence"
      );
      await secondPage
        .getByRole("navigation", { name: "Scenes" })
        .getByRole("button")
        .first()
        .click();
      await expect(
        secondPage.getByText("Human correction").first()
      ).toBeVisible({ timeout: 20_000 });
      importReviewEvidence = {
        format: "docx",
        sourceSha256: restoredSnapshot.sourceSha256,
        extractedTextSha256: restoredSnapshot.extractedTextSha256,
        extractionRevision: restoredSnapshot.extractionRevision,
        warningCount: restoredSnapshot.warningCount,
        approvalDecision: "approved",
        approvalPersistedAfterRestart,
        extractionPersistedAfterRestart,
        analysisPersistedAfterRestart
      };
      await secondPage
        .getByRole("button", { name: "Analysis", exact: true })
        .click();
      await secondPage
        .getByRole("button", { name: "Corrections", exact: true })
        .click();
      await expect(
        secondPage.locator(".correction-history article")
      ).toHaveCount(4);
      await expect(
        secondPage.locator(".analysis-gate header strong", {
          hasText: "Approved"
        })
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
      phase3Workflow.flowRecorder.record(
        "restore_phase_3a_casting_evidence"
      );
      phase3PersistenceEvidence = buildPhase3PersistenceEvidence(
        phase3Workflow,
        restoredPhase3,
        storyAnalysisEvidence
      );
      await expect(
        secondPage.locator(".review-card .review-state", {
          hasText: "Approved"
        })
      ).toHaveCount(3);
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
      await secondOwnershipSampler.stop();
      secondOwnershipSampler = null;
      const secondExitProof = await closeOwnedElectron(
        second,
        secondOwnership,
        secondShutdownEvidencePath
      );
      expect(secondExitProof.graceful).toBe(true);
      launchEvidence.push(
        machineLaunchEvidence(2, secondOwnership, secondExitProof)
      );
      phase3LaunchShutdowns.push(
        phase3LaunchShutdownProof(2, secondOwnership, secondExitProof)
      );
      phase3Workflow.flowRecorder.record("close_restarted_application");
      phase3Workflow.flowRecorder.record(
        "verify_final_owned_process_exit"
      );
      phase3Workflow.flowRecorder.complete();
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
      for (const sampler of [
        secondOwnershipSampler,
        firstOwnershipSampler
      ]) {
        try {
          await sampler?.stop();
        } catch (error) {
          cleanupErrors.push(error);
        }
      }
      for (const [application, ownership, shutdownEvidencePath] of [
        [second, secondOwnership, secondShutdownEvidencePath],
        [first, firstOwnership, firstShutdownEvidencePath]
      ] as const) {
        try {
          await closeOwnedElectron(
            application,
            ownership,
            shutdownEvidencePath
          );
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
      const completedAt = new Date().toISOString();
      if (operationError === null && cleanupCompleted) {
        failureStage = "evidence_generation";
        failureCode = "EVIDENCE_GENERATION_FAILED";
        if (
          phase3PersistenceEvidence === null ||
          launchEvidence.length !== 2
        ) {
          cleanupErrors.push(
            new Error(
              "The complete Phase 3A packaged evidence was unavailable."
            )
          );
        } else {
          try {
            const phase3Result = await buildPhase3MachineResult({
              completedAt,
              screenshotPath: evidencePath,
              packagedVersion: packaged.version,
              launchEvidence,
              launchShutdowns: phase3LaunchShutdowns,
              persistence: phase3PersistenceEvidence
            });
            await writePhase3PackagedE2eResult(
              phase3ResultPath,
              phase3Result
            );
            await writePhase3VoiceCastingEvidence(
              phase3VoiceCastingEvidencePath,
              buildPhase3VoiceCastingEvidence(
                phase3PersistenceEvidence,
                phase3Result
              )
            );
          } catch (error) {
            cleanupErrors.push(error);
          }
        }
      }
      try {
        await writeMachineResult(
          operationError === null &&
            cleanupCompleted &&
            cleanupErrors.length === 0
            ? "passed"
            : "failed",
          cleanupCompleted,
          completedAt
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

interface OwnershipSampler {
  stop(): Promise<void>;
}

interface ExitProof {
  readonly ownedPids: readonly number[];
  readonly graceful: boolean;
  readonly forcedPids: readonly number[];
  readonly remainingPids: readonly number[];
  readonly electronExitCode: number | null;
  readonly serviceShutdown: ServiceShutdownProof | null;
}

interface ServiceShutdownProof {
  readonly pid: number;
  readonly method: "stdin_eof";
  readonly exitCode: 0;
  readonly signalCode: null;
  readonly forceKillUsed: false;
}

interface ElectronShutdownState {
  readonly child: ReturnType<ElectronApplication["process"]>;
  readonly outcome: "closed" | "failed" | "timeout";
  readonly forcedPids: readonly number[];
  readonly electronExitCode: number | null;
  readonly serviceShutdownEvidence:
    | PackagedServiceShutdownEvidence
    | null;
  readonly shutdownEvidenceError: Error | null;
}

const electronShutdownStates =
  new WeakMap<ElectronApplication, ElectronShutdownState>();

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

async function launchPackaged(
  executablePath: string,
  isolatedPaths: IsolatedPaths,
  shutdownEvidencePath: string
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
  environment[packagedShutdownEvidenceEnvironment] =
    shutdownEvidencePath;

  return electron.launch({
    executablePath,
    cwd: path.dirname(executablePath),
    env: environment,
    timeout: 45_000
  });
}

async function closeOwnedElectron(
  application: ElectronApplication | null,
  ownership: LaunchOwnership | null,
  shutdownEvidencePath: string | null
): Promise<ExitProof> {
  if (application === null) {
    return {
      ownedPids: [],
      graceful: true,
      forcedPids: [],
      remainingPids: [],
      electronExitCode: null,
      serviceShutdown: null
    };
  }
  const priorShutdown = electronShutdownStates.get(application);
  const child = priorShutdown?.child ?? application.process();
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
  if (
    verifiedOwnership !== null &&
    (child.pid === undefined ||
      verifiedOwnership.launcherPid !== child.pid)
  ) {
    throw new Error("Packaged process ownership no longer matches its root.");
  }
  let shutdownState = priorShutdown;
  if (shutdownState === undefined) {
    const forcedPids: number[] = [];
    const outcome = await Promise.race([
      application.close().then(
        () => "closed" as const,
        () => "failed" as const
      ),
      delay(gracefulApplicationShutdownTimeoutMs).then(
        () => "timeout" as const
      )
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
    let serviceShutdownEvidence:
      | PackagedServiceShutdownEvidence
      | null = null;
    let shutdownEvidenceError: Error | null = null;
    if (shutdownEvidencePath === null) {
      shutdownEvidenceError = new Error(
        "The packaged service shutdown evidence path was unavailable."
      );
    } else {
      try {
        serviceShutdownEvidence =
          await readPackagedServiceShutdownEvidence(
            shutdownEvidencePath
          );
      } catch (error) {
        shutdownEvidenceError =
          error instanceof Error
            ? error
            : new Error(
                "The packaged service shutdown evidence could not be read."
              );
      }
    }
    shutdownState = {
      child,
      outcome,
      forcedPids,
      electronExitCode: child.exitCode,
      serviceShutdownEvidence,
      shutdownEvidenceError
    };
    electronShutdownStates.set(application, shutdownState);
  }
  const {
    outcome,
    forcedPids,
    electronExitCode,
    serviceShutdownEvidence,
    shutdownEvidenceError
  } = shutdownState;

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
  if (shutdownEvidenceError !== null) {
    throw shutdownEvidenceError;
  }
  const serviceShutdown = proveGracefulServiceShutdown(
    verifiedOwnership,
    serviceShutdownEvidence
  );
  if (electronExitCode !== 0) {
    throw new Error(
      `The packaged Electron launcher exit code was ${String(electronExitCode)} instead of 0.`
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
    remainingPids: [],
    electronExitCode: 0,
    serviceShutdown
  };
}

function proveGracefulServiceShutdown(
  ownership: LaunchOwnership,
  evidence: PackagedServiceShutdownEvidence | null
): ServiceShutdownProof {
  if (
    evidence === null ||
    evidence.status !== "succeeded" ||
    evidence.forceKillUsed !== false ||
    evidence.allProcessesExitedGracefully !== true ||
    evidence.processes.length !== 1
  ) {
    throw new Error(
      "The packaged service did not report one voluntary shutdown."
    );
  }
  const serviceRoot = ownership.processes.filter(
    (item) =>
      item.kind === "service" &&
      item.parentPid === ownership.rootPid
  );
  const process = evidence.processes[0];
  if (
    serviceRoot.length !== 1 ||
    process === undefined ||
    process.pid !== serviceRoot[0]?.pid ||
    process.method !== "stdin_eof" ||
    process.exitCode !== 0 ||
    process.signalCode !== null
  ) {
    throw new Error(
      "The packaged service shutdown record did not match the owned service root."
    );
  }
  return {
    pid: process.pid,
    method: "stdin_eof",
    exitCode: 0,
    signalCode: null,
    forceKillUsed: false
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

function startOwnershipSampler(
  ownership: LaunchOwnership
): OwnershipSampler {
  let stopRequested = false;
  let failure: Error | null = null;
  const completed = (async () => {
    while (!stopRequested) {
      try {
        const deadline =
          monotonicNow() + ownershipSampleDeadlineMs;
        const current = await queryRelevantProcesses(deadline);
        ownership.processes = adoptVerifiedProcessTree({
          current,
          baseline: ownership.baseline,
          owned: ownership.processes,
          rootPid: ownership.rootPid,
          packaged: ownership.packaged
        });
      } catch (error) {
        failure =
          error instanceof Error
            ? error
            : new Error("Packaged ownership sampling failed.");
        return;
      }
      if (!stopRequested) {
        await delay(ownershipSampleIntervalMs);
      }
    }
  })();

  return {
    async stop() {
      stopRequested = true;
      await completed;
      if (failure !== null) {
        throw failure;
      }
    }
  };
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
    exitProof: {
      ownedPids: exitProof.ownedPids,
      graceful: exitProof.graceful,
      forcedPids: exitProof.forcedPids,
      remainingPids: exitProof.remainingPids
    }
  };
}

function phase3LaunchShutdownProof(
  launch: 1 | 2,
  ownership: LaunchOwnership,
  exitProof: ExitProof
): Phase3LaunchShutdownProof {
  const service = exitProof.serviceShutdown;
  if (
    exitProof.electronExitCode !== 0 ||
    exitProof.forcedPids.length !== 0 ||
    service === null
  ) {
    throw new Error(
      "The Phase 3A launch shutdown proof was incomplete."
    );
  }
  return {
    launch,
    electron: {
      launcherPid: ownership.launcherPid,
      rootPid: ownership.rootPid,
      exitCode: 0,
      forceKillUsed: false
    },
    service: { ...service }
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

function requireReleaseEvidencePath(
  value: string,
  packaged: PackagedPaths,
  fileName: string,
  environmentName: string
): string {
  const candidate = requireAbsolutePath(value, environmentName);
  const expected = path.join(
    path.dirname(path.dirname(packaged.executablePath)),
    fileName
  );
  if (!samePath(candidate, expected)) {
    throw new Error(
      `${environmentName} must be the exact release evidence path.`
    );
  }
  return candidate;
}

async function buildPhase3MachineResult({
  completedAt,
  screenshotPath,
  packagedVersion,
  launchEvidence,
  launchShutdowns,
  persistence
}: {
  readonly completedAt: string;
  readonly screenshotPath: string;
  readonly packagedVersion: string;
  readonly launchEvidence: readonly MachineLaunchEvidence[];
  readonly launchShutdowns: readonly Phase3LaunchShutdownProof[];
  readonly persistence: Phase3PersistenceEvidence;
}): Promise<Phase3PackagedE2eResult> {
  if (
    launchEvidence.length !== 2 ||
    launchShutdowns.length !== 2 ||
    launchEvidence.some(
      (launch) =>
        !launch.exitProof.graceful ||
        launch.exitProof.forcedPids.length !== 0 ||
        launch.exitProof.remainingPids.length !== 0
    ) ||
    launchShutdowns.some((shutdown, index) => {
      const launch = launchEvidence[index];
      return (
        launch === undefined ||
        shutdown.launch !== launch.launch ||
        shutdown.electron.launcherPid !==
          launch.ownership.launcherPid ||
        shutdown.electron.rootPid !== launch.ownership.rootPid ||
        shutdown.electron.exitCode !== 0 ||
        shutdown.electron.forceKillUsed ||
        shutdown.service.method !== "stdin_eof" ||
        shutdown.service.exitCode !== 0 ||
        shutdown.service.signalCode !== null ||
        shutdown.service.forceKillUsed ||
        !launch.ownership.processes.some(
          (process) =>
            process.pid === shutdown.service.pid &&
            process.parentPid === launch.ownership.rootPid &&
            process.kind === "service"
        ) ||
        !launch.exitProof.ownedPids.includes(shutdown.service.pid)
      );
    })
  ) {
    throw new Error(
      "The aggregate Phase 3A owned-process exit proof is incomplete."
    );
  }
  const screenshot = await lstat(screenshotPath);
  if (!screenshot.isFile() || screenshot.size < 1) {
    throw new Error("The Phase 3A packaged screenshot is unavailable.");
  }
  const repositoryRoot = path.resolve(desktopRoot, "../..");
  const relativeScreenshotPath = path
    .relative(repositoryRoot, screenshotPath)
    .split(path.sep)
    .join("/");
  if (
    relativeScreenshotPath.length === 0 ||
    relativeScreenshotPath.startsWith("../") ||
    path.posix.isAbsolute(relativeScreenshotPath) ||
    relativeScreenshotPath !==
      `apps/desktop/release/${packagedVersion}/packaged-e2e.png`
  ) {
    throw new Error(
      "The Phase 3A screenshot path escaped the governed release directory."
    );
  }
  const electronOwnedPids = sortedUniquePids(
    launchEvidence.flatMap((launch) =>
      launch.ownership.processes
        .filter((process) => process.kind === "app")
        .map((process) => process.pid)
    )
  );
  const serviceOwnedPids = sortedUniquePids(
    launchEvidence.flatMap((launch) =>
      launch.ownership.processes
        .filter((process) => process.kind === "service")
        .map((process) => process.pid)
    )
  );
  if (electronOwnedPids.length === 0 || serviceOwnedPids.length === 0) {
    throw new Error(
      "The aggregate Phase 3A process ownership inventory is empty."
    );
  }
  return {
    schemaVersion: phase3PackagedE2eSchemaVersion,
    contractVersion: voiceCastingContractVersion,
    result: "passed",
    fixture: phase3PackagedFixture,
    flow: phase3PackagedFlow,
    casting: persistence.casting,
    screenshot: {
      relativePath: relativeScreenshotPath,
      byteSize: screenshot.size,
      sha256: createHash("sha256")
        .update(await readFile(screenshotPath))
        .digest("hex"),
      captureStatus: "success"
    },
    processOwnership: {
      ownershipEstablished: true,
      electronOwnedPids,
      serviceOwnedPids,
      launchShutdowns: launchShutdowns.map((shutdown) => ({
        launch: shutdown.launch,
        electron: { ...shutdown.electron },
        service: { ...shutdown.service }
      })),
      forcedPids: [],
      remainingOwnedPids: [],
      unrelatedProcessesTerminated: false
    },
    assertions: persistence.assertions,
    completedAt
  };
}

function sortedUniquePids(values: readonly number[]): readonly number[] {
  return [...new Set(values)].sort((left, right) => left - right);
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
