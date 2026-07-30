import path from "node:path";

import {
  dialog,
  ipcMain,
  type BrowserWindow,
  type IpcMainInvokeEvent
} from "electron";

import type {
  DeclaredImportFormat,
  ProjectDetail
} from "@cinematic-story-studio/contracts/api";

import {
  IPC_CHANNELS,
  type DesktopError,
  type DesktopResult
} from "../shared/desktop-api.js";
import type { BackendApiClient } from "./api-client.js";
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
    const detail = await options.api.openProject(request.payload.projectId);
    await options.preferences.setRecentProjectId(request.payload.projectId);
    return detail;
  });

  register(IPC_CHANNELS.projectsRestoreRecent, async (raw) => {
    parseEmptyRequest(raw);
    return restoreRecentProject(options.api, options.preferences);
  });

  register(IPC_CHANNELS.projectsImportSelectedFile, async (raw) => {
    const request = parseProjectIdRequest(raw);
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
      return null;
    }
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
    await options.preferences.setRecentProjectId(request.payload.projectId);
    return imported;
  });

  register(IPC_CHANNELS.projectsGetImportReview, async (raw) => {
    const request = parseImportReviewIdRequest(raw);
    return options.api.getImportReview(request.payload);
  });

  register(IPC_CHANNELS.projectsDecideImportReview, async (raw) => {
    const request = parseDecideImportReviewRequest(raw);
    return options.api.decideImportReview(request.payload);
  });

  register(IPC_CHANNELS.dialogueCorrectSpeaker, async (raw) => {
    const request = parseCorrectSpeakerRequest(raw);
    return options.api.correctSpeaker(request.payload);
  });

  register(IPC_CHANNELS.jobsCreate, async (raw) => {
    const request = parseCreateJobRequest(raw);
    return options.api.createJob(request.payload);
  });

  register(IPC_CHANNELS.jobsGet, async (raw) => {
    const request = parseJobIdRequest(raw);
    return options.api.getJob(request.payload.jobId);
  });

  register(IPC_CHANNELS.jobsEvents, async (raw) => {
    const request = parseJobEventsRequest(raw);
    return options.api.getJobEvents(
      request.payload.jobId,
      request.payload.afterSequence
    );
  });

  register(IPC_CHANNELS.jobsCancel, async (raw) => {
    const request = parseJobIdRequest(raw);
    return options.api.cancelJob(request.payload.jobId);
  });

  register(IPC_CHANNELS.jobsRetry, async (raw) => {
    const request = parseJobIdRequest(raw);
    return options.api.retryJob(request.payload.jobId);
  });

  register(IPC_CHANNELS.jobsResume, async (raw) => {
    const request = parseJobIdRequest(raw);
    return options.api.resumeJob(request.payload.jobId);
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
      await preferences.setRecentProjectId(null);
    }
  }
  const projects = await api.listProjects();
  const fallback = projects.items[0];
  if (fallback === undefined) {
    return null;
  }
  const detail = await api.openProject(fallback.projectId);
  await preferences.setRecentProjectId(fallback.projectId);
  return detail;
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
