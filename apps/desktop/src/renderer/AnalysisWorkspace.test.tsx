import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  PHASE_2_RUNTIME_AGENTS,
  WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
  WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
  type AnalysisEntity,
  type AnalysisEntityCollection,
  type AnalysisGateReview,
  type StoryAnalysisRun
} from "@cinematic-story-studio/contracts";
import type { ProjectDetail } from "@cinematic-story-studio/contracts/api";

import {
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
  type ListAnalysisEntitiesInput
} from "../shared/analysis-api";
import type {
  CinematicStoryDesktopApi,
  DesktopResult
} from "../shared/desktop-api";
import { AnalysisWorkspace } from "./AnalysisWorkspace";

const sha = (character: string) => character.repeat(64);

describe("Phase 2 Analysis workspace", () => {
  it("navigates bounded canonical structure, identity, dialogue, and narration pages", async () => {
    const fixture = createFixture();

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    expect(
      await screen.findByRole("progressbar", {
        name: "Whole-book analysis progress"
      })
    ).toHaveValue(1);
    expect(screen.getByText("Complete")).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", { name: "Structure" })
    );
    expect(
      await screen.findByRole("navigation", {
        name: "Analysis chapters"
      })
    ).toBeVisible();
    expect(
      await screen.findByRole("heading", { name: "Chapter One" })
    ).toBeVisible();
    expect(
      await screen.findByRole("heading", {
        name: "Kitchen confrontation"
      })
    ).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", { name: "Characters" })
    );
    const ambiguousCharacter = await screen.findByRole("heading", {
      name: "Mira Vale"
    });
    expect(ambiguousCharacter).toBeVisible();
    expect(screen.getByText(/M\. Vale/u)).toBeVisible();
    expect(ambiguousCharacter.closest("article")).toHaveAttribute(
      "data-identity-status",
      "ambiguous"
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Dialogue & narration" })
    );
    const unresolvedDialogue = await screen.findByRole("heading", {
      name: "Who told you?"
    });
    expect(unresolvedDialogue).toBeVisible();
    expect(unresolvedDialogue.closest("article")).toHaveAttribute(
      "data-speaker-state",
      "unknown"
    );
    expect(screen.getByText("I should never say it.")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "internal_thought" })
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: "Load next bounded page"
      })
    ).toBeVisible();

    expect(fixture.listEntities).toHaveBeenCalledWith(
      expect.objectContaining({
        collection: "narration-spans",
        limit: 50
      })
    );
  }, 10_000);

  it("uses exact confidence class ceilings and never filters agent executions", async () => {
    const fixture = createFixture();
    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );
    await screen.findByRole("progressbar", {
      name: "Whole-book analysis progress"
    });
    await waitFor(() => {
      expect(
        fixture.listEntities.mock.calls.some(
          ([input]) => input.collection === "agent-executions"
        )
      ).toBe(true);
    });

    fireEvent.change(screen.getByLabelText("Maximum confidence"), {
      target: { value: "0.749999" }
    });
    fireEvent.click(screen.getByLabelText("Human review only"));
    fireEvent.click(
      screen.getByRole("button", { name: "Dialogue & narration" })
    );
    await waitFor(() => {
      expect(
        fixture.listEntities.mock.calls.some(
          ([input]) =>
            input.collection === "dialogue-lines" &&
            input.confidenceMax === 0.749999 &&
            input.requiresReview === true
        )
      ).toBe(true);
    });

    fireEvent.change(screen.getByLabelText("Maximum confidence"), {
      target: { value: "0.849999" }
    });
    await waitFor(() => {
      expect(
        fixture.listEntities.mock.calls.some(
          ([input]) =>
            input.collection === "dialogue-lines" &&
            input.confidenceMax === 0.849999
        )
      ).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "Overview" }));
    await waitFor(() => {
      const agentCalls = fixture.listEntities.mock.calls
        .map(([input]) => input)
        .filter((input) => input.collection === "agent-executions");
      expect(agentCalls.length).toBeGreaterThan(1);
      expect(agentCalls.at(-1)).not.toHaveProperty("confidenceMax");
      expect(agentCalls.at(-1)).not.toHaveProperty("requiresReview");
    });
  });

  it("ignores deferred review and correction pages after switching runs", async () => {
    const fixture = createFixture();
    const runB = alternateRun(fixture.run, "b");
    const reviewsA = gateReviews(fixture.run, false);
    const reviewsB = gateReviews(runB, false).map((review, index) =>
      index === 0
        ? {
            ...review,
            state: "approved" as const,
            revision: 2,
            latestDecisionId: "decision-run-b",
            latestDecision: null
          }
        : review
    );
    let resolveReviewsB:
      | ((value: DesktopResult<unknown>) => void)
      | undefined;
    let resolveCorrectionsB:
      | ((value: DesktopResult<unknown>) => void)
      | undefined;
    const deferredReviewsB = new Promise<DesktopResult<unknown>>(
      (resolve) => {
        resolveReviewsB = resolve;
      }
    );
    const deferredCorrectionsB = new Promise<DesktopResult<unknown>>(
      (resolve) => {
        resolveCorrectionsB = resolve;
      }
    );
    vi.mocked(fixture.api.analysis.listRuns).mockResolvedValue(
      ok({
        correlationId: "correlation-two-runs",
        pageSize: 2,
        total: 2,
        runs: [runB, fixture.run]
      })
    );
    vi.mocked(fixture.api.analysis.listReviews).mockImplementation(
      async (input) =>
        input.runId === runB.runId
          ? (deferredReviewsB as ReturnType<
              CinematicStoryDesktopApi["analysis"]["listReviews"]
            >)
          : ok({
              correlationId: "correlation-reviews-a",
              runId: fixture.run.runId,
              items: reviewsA
            })
    );
    vi.mocked(fixture.api.analysis.listCorrections).mockImplementation(
      async (input) =>
        input.runId === runB.runId
          ? (deferredCorrectionsB as ReturnType<
              CinematicStoryDesktopApi["analysis"]["listCorrections"]
            >)
          : ok({
              correlationId: "correlation-corrections-a",
              runId: fixture.run.runId,
              pageSize: 0,
              total: 0,
              items: []
            })
    );

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );
    const selector = await screen.findByLabelText("Analysis run");
    await waitFor(() => {
      expect(within(selector).getAllByRole("option")).toHaveLength(2);
    });
    fireEvent.change(selector, { target: { value: runB.runId } });
    fireEvent.change(selector, { target: { value: fixture.run.runId } });

    await act(async () => {
      resolveReviewsB?.(
        ok({
          correlationId: "correlation-reviews-b",
          runId: runB.runId,
          items: reviewsB
        })
      );
      resolveCorrectionsB?.(
        ok({
          correlationId: "correlation-corrections-b",
          runId: runB.runId,
          pageSize: 1,
          total: 1,
          items: [
            correctionHistoryItem(
              "correction-run-b",
              "entity-character-mira",
              "2026-07-30T14:00:00Z",
              "Run B correction must stay hidden."
            )
          ]
        })
      );
      await Promise.resolve();
    });

    expect(selector).toHaveValue(fixture.run.runId);
    expect(
      screen.getByRole("button", {
        name: "Approve Story structure review"
      })
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Corrections" })
    );
    expect(
      screen.queryByText("Run B correction must stay hidden.")
    ).not.toBeInTheDocument();
  });

  it("never overlaps a slow run poll and schedules the next poll after it settles", async () => {
    vi.useFakeTimers();
    try {
      const activeRun: StoryAnalysisRun = {
        ...validRun(),
        status: "running",
        currentStage: "analyze_structure",
        progress: 0.4,
        reviewEligibility: "not_ready",
        completedAt: undefined
      };
      const fixture = createFixture({ run: activeRun });
      type GetRunResult = Awaited<
        ReturnType<CinematicStoryDesktopApi["analysis"]["getRun"]>
      >;
      let resolveFirstPoll:
        | ((value: GetRunResult) => void)
        | undefined;
      const firstPoll = new Promise<GetRunResult>((resolve) => {
        resolveFirstPoll = resolve;
      });
      const secondPoll = new Promise<GetRunResult>(() => undefined);
      vi.mocked(fixture.api.analysis.getRun)
        .mockReturnValueOnce(firstPoll)
        .mockReturnValueOnce(secondPoll);

      render(
        <AnalysisWorkspace
          project={fixture.project}
          api={fixture.api}
          connected
          onNotice={vi.fn()}
          onError={vi.fn()}
        />
      );
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      await act(async () => {
        vi.advanceTimersByTime(1_250);
        await Promise.resolve();
      });
      expect(fixture.api.analysis.getRun).toHaveBeenCalledTimes(1);

      await act(async () => {
        vi.advanceTimersByTime(5_000);
        await Promise.resolve();
      });
      expect(fixture.api.analysis.getRun).toHaveBeenCalledTimes(1);

      resolveFirstPoll?.(
        ok({
          correlationId: "correlation-slow-poll",
          run: {
            ...activeRun,
            progress: 0.6,
            updatedAt: "2026-07-30T12:00:30Z"
          }
        })
      );
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      await act(async () => {
        vi.advanceTimersByTime(1_250);
        await Promise.resolve();
      });
      expect(fixture.api.analysis.getRun).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("submits the exact effective fingerprint for a human identity correction", async () => {
    const fixture = createFixture();

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Characters" })
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Correct Mira Vale"
      })
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.change(
      within(dialog).getByLabelText("Correction type"),
      { target: { value: "character_identity" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Canonical name"),
      { target: { value: "Mira Valen" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Identity status"),
      { target: { value: "resolved" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Correction reason"),
      {
        target: {
          value: "The full-name mention resolves the ambiguous alias."
        }
      }
    );
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Save human correction"
      })
    );

    await waitFor(() => {
      expect(fixture.appendCorrection).toHaveBeenCalledTimes(1);
    });
    expect(fixture.appendCorrection).toHaveBeenCalledWith(
      expect.objectContaining({
        category: "character_identity",
        targetCollection: "characters",
        targetEntityId: "entity-character-mira",
        expectedTargetRevision: 1,
        expectedRunFingerprint: fixture.run.runFingerprint,
        previousValueFingerprint: sha("b"),
        patch: {
          canonicalName: "Mira Valen",
          normalizedCanonicalName: "mira valen",
          identityStatus: "resolved"
        }
      })
    );
  });

  it("requires at least one mention before enabling a character split", async () => {
    const fixture = createFixture();

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Characters" }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Correct Mira Vale"
      })
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.change(
      within(dialog).getByLabelText("Correction type"),
      { target: { value: "character_split" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("New canonical name"),
      { target: { value: "Mira Reviewed" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Correction reason"),
      {
        target: {
          value: "This mention belongs to a separate identity."
        }
      }
    );
    const save = within(dialog).getByRole("button", {
      name: "Save human correction"
    });
    expect(save).toBeDisabled();

    fireEvent.change(
      within(dialog).getByLabelText("Mention IDs (comma separated)"),
      { target: { value: "entity-mention-1" } }
    );
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => {
      expect(fixture.appendCorrection).toHaveBeenCalledTimes(1);
    });
    expect(
      fixture.appendCorrection.mock.calls[0]?.[0]
    ).toMatchObject({
      category: "character_split",
      targetCollection: "characters",
      targetEntityId: "entity-character-mira",
      patch: {
          canonicalName: "Mira Reviewed",
          normalizedCanonicalName: "mira reviewed",
          mentionIds: ["entity-mention-1"]
      }
    });
  });

  it("loads every bounded correction page before choosing the superseded correction", async () => {
    const fixture = createFixture();
    const firstPage = Array.from({ length: 50 }, (_, index) =>
      correctionHistoryItem(
        `correction-unrelated-${index}`,
        `entity-unrelated-${index}`,
        `2026-07-30T11:${String(index).padStart(2, "0")}:00Z`,
        `Unrelated correction ${index}.`
      )
    );
    const latest = correctionHistoryItem(
      "correction-mira-latest",
      "entity-character-mira",
      "2026-07-30T13:00:00Z",
      "Latest reviewed identity."
    );
    vi.mocked(
      fixture.api.analysis.listCorrections
    ).mockImplementation(async (input) =>
      input.cursor === undefined
        ? ok({
            correlationId: "correlation-corrections-page-1",
            runId: fixture.run.runId,
            pageSize: firstPage.length,
            total: firstPage.length + 1,
            nextCursor: "corrections-page-2",
            items: firstPage
          })
        : ok({
            correlationId: "correlation-corrections-page-2",
            runId: fixture.run.runId,
            pageSize: 1,
            total: firstPage.length + 1,
            items: [latest]
          })
    );

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );
    await waitFor(() => {
      expect(
        fixture.api.analysis.listCorrections
      ).toHaveBeenCalledWith(
        expect.objectContaining({ cursor: "corrections-page-2" })
      );
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Corrections" })
    );
    expect(await screen.findByText("Latest reviewed identity.")).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", { name: "Characters" })
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Correct Mira Vale"
      })
    );
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(/will supersede the latest immutable correction/u)
    ).toHaveTextContent("correction-mira-latest");
    fireEvent.change(
      within(dialog).getByLabelText("Correction reason"),
      { target: { value: "Supersede the reviewed identity." } }
    );
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Save human correction"
      })
    );
    await waitFor(() => {
      expect(fixture.appendCorrection).toHaveBeenCalledWith(
        expect.objectContaining({
          supersedesCorrectionId: "correction-mira-latest"
        })
      );
    });
  });

  it("submits explicit add, move, and remove structure operations", async () => {
    for (const operation of ["add", "move", "remove"] as const) {
      const fixture = createFixture();
      const rendered = render(
        <AnalysisWorkspace
          project={fixture.project}
          api={fixture.api}
          connected
          onNotice={vi.fn()}
          onError={vi.fn()}
        />
      );

      fireEvent.click(
        screen.getByRole("button", { name: "Structure" })
      );
      fireEvent.click(
        await screen.findByRole("button", {
          name: "Correct Chapter One"
        })
      );
      const dialog = screen.getByRole("dialog");
      fireEvent.change(
        within(dialog).getByLabelText("Structure operation"),
        { target: { value: operation } }
      );
      const startOffset = within(dialog).getByLabelText(
        "Selected start offset"
      );
      const endOffset = within(dialog).getByLabelText(
        "Selected end offset"
      );
      expect(
        within(dialog).getByText(
          /local service derives the selected span fingerprint/u
        )
      ).toBeVisible();
      if (operation === "remove") {
        expect(startOffset).toHaveAttribute("readonly");
        expect(endOffset).toHaveAttribute("readonly");
      } else {
        fireEvent.change(startOffset, { target: { value: "12" } });
        fireEvent.change(endOffset, { target: { value: "148" } });
      }
      fireEvent.change(
        within(dialog).getByLabelText("Correction reason"),
        { target: { value: `${operation} the reviewed boundary.` } }
      );
      fireEvent.click(
        within(dialog).getByRole("button", {
          name: "Save human correction"
        })
      );

      await waitFor(() => {
        expect(fixture.appendCorrection).toHaveBeenCalledOnce();
      });
      const request = fixture.appendCorrection.mock.calls[0]?.[0];
      expect(request?.category).toBe("structure_boundary");
      expect(request?.targetCollection).toBe("chapters");
      expect(request?.patch).toMatchObject({
        operation,
        parentEntityId: "story-1",
        ordinal: 0,
        sourceSpan:
          operation === "remove"
            ? {
                startOffset: 0,
                endOffset: 160
              }
            : {
                startOffset: 12,
                endOffset: 148
              }
      });
      expect(request?.patch).not.toHaveProperty(
        "sourceSpan.textSha256"
      );
      rendered.unmount();
    }
  }, 10_000);

  it("corrects relationship direction, endpoints, and scope explicitly", async () => {
    const fixture = createFixture();
    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Whole book" })
    );
    fireEvent.change(
      await screen.findByLabelText("Analysis layer"),
      { target: { value: "relationships" } }
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Correct unknown" })
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Source character"), {
      target: { value: "entity-character-rowan" }
    });
    fireEvent.change(within(dialog).getByLabelText("Target character"), {
      target: { value: "entity-character-mira" }
    });
    fireEvent.change(
      within(dialog).getByLabelText("Relationship kind"),
      { target: { value: "professional" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Relationship state"),
      { target: { value: "Rowan reports to Mira." } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Relationship change"),
      { target: { value: "strengthened" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Relationship scope"),
      { target: { value: "scene_range" } }
    );
    fireEvent.change(
      within(dialog).getByLabelText("Correction reason"),
      { target: { value: "The reviewed scenes establish direction." } }
    );
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Save human correction"
      })
    );

    await waitFor(() => {
      expect(fixture.appendCorrection).toHaveBeenCalledOnce();
    });
    const request = fixture.appendCorrection.mock.calls[0]?.[0];
    expect(request?.category).toBe("relationship");
    expect(request?.targetCollection).toBe("relationships");
    expect(request?.patch).toMatchObject({
      sourceCharacterId: "entity-character-rowan",
      targetCharacterId: "entity-character-mira",
      kind: "professional",
      state: "Rowan reports to Mira.",
      change: "strengthened",
      scope: {
        kind: "scene_range",
        firstSceneId: "entity-scene-1",
        lastSceneId: "entity-scene-1",
        sourceRange: {
          sourceDocumentId: "document-1",
          extractionId: "extraction-1",
          extractionRevision: 2,
          offsetUnit: "unicode-code-point",
          startOffset: 24,
          endOffset: 80,
          textSha256: sha("9")
        }
      }
    });
  });

  it("requires warning acknowledgement while source gates remain independent", async () => {
    const fixture = createFixture({ gateWarning: true });

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    const structureGate = await screen.findByRole("button", {
      name: "Approve Story structure review"
    });
    const characterGate = screen.getByRole("button", {
      name: "Approve Character registry review"
    });
    const dialogueGate = screen.getByRole("button", {
      name: "Approve Dialogue attribution review"
    });
    const wholeBookGate = screen.getByRole("button", {
      name: "Approve Whole-book analysis review"
    });
    expect(structureGate).toBeDisabled();
    expect(characterGate).toBeDisabled();

    fireEvent.change(
      screen.getByLabelText("Dialogue attribution review rationale"),
      { target: { value: "Dialogue evidence was reviewed independently." } }
    );
    fireEvent.change(
      screen.getByLabelText("Whole-book analysis review rationale"),
      { target: { value: "Whole-book evidence was reviewed." } }
    );
    expect(dialogueGate).toBeEnabled();
    expect(wholeBookGate).toBeDisabled();

    fireEvent.change(
      screen.getByLabelText("Story structure review rationale"),
      { target: { value: "The reviewed evidence supports approval." } }
    );
    fireEvent.click(
      screen.getByLabelText(
        "I reviewed and acknowledge 1 open warning."
      )
    );
    expect(structureGate).toBeEnabled();
    fireEvent.click(structureGate);

    await waitFor(() => {
      expect(fixture.decideReview).toHaveBeenCalledTimes(1);
    });
    expect(fixture.decideReview).toHaveBeenCalledWith(
      expect.objectContaining({
        gateId: "story_structure_review",
        decision: "approve",
        expectedRevision: 1,
        expectedArtifactFingerprint: sha("d"),
        expectedEvidenceFingerprint: sha("e"),
        acknowledgedWarningIds: ["warning-structure-1"],
        rationale: "The reviewed evidence supports approval."
      })
    );
  });

  it("keeps gate actions available to append a superseding decision", async () => {
    const fixture = createFixture({ approvedStructure: true });

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    const reapprove = await screen.findByRole("button", {
      name: "Reapprove Story structure review"
    });
    const gate = reapprove.closest("article");
    if (gate === null) {
      throw new Error("The structure gate is missing.");
    }
    fireEvent.change(
      within(gate).getByLabelText("Story structure review rationale"),
      { target: { value: "New evidence requires another pass." } }
    );
    fireEvent.click(
      within(gate).getByRole("button", { name: "Request changes" })
    );

    await waitFor(() => {
      expect(fixture.decideReview).toHaveBeenCalledWith(
        expect.objectContaining({
          gateId: "story_structure_review",
          decision: "request_changes",
          expectedRevision: 2,
          rationale: "New evidence requires another pass."
        })
      );
    });
  });

  it("allows an invalidated gate to recover while the whole-book gate stays blocked", async () => {
    const fixture = createFixture({
      run: {
        ...validRun(),
        reviewEligibility: "invalidated"
      },
      invalidatedStructure: true
    });

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    const structureGate = await screen.findByRole("button", {
      name: "Approve Story structure review"
    });
    const wholeBookGate = screen.getByRole("button", {
      name: "Approve Whole-book analysis review"
    });
    fireEvent.change(
      screen.getByLabelText("Story structure review rationale"),
      { target: { value: "The corrected structure evidence was reviewed." } }
    );
    fireEvent.change(
      screen.getByLabelText("Whole-book analysis review rationale"),
      { target: { value: "Whole-book evidence was reviewed." } }
    );

    expect(structureGate).toBeEnabled();
    expect(wholeBookGate).toBeDisabled();
    fireEvent.click(structureGate);

    await waitFor(() => {
      expect(fixture.decideReview).toHaveBeenCalledWith(
        expect.objectContaining({
          gateId: "story_structure_review",
          decision: "approve",
          rationale: "The corrected structure evidence was reviewed."
        })
      );
    });
  });

  it("never enables governance decisions for a partial run", async () => {
    const partialRun: StoryAnalysisRun = {
      ...validRun(),
      status: "partial",
      currentStage: "publish_analysis",
      progress: 0.96,
      reviewEligibility: "ready",
      completedAt: undefined
    };
    const fixture = createFixture({ run: partialRun });
    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    fireEvent.change(
      await screen.findByLabelText("Story structure review rationale"),
      { target: { value: "This partial output must not be approvable." } }
    );
    expect(
      screen.getByRole("button", {
        name: "Approve Story structure review"
      })
    ).toBeDisabled();
  });

  it.each(["pending", "rejected"] as const)(
    "does not reuse an older approval when the current import is %s",
    async (state) => {
      const fixture = createFixture();
      const oldReview = fixture.project.importReviews[0];
      if (oldReview === undefined || fixture.project.story === null) {
        throw new Error("The import fixture is incomplete.");
      }
      const project = {
        ...fixture.project,
        // Deliberately true to prove review/source binding is also enforced.
        analysisAllowed: true,
        story: {
          ...fixture.project.story,
          revision: fixture.project.story.revision + 1,
          sourceDocumentIds: ["document-2"]
        },
        extractions: [
          ...fixture.project.extractions,
          {
            ...fixture.project.extractions[0],
            extractionId: "extraction-2",
            sourceDocumentId: "document-2",
            revision: 1
          }
        ],
        importReviews: [
          {
            ...oldReview,
            updatedAt: "2026-07-30T11:00:00Z"
          },
          {
            ...oldReview,
            reviewId: "review-2",
            sourceDocumentId: "document-2",
            extractionId: "extraction-2",
            candidateStoryRevision:
              fixture.project.story.revision + 1,
            revision: 1,
            state,
            latestDecision:
              state === "pending"
                ? undefined
                : { decision: "rejected" },
            updatedAt: "2026-07-30T13:00:00Z"
          }
        ]
      } as unknown as ProjectDetail;

      render(
        <AnalysisWorkspace
          project={project}
          api={fixture.api}
          connected
          onNotice={vi.fn()}
          onError={vi.fn()}
        />
      );

      expect(
        await screen.findByRole("heading", {
          name: "Approve the current extraction before analysis"
        })
      ).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Rerun analysis" })
      ).toBeDisabled();
      expect(fixture.api.analysis.createRun).not.toHaveBeenCalled();
    }
  );

  it("shows human-controlled effective boundaries and registry splits beside machine proof", async () => {
    const fixture = createFixture({ effectiveViews: true });

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Structure" })
    );
    const boundary = await screen.findByRole("region", {
      name: "Human-controlled effective boundary"
    });
    expect(within(boundary).getByText("Human authority")).toBeVisible();
    expect(within(boundary).getByText("Move")).toBeVisible();
    expect(within(boundary).getByText("Included")).toBeVisible();
    expect(within(boundary).getByText("story-1")).toBeVisible();
    expect(
      within(boundary).getByText(
        /Unicode code points 12\u2013160/u
      )
    ).toBeVisible();
    expect(
      within(boundary).getByText("correction-boundary-1")
    ).toBeVisible();

    const boundaryArticle = boundary.closest("article");
    expect(boundaryArticle).toHaveAttribute(
      "data-effective-authority",
      "human"
    );
    if (boundaryArticle === null) {
      throw new Error("The effective boundary card is missing.");
    }
    fireEvent.click(
      within(boundaryArticle).getByText("Identity and fingerprints")
    );
    expect(within(boundaryArticle).getByText("Machine fingerprint")).toBeVisible();
    expect(within(boundaryArticle).getByText(sha("8"))).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", { name: "Characters" })
    );
    const registry = await screen.findByRole("region", {
      name: "Human-controlled effective registry"
    });
    expect(within(registry).getByText("Human authority")).toBeVisible();
    expect(within(registry).getByText("Split")).toBeVisible();
    expect(within(registry).getByText("Mira Reviewed")).toBeVisible();
    expect(
      within(registry).getByText("registry-mira-reviewed")
    ).toBeVisible();
    expect(within(registry).getByText("entity-mention-1")).toBeVisible();
    expect(
      within(registry).getByText("correction-split-1")
    ).toBeVisible();

    const registryArticle = registry.closest("article");
    expect(registryArticle).toHaveAttribute(
      "data-effective-authority",
      "human"
    );
    if (registryArticle === null) {
      throw new Error("The effective registry card is missing.");
    }
    fireEvent.click(
      within(registryArticle).getByText("Identity and fingerprints")
    );
    expect(within(registryArticle).getByText("Machine fingerprint")).toBeVisible();
    expect(within(registryArticle).getByText(sha("b"))).toBeVisible();
  }, 10_000);

  it("seeds a superseding boundary correction from the complete human-effective span", async () => {
    const fixture = createFixture({ effectiveViews: true });

    render(
      <AnalysisWorkspace
        project={fixture.project}
        api={fixture.api}
        connected
        onNotice={vi.fn()}
        onError={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Structure" }));
    const boundary = await screen.findByRole("region", {
      name: "Human-controlled effective boundary"
    });
    const boundaryArticle = boundary.closest("article");
    if (boundaryArticle === null) {
      throw new Error("The effective boundary card is missing.");
    }
    fireEvent.click(
      within(boundaryArticle).getByRole("button", {
        name: /^Correct /u
      })
    );

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Selected start offset")).toHaveValue(
      12
    );
    expect(within(dialog).getByLabelText("Selected end offset")).toHaveValue(
      160
    );
    expect(
      within(dialog).getByText(
        /local service derives the selected span fingerprint/u
      )
    ).toBeVisible();
  });
});

