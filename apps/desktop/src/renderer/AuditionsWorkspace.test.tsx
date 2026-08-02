import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Job, ProjectDetail } from "@cinematic-story-studio/contracts/api";
import type {
  AuditionClip,
  AuditionGateId,
  AuditionReview,
  AuditionReviewDecision,
  AuditionRoleStatus,
  AuditionSession,
  AuditionWorkspaceSnapshot,
  PronunciationEntry,
  PronunciationDictionary,
  SpeechAuditionProvenance,
  SpeechPreviewRequest
} from "@cinematic-story-studio/contracts";

import type {
  CinematicStoryDesktopApi,
  DesktopResult
} from "../shared/desktop-api";
import type { DecideAuditionReviewInput } from "../shared/audition-api";
import { AuditionsWorkspace } from "./AuditionsWorkspace";

const digest = "a".repeat(64);
const otherDigest = "b".repeat(64);
const now = "2026-07-31T12:00:00Z";
const createObjectUrl = vi.fn(() => "blob:private-audition");
const revokeObjectUrl = vi.fn();

describe("AuditionsWorkspace", () => {
  beforeEach(() => {
    createObjectUrl.mockClear();
    revokeObjectUrl.mockClear();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectUrl
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectUrl
    });
  });

  it("renders local provider/model status, three role controls, cache history, and five gates", async () => {
    const api = createApi();
    renderWorkspace(api);

    expect(
      await screen.findByRole("heading", {
        name: "Auditions & Pronunciation"
      })
    ).toBeVisible();
    expect(screen.getByText("Fixture-provider clip · lifecycle evidence only")).toBeVisible();
    expect(screen.getByText("Real local provider · human listening still required")).toBeVisible();
    expect(screen.getByText("Python socket API denied")).toBeVisible();
    expect(screen.getByText("Not externally observed")).toBeVisible();
    expect(
      screen.getByText("Not externally observed means unknown, not zero.")
    ).toBeVisible();
    expect(screen.getByRole("cell", { name: "verified" })).toBeVisible();
    expect(
      screen.getAllByRole("button", { name: "Generate audition" })
    ).toHaveLength(3);
    expect(screen.getByText("Fixture-provider clip · Verified Hit")).toBeVisible();
    for (const gate of [
      "Per-Role Audition Review · role-narrator",
      "Narrator Audition Review",
      "Character Audition Review",
      "Pronunciation Review",
      "Voice Readiness Review"
    ]) {
      expect(screen.getByText(gate)).toBeVisible();
    }
    expect(api.auditions.listPronunciations).toHaveBeenCalledWith({
      projectId: "project-1",
      limit: 50,
      expectedDictionaryRevision: 1,
      expectedDictionaryFingerprint: digest
    });
  });

  it("renders an incompatible voice binding as unavailable without substituting a provider voice", async () => {
    const api = createApi();
    const workspace = workspaceSnapshot();
    const narrator = workspace.roles.items[0];
    if (narrator === undefined) throw new Error("The narrator fixture was unavailable.");
    const incompatible: AuditionRoleStatus = {
      ...narrator,
      voiceRuntimeBinding: null,
      runtimeBindingStatus: "incompatible",
      runtimeBindingReasonCode: "VOICE_RUNTIME_BINDING_INCOMPATIBLE",
      latestSessionId: null,
      latestClipId: null,
      sessionEvidence: null,
      generationRequest: null
    };
    vi.mocked(api.auditions.getWorkspace).mockResolvedValue(
      ok({
        correlationId: "correlation-incompatible-binding",
        workspace: {
          ...workspace,
          roles: {
            items: [incompatible, ...workspace.roles.items.slice(1)],
            pageSize: workspace.roles.pageSize,
            total: workspace.roles.total
          }
        }
      })
    );

    renderWorkspace(api);
    const label = await screen.findByText("Narrator");
    const card = label.closest("article");
    expect(card).not.toBeNull();
    const scoped = within(card as HTMLElement);
    expect(scoped.getAllByText("unavailable")).toHaveLength(2);
    expect(scoped.getByText(/Incompatible.*VOICE RUNTIME BINDING INCOMPATIBLE/u))
      .toBeVisible();
    expect(
      scoped.getByRole("button", { name: "Create audition session" })
    ).toBeDisabled();
    expect(scoped.queryByText(/fixture-provider \/ fixture-voice/u))
      .not.toBeInTheDocument();
    expect(api.auditions.createSession).not.toHaveBeenCalled();
    expect(api.auditions.generate).not.toHaveBeenCalled();
  });

  it("queues a server-issued exact request and loads verified bytes without autoplay", async () => {
    const api = createApi();
    const play = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWorkspace(api);
    await screen.findByText("Fixture-provider clip · Verified Hit");

    await user.click(
      screen.getAllByRole("button", { name: "Generate audition" })[0]
    );
    await waitFor(() => expect(api.auditions.generate).toHaveBeenCalledTimes(1));
    expect(api.auditions.generate).toHaveBeenCalledWith({
      projectId: "project-1",
      preview: previewRequest("role-narrator", "session-narrator")
    });

    await user.click(screen.getByRole("button", { name: "Load audio" }));
    await waitFor(() => expect(api.auditions.loadAudio).toHaveBeenCalledTimes(1));
    expect(api.auditions.loadAudio).toHaveBeenCalledWith({
      projectId: "project-1",
      auditionClipId: "clip-1",
      auditionSessionId: "session-narrator",
      audioArtifactId: "artifact-1",
      expectedClipRevision: 1,
      expectedClipFingerprint: digest,
      expectedArtifactSha256: digest,
      mediaType: "audio/wav",
      byteSize: 48
    });
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(play).not.toHaveBeenCalled();
  });

  it.each([
    {
      initialState: "queued" as const,
      action: "cancel" as const,
      button: "Cancel",
      controlState: "cancelled" as const,
      finalState: "cancelled" as const
    },
    {
      initialState: "failed" as const,
      action: "retry" as const,
      button: "Retry",
      controlState: "running" as const,
      finalState: "succeeded" as const
    },
    {
      initialState: "interrupted" as const,
      action: "resume" as const,
      button: "Resume",
      controlState: "running" as const,
      finalState: "succeeded" as const
    }
  ])(
    "renders current audition job evidence and handles $action with a bounded refresh",
    async ({ initialState, action, button, controlState, finalState }) => {
      const api = createApi();
      const session = {
        ...auditionSession(),
        state: initialState === "queued" ? "queued" as const : "failed" as const,
        jobId: "audition-job-1"
      };
      vi.mocked(api.auditions.listSessions).mockResolvedValue(
        ok({
          correlationId: "correlation-sessions-job",
          projectId: "project-1",
          pageSize: 1,
          total: 1,
          items: [session]
        })
      );
      const initialJob = auditionJob(initialState, 0.25, "synthesize_audio");
      const controlJob = auditionJob(controlState, 0.5, "publish_audio");
      const finalJob = auditionJob(finalState, 1, "complete");
      vi.mocked(api.jobs.get)
        .mockResolvedValueOnce(ok({ correlationId: "job-read-1", job: initialJob }))
        .mockResolvedValue(ok({ correlationId: "job-read-2", job: finalJob }));
      vi.mocked(api.jobs[action]).mockResolvedValue(
        ok({ correlationId: `job-${action}`, job: controlJob })
      );
      const user = userEvent.setup();
      renderWorkspace(api);

      const inspector = await screen.findByTestId("audition-job-inspector");
      expect(await within(inspector).findByText("Synthesize Audio")).toBeVisible();
      expect(within(inspector).getByText("25%")).toBeVisible();
      expect(within(inspector).getByText("Attempt 1")).toBeVisible();

      await user.click(within(inspector).getByRole("button", { name: button }));
      await waitFor(() => expect(api.jobs[action]).toHaveBeenCalledWith("audition-job-1"));
      await waitFor(() =>
        expect(within(inspector).getByText(humanizedJobState(finalState))).toBeVisible()
      );
      expect(api.jobs.get).toHaveBeenCalledTimes(2);
      expect(vi.mocked(api.auditions.getWorkspace).mock.calls.length)
        .toBeGreaterThanOrEqual(2);
    }
  );

  it("refreshes a terminal audition job once across fresh session identities", async () => {
    const api = createApi();
    const session = {
      ...auditionSession(),
      jobId: "audition-job-1"
    };
    vi.mocked(api.auditions.listSessions).mockImplementation(async () =>
      ok({
        correlationId: "correlation-sessions-terminal",
        projectId: "project-1",
        pageSize: 1,
        total: 1,
        items: [{ ...session }]
      })
    );
    vi.mocked(api.jobs.get)
      .mockResolvedValueOnce(
        ok({
          correlationId: "job-terminal",
          job: auditionJob("succeeded", 1, "complete")
        })
      )
      .mockResolvedValue(fail("NOT_CONFIGURED"));

    renderWorkspace(api);

    await waitFor(() =>
      expect(api.auditions.getWorkspace).toHaveBeenCalledTimes(2)
    );
    await waitFor(() =>
      expect(api.auditions.listSessions).toHaveBeenCalledTimes(2)
    );
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      });
    });

    expect(api.jobs.get).toHaveBeenCalledTimes(1);
    expect(api.auditions.getWorkspace).toHaveBeenCalledTimes(2);
  });

  it("creates an explicit governed pronunciation-test script", async () => {
    const api = createApi();
    vi.mocked(api.auditions.createSession).mockResolvedValue(
      ok({
        correlationId: "correlation-pronunciation-test-session",
        session: {
          ...auditionSession(),
          auditionSessionId: "session-pronunciation-test",
          scriptCount: 0,
          clipCount: 0
        }
      })
    );
    const user = userEvent.setup();
    renderWorkspace(api);

    const input = await screen.findByLabelText("Pronunciation-test text");
    await user.clear(input);
    await user.type(input, "Harbor");
    await user.click(
      screen.getByRole("button", { name: "Create pronunciation test" })
    );

    await waitFor(() =>
      expect(api.auditions.createScript).toHaveBeenCalledTimes(1)
    );
    const sessionInput = vi.mocked(api.auditions.createSession).mock.calls[0]?.[0];
    expect(sessionInput).toMatchObject({
      projectId: "project-1",
      roleId: "role-narrator"
    });
    expect(sessionInput?.evidence.castAssignmentId).toBe(
      "assignment-role-narrator"
    );
    expect(sessionInput?.idempotencyKey).toMatch(
      /^pronunciation-test-session-/u
    );
    const requestInput = vi.mocked(api.auditions.createScript).mock.calls[0]?.[0];
    expect(requestInput).toMatchObject({
      projectId: "project-1",
      auditionSessionId: "session-pronunciation-test",
      expectedSessionRevision: 1,
      kind: "pronunciation_test",
      text: "Harbor",
      sourceDocumentId: null,
      sourceRevision: null,
      sourceSpan: null,
      acceptedOptionalNormalizationIds: []
    });
    expect(requestInput?.sourceTextSha256).toMatch(/^[a-f0-9]{64}$/u);
    expect(requestInput?.idempotencyKey).toMatch(/^pronunciation_test-/u);
  });

  it("renders exact normalization detail and clip QC finding codes", async () => {
    const api = createApi();
    const detailedClip = {
      ...auditionClip(),
      audioQuality: {
        ...auditionClip().audioQuality,
        blockingFindingCodes: ["AUDITION_WAV_INVALID"],
        warningCodes: ["AUDITION_LOW_HEADROOM"]
      }
    };
    vi.mocked(api.auditions.listClips).mockResolvedValue(
      ok({
        correlationId: "correlation-qc-detail",
        projectId: "project-1",
        pageSize: 1,
        total: 1,
        items: [detailedClip]
      })
    );
    vi.mocked(api.auditions.previewNormalization).mockResolvedValue(
      ok({
        correlationId: "correlation-normalization-detail",
        projectId: "project-1",
        auditionSessionId: "session-role-narrator",
        auditionSessionRevision: 1,
        providerId: "fixture-provider",
        acceptedOptionalNormalizationIds: ["normalization-edit-1"],
        customPronunciationScopeIds: [],
        plan: {
          contractVersion: "1.0.0",
          normalizationPlanId: "normalization-plan-detail",
          projectId: "project-1",
          originalTextSha256: digest,
          normalizedTextSha256: otherDigest,
          providerId: "fixture-provider",
          profileId: "audition-text-normalization",
          profileVersion: "1.0.0",
          transformations: [
            {
              transformationId: "normalization-edit-1",
              kind: "typographic_quote",
              sourceSpan: { start: 0, end: 8 },
              destinationSpan: { start: 0, end: 8 },
              originalTextSha256: digest,
              replacementTextSha256: otherDigest,
              originalText: "“Harbor”",
              replacementText: '"Harbor"',
              reasonCode: "NORMALIZATION_TYPOGRAPHIC_QUOTE",
              requiredByProvider: false,
              humanApprovalRequired: true,
              approved: true
            }
          ],
          appliedPronunciationEntryIds: ["pronunciation-1"],
          unsupportedCharacterCodePoints: ["U+2603"],
          warnings: ["NORMALIZATION_REVIEW_REQUIRED"],
          humanReviewRequired: true,
          planFingerprint: digest,
          provenance: provenance("application")
        }
      })
    );
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.click(
      await screen.findByRole("button", { name: "Preview normalization" })
    );
    const plan = await screen.findByTestId("normalization-plan");
    expect(
      within(plan).getByTestId("normalization-review-state")
    ).toHaveTextContent("Review required");
    expect(within(plan).getByText("“Harbor”")).toBeVisible();
    expect(within(plan).getByText('"Harbor"')).toBeVisible();
    expect(within(plan).getAllByText("0–8")).toHaveLength(2);
    expect(within(plan).getByText("pronunciation-1")).toBeVisible();
    expect(within(plan).getByText("U+2603")).toBeVisible();
    expect(within(plan).getByText("NORMALIZATION_REVIEW_REQUIRED")).toBeVisible();
    expect(await screen.findByText("AUDITION_WAV_INVALID")).toBeVisible();
    expect(screen.getByText("AUDITION_LOW_HEADROOM")).toBeVisible();
  });

  it("loads complete immutable review history through bounded pages", async () => {
    const api = createApi();
    const newer = reviewDecision({
      decisionId: "decision-newer",
      reviewId: "review-narrator-v2",
      gateId: "narrator_audition_review",
      roleId: null,
      decision: "approved",
      rationale: "Approved after listening to the corrected local audition.",
      decidedAt: "2026-07-31T12:05:00Z",
      supersedesDecisionId: "decision-older",
      expectedReviewRevision: 2
    });
    const older = reviewDecision({
      decisionId: "decision-older",
      reviewId: "review-narrator-v1",
      gateId: "narrator_audition_review",
      roleId: null,
      decision: "changes_requested",
      rationale: "Requested a corrected pronunciation before approval.",
      decidedAt: "2026-07-31T12:00:00Z",
      supersedesDecisionId: null,
      expectedReviewRevision: 1
    });
    vi.mocked(api.auditions.listReviewDecisions)
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-history-1",
          projectId: "project-1",
          gateId: "narrator_audition_review",
          roleId: null,
          pageSize: 1,
          total: 2,
          nextCursor: "opaque-page-2",
          items: [newer]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-history-2",
          projectId: "project-1",
          gateId: "narrator_audition_review",
          roleId: null,
          pageSize: 1,
          total: 2,
          items: [older]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-history-restart",
          projectId: "project-1",
          gateId: "narrator_audition_review",
          roleId: null,
          pageSize: 1,
          total: 2,
          nextCursor: "opaque-page-2",
          items: [newer]
        })
      );
    const user = userEvent.setup();
    renderWorkspace(api);

    const reviewCard = (await screen.findByText("Narrator Audition Review"))
      .closest("article");
    expect(reviewCard).not.toBeNull();
    const card = within(reviewCard as HTMLElement);
    await user.click(card.getByRole("button", { name: "Load decision history" }));
    await waitFor(() =>
      expect(api.auditions.listReviewDecisions).toHaveBeenCalledWith({
        projectId: "project-1",
        gateId: "narrator_audition_review",
        roleId: null,
        limit: 50
      })
    );
    expect(await card.findByText(newer.rationale)).toBeVisible();
    expect(card.getByText("Page 1 · showing 1 of 2 immutable decisions")).toBeVisible();

    await user.click(card.getByRole("button", { name: "Next decision page" }));
    await waitFor(() =>
      expect(api.auditions.listReviewDecisions).toHaveBeenLastCalledWith({
        projectId: "project-1",
        gateId: "narrator_audition_review",
        roleId: null,
        cursor: "opaque-page-2",
        limit: 50
      })
    );
    expect(await card.findByText(older.rationale)).toBeVisible();
    expect(card.queryByText(newer.rationale)).not.toBeInTheDocument();
    expect(card.getByText("Page 2 · showing 1 of 2 immutable decisions")).toBeVisible();
    expect(card.getByText(/review-narrator-v1/u)).toBeVisible();
    await user.click(
      card.getByRole("button", { name: "Restart decision history" })
    );
    await waitFor(() =>
      expect(api.auditions.listReviewDecisions).toHaveBeenLastCalledWith({
        projectId: "project-1",
        gateId: "narrator_audition_review",
        roleId: null,
        limit: 50
      })
    );
    expect(await card.findByText(newer.rationale)).toBeVisible();
    expect(card.queryByText(older.rationale)).not.toBeInTheDocument();
  });

  it("renders unavailable artifact state without exposing an audio-load action", async () => {
    const api = createApi();
    const unavailableClip = {
      ...auditionClip(),
      state: "invalidated" as const,
      audioArtifact: {
        ...auditionClip().audioArtifact,
        availability: "purged" as const,
        playbackEligible: false
      }
    };
    vi.mocked(api.auditions.listClips).mockResolvedValue(
      ok({
        correlationId: "correlation-unavailable-clip",
        projectId: "project-1",
        pageSize: 1,
        total: 1,
        items: [unavailableClip]
      })
    );

    renderWorkspace(api);

    const unavailable = await screen.findByText("Purged · Playback unavailable");
    const clipCard = unavailable.closest("article");
    expect(clipCard).not.toBeNull();
    expect(
      within(clipCard as HTMLElement).queryByRole("button", {
        name: "Load audio"
      })
    ).not.toBeInTheDocument();
    expect(
      within(clipCard as HTMLElement).getByRole("status", {
        name: ""
      })
    ).toHaveTextContent("Playback unavailable");
    expect(api.auditions.loadAudio).not.toHaveBeenCalled();
  });

  it("appends a safe project pronunciation and records a human gate decision", async () => {
    const api = createApi();
    const user = userEvent.setup();
    renderWorkspace(api);
    await screen.findByText("Harbor");

    await user.type(screen.getByLabelText("Written form"), "Lantern");
    await user.type(screen.getByLabelText("Pronunciation"), "LAN-turn");
    await user.click(screen.getByRole("button", { name: "Add for review" }));
    await waitFor(() =>
      expect(api.auditions.appendPronunciation).toHaveBeenCalledTimes(1)
    );
    expect(api.auditions.appendPronunciation).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-1",
        expectedDictionaryRevision: 1,
        expectedDictionaryFingerprint: digest,
        writtenForm: "Lantern",
        pronunciation: "LAN-turn",
        scope: "project",
        scopeId: null,
        representation: "provider_neutral"
      })
    );

    const reviewCard = screen
      .getByText("Narrator Audition Review")
      .closest("article");
    expect(reviewCard).not.toBeNull();
    const card = within(reviewCard as HTMLElement);
    fireEvent.change(card.getByLabelText("Decision rationale"), {
      target: { value: "Listened locally and reviewed exact evidence." }
    });
    await user.click(card.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(api.auditions.decideReview).toHaveBeenCalledTimes(1)
    );
    expect(api.auditions.decideReview).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-1",
        gateId: "narrator_audition_review",
        expectedReviewRevision: 1,
        expectedEvidenceFingerprint: digest,
        decision: "approve",
        rationale: "Listened locally and reviewed exact evidence."
      })
    );
  });

  it("records typed pronunciation decisions and appends bounded revisions", async () => {
    const api = createApi();
    const user = userEvent.setup();
    renderWorkspace(api);
    await screen.findByText("Harbor");

    await user.type(
      screen.getByLabelText("Pronunciation decision rationale"),
      "Reviewed the local pronunciation evidence."
    );
    await user.click(screen.getByRole("button", { name: "Approve entry" }));
    await waitFor(() =>
      expect(api.auditions.decidePronunciation).toHaveBeenCalledWith(
        expect.objectContaining({
          projectId: "project-1",
          entryId: "pronunciation-1",
          expectedEntryRevision: 1,
          expectedEntryFingerprint: digest,
          expectedDictionaryRevision: 1,
          expectedDictionaryFingerprint: digest,
          decision: "approve",
          rationale: "Reviewed the local pronunciation evidence."
        })
      )
    );

    await user.click(
      screen.getByRole("button", { name: "Revise append-only entry" })
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "prior entry remains immutable"
    );
    await user.clear(screen.getByLabelText("Pronunciation"));
    await user.type(screen.getByLabelText("Pronunciation"), "HAR-bur");
    await user.click(screen.getByRole("button", { name: "Append revision" }));
    await waitFor(() =>
      expect(api.auditions.appendPronunciation).toHaveBeenLastCalledWith(
        expect.objectContaining({
          supersedesEntryId: "pronunciation-1",
          writtenForm: "Harbor",
          pronunciation: "HAR-bur"
        })
      )
    );
  }, 10_000);

  it("creates a role session only from server-issued hash evidence", async () => {
    const api = createApi();
    const workspace = workspaceSnapshot();
    const firstRole = workspace.roles.items[0];
    if (firstRole === undefined) throw new Error("Missing narrator fixture role.");
    const sessionlessWorkspace = {
      ...workspace,
      roles: {
        ...workspace.roles,
        items: [
          {
            ...firstRole,
            latestSessionId: null,
            latestClipId: null,
            generationRequest: null
          },
          ...workspace.roles.items.slice(1)
        ]
      }
    };
    vi.mocked(api.auditions.getWorkspace).mockResolvedValue(
      ok({
        correlationId: "correlation-sessionless-workspace",
        workspace: sessionlessWorkspace
      })
    );
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.click(
      await screen.findByRole("button", { name: "Create audition session" })
    );
    await waitFor(() =>
      expect(api.auditions.createSession).toHaveBeenCalledTimes(1)
    );
    const createSessionInput =
      vi.mocked(api.auditions.createSession).mock.calls[0]?.[0];
    expect(createSessionInput?.idempotencyKey).toMatch(/^audition-session-/u);
    expect(createSessionInput).toEqual({
        projectId: "project-1",
        roleId: firstRole.roleId,
        evidence: firstRole.sessionEvidence,
        idempotencyKey: createSessionInput?.idempotencyKey
      });
  });

  it("clears only the project-owned audition cache with an explicit reason", async () => {
    const api = createApi();
    const user = userEvent.setup();
    renderWorkspace(api);
    await screen.findByText("Fixture-provider clip · Verified Hit");

    await user.click(screen.getByText("Private cache maintenance"));
    await user.click(
      screen.getByRole("button", { name: "Clear private audition cache" })
    );
    await waitFor(() =>
      expect(api.auditions.clearCache).toHaveBeenCalledTimes(1)
    );
    const clearCacheInput =
      vi.mocked(api.auditions.clearCache).mock.calls[0]?.[0];
    expect(clearCacheInput?.idempotencyKey).toMatch(/^audition-cache-clear-/u);
    expect(clearCacheInput).toEqual({
        projectId: "project-1",
        expectedProjectRevision: 1,
        reason: "User requested removal of private audition cache artifacts.",
        idempotencyKey: clearCacheInput?.idempotencyKey
      });
  });

  it("requires restricted-use acknowledgement and never sends the selected ZIP path", async () => {
    const api = createApi();
    vi.mocked(api.auditions.listModelPackages).mockResolvedValue(
      ok({
        correlationId: "correlation-real-model",
        projectId: "project-1",
        pageSize: 1,
        total: 1,
        items: [
          {
            manifest: realModelManifest(),
            installation: null,
            verification: null
          }
        ]
      })
    );
    const user = userEvent.setup();
    renderWorkspace(api);

    const install = await screen.findByRole("button", {
      name: "Choose ZIP & install"
    });
    expect(install).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", {
        name: /I acknowledge these model and voice bytes/u
      })
    );
    expect(install).toBeEnabled();
    await user.click(install);
    await waitFor(() =>
      expect(api.auditions.selectLocalModelPackage).toHaveBeenCalledTimes(1)
    );
    const request = vi.mocked(api.auditions.selectLocalModelPackage).mock
      .calls[0]?.[0];
    expect(request).toMatchObject({
      projectId: "project-1",
      modelPackageId: "kokoro-package-1",
      expectedManifestFingerprint: otherDigest,
      expectedInstallationRevision: null,
      operation: "install",
      acknowledgeRestrictedLocalUse: true
    });
    expect(request).not.toHaveProperty("sourcePath");
  });

  it("pages every governed role while rendering only one bounded role page", async () => {
    const api = createApi();
    const workspace = workspaceSnapshot();
    const roles = Array.from({ length: 14 }, (_, index) =>
      role(
        `role-paged-${index + 1}`,
        `Paged role ${index + 1}`,
        index === 0 ? "narrator" : "character",
        `session-paged-${index + 1}`
      )
    );
    const firstPage = {
      items: roles.slice(0, 12),
      pageSize: 12,
      total: 14,
      nextCursor: "role-page-2"
    };
    const secondPage = {
      items: roles.slice(12),
      pageSize: 2,
      total: 14
    };
    vi.mocked(api.auditions.getWorkspace)
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-role-page-1",
          workspace: { ...workspace, roles: firstPage }
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-role-page-2",
          workspace: { ...workspace, roles: secondPage }
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-role-page-1-previous",
          workspace: { ...workspace, roles: firstPage }
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-role-page-2-again",
          workspace: { ...workspace, roles: secondPage }
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-role-page-1-restart",
          workspace: { ...workspace, roles: firstPage }
        })
      );
    const user = userEvent.setup();
    renderWorkspace(api);

    expect(await screen.findByText("Paged role 1")).toBeVisible();
    expect(screen.getByText("Paged role 12")).toBeVisible();
    expect(screen.queryByText("Paged role 13")).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Generate audition" })
    ).toHaveLength(12);
    await user.click(screen.getByRole("button", { name: "Next roles" }));
    expect(await screen.findByText("Paged role 13")).toBeVisible();
    expect(screen.getByText("Paged role 14")).toBeVisible();
    expect(screen.queryByText("Paged role 1")).not.toBeInTheDocument();
    expect(screen.getByText("Page 2 of 2")).toBeVisible();
    expect(api.auditions.getWorkspace).toHaveBeenNthCalledWith(1, {
      projectId: "project-1",
      roleLimit: 12
    });
    expect(api.auditions.getWorkspace).toHaveBeenNthCalledWith(2, {
      projectId: "project-1",
      roleCursor: "role-page-2",
      roleLimit: 12
    });
    await user.click(screen.getByRole("button", { name: "Previous roles" }));
    expect(await screen.findByText("Paged role 1")).toBeVisible();
    expect(screen.queryByText("Paged role 13")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next roles" }));
    expect(await screen.findByText("Paged role 13")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Restart roles" }));
    expect(await screen.findByText("Paged role 1")).toBeVisible();
    expect(screen.queryByText("Paged role 13")).not.toBeInTheDocument();
    expect(api.auditions.getWorkspace).toHaveBeenNthCalledWith(5, {
      projectId: "project-1",
      roleLimit: 12
    });
  });

  it("replaces bounded cursor pages for models, sessions, pronunciations, and clips", async () => {
    const api = createApi();
    const workspace = workspaceSnapshot();
    const firstSession = auditionSession();
    const secondSession = {
      ...firstSession,
      auditionSessionId: "session-paged-2",
      roleId: "paged-session-role",
      sessionFingerprint: otherDigest
    };
    const firstClip = auditionClip();
    const secondClip = {
      ...firstClip,
      auditionClipId: "clip-paged-2",
      auditionSessionId: secondSession.auditionSessionId,
      roleId: "paged-clip-role",
      clipFingerprint: otherDigest,
      audioArtifact: {
        ...firstClip.audioArtifact,
        audioArtifactId: "artifact-paged-2",
        storageKey: "artifact-storage-paged-2",
        sha256: otherDigest
      },
      audioQuality: {
        ...firstClip.audioQuality,
        qualityRecordId: "quality-paged-2",
        audioArtifactId: "artifact-paged-2"
      }
    };
    const firstPronunciation = pronunciationEntry();
    const secondPronunciation = {
      ...firstPronunciation,
      entryId: "pronunciation-paged-2",
      writtenForm: "Lantern",
      normalizedLookupForm: "lantern",
      pronunciation: "LAN-turn",
      entryFingerprint: otherDigest
    };
    const fixtureModelResponse = {
      manifest: {
        contractVersion: "1.0.0" as const,
        manifestVersion: "1.0.0",
        modelPackageId: "fixture-model-package",
        providerId: "fixture-provider",
        modelId: "fixture-model",
        modelVersion: "1.0.0",
        runtimeVersion: "1.0.0",
        platform: "windows" as const,
        architecture: "x64" as const,
        sourceClassification: "repository_fixture" as const,
        officialSourceReference: "repository-fixture",
        licenseIdentifier: "fixture-only",
        commercialUseClassification: "fixture_only" as const,
        attributionRequirements: [],
        files: [],
        totalExpandedByteSize: 0,
        requiredRuntimeDependencies: [],
        compatibilityConstraints: [],
        state: "active" as const,
        manifestFingerprint: digest,
        provenance: provenance("fixture_provider")
      },
      installation: workspace.modelInstallations[0] ?? null,
      verification: workspace.modelVerifications[0] ?? null
    };

    vi.mocked(api.auditions.listModelPackages)
      .mockReset()
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-model-page-1",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          nextCursor: "model-next",
          items: [fixtureModelResponse]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-model-page-2",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          items: [
            {
              manifest: realModelManifest(),
              installation: null,
              verification: null
            }
          ]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-model-page-1-previous",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          nextCursor: "model-next",
          items: [fixtureModelResponse]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-model-page-2-again",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          items: [
            {
              manifest: realModelManifest(),
              installation: null,
              verification: null
            }
          ]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-model-page-1-restart",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          nextCursor: "model-next",
          items: [fixtureModelResponse]
        })
      );
    vi.mocked(api.auditions.listSessions)
      .mockReset()
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-session-page-1",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          nextCursor: "session-next",
          items: [firstSession]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-session-page-2",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          items: [secondSession]
        })
      );
    vi.mocked(api.auditions.listPronunciations)
      .mockReset()
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-pronunciation-page-1",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          nextCursor: "pronunciation-next",
          dictionary: dictionary(),
          items: [firstPronunciation]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-pronunciation-page-2",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          dictionary: dictionary(),
          items: [secondPronunciation]
        })
      );
    vi.mocked(api.auditions.listClips)
      .mockReset()
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-clip-page-1",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          nextCursor: "clip-next",
          items: [firstClip]
        })
      )
      .mockResolvedValueOnce(
        ok({
          correlationId: "correlation-clip-page-2",
          projectId: "project-1",
          pageSize: 1,
          total: 2,
          items: [secondClip]
        })
      );

    const user = userEvent.setup();
    renderWorkspace(api);
    await user.click(
      await screen.findByRole("button", { name: "Next model packages" })
    );
    const modelTable = screen.getByRole("table", {
      name: "Installed model packages and verification"
    });
    expect(
      await within(modelTable).findByText(
        "onnx-community/Kokoro-82M-v1.0-ONNX"
      )
    ).toBeVisible();
    expect(within(modelTable).queryByText("fixture-model")).not.toBeInTheDocument();
    expect(within(modelTable).getAllByRole("row")).toHaveLength(2);

    await user.click(
      screen.getByRole("button", { name: "Previous model packages" })
    );
    expect(await within(modelTable).findByText("fixture-model")).toBeVisible();
    expect(
      within(modelTable).queryByText("onnx-community/Kokoro-82M-v1.0-ONNX")
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Next model packages" })
    );
    await within(modelTable).findByText("onnx-community/Kokoro-82M-v1.0-ONNX");
    await user.click(
      screen.getByRole("button", { name: "Restart model packages" })
    );
    expect(await within(modelTable).findByText("fixture-model")).toBeVisible();
    expect(api.auditions.listModelPackages).toHaveBeenLastCalledWith({
      projectId: "project-1",
      limit: 50
    });

    await user.click(
      screen.getByRole("button", { name: "Next audition sessions" })
    );
    expect(
      await screen.findByRole("option", { name: /paged-session-role/u })
    ).toBeVisible();
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(
      screen.queryByRole("option", { name: /role-narrator/u })
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Next pronunciation entries" })
    );
    expect(
      await screen.findByText("Lantern", {
        selector: ".pronunciation-list strong"
      })
    ).toBeVisible();
    expect(
      screen.queryByText("Harbor", { selector: ".pronunciation-list strong" })
    ).not.toBeInTheDocument();
    expect(document.querySelectorAll(".pronunciation-list > li")).toHaveLength(1);

    const firstClipList = document.querySelector(".clip-list");
    expect(firstClipList).not.toBeNull();
    await user.click(
      within(firstClipList as HTMLElement).getByRole("checkbox", {
        name: "Compare"
      })
    );
    await user.click(
      within(firstClipList as HTMLElement).getByRole("button", {
        name: "Load audio"
      })
    );
    await waitFor(() => expect(createObjectUrl).toHaveBeenCalledTimes(1));
    expect(screen.getByText("1/3 compared")).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "Next audition clips" })
    );
    const clipList = document.querySelector(".clip-list");
    expect(clipList).not.toBeNull();
    expect(
      await within(clipList as HTMLElement).findByText("paged-clip-role")
    ).toBeVisible();
    expect(
      within(clipList as HTMLElement).queryByText("role-narrator")
    ).not.toBeInTheDocument();
    expect(within(clipList as HTMLElement).getAllByRole("article")).toHaveLength(1);
    expect(screen.getByText("0/3 compared")).toBeVisible();
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:private-audition");

    expect(api.auditions.listSessions).toHaveBeenLastCalledWith({
      projectId: "project-1",
      cursor: "session-next",
      limit: 50
    });
    expect(api.auditions.listPronunciations).toHaveBeenLastCalledWith({
      projectId: "project-1",
      cursor: "pronunciation-next",
      limit: 50,
      expectedDictionaryRevision: 1,
      expectedDictionaryFingerprint: digest
    });
    expect(api.auditions.listClips).toHaveBeenLastCalledWith({
      projectId: "project-1",
      cursor: "clip-next",
      limit: 50
    });
    expect(
      screen.getAllByText("Page 2 · showing 1 of 2", { exact: false })
    ).toHaveLength(3);
  }, 15_000);

  it("removes only an exact inactive model revision through the governed action", async () => {
    const api = createApi();
    const workspace = workspaceSnapshot();
    const currentInstallation = workspace.modelInstallations[0];
    if (currentInstallation === undefined) {
      throw new Error("The model installation fixture was unavailable.");
    }
    const inactiveInstallation = {
      ...currentInstallation,
      installationRevision: 2,
      status: "inactive" as const,
      active: false,
      lastAction: "deactivate" as const
    };
    vi.mocked(api.auditions.listModelPackages).mockResolvedValue(
      ok({
        correlationId: "correlation-inactive-model",
        projectId: "project-1",
        pageSize: 1,
        total: 1,
        items: [
          {
            manifest: {
              ...realModelManifest(),
              modelPackageId: inactiveInstallation.modelPackageId,
              manifestFingerprint: inactiveInstallation.manifestFingerprint
            },
            installation: inactiveInstallation,
            verification: workspace.modelVerifications[0] ?? null
          }
        ]
      })
    );
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.click(
      await screen.findByRole("button", { name: "Remove inactive model" })
    );
    await waitFor(() =>
      expect(api.auditions.performModelPackageAction).toHaveBeenCalledTimes(1)
    );
    const removeModelInput =
      vi.mocked(api.auditions.performModelPackageAction).mock.calls[0]?.[0];
    expect(removeModelInput?.idempotencyKey).toMatch(/^model-remove-/u);
    expect(removeModelInput).toEqual({
        projectId: "project-1",
        modelPackageId: inactiveInstallation.modelPackageId,
        expectedManifestFingerprint: inactiveInstallation.manifestFingerprint,
        expectedInstallationRevision: 2,
        action: "remove",
        reason:
          "User requested governed removal of this exact inactive local model package.",
        idempotencyKey: removeModelInput?.idempotencyKey
      });
  });
});

