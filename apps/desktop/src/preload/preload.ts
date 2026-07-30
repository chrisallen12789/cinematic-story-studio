import { contextBridge, ipcRenderer } from "electron";

import {
  DESKTOP_CONTRACT_VERSION,
  IPC_CHANNELS,
  type BackendSnapshot,
  type AnalysisRunInput,
  type CinematicStoryDesktopApi,
  type CorrectSpeakerInput,
  type DecideImportReviewInput,
  type CreateJobInput,
  type CreateProjectInput,
  type DesktopResult,
  type DesktopRequest,
  type ImportReviewIdInput,
  type JobEventsInput,
  type JobIdInput,
  type ProjectIdInput
} from "../shared/desktop-api.js";
import type {
  AppendAnalysisCorrectionInput,
  CreateAnalysisRunInput,
  DecideAnalysisReviewInput,
  ListAnalysisCorrectionsInput,
  ListAnalysisEntitiesInput,
  ListAnalysisReviewsInput,
  ListAnalysisRunsInput
} from "../shared/analysis-api.js";

const projectScopedChannels = new Set<string>([
  IPC_CHANNELS.projectsImportSelectedFile,
  IPC_CHANNELS.projectsGetImportReview,
  IPC_CHANNELS.projectsDecideImportReview,
  IPC_CHANNELS.dialogueCorrectSpeaker,
  IPC_CHANNELS.analysisCreateRun,
  IPC_CHANNELS.analysisListRuns,
  IPC_CHANNELS.analysisGetRun,
  IPC_CHANNELS.analysisListEntities,
  IPC_CHANNELS.analysisListCorrections,
  IPC_CHANNELS.analysisAppendCorrection,
  IPC_CHANNELS.analysisListReviews,
  IPC_CHANNELS.analysisDecideReview,
  IPC_CHANNELS.jobsCreate
]);

const jobScopedChannels = new Set<string>([
  IPC_CHANNELS.jobsGet,
  IPC_CHANNELS.jobsEvents,
  IPC_CHANNELS.jobsCancel,
  IPC_CHANNELS.jobsRetry,
  IPC_CHANNELS.jobsResume
]);

async function request<TPayload, TResult>(
  channel: string,
  payload: TPayload
): Promise<TResult> {
  const envelope: DesktopRequest<TPayload> = {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload
  };
  const payloadRecord = asRecord(payload);
  const selection =
    channel === IPC_CHANNELS.projectsOpen ||
    channel === IPC_CHANNELS.projectsRestoreRecent
      ? projectSession.beginSelection()
      : undefined;
  const expectedEpoch =
    selection === undefined
      ? projectScopedChannels.has(channel)
        ? projectSession.assertProject(payloadRecord.projectId)
        : jobScopedChannels.has(channel)
          ? projectSession.assertJob(payloadRecord.jobId)
          : undefined
      : undefined;
  if (expectedEpoch === null) {
    return projectContextFailure(
      "PROJECT_CONTEXT_MISMATCH",
      "The request does not belong to this window's active project."
    ) as TResult;
  }

  const result = (await ipcRenderer.invoke(channel, envelope)) as unknown;
  if (selection !== undefined) {
    if (!projectSession.selectionIsCurrent(selection)) {
      return projectContextFailure(
        "PROJECT_CONTEXT_CHANGED",
        "The active project changed before the request completed."
      ) as TResult;
    }
    const selected = desktopResultValue(result);
    if (selected !== undefined) {
      if (
        channel === IPC_CHANNELS.projectsOpen &&
        asRecord(selected).project !== undefined &&
        asRecord(asRecord(selected).project).projectId !==
          payloadRecord.projectId
      ) {
        return projectContextFailure(
          "PROJECT_CONTEXT_MISMATCH",
          "The opened project did not match the requested project."
        ) as TResult;
      }
      if (!projectSession.acceptSelection(selection, selected)) {
        return projectContextFailure(
          "PROJECT_CONTEXT_MISMATCH",
          "The selected project response contains cross-project data."
        ) as TResult;
      }
    }
    return result as TResult;
  }

  if (
    expectedEpoch !== undefined &&
    !projectSession.epochIsCurrent(expectedEpoch)
  ) {
    return projectContextFailure(
      "PROJECT_CONTEXT_CHANGED",
      "The active project changed before the request completed."
    ) as TResult;
  }
  const responseValue = desktopResultValue(result);
  if (responseValue !== undefined) {
    const job = asRecord(asRecord(responseValue).job);
    if (typeof job.jobId === "string") {
      if (
        typeof job.projectId === "string" &&
        !projectSession.isActiveProject(job.projectId)
      ) {
        return projectContextFailure(
          "PROJECT_CONTEXT_MISMATCH",
          "The returned job does not belong to the active project."
        ) as TResult;
      }
      projectSession.rememberJob(job.jobId);
    }
    const run = asRecord(asRecord(responseValue).run);
    if (typeof run.jobId === "string") {
      projectSession.rememberJob(run.jobId);
    }
  }
  return result as TResult;
}