function createFixture(options?: {
  readonly gateWarning?: boolean;
  readonly effectiveViews?: boolean;
  readonly approvedStructure?: boolean;
  readonly invalidatedStructure?: boolean;
  readonly run?: StoryAnalysisRun;
}) {
  const run = options?.run ?? validRun();
  const entities = entityFixture(options?.effectiveViews === true);
  const reviews = gateReviews(run, options?.gateWarning === true).map(
    (review) =>
      options?.approvedStructure === true &&
      review.gateId === "story_structure_review"
        ? {
            ...review,
            state: "approved" as const,
            revision: 2,
            latestDecisionId: "decision-structure-prior"
          }
        : options?.invalidatedStructure === true &&
            review.gateId === "story_structure_review"
          ? {
              ...review,
              state: "invalidated" as const,
              revision: 2
            }
        : review
  );
  const listEntities = vi.fn(
    async (input: ListAnalysisEntitiesInput) =>
      ok({
        correlationId: "correlation-entities",
        runId: run.runId,
        snapshotId: run.currentSnapshot?.snapshotId ?? "snapshot-1",
        collection: input.collection,
        pageSize: entities[input.collection].length,
        total: entities[input.collection].length + (
          input.collection === "narration-spans" ? 1 : 0
        ),
        ...(input.collection === "narration-spans"
          ? { nextCursor: "next-narration-page" }
          : {}),
        items: entities[input.collection]
      })
  );
  const appendCorrection = vi.fn(async (input: {
    readonly targetEntityId: string;
    readonly category: string;
    readonly targetCollection: AnalysisEntityCollection;
    readonly expectedTargetRevision: number;
    readonly expectedRunFingerprint: string;
    readonly previousValueFingerprint: string;
    readonly patch: Readonly<Record<string, unknown>>;
    readonly reason: string;
  }) =>
    ok({
      correlationId: "correlation-correction",
      correction: {
        contractVersion: "2.0.0" as const,
        correctionId: "correction-character-1",
        projectId: run.projectId,
        runId: run.runId,
        snapshotId: run.currentSnapshot?.snapshotId ?? "snapshot-1",
        category: "character_identity" as const,
        targetCollection: "characters" as const,
        targetEntityId: input.targetEntityId,
        expectedTargetRevision: input.expectedTargetRevision,
        expectedRunFingerprint: input.expectedRunFingerprint,
        previousValueFingerprint: input.previousValueFingerprint,
        correctedValueFingerprint: sha("c"),
        patch: input.patch,
        actor: {
          classification: "human" as const,
          actorId: "local-user"
        },
        reason: input.reason,
        recordedAt: "2026-07-30T12:10:00Z",
        immutable: true as const,
        lockedAgainstAutomation: true as const,
        idempotencyFingerprint: sha("f")
      },
      invalidatedGateIds: [
        "character_registry_review" as const,
        "whole_book_analysis_review" as const
      ],
      run,
      reviews
    })
  );
  const decideReview = vi.fn(async (input: {
    readonly gateId: AnalysisGateReview["gateId"];
    readonly acknowledgedWarningIds: readonly string[];
  }) => {
    const prior = reviews.find((review) => review.gateId === input.gateId);
    if (prior === undefined) {
      throw new Error("Missing review fixture.");
    }
    const review: AnalysisGateReview = {
      ...prior,
      state: "approved",
      revision: prior.revision + 1,
      acknowledgedWarningIds: input.acknowledgedWarningIds,
      latestDecisionId: "decision-structure-1"
    };
    return ok({
      correlationId: "correlation-decision",
      review,
      decision: {
        contractVersion: "2.0.0" as const,
        decisionId: "decision-structure-1",
        reviewId: review.reviewId,
        projectId: run.projectId,
        gateId: review.gateId,
        runId: run.runId,
        snapshotId: review.snapshotId,
        decision: "approved" as const,
        artifactFingerprint: review.artifactFingerprint,
        evidenceFingerprint: review.evidenceFingerprint,
        evidence: review.evidence,
        actor: {
          classification: "human" as const,
          actorId: "local-user"
        },
        acknowledgedWarningIds: input.acknowledgedWarningIds,
        provenance: humanReviewProvenance(),
        decidedAt: "2026-07-30T12:10:00Z",
        immutable: true as const
      },
      run
    });
  });
  const api = {
    version: "1.0.0",
    backend: {
      getStatus: vi.fn(async () => fail("NOT_CONFIGURED")),
      reconnect: vi.fn(async () => fail("NOT_CONFIGURED")),
      onStatus: vi.fn(() => () => undefined)
    },
    projects: {
      list: vi.fn(async () => fail("NOT_CONFIGURED")),
      create: vi.fn(async () => fail("NOT_CONFIGURED")),
      open: vi.fn(async () => fail("NOT_CONFIGURED")),
      restoreRecent: vi.fn(async () => fail("NOT_CONFIGURED")),
      importSelectedFile: vi.fn(async () => fail("NOT_CONFIGURED")),
      getImportReview: vi.fn(async () => fail("NOT_CONFIGURED")),
      decideImportReview: vi.fn(async () => fail("NOT_CONFIGURED"))
    },
    dialogue: {
      correctSpeaker: vi.fn(async () => fail("NOT_CONFIGURED"))
    },
    analysis: {
      createRun: vi.fn(async () => fail("NOT_CONFIGURED")),
      listRuns: vi.fn(async () =>
        ok({
          correlationId: "correlation-runs",
          pageSize: 1,
          total: 1,
          runs: [run]
        })
      ),
      getRun: vi.fn(async () =>
        ok({ correlationId: "correlation-run", run })
      ),
      listEntities,
      listCorrections: vi.fn(async () =>
        ok({
          correlationId: "correlation-corrections",
          runId: run.runId,
          pageSize: 0,
          total: 0,
          items: []
        })
      ),
      appendCorrection,
      listReviews: vi.fn(async () =>
        ok({
          correlationId: "correlation-reviews",
          runId: run.runId,
          items: reviews
        })
      ),
      decideReview
    },
    jobs: {
      create: vi.fn(async () => fail("NOT_CONFIGURED")),
      get: vi.fn(async () => fail("NOT_CONFIGURED")),
      events: vi.fn(async () => fail("NOT_CONFIGURED")),
      cancel: vi.fn(async () => fail("NOT_CONFIGURED")),
      retry: vi.fn(async () => fail("NOT_CONFIGURED")),
      resume: vi.fn(async () => fail("NOT_CONFIGURED"))
    },
    providers: {
      health: vi.fn(async () => fail("NOT_CONFIGURED"))
    },
    capabilities: {
      ffmpeg: vi.fn(async () => fail("NOT_CONFIGURED"))
    }
  } as unknown as CinematicStoryDesktopApi;
  const project = {
    correlationId: "correlation-project",
    project: {
      projectId: run.projectId,
      name: "Phase 2 Fixture",
      revision: 5
    },
    analysisAllowed: true,
    importReviews: [
      {
        reviewId: run.importReviewId,
        sourceDocumentId: run.sourceDocumentId,
        extractionId: run.extractionId,
        candidateStoryId: run.storyId,
        candidateStoryRevision: run.storyRevision,
        revision: run.importReviewRevision,
        state: "approved",
        evidenceFingerprint: run.approvedEvidenceFingerprint,
        latestDecision: { decision: "approved" },
        updatedAt: "2026-07-30T11:58:00Z"
      }
    ],
    extractions: [
      {
        extractionId: run.extractionId,
        sourceDocumentId: run.sourceDocumentId,
        revision: run.extractionRevision
      }
    ],
    story: {
      storyId: run.storyId,
      revision: run.storyRevision,
      sourceDocumentIds: [run.sourceDocumentId]
    },
    currentAnalysisRun: run,
    analysisGateReviews: reviews
  } as unknown as ProjectDetail;
  return {
    api,
    project,
    run,
    listEntities,
    appendCorrection,
    decideReview
  };
}