function renderWorkspace(api: CinematicStoryDesktopApi) {
  return render(
    <AuditionsWorkspace
      project={projectDetail()}
      api={api}
      connected
      onNotice={vi.fn()}
      onError={vi.fn()}
    />
  );
}

function createApi(): CinematicStoryDesktopApi {
  const workspace = workspaceSnapshot();
  const session = auditionSession();
  const clip = auditionClip();
  const api = {
    auditions: {
      getWorkspace: vi.fn(async () =>
        ok({ correlationId: "correlation-workspace", workspace })
      ),
      listModelPackages: vi.fn(async () =>
        ok({
          correlationId: "correlation-models",
          projectId: "project-1",
          pageSize: 1,
          total: 1,
          items: [
            {
              manifest: {
                contractVersion: "1.0.0",
                manifestVersion: "1.0.0",
                modelPackageId: "fixture-model-package",
                providerId: "fixture-provider",
                modelId: "fixture-model",
                modelVersion: "1.0.0",
                runtimeVersion: "1.0.0",
                platform: "windows",
                architecture: "x64",
                sourceClassification: "repository_fixture",
                officialSourceReference: "repository-fixture",
                licenseIdentifier: "fixture-only",
                commercialUseClassification: "fixture_only",
                attributionRequirements: [],
                files: [
                  {
                    relativePath: "model.onnx",
                    byteSize: 1,
                    sha256: digest,
                    mediaClassification: "onnx",
                    executable: false
                  }
                ],
                totalExpandedByteSize: 1,
                requiredRuntimeDependencies: [],
                compatibilityConstraints: [],
                state: "active",
                manifestFingerprint: digest,
                provenance: provenance("fixture_provider")
              },
              installation: workspace.modelInstallations[0],
              verification: workspace.modelVerifications[0]
            }
          ]
        })
      ),
      performModelPackageAction: vi.fn(async () =>
        ok({
          correlationId: "correlation-model-action",
          installation: workspace.modelInstallations[0],
          verification: workspace.modelVerifications[0]
        })
      ),
      selectLocalModelPackage: vi.fn(async () =>
        ok({
          correlationId: "correlation-local-model-package",
          installation: workspace.modelInstallations[0],
          verification: workspace.modelVerifications[0]
        })
      ),
      listPronunciations: vi.fn(async () =>
        ok({
          correlationId: "correlation-pronunciations",
          projectId: "project-1",
          pageSize: 1,
          total: 1,
          dictionary: dictionary(),
          items: [pronunciationEntry()]
        })
      ),
      appendPronunciation: vi.fn(async () =>
        ok({
          correlationId: "correlation-pronunciation",
          entry: pronunciationEntry(),
          dictionary: dictionary(),
          invalidatedClipIds: ["clip-1"],
          invalidatedClipCount: 1,
          invalidatedClipIdsTruncated: false,
          preservedClipIds: [],
          preservedClipCount: 0,
          preservedClipIdsTruncated: false,
          invalidatedGateIds: [
            "pronunciation_review",
            "voice_readiness_review"
          ]
        })
      ),
      decidePronunciation: vi.fn(async () =>
        ok({
          correlationId: "correlation-pronunciation-decision",
          entry: pronunciationEntry(),
          dictionary: dictionary(),
          invalidatedClipIds: [],
          invalidatedClipCount: 0,
          invalidatedClipIdsTruncated: false,
          preservedClipIds: ["clip-1"],
          preservedClipCount: 1,
          preservedClipIdsTruncated: false,
          invalidatedGateIds: []
        })
      ),
      clearCache: vi.fn(async () =>
        ok({
          correlationId: "correlation-cache-clear",
          projectId: "project-1",
          clearedRecordCount: 2,
          alreadyClearedRecordCount: 1,
          projectRevision: 2,
          purgedArtifactCount: 2
        })
      ),
      listSessions: vi.fn(async () =>
        ok({
          correlationId: "correlation-sessions",
          projectId: "project-1",
          pageSize: 1,
          total: 1,
          items: [session]
        })
      ),
      createSession: vi.fn(async () =>
        ok({ correlationId: "correlation-session", session })
      ),
      createScript: vi.fn(async () =>
        fail("NOT_CONFIGURED")
      ),
      previewNormalization: vi.fn(async () =>
        fail("NOT_CONFIGURED")
      ),
      generate: vi.fn(async () =>
        ok({
          correlationId: "correlation-generate",
          session,
          providerRequest: {
            contractVersion: "1.0.0",
            providerRequestId: "provider-request-1",
            speechPreviewRequestId: "preview-request-role-narrator",
            providerId: "fixture-provider",
            providerVersion: "1.0.0",
            modelId: "fixture-model",
            modelVersion: "1.0.0",
            modelPackageFingerprint: digest,
            runtimeProfileId: "runtime-profile-1",
            runtimeProfileFingerprint: digest,
            runtimeInstanceId: null,
            voiceProfileId: "voice-role-narrator",
            voiceProfileVersion: "1.0.0",
            voiceRuntimeBindingId: "voice-runtime-binding-role-narrator",
            voiceRuntimeBindingFingerprint: digest,
            providerVoiceId: "fixture-voice-role-narrator",
            castAssignmentId: "assignment-role-narrator",
            castAssignmentRevision: 1,
            auditionSessionId: "session-narrator",
            normalizedTextSha256: digest,
            pronunciationPlanFingerprint: digest,
            providerControlFingerprint: digest,
            cacheKey: digest,
            state: "queued",
            startedAt: null,
            finishedAt: null,
            retryable: false,
            warnings: [],
            requestFingerprint: digest,
            provenance: {
              ...provenance("application"),
              inputFingerprint: digest,
              details: {
                providerLanguage: "en-US",
                providerVoiceId: "fixture-voice-role-narrator",
                restrictedLocalUseAcknowledged: false,
                restrictedLocalUseAcknowledgementEventId: null,
                voiceRuntimeBindingFingerprint: digest,
                voiceRuntimeBindingId: "voice-runtime-binding-role-narrator",
                executionClassification: "provider_execution",
                providerDispatchCount: 0
              }
            }
          },
          jobId: "audition-job-1"
        })
      ),
      listClips: vi.fn(async () =>
        ok({
          correlationId: "correlation-clips",
          projectId: "project-1",
          pageSize: 1,
          total: 1,
          items: [clip]
        })
      ),
      listReviewDecisions: vi.fn(async () =>
        ok({
          correlationId: "correlation-review-history",
          projectId: "project-1",
          gateId: "voice_readiness_review" as const,
          roleId: null,
          pageSize: 0,
          total: 0,
          items: []
        })
      ),
      loadAudio: vi.fn(async () =>
        ok({
          projectId: "project-1",
          auditionClipId: "clip-1",
          auditionSessionId: "session-narrator",
          audioArtifactId: "artifact-1",
          mediaType: "audio/wav",
          byteSize: 48,
          sha256: digest,
          bytes: new Uint8Array(48).buffer
        })
      ),
      decideReview: vi.fn(async (input: DecideAuditionReviewInput) => {
        const review = workspace.reviews.find(
          (item) => item.reviewId === input.reviewId
        )!;
        return ok({
          correlationId: "correlation-review",
          review: { ...review, state: "approved" as const, revision: 2 },
          decision: {
            contractVersion: "1.0.0",
            decisionId: "decision-new",
            reviewId: review.reviewId,
            projectId: "project-1",
            gateId: review.gateId,
            roleId: review.roleId,
            decision: "approved",
            actor: { classification: "human", actorId: "desktop-user" },
            expectedReviewRevision: 1,
            evidenceFingerprint: digest,
            rationale: input.rationale,
            decidedAt: now,
            immutable: true,
            supersedesDecisionId: null,
            provenance: provenance("human")
          },
          voiceReadinessSnapshot: null
        })
      })
    },
    jobs: {
      get: vi.fn(async () => fail("NOT_CONFIGURED")),
      cancel: vi.fn(async () => fail("NOT_CONFIGURED")),
      retry: vi.fn(async () => fail("NOT_CONFIGURED")),
      resume: vi.fn(async () => fail("NOT_CONFIGURED"))
    }
  };
  return api as unknown as CinematicStoryDesktopApi;
}

