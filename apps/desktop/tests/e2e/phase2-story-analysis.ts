import { createHash } from "node:crypto";

import { expect, type Page } from "@playwright/test";

import {
  ANALYSIS_ENTITY_COLLECTIONS,
  ANALYSIS_GATE_IDS,
  ANALYSIS_JOB_STAGES,
  PHASE_2_RUNTIME_AGENTS,
  WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
  WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION,
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
  type AnalysisCorrection,
  type AnalysisEntity,
  type AnalysisEntityCollection,
  type AnalysisGateReview,
  type JobEvent,
  type StoryAnalysisRun
} from "@cinematic-story-studio/contracts";

import {
  createPackagedFlowRecorder,
  packagedCorrectionReasonFingerprints,
  packagedPhase2GovernanceFlow,
  type PackagedAnalysisAssertionsEvidence,
  type PackagedAnalysisCorrectionEvidence,
  type PackagedAnalysisGateStateEvidence,
  type PackagedStoryAnalysisEvidence
} from "../../src/verification/packaged-e2e-evidence";

const phase2CorrectionReason = Object.freeze({
  characterIdentity:
    "Phase 2 E2E: approved evidence resolves this ambiguous identity.",
  dialogueSpeaker:
    "Phase 2 E2E: adjacent source evidence identifies the speaker.",
  continuityDisposition:
    "Phase 2 E2E: human review confirms this continuity issue."
});
const snapshotContextRetryLimit = 3;
const snapshotContextRetryDelayMs = 250;
const projectContextChangedCode = "PROJECT_CONTEXT_CHANGED";

export interface Phase2RuntimeSnapshot {
  readonly run: StoryAnalysisRun;
  readonly events: readonly JobEvent[];
  readonly corrections: readonly AnalysisCorrection[];
  readonly reviews: readonly AnalysisGateReview[];
  readonly entities: Readonly<
    Record<AnalysisEntityCollection, readonly AnalysisEntity[]>
  >;
}

export interface Phase2WorkflowEvidence {
  readonly initial: Phase2RuntimeSnapshot;
  readonly governed: Phase2RuntimeSnapshot;
  readonly flow: readonly string[];
}

type Phase2RuntimeSnapshotAttemptResult =
  | {
      readonly outcome: "project_context_changed";
    }
  | {
      readonly outcome: "succeeded";
      readonly snapshot: Phase2RuntimeSnapshot;
    };

