// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectDetail } from "@cinematic-story-studio/contracts/api";

const electronMocks = vi.hoisted(() => {
  type Handler = (event: unknown, raw: unknown) => unknown;
  const handlers = new Map<string, Handler>();
  return {
    handlers,
    dialog: {
      showOpenDialog: vi.fn()
    },
    ipcMain: {
      handle: vi.fn((channel: string, handler: Handler) => {
        handlers.set(channel, handler);
      }),
      removeHandler: vi.fn((channel: string) => {
        handlers.delete(channel);
      })
    }
  };
});

vi.mock("electron", () => ({
  dialog: electronMocks.dialog,
  ipcMain: electronMocks.ipcMain
}));

import { IPC_CHANNELS } from "../shared/desktop-api";
import { CASTING_PROFILE_FINGERPRINT } from "../shared/casting-api";
import type { BackendApiClient } from "./api-client";
import { DesktopMainError } from "./errors";
import { ActiveProjectSession, registerDesktopIpc } from "./ipc";
import type { PreferenceStore } from "./preferences";
import type { ServiceManager } from "./service-manager";

beforeEach(() => {
  electronMocks.handlers.clear();
  electronMocks.dialog.showOpenDialog.mockReset();
  electronMocks.ipcMain.handle.mockClear();
  electronMocks.ipcMain.removeHandler.mockClear();
});

