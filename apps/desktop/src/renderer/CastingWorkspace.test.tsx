import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ProjectDetail } from "@cinematic-story-studio/contracts/api";

import {
  CASTING_PROFILE_FINGERPRINT,
  CASTING_PROFILE_ID,
  type CastingRun,
  type VoiceCatalogResponse
} from "../shared/casting-api";
import type {
  CinematicStoryDesktopApi,
  DesktopResult
} from "../shared/desktop-api";
import { CastingWorkspace } from "./CastingWorkspace";

const hashA = "a".repeat(64);
const hashB = "b".repeat(64);
const hashC = "c".repeat(64);
const hashD = "d".repeat(64);

describe("CastingWorkspace", () => {
  it("renders governed prerequisites, lazy roles/candidates, rights, conflicts, and gates", async () => {
    const api = createApi();
    const user = userEvent.setup();
    renderWorkspace(api);

    expect(
      await screen.findByRole("heading", { name: "Casting workspace" })
    ).toBeVisible();
    expect(screen.getByText("Current and approved")).toBeVisible();
    expect(screen.getByText(CASTING_PROFILE_ID)).toBeVisible();
    expect(await screen.findByText("fixture-provider")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Primary narrator.*1,200 words/u })
    ).toBeVisible();
    expect(
      screen.getByText(
        (_content, element) =>
          element?.tagName === "P" &&
          element.textContent ===
            "0 dialogue lines · 8 narration spans · 1,200 words · chapters 1–4"
      )
    ).toBeVisible();
    expect(screen.getByText("Metadata Similarity Risk")).toBeVisible();
    expect(screen.getByText("Machine Proposal")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Narrator Casting Review" })
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Complete Cast Review" })
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: /Primary narrator.*1,200 words/u })
    );
    expect(
      await screen.findByRole("heading", { name: "Amber Atlas" })
    ).toBeVisible();
    expect(screen.getByText("Language Support · Pass")).toBeVisible();
    expect(screen.getByText("Locale Match · 90")).toBeVisible();
    expect(screen.getByText("Rights Restricted")).toBeVisible();
    expect(screen.getByText("Consent Restricted")).toBeVisible();
    expect(screen.getByText("Provider Available")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Approve planned reuse" })
    ).toBeVisible();
    expect(api.casting.listCandidates).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-1",
        runId: "casting-run-1",
        roleId: "role-narrator",
        expectedRoleRevision: 2,
        expectedRunFingerprint: hashD,
        expectedCatalogFingerprint: hashB,
        expectedSnapshotFingerprint: hashC,
        limit: 12
      })
    );

    const completeCard = screen
      .getByRole("heading", { name: "Complete Cast Review" })
      .closest("article");
    expect(completeCard).not.toBeNull();
    expect(
      within(completeCard as HTMLElement).getByRole("button", {
        name: "Approve"
      })
    ).toBeDisabled();
    expect(
      screen.getByText(/restricted rights awaiting acknowledgement/u)
    ).toBeVisible();
  }, 10_000);

  it("submits server-issued role fingerprints for selection, lock, and rights acknowledgement", async () => {
    const api = createApi();
    const user = userEvent.setup();
    renderWorkspace(api);

    const roleButton = await screen.findByRole("button", {
      name: /Primary narrator.*1,200 words/u
    });
    await user.click(roleButton);
    await screen.findByRole("heading", { name: "Amber Atlas" });

    await user.click(screen.getByRole("button", { name: "Select voice" }));
    await waitFor(() => {
      expect(api.casting.appendCorrection).toHaveBeenCalledWith(
        expect.objectContaining({
          operation: "select_voice",
          targetRoleId: "role-narrator",
          previousEffectiveFingerprint: hashA,
          voiceProfileId: "voice-amber",
          correctedValue: { voiceProfileId: "voice-amber" }
        })
      );
    });

    await user.click(
      screen.getByRole("button", { name: "Lock assignment" })
    );
    await waitFor(() => {
      expect(api.casting.appendCorrection).toHaveBeenCalledWith(
        expect.objectContaining({
          operation: "lock_assignment",
          correctedValue: { assignmentId: "assignment-narrator" },
          voiceProfileId: null,
          previousEffectiveFingerprint: hashA
        })
      );
    });

    await user.click(
      screen.getByRole("button", {
        name: "Acknowledge restricted rights"
      })
    );
    await waitFor(() => {
      expect(api.casting.appendCorrection).toHaveBeenCalledWith(
        expect.objectContaining({
          operation: "acknowledge_restricted_rights",
          voiceProfileId: "voice-amber",
          correctedValue: {
            rightsRecordId: "rights-amber",
            rightsRecordRevision: 1
          }
        })
      );
    });
  });

  it("submits an explicit content-free custom role with frozen evidence", async () => {
    const api = createApi();
    const user = userEvent.setup();
    renderWorkspace(api);

    await screen.findByRole("heading", { name: "Casting workspace" });
    await user.clear(screen.getByLabelText("Custom role definition ID"));
    await user.type(
      screen.getByLabelText("Custom role definition ID"),
      "custom-role-a"
    );
    await user.type(
      screen.getByLabelText("Custom role label"),
      "Festival announcer"
    );
    await user.clear(screen.getByLabelText("Custom role reason"));
    await user.type(
      screen.getByLabelText("Custom role reason"),
      "Producer-defined role with no manuscript source."
    );
    await user.click(
      screen.getByRole("button", { name: "Create custom role" })
    );

    await waitFor(() => {
      expect(api.casting.createCustomRole).toHaveBeenCalledTimes(1);
    });
    const submitted =
      vi.mocked(api.casting.createCustomRole).mock.calls[0]?.[0];
    expect(submitted?.idempotencyKey).toMatch(
      /^[0-9a-f-]{36}$/u
    );
    expect(submitted).toEqual({
        projectId: "project-1",
        runId: "casting-run-1",
        expectedRunFingerprint: hashD,
        expectedCatalogRevisionId: "catalog-1",
        expectedCatalogFingerprint: hashB,
        expectedSnapshotId: "analysis-snapshot-1",
        expectedSnapshotRevision: 2,
        expectedSnapshotFingerprint: hashC,
        definitionId: "custom-role-a",
        label: "Festival announcer",
        performanceRequirements: {
          language: "en",
          locales: ["en-US"],
          agePresentationRange: null,
          vocalPresentations: [],
          preferredTextures: [],
          speakingRateRange: null,
          requiredExpressiveRange: [],
          longFormRequired: false
        },
        reason: "Producer-defined role with no manuscript source.",
        expectedCorrectionSetFingerprint: hashA,
        expectedCastingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
        idempotencyKey: submitted?.idempotencyKey
    });
  }, 20_000);

  it("does not offer reuse approval for a non-reuse conflict", async () => {
    const api = createApi();
    vi.mocked(api.casting.listConflicts).mockResolvedValue(
      ok({
        correlationId: "correlation-conflicts",
        castingRunId: "casting-run-1",
        pageSize: 1,
        total: 1,
        items: [{ ...conflict(), category: "rights_conflict" }]
      } as never)
    );
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.click(
      await screen.findByRole("button", {
        name: /Primary narrator.*1,200 words/u
      })
    );
    expect(
      screen.queryByRole("button", { name: "Approve planned reuse" })
    ).not.toBeInTheDocument();
  });

  it("retries a transient run refresh until published casting resources load", async () => {
    const api = createApi();
    const queuedRun = {
      ...castingRun(),
      status: "running",
      currentStage: "generate_bounded_candidates",
      progress: 0.56,
      outputFingerprint: null,
      approvedCastSnapshot: null
    } as unknown as CastingRun;
    vi.mocked(api.casting.listRuns).mockResolvedValue(
      ok({
        correlationId: "correlation-runs",
        pageSize: 1,
        total: 1,
        items: [queuedRun]
      } as never)
    );
    vi.mocked(api.casting.getRun)
      .mockResolvedValueOnce(
        ok({ correlationId: "correlation-run", run: queuedRun } as never)
      )
      .mockRejectedValueOnce(new Error("transient publication race"))
      .mockResolvedValue(
        ok({ correlationId: "correlation-run", run: castingRun() } as never)
      );

    renderWorkspace(api);

    expect(await screen.findByText("Running")).toBeVisible();
    expect(await screen.findByText("Succeeded", {}, { timeout: 5_500 }))
      .toBeVisible();
    expect(api.casting.getRun).toHaveBeenCalledTimes(3);
  }, 8_000);
});