export async function runPhase2GovernanceWorkflow(
  page: Page
): Promise<Phase2WorkflowEvidence> {
  const flow = createPackagedFlowRecorder(packagedPhase2GovernanceFlow);
  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Story intelligence" })
  ).toBeVisible();

  const start = page.getByRole("button", {
    name: /^(Analyze whole book|Rerun analysis)$/u
  });
  await expect(start).toBeEnabled({ timeout: 30_000 });
  await start.click();
  flow.record("start_whole_book_analysis");
  await expect(
    page.locator(".analysis-run-status .analysis-state")
  ).toHaveText("Succeeded", { timeout: 120_000 });
  await expect(
    page.getByRole("progressbar", {
      name: "Whole-book analysis progress"
    })
  ).toHaveAttribute("value", "1");
  await dismissNotice(page);

  const initial = await readPhase2RuntimeSnapshot(page);
  expect(observedStages(initial.events)).toEqual(ANALYSIS_JOB_STAGES);
  flow.record("observe_analysis_stages");
  expect(initial.corrections).toHaveLength(1);
  expect(initial.corrections[0]?.category).toBe("dialogue_speaker");

  await page.getByRole("button", { name: "Structure", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "Analysis chapters" })
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.locator('.analysis-entity[data-collection="beats"]').first()
  ).toBeVisible();
  flow.record("inspect_structure");

  await page.getByRole("button", { name: "Characters", exact: true }).click();
  const characterCards = page.locator(
    '.analysis-entity[data-collection="characters"]'
  );
  await expect(characterCards.first()).toBeVisible({ timeout: 30_000 });
  const ambiguousCharacter = page
    .locator(
      '.analysis-entity[data-collection="characters"][data-identity-status="ambiguous"]'
    )
    .first();
  await expect(ambiguousCharacter).toBeVisible({ timeout: 30_000 });
  flow.record("inspect_character_registry");
  const characterCard = ambiguousCharacter;
  await characterCard
    .getByRole("button", { name: /^Correct /u })
    .click();
  let dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const canonicalName = dialog.getByLabel("Canonical name", {
    exact: true
  });
  const priorCanonicalName = await canonicalName.inputValue();
  await canonicalName.fill(`${priorCanonicalName} Reviewed`);
  await dialog
    .getByLabel("Identity status", { exact: true })
    .fill("resolved");
  await dialog
    .getByLabel("Correction reason", { exact: true })
    .fill(phase2CorrectionReason.characterIdentity);
  await dialog
    .getByRole("button", { name: "Save human correction" })
    .click();
  await expect(dialog).toBeHidden({ timeout: 30_000 });
  await expectHumanCorrectionNotice(page);
  flow.record("correct_character_identity");

  await page
    .getByRole("button", { name: "Dialogue & narration", exact: true })
    .click();
  const dialogueCard = page
    .locator(
      '.analysis-entity[data-collection="dialogue-lines"][data-speaker-state="ambiguous"], ' +
        '.analysis-entity[data-collection="dialogue-lines"][data-speaker-state="unknown"]'
    )
    .first();
  await expect(dialogueCard).toBeVisible({ timeout: 30_000 });
  await expect(
    page.locator('.analysis-entity[data-collection="narration-spans"]').first()
  ).toBeVisible();
  flow.record("inspect_dialogue_and_narration");
  await dialogueCard
    .getByRole("button", { name: /^Correct /u })
    .click();
  dialog = page.getByRole("dialog");
  const speaker = dialog.getByLabel("Effective speaker", {
    exact: true
  });
  await expect
    .poll(() => speaker.locator("option").count())
    .toBeGreaterThan(1);
  await speaker.selectOption({ index: 1 });
  await dialog
    .getByLabel("Correction reason", { exact: true })
    .fill(phase2CorrectionReason.dialogueSpeaker);
  await dialog
    .getByRole("button", { name: "Save human correction" })
    .click();
  await expect(dialog).toBeHidden({ timeout: 30_000 });
  await expectHumanCorrectionNotice(page);
  flow.record("correct_dialogue_speaker");

  await page.getByRole("button", { name: "Whole book", exact: true }).click();
  await page
    .getByLabel("Analysis layer", { exact: true })
    .selectOption("continuity-findings");
  const continuityCard = page
    .locator('.analysis-entity[data-collection="continuity-findings"]')
    .first();
  await expect(continuityCard).toBeVisible({ timeout: 30_000 });
  flow.record("inspect_whole_book_intelligence");
  await continuityCard
    .getByRole("button", { name: /^Correct /u })
    .click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Disposition", { exact: true })
    .selectOption("confirmed_issue");
  await dialog
    .getByLabel("Explanation", { exact: true })
    .fill(phase2CorrectionReason.continuityDisposition);
  await dialog
    .getByLabel("Correction reason", { exact: true })
    .fill(phase2CorrectionReason.continuityDisposition);
  await dialog
    .getByRole("button", { name: "Save human correction" })
    .click();
  await expect(dialog).toBeHidden({ timeout: 30_000 });
  await expectHumanCorrectionNotice(page);
  flow.record("disposition_continuity");

  for (const gateId of ANALYSIS_GATE_IDS) {
    await approveGate(page, gateTitle(gateId));
    flow.record(gateFlowStep(gateId));
  }

  const governed = await readPhase2RuntimeSnapshot(page);
  expect(governed.corrections).toHaveLength(4);
  expect(
    governed.reviews.map((review) => [review.gateId, review.state])
  ).toEqual(
    ANALYSIS_GATE_IDS.map((gateId) => [gateId, "approved"])
  );
  return { initial, governed, flow: flow.complete() };
}