describe("desktop main active project session", () => {
  it("keeps the selected model ZIP path in main and forwards only a bounded upload", async () => {
    const applySelectedLocalModelPackage = vi.fn(async () => ({
      correlationId: "correlation-model-install",
      installation: { modelPackageId: "kokoro-package-1" },
      verification: null
    }));
    const api = {
      openProject: vi.fn(async () =>
        projectDetail("project-a", "job-project-a")
      ),
      applySelectedLocalModelPackage
    } as unknown as BackendApiClient;
    const harness = registerHarness(api, preferenceStore());
    const request = {
      projectId: "project-a",
      modelPackageId: "kokoro-package-1",
      expectedManifestFingerprint: "a".repeat(64),
      expectedInstallationRevision: null,
      operation: "install",
      acknowledgeRestrictedLocalUse: true,
      reason: "Install exact local bytes for restricted audition use only.",
      idempotencyKey: "model-install-1"
    } as const;
    electronMocks.dialog.showOpenDialog.mockResolvedValue({
      canceled: false,
      filePaths: ["C:\\private\\kokoro-package.zip"]
    });

    try {
      await harness.invoke(IPC_CHANNELS.projectsOpen, {
        projectId: "project-a"
      });
      await expect(
        harness.invoke(
          IPC_CHANNELS.auditionsSelectLocalModelPackage,
          request
        )
      ).resolves.toMatchObject({
        ok: true,
        value: { correlationId: "correlation-model-install" }
      });
      expect(applySelectedLocalModelPackage).toHaveBeenCalledWith(
        request,
        "C:\\private\\kokoro-package.zip"
      );
      expect(electronMocks.dialog.showOpenDialog).toHaveBeenCalledWith(
        expect.anything(),
        expect.objectContaining({
          properties: ["openFile", "dontAddToRecent"],
          filters: [{ name: "Model package ZIP", extensions: ["zip"] }]
        })
      );
    } finally {
      harness.dispose();
    }
  });

  it("routes a bounded review-history request only for the active project", async () => {
    const listAuditionReviewDecisions = vi.fn(async () => ({
      correlationId: "correlation-review-history",
      projectId: "project-a",
      gateId: "per_role_audition_review",
      roleId: "role-1",
      items: [],
      pageSize: 0,
      total: 0
    }));
    const api = {
      openProject: vi.fn(async () =>
        projectDetail("project-a", "job-project-a")
      ),
      listAuditionReviewDecisions
    } as unknown as BackendApiClient;
    const harness = registerHarness(api, preferenceStore());
    const request = {
      projectId: "project-a",
      gateId: "per_role_audition_review",
      roleId: "role-1",
      cursor: "opaque-page-2",
      limit: 25
    } as const;

    try {
      await harness.invoke(IPC_CHANNELS.projectsOpen, {
        projectId: "project-a"
      });
      await expect(
        harness.invoke(IPC_CHANNELS.auditionsListReviewDecisions, request)
      ).resolves.toMatchObject({
        ok: true,
        value: { correlationId: "correlation-review-history" }
      });
      expect(listAuditionReviewDecisions).toHaveBeenCalledWith(request);
      await expect(
        harness.invoke(IPC_CHANNELS.auditionsListReviewDecisions, {
          ...request,
          projectId: "project-b"
        })
      ).resolves.toMatchObject({
        ok: false,
        error: { code: "PROJECT_CONTEXT_MISMATCH" }
      });
    } finally {
      harness.dispose();
    }
  });

  it("isolates project and job ownership and rejects a stale selection", () => {
    const session = new ActiveProjectSession();
    const projectASelection = session.beginSelection();
    session.acceptSelection(
      projectASelection,
      projectDetail("project-a", "job-a")
    );

    expect(session.assertActive("project-a")).toBe(projectASelection);
    expect(session.assertKnownJob("job-a")).toBe(projectASelection);
    expect(() => session.assertActive("project-b")).toThrow(
      "active project"
    );
    expect(() => session.assertKnownJob("job-b")).toThrow(
      "active project"
    );

    const staleProjectASelection = session.beginSelection();
    const projectBSelection = session.beginSelection();
    expect(() =>
      session.acceptSelection(
        staleProjectASelection,
        projectDetail("project-a", "job-a")
      )
    ).toThrow("changed before");

    session.acceptSelection(
      projectBSelection,
      projectDetail("project-b", "job-b")
    );
    expect(session.assertActive("project-b")).toBe(projectBSelection);
    expect(session.assertKnownJob("job-b")).toBe(projectBSelection);
    expect(() => session.assertActive("project-a")).toThrow(
      "active project"
    );
    expect(() => session.assertKnownJob("job-a")).toThrow(
      "active project"
    );
  });

  it("fails closed when a selected project contains foreign-owned work", () => {
    const session = new ActiveProjectSession();
    const selection = session.beginSelection();
    const detail = projectDetail("project-a", "job-a");

    expect(() =>
      session.acceptSelection(selection, {
        ...detail,
        jobs: [{ ...detail.jobs[0], projectId: "project-b" }]
      })
    ).toThrow("owned by another project");
    expect(() => session.assertActive("project-a")).toThrow(
      "active project"
    );
  });

  it("serializes a deferred preference write so accepted project B persists last", async () => {
    let resolveProjectAWrite: (() => void) | undefined;
    const projectAWrite = new Promise<void>((resolve) => {
      resolveProjectAWrite = resolve;
    });
    let persistedProjectId: string | null = null;
    const setRecentProjectId = vi.fn(
      async (projectId: string | null) => {
        if (projectId === "project-a") {
          await projectAWrite;
        }
        persistedProjectId = projectId;
      }
    );
    const preferences = preferenceStore({
      setRecentProjectId
    });
    const api = {
      openProject: vi.fn(async (projectId: string) =>
        projectDetail(projectId, `job-${projectId}`)
      )
    } as unknown as BackendApiClient;
    const harness = registerHarness(api, preferences);

    try {
      const projectA = harness.invoke(IPC_CHANNELS.projectsOpen, {
        projectId: "project-a"
      });
      await vi.waitFor(() => {
        expect(setRecentProjectId).toHaveBeenCalledWith("project-a");
      });

      const projectB = harness.invoke(IPC_CHANNELS.projectsOpen, {
        projectId: "project-b"
      });
      await new Promise<void>((resolve) => {
        setImmediate(resolve);
      });
      expect(setRecentProjectId).toHaveBeenCalledTimes(1);

      resolveProjectAWrite?.();
      await expect(projectA).resolves.toMatchObject({
        ok: false,
        error: { code: "PROJECT_CONTEXT_CHANGED" }
      });
      await expect(projectB).resolves.toMatchObject({ ok: true });
      expect(setRecentProjectId).toHaveBeenNthCalledWith(2, "project-b");
      expect(persistedProjectId).toBe("project-b");
    } finally {
      harness.dispose();
    }
  });

  it("keeps a stale invalid-recent fallback from overwriting accepted project B", async () => {
    let resolveFallback:
      | ((detail: ProjectDetail) => void)
      | undefined;
    const fallback = new Promise<ProjectDetail>((resolve) => {
      resolveFallback = resolve;
    });
    let persistedProjectId: string | null = "project-invalid";
    const setRecentProjectId = vi.fn(
      async (projectId: string | null) => {
        persistedProjectId = projectId;
      }
    );
    const preferences = preferenceStore({
      getRecentProjectId: vi.fn(async () => "project-invalid"),
      setRecentProjectId
    });
    const openProject = vi.fn((projectId: string) => {
      if (projectId === "project-invalid") {
        return Promise.reject(
          new DesktopMainError(
            "PROJECT_NOT_FOUND",
            "The recent project no longer exists.",
            false
          )
        );
      }
      if (projectId === "project-fallback") {
        return fallback;
      }
      return Promise.resolve(
        projectDetail("project-b", "job-project-b")
      );
    });
    const api = {
      listProjects: vi.fn(async () => ({
        correlationId: "correlation-projects",
        items: [
          {
            projectId: "project-fallback",
            name: "Fallback",
            status: "analysis",
            revision: 1,
            createdAt: "2026-07-30T12:00:00Z",
            updatedAt: "2026-07-30T12:00:00Z"
          }
        ]
      })),
      openProject
    } as unknown as BackendApiClient;
    const harness = registerHarness(api, preferences);

    try {
      const restore = harness.invoke(
        IPC_CHANNELS.projectsRestoreRecent,
        {}
      );
      await vi.waitFor(() => {
        expect(openProject).toHaveBeenCalledWith("project-fallback");
      });

      await expect(
        harness.invoke(IPC_CHANNELS.projectsOpen, {
          projectId: "project-b"
        })
      ).resolves.toMatchObject({ ok: true });
      expect(persistedProjectId).toBe("project-b");

      resolveFallback?.(
        projectDetail("project-fallback", "job-project-fallback")
      );
      await expect(restore).resolves.toMatchObject({
        ok: false,
        error: { code: "PROJECT_CONTEXT_CHANGED" }
      });
      expect(setRecentProjectId).toHaveBeenCalledTimes(1);
      expect(setRecentProjectId).toHaveBeenCalledWith("project-b");
      expect(persistedProjectId).toBe("project-b");
    } finally {
      harness.dispose();
    }
  });

  it("passes the exact selected snapshot identity through analysis entity IPC", async () => {
    const listAnalysisEntities = vi.fn(
      async (input: {
        readonly projectId: string;
        readonly runId: string;
        readonly expectedSnapshotId: string;
        readonly collection: string;
      }) => ({
        correlationId: "correlation-entities",
        runId: input.runId,
        snapshotId: input.expectedSnapshotId,
        collection: input.collection,
        pageSize: 0,
        total: 0,
        items: []
      })
    );
    const api = {
      openProject: vi.fn(async () =>
        projectDetail("project-a", "job-project-a")
      ),
      listAnalysisEntities
    } as unknown as BackendApiClient;
    const harness = registerHarness(api, preferenceStore());
    const request = {
      projectId: "project-a",
      runId: "run-a",
      expectedSnapshotId: "snapshot-a",
      collection: "chapters",
      limit: 50
    };

    try {
      await expect(
        harness.invoke(IPC_CHANNELS.projectsOpen, {
          projectId: "project-a"
        })
      ).resolves.toMatchObject({ ok: true });
      await expect(
        harness.invoke(IPC_CHANNELS.analysisListEntities, request)
      ).resolves.toMatchObject({
        ok: true,
        value: {
          runId: "run-a",
          snapshotId: "snapshot-a",
          collection: "chapters"
        }
      });
      expect(listAnalysisEntities).toHaveBeenCalledWith(request);
    } finally {
      harness.dispose();
    }
  });

  it("passes only bounded project-owned casting evidence through IPC", async () => {
    const listCastingCandidates = vi.fn(async () => ({
      items: [],
      total: 0,
      pageSize: 0
    }));
    const createCustomProductionRole = vi.fn(async () => ({
      role: { roleId: "role-custom-a" },
      run: {
        castingRunId: "casting-run-a",
        projectId: "project-a",
        jobId: "job-casting-a"
      },
      reviews: []
    }));
    const api = {
      openProject: vi.fn(async () =>
        projectDetail("project-a", "job-project-a")
      ),
      listCastingCandidates,
      createCustomProductionRole
    } as unknown as BackendApiClient;
    const harness = registerHarness(api, preferenceStore());
    const request = {
      projectId: "project-a",
      runId: "casting-run-a",
      expectedRunFingerprint: "a".repeat(64),
      expectedCatalogRevisionId: "catalog-a",
      expectedCatalogFingerprint: "b".repeat(64),
      expectedSnapshotId: "snapshot-a",
      expectedSnapshotRevision: 3,
      expectedSnapshotFingerprint: "c".repeat(64),
      expectedCastingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
      roleId: "role-a",
      expectedRoleRevision: 2,
      limit: 12
    };
    const customRoleRequest = {
      projectId: "project-a",
      runId: "casting-run-a",
      definitionId: "custom-role-a",
      label: "Festival announcer",
      performanceRequirements: {
        language: "en",
        locales: ["en-US"],
        agePresentationRange: null,
        vocalPresentations: ["neutral"],
        preferredTextures: ["clear"],
        speakingRateRange: null,
        requiredExpressiveRange: ["authoritative"],
        longFormRequired: false
      },
      reason: "Producer-defined role with no manuscript source.",
      expectedRunFingerprint: "a".repeat(64),
      expectedCatalogRevisionId: "catalog-a",
      expectedCatalogFingerprint: "b".repeat(64),
      expectedSnapshotId: "snapshot-a",
      expectedSnapshotRevision: 3,
      expectedSnapshotFingerprint: "c".repeat(64),
      expectedCorrectionSetFingerprint: "d".repeat(64),
      expectedCastingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
      idempotencyKey: "custom-role-create-1"
    };

    try {
      await expect(
        harness.invoke(IPC_CHANNELS.projectsOpen, {
          projectId: "project-a"
        })
      ).resolves.toMatchObject({ ok: true });
      await expect(
        harness.invoke(IPC_CHANNELS.castingListCandidates, request)
      ).resolves.toMatchObject({
        ok: true,
        value: { total: 0, pageSize: 0 }
      });
      expect(listCastingCandidates).toHaveBeenCalledWith(request);
      await expect(
        harness.invoke(
          IPC_CHANNELS.castingCreateCustomRole,
          customRoleRequest
        )
      ).resolves.toMatchObject({
        ok: true,
        value: { role: { roleId: "role-custom-a" } }
      });
      expect(createCustomProductionRole).toHaveBeenCalledWith(
        customRoleRequest
      );
      await expect(
        harness.invoke(IPC_CHANNELS.castingListCandidates, {
          ...request,
          projectId: "project-b"
        })
      ).resolves.toMatchObject({
        ok: false,
        error: { code: "PROJECT_CONTEXT_MISMATCH" }
      });
      await expect(
        harness.invoke(IPC_CHANNELS.castingListCandidates, {
          ...request,
          limit: 13
        })
      ).resolves.toMatchObject({
        ok: false,
        error: { code: "INVALID_DESKTOP_REQUEST" }
      });
    } finally {
      harness.dispose();
    }
  });
});

