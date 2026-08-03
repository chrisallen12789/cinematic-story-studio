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
  phase3bRuntimeShutdownEvidenceEnvironment,
  readPhase3bRuntimeShutdownEvidence,
  readPackagedServiceShutdownEvidence,
  type PackagedServiceShutdownEvidence,
  type Phase3bRuntimeShutdownEvidence
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
  adoptVerifiedProcessTreeWithPathConfirmation,
  appExecutableName,
  bindProviderWorkerProcessTree,
  containsProcessIdentity,
  createPackagedProcessInventory,
  defaultProcessInventoryPolicy,
  matchesPackagedProcessPath,
  remainingOwnedProcesses,
  serviceExecutableName,
  type ConfirmedExitedTransientProcess,
  type OwnedProcess,
  type PackagedProcessPaths,
  type ProcessIdentity
} from "../../src/verification/packaged-process-inventory";
import {
  observeRelevantProcessBaselineAfterRejectedLaunch,
  packagedElectronFirstWindowTimeout,
  packagedElectronLaunchTimeout,
  type PackagedElectronLaunchPurpose,
  type RejectedLaunchBaselineObservation
} from "../../src/verification/packaged-launch-rejection";
import {
  writePhase3b1PrivateFailureSidecar,
  type Phase3b1PrivateFailureCode,
  type Phase3b1PrivateFailureStage,
  type Phase3b1PrivateFailureStartupObservation
} from "../../src/verification/phase3b1-private-failure-sidecar";
import {
  phase3b1RendererErrorCodeFromError
} from "../../src/verification/phase3b1-renderer-error-evidence";
import {
  observeStableOwnedProcessNetworkEndpoints,
  type OwnedProcessNetworkObservation
} from "../../src/verification/owned-process-network-observation";
import {
  phase3bAssertionKeys,
  phase3b1SyntheticMetadataEvidence,
  phase3bFixtureEvidenceClassification,
  phase3bPackagedE2eResultEnvironment,
  phase3bPackagedE2eSchemaVersion,
  writePhase3bPackagedE2eResult,
  type Phase3bPackagedE2eResult,
  type Phase3bRuntimeExitEvidence
} from "../../src/verification/phase3b-packaged-e2e-evidence";
import {
  provePhase3bRestartPersistence,
  runPhase3bGovernanceWorkflow,
  type Phase3bRestartEvidence,
  type Phase3bWorkflowEvidence
} from "./phase3b-local-speech-auditions";
import {
  completePrivateListeningPackage,
  phase3b1PrivateEvidenceRootEnvironment,
  phase3b1RealModelPackageZipEnvironment,
  phase3b1SourceHeadEnvironment,
  preservePhase3b1PrivateReplayState,
  provePhase3b1RestartPersistence,
  requirePhase3b1LocalInputs,
  runPhase3b1RealProductPathWorkflow,
  type Phase3b1LocalInputs
} from "./phase3b1-real-product-path";

const desktopRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const encodedFixturePath = path.resolve(
  desktopRoot,
  "../../fixtures/synthetic-story/sample-story.docx.base64"
);
const localRendersRoot = path.resolve(desktopRoot, "../../local-renders");
const packagedExecutableEnvironment = "CSS_PACKAGED_E2E_EXECUTABLE";
const evidencePathEnvironment = "CSS_PACKAGED_E2E_EVIDENCE_PATH";
const resultPathEnvironment = "CSS_PACKAGED_E2E_RESULT_PATH";
const processInventory = createPackagedProcessInventory();
const monotonicNow = () => performance.now();
const correctionReason = "packaged fixture correction";
const ownershipSampleIntervalMs = 100;
const ownershipSampleDeadlineMs = 10_000;
const gracefulApplicationShutdownTimeoutMs = 30_000;
const phase3b1StartupProbeTimeoutMs = 10_000;

