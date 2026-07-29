import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  CorrectDialogueSpeakerResponse,
  FfmpegCapabilityResponse,
  ImportStoryResponse,
  Job,
  ProjectDetail,
  ProjectPageResponse,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";

import type {
  BackendSnapshot,
  CinematicStoryDesktopApi,
  DesktopResult
} from "../shared/desktop-api";
import { App } from "./App";

const readyBackend: BackendSnapshot = {
  state: "ready",
  message: "Backend ready.",
  checkedAt: "2026-07-29T12:00:00Z",
  health: {
    status: "ready",
    serviceVersion: "0.1.0",
    contractVersion: "1.0.0",
    instanceId: "instance-1",
    database: { status: "ready" },
    checkedAt: "2026-07-29T12:00:00Z",
    correlationId: "correlation-1"
  }
};

describe("Phase 0 desktop workspace", () => {
  it("shows a non-success backend unavailable state and disables mutation", async () => {
    const api = createApi({
      backend: {
        state: "unavailable",
        message: "The local service could not be started.",
        checkedAt: "2026-07-29T12:00:00Z"
      }
    });
    render(<App api={api} />);

    expect(await screen.findAllByText("Backend unavailable")).not.toHaveLength(0);
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry connection" })).toBeEnabled();
  });

  it("does not present project loading as a successful workspace", async () => {
    let resolveRestore:
      | ((result: DesktopResult<ProjectDetail | null>) => void)
      | undefined;
    const restorePromise = new Promise<DesktopResult<ProjectDetail | null>>(
      (resolve) => {
        resolveRestore = resolve;
      }
    );
    const api = createApi();
    vi.mocked(api.projects.restoreRecent).mockReturnValue(restorePromise);
    render(<App api={api} />);

    expect(await screen.findByText("Opening project...")).toBeInTheDocument();
    expect(screen.queryByText("Scene One")).not.toBeInTheDocument();
    resolveRestore?.(ok(null));
  });

  it("loads a project and supports keyboard-operable chapter and scene navigation", async () => {
    const detail = createProjectDetail();
    const api = createApi({ project: detail });
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByText("Rain stitched the windows.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Chapter Two/u }));
    await user.click(screen.getByRole("button", { name: /The Vault/u }));
    expect(screen.getByRole("heading", { name: "The Vault" })).toBeVisible();
    expect(screen.getByText("The lock answered with a click.")).toBeVisible();
  });

  it("saves a speaker correction with reason and revision", async () => {
    const detail = createProjectDetail();
    const api = createApi({ project: detail });
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.selectOptions(screen.getByLabelText("Speaker"), "character-bob");
    await user.type(
      screen.getByLabelText("Correction reason"),
      "fixture correction"
    );
    await user.click(
      screen.getByRole("button", { name: "Save correction" })
    );

    await waitFor(() => {
      expect(api.dialogue.correctSpeaker).toHaveBeenCalledWith({
        projectId: "project-1",
        lineId: "line-1",
        characterId: "character-bob",
        reason: "fixture correction",
        expectedRevision: 2
      });
    });
  });

  it("imports a selected text story through the narrow desktop operation", async () => {
    const detail = createProjectDetail();
    const api = createApi({ project: detail });
    const imported: ImportStoryResponse = {
      correlationId: "import-correlation",
      sourceDocument: {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance: detail.project.provenance,
        documentId: "document-1",
        projectId: "project-1",
        displayName: "sample-story.md",
        mediaType: "text/markdown",
        contentSha256: "a".repeat(64),
        byteLength: 512,
        importedAt: "2026-07-29T12:00:00Z",
        originalTextPreserved: true,
        storageKey: "sources/document-1",
        extractionStatus: "complete",
        warnings: []
      },
      story: detail.story!
    };
    vi.mocked(api.projects.importSelectedFile).mockResolvedValue(ok(imported));
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(screen.getByRole("button", { name: "Import TXT / MD" }));
    await waitFor(() => {
      expect(api.projects.importSelectedFile).toHaveBeenCalledWith("project-1");
    });
    expect(
      await screen.findByText(
        "Imported sample-story.md without changing its text."
      )
    ).toBeVisible();
  });

  it("queues analysis against the imported story revision", async () => {
    const detail = createProjectDetail();
    const api = createApi({ project: detail });
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(screen.getByRole("button", { name: "Analyze story" }));
    await waitFor(() => {
      expect(api.jobs.create).toHaveBeenCalledOnce();
    });
    const request = vi.mocked(api.jobs.create).mock.calls[0]?.[0];
    expect(request?.projectId).toBe("project-1");
    expect(request?.type).toBe("analyze_story");
    expect(request?.inputRevision).toBe(1);
    expect(request?.idempotencyKey).toHaveLength(36);
  });

  it("renders job progress and invokes cancel control", async () => {
    const runningJob = createJob({ state: "running", progress: 0.42 });
    const detail = createProjectDetail({ jobs: [runningJob] });
    const api = createApi({ project: detail });
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(
      await screen.findByRole("progressbar", { name: "Analyze story progress" })
    ).toHaveAttribute("value", "0.42");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.jobs.cancel).toHaveBeenCalledWith("job-1");
  });

  it("renders one typed unassigned casting row per detected character", async () => {
    const api = createApi({ project: createProjectDetail() });
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(screen.getByRole("button", { name: "Casting" }));
    expect(screen.getByRole("heading", { name: "Alice" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Bob" })).toBeVisible();
    expect(screen.getAllByText("Unassigned")).toHaveLength(2);
    expect(screen.getAllByText("No provider or voice selected.")).toHaveLength(
      2
    );
  });

  it("shows Kokoro and FFmpeg status without blocking project APIs", async () => {
    const api = createApi({ project: createProjectDetail() });
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(screen.getByRole("button", { name: "Systems" }));
    expect(await screen.findByRole("heading", { name: "Kokoro" })).toBeVisible();
    expect(screen.getByText("Development runtime is not running.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "FFmpeg" })).toBeVisible();
    expect(screen.getByText("Managed binary is not staged.")).toBeVisible();
  });
});

function createApi(options?: {
  readonly backend?: BackendSnapshot;
  readonly project?: ProjectDetail;
}): CinematicStoryDesktopApi {
  const backend = options?.backend ?? readyBackend;
  const project = options?.project;
  const page: ProjectPageResponse = {
    correlationId: "correlation-list",
    items:
      project === undefined
        ? []
        : [
            {
              projectId: project.project.projectId,
              name: project.project.name,
              status: project.project.status,
              revision: project.project.revision,
              createdAt: project.project.createdAt,
              updatedAt: project.project.updatedAt
            }
          ]
  };
  const providers: ProviderHealthResponse = {
    correlationId: "correlation-providers",
    providers: [
      {
        providerId: "kokoro-development",
        kind: "speech",
        executionLocation: "local",
        status: "unavailable",
        capabilities: ["speech"],
        redactedReason: "Development runtime is not running.",
        checkedAt: "2026-07-29T12:00:00Z"
      }
    ]
  };
  const ffmpeg: FfmpegCapabilityResponse = {
    correlationId: "correlation-ffmpeg",
    status: "missing",
    executableOrigin: "none",
    capabilities: [],
    missingCapabilities: ["encode_mp3"],
    redactedReason: "Managed binary is not staged.",
    checkedAt: "2026-07-29T12:00:00Z"
  };
  const cancelledJob = createJob({
    state: "cancel_requested",
    progress: 0.42,
    cancellationRequested: true
  });

  return {
    version: "1.0.0",
    backend: {
      getStatus: vi.fn(async () => ok(backend)),
      reconnect: vi.fn(async () => ok(readyBackend)),
      onStatus: vi.fn(() => () => undefined)
    },
    projects: {
      list: vi.fn(async () => ok(page)),
      create: vi.fn(async () => {
        throw new Error("Not configured.");
      }),
      open: vi.fn(async (): Promise<DesktopResult<ProjectDetail>> =>
        project === undefined
          ? fail<ProjectDetail>("PROJECT_NOT_FOUND")
          : ok(project)
      ),
      restoreRecent: vi.fn(async () => ok(project ?? null)),
      importSelectedFile: vi.fn(async () => ok(null))
    },
    dialogue: {
      correctSpeaker: vi.fn(
        async (): Promise<
          DesktopResult<CorrectDialogueSpeakerResponse>
        > =>
        ok({
          correlationId: "correction-correlation",
          attribution: {
            ...createProjectDetail().dialogueAttributions[0],
            effectiveSpeakerId: "character-bob",
            effectiveAuthority: "human" as const
          },
          appendedCorrection: {
            correctionId: "correction-1",
            target: {
              entityType: "dialogue_attribution",
              entityId: "attribution-1",
              revision: 2
            },
            fieldPath: "/effectiveSpeakerId",
            correctedValue: "character-bob",
            reason: "fixture correction",
            authority: { source: "human", actorId: "desktop-user" },
            recordedAt: "2026-07-29T12:00:00Z",
            immutable: true,
            lockedAgainstAutomation: true
          },
          projectRevision: 4,
          lineRevision: 3
        })
      )
    },
    jobs: {
      create: vi.fn(async () => ok({ correlationId: "job-correlation", job: createJob() })),
      get: vi.fn(async () => ok({ correlationId: "job-correlation", job: createJob() })),
      events: vi.fn(async () =>
        ok({
          correlationId: "events-correlation",
          events: [],
          lastSequence: 0
        })
      ),
      cancel: vi.fn(async () =>
        ok({ correlationId: "job-correlation", job: cancelledJob })
      ),
      retry: vi.fn(async () =>
        ok({ correlationId: "job-correlation", job: createJob() })
      ),
      resume: vi.fn(async () =>
        ok({ correlationId: "job-correlation", job: createJob() })
      )
    },
    providers: {
      health: vi.fn(async () => ok(providers))
    },
    capabilities: {
      ffmpeg: vi.fn(async () => ok(ffmpeg))
    }
  };
}

function createProjectDetail(options?: {
  readonly jobs?: readonly Job[];
}): ProjectDetail {
  const provenance = {
    origin: "runtime_agent" as const,
    recordedAt: "2026-07-29T12:00:00Z",
    actorId: "agent-story-structure"
  };
  const span = {
    sourceDocumentId: "document-1",
    offsetUnit: "unicode-code-point" as const,
    startOffset: 0,
    endOffset: 24,
    textSha256: "a".repeat(64)
  };
  const confidence = { score: 0.82, basis: "synthetic fixture" };
  return {
    correlationId: "correlation-detail",
    project: {
      schemaVersion: "1.0.0",
      revision: 3,
      provenance,
      projectId: "project-1",
      name: "Synthetic Demo",
      status: "analysis",
      createdAt: "2026-07-29T12:00:00Z",
      updatedAt: "2026-07-29T12:05:00Z",
      storyId: "story-1",
      sourceDocumentIds: ["document-1"],
      approvalDecisionIds: [],
      dataClassification: "private_local_content",
      settings: {
        defaultLanguage: "en",
        cloudTransmissionPolicy: "local_only",
        audioProfile: "cinematic_stereo_v1"
      }
    },
    sourceDocuments: [],
    story: {
      schemaVersion: "1.0.0",
      revision: 1,
      provenance,
      storyId: "story-1",
      projectId: "project-1",
      title: "Synthetic Demo",
      sourceDocumentIds: ["document-1"],
      contentFingerprint: "b".repeat(64),
      originalTextPreserved: true,
      importedAt: "2026-07-29T12:00:00Z",
      chapterIds: ["chapter-1", "chapter-2"]
    },
    chapters: [
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        chapterId: "chapter-1",
        projectId: "project-1",
        storyId: "story-1",
        ordinal: 0,
        title: "Chapter One",
        sourceSpan: span,
        sceneIds: ["scene-1"],
        approvalState: "pending"
      },
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        chapterId: "chapter-2",
        projectId: "project-1",
        storyId: "story-1",
        ordinal: 1,
        title: "Chapter Two",
        sourceSpan: span,
        sceneIds: ["scene-2"],
        approvalState: "pending"
      }
    ],
    scenes: [
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        sceneId: "scene-1",
        projectId: "project-1",
        chapterId: "chapter-1",
        ordinal: 0,
        heading: "Scene One",
        location: "Observatory",
        mood: "Tense",
        sourceSpan: span,
        beatIds: ["beat-1", "beat-2"],
        dialogueLineIds: ["line-1"],
        characterIds: ["character-alice", "character-bob"],
        approvalState: "pending",
        confidence,
        warnings: []
      },
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        sceneId: "scene-2",
        projectId: "project-1",
        chapterId: "chapter-2",
        ordinal: 0,
        heading: "The Vault",
        sourceSpan: span,
        beatIds: ["beat-3"],
        dialogueLineIds: [],
        characterIds: ["character-bob"],
        approvalState: "pending",
        confidence,
        warnings: []
      }
    ],
    beats: [
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        beatId: "beat-1",
        projectId: "project-1",
        sceneId: "scene-1",
        ordinal: 0,
        kind: "narration",
        sourceSpan: span,
        summary: "Rain stitched the windows."
      },
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        beatId: "beat-2",
        projectId: "project-1",
        sceneId: "scene-1",
        ordinal: 1,
        kind: "dialogue",
        sourceSpan: span,
        dialogueLineId: "line-1"
      },
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        beatId: "beat-3",
        projectId: "project-1",
        sceneId: "scene-2",
        ordinal: 0,
        kind: "narration",
        sourceSpan: span,
        summary: "The lock answered with a click."
      }
    ],
    characters: [
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        characterId: "character-alice",
        projectId: "project-1",
        storyId: "story-1",
        displayName: "Alice",
        aliases: [],
        sourceReferences: [span],
        humanCorrections: [],
        confidence,
        warnings: []
      },
      {
        schemaVersion: "1.0.0",
        revision: 1,
        provenance,
        characterId: "character-bob",
        projectId: "project-1",
        storyId: "story-1",
        displayName: "Bob",
        aliases: [],
        sourceReferences: [span],
        humanCorrections: [],
        confidence,
        warnings: []
      }
    ],
    dialogueLines: [
      {
        schemaVersion: "1.0.0",
        revision: 2,
        provenance,
        lineId: "line-1",
        projectId: "project-1",
        sceneId: "scene-1",
        beatId: "beat-2",
        ordinal: 0,
        sourceSpan: span,
        verbatimText: '"We should go."',
        textSha256: "c".repeat(64),
        originalTextPreserved: true,
        attributionId: "attribution-1"
      }
    ],
    dialogueAttributions: [
      {
        schemaVersion: "1.0.0",
        revision: 2,
        provenance,
        attributionId: "attribution-1",
        projectId: "project-1",
        lineId: "line-1",
        proposedSpeakerId: "character-alice",
        effectiveSpeakerId: "character-alice",
        effectiveAuthority: "runtime_agent",
        evidence: [span],
        confidence,
        warnings: [],
        humanCorrections: [],
        updatedAt: "2026-07-29T12:05:00Z"
      }
    ],
    castingAssignments: [],
    castingPlaceholders: [
      {
        characterId: "character-alice",
        status: "unassigned",
        providerId: null,
        voiceId: null
      },
      {
        characterId: "character-bob",
        status: "unassigned",
        providerId: null,
        voiceId: null
      }
    ],
    approvals: [],
    jobs: options?.jobs ?? []
  };
}

function createJob(
  overrides?: Partial<Job>
): Job {
  return {
    jobId: "job-1",
    projectId: "project-1",
    type: "analyze_story",
    state: "queued",
    inputRevision: 3,
    inputFingerprint: "d".repeat(64),
    attempt: 1,
    stage: "analyze_story",
    progress: 0,
    checkpointAvailable: false,
    cancellationRequested: false,
    warnings: [],
    createdAt: "2026-07-29T12:00:00Z",
    updatedAt: "2026-07-29T12:00:00Z",
    ...overrides
  };
}

function ok<T>(value: T): DesktopResult<T> {
  return { ok: true, value };
}

function fail<T>(code: string): DesktopResult<T> {
  return {
    ok: false,
    error: {
      code,
      message: "The requested item was not found.",
      retryable: false
    }
  };
}