export async function readPhase2RuntimeSnapshot(
  page: Page
): Promise<Phase2RuntimeSnapshot> {
  for (let attempt = 1; attempt <= snapshotContextRetryLimit; attempt += 1) {
    const result = await readPhase2RuntimeSnapshotAttempt(page);
    if (result.outcome === "succeeded") {
      return result.snapshot;
    }
    if (attempt < snapshotContextRetryLimit) {
      // When a durable job leaves an active state, the renderer performs one
      // same-project refresh. That refresh intentionally changes the guarded
      // selection epoch and rejects concurrent evidence reads. Retry only that
      // exact typed result, rebuild the entire snapshot, and never reuse a
      // partial page from the invalidated attempt.
      await page.waitForTimeout(snapshotContextRetryDelayMs * attempt);
    }
  }
  throw new Error(
    "The Phase 2 project context changed during all bounded evidence reads."
  );
}

async function readPhase2RuntimeSnapshotAttempt(
  page: Page
): Promise<Phase2RuntimeSnapshotAttemptResult> {
  const result = await page.evaluate(
    async ({ collections, contextChangedCode }) => {
      const restored = await window.cinematicStory.projects.restoreRecent();
      if (!restored.ok) {
        if (restored.error.code === contextChangedCode) {
          return { outcome: "project_context_changed" } as const;
        }
        throw new Error(
          `The Phase 2 project restore failed with ${restored.error.code}: ${restored.error.message}`
        );
      }
      if (restored.value === null) {
        throw new Error("The Phase 2 project could not be restored.");
      }
      const projectedRun = restored.value.currentAnalysisRun;
      if (projectedRun === null) {
        throw new Error("The current Phase 2 run was unavailable.");
      }
      const runResult = await window.cinematicStory.analysis.getRun({
        projectId: projectedRun.projectId,
        runId: projectedRun.runId
      });
      if (!runResult.ok) {
        if (runResult.error.code === contextChangedCode) {
          return { outcome: "project_context_changed" } as const;
        }
        throw new Error(
          `The Phase 2 run read failed with ${runResult.error.code}: ${runResult.error.message}`
        );
      }
      const run = runResult.value.run;
      if (run.currentSnapshot === null) {
        throw new Error("The current Phase 2 snapshot was unavailable.");
      }
      const currentSnapshot = run.currentSnapshot;
      const [
        eventResult,
        correctionResult,
        reviewResult,
        entityResults
      ] = await Promise.all([
        window.cinematicStory.jobs.events(run.jobId),
        window.cinematicStory.analysis.listCorrections({
          projectId: run.projectId,
          runId: run.runId,
          limit: 200
        }),
        window.cinematicStory.analysis.listReviews({
          projectId: run.projectId,
          runId: run.runId,
          expectedSourceDocumentId: run.sourceDocumentId,
          expectedExtractionId: run.extractionId,
          expectedExtractionRevision: run.extractionRevision,
          expectedStoryId: run.storyId,
          expectedProfileId: run.profile.profileId,
          expectedProfileFingerprint: run.profile.fingerprint,
          expectedRunFingerprint: run.runFingerprint,
          expectedSnapshotId: currentSnapshot.snapshotId,
          expectedSnapshotRevision: currentSnapshot.revision,
          expectedSnapshotFingerprint:
            currentSnapshot.snapshotFingerprint
        }),
        Promise.all(
          collections.map(async (collection) => {
            const result =
              await window.cinematicStory.analysis.listEntities({
                projectId: run.projectId,
                runId: run.runId,
                expectedSnapshotId: currentSnapshot.snapshotId,
                collection,
                limit: 200
              });
            return [collection, result] as const;
          })
        )
      ]);
      const guardedResults = [
        eventResult,
        correctionResult,
        reviewResult,
        ...entityResults.map(([, entityResult]) => entityResult)
      ];
      if (
        guardedResults.some(
          (guardedResult) =>
            !guardedResult.ok &&
            guardedResult.error.code === contextChangedCode
        )
      ) {
        return { outcome: "project_context_changed" } as const;
      }
      const entityPages = entityResults.map(([collection, entityResult]) => {
        if (!entityResult.ok) {
          throw new Error(
            `The ${collection} read failed with ${entityResult.error.code}: ${entityResult.error.message}`
          );
        }
        const page = entityResult.value;
        if (page.nextCursor !== undefined) {
          throw new Error(
            `The ${collection} fixture exceeded one bounded E2E page.`
          );
        }
        if (
          page.runId !== run.runId ||
          page.snapshotId !== currentSnapshot.snapshotId ||
          page.collection !== collection ||
          page.pageSize !== page.items.length ||
          page.total < page.items.length
        ) {
          throw new Error(
            `The ${collection} page metadata did not bind the exact run snapshot.`
          );
        }
        return [collection, page.items] as const;
      });
      if (!eventResult.ok) {
        throw new Error(
          `The Phase 2 event read failed with ${eventResult.error.code}: ${eventResult.error.message}`
        );
      }
      if (!correctionResult.ok) {
        throw new Error(
          `The correction read failed with ${correctionResult.error.code}: ${correctionResult.error.message}`
        );
      }
      if (!reviewResult.ok) {
        throw new Error(
          `The gate read failed with ${reviewResult.error.code}: ${reviewResult.error.message}`
        );
      }
      return {
        outcome: "succeeded",
        snapshot: {
          run,
          events: eventResult.value.events,
          corrections: correctionResult.value.items,
          reviews: reviewResult.value.items,
          entities: Object.fromEntries(entityPages)
        }
      } as const;
    },
    {
      collections: ANALYSIS_ENTITY_COLLECTIONS,
      contextChangedCode: projectContextChangedCode
    }
  );
  return result as Phase2RuntimeSnapshotAttemptResult;
}

