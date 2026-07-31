import path from "node:path";

import {
  dialog,
  ipcMain,
  type BrowserWindow,
  type IpcMainInvokeEvent
} from "electron";

import type {
  DeclaredImportFormat,
  Job,
  ProjectDetail
} from "@cinematic-story-studio/contracts/api";
import type { StoryAnalysisRun } from "@cinematic-story-studio/contracts";
import type { CastingRun } from "../shared/casting-api.js";

import {
  IPC_CHANNELS,
  type DesktopError,
  type DesktopResult
} from "../shared/desktop-api.js";
import type {
  BackendApiClient,
  JobResponseExpectation
} from "./api-client.js";
import {
  parseAnalysisRunRequest,
  parseAppendAnalysisCorrectionRequest,
  parseCreateAnalysisRunRequest,
  parseDecideAnalysisReviewRequest,
  parseListAnalysisCorrectionsRequest,
  parseListAnalysisEntitiesRequest,
  parseListAnalysisReviewsRequest,
  parseListAnalysisRunsRequest
} from "./analysis-validation.js";
import {
  parseAppendCastingCorrectionRequest,
  parseCastingRunRequest,
  parseCreateCastingRunRequest,
  parseCreateCustomProductionRoleRequest,
  parseDecideCastingReviewRequest,
  parseListCastAssignmentsRequest,
  parseListCastingCandidatesRequest,
  parseListCastingConflictsRequest,
  parseListCastingCorrectionsRequest,
  parseListCastingReviewsRequest,
  parseListCastingRunsRequest,
  parseListProductionRolesRequest,
  parseListVoiceCatalogRequest
} from "./casting-validation.js";
import { DesktopMainError } from "./errors.js";
import type { PreferenceStore } from "./preferences.js";
import type { ServiceManager } from "./service-manager.js";
import {
  parseCorrectSpeakerRequest,
  parseCreateJobRequest,
  parseCreateProjectRequest,
  parseDecideImportReviewRequest,
  parseEmptyRequest,
  parseImportReviewIdRequest,
  parseJobEventsRequest,
  parseJobIdRequest,
  parseProjectIdRequest,
  ValidationError
} from "./validation.js";

interface DesktopIpcOptions {
  readonly window: BrowserWindow;
  readonly service: ServiceManager;
  readonly api: BackendApiClient;
  readonly preferences: PreferenceStore;
}

