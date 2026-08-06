import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  app,
  BrowserWindow,
  Menu,
  session,
  shell,
  type WebContents
} from "electron";

import { BackendApiClient } from "./api-client.js";
import { registerDesktopIpc } from "./ipc.js";
import { PreferenceStore } from "./preferences.js";
import { rendererContentSecurityPolicy } from "./security-policy.js";
import {
  ServiceManager,
  type ServiceStopResult
} from "./service-manager.js";
import {
  createPackagedServiceShutdownEvidence,
  packagedShutdownEvidenceEnvironment,
  resolvePackagedShutdownEvidencePath,
  writePackagedServiceShutdownEvidence
} from "./shutdown-evidence.js";

const mainDirectory = path.dirname(fileURLToPath(import.meta.url));
const developmentUrl = "http://127.0.0.1:5173";
const allowedExternalHosts = new Set(["cinematicstorystudio.com"]);

let mainWindow: BrowserWindow | null = null;
let service: ServiceManager | null = null;
let unregisterIpc: (() => void) | null = null;
let shutdownStarted = false;
let shutdownComplete = false;

app.enableSandbox();
app.setAppUserModelId("com.cinematicstorystudio.desktop");

if (app.isPackaged) {
  const localAppData =
    safeDevelopmentDataPath(process.env.LOCALAPPDATA) ??
    path.resolve(app.getPath("appData"), "..", "Local");
  app.setPath(
    "userData",
    path.join(localAppData, "Cinematic Story Studio")
  );
} else {
  const e2eDataPath = safeDevelopmentDataPath(process.env.CSS_E2E_DATA_DIR);
  if (e2eDataPath !== null) {
    app.setPath("userData", e2eDataPath);
  }
}

const packagedShutdownEvidencePath = app.isPackaged
  ? resolvePackagedShutdownEvidencePath(
      process.env[packagedShutdownEvidenceEnvironment],
      app.getPath("userData")
    )
  : null;

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow !== null) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.show();
      mainWindow.focus();
    }
  });

  void app.whenReady().then(async () => {
    configureSessionSecurity();
    Menu.setApplicationMenu(null);
    mainWindow = createWindow();
    service = new ServiceManager({
      isPackaged: app.isPackaged,
      appPath: app.getAppPath(),
      resourcesPath: process.resourcesPath,
      userDataPath: app.getPath("userData")
    });
    const api = new BackendApiClient(service);
    const preferences = new PreferenceStore(app.getPath("userData"));
    unregisterIpc = registerDesktopIpc({
      window: mainWindow,
      service,
      api,
      preferences
    });

    await loadRenderer(mainWindow);
    void service.start().catch(() => undefined);
  });
}

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && app.isReady()) {
    mainWindow = createWindow();
    void loadRenderer(mainWindow);
  }
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (shutdownComplete) {
    return;
  }
  event.preventDefault();
  if (shutdownStarted) {
    return;
  }
  shutdownStarted = true;
  unregisterIpc?.();
  unregisterIpc = null;
  void completeShutdown();
});

async function completeShutdown(): Promise<void> {
  let stopResult: ServiceStopResult | null = null;
  let shutdownFailed = false;
  try {
    stopResult = service === null ? null : await service.stop(true);
  } catch {
    shutdownFailed = true;
  }
  if (
    stopResult !== null &&
    (stopResult.forceKillUsed ||
      !stopResult.allProcessesExitedGracefully)
  ) {
    shutdownFailed = true;
  }
  if (packagedShutdownEvidencePath !== null) {
    try {
      await writePackagedServiceShutdownEvidence(
        packagedShutdownEvidencePath,
        createPackagedServiceShutdownEvidence(stopResult)
      );
    } catch {
      shutdownFailed = true;
    }
  }
  if (shutdownFailed) {
    process.exitCode = 1;
  }
  shutdownComplete = true;
  app.quit();
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    title: "Cinematic Story Studio",
    width: 1440,
    height: 920,
    minWidth: 1040,
    minHeight: 700,
    show: false,
    backgroundColor: "#0e1118",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(mainDirectory, "../preload/preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      devTools: !app.isPackaged,
      spellcheck: true
    }
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternalUrl(url)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (url !== window.webContents.getURL()) {
      event.preventDefault();
    }
  });
  window.webContents.on("will-attach-webview", (event) => {
    event.preventDefault();
  });
  window.once("ready-to-show", () => {
    window.show();
  });
  window.on("closed", () => {
    if (mainWindow === window) {
      mainWindow = null;
    }
  });
  return window;
}

async function loadRenderer(window: BrowserWindow): Promise<void> {
  if (
    !app.isPackaged &&
    process.env.CSS_DESKTOP_DEV_URL === developmentUrl
  ) {
    await window.loadURL(developmentUrl);
    return;
  }
  await window.loadFile(path.join(mainDirectory, "../renderer/index.html"));
}

function configureSessionSecurity(): void {
  const rendererSession = session.defaultSession;
  rendererSession.setPermissionCheckHandler(() => false);
  rendererSession.setPermissionRequestHandler((_webContents, _permission, reply) => {
    reply(false);
  });
  rendererSession.webRequest.onHeadersReceived((details, callback) => {
    const policy = rendererContentSecurityPolicy(app.isPackaged);
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [policy],
        "X-Content-Type-Options": ["nosniff"],
        "Referrer-Policy": ["no-referrer"]
      }
    });
  });
}

function isAllowedExternalUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      allowedExternalHosts.has(url.hostname) &&
      url.username.length === 0 &&
      url.password.length === 0
    );
  } catch {
    return false;
  }
}

function safeDevelopmentDataPath(value: string | undefined): string | null {
  if (
    value === undefined ||
    value.length === 0 ||
    value.length > 2_048 ||
    value.includes("\0") ||
    !path.isAbsolute(value)
  ) {
    return null;
  }
  return path.resolve(value);
}

function preventUnexpectedDownloads(contents: WebContents): void {
  contents.session.on("will-download", (event) => {
    event.preventDefault();
  });
}

app.on("web-contents-created", (_event, contents) => {
  preventUnexpectedDownloads(contents);
});