export function buildPackagedStoryAnalysisEvidence(
  workflow: Phase2WorkflowEvidence,
  restored: Phase2RuntimeSnapshot
): PackagedStoryAnalysisEvidence {
  const before = workflow.governed;
  const run = before.run;
  const machineSnapshot = requiredAnalysisSnapshot(run);
  const restoredSnapshot = requiredAnalysisSnapshot(restored.run);
  const agents = agentEvidence(before);
  const stages = observedStages(before.events);
  if (!equalStrings(stages, ANALYSIS_JOB_STAGES)) {
    throw new Error("The complete ordered Phase 2 stage ledger was not observed.");
  }
  const correction = <
    TCategory extends
      | "character_identity"
      | "dialogue_speaker"
      | "continuity_disposition"
  >(
    category: TCategory
  ): PackagedAnalysisCorrectionEvidence & {
    readonly category: TCategory;
  } => {
    const reason =
      category === "character_identity"
        ? phase2CorrectionReason.characterIdentity
        : category === "dialogue_speaker"
          ? phase2CorrectionReason.dialogueSpeaker
          : phase2CorrectionReason.continuityDisposition;
    const prior = requiredCorrection(before, category, reason);
    const after = requiredCorrection(restored, category, reason);
    const reasonFingerprint = createHash("sha256")
      .update(reason, "utf8")
      .digest("hex");
    const expectedReasonFingerprint =
      category === "character_identity"
        ? packagedCorrectionReasonFingerprints.characterIdentity
        : category === "dialogue_speaker"
          ? packagedCorrectionReasonFingerprints.dialogueSpeaker
          : packagedCorrectionReasonFingerprints.continuityDisposition;
    if (reasonFingerprint !== expectedReasonFingerprint) {
      throw new Error(`${category} correction reason fingerprint is invalid.`);
    }
    if (prior.correctionId !== after.correctionId) {
      throw new Error(`${category} correction identity changed after restart.`);
    }
    const priorEntity = requiredCorrectionEntity(before, prior);
    const afterEntity = requiredCorrectionEntity(restored, after);
    return {
      correctionId: prior.correctionId,
      category,
      targetEntityId: prior.targetEntityId,
      reasonFingerprint,
      previousValueFingerprint: prior.previousValueFingerprint,
      correctedValueFingerprint: prior.correctedValueFingerprint,
      effectiveValueFingerprintBeforeRestart:
        priorEntity.effectiveValueFingerprint,
      effectiveValueFingerprintAfterRestart:
        afterEntity.effectiveValueFingerprint,
      effectiveAuthorityBeforeRestart: requireHumanAuthority(priorEntity),
      effectiveAuthorityAfterRestart: requireHumanAuthority(afterEntity),
      immutable: prior.immutable,
      lockedAgainstAutomation: prior.lockedAgainstAutomation,
      persistedAfterRestart:
        JSON.stringify(prior) === JSON.stringify(after)
    };
  };
  const gates = ANALYSIS_GATE_IDS.map((gateId) => {
    const prior = requiredReview(before, gateId);
    const after = requiredReview(restored, gateId);
    if (
      canonicalJson(prior.latestDecision) !==
      canonicalJson(after.latestDecision)
    ) {
      throw new Error(
        `The immutable ${gateId} decision changed after restart.`
      );
    }
    return {
      gateId,
      beforeRestart: gateState(prior),
      afterRestart: gateState(after),
      immutable: true
    } as const;
  });
  const counts = {
    ...machineSnapshot.counts,
    corrections: before.corrections.length
  };
  return {
    profile: {
      profileId: run.profile.profileId,
      semanticVersion: run.profile.semanticVersion,
      profileFingerprint: run.profile.fingerprint,
      producerId: run.producer.producerId,
      producerVersion: run.producer.producerVersion
    },
    agents,
    approvedInput: {
      sourceDocumentId: run.sourceDocumentId,
      sourceRevision: run.sourceRevision,
      sourceSha256: run.sourceSha256,
      extractionId: run.extractionId,
      extractionRevision: run.extractionRevision,
      extractedTextSha256: run.extractedTextSha256,
      importReviewId: run.importReviewId,
      importReviewRevision: run.importReviewRevision,
      importReviewDecisionId: run.importReviewDecisionId,
      approvedEvidenceFingerprint: run.approvedEvidenceFingerprint,
      storyId: run.storyId,
      storyRevision: run.storyRevision,
      storyFingerprint: run.storyFingerprint
    },
    run: {
      runId: run.runId,
      inputFingerprint: run.inputFingerprint,
      runFingerprint: run.runFingerprint,
      jobId: run.jobId,
      status: requireSucceeded(run),
      snapshotId: machineSnapshot.snapshotId,
      snapshotRevision: machineSnapshot.revision,
      snapshotFingerprint: machineSnapshot.snapshotFingerprint,
      correctionSetFingerprint: machineSnapshot.correctionSetFingerprint
    },
    observedStages: stages,
    counts,
    assertions: analysisAssertions(workflow.initial),
    corrections: {
      characterIdentity: correction("character_identity"),
      dialogueSpeaker: correction("dialogue_speaker"),
      continuityDisposition: correction("continuity_disposition")
    },
    gates,
    restart: {
      runPersisted:
        run.runId === restored.run.runId &&
        run.runFingerprint === restored.run.runFingerprint,
      snapshotPersisted:
        machineSnapshot.snapshotId === restoredSnapshot.snapshotId &&
        machineSnapshot.snapshotFingerprint ===
          restoredSnapshot.snapshotFingerprint,
      correctionSetPersisted:
        machineSnapshot.correctionSetFingerprint ===
          restoredSnapshot.correctionSetFingerprint &&
        before.corrections.length === restored.corrections.length,
      gateDecisionsPersisted: gates.every(
        (gate) =>
          JSON.stringify(gate.beforeRestart) ===
          JSON.stringify(gate.afterRestart)
      ),
      agentExecutionsPersisted:
        JSON.stringify(agents) ===
        JSON.stringify(agentEvidence(restored))
    }
  };
}