function validRun(): StoryAnalysisRun {
  return {
    contractVersion: "2.0.0",
    runId: "run-1",
    projectId: "project-1",
    storyId: "story-1",
    storyRevision: 4,
    storyFingerprint: sha("1"),
    sourceDocumentId: "document-1",
    sourceRevision: 2,
    sourceSha256: sha("0"),
    extractionId: "extraction-1",
    extractionRevision: 2,
    extractedTextSha256: sha("2"),
    importReviewId: "review-1",
    importReviewRevision: 3,
    importReviewDecisionId: "import-decision-1",
    approvedEvidenceFingerprint: sha("3"),
    inputFingerprint: sha("4"),
    runFingerprint: sha("5"),
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
    jobId: "job-1",
    status: "succeeded",
    currentStage: "complete",
    progress: 1,
    warnings: [],
    snapshotCount: 1,
    currentSnapshot: {
      contractVersion: "2.0.0",
      snapshotId: "snapshot-1",
      runId: "run-1",
      revision: 1,
      inputFingerprint: sha("4"),
      snapshotFingerprint: sha("6"),
      correctionSetFingerprint: sha("7"),
      counts: counts(),
      collections: [],
      createdAt: "2026-07-30T12:00:00Z",
      immutable: true
    },
    summary: counts(),
    reviewEligibility: "ready",
    createdAt: "2026-07-30T11:59:00Z",
    updatedAt: "2026-07-30T12:00:00Z",
    completedAt: "2026-07-30T12:00:00Z"
  };
}