export function registerDesktopIpc(options: DesktopIpcOptions): () => void {
  const registeredChannels: string[] = [];
  const projectSession = new ActiveProjectSession();
  const recentProjectPreferences =
    new RecentProjectPreferenceCommitQueue(
      options.preferences,
      projectSession
    );

  register(IPC_CHANNELS.backendGetStatus, (raw) => {
    parseEmptyRequest(raw);
    return options.service.snapshot;
  });

  register(IPC_CHANNELS.backendReconnect, async (raw) => {
    parseEmptyRequest(raw);
    return options.service.reconnect();
  });

  register(IPC_CHANNELS.projectsList, async (raw) => {
    parseEmptyRequest(raw);
    return options.api.listProjects();
  });

  register(IPC_CHANNELS.projectsCreate, async (raw) => {
    const request = parseCreateProjectRequest(raw);
    return options.api.createProject(request.payload);
  });

  register(IPC_CHANNELS.projectsOpen, async (raw) => {
    const request = parseProjectIdRequest(raw);
    const selection = projectSession.beginSelection();
    const detail = await options.api.openProject(request.payload.projectId);
    projectSession.acceptSelection(selection, detail);
    projectSession.assertUnchanged(request.payload.projectId, selection);
    await recentProjectPreferences.commit(
      selection,
      request.payload.projectId
    );
    return detail;
  });

  register(IPC_CHANNELS.projectsRestoreRecent, async (raw) => {
    parseEmptyRequest(raw);
    const selection = projectSession.beginSelection();
    const detail = await restoreRecentProject(
      options.api,
      options.preferences
    );
    projectSession.acceptSelection(selection, detail);
    await recentProjectPreferences.commit(
      selection,
      detail?.project.projectId ?? null
    );
    return detail;
  });

  register(IPC_CHANNELS.projectsImportSelectedFile, async (raw) => {
    const request = parseProjectIdRequest(raw);
    const epoch = projectSession.assertActive(request.payload.projectId);
    const selection = await dialog.showOpenDialog(options.window, {
      title: "Import a story",
      buttonLabel: "Import story",
      properties: ["openFile", "dontAddToRecent"],
      filters: [
        {
          name: "Story documents",
          extensions: ["txt", "md", "markdown", "docx", "epub", "pdf"]
        }
      ]
    });
    if (selection.canceled || selection.filePaths.length !== 1) {
      projectSession.assertUnchanged(
        request.payload.projectId,
        epoch
      );
      return null;
    }
    projectSession.assertUnchanged(request.payload.projectId, epoch);
    const selectedPath = selection.filePaths[0];
    if (selectedPath === undefined || selectedPath.length > 4_096) {
      throw new ValidationError("The selected file path was invalid.");
    }
    const extension = path.extname(selectedPath).toLowerCase();
    const declaredFormat = declaredImportFormat(extension);
    const imported = await options.api.importSelectedFile(
      request.payload.projectId,
      selectedPath,
      declaredFormat
    );
    projectSession.assertUnchanged(request.payload.projectId, epoch);
    projectSession.rememberJob(imported.job);
    await recentProjectPreferences.commit(
      epoch,
      request.payload.projectId
    );
    return imported;
  });

  register(IPC_CHANNELS.projectsGetImportReview, async (raw) => {
    const request = parseImportReviewIdRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.getImportReview(request.payload)
    );
  });

  register(IPC_CHANNELS.projectsDecideImportReview, async (raw) => {
    const request = parseDecideImportReviewRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.decideImportReview(request.payload)
    );
  });

  register(IPC_CHANNELS.analysisCreateRun, async (raw) => {
    const request = parseCreateAnalysisRunRequest(raw);
    const response = await forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.createAnalysisRun(request.payload)
    );
    projectSession.rememberJob(response.job);
    return response;
  });

  register(IPC_CHANNELS.analysisListRuns, async (raw) => {
    const request = parseListAnalysisRunsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listAnalysisRuns(request.payload)
    );
  });

  register(IPC_CHANNELS.analysisGetRun, async (raw) => {
    const request = parseAnalysisRunRequest(raw);
    const response = await forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.getAnalysisRun(request.payload)
    );
    projectSession.rememberAnalysisRun(response.run);
    return response;
  });

  register(IPC_CHANNELS.analysisListEntities, async (raw) => {
    const request = parseListAnalysisEntitiesRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listAnalysisEntities(request.payload)
    );
  });

  register(IPC_CHANNELS.analysisListCorrections, async (raw) => {
    const request = parseListAnalysisCorrectionsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listAnalysisCorrections(request.payload)
    );
  });

  register(IPC_CHANNELS.analysisAppendCorrection, async (raw) => {
    const request = parseAppendAnalysisCorrectionRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.appendAnalysisCorrection(request.payload)
    );
  });

  register(IPC_CHANNELS.analysisListReviews, async (raw) => {
    const request = parseListAnalysisReviewsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listAnalysisReviews(request.payload)
    );
  });

  register(IPC_CHANNELS.analysisDecideReview, async (raw) => {
    const request = parseDecideAnalysisReviewRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.decideAnalysisReview(request.payload)
    );
  });

  register(IPC_CHANNELS.castingGetCatalog, async (raw) => {
    const request = parseListVoiceCatalogRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.getVoiceCatalog(request.payload)
    );
  });

  register(IPC_CHANNELS.castingCreateRun, async (raw) => {
    const request = parseCreateCastingRunRequest(raw);
    const response = await forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.createCastingRun(request.payload)
    );
    projectSession.rememberJob(response.job);
    projectSession.rememberCastingRun(response.run);
    return response;
  });

  register(IPC_CHANNELS.castingListRuns, async (raw) => {
    const request = parseListCastingRunsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listCastingRuns(request.payload)
    );
  });

  register(IPC_CHANNELS.castingGetRun, async (raw) => {
    const request = parseCastingRunRequest(raw);
    const response = await forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.getCastingRun(request.payload)
    );
    projectSession.rememberCastingRun(response.run);
    return response;
  });

  register(IPC_CHANNELS.castingListRoles, async (raw) => {
    const request = parseListProductionRolesRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listProductionRoles(request.payload)
    );
  });

  register(IPC_CHANNELS.castingCreateCustomRole, async (raw) => {
    const request = parseCreateCustomProductionRoleRequest(raw);
    const response = await forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.createCustomProductionRole(request.payload)
    );
    projectSession.rememberCastingRun(response.run);
    return response;
  });

  register(IPC_CHANNELS.castingListCandidates, async (raw) => {
    const request = parseListCastingCandidatesRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listCastingCandidates(request.payload)
    );
  });

  register(IPC_CHANNELS.castingListConflicts, async (raw) => {
    const request = parseListCastingConflictsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listCastingConflicts(request.payload)
    );
  });

  register(IPC_CHANNELS.castingListAssignments, async (raw) => {
    const request = parseListCastAssignmentsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listCastAssignments(request.payload)
    );
  });

  register(IPC_CHANNELS.castingListCorrections, async (raw) => {
    const request = parseListCastingCorrectionsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listCastingCorrections(request.payload)
    );
  });

  register(IPC_CHANNELS.castingAppendCorrection, async (raw) => {
    const request = parseAppendCastingCorrectionRequest(raw);
    const response = await forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.appendCastingCorrection(request.payload)
    );
    projectSession.rememberCastingRun(response.run);
    return response;
  });

  register(IPC_CHANNELS.castingListReviews, async (raw) => {
    const request = parseListCastingReviewsRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.listCastingReviews(request.payload)
    );
  });

  register(IPC_CHANNELS.castingDecideReview, async (raw) => {
    const request = parseDecideCastingReviewRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.decideCastingReview(request.payload)
    );
  });

  register(IPC_CHANNELS.dialogueCorrectSpeaker, async (raw) => {
    const request = parseCorrectSpeakerRequest(raw);
    return forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.correctSpeaker(request.payload)
    );
  });

  register(IPC_CHANNELS.jobsCreate, async (raw) => {
    const request = parseCreateJobRequest(raw);
    const response = await forActiveProject(
      projectSession,
      request.payload.projectId,
      () => options.api.createJob(request.payload)
    );
    projectSession.rememberJob(response.job);
    return response;
  });

  register(IPC_CHANNELS.jobsGet, async (raw) => {
    const request = parseJobIdRequest(raw);
    const { epoch, expected } = projectSession.captureKnownJob(
      request.payload.jobId
    );
    const response = await options.api.getJob(
      request.payload.jobId,
      expected
    );
    projectSession.assertKnownJob(request.payload.jobId, epoch);
    projectSession.assertJobProject(response.job.projectId);
    projectSession.rememberJob(response.job);
    return response;
  });

  register(IPC_CHANNELS.jobsEvents, async (raw) => {
    const request = parseJobEventsRequest(raw);
    const epoch = projectSession.assertKnownJob(request.payload.jobId);
    const response = await options.api.getJobEvents(
      request.payload.jobId,
      request.payload.afterSequence
    );
    projectSession.assertKnownJob(request.payload.jobId, epoch);
    return response;
  });

  register(IPC_CHANNELS.jobsCancel, async (raw) => {
    const request = parseJobIdRequest(raw);
    return forKnownJob(projectSession, request.payload.jobId, (expected) =>
      options.api.cancelJob(request.payload.jobId, expected)
    );
  });

  register(IPC_CHANNELS.jobsRetry, async (raw) => {
    const request = parseJobIdRequest(raw);
    return forKnownJob(projectSession, request.payload.jobId, (expected) =>
      options.api.retryJob(request.payload.jobId, expected)
    );
  });

  register(IPC_CHANNELS.jobsResume, async (raw) => {
    const request = parseJobIdRequest(raw);
    return forKnownJob(projectSession, request.payload.jobId, (expected) =>
      options.api.resumeJob(request.payload.jobId, expected)
    );
  });

  register(IPC_CHANNELS.providersHealth, async (raw) => {
    parseEmptyRequest(raw);
    return options.api.providerHealth();
  });

  register(IPC_CHANNELS.ffmpegCapability, async (raw) => {
    parseEmptyRequest(raw);
    return options.api.ffmpegCapability();
  });

  const unsubscribeStatus = options.service.onStatus((snapshot) => {
    if (!options.window.isDestroyed()) {
      options.window.webContents.send(IPC_CHANNELS.backendStatus, snapshot);
    }
  });

  return () => {
    unsubscribeStatus();
    for (const channel of registeredChannels) {
      ipcMain.removeHandler(channel);
    }
  };

  function register(
    channel: string,
    operation: (raw: unknown) => unknown
  ): void {
    registeredChannels.push(channel);
    ipcMain.handle(channel, async (event, raw: unknown) => {
      try {
        assertTrustedSender(event, options.window);
        return success(await operation(raw));
      } catch (error) {
        return failure(toDesktopError(error));
      }
    });
  }
}