export function expectPhase2RestartPersistence(
  evidence: PackagedStoryAnalysisEvidence
): void {
  expect(Object.values(evidence.assertions).every(Boolean)).toBe(true);
  expect(
    Object.values(evidence.corrections).every(
      (correction) =>
        correction.persistedAfterRestart &&
        correction.immutable &&
        correction.lockedAgainstAutomation &&
        correction.effectiveAuthorityAfterRestart === "human"
    )
  ).toBe(true);
  expect(evidence.corrections.characterIdentity.reasonFingerprint).toBe(
    packagedCorrectionReasonFingerprints.characterIdentity
  );
  expect(evidence.corrections.dialogueSpeaker.reasonFingerprint).toBe(
    packagedCorrectionReasonFingerprints.dialogueSpeaker
  );
  expect(evidence.corrections.continuityDisposition.reasonFingerprint).toBe(
    packagedCorrectionReasonFingerprints.continuityDisposition
  );
  expect(
    evidence.gates.every(
      (gate) =>
        gate.beforeRestart.state === "approved" &&
        gate.beforeRestart.profileFingerprint ===
          evidence.profile.profileFingerprint &&
        gate.beforeRestart.runFingerprint === evidence.run.runFingerprint &&
        gate.beforeRestart.snapshotId === evidence.run.snapshotId &&
        gate.beforeRestart.snapshotRevision === evidence.run.snapshotRevision &&
        gate.beforeRestart.snapshotFingerprint ===
          evidence.run.snapshotFingerprint &&
        /^[a-f0-9]{64}$/u.test(
          gate.beforeRestart.decisionRecordFingerprint
        ) &&
        JSON.stringify(gate.beforeRestart) ===
          JSON.stringify(gate.afterRestart)
    )
  ).toBe(true);
  expect(Object.values(evidence.restart).every(Boolean)).toBe(true);
}

