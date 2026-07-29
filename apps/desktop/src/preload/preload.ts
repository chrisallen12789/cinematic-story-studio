import { contextBridge, ipcRenderer } from "electron";

import {
  DESKTOP_CONTRACT_VERSION,
  IPC_CHANNELS,
  type BackendSnapshot,
  type CinematicStoryDesktopApi,
  type CorrectSpeakerInput,
  type CreateJobInput,
  type CreateProjectInput,
  type DesktopRequest,
  type JobEventsInput,
  type JobIdInput,
  type ProjectIdInput
} from "../shared/desktop-api.js";

function request<TPayload, TResult>(
  channel: string,
  payload: TPayload
): Promise<TResult> {
  const envelope: DesktopRequest<TPayload> = {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload
  };
  return ipcRenderer.invoke(channel, envelope) as Promise<TResult>;
}

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
      request(IPC_CHANNELS.projectsImportSelectedFile, { projectId })
  },
  dialogue: {
    correctSpeaker: (input: CorrectSpeakerInput) =>
      request(IPC_CHANNELS.dialogueCorrectSpeaker, input)
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
    jobs: Object.freeze(api.jobs),
    providers: Object.freeze(api.providers),
    capabilities: Object.freeze(api.capabilities)
  })
);
