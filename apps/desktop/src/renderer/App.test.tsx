import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  PHASE_2_RUNTIME_AGENTS,
  WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
  WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
  type AnalysisGateReview,
  type StoryAnalysisRun
} from "@cinematic-story-studio/contracts";
import type {
  CorrectDialogueSpeakerResponse,
  CreateProjectResponse,
  DecideImportReviewResponse,
  DocumentExtractionSummary,
  FfmpegCapabilityResponse,
  ImportReview,
  ImportReviewResponse,
  ImportStoryResponse,
  Job,
  ProjectDetail,
  ProjectPageResponse,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";

import type {
  BackendSnapshot,
  CinematicStoryDesktopApi,
  DecideImportReviewInput,
  DesktopResult,
  ImportReviewIdInput
} from "../shared/desktop-api";
import {
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION
} from "../shared/analysis-api";
import { App } from "./App";

const sha = (character: string) => character.repeat(64);

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

  it("does not let a deferred project A response replace active project B", async () => {
    const projectA = createProjectDetail();
    const projectB: ProjectDetail = {
      ...createProjectDetail(),
      correlationId: "correlation-project-b",
      project: {
        ...createProjectDetail().project,
        projectId: "project-2",
        name: "Project B"
      }
    };
    let resolveProjectA:
      | ((result: DesktopResult<ProjectDetail>) => void)
      | undefined;
    const deferredProjectA = new Promise<DesktopResult<ProjectDetail>>(
      (resolve) => {
        resolveProjectA = resolve;
      }
    );
    const api = createApi({ project: projectA });
    vi.mocked(api.projects.list).mockResolvedValue(
      ok({
        correlationId: "correlation-project-list",
        items: [projectA, projectB].map((detail) => ({
          projectId: detail.project.projectId,
          name: detail.project.name,
          status: detail.project.status,
          revision: detail.project.revision,
          createdAt: detail.project.createdAt,
          updatedAt: detail.project.updatedAt
        }))
      })
    );
    vi.mocked(api.projects.open).mockImplementation((projectId) =>
      projectId === "project-1"
        ? deferredProjectA
        : Promise.resolve(ok(projectB))
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(
      screen.getByRole("button", { name: /Synthetic Demo/u })
    );
    await user.click(screen.getByRole("button", { name: /Project B/u }));
    expect(
      await screen.findByRole("heading", { name: "Project B" })
    ).toBeVisible();

    resolveProjectA?.(ok(projectA));
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Project B" })
      ).toBeVisible();
    });
  });

  it("does not auto-open a created project after a newer selection is accepted", async () => {
    const projectA = createProjectDetail();
    const projectB: ProjectDetail = {
      ...createProjectDetail(),
      project: {
        ...createProjectDetail().project,
        projectId: "project-2",
        name: "Project B"
      }
    };
    const projectC: ProjectDetail = {
      ...createProjectDetail(),
      project: {
        ...createProjectDetail().project,
        projectId: "project-3",
        name: "Project C"
      }
    };
    let resolveCreate:
      | ((result: DesktopResult<CreateProjectResponse>) => void)
      | undefined;
    const deferredCreate = new Promise<
      DesktopResult<CreateProjectResponse>
    >((resolve) => {
      resolveCreate = resolve;
    });
    const api = createApi({ project: projectA });
    vi.mocked(api.projects.create).mockReturnValue(deferredCreate);
    vi.mocked(api.projects.list).mockResolvedValue(
      ok({
        correlationId: "correlation-project-list",
        items: [projectA, projectB, projectC].map((detail) => ({
          projectId: detail.project.projectId,
          name: detail.project.name,
          status: detail.project.status,
          revision: detail.project.revision,
          createdAt: detail.project.createdAt,
          updatedAt: detail.project.updatedAt
        }))
      })
    );
    vi.mocked(api.projects.open).mockImplementation((projectId) =>
      Promise.resolve(
        ok(
          projectId === projectB.project.projectId
            ? projectB
            : projectC
        )
      )
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.type(
      screen.getByLabelText("New production"),
      "Project C"
    );
    await user.click(
      screen.getByRole("button", { name: "Create project" })
    );
    await user.click(screen.getByRole("button", { name: /Project B/u }));
    expect(
      await screen.findByRole("heading", { name: "Project B" })
    ).toBeVisible();

    resolveCreate?.(
      ok({
        correlationId: "correlation-create-project-c",
        project: projectC.project
      })
    );
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Project B" })
      ).toBeVisible();
    });
    expect(api.projects.open).not.toHaveBeenCalledWith("project-3");
  });

  it("does not let a stale project operation clear a newer busy action", async () => {
    const projectA = createProjectDetail();
    const projectB: ProjectDetail = {
      ...createProjectDetail(),
      project: {
        ...createProjectDetail().project,
        projectId: "project-2",
        name: "Project B"
      }
    };
    let resolveImportA:
      | ((result: DesktopResult<ImportStoryResponse | null>) => void)
      | undefined;
    let resolveImportB:
      | ((result: DesktopResult<ImportStoryResponse | null>) => void)
      | undefined;
    const importA = new Promise<
      DesktopResult<ImportStoryResponse | null>
    >((resolve) => {
      resolveImportA = resolve;
    });
    const importB = new Promise<
      DesktopResult<ImportStoryResponse | null>
    >((resolve) => {
      resolveImportB = resolve;
    });
    const api = createApi({ project: projectA });
    vi.mocked(api.projects.list).mockResolvedValue(
      ok({
        correlationId: "correlation-project-list",
        items: [projectA, projectB].map((detail) => ({
          projectId: detail.project.projectId,
          name: detail.project.name,
          status: detail.project.status,
          revision: detail.project.revision,
          createdAt: detail.project.createdAt,
          updatedAt: detail.project.updatedAt
        }))
      })
    );
    vi.mocked(api.projects.open).mockImplementation((projectId) =>
      Promise.resolve(
        ok(
          projectId === projectB.project.projectId
            ? projectB
            : projectA
        )
      )
    );
    vi.mocked(api.projects.importSelectedFile).mockImplementation(
      (projectId) =>
        projectId === projectA.project.projectId ? importA : importB
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(
      screen.getByRole("button", { name: "Import document" })
    );
    expect(
      screen.getByRole("button", { name: "Selecting..." })
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Project B/u }));
    expect(
      await screen.findByRole("heading", { name: "Project B" })
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "Import document" })
    );

    resolveImportA?.(ok(null));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Selecting..." })
      ).toBeDisabled();
    });
    resolveImportB?.(ok(null));
    expect(
      await screen.findByRole("button", { name: "Import document" })
    ).toBeEnabled();
  });

  it("atomically clears rendered ownership while a project selection is pending or fails", async () => {
    const projectA = createProjectDetail();
    const projectB: ProjectDetail = {
      ...createProjectDetail(),
      project: {
        ...createProjectDetail().project,
        projectId: "project-2",
        name: "Project B"
      }
    };
    let resolveProjectB:
      | ((result: DesktopResult<ProjectDetail>) => void)
      | undefined;
    const deferredProjectB = new Promise<DesktopResult<ProjectDetail>>(
      (resolve) => {
        resolveProjectB = resolve;
      }
    );
    const api = createApi({ project: projectA });
    vi.mocked(api.projects.list).mockResolvedValue(
      ok({
        correlationId: "correlation-project-list",
        items: [projectA, projectB].map((detail) => ({
          projectId: detail.project.projectId,
          name: detail.project.name,
          status: detail.project.status,
          revision: detail.project.revision,
          createdAt: detail.project.createdAt,
          updatedAt: detail.project.updatedAt
        }))
      })
    );
    vi.mocked(api.projects.open).mockImplementation((projectId) =>
      projectId === projectB.project.projectId
        ? deferredProjectB
        : Promise.resolve(ok(projectA))
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(screen.getByRole("button", { name: /Project B/u }));
    expect(await screen.findByText("Opening project...")).toBeVisible();
    expect(screen.queryByText('"We should go."')).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save correction" })
    ).not.toBeInTheDocument();

    resolveProjectB?.(fail("PROJECT_OPEN_FAILED"));
    await waitFor(() => {
      expect(screen.queryByText('"We should go."')).not.toBeInTheDocument();
    });
    expect(api.dialogue.correctSpeaker).not.toHaveBeenCalled();
  });

  it("preserves rendered ownership when a same-project job refresh fails", async () => {
    const runningJob = createJob({ state: "running", progress: 0.8 });
    const succeededJob = createJob({
      state: "succeeded",
      stage: "complete",
      progress: 1
    });
    const detail = createProjectDetail({ jobs: [runningJob] });
    let resolveRefresh:
      | ((result: DesktopResult<ProjectDetail>) => void)
      | undefined;
    let resolveImport:
      | ((result: DesktopResult<ImportStoryResponse | null>) => void)
      | undefined;
    const refresh = new Promise<DesktopResult<ProjectDetail>>((resolve) => {
      resolveRefresh = resolve;
    });
    const importRequest = new Promise<
      DesktopResult<ImportStoryResponse | null>
    >((resolve) => {
      resolveImport = resolve;
    });
    const api = createApi({ project: detail });
    vi.mocked(api.jobs.get).mockResolvedValue(
      ok({ correlationId: "job-refresh-correlation", job: succeededJob })
    );
    vi.mocked(api.projects.open).mockReturnValue(refresh);
    vi.mocked(api.projects.importSelectedFile).mockReturnValue(importRequest);
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByText('"We should go."')).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Import document" }));
    expect(
      screen.getByRole("button", { name: "Selecting..." })
    ).toBeDisabled();
    await waitFor(
      () => {
        expect(api.projects.open).toHaveBeenCalledWith(
          detail.project.projectId
        );
      },
      { timeout: 5_000 }
    );
    expect(
      screen.getByRole("heading", { name: detail.project.name })
    ).toBeVisible();
    expect(screen.getByText('"We should go."')).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Selecting..." })
    ).toBeDisabled();

    resolveRefresh?.(fail("PROJECT_CONTEXT_CHANGED"));
    expect(
      await screen.findByText("PROJECT_CONTEXT_CHANGED", { exact: true })
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: detail.project.name })
    ).toBeVisible();
    expect(screen.getByText('"We should go."')).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Selecting..." })
    ).toBeDisabled();

    resolveImport?.(ok(null));
    expect(
      await screen.findByRole("button", { name: "Import document" })
    ).toBeEnabled();
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
    const sourceDocument = {
      schemaVersion: "1.0.0" as const,
      revision: 1,
      provenance: detail.project.provenance,
      documentId: "document-2",
      projectId: "project-1",
      displayName: "sample-story.docx",
      mediaType:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" as const,
      declaredFormat: "docx" as const,
      contentSha256: "a".repeat(64),
      byteLength: 512,
      importedAt: "2026-07-29T12:00:00Z",
      originalTextPreserved: true as const,
      originalBytesPreserved: true as const,
      storageKey: "sources/document-2",
      extractionStatus: "pending" as const,
      sourceRevision: 2,
      warnings: []
    };
    const extraction = createExtraction({
      sourceDocumentId: sourceDocument.documentId
    });
    const extractionJob = createJob({
      jobId: "job-extraction",
      type: "extract_document",
      inputFingerprint: sourceDocument.contentSha256,
      stage: "extract_document"
    });
    const imported: ImportStoryResponse = {
      correlationId: "import-correlation",
      sourceDocument,
      extraction,
      job: extractionJob
    };
    vi.mocked(api.projects.importSelectedFile).mockResolvedValue(ok(imported));
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(screen.getByRole("button", { name: "Import document" }));
    await waitFor(() => {
      expect(api.projects.importSelectedFile).toHaveBeenCalledWith("project-1");
    });
    expect(
      await screen.findByText(
        "Queued secure extraction for sample-story.docx."
      )
    ).toBeVisible();
    expect(
      screen.getByRole("progressbar", { name: "Extract document progress" })
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

  it("gates analysis on exact import review and records approval", async () => {
    const extraction = createExtraction({
      status: "complete",
      extractedTitle: "Synthetic Review",
      extractedTextSha256: "b".repeat(64),
      extractedCharacterCount: 72,
      sectionCount: 4,
      pageCount: 3,
      quality: {
        classification: "structured_extraction",
        confidence: 0.98
      },
      completedAt: "2026-07-29T12:01:00Z"
    });
    const review = createImportReview();
    const detail = createProjectDetail({
      sourceDocuments: [createSourceDocument()],
      extractions: [extraction],
      importReviews: [review],
      analysisAllowed: false
    });
    const api = createApi({ project: detail });
    vi.mocked(api.projects.open).mockResolvedValue(
      ok({
        ...detail,
        analysisAllowed: true,
        importReviews: [
          createImportReview({
            revision: 2,
            state: "approved",
            updatedAt: "2026-07-29T12:10:00Z"
          })
        ]
      })
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(
      await screen.findByRole("heading", { name: "sample-story.docx" })
    ).toBeVisible();
    expect(screen.getByText("Declared format").parentElement).toHaveTextContent(
      "Declared formatDOCX"
    );
    expect(screen.getByText("Detected format").parentElement).toHaveTextContent(
      "Detected formatDOCX"
    );
    expect(screen.getByText("No extraction warnings")).toHaveAttribute(
      "role",
      "status"
    );
    expect(screen.getByText("A bounded synthetic preview.")).toBeVisible();
    expect(screen.getByText("Exact original bytes preserved")).toBeVisible();
    expect(screen.getByText("Synthetic Review")).toBeVisible();
    expect(
      screen.getByText(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      )
    ).toBeVisible();
    expect(screen.getByText("72")).toBeVisible();
    expect(screen.getByText("4")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
    expect(
      screen.getByText("Analysis may continue").nextElementSibling
    ).toHaveTextContent("No");
    expect(
      screen.getByRole("button", { name: "Analyze story" })
    ).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Return to project" })
    );
    expect(
      screen.getByRole("button", { name: "Review import" })
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Review import" }));
    const rationaleInput = screen.getByLabelText(
      "Review rationale (optional for approval)"
    );
    expect(rationaleInput).toHaveAttribute("maxlength", "2000");
    await user.clear(rationaleInput);
    expect(
      screen.getByRole("button", { name: "Request changes" })
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Approve import" }));

    await waitFor(() => {
      expect(api.projects.decideImportReview).toHaveBeenCalledOnce();
    });
    expect(
      vi.mocked(api.projects.decideImportReview).mock.calls[0]?.[0]
    ).toMatchObject({
      projectId: "project-1",
      reviewId: "review-1",
      sourceDocumentId: "document-1",
      extractionId: "extraction-1",
      candidateStoryId: "story-1",
      candidateStoryRevision: 1,
      decision: "approved",
      expectedRevision: 1,
      evidenceFingerprint: "c".repeat(64)
    });
    expect(
      vi.mocked(api.projects.decideImportReview).mock.calls[0]?.[0]
    ).not.toHaveProperty("rationale");
    expect(api.projects.getImportReview).toHaveBeenCalledWith({
      projectId: "project-1",
      reviewId: "review-1",
      sourceDocumentId: "document-1",
      extractionId: "extraction-1",
      candidateStoryId: "story-1",
      candidateStoryRevision: 1,
      evidenceFingerprint: "c".repeat(64)
    });
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Analyze story" })
      ).toBeEnabled();
    });
  });

  it("does not pair an import review with an unrelated extraction", async () => {
    const detail = createProjectDetail({
      sourceDocuments: [createSourceDocument()],
      extractions: [createExtraction()],
      importReviews: [
        createImportReview({ extractionId: "extraction-missing" })
      ],
      analysisAllowed: false
    });
    render(<App api={createApi({ project: detail })} />);

    expect(
      await screen.findByRole("heading", { name: "Synthetic Demo" })
    ).toBeVisible();
    await waitFor(() => {
      expect(
        screen.queryByText("A bounded synthetic preview.")
      ).not.toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: "Approve import" })
    ).not.toBeInTheDocument();
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

  it("retains a newly created Phase 2 run and job after leaving Analysis", async () => {
    const runA = createPhase2Run("a", "2026-07-30T12:00:00Z");
    const runB = createPhase2Run("b", "2026-07-30T13:00:00Z");
    const jobA = createPhase2Job(runA);
    const jobB = createPhase2Job(runB);
    const importReview = createApprovedImportReview();
    const base = createProjectDetail({
      jobs: [jobA],
      sourceDocuments: [createSourceDocument()],
      extractions: [
        createExtraction({
          status: "complete",
          extractedTextSha256: "b".repeat(64),
          extractedCharacterCount: 72,
          sectionCount: 4,
          quality: {
            classification: "structured_extraction",
            confidence: 0.98
          },
          completedAt: "2026-07-30T11:55:00Z"
        })
      ],
      importReviews: [importReview],
      analysisAllowed: true
    });
    const detail: ProjectDetail = {
      ...base,
      currentAnalysisRun: runA,
      analysisGateReviews: createPhase2GateReviews(runA)
    };
    const api = createApi({ project: detail });
    vi.mocked(api.analysis.listRuns).mockResolvedValue(
      ok({
        correlationId: "correlation-phase2-runs",
        pageSize: 2,
        total: 2,
        runs: [runB, runA]
      })
    );
    vi.mocked(api.analysis.listReviews).mockImplementation(async (input) => {
      const selected = input.runId === runB.runId ? runB : runA;
      return ok({
        correlationId: `correlation-reviews-${selected.runId}`,
        runId: selected.runId,
        items: createPhase2GateReviews(selected)
      });
    });
    vi.mocked(api.analysis.listCorrections).mockImplementation(
      async (input) =>
        ok({
          correlationId: `correlation-corrections-${input.runId}`,
          runId: input.runId,
          pageSize: 0,
          total: 0,
          items: []
        })
    );
    vi.mocked(api.analysis.listEntities).mockImplementation(
      async (input) => {
        const selected = input.runId === runB.runId ? runB : runA;
        const snapshot = selected.currentSnapshot;
        if (snapshot === null) {
          return fail("SNAPSHOT_NOT_AVAILABLE");
        }
        return ok({
          correlationId: `correlation-entities-${selected.runId}`,
          runId: selected.runId,
          snapshotId: snapshot.snapshotId,
          collection: input.collection,
          pageSize: 0,
          total: 0,
          items: []
        });
      }
    );
    vi.mocked(api.analysis.createRun).mockResolvedValue(
      ok({
        correlationId: "correlation-create-run-b",
        run: runB,
        job: jobB
      })
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByText('"We should go."');
    await user.click(screen.getByRole("button", { name: "Analysis" }));
    expect(await screen.findByLabelText("Analysis run")).toHaveValue(
      runA.runId
    );
    await user.click(
      screen.getByRole("button", { name: "Rerun analysis" })
    );
    await waitFor(() => {
      expect(screen.getByLabelText("Analysis run")).toHaveValue(runB.runId);
    });

    await user.click(screen.getByRole("button", { name: "Story" }));
    expect(
      screen.getAllByRole("progressbar", {
        name: "Analyze whole book progress"
      })
    ).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Analysis" }));
    await waitFor(() => {
      expect(screen.getByLabelText("Analysis run")).toHaveValue(runB.runId);
    });
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
      importSelectedFile: vi.fn(async () => ok(null)),
      getImportReview: vi.fn(
        async (
          input: ImportReviewIdInput
        ): Promise<DesktopResult<ImportReviewResponse>> => {
          const review = project?.importReviews.find(
            (item) => item.reviewId === input.reviewId
          );
          return review === undefined
            ? fail<ImportReviewResponse>("IMPORT_REVIEW_NOT_FOUND")
            : ok({
                correlationId: "review-correlation",
                review
              });
        }
      ),
      decideImportReview: vi.fn(
        async (
          input: DecideImportReviewInput
        ): Promise<DesktopResult<DecideImportReviewResponse>> => {
          const review = project?.importReviews.find(
            (item) => item.reviewId === input.reviewId
          );
          if (review === undefined) {
            return fail("IMPORT_REVIEW_NOT_FOUND");
          }
          const decidedReview: ImportReview = {
            ...review,
            revision: review.revision + 1,
            state: input.decision,
            updatedAt: "2026-07-29T12:10:00Z"
          };
          return ok({
            correlationId: "decision-correlation",
            review: decidedReview,
            decision: {
              schemaVersion: "1.0.0",
              revision: 1,
              provenance: {
                origin: "human",
                recordedAt: "2026-07-29T12:10:00Z",
                actorId: "desktop-user"
              },
              decisionId: "decision-1",
              projectId: "project-1",
              gateId: "import_review",
              scope: {
                entityType: "imported_story",
                entityId: review.candidateStoryId,
                revision: review.candidateStoryRevision
              },
              decision: input.decision,
              actor: { type: "human", actorId: "desktop-user" },
              rationale: input.rationale ?? "",
              evidenceFingerprint: input.evidenceFingerprint,
              decidedAt: "2026-07-29T12:10:00Z",
              immutable: true
            },
            projectRevision: 4,
            analysisAllowed: input.decision === "approved"
          });
        }
      )
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
    analysis: {
      createRun: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
      ),
      listRuns: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
      ),
      getRun: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
      ),
      listEntities: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
      ),
      listCorrections: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
      ),
      appendCorrection: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
      ),
      listReviews: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
      ),
      decideReview: vi.fn(async () =>
        fail<never>("ANALYSIS_NOT_CONFIGURED")
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
  readonly sourceDocuments?: ProjectDetail["sourceDocuments"];
  readonly extractions?: ProjectDetail["extractions"];
  readonly importReviews?: ProjectDetail["importReviews"];
  readonly analysisAllowed?: boolean;
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
    sourceDocuments: options?.sourceDocuments ?? [],
    extractions: options?.extractions ?? [],
    importReviews: options?.importReviews ?? [],
    analysisAllowed: options?.analysisAllowed ?? true,
    currentAnalysisRun: null,
    analysisGateReviews: [],
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

function createExtraction(
  overrides?: Partial<DocumentExtractionSummary>
): DocumentExtractionSummary {
  return {
    schemaVersion: "1.0.0",
    revision: 1,
    provenance: {
      origin: "system",
      recordedAt: "2026-07-29T12:00:00Z",
      actorId: "document-extractor"
    },
    extractionId: "extraction-1",
    projectId: "project-1",
    sourceDocumentId: "document-1",
    declaredFormat: "docx",
    detectedFormat: "docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "pending",
    adapterId: "secure-ooxml",
    adapterVersion: "1.0.0",
    parserDependency: "lxml",
    parserVersion: "6.0.0",
    sourceSha256: "a".repeat(64),
    sourceByteCount: 512,
    warnings: [],
    quality: {
      classification: "pending",
      confidence: 0
    },
    retryability: "not_retryable",
    reviewRequired: true,
    originalPreserved: true,
    createdAt: "2026-07-29T12:00:00Z",
    updatedAt: "2026-07-29T12:00:00Z",
    ...overrides
  };
}

function createSourceDocument(): ProjectDetail["sourceDocuments"][number] {
  return {
    schemaVersion: "1.0.0",
    revision: 1,
    provenance: {
      origin: "import",
      recordedAt: "2026-07-29T12:00:00Z",
      actorId: "desktop-user"
    },
    documentId: "document-1",
    projectId: "project-1",
    displayName: "sample-story.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    declaredFormat: "docx",
    contentSha256: "a".repeat(64),
    byteLength: 512,
    importedAt: "2026-07-29T12:00:00Z",
    originalTextPreserved: true,
    originalBytesPreserved: true,
    storageKey: "sources/document-1",
    extractionStatus: "complete",
    sourceRevision: 1,
    warnings: []
  };
}

function createImportReview(
  overrides?: Partial<ImportReview>
): ImportReview {
  return {
    schemaVersion: "1.0.0",
    revision: 1,
    provenance: {
      origin: "system",
      recordedAt: "2026-07-29T12:01:00Z",
      actorId: "document-extractor"
    },
    reviewId: "review-1",
    projectId: "project-1",
    sourceDocumentId: "document-1",
    extractionId: "extraction-1",
    candidateStoryId: "story-1",
    candidateStoryRevision: 1,
    state: "pending",
    evidenceFingerprint: "c".repeat(64),
    previewText: "A bounded synthetic preview.",
    previewTruncated: false,
    warnings: [],
    createdAt: "2026-07-29T12:01:00Z",
    updatedAt: "2026-07-29T12:01:00Z",
    ...overrides
  };
}

function createApprovedImportReview(): ImportReview {
  const decidedAt = "2026-07-30T11:58:00Z";
  return createImportReview({
    revision: 2,
    state: "approved",
    latestDecision: {
      schemaVersion: "1.0.0",
      revision: 1,
      provenance: {
        origin: "human",
        recordedAt: decidedAt,
        actorId: "desktop-user"
      },
      decisionId: "import-decision-1",
      projectId: "project-1",
      gateId: "import_review",
      scope: {
        entityType: "imported_story",
        entityId: "story-1",
        revision: 1
      },
      decision: "approved",
      actor: {
        type: "human",
        actorId: "desktop-user"
      },
      rationale: "Synthetic import evidence reviewed.",
      evidenceFingerprint: sha("c"),
      decidedAt,
      immutable: true
    },
    updatedAt: decidedAt
  });
}

function createPhase2Run(
  suffix: "a" | "b",
  completedAt: string
): StoryAnalysisRun {
  const runId = `run-${suffix}`;
  const inputFingerprint = sha("d");
  const runFingerprint = suffix === "a" ? sha("e") : sha("f");
  const snapshotFingerprint = suffix === "a" ? sha("0") : sha("1");
  const correctionSetFingerprint =
    suffix === "a" ? sha("2") : sha("3");
  const summary = {
    agentExecutions: 11,
    chapters: 2,
    scenes: 2,
    beats: 3,
    characters: 2,
    mentions: 2,
    dialogueLines: 1,
    narrationSpans: 2,
    povSegments: 1,
    locations: 2,
    timelineEvents: 1,
    temporalConstraints: 1,
    relationships: 1,
    emotionalStates: 1,
    dramaticIntents: 1,
    continuityFindings: 0,
    corrections: 0
  };
  return {
    contractVersion: "2.0.0",
    runId,
    projectId: "project-1",
    storyId: "story-1",
    storyRevision: 1,
    storyFingerprint: sha("b"),
    sourceDocumentId: "document-1",
    sourceRevision: 1,
    sourceSha256: sha("a"),
    extractionId: "extraction-1",
    extractionRevision: 1,
    extractedTextSha256: sha("b"),
    importReviewId: "review-1",
    importReviewRevision: 2,
    importReviewDecisionId: "import-decision-1",
    approvedEvidenceFingerprint: sha("c"),
    inputFingerprint,
    runFingerprint,
    profile: {
      profileId: WHOLE_BOOK_ANALYSIS_PROFILE_ID,
      semanticVersion: WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
      fingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
    },
    producer: {
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION
    },
    agentVersions: PHASE_2_RUNTIME_AGENTS,
    jobId: `job-${suffix}`,
    status: "succeeded",
    currentStage: "complete",
    progress: 1,
    warnings: [],
    snapshotCount: 1,
    currentSnapshot: {
      contractVersion: "2.0.0",
      snapshotId: `snapshot-${suffix}`,
      runId,
      revision: 1,
      inputFingerprint,
      snapshotFingerprint,
      correctionSetFingerprint,
      counts: summary,
      collections: [],
      createdAt: completedAt,
      immutable: true
    },
    summary,
    reviewEligibility: "ready",
    createdAt: completedAt,
    updatedAt: completedAt,
    completedAt
  };
}

function createPhase2GateReviews(
  run: StoryAnalysisRun
): readonly AnalysisGateReview[] {
  const snapshot = run.currentSnapshot;
  if (snapshot === null) {
    throw new Error("The Phase 2 run fixture requires a snapshot.");
  }
  return [
    "story_structure_review",
    "character_registry_review",
    "dialogue_attribution_review",
    "whole_book_analysis_review"
  ].map((gateId, index) => ({
    contractVersion: "2.0.0" as const,
    reviewId: `gate-${run.runId}-${index + 1}`,
    projectId: run.projectId,
    gateId: gateId as AnalysisGateReview["gateId"],
    runId: run.runId,
    snapshotId: snapshot.snapshotId,
    state: "pending" as const,
    revision: 1,
    artifactFingerprint: sha("4"),
    evidenceFingerprint: sha("5"),
    evidence: {
      projectId: run.projectId,
      sourceDocumentId: run.sourceDocumentId,
      extractionId: run.extractionId,
      extractionRevision: run.extractionRevision,
      storyId: run.storyId,
      profileId: run.profile.profileId,
      profileFingerprint: run.profile.fingerprint,
      runId: run.runId,
      runFingerprint: run.runFingerprint,
      snapshotId: snapshot.snapshotId,
      snapshotRevision: snapshot.revision,
      snapshotFingerprint: snapshot.snapshotFingerprint,
      artifactFingerprint: sha("4"),
      evidenceFingerprint: sha("5")
    },
    openWarningIds: [],
    acknowledgedWarningIds: [],
    latestDecision: null,
    provenance: {
      origin: "human_review" as const,
      recordedAt: run.updatedAt,
      inputFingerprint: run.inputFingerprint,
      producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
      producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
      deterministic: true
    },
    updatedAt: run.updatedAt
  }));
}

function createPhase2Job(run: StoryAnalysisRun): Job {
  return createJob({
    jobId: run.jobId,
    type: "analyze_whole_book",
    state: "succeeded",
    inputRevision: run.storyRevision,
    inputFingerprint: run.inputFingerprint,
    stage: "complete",
    progress: 1,
    createdAt: run.createdAt,
    updatedAt: run.updatedAt
  });
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