function declaredImportFormat(extension: string): DeclaredImportFormat {
  switch (extension) {
    case ".txt":
      return "txt";
    case ".md":
    case ".markdown":
      return "markdown";
    case ".docx":
      return "docx";
    case ".epub":
      return "epub";
    case ".pdf":
      return "pdf";
    default:
      throw new ValidationError(
        "Only TXT, Markdown, DOCX, EPUB, and text-based PDF files are supported."
      );
  }
}

async function restoreRecentProject(
  api: BackendApiClient,
  preferences: PreferenceStore
): Promise<ProjectDetail | null> {
  const recentProjectId = await preferences.getRecentProjectId();
  if (recentProjectId !== null) {
    try {
      return await api.openProject(recentProjectId);
    } catch (error) {
      if (
        error instanceof DesktopMainError &&
        error.code === "BACKEND_UNAVAILABLE"
      ) {
        throw error;
      }
    }
  }
  const projects = await api.listProjects();
  const fallback = projects.items[0];
  if (fallback === undefined) {
    return null;
  }
  return api.openProject(fallback.projectId);
}

class RecentProjectPreferenceCommitQueue {
  #tail: Promise<void> = Promise.resolve();

  constructor(
    private readonly preferences: PreferenceStore,
    private readonly projectSession: ActiveProjectSession
  ) {}