function renderWorkspace(api: CinematicStoryDesktopApi) {
  return render(
    <CastingWorkspace
      project={projectDetail()}
      api={api}
      connected
      onNotice={vi.fn()}
      onError={vi.fn()}
    />
  );
}

function createApi(): CinematicStoryDesktopApi {
  const run = castingRun();
  const catalog = voiceCatalog();
  const rolesPage = {
    correlationId: "correlation-roles",
    castingRunId: run.castingRunId,
    pageSize: 1,
    total: 1,
    items: [narratorRole()]
  };
  const assignmentsPage = {
    correlationId: "correlation-assignments",
    castingRunId: run.castingRunId,
    pageSize: 1,
    total: 1,
    items: [assignment()]
  };
  const conflictsPage = {
    correlationId: "correlation-conflicts",
    castingRunId: run.castingRunId,
    pageSize: 1,
    total: 1,
    items: [conflict()]
  };
  const correctionsPage = {
    correlationId: "correlation-corrections",
    castingRunId: run.castingRunId,
    pageSize: 0,
    total: 0,
    items: []
  };
  const reviews = castingReviews();
  const casting = {
    getCatalog: vi.fn(async () => ok(catalog)),
    createRun: vi.fn(async () => fail("NOT_USED")),
    listRuns: vi.fn(async () =>
      ok({
        correlationId: "correlation-runs",
        pageSize: 1,
        total: 1,
        items: [run]
      } as never)
    ),
    getRun: vi.fn(async () =>
      ok({ correlationId: "correlation-run", run } as never)
    ),
    listRoles: vi.fn(async () => ok(rolesPage as never)),
    createCustomRole: vi.fn(async () =>
      ok({
        correlationId: "correlation-custom-role",
        role: customRole(),
        invalidatedGateIds: [
          "narrator_casting_review",
          "character_casting_review",
          "complete_cast_review"
        ],
        run,
        reviews
      } as never)
    ),
    listCandidates: vi.fn(async () =>
      ok({
        correlationId: "correlation-candidates",
        castingRunId: run.castingRunId,
        pageSize: 1,
        total: 1,
        items: [candidate()]
      } as never)
    ),
    listConflicts: vi.fn(async () => ok(conflictsPage as never)),
    listAssignments: vi.fn(async () => ok(assignmentsPage as never)),
    listCorrections: vi.fn(async () => ok(correctionsPage as never)),
    appendCorrection: vi.fn(async () =>
      ok({
        correlationId: "correlation-correction",
        correction: {},
        assignment: assignment(),
        invalidatedGateIds: [],
        run,
        reviews
      } as never)
    ),
    listReviews: vi.fn(async () =>
      ok({
        correlationId: "correlation-reviews",
        castingRunId: run.castingRunId,
        items: reviews
      } as never)
    ),
    decideReview: vi.fn(async () => fail("NOT_USED"))
  };
  return {
    version: "1.0.0",
    backend: {
      getStatus: vi.fn(async () => fail("NOT_USED")),
      reconnect: vi.fn(async () => fail("NOT_USED")),
      onStatus: vi.fn(() => () => undefined)
    },
    projects: {
      list: vi.fn(async () => fail("NOT_USED")),
      create: vi.fn(async () => fail("NOT_USED")),
      open: vi.fn(async () => fail("NOT_USED")),
      restoreRecent: vi.fn(async () => fail("NOT_USED")),
      importSelectedFile: vi.fn(async () => fail("NOT_USED")),
      getImportReview: vi.fn(async () => fail("NOT_USED")),
      decideImportReview: vi.fn(async () => fail("NOT_USED"))
    },
    dialogue: {
      correctSpeaker: vi.fn(async () => fail("NOT_USED"))
    },
    analysis: {
      createRun: vi.fn(async () => fail("NOT_USED")),
      listRuns: vi.fn(async () => fail("NOT_USED")),
      getRun: vi.fn(async () => fail("NOT_USED")),
      listEntities: vi.fn(async () => fail("NOT_USED")),
      listCorrections: vi.fn(async () => fail("NOT_USED")),
      appendCorrection: vi.fn(async () => fail("NOT_USED")),
      listReviews: vi.fn(async () => fail("NOT_USED")),
      decideReview: vi.fn(async () => fail("NOT_USED"))
    },
    casting,
    jobs: {
      create: vi.fn(async () => fail("NOT_USED")),
      get: vi.fn(async () => fail("NOT_USED")),
      events: vi.fn(async () => fail("NOT_USED")),
      cancel: vi.fn(async () => fail("NOT_USED")),
      retry: vi.fn(async () => fail("NOT_USED")),
      resume: vi.fn(async () => fail("NOT_USED"))
    },
    providers: {
      health: vi.fn(async () => fail("NOT_USED"))
    },
    capabilities: {
      ffmpeg: vi.fn(async () => fail("NOT_USED"))
    }
  } as unknown as CinematicStoryDesktopApi;
}