test.describe("packaged desktop verification", () => {
  test.skip(
    !hasEnvironmentValue(packagedExecutableEnvironment) ||
      !hasEnvironmentValue(evidencePathEnvironment) ||
      !hasEnvironmentValue(resultPathEnvironment) ||
      !hasEnvironmentValue(phase3PackagedE2eResultEnvironment) ||
      !hasEnvironmentValue(phase3VoiceCastingEvidenceEnvironment) ||
      !hasEnvironmentValue(phase3bPackagedE2eResultEnvironment),
    `Set ${packagedExecutableEnvironment}, ${evidencePathEnvironment}, ${resultPathEnvironment}, ${phase3PackagedE2eResultEnvironment}, ${phase3VoiceCastingEvidenceEnvironment}, and ${phase3bPackagedE2eResultEnvironment} to run the packaged gate.`
  );

  test("runs the synthetic persistence flow in the packaged application", async () => {
    const realModelPackageConfigured = hasEnvironmentValue(
      phase3b1RealModelPackageZipEnvironment
    );
    const privateEvidenceRootConfigured = hasEnvironmentValue(
      phase3b1PrivateEvidenceRootEnvironment
    );
    const sourceHeadConfigured = hasEnvironmentValue(
      phase3b1SourceHeadEnvironment
    );
    if (
      new Set([
        realModelPackageConfigured,
        privateEvidenceRootConfigured,
        sourceHeadConfigured
      ]).size !== 1
    ) {
      throw new Error(
        `Set all or none of ${phase3b1RealModelPackageZipEnvironment}, ${phase3b1PrivateEvidenceRootEnvironment}, and ${phase3b1SourceHeadEnvironment}.`
      );
    }
    const runRealProductPath = realModelPackageConfigured;
    test.setTimeout(runRealProductPath ? 1_800_000 : 900_000);
    const phase3b1Inputs: Phase3b1LocalInputs | null = runRealProductPath
      ? await requirePhase3b1LocalInputs(
          requiredEnvironment(phase3b1RealModelPackageZipEnvironment),
          requiredEnvironment(phase3b1PrivateEvidenceRootEnvironment)
        )
      : null;
    const phase3b1SourceHead = runRealProductPath
      ? requireSourceHead(requiredEnvironment(phase3b1SourceHeadEnvironment))
      : null;
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
    const phase3bResultPath = requireReleaseEvidencePath(
      requiredEnvironment(phase3bPackagedE2eResultEnvironment),
      packaged,
      "phase-3b-packaged-e2e-result.json",
      phase3bPackagedE2eResultEnvironment
    );

    let isolationRoot: string | null = null;
    let preexistingProcesses: readonly ProcessIdentity[] | null = null;
    let first: ElectronApplication | null = null;
    let second: ElectronApplication | null = null;
    let third: ElectronApplication | null = null;
    let fourth: ElectronApplication | null = null;
    let firstOwnership: LaunchOwnership | null = null;
    let secondOwnership: LaunchOwnership | null = null;
    let thirdOwnership: LaunchOwnership | null = null;
    let fourthOwnership: LaunchOwnership | null = null;
    let firstOwnershipSampler: OwnershipSampler | null = null;
    let secondOwnershipSampler: OwnershipSampler | null = null;
    let thirdOwnershipSampler: OwnershipSampler | null = null;
    let fourthOwnershipSampler: OwnershipSampler | null = null;
    let firstShutdownEvidencePath: string | null = null;
    let secondShutdownEvidencePath: string | null = null;
    let thirdShutdownEvidencePath: string | null = null;
    let fourthShutdownEvidencePath: string | null = null;
    let operationError: Error | null = null;
    let failureStage: PackagedFailureStage | null = null;
    let failureCode: PackagedFailureCode | null = null;
    let currentStage: PackagedFailureStage = "isolation_setup";
    let syntheticGateCompleted = false;
    let phase3b1Stage: Phase3b1PrivateFailureStage | null = null;
    let phase3b1CurrentLaunch: 3 | 4 | null = null;
    let phase3b1StartedAt: string | null = null;
    let phase3b1LaunchReturnedAt: string | null = null;
    let phase3b1FirstWindowWaitStartedAt: string | null = null;
    let phase3b1OperationFailedAt: string | null = null;
    let phase3b1ExplicitFailureCode: Phase3b1PrivateFailureCode | null = null;
    let phase3b1StartupObservations:
      Phase3b1PrivateFailureStartupObservation[] = [];
    let phase3b1RejectedLaunchObservation:
      | RejectedLaunchBaselineObservation
      | null = null;
    let phase3b1CurrentLaunchOwnershipEstablished = false;
    let phase3b1CurrentLaunchOwnedProcessExitClaimed = false;
    let phase3b1IsolationCleanupAllowed = true;
    const phase3b1UnownedApplications = new WeakSet<ElectronApplication>();
    let screenshotCaptured = false;
    let importReviewEvidence: PackagedImportReviewEvidence | null = null;
    let storyAnalysisEvidence: PackagedStoryAnalysisEvidence | null = null;
    let phase2Workflow: Phase2WorkflowEvidence;
    let phase3Workflow: Phase3WorkflowEvidence;
    let phase3PersistenceEvidence: Phase3PersistenceEvidence | null = null;
    let phase3bWorkflow: Phase3bWorkflowEvidence | null = null;
    let phase3bRestartEvidence: Phase3bRestartEvidence | null = null;
    const phase3bNetworkObservations: OwnedProcessNetworkObservation[] = [];
    const phase3bRuntimeExits: Phase3bRuntimeExitEvidence[] = [];
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
    const beginPhase3b1Launch = (launch: 3 | 4) => {
      phase3b1CurrentLaunch = launch;
      phase3b1StartedAt = new Date().toISOString();
      phase3b1LaunchReturnedAt = null;
      phase3b1FirstWindowWaitStartedAt = null;
      phase3b1OperationFailedAt = null;
      phase3b1ExplicitFailureCode = null;
      phase3b1StartupObservations = [];
      phase3b1CurrentLaunchOwnershipEstablished = false;
      phase3b1CurrentLaunchOwnedProcessExitClaimed = false;
      phase3b1RejectedLaunchObservation = null;
    };
    const observePhase3b1Startup = async (
      application: ElectronApplication,
      phase: Phase3b1PrivateFailureStartupObservation["phase"]
    ): Promise<Phase3b1PrivateFailureStartupObservation> => {
      let timeout: ReturnType<typeof setTimeout> | null = null;
      try {
        const state = await Promise.race([
          application.evaluate(({ app, BrowserWindow }) => ({
            appReady: app.isReady(),
            singleInstanceLockHeld: app.hasSingleInstanceLock(),
            browserWindowCount: BrowserWindow.getAllWindows().length
          })),
          new Promise<never>((_resolve, reject) => {
            timeout = setTimeout(() => {
              reject(
                new Error("The bounded Electron startup probe timed out.")
              );
            }, phase3b1StartupProbeTimeoutMs);
          })
        ]);
        if (
          typeof state.appReady !== "boolean" ||
          typeof state.singleInstanceLockHeld !== "boolean" ||
          !Number.isSafeInteger(state.browserWindowCount) ||
          state.browserWindowCount < 0 ||
          state.browserWindowCount > 256
        ) {
          throw new Error("The Electron startup probe result was invalid.");
        }
        const observation: Phase3b1PrivateFailureStartupObservation = {
          phase,
          recordedAt: new Date().toISOString(),
          appReady: state.appReady,
          singleInstanceLockHeld: state.singleInstanceLockHeld,
          browserWindowCount: state.browserWindowCount
        };
        phase3b1StartupObservations.push(observation);
        return observation;
      } finally {
        if (timeout !== null) {
          clearTimeout(timeout);
        }
      }
    };
    const waitForPhase3b1FirstWindow = async (
      application: ElectronApplication
    ): Promise<Page> => {
      let initialObservation: Phase3b1PrivateFailureStartupObservation;
      try {
        initialObservation = await observePhase3b1Startup(
          application,
          "after_root_ownership"
        );
      } catch (error) {
        phase3b1ExplicitFailureCode = "startup_probe_failed";
        throw error;
      }
      if (!initialObservation.singleInstanceLockHeld) {
        phase3b1ExplicitFailureCode = "single_instance_lock_not_held";
        throw new Error(
          "The owned Electron root did not hold the single-instance lock."
        );
      }
      phase3b1FirstWindowWaitStartedAt = new Date().toISOString();
      try {
        return await application.firstWindow({
          timeout: packagedElectronFirstWindowTimeout("phase3b1_real")
        });
      } catch (error) {
        if (error instanceof Error && error.name === "TimeoutError") {
          phase3b1ExplicitFailureCode = "first_window_timeout";
        }
        try {
          await observePhase3b1Startup(
            application,
            "after_first_window_failure"
          );
        } catch {
          // The primary first-window failure remains authoritative.
        }
        throw error;
      }
    };
    const launchPhase3b1Packaged = async (
      launch: 3 | 4,
      isolatedPaths: IsolatedPaths,
      shutdownEvidencePath: string,
      beforeLaunch: readonly ProcessIdentity[]
    ): Promise<ElectronApplication> => {
      phase3b1Stage = launch === 3 ? "launch_3" : "launch_4";
      try {
        const application = await launchPackaged(
          packaged.executablePath,
          isolatedPaths,
          shutdownEvidencePath,
          "phase3b1_real"
        );
        phase3b1LaunchReturnedAt = new Date().toISOString();
        return application;
      } catch (launchError) {
        phase3b1Stage =
          launch === 3
            ? "post_rejection_inventory_3"
            : "post_rejection_inventory_4";
        const observation =
          await observeRelevantProcessBaselineAfterRejectedLaunch({
            baseline: beforeLaunch,
            queryCurrent: (deadlineAt) => queryRelevantProcesses(deadlineAt),
            deadlineAt:
              monotonicNow() +
              defaultProcessInventoryPolicy.totalDeadlineMs
          });
        phase3b1RejectedLaunchObservation = observation;
        if (
          observation.baselineDeltaStatus === "observed_absent" &&
          observation.consecutiveDeltaFreeObservations ===
            observation.requiredConsecutiveDeltaFreeObservations
        ) {
          phase3b1Stage = launch === 3 ? "launch_3" : "launch_4";
          throw launchError;
        }
        phase3b1IsolationCleanupAllowed = false;
        throw new AggregateError(
          [
            launchError,
            new Error(
              "The allow-listed relevant-process baseline was not proved restored after Playwright rejected the Electron launch."
            )
          ],
          "The Phase 3B.1 Electron launch was rejected and its relevant-process baseline was not restored.",
          { cause: launchError }
        );
      }
    };
    try {
      await Promise.all([
        mkdir(path.dirname(evidencePath), { recursive: true }),
        mkdir(path.dirname(resultPath), { recursive: true }),
        mkdir(path.dirname(phase3ResultPath), { recursive: true }),
        mkdir(path.dirname(phase3bResultPath), { recursive: true }),
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
      if (phase3b1Inputs !== null) {
        thirdShutdownEvidencePath = path.join(
          isolatedUserDataPath,
          "phase3b1-real-service-shutdown-3.json"
        );
        fourthShutdownEvidencePath = path.join(
          isolatedUserDataPath,
          "phase3b1-real-service-shutdown-4.json"
        );
      }
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
        firstShutdownEvidencePath,
        "synthetic_fixture"
      );
      await checkpointStage("root_ownership_1");
      firstOwnership = await establishRootOwnership(
        first,
        preexistingProcesses,
        packaged
      );
      await checkpointStage("readiness_1");
      const firstPage = await first.firstWindow({
        timeout: packagedElectronFirstWindowTimeout("synthetic_fixture")
      });
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
      phase3bWorkflow = await runPhase3bGovernanceWorkflow(firstPage);

      const completedFirstOwnershipSampler = firstOwnershipSampler;
      firstOwnershipSampler = null;
      await completedFirstOwnershipSampler.stop();
      firstOwnership = await bindPhase3bProviderWorkerOwnership(
        firstOwnership,
        phase3bWorkflow.liveRuntimeInstance.workerPid,
        phase3bWorkflow.liveRuntimeInstance.parentPid
      );
      phase3bNetworkObservations.push(
        await requireNoOwnedPythonExternalEndpoints(
          firstOwnership,
          phase3bWorkflow.liveRuntimeInstance.workerPid,
          phase3bWorkflow.liveRuntimeInstance.parentPid
        )
      );

      await checkpointStage("shutdown_1");
      const firstExitProof = await closeOwnedElectron(
        first,
        firstOwnership,
        firstShutdownEvidencePath
      );
      expect(firstExitProof.graceful).toBe(true);
      phase3bRuntimeExits.push(
        provePhase3bRuntimeShutdown(
          firstExitProof.runtimeShutdownEvidence,
          phase3bWorkflow.liveRuntimeInstance
        )
      );
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
        secondShutdownEvidencePath,
        "synthetic_fixture"
      );
      await checkpointStage("root_ownership_2");
      secondOwnership = await establishRootOwnership(
        second,
        beforeSecondLaunch,
        packaged
      );
      await checkpointStage("readiness_2");
      const secondPage = await second.firstWindow({
        timeout: packagedElectronFirstWindowTimeout("synthetic_fixture")
      });
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
      phase3bRestartEvidence = await provePhase3bRestartPersistence(
        secondPage,
        phase3bWorkflow
      );
      const completedSecondOwnershipSampler = secondOwnershipSampler;
      secondOwnershipSampler = null;
      await completedSecondOwnershipSampler.stop();
      secondOwnership = await bindPhase3bProviderWorkerOwnership(
        secondOwnership,
        phase3bRestartEvidence.liveRuntimeInstance.workerPid,
        phase3bRestartEvidence.liveRuntimeInstance.parentPid
      );
      phase3bNetworkObservations.push(
        await requireNoOwnedPythonExternalEndpoints(
          secondOwnership,
          phase3bRestartEvidence.liveRuntimeInstance.workerPid,
          phase3bRestartEvidence.liveRuntimeInstance.parentPid
        )
      );
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
        secondOwnership,
        secondShutdownEvidencePath
      );
      expect(secondExitProof.graceful).toBe(true);
      phase3bRuntimeExits.push(
        provePhase3bRuntimeShutdown(
          secondExitProof.runtimeShutdownEvidence,
          phase3bRestartEvidence.liveRuntimeInstance
        )
      );
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
      syntheticGateCompleted = true;

      if (
        phase3b1Inputs !== null &&
        phase3b1SourceHead !== null &&
        thirdShutdownEvidencePath !== null &&
        fourthShutdownEvidencePath !== null
      ) {
        beginPhase3b1Launch(3);
        phase3b1Stage = "prelaunch_inventory_3";
        const beforeThirdLaunch = await queryRelevantProcesses();
        third = await launchPhase3b1Packaged(
          3,
          { ...isolatedPaths },
          thirdShutdownEvidencePath,
          beforeThirdLaunch
        );
        phase3b1Stage = "root_ownership_3";
        try {
          thirdOwnership = await establishRootOwnership(
            third,
            beforeThirdLaunch,
            packaged
          );
        } catch (error) {
          phase3b1UnownedApplications.add(third);
          phase3b1IsolationCleanupAllowed = false;
          throw error;
        }
        phase3b1CurrentLaunchOwnershipEstablished = true;
        phase3b1Stage = "readiness_3";
        const thirdPage = await waitForPhase3b1FirstWindow(third);
        await expect(
          thirdPage.getByText("Backend ready", { exact: true }).first()
        ).toBeVisible({ timeout: 45_000 });
        phase3b1Stage = "workflow_3";
        thirdOwnership = await expandOwnership(thirdOwnership, true);
        thirdOwnershipSampler = startOwnershipSampler(thirdOwnership);
        const phase3b1Workflow = await runPhase3b1RealProductPathWorkflow(
          thirdPage,
          third,
          phase3b1Inputs
        );
        const completedThirdOwnershipSampler = thirdOwnershipSampler;
        thirdOwnershipSampler = null;
        await completedThirdOwnershipSampler.stop();
        thirdOwnership = await bindPhase3bProviderWorkerOwnership(
          thirdOwnership,
          phase3b1Workflow.liveRuntimeInstance.workerPid,
          phase3b1Workflow.liveRuntimeInstance.parentPid
        );
        const realNetworkObservation =
          await requireNoOwnedPythonExternalEndpoints(
            thirdOwnership,
            phase3b1Workflow.liveRuntimeInstance.workerPid,
            phase3b1Workflow.liveRuntimeInstance.parentPid
          );
        phase3b1Stage = "shutdown_3";
        const thirdExitProof = await closeOwnedElectron(
          third,
          thirdOwnership,
          thirdShutdownEvidencePath
        );
        const realRuntimeExit = provePhase3bRuntimeShutdown(
          thirdExitProof.runtimeShutdownEvidence,
          phase3b1Workflow.liveRuntimeInstance
        );
        const thirdLaunchProof = phase3b1LocalLaunchProof(
          3,
          thirdOwnership,
          thirdExitProof
        );
        phase3b1CurrentLaunchOwnedProcessExitClaimed = true;
        third = null;
        thirdOwnership = null;

        beginPhase3b1Launch(4);
        phase3b1Stage = "prelaunch_inventory_4";
        const beforeFourthLaunch = await queryRelevantProcesses();
        fourth = await launchPhase3b1Packaged(
          4,
          { ...isolatedPaths },
          fourthShutdownEvidencePath,
          beforeFourthLaunch
        );
        phase3b1Stage = "root_ownership_4";
        try {
          fourthOwnership = await establishRootOwnership(
            fourth,
            beforeFourthLaunch,
            packaged
          );
        } catch (error) {
          phase3b1UnownedApplications.add(fourth);
          phase3b1IsolationCleanupAllowed = false;
          throw error;
        }
        phase3b1CurrentLaunchOwnershipEstablished = true;
        phase3b1Stage = "readiness_4";
        const fourthPage = await waitForPhase3b1FirstWindow(fourth);
        await expect(
          fourthPage.getByText("Backend ready", { exact: true }).first()
        ).toBeVisible({ timeout: 45_000 });
        phase3b1Stage = "restore_4";
        fourthOwnership = await expandOwnership(fourthOwnership, true);
        fourthOwnershipSampler = startOwnershipSampler(fourthOwnership);
        const phase3b1RestartEvidence = await provePhase3b1RestartPersistence(
          fourthPage,
          phase3b1Workflow.evidence
        );
        const completedFourthOwnershipSampler = fourthOwnershipSampler;
        fourthOwnershipSampler = null;
        await completedFourthOwnershipSampler.stop();
        fourthOwnership = await expandOwnership(fourthOwnership, true);
        phase3b1Stage = "shutdown_4";
        const fourthExitProof = await closeOwnedElectron(
          fourth,
          fourthOwnership,
          fourthShutdownEvidencePath
        );
        const fourthLaunchProof = phase3b1LocalLaunchProof(
          4,
          fourthOwnership,
          fourthExitProof
        );
        phase3b1CurrentLaunchOwnedProcessExitClaimed = true;
        fourth = null;
        fourthOwnership = null;

        if (isolationRoot === null) {
          throw new Error("The isolated private replay state was unavailable.");
        }
        phase3b1Stage = "private_evidence_generation";
        await preservePhase3b1PrivateReplayState(
          isolationRoot,
          phase3b1Workflow.listeningPackage,
          packaged.version
        );
        const listeningPackagePath = await completePrivateListeningPackage(
          phase3b1Workflow.listeningPackage,
          {
            schemaVersion: 1,
            completedAt: new Date().toISOString(),
            status: "passed",
            sourceHeadSha: phase3b1SourceHead,
            packagedVersion: packaged.version,
            executable:
              `release/${packaged.version}/win-unpacked/${appExecutableName}`,
            workflow: phase3b1Workflow.evidence,
            restart: phase3b1RestartEvidence,
            process: {
              launches: [thirdLaunchProof, fourthLaunchProof],
              providerRuntimeExit: realRuntimeExit,
              networkObservation: realNetworkObservation,
              exactOwnedProcessesExited: true,
              forcedTerminationUsed: false,
              unrelatedProcessesInspected: false,
              unrelatedProcessesTerminated: false
            },
            privateListeningPackage: {
              directoryName: phase3b1Workflow.listeningPackage.directoryName,
              replayStateDirectoryName:
                phase3b1Workflow.listeningPackage.replayStateDirectoryName,
              replayLauncherFileName:
                phase3b1Workflow.listeningPackage.replayLauncherFileName,
              isolatedDesktopStateRetained: true,
              listeningIndexSha256:
                phase3b1Workflow.listeningPackage.indexSha256,
              listeningScorecardSha256:
                phase3b1Workflow.listeningPackage.scorecardSha256,
              committed: false,
              uploaded: false
            },
            claims: {
              humanListeningStatus: "pending",
              humanListeningClaimed: false,
              humanPerceivedQualityClaimed: false,
              productionExportEligible: false,
              commercialClearanceClaimed: false,
              consentClaimed: false
            }
          }
        );
        process.stdout.write(
          `PHASE3B1_PRIVATE_LISTENING_PACKAGE=${listeningPackagePath}\n`
        );
      }
    } catch (error) {
      phase3b1OperationFailedAt = new Date().toISOString();
      operationError =
        error instanceof Error
          ? error
          : new Error("Packaged verification failed.");
      if (!syntheticGateCompleted) {
        failureStage = currentStage;
        failureCode = packagedFailureCode(currentStage, error);
        try {
          await writeMachineResult("failed", false);
        } catch (checkpointError) {
          cleanupErrors.push(checkpointError);
        }
      }
    } finally {
      for (const sampler of [
        fourthOwnershipSampler,
        thirdOwnershipSampler,
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
        [fourth, fourthOwnership, fourthShutdownEvidencePath],
        [third, thirdOwnership, thirdShutdownEvidencePath],
        [second, secondOwnership, secondShutdownEvidencePath],
        [first, firstOwnership, firstShutdownEvidencePath]
      ] as const) {
        if (
          application !== null &&
          phase3b1UnownedApplications.has(application)
        ) {
          continue;
        }
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
      if (
        isolationRoot !== null &&
        phase3b1IsolationCleanupAllowed &&
        cleanupErrors.length === 0
      ) {
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
      const cleanupCompleted =
        cleanupErrors.length === 0 && phase3b1IsolationCleanupAllowed;
      if (!syntheticGateCompleted && operationError === null && !cleanupCompleted) {
        failureStage = "cleanup";
        failureCode = "CLEANUP_FAILED";
      }
      const completedAt = new Date().toISOString();
      if (syntheticGateCompleted && !cleanupCompleted) {
        failureStage = "cleanup";
        failureCode = "CLEANUP_FAILED";
      }
      let syntheticEvidenceGenerated = false;
      if (syntheticGateCompleted && cleanupCompleted) {
        failureStage = "evidence_generation";
        failureCode = "EVIDENCE_GENERATION_FAILED";
        if (
          phase3PersistenceEvidence === null ||
          phase3bWorkflow === null ||
          phase3bRestartEvidence === null ||
          launchEvidence.length !== 2 ||
          phase3bNetworkObservations.length !== 2 ||
          phase3bRuntimeExits.length !== 2
        ) {
          cleanupErrors.push(
            new Error(
              "The complete Phase 3A and Phase 3B packaged evidence was unavailable."
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
            await writePhase3bPackagedE2eResult(
              phase3bResultPath,
              buildPhase3bMachineResult({
                completedAt,
                casting: phase3PersistenceEvidence.casting,
                workflow: phase3bWorkflow,
                restart: phase3bRestartEvidence,
                launchEvidence,
                networkObservations: phase3bNetworkObservations,
                runtimeExits: phase3bRuntimeExits
              })
            );
            syntheticEvidenceGenerated = true;
          } catch (error) {
            cleanupErrors.push(error);
          }
        }
      }
      try {
        await writeMachineResult(
          syntheticGateCompleted &&
            cleanupCompleted &&
            syntheticEvidenceGenerated &&
            cleanupErrors.length === 0
            ? "passed"
            : "failed",
          cleanupCompleted,
          completedAt
        );
      } catch (error) {
        cleanupErrors.push(error);
      }
      if (
        syntheticGateCompleted &&
        phase3b1Inputs !== null &&
        phase3b1SourceHead !== null &&
        phase3b1StartedAt !== null &&
        phase3b1CurrentLaunch !== null &&
        phase3b1Stage !== null &&
        (operationError !== null || !cleanupCompleted)
      ) {
        const privateFailureStage =
          operationError === null ? "cleanup" : phase3b1Stage;
        try {
          const sidecar = await writePhase3b1PrivateFailureSidecar({
            expectedLocalRendersParent: localRendersRoot,
            privateRoot: phase3b1Inputs.privateEvidenceRoot,
            sourceHeadSha: phase3b1SourceHead,
            applicationVersion: packaged.version,
            executableRelativePath:
              `apps/desktop/release/${packaged.version}/win-unpacked/${appExecutableName}`,
            launch: phase3b1CurrentLaunch,
            stage: privateFailureStage,
            failureCode:
              phase3b1ExplicitFailureCode ??
              phase3b1PrivateFailureCode(
                privateFailureStage,
                operationError
              ),
            rendererErrorCode:
              phase3b1RendererErrorCodeFromError(operationError),
            configuredLaunchTimeoutMs: packagedElectronLaunchTimeout(
              "phase3b1_real"
            ),
            configuredFirstWindowTimeoutMs:
              packagedElectronFirstWindowTimeout("phase3b1_real"),
            startedAt: phase3b1StartedAt,
            launchReturnedAt: phase3b1LaunchReturnedAt,
            firstWindowWaitStartedAt:
              phase3b1FirstWindowWaitStartedAt,
            failedAt:
              operationError === null
                ? completedAt
                : (phase3b1OperationFailedAt ?? completedAt),
            startupObservations: phase3b1StartupObservations,
            syntheticGateCompleted,
            ownershipEstablished:
              phase3b1CurrentLaunchOwnershipEstablished,
            ownedProcessExitClaimed:
              phase3b1CurrentLaunchOwnedProcessExitClaimed,
            cleanupCompleted,
            rejectedLaunchBaselineObservation:
              phase3b1RejectedLaunchObservation
          });
          process.stdout.write(
            `PHASE3B1_PRIVATE_FAILURE_SIDECAR=local-renders/phase3b1-real-product-path/${sidecar.relativePath}\n`
          );
        } catch (error) {
          cleanupErrors.push(error);
        }
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
  confirmedExitedTransientProcesses: readonly ConfirmedExitedTransientProcess[];
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
  readonly runtimeShutdownEvidence: Phase3bRuntimeShutdownEvidence | null;
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
  readonly runtimeShutdownEvidence:
    | Phase3bRuntimeShutdownEvidence
    | null;
  readonly runtimeShutdownEvidenceError: Error | null;
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

function phase3b1PrivateFailureCode(
  stage: Phase3b1PrivateFailureStage,
  error: Error | null
): Phase3b1PrivateFailureCode {
  if (stage === "launch_3" || stage === "launch_4") {
    return error?.name === "TimeoutError"
      ? "launch_timeout"
      : "launch_rejected";
  }
  if (
    stage === "prelaunch_inventory_3" ||
    stage === "prelaunch_inventory_4" ||
    stage === "post_rejection_inventory_3" ||
    stage === "post_rejection_inventory_4"
  ) {
    return "inventory_failure";
  }
  return "other";
}

async function launchPackaged(
  executablePath: string,
  isolatedPaths: IsolatedPaths,
  shutdownEvidencePath: string,
  purpose: PackagedElectronLaunchPurpose
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
  delete environment[phase3b1RealModelPackageZipEnvironment];
  delete environment[phase3b1PrivateEvidenceRootEnvironment];
  delete environment[phase3b1SourceHeadEnvironment];
  environment[packagedShutdownEvidenceEnvironment] =
    shutdownEvidencePath;
  environment[phase3bRuntimeShutdownEvidenceEnvironment] = "1";

  return electron.launch({
    executablePath,
    cwd: path.dirname(executablePath),
    env: environment,
    timeout: packagedElectronLaunchTimeout(purpose)
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
      serviceShutdown: null,
      runtimeShutdownEvidence: null
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
    let runtimeShutdownEvidence: Phase3bRuntimeShutdownEvidence | null = null;
    let runtimeShutdownEvidenceError: Error | null = null;
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
    if (shutdownEvidencePath === null) {
      runtimeShutdownEvidenceError = new Error(
        "The Phase 3B runtime shutdown evidence path was unavailable."
      );
    } else {
      try {
        runtimeShutdownEvidence = await readPhase3bRuntimeShutdownEvidence(
          path.dirname(shutdownEvidencePath)
        );
      } catch (error) {
        runtimeShutdownEvidenceError =
          error instanceof Error
            ? error
            : new Error(
                "The Phase 3B runtime shutdown evidence could not be read."
              );
      }
    }
    shutdownState = {
      child,
      outcome,
      forcedPids,
      electronExitCode: child.exitCode,
      serviceShutdownEvidence,
      shutdownEvidenceError,
      runtimeShutdownEvidence,
      runtimeShutdownEvidenceError
    };
    electronShutdownStates.set(application, shutdownState);
  }
  const {
    outcome,
    forcedPids,
    electronExitCode,
    serviceShutdownEvidence,
    shutdownEvidenceError,
    runtimeShutdownEvidence,
    runtimeShutdownEvidenceError
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
    processes: exitObservation.processes,
    confirmedExitedTransientProcesses:
      exitObservation.confirmedExitedTransientProcesses
  };
  ownership.processes = verifiedOwnership.processes;
  ownership.confirmedExitedTransientProcesses =
    verifiedOwnership.confirmedExitedTransientProcesses;
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
  if (runtimeShutdownEvidenceError !== null) {
    throw runtimeShutdownEvidenceError;
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
    ownedPids: allOwnedProcessesForEvidence(verifiedOwnership).map(
      (item) => item.pid
    ),
    graceful: true,
    forcedPids: [],
    remainingPids: [],
    electronExitCode: 0,
    serviceShutdown,
    runtimeShutdownEvidence
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

function provePhase3bRuntimeShutdown(
  evidence: Phase3bRuntimeShutdownEvidence | null,
  liveRuntime: Phase3bWorkflowEvidence["liveRuntimeInstance"]
): Phase3bRuntimeExitEvidence {
  const runtimeExit = evidence?.runtimeExits[0];
  if (
    evidence === null ||
    evidence.contractVersion !== "1.0.0" ||
    evidence.ownedRuntimeCount !== 1 ||
    evidence.runtimeExits.length !== 1 ||
    evidence.allGracefulShutdownsConfirmed !== true ||
    runtimeExit === undefined ||
    runtimeExit.runtimeInstanceId !== liveRuntime.runtimeInstanceId ||
    runtimeExit.workerPid !== liveRuntime.workerPid ||
    liveRuntime.handshakeAuthenticated !== true ||
    runtimeExit.state !== "stopped" ||
    runtimeExit.stopReasonCode !== "clean" ||
    runtimeExit.exitCode !== 0 ||
    runtimeExit.shutdownAcknowledged !== true ||
    runtimeExit.gracefulShutdownConfirmed !== true ||
    runtimeExit.terminatedByParent !== false ||
    runtimeExit.ownershipConfirmed !== true ||
    runtimeExit.ownedProcessesConfirmedExited !== true ||
    runtimeExit.jobObjectAssigned !== true ||
    runtimeExit.deniedNetworkAttemptCount !== 0 ||
    Date.parse(evidence.writtenAt) < Date.parse(runtimeExit.stoppedAt)
  ) {
    throw new Error(
      "The exact provider worker lacked authenticated graceful-shutdown sidecar proof."
    );
  }
  return {
    runtimeInstanceId: runtimeExit.runtimeInstanceId,
    workerPid: runtimeExit.workerPid,
    parentPid: liveRuntime.parentPid,
    state: "stopped",
    stoppedAt: runtimeExit.stoppedAt,
    stopReasonCode: "clean",
    handshakeAuthenticated: true,
    shutdownAcknowledged: true,
    gracefulShutdownConfirmed: true,
    exitCode: 0,
    terminatedByParent: false,
    ownershipConfirmed: true,
    confirmedExited: true,
    ownedProcessesConfirmedExited: true,
    jobObjectAssigned: true,
    deniedNetworkAttemptCount: 0
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
        confirmedExitedTransientProcesses: [],
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
        const adoption =
          await adoptVerifiedProcessTreeWithPathConfirmation({
            current,
            baseline: ownership.baseline,
            owned: ownership.processes,
            confirmedExitedTransientProcesses:
              ownership.confirmedExitedTransientProcesses,
            rootPid: ownership.rootPid,
            packaged: ownership.packaged,
            deadlineAt: deadline,
            queryCurrent: (confirmationDeadline) =>
              queryRelevantProcesses(confirmationDeadline)
          });
        ownership.processes = adoption.ownedProcesses;
        ownership.confirmedExitedTransientProcesses =
          adoption.confirmedExitedTransientProcesses;
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
  const adoption = await adoptVerifiedProcessTreeWithPathConfirmation({
    current,
    baseline: ownership.baseline,
    owned: ownership.processes,
    confirmedExitedTransientProcesses:
      ownership.confirmedExitedTransientProcesses,
    rootPid: ownership.rootPid,
    packaged: ownership.packaged,
    deadlineAt,
    queryCurrent: (confirmationDeadline) =>
      queryRelevantProcesses(confirmationDeadline)
  });
  const owned = adoption.ownedProcesses;
  if (owned.length === 0) {
    throw new Error("The packaged Electron root identity was lost.");
  }
  ownership.processes = owned;
  ownership.confirmedExitedTransientProcesses =
    adoption.confirmedExitedTransientProcesses;
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

async function bindPhase3bProviderWorkerOwnership(
  ownership: LaunchOwnership,
  workerPid: number,
  reportedParentPid: number
): Promise<LaunchOwnership> {
  const expanded = await expandOwnership(ownership, true);
  expanded.processes = bindProviderWorkerProcessTree({
    owned: expanded.processes,
    rootPid: expanded.rootPid,
    workerPid,
    reportedParentPid
  });
  if (
    !expanded.processes.some(
      (item) => item.pid === workerPid && item.kind === "provider_worker"
    )
  ) {
    throw new Error(
      "The runtime-reported provider worker was not bound to the exact owned process tree."
    );
  }
  return expanded;
}

async function requireNoOwnedPythonExternalEndpoints(
  ownership: LaunchOwnership,
  workerPid: number,
  reportedParentPid: number
): Promise<OwnedProcessNetworkObservation> {
  const historicalOwnedPythonProcesses = ownership.processes.filter(
    (item) => item.kind === "service" || item.kind === "provider_worker"
  );
  if (
    !historicalOwnedPythonProcesses.some(
      (item) => item.pid === reportedParentPid && item.kind === "service"
    ) ||
    !historicalOwnedPythonProcesses.some(
      (item) => item.pid === workerPid && item.kind === "provider_worker"
    )
  ) {
    throw new Error(
      "Exact service and provider-worker ownership is required before endpoint observation."
    );
  }
  const stable = await observeStableOwnedProcessNetworkEndpoints(
    ownership.processes,
    [reportedParentPid, workerPid],
    async (current) => {
      ownership.processes = [...current];
      const rebound = await bindPhase3bProviderWorkerOwnership(
        ownership,
        workerPid,
        reportedParentPid
      );
      return rebound.processes;
    }
  );
  ownership.processes = [...stable.ownedProcesses];
  const observation = stable.observation;
  if (observation.observedNonLoopbackEndpointCount !== 0) {
    throw new Error(
      "An exact owned Python process exposed or used a non-loopback TCP endpoint."
    );
  }
  return observation;
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
  readonly confirmedExitedTransientProcesses: readonly ConfirmedExitedTransientProcess[];
  readonly remaining: readonly OwnedProcess[];
}> {
  const deadline = Math.min(
    callerDeadline,
    monotonicNow() + timeoutMs
  );
  let processes = ownership.processes;
  let confirmedExitedTransientProcesses =
    ownership.confirmedExitedTransientProcesses;
  let remaining: readonly OwnedProcess[] = processes;
  let consecutiveAbsenceObservations = 0;
  while (monotonicNow() < deadline) {
    const current = await queryRelevantProcesses(deadline);
    const adoption = await adoptVerifiedProcessTreeWithPathConfirmation({
      current,
      baseline: ownership.baseline,
      owned: processes,
      confirmedExitedTransientProcesses,
      rootPid: ownership.rootPid,
      packaged: ownership.packaged,
      deadlineAt: deadline,
      queryCurrent: (confirmationDeadline) =>
        queryRelevantProcesses(confirmationDeadline)
    });
    processes = adoption.ownedProcesses;
    confirmedExitedTransientProcesses =
      adoption.confirmedExitedTransientProcesses;
    ownership.processes = processes;
    ownership.confirmedExitedTransientProcesses =
      confirmedExitedTransientProcesses;
    remaining = remainingOwnedProcesses(
      adoption.observedProcesses,
      processes
    );
    if (remaining.length === 0) {
      consecutiveAbsenceObservations += 1;
      if (consecutiveAbsenceObservations >= 2) {
        return {
          processes,
          confirmedExitedTransientProcesses,
          remaining: []
        };
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
  return { processes, confirmedExitedTransientProcesses, remaining };
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
    processes: allOwnedProcessesForEvidence(ownership).map((item) => ({
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

function phase3b1LocalLaunchProof(
  launch: 3 | 4,
  ownership: LaunchOwnership,
  exitProof: ExitProof
) {
  const allOwnedProcesses = allOwnedProcessesForEvidence(ownership);
  const ownedPids = [...exitProof.ownedPids].sort(
    (left, right) => left - right
  );
  const expectedPids = allOwnedProcesses
    .map((item) => item.pid)
    .sort((left, right) => left - right);
  if (
    !exitProof.graceful ||
    exitProof.electronExitCode !== 0 ||
    exitProof.forcedPids.length !== 0 ||
    exitProof.remainingPids.length !== 0 ||
    exitProof.serviceShutdown === null ||
    ownedPids.join(",") !== expectedPids.join(",") ||
    !allOwnedProcesses.some((item) => item.kind === "app") ||
    !allOwnedProcesses.some((item) => item.kind === "service")
  ) {
    throw new Error(
      `The exact Phase 3B.1 owned-process exit proof for launch ${launch} was incomplete.`
    );
  }
  return {
    launch,
    preexistingRelevantProcesses: ownership.baseline
      .map(redactPreexistingProcess)
      .sort(compareEvidenceProcess),
    ownedProcesses: allOwnedProcesses.map((item) => ({
      pid: item.pid,
      parentPid: item.parentPid,
      kind: item.kind === "app" ? "electron" : item.kind,
      executableName: item.name,
      creationIdentity: item.creationDate,
      goneAfterShutdown: true,
      ...phase3b1OwnershipBasis(item)
    })),
    electron: {
      launcherPid: ownership.launcherPid,
      rootPid: ownership.rootPid,
      exitCode: 0,
      forceKillUsed: false
    },
    service: { ...exitProof.serviceShutdown },
    forcedPids: [],
    remainingPids: [],
    unrelatedProcessesInspected: false,
    unrelatedProcessesTerminated: false
  } as const;
}

function phase3b1OwnershipBasis(
  item: OwnedProcess | ConfirmedExitedTransientProcess
) {
  if ("pathStatus" in item) {
    return {
      ownershipBasis:
        "verified_exact_parent_and_two_absence_observations" as const,
      executablePathStatus: item.pathStatus,
      absenceObservations: item.absenceObservations,
      verifiedParentCreationIdentity: item.verifiedParentCreationDate
    };
  }
  if (item.executablePath === null) {
    throw new Error(
      "An exact-path owned process lost its executable-path provenance."
    );
  }
  return {
    ownershipBasis: "exact_executable_path_and_verified_ancestry" as const,
    executablePathStatus: "exact_path_confirmed" as const,
    absenceObservations: 0 as const,
    verifiedParentCreationIdentity: null
  };
}

function allOwnedProcessesForEvidence(
  ownership: LaunchOwnership
): readonly (OwnedProcess | ConfirmedExitedTransientProcess)[] {
  const processes = [
    ...ownership.processes,
    ...ownership.confirmedExitedTransientProcesses
  ].sort((left, right) => left.pid - right.pid);
  if (new Set(processes.map((item) => item.pid)).size !== processes.length) {
    throw new Error("The packaged ownership evidence repeated a PID.");
  }
  return processes;
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

function buildPhase3bMachineResult({
  completedAt,
  casting,
  workflow,
  restart,
  launchEvidence,
  networkObservations,
  runtimeExits
}: {
  readonly completedAt: string;
  readonly casting: Phase3PersistenceEvidence["casting"];
  readonly workflow: Phase3bWorkflowEvidence;
  readonly restart: Phase3bRestartEvidence;
  readonly launchEvidence: readonly MachineLaunchEvidence[];
  readonly networkObservations: readonly OwnedProcessNetworkObservation[];
  readonly runtimeExits: readonly Phase3bRuntimeExitEvidence[];
}): Phase3bPackagedE2eResult {
  if (
    launchEvidence.length !== 2 ||
    networkObservations.length !== 2 ||
    runtimeExits.length !== 2 ||
    networkObservations.some(
      (item) =>
        item.method !== "owned_pid_tcp_endpoint_inventory" ||
        item.ownedPidsOnly !== true ||
        item.observedNonLoopbackEndpointCount !== 0
    )
  ) {
    throw new Error(
      "The two-launch owned-PID network observation proof was incomplete."
    );
  }
  const expectedAssignments = [
    {
      ...casting.narratorAssignment,
      auditionRoleType: "narrator" as const
    },
    ...casting.characterAssignments.map((assignment) => ({
      ...assignment,
      auditionRoleType: "character" as const
    }))
  ];
  const expectedAssignmentsByRole = new Map(
    expectedAssignments.map((assignment) => [assignment.roleId, assignment])
  );
  if (expectedAssignmentsByRole.size !== expectedAssignments.length) {
    throw new Error("The Phase 3A casting proof repeated a governed role.");
  }
  const observedRoleIds = new Set<string>();
  for (const audition of workflow.auditions) {
    const expected = expectedAssignmentsByRole.get(audition.roleId);
    if (
      expected === undefined ||
      audition.roleType !== expected.auditionRoleType ||
      audition.assignmentId !== expected.assignmentId ||
      audition.assignmentRevision !== expected.revision
    ) {
      throw new Error(
        "The Phase 3B audition evidence did not match the complete Phase 3A cast."
      );
    }
    observedRoleIds.add(audition.roleId);
  }
  const approvedRoleIds = workflow.gateDecisions
    .filter((decision) => decision.gateId === "per_role_audition_review")
    .map((decision) => decision.roleId);
  if (
    observedRoleIds.size !== expectedAssignmentsByRole.size ||
    expectedAssignments.some(
      (assignment) => !observedRoleIds.has(assignment.roleId)
    ) ||
    approvedRoleIds.some((roleId) => roleId === null) ||
    new Set(approvedRoleIds).size !== expectedAssignmentsByRole.size ||
    approvedRoleIds.some(
      (roleId) =>
        roleId === null || !expectedAssignmentsByRole.has(roleId)
    )
  ) {
    throw new Error(
      "The Phase 3B evidence did not cover every governed role and approval."
    );
  }
  const runtimeIdentities = [
    workflow.liveRuntimeInstance,
    restart.liveRuntimeInstance
  ];
  const processLaunches = launchEvidence.map((launch, index) => {
    const runtime = runtimeIdentities[index];
    const runtimeExit = runtimeExits[index];
    if (
      runtime === undefined ||
      runtimeExit === undefined ||
      runtimeExit.runtimeInstanceId !== runtime.runtimeInstanceId ||
      runtimeExit.workerPid !== runtime.workerPid ||
      runtimeExit.parentPid !== runtime.parentPid ||
      !launch.exitProof.graceful ||
      launch.exitProof.forcedPids.length !== 0 ||
      launch.exitProof.remainingPids.length !== 0 ||
      launch.ownership.processes.length < 3 ||
      !launch.ownership.processes.some(
        (item) =>
          item.kind === "provider_worker" &&
          item.pid === runtime.workerPid
      ) ||
      !launch.ownership.processes.some(
        (item) =>
          item.kind === "service" &&
          item.pid === runtime.parentPid
      ) ||
      launch.ownership.processes.some(
        (item) => !launch.exitProof.ownedPids.includes(item.pid)
      ) ||
      launch.exitProof.ownedPids.some(
        (pid) =>
          !launch.ownership.processes.some((item) => item.pid === pid)
      )
    ) {
      throw new Error(
        `The exact Phase 3B ownership or exit proof for launch ${launch.launch} was incomplete.`
      );
    }
    return {
      launch: launch.launch,
      ownedProcesses: launch.ownership.processes.map((item) => {
        if (
          (item.kind === "app" && item.executableName !== appExecutableName) ||
          (item.kind !== "app" &&
            item.executableName !== serviceExecutableName)
        ) {
          throw new Error(
            "A Phase 3B owned process executable identity was invalid."
          );
        }
        return {
          pid: item.pid,
          parentPid: item.parentPid,
          kind: item.kind === "app" ? "electron" as const : item.kind,
          executableName:
            item.kind === "app"
              ? "Cinematic Story Studio.exe" as const
              : "cinematic-story-service.exe" as const,
          creationIdentity: item.creationDate,
          goneAfterShutdown: true as const
        };
      }),
      providerRuntimeExit: runtimeExit,
      forcedPids: [] as const,
      remainingPids: [] as const,
      unrelatedProcessesInspected: false as const,
      unrelatedProcessesTerminated: false as const
    };
  });
  const {
    liveRuntimeInstance,
    ...restartEvidence
  } = restart;
  void liveRuntimeInstance;
  if (
    runtimeExits[0] === undefined ||
    !runtimeExitsEqual(
      restartEvidence.priorLaunchRuntimeExit,
      runtimeExits[0]
    )
  ) {
    throw new Error(
      "The persisted first-launch runtime exit did not match its shutdown sidecar."
    );
  }
  return {
    schemaVersion: phase3bPackagedE2eSchemaVersion,
    completedAt,
    status: "passed",
    evidenceClassification: phase3bFixtureEvidenceClassification,
    fixtureClaims: {
      lifecycleEvidenceOnly: true,
      naturalSpeechQualityProven: false,
      productionExportEligible: false,
      humanListeningClaimed: false
    },
    phase3b1SyntheticMetadata: phase3b1SyntheticMetadataEvidence,
    runtime: {
      ...workflow.runtime,
      runtimeInstanceIds: sortedUniqueStrings([
        ...workflow.runtime.runtimeInstanceIds,
        restart.liveRuntimeInstance.runtimeInstanceId
      ]),
      externalNetworkObservation: {
        method: "owned_pid_tcp_endpoint_inventory",
        ownedPidsOnly: true,
        observedNonLoopbackEndpointCount: 0
      }
    },
    fixtureProvider: workflow.fixtureProvider,
    realProviderAdapter: workflow.realProviderAdapter,
    model: workflow.model,
    pronunciation: workflow.pronunciation,
    auditions: workflow.auditions,
    cacheHit: workflow.cacheHit,
    targetedInvalidation: workflow.targetedInvalidation,
    gateDecisions: workflow.gateDecisions,
    restart: restartEvidence,
    process: {
      launches: processLaunches
    },
    screenshot: {
      artifactId: "packaged-ui-screenshot",
      captured: true
    },
    assertions: Object.fromEntries(
      phase3bAssertionKeys.map((key) => [key, true])
    ) as Readonly<Record<(typeof phase3bAssertionKeys)[number], true>>
  };
}

function runtimeExitsEqual(
  left: Phase3bRuntimeExitEvidence,
  right: Phase3bRuntimeExitEvidence
): boolean {
  return (
    left.runtimeInstanceId === right.runtimeInstanceId &&
    left.workerPid === right.workerPid &&
    left.parentPid === right.parentPid &&
    left.state === right.state &&
    left.stoppedAt === right.stoppedAt &&
    left.stopReasonCode === right.stopReasonCode &&
    left.handshakeAuthenticated === right.handshakeAuthenticated &&
    left.shutdownAcknowledged === right.shutdownAcknowledged &&
    left.gracefulShutdownConfirmed === right.gracefulShutdownConfirmed &&
    left.exitCode === right.exitCode &&
    left.terminatedByParent === right.terminatedByParent &&
    left.ownershipConfirmed === right.ownershipConfirmed &&
    left.confirmedExited === right.confirmedExited &&
    left.ownedProcessesConfirmedExited ===
      right.ownedProcessesConfirmedExited &&
    left.jobObjectAssigned === right.jobObjectAssigned &&
    left.deniedNetworkAttemptCount === right.deniedNetworkAttemptCount
  );
}

function sortedUniquePids(values: readonly number[]): readonly number[] {
  return [...new Set(values)].sort((left, right) => left - right);
}

function sortedUniqueStrings(values: readonly string[]): readonly string[] {
  return [...new Set(values)].sort((left, right) =>
    left.localeCompare(right)
  );
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

function requireSourceHead(value: string): string {
  if (!/^[a-f0-9]{40}$/u.test(value)) {
    throw new Error(
      `${phase3b1SourceHeadEnvironment} must be an exact lowercase Git commit SHA.`
    );
  }
  return value;
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