class PreloadProjectSession {
  #epoch = 0;
  #projectId: string | null = null;
  #jobIds = new Set<string>();

  beginSelection(): number {
    this.#epoch += 1;
    this.#projectId = null;
    this.#jobIds.clear();
    return this.#epoch;
  }

  selectionIsCurrent(epoch: number): boolean {
    return epoch === this.#epoch;
  }

  acceptSelection(epoch: number, value: unknown): boolean {
    if (!this.selectionIsCurrent(epoch)) {
      return false;
    }
    const detail = asRecord(value);
    if (value === null) {
      this.#projectId = null;
      this.#jobIds.clear();
      return true;
    }
    const project = asRecord(detail.project);
    const projectId =
      typeof project.projectId === "string" && project.projectId.length > 0
        ? project.projectId
        : null;
    if (projectId === null) {
      this.#projectId = null;
      this.#jobIds.clear();
      return false;
    }
    const jobs = Array.isArray(detail.jobs) ? detail.jobs : [];
    const run =
      detail.currentAnalysisRun === null
        ? null
        : asRecord(detail.currentAnalysisRun);
    if (
      jobs.some((jobValue) => {
        const job = asRecord(jobValue);
        return (
          typeof job.jobId !== "string" ||
          typeof job.projectId !== "string" ||
          job.projectId !== projectId
        );
      }) ||
      (run !== null &&
        (typeof run.jobId !== "string" ||
          typeof run.projectId !== "string" ||
          run.projectId !== projectId))
    ) {
      this.#projectId = null;
      this.#jobIds.clear();
      return false;
    }
    this.#projectId = projectId;
    this.#jobIds.clear();
    for (const jobValue of jobs) {
      const job = asRecord(jobValue);
      this.#jobIds.add(job.jobId as string);
    }
    if (run !== null) {
      this.#jobIds.add(run.jobId as string);
    }
    return true;
  }

  assertProject(value: unknown): number | null {
    return typeof value === "string" && value === this.#projectId
      ? this.#epoch
      : null;
  }

  assertJob(value: unknown): number | null {
    return typeof value === "string" &&
      this.#projectId !== null &&
      this.#jobIds.has(value)
      ? this.#epoch
      : null;
  }

  epochIsCurrent(epoch: number): boolean {
    return epoch === this.#epoch;
  }

  isActiveProject(projectId: string): boolean {
    return projectId === this.#projectId;
  }

  rememberJob(jobId: string): void {
    if (this.#projectId !== null) {
      this.#jobIds.add(jobId);
    }
  }
}