function projectDetail(): ProjectDetail {
  const analysisRun = {
    runId: "analysis-run-1",
    extractionId: "extraction-1",
    approvedEvidenceFingerprint: hashA,
    currentSnapshot: {
      snapshotId: "analysis-snapshot-1",
      revision: 2,
      snapshotFingerprint: hashC,
      correctionSetFingerprint: hashA
    }
  };
  const review = (
    gateId: string,
    decisionId: string
  ) => ({
    gateId,
    runId: analysisRun.runId,
    snapshotId: analysisRun.currentSnapshot.snapshotId,
    state: "approved",
    artifactFingerprint: hashA,
    evidenceFingerprint: hashB,
    latestDecision: {
      decisionId,
      decision: "approved",
      artifactFingerprint: hashA,
      evidenceFingerprint: hashB
    }
  });
  return {
    project: { projectId: "project-1" },
    currentAnalysisRun: analysisRun,
    importReviews: [
      {
        extractionId: "extraction-1",
        state: "approved",
        evidenceFingerprint: hashA,
        latestDecision: {
          decisionId: "import-decision-1",
          decision: "approved",
          evidenceFingerprint: hashA
        }
      }
    ],
    analysisGateReviews: [
      review("story_structure_review", "decision-structure"),
      review("character_registry_review", "decision-characters"),
      review("dialogue_attribution_review", "decision-dialogue"),
      review("whole_book_analysis_review", "decision-whole-book")
    ]
  } as unknown as ProjectDetail;
}