function workspaceSnapshot(): AuditionWorkspaceSnapshot {
  return {
    contractVersion: "1.0.0",
    projectId: "project-1",
    prerequisites: [
      {
        prerequisiteId: "import_review",
        current: true,
        statusCode: "approved",
        evidenceId: "import-decision-1",
        evidenceFingerprint: digest
      }
    ],
    approvedCastSnapshot: {
      snapshotId: "cast-snapshot-1",
      revision: 1,
      fingerprint: digest
    },
    providers: [
      provider("fixture-provider", "Deterministic fixture", "deterministic_fixture", false),
      provider("real-provider", "Kokoro local", "real_local", true)
    ],
    runtimeProfiles: [
      {
        contractVersion: "1.0.0",
        runtimeProfileId: "runtime-profile-1",
        revision: 1,
        runtimeDescriptorId: "runtime-descriptor-1",
        providerIds: ["fixture-provider", "real-provider"],
        compatibleModelPackageIds: ["fixture-model-package"],
        protocolVersion: "1.0.0",
        startupDeadlineMilliseconds: 30_000,
        requestDeadlineMilliseconds: 30_000,
        idleShutdownMilliseconds: 60_000,
        maximumRetryAttempts: 0,
        maximumConcurrentRequests: 1,
        shellUsed: false,
        networkAccessDuringSynthesis: false,
        profileFingerprint: digest,
        active: true,
        provenance: provenance("application")
      }
    ],
    runtimeHealth: [
      runtimeHealth("fixture-provider"),
      runtimeHealth("real-provider")
    ],
    runtimeInstances: [
      {
        contractVersion: "1.0.0",
        runtimeInstanceId: "runtime-instance-1",
        runtimeProfileId: "runtime-profile-1",
        runtimeProfileFingerprint: digest,
        providerId: "fixture-provider",
        modelPackageFingerprint: digest,
        workerPid: 4102,
        parentPid: 4101,
        executableIdentity: "cinematic-story-service.exe",
        executableSha256: digest,
        creationIdentity: digest,
        protocolVersion: "1.0.0",
        handshakeAuthenticated: true,
        state: "idle",
        startedAt: now,
        lastActivityAt: now,
        stoppedAt: null,
        stopReasonCode: null,
        shutdownAcknowledged: null,
        gracefulShutdownConfirmed: null,
        exitCode: null,
        terminatedByParent: null,
        ownershipConfirmed: null,
        confirmedExited: null,
        ownedProcessesConfirmedExited: null,
        jobObjectAssigned: true,
        deniedNetworkAttemptCount: 0,
        networkPolicy: "python_socket_api_denied",
        observedNetworkRequestCount: null,
        restartReconciliation: null,
        provenance: provenance("application")
      }
    ],
    modelInstallations: [
      {
        contractVersion: "1.0.0",
        installationId: "installation-1",
        modelPackageId: "fixture-model-package",
        manifestFingerprint: digest,
        installationRevision: 1,
        storageKey: "model-storage-1",
        status: "active",
        active: true,
        installedAt: now,
        updatedAt: now,
        lastAction: "activate",
        actionReasonCode: "fixture_ready",
        immutableEventId: "model-event-1",
        provenance: provenance("application")
      }
    ],
    modelVerifications: [
      {
        contractVersion: "1.0.0",
        verificationId: "verification-1",
        installationId: "installation-1",
        modelPackageId: "fixture-model-package",
        manifestFingerprint: digest,
        verificationFingerprint: digest,
        status: "verified",
        verifiedFileCount: 1,
        verifiedByteSize: 1,
        unexpectedFileCount: 0,
        symlinkOrReparsePointDetected: false,
        checkedAt: now,
        blockingReasonCodes: [],
        provenance: provenance("application")
      }
    ],
    currentDictionary: dictionary(),
    roles: {
      items: [
        role("role-narrator", "Narrator", "narrator", "session-narrator"),
        role("role-character-1", "Avery", "character", "session-character-1"),
        role("role-character-2", "Morgan", "character", "session-character-2")
      ],
      pageSize: 3,
      total: 3
    },
    reviews: [
      review("per_role_audition_review", "review-role", "role-narrator"),
      review("narrator_audition_review", "review-narrator", null),
      review("character_audition_review", "review-character", null),
      review("pronunciation_review", "review-pronunciation", null),
      review("voice_readiness_review", "review-readiness", null)
    ],
    voiceReadinessSnapshot: null,
    updatedAt: now
  };
}