function analysisAssertions(
  snapshot: Phase2RuntimeSnapshot
): PackagedAnalysisAssertionsEvidence {
  const entities = snapshot.entities;
  return {
    structureDetected:
      entities.chapters.length > 0 && entities.scenes.length > 0,
    characterRegistryDetected: entities.characters.length > 0,
    ambiguousIdentityPreserved:
      entities.characters.some(
        (entity) =>
          value(entity, "identityStatus") === "ambiguous" ||
          value(entity, "identityStatus") === "unresolved"
      ) ||
      entities.mentions.some(
        (entity) =>
          value(entity, "resolution") === "ambiguous" ||
          value(entity, "resolution") === "unresolved"
      ),
    ambiguousDialoguePreserved: entities["dialogue-lines"].some(
      (entity) => {
        const attribution = objectValue(entity, "effectiveAttribution");
        return (
          attribution.requiresHumanReview === true ||
          attribution.authority === "unresolved"
        );
      }
    ),
    narrationDistinctionDetected:
      entities["narration-spans"].length > 0,
    povShiftDetected: entities["pov-segments"].some(
      (entity) => value(entity, "shiftKind") !== "initial"
    ),
    locationsDetected: entities.locations.length > 0,
    timelineFlashbackDetected: entities["timeline-events"].some(
      (entity) => value(entity, "kind") === "flashback"
    ),
    relationshipChangeDetected: entities.relationships.some(
      (entity) => value(entity, "change") !== "unchanged"
    ),
    emotionalProgressionDetected: entities["emotional-states"].some(
      (entity) =>
        value(entity, "progression") !== "initial" &&
        value(entity, "progression") !== "stable"
    ),
    continuityAnomalyDetected: entities["continuity-findings"].length > 0
  };
}

function agentEvidence(snapshot: Phase2RuntimeSnapshot) {
  const executions = [...snapshot.entities["agent-executions"]].sort(
    (left, right) =>
      numberValue(left, "ordinal") - numberValue(right, "ordinal")
  );
  if (executions.length !== PHASE_2_RUNTIME_AGENTS.length) {
    throw new Error("The complete Phase 2 agent ledger was unavailable.");
  }
  return executions.map((execution, index) => {
    const expected = PHASE_2_RUNTIME_AGENTS[index];
    const agentId = stringValue(execution, "agentId");
    const agentVersion = stringValue(execution, "agentVersion");
    const status = stringValue(execution, "status");
    if (
      expected === undefined ||
      agentId !== expected.agentId ||
      agentVersion !== expected.version ||
      status !== "succeeded"
    ) {
      throw new Error("The Phase 2 agent ledger identity is invalid.");
    }
    return {
      agentId,
      agentVersion: "1.0.0" as const,
      executionId: stringValue(execution, "executionId"),
      status: "succeeded" as const,
      outputFingerprint: stringValue(execution, "outputFingerprint")
    };
  });
}