function alternateRun(
  source: StoryAnalysisRun,
  suffix: string
): StoryAnalysisRun {
  const snapshot = source.currentSnapshot;
  if (snapshot === null) {
    throw new Error("Alternate run requires a snapshot.");
  }
  return {
    ...source,
    runId: `run-${suffix}`,
    jobId: `job-${suffix}`,
    runFingerprint: sha("a"),
    currentSnapshot: {
      ...snapshot,
      snapshotId: `snapshot-${suffix}`,
      runId: `run-${suffix}`,
      snapshotFingerprint: sha("b"),
      correctionSetFingerprint: sha("c")
    },
    createdAt: "2026-07-30T13:00:00Z",
    updatedAt: "2026-07-30T13:01:00Z",
    completedAt: "2026-07-30T13:01:00Z"
  };
}

function entityFixture(withEffectiveViews = false): Readonly<
  Record<AnalysisEntityCollection, readonly AnalysisEntity[]>
> {
  const header = (
    entityId: string,
    fingerprint: string,
    ordinal: number
  ) => ({
    contractVersion: "2.0.0" as const,
    entityId,
    stableSemanticId: `semantic-${entityId}`,
    runId: "run-1",
    snapshotId: "snapshot-1",
    revision: 1,
    effectiveRevision: 1,
    machineEntityFingerprint: fingerprint,
    effectiveValueFingerprint: fingerprint,
    effectiveAuthority: "runtime_agent" as const,
    ordinal,
    confidence: {
      score: 0.9,
      classification: "high" as const,
      basis: "Synthetic deterministic fixture."
    },
    warnings: [],
    provenance: runtimeProvenance(),
    evidence: []
  });
  const chapter = {
    ...header("entity-chapter-1", sha("8"), 0),
    ...(withEffectiveViews
      ? {
          effectiveRevision: 2,
          effectiveValueFingerprint: sha("1"),
          effectiveAuthority: "human" as const,
          effectiveBoundary: {
            parentEntityId: "story-1",
            ordinal: 1,
            sourceSpan: {
              ...sourceSpan(12, 160),
              textSha256: sha("2")
            },
            authority: "human" as const,
            correctionId: "correction-boundary-1",
            operation: "move" as const,
            included: true as const
          }
        }
      : {}),
    chapterId: "entity-chapter-1",
    title: "Chapter One",
    sourceSpan: sourceSpan(0, 160),
    firstSceneId: "entity-scene-1",
    lastSceneId: "entity-scene-1",
    sceneCount: 1
  };
  const scene = {
    ...header("entity-scene-1", sha("9"), 0),
    sceneId: "entity-scene-1",
    chapterId: "entity-chapter-1",
    heading: "Kitchen confrontation",
    sourceSpan: sourceSpan(0, 160),
    boundaryKind: "heading",
    firstBeatId: "entity-beat-1",
    lastBeatId: "entity-beat-1",
    beatCount: 1
  };
  const beat = {
    ...header("entity-beat-1", sha("a"), 0),
    beatId: "entity-beat-1",
    chapterId: "entity-chapter-1",
    sceneId: "entity-scene-1",
    kind: "dialogue",
    sourceSpan: sourceSpan(24, 80),
    summary: "Mira challenges Rowan.",
    dialogueLineId: "entity-dialogue-1"
  };
  const character = {
    ...header("entity-character-mira", sha("b"), 0),
    ...(withEffectiveViews
      ? {
          effectiveRevision: 2,
          effectiveValueFingerprint: sha("2"),
          effectiveAuthority: "human" as const,
          effectiveRegistry: {
            authority: "human" as const,
            correctionId: "correction-split-1",
            operation: "split" as const,
            splitIdentity: {
              registryCharacterId: "registry-mira-reviewed",
              canonicalName: "Mira Reviewed",
              normalizedCanonicalName: "mira reviewed",
              mentionIds: ["entity-mention-1"]
            }
          }
        }
      : {}),
    characterId: "entity-character-mira",
    canonicalName: "Mira Vale",
    normalizedCanonicalName: "mira vale",
    kind: "person",
    identityStatus: "ambiguous",
    aliases: [
      {
        aliasId: "alias-mira-1",
        characterId: "entity-character-mira",
        alias: "Mira",
        normalizedAlias: "mira",
        kind: "given_name",
        ambiguous: true,
        confidence: {
          score: 0.72,
          classification: "low",
          basis: "Alias collides with a quoted signature."
        },
        evidence: []
      },
      {
        aliasId: "alias-mira-2",
        characterId: "entity-character-mira",
        alias: "M. Vale",
        normalizedAlias: "m vale",
        kind: "full_name",
        ambiguous: false,
        confidence: {
          score: 0.9,
          classification: "high",
          basis: "Exact full-name mention."
        },
        evidence: []
      }
    ],
    firstMentionId: "entity-mention-1",
    mentionCount: 2
  };
  const mention = {
    ...header("entity-mention-1", sha("c"), 0),
    mentionId: "entity-mention-1",
    chapterId: "entity-chapter-1",
    sceneId: "entity-scene-1",
    exactText: exactText("Mira", 24),
    mentionKind: "proper_name",
    resolution: "ambiguous",
    effectiveCharacterId: "entity-character-mira",
    candidateCharacterIds: ["entity-character-mira"]
  };
  const rowan = {
    ...header("entity-character-rowan", sha("1"), 1),
    characterId: "entity-character-rowan",
    canonicalName: "Rowan Cross",
    normalizedCanonicalName: "rowan cross",
    kind: "person",
    identityStatus: "resolved",
    aliases: [],
    firstMentionId: "entity-mention-rowan",
    mentionCount: 1
  };
  const dialogue = {
    ...header("entity-dialogue-1", sha("d"), 0),
    dialogueLineId: "entity-dialogue-1",
    chapterId: "entity-chapter-1",
    sceneId: "entity-scene-1",
    beatId: "entity-beat-1",
    exactText: exactText("Who told you?", 32),
    distinction: "spoken_dialogue",
    candidates: [],
    speakerState: "unknown",
    effectiveAttribution: {
      speakerCharacterId: null,
      selectedCandidateId: null,
      authority: "unresolved",
      confidence: {
        score: 0,
        classification: "unknown",
        basis: "No candidate cleared the attribution threshold."
      },
      requiresHumanReview: true
    }
  };
  const narration = {
    ...header("entity-narration-1", sha("e"), 0),
    narrationSpanId: "entity-narration-1",
    chapterId: "entity-chapter-1",
    sceneId: "entity-scene-1",
    exactText: exactText("I should never say it.", 48),
    classification: "internal_thought",
    narratorCharacterId: "entity-character-mira"
  };
  const continuity = {
    ...header("entity-continuity-1", sha("f"), 0),
    continuityFindingId: "entity-continuity-1",
    category: "possible_duplicate_character",
    severity: "warning",
    machineStatus: "open",
    explanation: "Mira and M. Vale may be duplicate identities.",
    suggestedReviewAction: "Resolve or confirm the alias.",
    relatedEntityIds: ["entity-character-mira"],
    requiresHumanReview: true
  };
  const relationship = {
    ...header("entity-relationship-1", sha("2"), 0),
    relationshipId: "entity-relationship-1",
    sourceCharacterId: null,
    targetCharacterId: "entity-character-mira",
    sourceCandidateCharacterIds: ["entity-character-rowan"],
    targetCandidateCharacterIds: ["entity-character-mira"],
    resolution: "ambiguous",
    sceneId: "entity-scene-1",
    chapterId: "entity-chapter-1",
    scope: {
      kind: "scene",
      firstSceneId: "entity-scene-1",
      lastSceneId: "entity-scene-1",
      sourceRange: sourceSpan(24, 80)
    },
    validFromEventId: null,
    validThroughEventId: null,
    kind: "unknown",
    state: "The directional relationship requires review.",
    change: "uncertain"
  };
  return {
    "agent-executions": [],
    chapters: [chapter],
    scenes: [scene as unknown as AnalysisEntity],
    beats: [beat as unknown as AnalysisEntity],
    characters: [
      character as unknown as AnalysisEntity,
      rowan as unknown as AnalysisEntity
    ],
    mentions: [mention as unknown as AnalysisEntity],
    "dialogue-lines": [dialogue as unknown as AnalysisEntity],
    "narration-spans": [narration as unknown as AnalysisEntity],
    "pov-segments": [],
    locations: [],
    "timeline-events": [],
    "temporal-constraints": [],
    relationships: [relationship as unknown as AnalysisEntity],
    "emotional-states": [],
    "dramatic-intents": [],
    "continuity-findings": [continuity as unknown as AnalysisEntity]
  };
}