function provider(
  providerId: string,
  displayName: string,
  providerClass: "deterministic_fixture" | "real_local",
  exportEligible: boolean
) {
  return {
    contractVersion: "1.0.0" as const,
    providerId,
    providerVersion: "1.0.0",
    adapterId: `${providerId}-adapter`,
    adapterVersion: "1.0.0",
    providerClass,
    displayName,
    synthesisImplemented: true,
    localOnly: true as const,
    networkRequired: false as const,
    credentialsRequired: false as const,
    deterministic: providerClass === "deterministic_fixture",
    productionExportEligible: exportEligible,
    supportedLanguages: ["en"],
    outputFormats: ["pcm_s16le_wav" as const],
    supportedSampleRatesHz: [24_000],
    licenseIdentifier: providerClass === "real_local" ? "Apache-2.0" : "fixture-only",
    commercialUseClassification: providerClass === "real_local" ? "allowed" as const : "fixture_only" as const,
    attributionRequired: false,
    status: "available" as const,
    statusReasonCode: null,
    descriptorFingerprint: digest,
    provenance: provenance(providerClass === "real_local" ? "real_local_provider" : "fixture_provider")
  };
}

function realModelManifest() {
  return {
    contractVersion: "1.0.0" as const,
    manifestVersion: "1.0.0",
    modelPackageId: "kokoro-package-1",
    providerId: "real-provider",
    modelId: "onnx-community/Kokoro-82M-v1.0-ONNX",
    modelVersion: "1.0",
    runtimeVersion: "1.0.0",
    platform: "windows" as const,
    architecture: "x64" as const,
    sourceClassification: "maintainer_referenced_conversion" as const,
    officialSourceReference: "pinned-maintainer-reference",
    licenseIdentifier: "Apache-2.0",
    commercialUseClassification: "restricted" as const,
    attributionRequirements: [],
    files: [
      {
        relativePath: "onnx/model_quantized.onnx",
        byteSize: 1,
        sha256: digest,
        mediaClassification: "onnx" as const,
        executable: false as const
      }
    ],
    totalExpandedByteSize: 1,
    requiredRuntimeDependencies: [],
    compatibilityConstraints: [],
    state: "active" as const,
    manifestFingerprint: otherDigest,
    provenance: provenance("real_local_provider")
  };
}