function observedStages(events: readonly JobEvent[]) {
  const observed: string[] = [];
  for (const event of [...events].sort(
    (left, right) => left.sequence - right.sequence
  )) {
    if (
      event.stage !== undefined &&
      (ANALYSIS_JOB_STAGES as readonly string[]).includes(event.stage) &&
      observed.at(-1) !== event.stage
    ) {
      observed.push(event.stage);
    }
  }
  return observed as readonly (typeof ANALYSIS_JOB_STAGES)[number][];
}

function requiredAnalysisSnapshot(run: StoryAnalysisRun) {
  if (run.currentSnapshot === null) {
    throw new Error("The immutable analysis snapshot was unavailable.");
  }
  return run.currentSnapshot;
}

function requiredCorrection<
  TCategory extends
    | "character_identity"
    | "dialogue_speaker"
    | "continuity_disposition"
>(
  snapshot: Phase2RuntimeSnapshot,
  category: TCategory,
  reason: string
): AnalysisCorrection {
  const matches = snapshot.corrections.filter(
    (correction) =>
      correction.category === category && correction.reason === reason
  );
  if (matches.length !== 1) {
    throw new Error(`Expected one durable ${category} correction.`);
  }
  const correction = matches[0];
  if (correction === undefined) {
    throw new Error(`Expected one durable ${category} correction.`);
  }
  return correction;
}

function requiredCorrectionEntity(
  snapshot: Phase2RuntimeSnapshot,
  correction: AnalysisCorrection
) {
  const entity = snapshot.entities[correction.targetCollection].find(
    (candidate) =>
      "entityId" in candidate &&
      candidate.entityId === correction.targetEntityId
  );
  if (entity === undefined || !("effectiveValueFingerprint" in entity)) {
    throw new Error("The corrected effective entity was unavailable.");
  }
  return entity;
}

function requireHumanAuthority(entity: {
  readonly effectiveAuthority: "runtime_agent" | "human";
}): "human" {
  if (entity.effectiveAuthority !== "human") {
    throw new Error("The human correction lost effective authority.");
  }
  return "human";
}

function requiredReview(
  snapshot: Phase2RuntimeSnapshot,
  gateId: (typeof ANALYSIS_GATE_IDS)[number]
): AnalysisGateReview {
  const review = snapshot.reviews.find((item) => item.gateId === gateId);
  if (
    review === undefined ||
    review.state !== "approved" ||
    review.latestDecisionId === undefined ||
    review.latestDecision === null ||
    review.latestDecision.decisionId !== review.latestDecisionId
  ) {
    throw new Error(`The ${gateId} approval was unavailable.`);
  }
  return review;
}

function gateState(
  review: AnalysisGateReview
): PackagedAnalysisGateStateEvidence {
  if (
    review.state !== "approved" ||
    review.latestDecisionId === undefined ||
    review.latestDecision === null ||
    review.latestDecision.decisionId !== review.latestDecisionId ||
    review.latestDecision.immutable !== true
  ) {
    throw new Error("The immutable approved gate state was unavailable.");
  }
  return {
    reviewId: review.reviewId,
    decisionId: review.latestDecisionId,
    state: "approved",
    profileFingerprint: review.evidence.profileFingerprint,
    runFingerprint: review.evidence.runFingerprint,
    snapshotId: review.snapshotId,
    snapshotRevision: review.evidence.snapshotRevision,
    snapshotFingerprint: review.evidence.snapshotFingerprint,
    decisionRecordFingerprint: createHash("sha256")
      .update(canonicalJson(review.latestDecision), "utf8")
      .digest("hex"),
    artifactFingerprint: review.artifactFingerprint,
    evidenceFingerprint: review.evidenceFingerprint
  };
}

