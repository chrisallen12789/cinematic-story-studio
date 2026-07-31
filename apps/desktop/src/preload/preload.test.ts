// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectDetail } from "@cinematic-story-studio/contracts/api";

import {
  IPC_CHANNELS,
  type CinematicStoryDesktopApi,
  type DesktopResult
} from "../shared/desktop-api";

const electron = vi.hoisted(() => ({
  exposeInMainWorld: vi.fn(),
  invoke: vi.fn(),
  on: vi.fn(),
  removeListener: vi.fn()
}));

vi.mock("electron", () => ({
  contextBridge: {
    exposeInMainWorld: electron.exposeInMainWorld
  },
  ipcRenderer: {
    invoke: electron.invoke,
    on: electron.on,
    removeListener: electron.removeListener
  }
}));

describe("preload active project session", () => {
  beforeEach(() => {
    vi.resetModules();
    electron.exposeInMainWorld.mockReset();
    electron.invoke.mockReset();
  });

  it("independently blocks cross-project and stale project responses", async () => {
    let deferProjectA = false;
    let resolveProjectA:
      | ((result: DesktopResult<ProjectDetail>) => void)
      | undefined;
    electron.invoke.mockImplementation(
      async (channel: string, envelope: unknown) => {
        const payload = requestPayload(envelope);
        if (channel === IPC_CHANNELS.projectsOpen) {
          const projectId = payload.projectId as string;
          if (projectId === "project-a" && deferProjectA) {
            return new Promise<DesktopResult<ProjectDetail>>((resolve) => {
              resolveProjectA = resolve;
            });
          }
          return ok(projectDetail(projectId, `job-${projectId}`));
        }
        if (channel === IPC_CHANNELS.analysisListRuns) {
          return ok({
            correlationId: "correlation-runs",
            pageSize: 0,
            total: 0,
            runs: []
          });
        }
        if (channel === IPC_CHANNELS.jobsGet) {
          return ok({
            correlationId: "correlation-job",
            job: {
              jobId: payload.jobId,
              projectId: "project-a"
            }
          });
        }
        return ok({});
      }
    );

    await import("./preload");
    const api = exposedApi();

    const unopened = await api.analysis.listRuns({
      projectId: "project-a"
    });
    expect(unopened).toMatchObject({
      ok: false,
      error: { code: "PROJECT_CONTEXT_MISMATCH" }
    });
    expect(electron.invoke).not.toHaveBeenCalled();

    expect((await api.projects.open("project-a")).ok).toBe(true);
    const exactEntityRequest = {
      projectId: "project-a",
      runId: "run-a",
      expectedSnapshotId: "snapshot-a",
      collection: "chapters" as const,
      limit: 50
    };
    expect(
      await api.analysis.listEntities(exactEntityRequest)
    ).toMatchObject({ ok: true });
    expect(electron.invoke).toHaveBeenCalledWith(
      IPC_CHANNELS.analysisListEntities,
      {
        contractVersion: "1.0.0",
        payload: exactEntityRequest
      }
    );
    const projectBRequest = await api.analysis.listRuns({
      projectId: "project-b"
    });
    expect(projectBRequest).toMatchObject({
      ok: false,
      error: { code: "PROJECT_CONTEXT_MISMATCH" }
    });
    expect(
      await api.jobs.get("job-project-a")
    ).toMatchObject({ ok: true });
    expect(await api.jobs.get("job-project-b")).toMatchObject({
      ok: false,
      error: { code: "PROJECT_CONTEXT_MISMATCH" }
    });

    deferProjectA = true;
    const staleA = api.projects.open("project-a");
    const selectedB = await api.projects.open("project-b");
    expect(selectedB).toMatchObject({
      ok: true,
      value: { project: { projectId: "project-b" } }
    });
    resolveProjectA?.(ok(projectDetail("project-a", "job-project-a")));
    expect(await staleA).toMatchObject({
      ok: false,
      error: { code: "PROJECT_CONTEXT_CHANGED" }
    });
    expect(
      await api.analysis.listRuns({ projectId: "project-a" })
    ).toMatchObject({
      ok: false,
      error: { code: "PROJECT_CONTEXT_MISMATCH" }
    });
    expect(
      await api.analysis.listRuns({ projectId: "project-b" })
    ).toMatchObject({ ok: true });
  });

  it("fails closed when selection data claims foreign ownership", async () => {
    electron.invoke.mockImplementation(async (channel: string) => {
      if (channel === IPC_CHANNELS.projectsOpen) {
        const detail = projectDetail("project-a", "job-project-a");
        return ok({
          ...detail,
          jobs: [{ ...detail.jobs[0], projectId: "project-b" }]
        });
      }
      return ok({});
    });

    await import("./preload");
    const api = exposedApi();

    expect(await api.projects.open("project-a")).toMatchObject({
      ok: false,
      error: { code: "PROJECT_CONTEXT_MISMATCH" }
    });
    expect(
      await api.analysis.listRuns({ projectId: "project-a" })
    ).toMatchObject({
      ok: false,
      error: { code: "PROJECT_CONTEXT_MISMATCH" }
    });
    expect(electron.invoke).toHaveBeenCalledTimes(1);
  });
});

function exposedApi(): CinematicStoryDesktopApi {
  const call = electron.exposeInMainWorld.mock.calls.at(-1);
  if (call === undefined) {
    throw new Error("The preload API was not exposed.");
  }
  return call[1] as CinematicStoryDesktopApi;
}

function requestPayload(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object") {
    return {};
  }
  const payload = (value as { readonly payload?: unknown }).payload;
  return payload !== null &&
    typeof payload === "object" &&
    !Array.isArray(payload)
    ? (payload as Record<string, unknown>)
    : {};
}

function projectDetail(
  projectId: string,
  jobId: string
): ProjectDetail {
  return {
    correlationId: `correlation-${projectId}`,
    project: { projectId },
    jobs: [{ jobId, projectId }],
    currentAnalysisRun: null
  } as unknown as ProjectDetail;
}

function ok<T>(value: T): DesktopResult<T> {
  return { ok: true, value };
}