function preferenceStore(
  overrides?: Partial<PreferenceStore>
): PreferenceStore {
  return {
    getRecentProjectId: vi.fn(async () => null),
    setRecentProjectId: vi.fn(async () => undefined),
    ...overrides
  } as unknown as PreferenceStore;
}

function registerHarness(
  api: BackendApiClient,
  preferences: PreferenceStore
) {
  const mainFrame = {};
  const window = {
    isDestroyed: () => false,
    webContents: {
      id: 17,
      mainFrame,
      send: vi.fn()
    }
  };
  const service = {
    snapshot: {
      state: "ready",
      message: "Backend ready.",
      checkedAt: "2026-07-30T12:00:00Z"
    },
    reconnect: vi.fn(),
    onStatus: vi.fn(() => () => undefined)
  } as unknown as ServiceManager;
  const dispose = registerDesktopIpc({
    window: window as never,
    service,
    api,
    preferences
  });
  return {
    dispose,
    async invoke(
      channel: string,
      payload: Readonly<Record<string, unknown>>
    ) {
      const handler = electronMocks.handlers.get(channel);
      if (handler === undefined) {
        throw new Error(`No IPC handler was registered for ${channel}.`);
      }
      return handler(
        {
          sender: { id: window.webContents.id },
          senderFrame: mainFrame
        },
        {
          contractVersion: "1.0.0",
          payload
        }
      );
    }
  };
}

function projectDetail(
  projectId: string,
  jobId: string
): ProjectDetail {
  return {
    project: { projectId },
    jobs: [{ jobId, projectId }],
    currentAnalysisRun: null
  } as unknown as ProjectDetail;
}