  commit(epoch: number, projectId: string | null): Promise<void> {
    const operation = this.#tail.then(async () => {
      this.projectSession.assertSelection(epoch, projectId);
      await this.preferences.setRecentProjectId(projectId);
      this.projectSession.assertSelection(epoch, projectId);
    });
    this.#tail = operation.catch(() => undefined);
    return operation;
  }
}

export class ActiveProjectSession {
  #epoch = 0;
  #projectId: string | null = null;
  #jobs = new Map<string, JobResponseExpectation>();

  beginSelection(): number {
    this.#epoch += 1;
    this.#projectId = null;
    this.#jobs.clear();
    return this.#epoch;
  }

  acceptSelection(epoch: number, detail: ProjectDetail | null): void {
    if (epoch !== this.#epoch) {
      throw projectContextChanged();
    }
    this.#jobs.clear();
    if (detail === null) {
      this.#projectId = null;
      return;
    }
    const projectId = detail.project.projectId;
    for (const job of detail.jobs) {
      if (job.projectId !== projectId) {
        this.#projectId = null;
        throw new DesktopMainError(
          "PROJECT_CONTEXT_MISMATCH",
          "The selected project contains a job owned by another project.",
          false
        );
      }
    }
    if (
      detail.currentAnalysisRun !== null &&
      detail.currentAnalysisRun.projectId !== projectId
    ) {
      this.#projectId = null;
      throw new DesktopMainError(
        "PROJECT_CONTEXT_MISMATCH",
        "The selected project contains an analysis run owned by another project.",
        false
      );
    }
    this.#projectId = projectId;
    for (const job of detail.jobs) {
      this.#jobs.set(job.jobId, jobExpectation(job));
    }
    if (detail.currentAnalysisRun !== null) {
      this.rememberAnalysisRun(detail.currentAnalysisRun);
    }
  }

  assertActive(projectId: string): number {
    if (this.#projectId === null || this.#projectId !== projectId) {
      throw new DesktopMainError(
        "PROJECT_CONTEXT_MISMATCH",
        "The request does not belong to this window's active project.",
        false
      );
    }
    return this.#epoch;
  }

  assertUnchanged(projectId: string, epoch: number): void {
    this.assertSelection(epoch, projectId);
  }

  assertSelection(epoch: number, projectId: string | null): void {
    if (epoch !== this.#epoch || projectId !== this.#projectId) {
      throw projectContextChanged();
    }
  }

  rememberJob(job: Job): void {
    this.assertJobProject(job.projectId);
    this.#jobs.set(job.jobId, jobExpectation(job));
  }