function runtimeHealth(providerId: string) {
  return {
    contractVersion: "1.0.0" as const,
    runtimeProfileId: "runtime-profile-1",
    runtimeProfileFingerprint: digest,
    runtimeInstanceId: "runtime-instance-1",
    providerId,
    status: "available" as const,
    reasonCode: "ready",
    checkedAt: now,
    expiresAt: "2026-07-31T12:01:00Z",
    modelPackageFingerprint: digest,
    protocolVersion: "1.0.0" as const
  };
}

function dictionary(): PronunciationDictionary {
  return {
    contractVersion: "1.0.0",
    dictionaryId: "dictionary-1",
    projectId: "project-1",
    revision: 1,
    entryCount: 1,
    currentEntryCount: 1,
    dictionaryFingerprint: digest,
    createdAt: now,
    updatedAt: now,
    provenance: provenance("human")
  };
}

function pronunciationEntry(): PronunciationEntry {
  return {
    contractVersion: "1.0.0",
    entryId: "pronunciation-1",
    projectId: "project-1",
    dictionaryId: "dictionary-1",
    dictionaryRevision: 1,
    revision: 1,
    writtenForm: "Harbor",
    normalizedLookupForm: "harbor",
    language: "en",
    locale: "en-US",
    scope: "project",
    scopeId: null,
    representation: "provider_neutral",
    pronunciation: "HAR-bor",
    ipa: null,
    providerId: null,
    providerCompiledValue: null,
    caseSensitive: false,
    matchRule: "whole_word",
    priority: 0,
    actor: { classification: "human", actorId: "desktop-user" },
    reason: "Reviewed locally.",
    verificationState: "approved",
    entryFingerprint: digest,
    supersedesEntryId: null,
    supersededByEntryId: null,
    immutable: true,
    provenance: provenance("human")
  };
}