function castingRun(): CastingRun {
  return {
    castingRunId: "casting-run-1",
    projectId: "project-1",
    jobId: "job-casting-1",
    status: "succeeded",
    currentStage: "complete",
    progress: 1,
    catalogRevisionId: "catalog-1",
    catalogFingerprint: hashB,
    profile: {
      profileId: CASTING_PROFILE_ID,
      fingerprint: CASTING_PROFILE_FINGERPRINT
    },
    inputFingerprint: hashA,
    outputFingerprint: hashD,
    effectiveCorrectionSetFingerprint: hashA,
    prerequisites: {
      analysisRunId: "analysis-run-1",
      analysisSnapshotId: "analysis-snapshot-1",
      analysisSnapshotRevision: 2,
      analysisSnapshotFingerprint: hashC,
      analysisCorrectionSetFingerprint: hashA
    },
    summary: {
      productionRoles: 1,
      narratorRoles: 1,
      characterRoles: 0,
      preReductionCandidates: 10,
      finalCandidates: 1,
      conflicts: 1,
      assignments: 1,
      corrections: 0
    },
    approvedCastSnapshot: {
      snapshotId: "cast-snapshot-1",
      revision: 1,
      reviewEligible: false
    }
  } as unknown as CastingRun;
}

function narratorRole() {
  return {
    roleId: "role-narrator",
    projectId: "project-1",
    roleType: "primary_narrator",
    narratorKind: "primary",
    phase2EntityId: null,
    effectiveDisplayLabel: "Primary narrator",
    effectiveFingerprint: hashA,
    analysisRunId: "analysis-run-1",
    analysisSnapshotId: "analysis-snapshot-1",
    analysisSnapshotRevision: 2,
    analysisSnapshotFingerprint: hashC,
    dialogueLineCount: 0,
    narrationSpanCount: 8,
    approximateWordCount: 1_200,
    range: {
      firstChapterOrdinal: 1,
      lastChapterOrdinal: 4,
      firstSceneOrdinal: 1,
      lastSceneOrdinal: 8
    },
    languageRequirements: ["en"],
    performanceRequirements: {
      language: "en",
      locales: ["en-US"],
      agePresentationRange: null,
      vocalPresentations: ["neutral"],
      preferredTextures: ["warm"],
      speakingRateRange: {
        minimum: 0.9,
        maximum: 1.1,
        unit: "multiplier"
      },
      requiredExpressiveRange: ["reflective"],
      longFormRequired: true
    },
    warnings: [],
    status: "active",
    revision: 2
  } as const;
}

function customRole() {
  return {
    ...narratorRole(),
    roleId: "role-custom-a",
    roleType: "custom",
    narratorKind: undefined,
    effectiveDisplayLabel: "Festival announcer",
    dialogueLineCount: 0,
    narrationSpanCount: 0,
    approximateWordCount: 0,
    range: {
      firstChapterOrdinal: null,
      lastChapterOrdinal: null,
      firstSceneOrdinal: null,
      lastSceneOrdinal: null
    }
  } as const;
}