function desktopResultValue(value: unknown): unknown {
  const result = asRecord(value);
  return result.ok === true && "value" in result
    ? result.value
    : undefined;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function projectContextFailure(
  code: string,
  message: string
): DesktopResult<never> {
  return {
    ok: false,
    error: {
      code,
      message,
      retryable: false
    }
  };
}

const projectSession = new PreloadProjectSession();

const api: CinematicStoryDesktopApi = {
  version: DESKTOP_CONTRACT_VERSION,
  backend: {
    getStatus: () =>
      request(IPC_CHANNELS.backendGetStatus, Object.freeze({})),
    reconnect: () =>
      request(IPC_CHANNELS.backendReconnect, Object.freeze({})),
    onStatus: (listener) => {
      const wrapped = (_event: Electron.IpcRendererEvent, value: unknown) => {
        listener(value as BackendSnapshot);
      };
      ipcRenderer.on(IPC_CHANNELS.backendStatus, wrapped);
      return () => {
        ipcRenderer.removeListener(IPC_CHANNELS.backendStatus, wrapped);
      };
    }
  },
  projects: {
    list: () => request(IPC_CHANNELS.projectsList, Object.freeze({})),
    create: (input: CreateProjectInput) =>
      request(IPC_CHANNELS.projectsCreate, input),
    open: (projectId: string) =>
      request<
        ProjectIdInput,
        Awaited<ReturnType<CinematicStoryDesktopApi["projects"]["open"]>>
      >(
        IPC_CHANNELS.projectsOpen,
        { projectId }
      ),
    restoreRecent: () =>
      request(IPC_CHANNELS.projectsRestoreRecent, Object.freeze({})),
    importSelectedFile: (projectId: string) =>
      request(IPC_CHANNELS.projectsImportSelectedFile, { projectId }),
    getImportReview: (input: ImportReviewIdInput) =>
      request<
        ImportReviewIdInput,
        Awaited<
          ReturnType<CinematicStoryDesktopApi["projects"]["getImportReview"]>
        >
      >(IPC_CHANNELS.projectsGetImportReview, input),
    decideImportReview: (input: DecideImportReviewInput) =>
      request(IPC_CHANNELS.projectsDecideImportReview, input)
  },
  dialogue: {
    correctSpeaker: (input: CorrectSpeakerInput) =>
      request(IPC_CHANNELS.dialogueCorrectSpeaker, input)
  },
  analysis: {
    createRun: (input: CreateAnalysisRunInput) =>
      request(IPC_CHANNELS.analysisCreateRun, input),
    listRuns: (input: ListAnalysisRunsInput) =>
      request<
        ListAnalysisRunsInput,
        Awaited<
          ReturnType<CinematicStoryDesktopApi["analysis"]["listRuns"]>
        >
      >(IPC_CHANNELS.analysisListRuns, input),
    getRun: (input: AnalysisRunInput) =>
      request(IPC_CHANNELS.analysisGetRun, input),
    listEntities: (input: ListAnalysisEntitiesInput) =>
      request(IPC_CHANNELS.analysisListEntities, input),
    listCorrections: (input: ListAnalysisCorrectionsInput) =>
      request(IPC_CHANNELS.analysisListCorrections, input),
    appendCorrection: (input: AppendAnalysisCorrectionInput) =>
      request(IPC_CHANNELS.analysisAppendCorrection, input),
    listReviews: (input: ListAnalysisReviewsInput) =>
      request(IPC_CHANNELS.analysisListReviews, input),
    decideReview: (input: DecideAnalysisReviewInput) =>
      request(IPC_CHANNELS.analysisDecideReview, input)
  },
  jobs: {
    create: (input: CreateJobInput) =>
      request(IPC_CHANNELS.jobsCreate, input),
    get: (jobId: string) =>
      request<
        JobIdInput,
        Awaited<ReturnType<CinematicStoryDesktopApi["jobs"]["get"]>>
      >(
        IPC_CHANNELS.jobsGet,
        { jobId }
      ),
    events: (jobId: string, afterSequence?: number) =>
      request<
        JobEventsInput,
        Awaited<ReturnType<CinematicStoryDesktopApi["jobs"]["events"]>>
      >(IPC_CHANNELS.jobsEvents, { jobId, afterSequence }),
    cancel: (jobId: string) =>
      request<
        JobIdInput,
        Awaited<ReturnType<CinematicStoryDesktopApi["jobs"]["cancel"]>>
      >(
        IPC_CHANNELS.jobsCancel,
        { jobId }
      ),
    retry: (jobId: string) =>
      request<
        JobIdInput,
        Awaited<ReturnType<CinematicStoryDesktopApi["jobs"]["retry"]>>
      >(
        IPC_CHANNELS.jobsRetry,
        { jobId }
      ),
    resume: (jobId: string) =>
      request<
        JobIdInput,
        Awaited<ReturnType<CinematicStoryDesktopApi["jobs"]["resume"]>>
      >(
        IPC_CHANNELS.jobsResume,
        { jobId }
      )
  },
  providers: {
    health: () => request(IPC_CHANNELS.providersHealth, Object.freeze({}))
  },
  capabilities: {
    ffmpeg: () => request(IPC_CHANNELS.ffmpegCapability, Object.freeze({}))
  }
};

contextBridge.exposeInMainWorld(
  "cinematicStory",
  Object.freeze({
    ...api,
    backend: Object.freeze(api.backend),
    projects: Object.freeze(api.projects),
    dialogue: Object.freeze(api.dialogue),
    analysis: Object.freeze(api.analysis),
    jobs: Object.freeze(api.jobs),
    providers: Object.freeze(api.providers),
    capabilities: Object.freeze(api.capabilities)
  })
);