function role(
  roleId: string,
  displayLabel: string,
  roleType: "narrator" | "character",
  sessionId: string
): AuditionRoleStatus {
  const request = previewRequest(roleId, sessionId);
  const binding = voiceRuntimeBinding(roleId);
  return {
    roleId,
    roleType,
    displayLabel,
    required: true,
    assignmentId: `assignment-${roleId}`,
    assignmentRevision: 1,
    voiceProfileId: `voice-${roleId}`,
    voiceDisplayLabel: `${displayLabel} voice`,
    voiceRuntimeBinding: binding,
    runtimeBindingStatus: "compatible",
    runtimeBindingReasonCode: null,
    rightsState: "verified",
    latestSessionId: sessionId,
    latestClipId: roleId === "role-narrator" ? "clip-1" : null,
    reviewState: "pending",
    sessionEvidence: request.evidence,
    generationRequest: request
  };
}

function voiceRuntimeBinding(
  roleId: string
): NonNullable<AuditionRoleStatus["voiceRuntimeBinding"]> {
  return {
    contractVersion: "1.0.0",
    bindingId: `voice-runtime-binding-${roleId}`,
    bindingKind: "declared_fixture_adapter",
    voiceProfileId: `voice-${roleId}`,
    voiceProfileVersion: "1.0.0",
    voiceProfileFingerprint: digest,
    sourceProviderId: "fixture-source-provider",
    sourceProviderVersion: "1.0.0",
    sourceProviderFingerprint: digest,
    sourceModelId: "fixture-source-model",
    sourceModelVersion: "1.0.0",
    sourceModelFingerprint: digest,
    providerId: "fixture-provider",
    providerVersion: "1.0.0",
    providerVoiceId: `fixture-voice-${roleId}`,
    modelId: "fixture-model",
    modelVersion: "1.0.0",
    modelPackageId: "fixture-model-package",
    modelPackageFingerprint: digest,
    runtimeProfileId: "runtime-profile-1",
    runtimeProfileFingerprint: digest,
    bindingFingerprint: digest,
    active: true,
    provenance: provenance("application"),
    createdAt: now
  };
}