function assignment() {
  return {
    assignmentId: "assignment-narrator",
    projectId: "project-1",
    castingRunId: "casting-run-1",
    roleId: "role-narrator",
    voiceProfileId: "voice-amber",
    voiceProfileVersion: "1.0.0",
    rightsRecordId: "rights-amber",
    rightsRecordRevision: 1,
    authority: "machine_proposal",
    rightsState: "restricted",
    effective: true
  } as const;
}

function conflict() {
  return {
    conflictId: "conflict-1",
    castingRunId: "casting-run-1",
    category: "metadata_similarity_risk",
    severity: "warning",
    roleIds: ["role-narrator"],
    voiceProfileIds: ["voice-amber"],
    explanation:
      "Declared texture and pace overlap with another major production role.",
    metadataOnly: true,
    acousticSimilarityClaimed: false,
    resolutionState: "open",
    dispositionCorrectionId: null
  } as const;
}

function candidate() {
  return {
    candidateId: "candidate-amber",
    castingRunId: "casting-run-1",
    roleId: "role-narrator",
    voiceProfileId: "voice-amber",
    rank: 1,
    preReductionRank: 1,
    rejectedByCorrectionId: null,
    conflictWarnings: [
      {
        code: "METADATA_SIMILARITY_RISK",
        message: "Declared metadata overlaps another role."
      }
    ],
    assessment: {
      compatibilityStatus: "compatible_with_warnings",
      compatibilityScore: 0.88,
      confidence: { classification: "high" },
      hardConstraints: [
        {
          constraintId: "language_support",
          result: "pass",
          explanation: "English is declared."
        }
      ],
      softPreferences: [
        {
          preferenceId: "locale_match",
          score: 0.9,
          explanation: "Locale matches the role."
        }
      ],
      rightsEligibility: "restricted_requires_acknowledgement",
      languageEligibility: "pass",
      providerAvailability: "available",
      modelAvailability: "available",
      longFormSuitability: "preferred",
      explanation:
        "Metadata supports this role with explicit restricted-rights review."
    }
  } as const;
}

function voiceCatalog(): VoiceCatalogResponse {
  return {
    correlationId: "correlation-catalog",
    catalogRevision: {
      catalogRevisionId: "catalog-1",
      semanticVersion: "1.0.0",
      catalogFingerprint: hashB
    },
    providers: [
      {
        providerId: "fixture-provider",
        healthStatus: "healthy",
        runtimeAvailability: "available",
        networkUseRequired: false
      }
    ],
    models: [
      {
        modelId: "fixture-model",
        providerId: "fixture-provider",
        availability: "available",
        deprecated: false
      }
    ],
    items: [
      {
        voiceProfileId: "voice-amber",
        providerId: "fixture-provider",
        modelId: "fixture-model",
        displayLabel: "Amber Atlas",
        locale: "en-US",
        vocalTexture: "warm",
        longFormSuitability: "preferred",
        rightsRecordId: "rights-amber",
        rightsState: "restricted"
      }
    ],
    rights: [
      {
        rightsRecordId: "rights-amber",
        voiceProfileId: "voice-amber",
        revision: 1,
        state: "restricted",
        consentStatus: "restricted"
      }
    ],
    total: 1,
    pageSize: 1
  } as unknown as VoiceCatalogResponse;
}

function castingReviews() {
  const review = (
    gateId:
      | "narrator_casting_review"
      | "character_casting_review"
      | "complete_cast_review"
  ) => ({
    gateId,
    projectId: "project-1",
    castingRunId: "casting-run-1",
    state: "pending",
    revision: 1,
    openWarningIds: [],
    acknowledgedWarningIds: [],
    latestDecision: null,
    evidence: {
      approvedCastSnapshotId: "cast-snapshot-1",
      approvedCastSnapshotRevision: 1,
      evidenceFingerprint: hashA
    }
  });
  return [
    review("narrator_casting_review"),
    review("character_casting_review"),
    review("complete_cast_review")
  ] as const;
}

function ok<T>(value: T): DesktopResult<T> {
  return { ok: true, value };
}

function fail<T>(code: string): DesktopResult<T> {
  return {
    ok: false,
    error: {
      code,
      message: code,
      retryable: false
    }
  };
}