function canonicalJson(value: unknown): string {
  const serialized = JSON.stringify(canonicalize(value));
  if (serialized === undefined) {
    throw new Error("The immutable decision record was not serializable.");
  }
  return serialized;
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => canonicalize(item));
  }
  if (
    value !== null &&
    typeof value === "object"
  ) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, item]) => item !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)])
    );
  }
  return value;
}

function requireSucceeded(run: StoryAnalysisRun): "succeeded" {
  if (run.status !== "succeeded") {
    throw new Error("The Phase 2 run did not succeed.");
  }
  if (
    run.profile.profileId !== WHOLE_BOOK_ANALYSIS_PROFILE_ID ||
    run.profile.semanticVersion !== WHOLE_BOOK_ANALYSIS_PROFILE_VERSION ||
    run.profile.fingerprint !== WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT ||
    run.producer.producerId !== WHOLE_BOOK_ANALYSIS_PRODUCER_ID ||
    run.producer.producerVersion !==
      WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION
  ) {
    throw new Error("The Phase 2 run profile or producer is invalid.");
  }
  return "succeeded";
}

async function approveGate(page: Page, title: string): Promise<void> {
  const card = page.locator(".analysis-gate").filter({
    has: page.getByRole("heading", { name: title })
  });
  const warning = card.locator('input[type="checkbox"]');
  if ((await warning.count()) > 0 && !(await warning.isChecked())) {
    await warning.check();
  }
  await card
    .getByLabel(`${title} rationale`)
    .fill(`Phase 2 E2E approval for ${title}.`);
  const approve = card.getByRole("button", {
    name: `Approve ${title}`
  });
  await expect(approve).toBeEnabled({ timeout: 30_000 });
  await approve.click();
  await expect(card.locator("header strong")).toHaveText("Approved", {
    timeout: 30_000
  });
  await dismissNotice(page);
}

async function expectHumanCorrectionNotice(page: Page): Promise<void> {
  await expect(
    page.getByText(/Human correction saved; \d+ review gate/u)
  ).toBeVisible({ timeout: 30_000 });
  await dismissNotice(page);
}

async function dismissNotice(page: Page): Promise<void> {
  const dismiss = page.getByRole("button", {
    name: "Dismiss notification"
  });
  if ((await dismiss.count()) > 0 && (await dismiss.first().isVisible())) {
    await dismiss.first().click();
  }
}

function gateTitle(
  gateId: (typeof ANALYSIS_GATE_IDS)[number]
): string {
  switch (gateId) {
    case "story_structure_review":
      return "Story structure review";
    case "character_registry_review":
      return "Character registry review";
    case "dialogue_attribution_review":
      return "Dialogue attribution review";
    case "whole_book_analysis_review":
      return "Whole-book analysis review";
  }
}

function gateFlowStep(
  gateId: (typeof ANALYSIS_GATE_IDS)[number]
): string {
  return `approve_${gateId}`;
}

function equalStrings(
  left: readonly string[],
  right: readonly string[]
): boolean {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

function value(entity: AnalysisEntity, key: string): unknown {
  return (entity as unknown as Record<string, unknown>)[key];
}

function stringValue(entity: AnalysisEntity, key: string): string {
  const item = value(entity, key);
  if (typeof item !== "string") {
    throw new Error(`The ${key} evidence value was unavailable.`);
  }
  return item;
}

function numberValue(entity: AnalysisEntity, key: string): number {
  const item = value(entity, key);
  if (typeof item !== "number") {
    throw new Error(`The ${key} evidence value was unavailable.`);
  }
  return item;
}

function objectValue(
  entity: AnalysisEntity,
  key: string
): Record<string, unknown> {
  const item = value(entity, key);
  if (
    item === null ||
    typeof item !== "object" ||
    Array.isArray(item)
  ) {
    throw new Error(`The ${key} evidence value was unavailable.`);
  }
  return item as Record<string, unknown>;
}