function gateReviews(
  run: StoryAnalysisRun,
  firstWarning: boolean
): readonly AnalysisGateReview[] {
  const snapshot = run.currentSnapshot;
  if (snapshot === null) {
    throw new Error("Fixture snapshot is missing.");
  }
  return [
    "story_structure_review",
    "character_registry_review",
    "dialogue_attribution_review",
    "whole_book_analysis_review"
  ].map((gateId, index) => ({
    contractVersion: "2.0.0" as const,
    reviewId: `review-gate-${index + 1}`,
    projectId: run.projectId,
    gateId: gateId as AnalysisGateReview["gateId"],
    runId: run.runId,
    snapshotId: snapshot.snapshotId,
    state: "pending" as const,
    revision: 1,
    artifactFingerprint: sha("d"),
    evidenceFingerprint: sha("e"),
    evidence: {
      projectId: run.projectId,
      sourceDocumentId: run.sourceDocumentId,
      extractionId: run.extractionId,
      extractionRevision: run.extractionRevision,
      storyId: run.storyId,
      profileId: WHOLE_BOOK_ANALYSIS_PROFILE_ID,
      profileFingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
      runId: run.runId,
      runFingerprint: run.runFingerprint,
      snapshotId: snapshot.snapshotId,
      snapshotRevision: snapshot.revision,
      snapshotFingerprint: snapshot.snapshotFingerprint,
      artifactFingerprint: sha("d"),
      evidenceFingerprint: sha("e")
    },
    openWarningIds:
      firstWarning && index === 0 ? ["warning-structure-1"] : [],
    acknowledgedWarningIds: [],
    latestDecision: null,
    provenance: humanReviewProvenance(),
    updatedAt: "2026-07-30T12:00:00Z"
  }));
}