  rememberAnalysisRun(run: StoryAnalysisRun): void {
    this.assertJobProject(run.projectId);
    const expected: JobResponseExpectation = {
      jobId: run.jobId,
      projectId: run.projectId,
      type: "analyze_whole_book",
      inputRevision: run.storyRevision,
      inputFingerprint: run.storyFingerprint
    };
    const known = this.#jobs.get(run.jobId);
    if (known !== undefined && !sameJobExpectation(known, expected)) {
      throw new DesktopMainError(
        "PROJECT_CONTEXT_MISMATCH",
        "The analysis run and job projection are inconsistent.",
        false
      );
    }
    this.#jobs.set(run.jobId, expected);
  }

  rememberCastingRun(run: CastingRun): void {
    this.assertJobProject(run.projectId);
    const known = this.#jobs.get(run.jobId);
    if (
      known !== undefined &&
      (known.jobId !== run.jobId || known.projectId !== run.projectId)
    ) {
      throw new DesktopMainError(
        "PROJECT_CONTEXT_MISMATCH",
        "The casting run and job projection are inconsistent.",
        false
      );
    }
    if (known === undefined) {
      this.#jobs.set(run.jobId, {
        jobId: run.jobId,
        projectId: run.projectId
      });
    }
  }

  captureKnownJob(jobId: string): {
    readonly epoch: number;
    readonly expected: JobResponseExpectation;
  } {
    const epoch = this.assertKnownJob(jobId);
    const expected = this.#jobs.get(jobId);
    if (expected === undefined) {
      throw projectContextChanged();
    }
    return { epoch, expected };
  }

  assertKnownJob(jobId: string, expectedEpoch?: number): number {
    if (
      this.#projectId === null ||
      !this.#jobs.has(jobId) ||
      (expectedEpoch !== undefined && expectedEpoch !== this.#epoch)
    ) {
      throw new DesktopMainError(
        "PROJECT_CONTEXT_MISMATCH",
        "The job does not belong to this window's active project.",
        false
      );
    }
    return this.#epoch;
  }

  assertJobProject(projectId: string): void {
    this.assertActive(projectId);
  }
}

async function forActiveProject<TResult>(
  session: ActiveProjectSession,
  projectId: string,
  operation: () => Promise<TResult>
): Promise<TResult> {
  const epoch = session.assertActive(projectId);
  const result = await operation();
  session.assertUnchanged(projectId, epoch);
  return result;
}

async function forKnownJob<
  TResult extends { readonly job: Job }
>(
  session: ActiveProjectSession,
  jobId: string,
  operation: (expected: JobResponseExpectation) => Promise<TResult>
): Promise<TResult> {
  const { epoch, expected } = session.captureKnownJob(jobId);
  const result = await operation(expected);
  session.assertKnownJob(jobId, epoch);
  session.assertJobProject(result.job.projectId);
  session.rememberJob(result.job);
  return result;
}

function jobExpectation(job: Job): JobResponseExpectation {
  return {
    jobId: job.jobId,
    projectId: job.projectId,
    type: job.type,
    inputRevision: job.inputRevision,
    inputFingerprint: job.inputFingerprint
  };
}

function sameJobExpectation(
  left: JobResponseExpectation,
  right: JobResponseExpectation
): boolean {
  return (
    left.jobId === right.jobId &&
    left.projectId === right.projectId &&
    left.type === right.type &&
    left.inputRevision === right.inputRevision &&
    left.inputFingerprint === right.inputFingerprint
  );
}

function projectContextChanged(): DesktopMainError {
  return new DesktopMainError(
    "PROJECT_CONTEXT_CHANGED",
    "The active project changed before the request completed.",
    false
  );
}

function assertTrustedSender(
  event: IpcMainInvokeEvent,
  window: BrowserWindow
): void {
  if (
    window.isDestroyed() ||
    event.sender.id !== window.webContents.id ||
    event.senderFrame !== window.webContents.mainFrame
  ) {
    throw new DesktopMainError(
      "UNTRUSTED_RENDERER",
      "The desktop request did not originate from the application window.",
      false
    );
  }
}

function success<T>(value: T): DesktopResult<T> {
  return { ok: true, value };
}

function failure(error: DesktopError): DesktopResult<never> {
  return { ok: false, error };
}

function toDesktopError(error: unknown): DesktopError {
  if (error instanceof DesktopMainError) {
    return error.toDesktopError();
  }
  if (error instanceof ValidationError) {
    return {
      code: error.code,
      message: error.message,
      retryable: false
    };
  }
  return {
    code: "DESKTOP_OPERATION_FAILED",
    message: "The desktop operation could not be completed.",
    retryable: false
  };
}