function previewRequest(roleId: string, sessionId: string): SpeechPreviewRequest {
  return {
    contractVersion: "1.0.0",
    requestId: `preview-request-${roleId}`,
    auditionSessionId: sessionId,
    auditionSessionRevision: 1,
    auditionScriptId: `script-${roleId}`,
    auditionScriptFingerprint: digest,
    evidence: {
      projectId: "project-1",
      sourceDocumentId: "source-1",
      sourceRevision: 1,
      extractionId: "extraction-1",
      extractionRevision: 1,
      extractedTextSha256: digest,
      phase2RunId: "analysis-run-1",
      phase2SnapshotId: "analysis-snapshot-1",
      phase2SnapshotRevision: 1,
      phase2SnapshotFingerprint: digest,
      phase2CorrectionSetFingerprint: digest,
      castingRunId: "casting-run-1",
      approvedCastSnapshotId: "cast-snapshot-1",
      approvedCastSnapshotRevision: 1,
      approvedCastSnapshotFingerprint: digest,
      castAssignmentId: `assignment-${roleId}`,
      castAssignmentRevision: 1,
      voiceProfileId: `voice-${roleId}`,
      voiceProfileVersion: "1.0.0",
      voiceRuntimeBindingId: `voice-runtime-binding-${roleId}`,
      voiceRuntimeBindingFingerprint: digest,
      providerVoiceId: `fixture-voice-${roleId}`,
      providerId: "fixture-provider",
      providerVersion: "1.0.0",
      modelId: "fixture-model",
      modelVersion: "1.0.0",
      catalogRevisionId: "catalog-1",
      catalogFingerprint: digest,
      rightsRecordId: `rights-${roleId}`,
      rightsRecordRevision: 1,
      rightsRecordFingerprint: digest,
      pronunciationDictionaryId: "dictionary-1",
      pronunciationDictionaryRevision: 1,
      pronunciationDictionaryFingerprint: digest,
      runtimeProfileId: "runtime-profile-1",
      runtimeProfileFingerprint: digest,
      modelPackageId: "fixture-model-package",
      modelPackageFingerprint: digest,
      producerVersion: "1.0.0"
    },
    normalizedTextSha256: digest,
    normalizationPlanFingerprint: digest,
    pronunciationPlanFingerprint: digest,
    providerControls: {
      speakingRate: 1,
      pitch: null,
      style: null,
      energy: null,
      controlsFingerprint: digest
    },
    outputFormat: "pcm_s16le_wav",
    sampleRateHz: 24_000,
    channels: 1,
    idempotencyKey: `generate-${roleId}`,
    requestFingerprint: otherDigest
  };
}