function runtimeProvenance() {
  return {
    origin: "runtime_agent" as const,
    recordedAt: "2026-07-30T12:00:00Z",
    inputFingerprint: sha("4"),
    agentExecutionId: "execution-1",
    agentId: "story-structure" as const,
    agentVersion: "1.0.0" as const,
    producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
    producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
    deterministic: true
  };
}

function humanReviewProvenance() {
  return {
    origin: "human_review" as const,
    recordedAt: "2026-07-30T12:10:00Z",
    inputFingerprint: sha("4"),
    producerId: WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
    producerVersion: WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
    deterministic: true
  };
}

function sourceSpan(startOffset: number, endOffset: number) {
  return {
    sourceDocumentId: "document-1",
    extractionId: "extraction-1",
    extractionRevision: 2,
    offsetUnit: "unicode-code-point" as const,
    startOffset,
    endOffset,
    textSha256: sha("9")
  };
}

function correctionHistoryItem(
  correctionId: string,
  targetEntityId: string,
  recordedAt: string,
  reason: string
) {
  return {
    contractVersion: "2.0.0" as const,
    correctionId,
    projectId: "project-1",
    runId: "run-1",
    snapshotId: "snapshot-1",
    category: "character_identity" as const,
    targetCollection: "characters" as const,
    targetEntityId,
    expectedTargetRevision: 1,
    expectedRunFingerprint: sha("5"),
    previousValueFingerprint: sha("b"),
    correctedValueFingerprint: sha("c"),
    patch: {
      canonicalName: "Reviewed identity",
      normalizedCanonicalName: "reviewed identity",
      identityStatus: "resolved" as const
    },
    actor: {
      classification: "human" as const,
      actorId: "local-user"
    },
    reason,
    recordedAt,
    immutable: true as const,
    lockedAgainstAutomation: true as const,
    idempotencyFingerprint: sha("f")
  };
}

function exactText(text: string, startOffset: number) {
  return {
    ...sourceSpan(startOffset, startOffset + [...text].length),
    exactText: text,
    exactTextSha256: sha("a"),
    originalCodePointCount: [...text].length,
    exactTextTruncated: false,
    originalTextPreserved: true as const
  };
}

function counts() {
  return {
    agentExecutions: 11,
    chapters: 1,
    scenes: 1,
    beats: 1,
    characters: 1,
    mentions: 1,
    dialogueLines: 1,
    narrationSpans: 1,
    povSegments: 1,
    locations: 1,
    timelineEvents: 1,
    temporalConstraints: 1,
    relationships: 1,
    emotionalStates: 1,
    dramaticIntents: 1,
    continuityFindings: 1,
    corrections: 0
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
      message: "Not configured for this focused test.",
      retryable: false
    }
  };
}