function auditionSession(): AuditionSession {
  return {
    contractVersion: "1.0.0",
    auditionSessionId: "session-narrator",
    projectId: "project-1",
    roleId: "role-narrator",
    castAssignmentId: "assignment-role-narrator",
    castAssignmentRevision: 1,
    approvedCastSnapshotId: "cast-snapshot-1",
    approvedCastSnapshotRevision: 1,
    approvedCastSnapshotFingerprint: digest,
    voiceRuntimeBindingId: "voice-runtime-binding-role-narrator",
    voiceRuntimeBindingFingerprint: digest,
    providerVoiceId: "fixture-voice-role-narrator",
    voiceRuntimeBinding: voiceRuntimeBinding("role-narrator"),
    providerId: "fixture-provider",
    providerVersion: "1.0.0",
    modelPackageFingerprint: digest,
    runtimeProfileFingerprint: digest,
    pronunciationDictionaryRevision: 1,
    pronunciationDictionaryFingerprint: digest,
    state: "reviewable",
    revision: 1,
    scriptCount: 1,
    clipCount: 1,
    approvedClipId: null,
    jobId: null,
    sessionFingerprint: digest,
    createdAt: now,
    updatedAt: now,
    provenance: provenance("application")
  };
}

function auditionClip(): AuditionClip {
  return {
    contractVersion: "1.0.0",
    auditionClipId: "clip-1",
    projectId: "project-1",
    auditionSessionId: "session-narrator",
    auditionScriptId: "script-role-narrator",
    roleId: "role-narrator",
    castAssignmentId: "assignment-role-narrator",
    castAssignmentRevision: 1,
    providerRequestId: "provider-request-1",
    providerId: "fixture-provider",
    providerVersion: "1.0.0",
    voiceRuntimeBindingId: "voice-runtime-binding-role-narrator",
    voiceRuntimeBindingFingerprint: digest,
    providerVoiceId: "fixture-voice-role-narrator",
    providerClass: "deterministic_fixture",
    modelId: "fixture-model",
    modelVersion: "1.0.0",
    modelPackageFingerprint: digest,
    runtimeProfileFingerprint: digest,
    normalizedTextSha256: digest,
    pronunciationPlanFingerprint: digest,
    providerControlFingerprint: digest,
    cacheKey: digest,
    cacheStatus: "verified_hit",
    cacheProof: {
      cacheRecordId: "cache-record-1",
      cacheKey: digest,
      voiceRuntimeBindingId: "voice-runtime-binding-role-narrator",
      voiceRuntimeBindingFingerprint: digest,
      providerVoiceId: "fixture-voice-role-narrator",
      verificationFingerprint: digest
    },
    audioArtifact: {
      contractVersion: "1.0.0",
      audioArtifactId: "artifact-1",
      projectId: "project-1",
      storageKey: "artifact-storage-1",
      mediaType: "audio/wav",
      codec: "pcm_s16le",
      sampleRateHz: 24_000,
      channels: 1,
      sampleWidthBytes: 2,
      frameCount: 48,
      durationMilliseconds: 2,
      byteSize: 48,
      sha256: digest,
      availability: "present",
      playbackEligible: true,
      publishedAtomically: true,
      createdAt: now,
      immutable: true
    },
    audioQuality: {
      contractVersion: "1.0.0",
      qualityRecordId: "quality-1",
      projectId: "project-1",
      audioArtifactId: "artifact-1",
      profileId: "audition-qc-v1",
      profileVersion: "1.0.0",
      validWav: true,
      nonSilent: true,
      peakDbfs: -8,
      silenceRatio: 0.1,
      clippedSampleCount: 0,
      blockingFindingCodes: [],
      warningCodes: [],
      subjectiveQualityClaimed: false,
      qualityFingerprint: digest,
      measuredAt: now,
      provenance: provenance("application")
    },
    state: "reviewable",
    clipFingerprint: digest,
    revision: 1,
    createdAt: now,
    provenance: provenance("fixture_provider")
  };
}

function review(
  gateId: AuditionGateId,
  reviewId: string,
  roleId: string | null
): AuditionReview {
  return {
    contractVersion: "1.0.0",
    reviewId,
    projectId: "project-1",
    gateId,
    roleId,
    state: "pending",
    revision: 1,
    prerequisiteGateIds: [],
    evidence: {
      projectId: "project-1",
      gateId,
      roleId,
      auditionSessionId: roleId === null ? null : "session-narrator",
      auditionClipId: roleId === null ? null : "clip-1",
      auditionClipRevision: roleId === null ? null : 1,
      approvedCastSnapshotFingerprint: digest,
      castAssignmentFingerprint: roleId === null ? null : digest,
      rightsRecordFingerprint: digest,
      runtimeProfileFingerprint: digest,
      modelVerificationFingerprint: digest,
      pronunciationDictionaryFingerprint: digest,
      pronunciationDependencyFingerprint: digest,
      audioQualityFingerprint: roleId === null ? null : digest,
      evidenceFingerprint: digest
    },
    blockerCodes: [],
    warningCodes: [],
    latestDecision: null,
    updatedAt: now
  };
}

function reviewDecision(input: {
  readonly decisionId: string;
  readonly reviewId: string;
  readonly gateId: AuditionGateId;
  readonly roleId: string | null;
  readonly decision: AuditionReviewDecision["decision"];
  readonly rationale: string;
  readonly decidedAt: string;
  readonly supersedesDecisionId: string | null;
  readonly expectedReviewRevision: number;
}): AuditionReviewDecision {
  return {
    contractVersion: "1.0.0",
    decisionId: input.decisionId,
    reviewId: input.reviewId,
    projectId: "project-1",
    gateId: input.gateId,
    roleId: input.roleId,
    decision: input.decision,
    actor: { classification: "human", actorId: "desktop-user" },
    expectedReviewRevision: input.expectedReviewRevision,
    evidenceFingerprint: digest,
    rationale: input.rationale,
    decidedAt: input.decidedAt,
    immutable: true,
    supersedesDecisionId: input.supersedesDecisionId,
    provenance: provenance("human")
  };
}

function provenance(
  origin: SpeechAuditionProvenance["origin"]
): SpeechAuditionProvenance {
  return {
    origin,
    producerId: "phase3b-test",
    producerVersion: "1.0.0",
    recordedAt: now
  };
}

function projectDetail(): ProjectDetail {
  return {
    project: { projectId: "project-1", name: "Synthetic project", revision: 1 }
  } as unknown as ProjectDetail;
}

function auditionJob(
  state: Job["state"],
  progress: number,
  stage: string
): Job {
  return {
    jobId: "audition-job-1",
    projectId: "project-1",
    type: "generate_audition",
    state,
    target: { type: "audition_session", id: "session-narrator" },
    inputRevision: 1,
    inputFingerprint: digest,
    attempt: 1,
    stage,
    progress,
    checkpointAvailable: state === "interrupted" || state === "paused",
    cancellationRequested: state === "cancel_requested",
    warnings: [],
    ...(state === "failed"
      ? {
          error: {
            code: "SPEECH_RUNTIME_INTERRUPTED",
            message: "The local audition worker was interrupted.",
            retryable: true
          }
        }
      : {}),
    createdAt: now,
    updatedAt: now,
    ...(state === "cancelled" || state === "failed" || state === "succeeded"
      ? { terminalAt: now }
      : {})
  };
}

function humanizedJobState(state: Job["state"]): string {
  return state.replaceAll("_", " ").replace(/\b\w/gu, (letter) =>
    letter.toUpperCase()
  );
}

function ok<T>(value: T): DesktopResult<T> {
  return { ok: true, value };
}

function fail<T = never>(code: string): DesktopResult<T> {
  return {
    ok: false,
    error: { code, message: code, retryable: false }
  };
}
