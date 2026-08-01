import { expect, type Locator, type Page } from "@playwright/test";

import {
  CASTING_GATE_IDS,
  CASTING_JOB_STAGES,
  GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
  GOVERNED_VOICE_CASTING_PROFILE_ID,
  VOICE_CASTING_PRODUCER_ID,
  VOICE_RIGHTS_POLICY_ID,
  type CastAssignment,
  type CastingCandidate,
  type CastingConflict,
  type CastingCorrection,
  type CastingGateId,
  type CastingGateReview,
  type CastingRun,
  type CastingVoiceProfile,
  type JobEvent,
  type ProductionRole,
  type VoiceCatalogRevision,
  type VoiceModelDescriptor,
  type VoiceProviderDescriptor,
  type VoiceRightsRecord
} from "@cinematic-story-studio/contracts";

import type { VoiceCatalogResponse } from "../../src/shared/casting-api";
import type { PackagedStoryAnalysisEvidence } from "../../src/verification/packaged-e2e-evidence";
import {
  createPhase3PackagedFlowRecorder,
  governedVoiceCastingProfileFingerprint,
  governedVoiceCastingProfileId,
  type Phase3Assertions,
  type Phase3CastingProof,
  type Phase3PackagedE2eResult,
  type Phase3PackagedFlowRecorder,
  type Phase3RoleVoiceAssignmentEvidence,
  type Phase3VoiceCastingEvidence
} from "../../src/verification/phase3-packaged-e2e-evidence";
import type { Phase2RuntimeSnapshot } from "./phase2-story-analysis";

const narratorVoiceProfileId = "synthetic-narrator-02";
const firstCharacterVoiceProfileId = "synthetic-character-01";
const secondCharacterVoiceProfileId = "synthetic-character-02";
const prohibitedVoiceProfileId = "synthetic-character-07";
const unknownVoiceProfileId = "synthetic-character-08";
const snapshotReadRetryLimit = 5;
const snapshotReadRetryDelayMs = 250;
const retryableProjectContextCodes = Object.freeze([
  "PROJECT_CONTEXT_CHANGED",
  "PROJECT_CONTEXT_MISMATCH"
]);

export interface Phase3RuntimeSnapshot {
  readonly catalog: VoiceCatalogResponse;
  readonly run: CastingRun;
  readonly events: readonly JobEvent[];
  readonly roles: readonly ProductionRole[];
  readonly candidates: Readonly<
    Record<string, readonly CastingCandidate[]>
  >;
  readonly conflicts: readonly CastingConflict[];
  readonly assignments: readonly CastAssignment[];
  readonly corrections: readonly CastingCorrection[];
  readonly reviews: readonly CastingGateReview[];
}

export interface Phase3WorkflowEvidence {
  readonly governed: Phase3RuntimeSnapshot;
  readonly flowRecorder: Phase3PackagedFlowRecorder;
  readonly ineligibleRightsRejections: readonly [
    "prohibited",
    "unknown"
  ];
}

export interface Phase3PersistenceEvidence {
  readonly casting: Phase3CastingProof;
  readonly assertions: Phase3Assertions;
  readonly voiceCasting: Omit<
    Phase3VoiceCastingEvidence,
    "packagedE2e"
  >;
}

type SnapshotReadAttempt =
  | {
      readonly outcome: "project_context_unavailable";
    }
  | {
      readonly outcome: "succeeded";
      readonly snapshot: Phase3RuntimeSnapshot;
    };

export async function runPhase3GovernanceWorkflow(
  page: Page,
  phase2: Phase2RuntimeSnapshot
): Promise<Phase3WorkflowEvidence> {
  const flowRecorder = createPhase3PackagedFlowRecorder();
  assertCurrentPhase2Prerequisites(phase2);
  flowRecorder.record("create_project");
  flowRecorder.record("import_synthetic_docx");
  flowRecorder.record("wait_for_extraction");
  flowRecorder.record("approve_import_review");
  flowRecorder.record("complete_phase_2_analysis");
  flowRecorder.record("verify_four_phase_2_approvals");

  await page.getByRole("button", { name: "Casting", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Casting workspace", exact: true })
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByRole("region", { name: "Phase 2 prerequisites" })
  ).toContainText("Current and approved");
  await expect(page.locator(".prerequisite-card")).toHaveCount(5);
  flowRecorder.record("open_casting_workspace");

  const initialCatalog = await readCatalog(page);
  expect(initialCatalog.catalogRevision.catalogRevisionId).toBe(
    "synthetic-voice-catalog-v1@1.0.0"
  );
  expect(initialCatalog.catalogRevision.catalogFingerprint).toMatch(
    /^[a-f0-9]{64}$/u
  );
  expect(initialCatalog.items).toHaveLength(14);
  expect(initialCatalog.providers.length).toBeGreaterThanOrEqual(2);
  expect(initialCatalog.models.length).toBeGreaterThanOrEqual(5);
  await expect(
    page.getByText(GOVERNED_VOICE_CASTING_PROFILE_ID, { exact: true })
  ).toBeVisible();
  flowRecorder.record("load_synthetic_voice_catalog");

  const start = page.getByRole("button", {
    name: "Start casting analysis",
    exact: true
  });
  await expect(start).toBeEnabled({ timeout: 30_000 });
  await start.click();
  await waitForCastingPublication(page);
  await dismissNotice(page);

  let snapshot = await readPhase3RuntimeSnapshot(page);
  expect(snapshot.run.status).toBe("succeeded");
  expect(snapshot.run.profile).toEqual({
    profileId: GOVERNED_VOICE_CASTING_PROFILE_ID,
    fingerprint: GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
  });
  expect(snapshot.run.producerId).toBe(VOICE_CASTING_PRODUCER_ID);
  expect(snapshot.run.catalogRevisionId).toBe(
    snapshot.catalog.catalogRevision.catalogRevisionId
  );
  expect(snapshot.run.catalogFingerprint).toBe(
    snapshot.catalog.catalogRevision.catalogFingerprint
  );
  expect(snapshot.roles.some(isNarratorRole)).toBe(true);
  expect(
    snapshot.roles.filter((role) => role.roleType === "named_character")
      .length
  ).toBeGreaterThanOrEqual(2);
  flowRecorder.record("create_production_roles");
  expect(observedCastingStages(snapshot.events)).toEqual(
    CASTING_JOB_STAGES
  );
  expect(
    Object.values(snapshot.candidates).every(
      (items) => items.length > 0 && items.length <= 12
    )
  ).toBe(true);
  flowRecorder.record("run_casting_analysis");

  const narrator = requiredRole(
    snapshot.roles,
    (role) => role.roleType === "primary_narrator",
    "primary narrator"
  );
  await loadCandidatesForRole(page, narrator);
  await expect(page.locator(".candidate-card")).toHaveCount(
    snapshot.candidates[narrator.roleId]?.length ?? 0
  );
  expect(
    snapshot.candidates[narrator.roleId]?.filter(
      (candidate) =>
        candidate.assessment.compatibilityStatus !== "incompatible"
    ).length
  ).toBeGreaterThanOrEqual(2);
  flowRecorder.record("inspect_narrator_candidates");

  await ensureLanguageRequirement(page, "en");
  await selectVoice(page, snapshot.catalog, narratorVoiceProfileId);
  flowRecorder.record("select_narrator_voice");
  await lockSelectedAssignment(page);
  flowRecorder.record("lock_narrator_assignment");

  snapshot = await readPhase3RuntimeSnapshot(page);
  const characterRoles = snapshot.roles.filter(
    (role) => role.roleType === "named_character"
  );
  const firstCharacter = requiredItem(
    characterRoles[0],
    "first named-character role"
  );
  const secondCharacter = requiredItem(
    characterRoles[1],
    "second named-character role"
  );
  await loadCandidatesForRole(page, firstCharacter);
  expect(
    snapshot.candidates[firstCharacter.roleId]?.length
  ).toBeGreaterThanOrEqual(12);
  flowRecorder.record("inspect_character_candidates");
  await ensureLanguageRequirement(page, "en");
  await selectVoice(
    page,
    snapshot.catalog,
    firstCharacterVoiceProfileId
  );
  flowRecorder.record("select_first_character_voice");
  await lockSelectedAssignment(page);
  flowRecorder.record("lock_first_character_assignment");

  await loadCandidatesForRole(page, secondCharacter);
  await ensureLanguageRequirement(page, "en");
  await selectVoice(
    page,
    snapshot.catalog,
    secondCharacterVoiceProfileId
  );
  flowRecorder.record("select_second_character_voice");
  await lockSelectedAssignment(page);
  flowRecorder.record("lock_second_character_assignment");

  for (let index = 2; index < characterRoles.length; index += 1) {
    const role = characterRoles[index];
    if (role === undefined) {
      continue;
    }
    await loadCandidatesForRole(page, role);
    await ensureLanguageRequirement(page, "en");
    await selectVoice(
      page,
      snapshot.catalog,
      index % 2 === 0
        ? firstCharacterVoiceProfileId
        : secondCharacterVoiceProfileId
    );
    await lockSelectedAssignment(page);
  }

  const nonRequiredRoles = snapshot.roles.filter(
    (role) =>
      !isNarratorRole(role) && role.roleType !== "named_character"
  );
  for (const role of nonRequiredRoles) {
    await selectRole(page, role);
    await page
      .getByRole("button", {
        name: "Mark intentionally uncast",
        exact: true
      })
      .click();
    await expectCorrectionNotice(page, "Mark Intentionally Uncast");
  }

  snapshot = await readPhase3RuntimeSnapshot(page);
  const metadataConflictIndex = snapshot.conflicts.findIndex(
    (conflict) =>
      conflict.category === "metadata_similarity_risk" &&
      conflict.resolutionState === "open"
  );
  const metadataConflict = requiredItem(
    snapshot.conflicts[metadataConflictIndex],
    "metadata-similarity conflict"
  );
  const conflictRole = requiredItem(
    snapshot.roles.find((role) =>
      metadataConflict.roleIds.includes(role.roleId)
    ),
    "metadata-conflict role"
  );
  await selectRole(page, conflictRole);
  const conflictRow = await loadConflictRow(
    page,
    metadataConflict.conflictId,
    "Metadata Similarity Risk",
    Math.floor(metadataConflictIndex / 50)
  );
  await expect(conflictRow).toContainText("metadata");
  await expect(conflictRow).toContainText("no acoustic claim");
  flowRecorder.record("surface_metadata_differentiation_conflict");
  await conflictRow
    .getByRole("button", { name: "Approve planned reuse", exact: true })
    .click();
  await expectCorrectionNotice(page, "Approve Voice Reuse");
  snapshot = await readPhase3RuntimeSnapshot(page);
  const disposedConflict = requiredItem(
    snapshot.conflicts.find(
      (conflict) => conflict.conflictId === metadataConflict.conflictId
    ),
    "disposed metadata conflict"
  );
  expect(disposedConflict.resolutionState).toBe("approved_reuse");
  expect(disposedConflict.dispositionCorrectionId).toMatch(
    /^[A-Za-z0-9][A-Za-z0-9@._:-]{0,199}$/u
  );
  flowRecorder.record("disposition_casting_conflict");

  await loadCandidatesForRole(page, secondCharacter);
  const prohibitedCard = candidateCard(
    page,
    snapshot.catalog,
    prohibitedVoiceProfileId
  );
  const unknownCard = candidateCard(
    page,
    snapshot.catalog,
    unknownVoiceProfileId
  );
  await expect(prohibitedCard).toContainText("Rights Prohibited");
  await expect(unknownCard).toContainText("Rights Unknown");
  flowRecorder.record("surface_restricted_or_ineligible_rights");

  await prohibitedCard
    .getByRole("button", { name: "Select voice", exact: true })
    .click();
  await expectCorrectionNotice(page, "Select Voice");
  await expect(page.locator(".final-blockers")).toContainText(
    "unknown/prohibited rights"
  );
  await expectCastingGateApprovalRejected(
    page,
    "character_casting_review",
    secondCharacter.roleId,
    prohibitedVoiceProfileId,
    "prohibited"
  );
  await expectCastingGateApprovalRejected(
    page,
    "complete_cast_review",
    secondCharacter.roleId,
    prohibitedVoiceProfileId,
    "prohibited"
  );

  await loadCandidatesForRole(page, secondCharacter);
  await candidateCard(page, snapshot.catalog, unknownVoiceProfileId)
    .getByRole("button", { name: "Select voice", exact: true })
    .click();
  await expectCorrectionNotice(page, "Select Voice");
  await expect(page.locator(".final-blockers")).toContainText(
    "unknown/prohibited rights"
  );
  await expectCastingGateApprovalRejected(
    page,
    "character_casting_review",
    secondCharacter.roleId,
    unknownVoiceProfileId,
    "unknown"
  );
  await expectCastingGateApprovalRejected(
    page,
    "complete_cast_review",
    secondCharacter.roleId,
    unknownVoiceProfileId,
    "unknown"
  );
  flowRecorder.record("reject_ineligible_final_approval");

  await loadCandidatesForRole(page, secondCharacter);
  await selectVoice(
    page,
    snapshot.catalog,
    secondCharacterVoiceProfileId
  );
  await lockSelectedAssignment(page);

  snapshot = await readPhase3RuntimeSnapshot(page);
  const currentNarrator = requiredRole(
    snapshot.roles,
    (role) => role.roleType === "primary_narrator",
    "current primary narrator"
  );
  await selectRole(page, currentNarrator);
  await expect(page.locator(".effective-assignment")).toContainText(
    "Restricted rights"
  );
  const acknowledgeRestricted = page.getByRole("button", {
    name: "Acknowledge restricted rights",
    exact: true
  });
  await expect(acknowledgeRestricted).toBeVisible();
  await acknowledgeRestricted.click();
  await expectCorrectionNotice(page, "Acknowledge Restricted Rights");

  snapshot = await readPhase3RuntimeSnapshot(page);
  assertFinalAssignments(snapshot);
  for (const gateId of CASTING_GATE_IDS) {
    await approveCastingGate(
      page,
      gateId,
      snapshot.conflicts.length
    );
    flowRecorder.record(gateFlowOperation(gateId));
  }

  const governed = await readPhase3RuntimeSnapshot(page);
  assertGovernedCasting(governed);
  return {
    governed,
    flowRecorder,
    ineligibleRightsRejections: ["prohibited", "unknown"]
  };
}

export async function readPhase3RuntimeSnapshot(
  page: Page
): Promise<Phase3RuntimeSnapshot> {
  for (let attempt = 1; attempt <= snapshotReadRetryLimit; attempt += 1) {
    const result = await readPhase3RuntimeSnapshotAttempt(page);
    if (result.outcome === "succeeded") {
      return result.snapshot;
    }
    if (attempt < snapshotReadRetryLimit) {
      await page.waitForTimeout(snapshotReadRetryDelayMs * attempt);
    }
  }
  throw new Error(
    "The Phase 3A project context was unavailable during all bounded evidence reads."
  );
}

export function expectPhase3RestartPersistence(
  workflow: Phase3WorkflowEvidence,
  restored: Phase3RuntimeSnapshot
): void {
  const before = workflow.governed;
  expect(restored.run.castingRunId).toBe(before.run.castingRunId);
  expect(restored.run.outputFingerprint).toBe(
    before.run.outputFingerprint
  );
  expect(restored.run.effectiveCorrectionSetFingerprint).toBe(
    before.run.effectiveCorrectionSetFingerprint
  );
  expect(restored.run.approvedCastSnapshot).toEqual(
    before.run.approvedCastSnapshot
  );
  expect(catalogIdentity(restored.catalog)).toEqual(
    catalogIdentity(before.catalog)
  );
  expect(roleIdentity(restored.roles)).toEqual(
    roleIdentity(before.roles)
  );
  expect(candidateIdentity(restored.candidates)).toEqual(
    candidateIdentity(before.candidates)
  );
  expect(effectiveAssignmentIdentity(restored.assignments)).toEqual(
    effectiveAssignmentIdentity(before.assignments)
  );
  expect(restored.corrections).toEqual(before.corrections);
  expect(conflictIdentity(restored.conflicts)).toEqual(
    conflictIdentity(before.conflicts)
  );
  expect(reviewIdentity(restored.reviews)).toEqual(
    reviewIdentity(before.reviews)
  );
  assertGovernedCasting(restored);
}

export function buildPhase3PersistenceEvidence(
  workflow: Phase3WorkflowEvidence,
  restored: Phase3RuntimeSnapshot,
  phase2: PackagedStoryAnalysisEvidence
): Phase3PersistenceEvidence {
  expectPhase3RestartPersistence(workflow, restored);
  const snapshot = requiredItem(
    restored.run.approvedCastSnapshot,
    "approved cast snapshot"
  );
  const summary = snapshot.counts;
  const effectiveAssignments = restored.assignments.filter(
    (assignment) => assignment.effective
  );
  const activeConflicts = restored.conflicts.filter(
    (conflict) => conflict.resolutionState !== "superseded"
  );
  const rolesById = new Map(
    restored.roles.map((role) => [role.roleId, role])
  );
  expect([...snapshot.assignmentIds].sort()).toEqual(
    effectiveAssignments
      .map((assignment) => assignment.assignmentId)
      .sort()
  );
  const intentionallyUncastRoleIds = restored.roles
    .filter((role) => role.status === "intentionally_uncast")
    .map((role) => role.roleId);
  expect([...snapshot.intentionallyUncastRoleIds].sort()).toEqual(
    intentionallyUncastRoleIds.sort()
  );
  expect(summary).toEqual({
    productionRoles: restored.roles.length,
    narratorRoles: restored.roles.filter(isNarratorRole).length,
    characterRoles: restored.roles.filter(
      (role) => !isNarratorRole(role)
    ).length,
    preReductionCandidates:
      restored.roles.length * Math.min(50, restored.catalog.total),
    finalCandidates: Object.values(restored.candidates).reduce(
      (total, candidates) => total + candidates.length,
      0
    ),
    conflicts: activeConflicts.length,
    assignments: effectiveAssignments.length,
    corrections: restored.corrections.length
  });
  const narratorAssignment = requiredItem(
    effectiveAssignments.find((assignment) => {
      const role = rolesById.get(assignment.roleId);
      return role !== undefined && isNarratorRole(role);
    }),
    "effective narrator assignment"
  );
  const characterAssignments = effectiveAssignments.filter(
    (assignment) => {
      const role = rolesById.get(assignment.roleId);
      return role !== undefined && !isNarratorRole(role);
    }
  );
  if (characterAssignments.length < 2) {
    throw new Error(
      "At least two effective character assignments are required."
    );
  }
  const narratorAssignmentEvidence = phase3RoleVoiceAssignmentEvidence(
    narratorAssignment,
    rolesById
  );
  const characterAssignmentEvidence = characterAssignments.map(
    (assignment) =>
      phase3RoleVoiceAssignmentEvidence(assignment, rolesById)
  );
  const metadataConflict = requiredItem(
    activeConflicts.find(
      (conflict) =>
        conflict.category === "metadata_similarity_risk" &&
        conflict.resolutionState === "approved_reuse" &&
        conflict.dispositionCorrectionId !== null
    ),
    "persisted metadata conflict disposition"
  );
  const conflictCorrection = requiredItem(
    restored.corrections.find(
      (correction) =>
        correction.category === "approve_voice_reuse" &&
        correction.correctedValue.conflictId ===
          metadataConflict.conflictId
    ),
    "persisted voice-reuse correction"
  );
  const restrictedCorrection = requiredItem(
    restored.corrections.find(
      (correction) =>
        correction.category === "acknowledge_restricted_rights" &&
        correction.targetRoleId === narratorAssignment.roleId &&
        correction.correctedValue.rightsRecordId ===
          narratorAssignment.rightsRecordId &&
        correction.correctedValue.rightsRecordRevision ===
          narratorAssignment.rightsRecordRevision
    ),
    "persisted restricted-rights acknowledgement"
  );
  const restrictedRightsWarningId =
    `restricted-rights:${narratorAssignment.roleId}:${narratorAssignment.rightsRecordId}`;
  const narratorReview = requiredReview(
    restored.reviews,
    "narrator_casting_review"
  );
  expect(narratorReview.openWarningIds).toContain(
    restrictedRightsWarningId
  );
  expect(
    narratorReview.latestDecision?.acknowledgedWarningIds
  ).toContain(restrictedRightsWarningId);
  const gateDecisionIds = CASTING_GATE_IDS.map((gateId) => {
    const review = requiredReview(restored.reviews, gateId);
    return requiredItem(
      review.latestDecision?.decisionId,
      `${gateId} decision`
    );
  });
  const phase2GateDecisionIds = {
    storyStructureReview: requiredPhase2GateDecisionId(
      phase2,
      "story_structure_review"
    ),
    characterRegistryReview: requiredPhase2GateDecisionId(
      phase2,
      "character_registry_review"
    ),
    dialogueAttributionReview: requiredPhase2GateDecisionId(
      phase2,
      "dialogue_attribution_review"
    ),
    wholeBookAnalysisReview: requiredPhase2GateDecisionId(
      phase2,
      "whole_book_analysis_review"
    )
  };
  const assertionChecks: Record<keyof Phase3Assertions, boolean> = {
    phase2PrerequisitesCurrent:
      restored.run.prerequisites.sourceDocumentId ===
        phase2.approvedInput.sourceDocumentId &&
      restored.run.prerequisites.sourceRevision ===
        phase2.approvedInput.sourceRevision &&
      restored.run.prerequisites.extractionId ===
        phase2.approvedInput.extractionId &&
      restored.run.prerequisites.extractionRevision ===
        phase2.approvedInput.extractionRevision &&
      restored.run.prerequisites.extractedTextSha256 ===
        phase2.approvedInput.extractedTextSha256 &&
      restored.run.prerequisites.importReviewDecisionId ===
        phase2.approvedInput.importReviewDecisionId &&
      restored.run.prerequisites.analysisRunId === phase2.run.runId &&
      restored.run.prerequisites.analysisSnapshotId ===
        phase2.run.snapshotId &&
      restored.run.prerequisites.analysisSnapshotRevision ===
        phase2.run.snapshotRevision &&
      restored.run.prerequisites.analysisSnapshotFingerprint ===
        phase2.run.snapshotFingerprint &&
      restored.run.prerequisites.analysisCorrectionSetFingerprint ===
        phase2.run.correctionSetFingerprint &&
      restored.run.prerequisites.phase2GateDecisionIds
        .storyStructureReview ===
        phase2GateDecisionIds.storyStructureReview &&
      restored.run.prerequisites.phase2GateDecisionIds
        .characterRegistryReview ===
        phase2GateDecisionIds.characterRegistryReview &&
      restored.run.prerequisites.phase2GateDecisionIds
        .dialogueAttributionReview ===
        phase2GateDecisionIds.dialogueAttributionReview &&
      restored.run.prerequisites.phase2GateDecisionIds
        .wholeBookAnalysisReview ===
        phase2GateDecisionIds.wholeBookAnalysisReview,
    castingProfilePinned:
      restored.run.profile.profileId === governedVoiceCastingProfileId &&
      restored.run.profile.fingerprint ===
        governedVoiceCastingProfileFingerprint,
    catalogFingerprintVerified:
      restored.run.catalogFingerprint ===
        restored.catalog.catalogRevision.catalogFingerprint &&
      restored.catalog.catalogRevision.rightsPolicyId ===
        VOICE_RIGHTS_POLICY_ID,
    rolesCreated:
      summary.productionRoles === restored.roles.length &&
      summary.narratorRoles >= 1 &&
      summary.characterRoles >= 2,
    boundedCandidatesCreated:
      summary.preReductionCandidates >= summary.finalCandidates &&
      summary.finalCandidates <= summary.productionRoles * 12 &&
      Object.values(restored.candidates).every(
        (candidates) => candidates.length > 0 && candidates.length <= 12
      ),
    metadataConflictProven:
      metadataConflict.metadataOnly &&
      !metadataConflict.acousticSimilarityClaimed,
    rightsGovernanceProven:
      narratorAssignment.rightsState === "restricted" &&
      workflow.ineligibleRightsRejections.join(",") ===
        "prohibited,unknown" &&
      restrictedCorrection.immutable,
    humanAssignmentsLocked:
      effectiveAssignments.length >= 3 &&
      effectiveAssignments.every(
        (assignment) => assignment.authority === "human_locked"
      ),
    threeGateDecisionsPersisted:
      gateDecisionIds.length === 3 &&
      restored.reviews.every(
        (review) =>
          review.state === "approved" &&
          review.latestDecision?.immutable === true
      ),
    restartPersistenceProven: true,
    processOwnershipExitProven: true
  };
  if (!Object.values(assertionChecks).every(Boolean)) {
    throw new Error(
      "The Phase 3A restart assertions were not all proven."
    );
  }
  const assertions: Phase3Assertions = {
    phase2PrerequisitesCurrent: true,
    castingProfilePinned: true,
    catalogFingerprintVerified: true,
    rolesCreated: true,
    boundedCandidatesCreated: true,
    metadataConflictProven: true,
    rightsGovernanceProven: true,
    humanAssignmentsLocked: true,
    threeGateDecisionsPersisted: true,
    restartPersistenceProven: true,
    processOwnershipExitProven: true
  };
  const casting: Phase3CastingProof = {
    profileId: governedVoiceCastingProfileId,
    profileFingerprint: governedVoiceCastingProfileFingerprint,
    catalogRevisionId:
      restored.catalog.catalogRevision.catalogRevisionId,
    catalogFingerprint:
      restored.catalog.catalogRevision.catalogFingerprint,
    castingRunId: restored.run.castingRunId,
    approvedCastSnapshotId: snapshot.snapshotId,
    approvedCastSnapshotRevision: snapshot.revision,
    narratorAssignmentId: narratorAssignment.assignmentId,
    characterAssignmentIds: characterAssignments.map(
      (assignment) => assignment.assignmentId
    ),
    narratorAssignment: narratorAssignmentEvidence,
    characterAssignments: characterAssignmentEvidence,
    conflictId: metadataConflict.conflictId,
    conflictDispositionCorrectionId:
      conflictCorrection.correctionId,
    restrictedRightsWarningId,
    ineligibleApprovalRejected: true,
    gateDecisionIds: {
      narratorCastingReview: requiredItem(
        gateDecisionIds[0],
        "narrator gate decision"
      ),
      characterCastingReview: requiredItem(
        gateDecisionIds[1],
        "character gate decision"
      ),
      completeCastReview: requiredItem(
        gateDecisionIds[2],
        "complete gate decision"
      )
    },
    restartRestored: true
  };
  const voiceCasting: Omit<
    Phase3VoiceCastingEvidence,
    "packagedE2e"
  > = {
    castingProfile: {
      profileId: governedVoiceCastingProfileId,
      fingerprint: governedVoiceCastingProfileFingerprint,
      producerId: VOICE_CASTING_PRODUCER_ID
    },
    providers:
      restored.catalog.catalogRevision.providerDescriptorIds.map(
        (providerId) => {
          const provider = requiredItem(
            restored.catalog.providers.find(
              (candidate) => candidate.providerId === providerId
            ),
            `provider descriptor ${providerId}`
          );
          return {
            descriptorId: provider.providerId,
            version: provider.providerVersion
          };
        }
      ),
    models: restored.catalog.catalogRevision.modelDescriptorIds.map(
      (modelId) => {
        const model = requiredItem(
          restored.catalog.models.find(
            (candidate) => candidate.modelId === modelId
          ),
          `model descriptor ${modelId}`
        );
        return {
          descriptorId: model.modelId,
          version: model.modelVersion
        };
      }
    ),
    catalogRevision: {
      catalogRevisionId:
        restored.catalog.catalogRevision.catalogRevisionId,
      revision: restored.catalog.catalogRevision.revision,
      fingerprint:
        restored.catalog.catalogRevision.catalogFingerprint
    },
    rightsPolicyId: VOICE_RIGHTS_POLICY_ID,
    phase2Evidence: {
      analysisRunId: phase2.run.runId,
      snapshotId: phase2.run.snapshotId,
      snapshotRevision: phase2.run.snapshotRevision,
      snapshotFingerprint: phase2.run.snapshotFingerprint,
      correctionSetFingerprint: phase2.run.correctionSetFingerprint,
      gateDecisionIds: [
        phase2GateDecisionIds.storyStructureReview,
        phase2GateDecisionIds.characterRegistryReview,
        phase2GateDecisionIds.dialogueAttributionReview,
        phase2GateDecisionIds.wholeBookAnalysisReview
      ]
    },
    counts: summary,
    castingRunId: restored.run.castingRunId,
    approvedCastSnapshot: {
      snapshotId: snapshot.snapshotId,
      revision: snapshot.revision,
      fingerprint: snapshot.snapshotFingerprint
    },
    assignments: {
      narratorAssignmentId: narratorAssignment.assignmentId,
      characterAssignmentIds: characterAssignments.map(
        (assignment) => assignment.assignmentId
      ),
      narratorAssignment: narratorAssignmentEvidence,
      characterAssignments: characterAssignmentEvidence
    },
    rightsEligibility:
      "eligible_after_required_acknowledgements",
    correctionPersistence: true,
    conflictDispositionPersistence: true,
    gateDecisions: gateDecisionIds,
    restartPersistence: true,
    assertions
  };
  return { casting, assertions, voiceCasting };
}

export function buildPhase3VoiceCastingEvidence(
  evidence: Phase3PersistenceEvidence,
  packagedE2e: Phase3PackagedE2eResult
): Phase3VoiceCastingEvidence {
  return {
    ...evidence.voiceCasting,
    packagedE2e
  };
}

async function waitForCastingPublication(page: Page): Promise<void> {
  const timeoutMs = 240_000;
  const publicationUiGraceMs = 15_000;
  const deadline = Date.now() + timeoutMs;
  let backendPublishedAt: number | null = null;
  let explicitRefreshRequested = false;
  let lastDiagnostic: unknown = null;
  const uiState = page.locator(".casting-runbar .run-state");

  while (Date.now() < deadline) {
    if ((await uiState.textContent())?.trim() === "Succeeded") {
      return;
    }
    const diagnostic = await readCastingWaitDiagnostic(page);
    lastDiagnostic = diagnostic.payload;
    if (
      diagnostic.runStatus !== null &&
      ["failed", "cancelled", "interrupted"].includes(
        diagnostic.runStatus
      )
    ) {
      throw new Error(
        `The casting run reached ${diagnostic.runStatus}: ${JSON.stringify(diagnostic.payload)}`
      );
    }
    if (diagnostic.runStatus === "succeeded") {
      backendPublishedAt ??= Date.now();
      if (!explicitRefreshRequested) {
        const refresh = page.getByRole("button", {
          name: "Refresh evidence",
          exact: true
        });
        if (await refresh.isEnabled()) {
          explicitRefreshRequested = true;
          await refresh.click();
        }
      }
      if (Date.now() - backendPublishedAt >= publicationUiGraceMs) {
        throw new Error(
          `The service published casting, but the desktop did not load the published resources: ${JSON.stringify(diagnostic.payload)}`
        );
      }
    }
    await page.waitForTimeout(1_000);
  }
  throw new Error(
    `The casting run did not publish within ${timeoutMs}ms: ${JSON.stringify(lastDiagnostic)}`
  );
}

async function readCastingWaitDiagnostic(page: Page): Promise<{
  readonly runStatus: string | null;
  readonly payload: unknown;
}> {
  const service = await page.evaluate(async () => {
    const projects = await window.cinematicStory.projects.list();
    if (!projects.ok) {
      return {
        runStatus: null,
        discovery: {
          ok: false,
          code: projects.error.code,
          message: projects.error.message
        }
      };
    }
    const project = projects.value.items[0];
    if (project === undefined || projects.value.items.length !== 1) {
      return {
        runStatus: null,
        discovery: {
          ok: false,
          code: "E2E_PROJECT_CARDINALITY",
          projectCount: projects.value.items.length
        }
      };
    }
    const runs = await window.cinematicStory.casting.listRuns({
      projectId: project.projectId,
      limit: 50
    });
    if (!runs.ok) {
      return {
        runStatus: null,
        discovery: {
          ok: false,
          code: runs.error.code,
          message: runs.error.message
        }
      };
    }
    const projectedRun = runs.value.items[0];
    if (projectedRun === undefined) {
      return {
        runStatus: null,
        discovery: {
          ok: false,
          code: "E2E_CASTING_RUN_UNAVAILABLE"
        }
      };
    }
    const detail = await window.cinematicStory.casting.getRun({
      projectId: project.projectId,
      runId: projectedRun.castingRunId
    });
    if (!detail.ok) {
      return {
        runStatus: null,
        discovery: {
          ok: false,
          code: detail.error.code,
          message: detail.error.message
        }
      };
    }
    const run = detail.value.run;
    const events = await window.cinematicStory.jobs.events(run.jobId);
    const eventSummary = events.ok
      ? {
          ok: true,
          count: events.value.events.length,
          events: events.value.events.map((event) => ({
            sequence: event.sequence,
            type: event.type,
            state: event.state,
            stage: event.stage,
            progress: event.progress,
            errorCode: event.error?.code
          }))
        }
      : {
          ok: false,
          code: events.error.code,
          message: events.error.message
        };
    const runSummary = {
      castingRunId: run.castingRunId,
      jobId: run.jobId,
      status: run.status,
      currentStage: run.currentStage,
      progress: run.progress,
      outputFingerprintPresent: run.outputFingerprint !== null,
      approvedCastSnapshotPresent:
        run.approvedCastSnapshot !== null
    };
    if (
      run.status !== "succeeded" ||
      run.outputFingerprint === null ||
      run.approvedCastSnapshot === null
    ) {
      return {
        runStatus: run.status,
        run: runSummary,
        events: eventSummary,
        resources: []
      };
    }
    const evidence = {
      projectId: run.projectId,
      runId: run.castingRunId,
      expectedRunFingerprint: run.outputFingerprint,
      expectedCatalogRevisionId: run.catalogRevisionId,
      expectedCatalogFingerprint: run.catalogFingerprint,
      expectedSnapshotId: run.prerequisites.analysisSnapshotId,
      expectedSnapshotRevision:
        run.prerequisites.analysisSnapshotRevision,
      expectedSnapshotFingerprint:
        run.prerequisites.analysisSnapshotFingerprint
    };
    const [roles, conflicts, assignments, corrections, reviews] =
      await Promise.all([
        window.cinematicStory.casting.listRoles({
          ...evidence,
          limit: 50
        }),
        window.cinematicStory.casting.listConflicts({
          ...evidence,
          limit: 50
        }),
        window.cinematicStory.casting.listAssignments({
          ...evidence,
          limit: 200
        }),
        window.cinematicStory.casting.listCorrections({
          ...evidence,
          limit: 50
        }),
        window.cinematicStory.casting.listReviews({
          ...evidence,
          expectedApprovedCastSnapshotId:
            run.approvedCastSnapshot.snapshotId,
          expectedApprovedCastSnapshotRevision:
            run.approvedCastSnapshot.revision
        })
      ]);
    const resources = [
      ["roles", roles],
      ["conflicts", conflicts],
      ["assignments", assignments],
      ["corrections", corrections],
      ["reviews", reviews]
    ].map(([name, result]) => {
      if (typeof name !== "string" || typeof result === "string") {
        throw new Error("The casting diagnostic result is invalid.");
      }
      if (!result.ok) {
        return {
          name,
          ok: false,
          code: result.error.code,
          message: result.error.message
        };
      }
      return {
        name,
        ok: true,
        count: result.value.items.length
      };
    });
    return {
      runStatus: run.status,
      run: runSummary,
      events: eventSummary,
      resources
    };
  });
  const alerts = (
    await page.locator(".notice.error[role='alert']").allTextContents()
  )
    .map((value) => value.trim().slice(0, 500))
    .filter((value) => value.length > 0);
  return {
    runStatus: service.runStatus,
    payload: {
      ...service,
      alerts
    }
  };
}

async function readCatalog(page: Page): Promise<VoiceCatalogResponse> {
  return page.evaluate(async () => {
    const projects = await window.cinematicStory.projects.list();
    if (!projects.ok) {
      throw new Error(
        `Casting project discovery failed with ${projects.error.code}.`
      );
    }
    const project = projects.value.items[0];
    if (
      project === undefined ||
      projects.value.items.length !== 1 ||
      projects.value.nextCursor !== undefined
    ) {
      throw new Error(
        "The isolated casting workspace did not contain exactly one project."
      );
    }
    const catalog = await window.cinematicStory.casting.getCatalog({
      projectId: project.projectId,
      limit: 50
    });
    if (!catalog.ok) {
      throw new Error(
        `Voice catalog read failed with ${catalog.error.code}: ${catalog.error.message}`
      );
    }
    return catalog.value;
  });
}

async function readPhase3RuntimeSnapshotAttempt(
  page: Page
): Promise<SnapshotReadAttempt> {
  return page.evaluate(async (retryCodes) => {
    const retryableCodes = new Set<string>(retryCodes);
    const projects = await window.cinematicStory.projects.list();
    if (!projects.ok) {
      throw new Error(
        `Casting project discovery failed with ${projects.error.code}.`
      );
    }
    const project = projects.value.items[0];
    if (
      project === undefined ||
      projects.value.items.length !== 1 ||
      projects.value.nextCursor !== undefined
    ) {
      throw new Error(
        "The isolated Phase 3A workspace did not contain exactly one project."
      );
    }
    const runs = await window.cinematicStory.casting.listRuns({
      projectId: project.projectId,
      limit: 50
    });
    if (!runs.ok) {
      if (retryableCodes.has(runs.error.code)) {
        return { outcome: "project_context_unavailable" } as const;
      }
      throw new Error(
        `Casting run discovery failed with ${runs.error.code}.`
      );
    }
    const projectedRun = runs.value.items[0];
    if (
      projectedRun === undefined ||
      runs.value.items.length !== 1 ||
      runs.value.nextCursor !== undefined
    ) {
      throw new Error(
        "The isolated Phase 3A project did not contain exactly one casting run."
      );
    }
    const detail = await window.cinematicStory.casting.getRun({
      projectId: project.projectId,
      runId: projectedRun.castingRunId
    });
    if (!detail.ok) {
      if (retryableCodes.has(detail.error.code)) {
        return { outcome: "project_context_unavailable" } as const;
      }
      throw new Error(
        `Casting run read failed with ${detail.error.code}.`
      );
    }
    const run = detail.value.run;
    if (
      run.status !== "succeeded" ||
      run.outputFingerprint === null ||
      run.approvedCastSnapshot === null
    ) {
      throw new Error(
        "The current Phase 3A run did not publish a reviewable snapshot."
      );
    }
    const evidence = {
      projectId: run.projectId,
      runId: run.castingRunId,
      expectedRunFingerprint: run.outputFingerprint,
      expectedCatalogRevisionId: run.catalogRevisionId,
      expectedCatalogFingerprint: run.catalogFingerprint,
      expectedSnapshotId: run.prerequisites.analysisSnapshotId,
      expectedSnapshotRevision:
        run.prerequisites.analysisSnapshotRevision,
      expectedSnapshotFingerprint:
        run.prerequisites.analysisSnapshotFingerprint
    };
    const [catalog, events, roles, conflicts, assignments, corrections, reviews] =
      await Promise.all([
        window.cinematicStory.casting.getCatalog({
          projectId: run.projectId,
          expectedCatalogRevisionId: run.catalogRevisionId,
          expectedCatalogFingerprint: run.catalogFingerprint,
          limit: 50
        }),
        window.cinematicStory.jobs.events(run.jobId),
        window.cinematicStory.casting.listRoles({
          ...evidence,
          limit: 200
        }),
        window.cinematicStory.casting.listConflicts({
          ...evidence,
          limit: 200
        }),
        window.cinematicStory.casting.listAssignments({
          ...evidence,
          limit: 200
        }),
        window.cinematicStory.casting.listCorrections({
          ...evidence,
          limit: 200
        }),
        window.cinematicStory.casting.listReviews({
          ...evidence,
          expectedApprovedCastSnapshotId:
            run.approvedCastSnapshot.snapshotId,
          expectedApprovedCastSnapshotRevision:
            run.approvedCastSnapshot.revision
        })
      ]);
    const guarded = [
      catalog,
      events,
      roles,
      conflicts,
      assignments,
      corrections,
      reviews
    ];
    if (
      guarded.some(
        (result) =>
          !result.ok && retryableCodes.has(result.error.code)
      )
    ) {
      return { outcome: "project_context_unavailable" } as const;
    }
    if (!catalog.ok) {
      throw new Error(`Catalog read failed with ${catalog.error.code}.`);
    }
    if (!events.ok) {
      throw new Error(`Casting event read failed with ${events.error.code}.`);
    }
    if (!roles.ok) {
      throw new Error(`Role read failed with ${roles.error.code}.`);
    }
    if (!conflicts.ok) {
      throw new Error(
        `Conflict read failed with ${conflicts.error.code}.`
      );
    }
    if (!assignments.ok) {
      throw new Error(
        `Assignment read failed with ${assignments.error.code}.`
      );
    }
    if (!corrections.ok) {
      throw new Error(
        `Correction read failed with ${corrections.error.code}.`
      );
    }
    if (!reviews.ok) {
      throw new Error(`Review read failed with ${reviews.error.code}.`);
    }
    const boundedPages = [
      catalog.value,
      roles.value,
      conflicts.value,
      assignments.value,
      corrections.value
    ];
    if (
      boundedPages.some((value) => value.nextCursor !== undefined)
    ) {
      throw new Error(
        "The synthetic Phase 3A fixture exceeded a bounded evidence page."
      );
    }
    const candidateResults = await Promise.all(
      roles.value.items.map(async (role) => {
        const result =
          await window.cinematicStory.casting.listCandidates({
            ...evidence,
            roleId: role.roleId,
            expectedRoleRevision: role.revision,
            limit: 12
          });
        return [role.roleId, result] as const;
      })
    );
    if (
      candidateResults.some(
        ([, result]) =>
          !result.ok && retryableCodes.has(result.error.code)
      )
    ) {
      return { outcome: "project_context_unavailable" } as const;
    }
    const candidates = Object.fromEntries(
      candidateResults.map(([roleId, result]) => {
        if (!result.ok) {
          throw new Error(
            `Candidate read failed with ${result.error.code}.`
          );
        }
        if (result.value.nextCursor !== undefined) {
          throw new Error(
            "A synthetic candidate page exceeded its governed bound."
          );
        }
        return [roleId, result.value.items] as const;
      })
    );
    return {
      outcome: "succeeded",
      snapshot: {
        catalog: catalog.value,
        run,
        events: events.value.events,
        roles: roles.value.items,
        candidates,
        conflicts: conflicts.value.items,
        assignments: assignments.value.items,
        corrections: corrections.value.items,
        reviews: reviews.value.items
      }
    } as const;
  }, retryableProjectContextCodes);
}

async function expectCastingGateApprovalRejected(
  page: Page,
  gateId: CastingGateId,
  roleId: string,
  voiceProfileId: string,
  rightsState: "prohibited" | "unknown"
): Promise<void> {
  const snapshot = await readPhase3RuntimeSnapshot(page);
  expect(
    snapshot.assignments.find(
      (assignment) =>
        assignment.roleId === roleId && assignment.effective
    )
  ).toMatchObject({
    voiceProfileId,
    rightsState
  });
  const review = requiredReview(snapshot.reviews, gateId);
  const castSnapshot = requiredItem(
    snapshot.run.approvedCastSnapshot,
    "ineligible cast snapshot"
  );
  const result = await page.evaluate(
    async ({ input }) =>
      window.cinematicStory.casting.decideReview(input),
    {
      input: {
        projectId: snapshot.run.projectId,
        runId: snapshot.run.castingRunId,
        gateId,
        decision: "approve" as const,
        expectedRevision: review.revision,
        expectedEvidenceFingerprint:
          review.evidence.evidenceFingerprint,
        expectedRunFingerprint: requiredItem(
          snapshot.run.outputFingerprint,
          "ineligible run fingerprint"
        ),
        expectedApprovedCastSnapshotId: castSnapshot.snapshotId,
        expectedApprovedCastSnapshotRevision: castSnapshot.revision,
        warningAcknowledgementIds: review.openWarningIds,
        rationale:
          "Phase 3A E2E must reject ineligible voice rights.",
        supersedesDecisionId: review.latestDecision?.decisionId ?? null,
        idempotencyKey: crypto.randomUUID()
      }
    }
  );
  expect(result.ok).toBe(false);
  if (result.ok) {
    throw new Error(
      "An ineligible rights state unexpectedly received gate approval."
    );
  }
  expect(result.error.code).toBe("CASTING_REVIEW_NOT_ELIGIBLE");
}

async function approveCastingGate(
  page: Page,
  gateId: CastingGateId,
  expectedConflictTotal: number
): Promise<void> {
  await loadAllConflictPages(page, expectedConflictTotal);
  const title = gateTitle(gateId);
  const card = page.locator(".review-card").filter({
    has: page.getByRole("heading", { name: title, exact: true })
  });
  await expect(card).toBeVisible();
  const warning = card.locator('input[type="checkbox"]');
  if ((await warning.count()) > 0 && !(await warning.isChecked())) {
    await warning.check();
  }
  await card
    .getByLabel("Decision rationale", { exact: true })
    .fill(`Phase 3A E2E approval for ${title}.`);
  const approve = card.getByRole("button", {
    name: "Approve",
    exact: true
  });
  await expect(approve).toBeEnabled({ timeout: 30_000 });
  await approve.click();
  await expect(card.locator(".review-state")).toHaveText("Approved", {
    timeout: 30_000
  });
  await dismissNotice(page);
}

async function loadCandidatesForRole(
  page: Page,
  role: ProductionRole
): Promise<void> {
  await selectRole(page, role);
  await expect(page.locator(".candidate-card").first()).toBeVisible({
    timeout: 30_000
  });
}

async function ensureLanguageRequirement(
  page: Page,
  language: string
): Promise<void> {
  const input = page.getByLabel("Required language", { exact: true });
  await expect(input).toBeVisible();
  if ((await input.inputValue()) === language) {
    return;
  }
  await input.fill(language);
  const save = page.getByRole("button", {
    name: "Save requirement",
    exact: true
  });
  await expect(save).toBeEnabled();
  await save.click();
  await expectCorrectionNotice(page, "Change Casting Requirement");
  const reload = page.getByRole("button", {
    name: "Load candidates",
    exact: true
  });
  await expect(reload).toBeEnabled({ timeout: 30_000 });
  await reload.click();
  await expect(page.locator(".candidate-card").first()).toBeVisible({
    timeout: 30_000
  });
}

async function selectRole(
  page: Page,
  role: ProductionRole
): Promise<void> {
  const button = page.locator(".role-row").filter({
    has: page.getByText(role.effectiveDisplayLabel, { exact: true })
  });
  await expect(button).toHaveCount(1);
  await button.click();
  await expect(
    page.locator(".role-header h3", {
      hasText: role.effectiveDisplayLabel
    })
  ).toBeVisible();
}

async function loadConflictRow(
  page: Page,
  conflictId: string,
  label: string,
  maximumPageLoads: number
): Promise<Locator> {
  const rows = page.locator(".conflict-row");
  const target = page.locator(
    `.conflict-row[data-conflict-id=${JSON.stringify(conflictId)}]`
  );
  for (let loaded = 0; loaded <= maximumPageLoads; loaded += 1) {
    if ((await target.count()) > 0) {
      await expect(target).toBeVisible();
      return target;
    }
    if (loaded === maximumPageLoads) {
      break;
    }
    const loadMore = page.getByRole("button", {
      name: "Load more conflicts",
      exact: true
    });
    await expect(loadMore).toBeEnabled();
    const priorCount = await rows.count();
    await loadMore.click();
    await expect
      .poll(() => rows.count(), { timeout: 30_000 })
      .toBeGreaterThan(priorCount);
  }
  throw new Error(
    `The ${label} conflict was not visible after ${maximumPageLoads} bounded page loads.`
  );
}

async function loadAllConflictPages(
  page: Page,
  expectedTotal: number
): Promise<void> {
  const pageSize = 50;
  const maximumSyntheticEvidenceTotal = 200;
  if (
    expectedTotal < 0 ||
    expectedTotal > maximumSyntheticEvidenceTotal
  ) {
    throw new Error(
      "The synthetic conflict total exceeded the bounded E2E evidence limit."
    );
  }
  const rows = page.locator(".conflict-row");
  const maximumPageLoads = Math.max(
    0,
    Math.ceil(expectedTotal / pageSize) - 1
  );
  for (let loaded = 0; loaded <= maximumPageLoads; loaded += 1) {
    const currentCount = await rows.count();
    if (currentCount === expectedTotal) {
      await expect(
        page.getByRole("button", {
          name: "Load more conflicts",
          exact: true
        })
      ).toHaveCount(0);
      return;
    }
    if (currentCount > expectedTotal || loaded === maximumPageLoads) {
      break;
    }
    const loadMore = page.getByRole("button", {
      name: "Load more conflicts",
      exact: true
    });
    await expect(loadMore).toBeEnabled();
    await loadMore.click();
    await expect
      .poll(() => rows.count(), { timeout: 30_000 })
      .toBeGreaterThan(currentCount);
  }
  throw new Error(
    `The desktop loaded ${await rows.count()} of ${expectedTotal} bounded conflict records.`
  );
}

async function selectVoice(
  page: Page,
  catalog: VoiceCatalogResponse,
  voiceProfileId: string
): Promise<void> {
  const profile = requiredItem(
    catalog.items.find(
      (candidate) => candidate.voiceProfileId === voiceProfileId
    ),
    `voice profile ${voiceProfileId}`
  );
  const card = page.locator(".candidate-card").filter({
    has: page.getByRole("heading", {
      name: profile.displayLabel,
      exact: true
    })
  });
  await expect(card).toBeVisible();
  await card
    .getByRole("button", { name: "Select voice", exact: true })
    .click();
  await expectCorrectionNotice(page, "Select Voice");
  await expect(
    page.locator(".effective-assignment strong")
  ).toHaveText(profile.displayLabel);
}

async function lockSelectedAssignment(page: Page): Promise<void> {
  const lock = page.getByRole("button", {
    name: "Lock assignment",
    exact: true
  });
  await expect(lock).toBeEnabled({ timeout: 30_000 });
  await lock.click();
  await expectCorrectionNotice(page, "Lock Assignment");
  await expect(page.locator(".effective-assignment")).toContainText(
    "Human Locked"
  );
}

function candidateCard(
  page: Page,
  catalog: VoiceCatalogResponse,
  voiceProfileId: string
) {
  const profile = requiredItem(
    catalog.items.find(
      (candidate) => candidate.voiceProfileId === voiceProfileId
    ),
    `voice profile ${voiceProfileId}`
  );
  return page.locator(".candidate-card").filter({
    has: page.getByRole("heading", {
      name: profile.displayLabel,
      exact: true
    })
  });
}

async function expectCorrectionNotice(
  page: Page,
  operation: string
): Promise<void> {
  await expect(
    page.getByText(
      `${operation} saved as immutable human provenance.`,
      { exact: true }
    )
  ).toBeVisible({ timeout: 30_000 });
  await dismissNotice(page);
}

async function dismissNotice(page: Page): Promise<void> {
  const dismiss = page.getByRole("button", {
    name: "Dismiss notification",
    exact: true
  });
  if ((await dismiss.count()) > 0 && (await dismiss.first().isVisible())) {
    await dismiss.first().click();
  }
}

function assertCurrentPhase2Prerequisites(
  phase2: Phase2RuntimeSnapshot
): void {
  expect(phase2.run.status).toBe("succeeded");
  expect(phase2.run.currentSnapshot).not.toBeNull();
  expect(
    phase2.reviews.map((review) => [review.gateId, review.state])
  ).toEqual([
    ["story_structure_review", "approved"],
    ["character_registry_review", "approved"],
    ["dialogue_attribution_review", "approved"],
    ["whole_book_analysis_review", "approved"]
  ]);
  expect(
    phase2.reviews.every(
      (review) =>
        review.latestDecision !== null &&
        review.latestDecision.decision === "approved"
    )
  ).toBe(true);
}

function assertFinalAssignments(snapshot: Phase3RuntimeSnapshot): void {
  const effective = snapshot.assignments.filter(
    (assignment) => assignment.effective
  );
  const rolesById = new Map(
    snapshot.roles.map((role) => [role.roleId, role])
  );
  const requiredRoles = snapshot.roles.filter(
    (role) => isNarratorRole(role) || role.roleType === "named_character"
  );
  expect(effective).toHaveLength(requiredRoles.length);
  expect(
    requiredRoles.every((role) =>
      effective.some(
        (assignment) =>
          assignment.roleId === role.roleId &&
          assignment.authority === "human_locked" &&
          !["unknown", "prohibited"].includes(assignment.rightsState)
      )
    )
  ).toBe(true);
  expect(
    snapshot.roles
      .filter(
        (role) =>
          !isNarratorRole(role) &&
          role.roleType !== "named_character"
      )
      .every((role) => role.status === "intentionally_uncast")
  ).toBe(true);
  expect(
    effective.every((assignment) => rolesById.has(assignment.roleId))
  ).toBe(true);
}

function assertGovernedCasting(snapshot: Phase3RuntimeSnapshot): void {
  assertFinalAssignments(snapshot);
  expect(snapshot.run.status).toBe("succeeded");
  expect(snapshot.run.profile.profileId).toBe(
    governedVoiceCastingProfileId
  );
  expect(snapshot.run.profile.fingerprint).toBe(
    governedVoiceCastingProfileFingerprint
  );
  expect(snapshot.run.approvedCastSnapshot?.reviewEligible).toBe(false);
  expect(
    snapshot.conflicts.some(
      (conflict) =>
        conflict.category === "metadata_similarity_risk" &&
        conflict.resolutionState === "approved_reuse" &&
        conflict.dispositionCorrectionId !== null
    )
  ).toBe(true);
  expect(
    snapshot.corrections.some(
      (correction) =>
        correction.category === "acknowledge_restricted_rights"
    )
  ).toBe(true);
  expect(
    snapshot.reviews.map((review) => [review.gateId, review.state])
  ).toEqual(
    CASTING_GATE_IDS.map((gateId) => [gateId, "approved"])
  );
  expect(
    snapshot.reviews.every(
      (review) =>
        review.latestDecision?.decision === "approved" &&
        review.latestDecision.immutable
    )
  ).toBe(true);
}

function observedCastingStages(
  events: readonly JobEvent[]
): readonly (typeof CASTING_JOB_STAGES)[number][] {
  const observed: string[] = [];
  for (const event of [...events].sort(
    (left, right) => left.sequence - right.sequence
  )) {
    if (
      event.stage !== undefined &&
      (CASTING_JOB_STAGES as readonly string[]).includes(event.stage) &&
      observed.at(-1) !== event.stage
    ) {
      observed.push(event.stage);
    }
  }
  return observed as readonly (typeof CASTING_JOB_STAGES)[number][];
}

function requiredReview(
  reviews: readonly CastingGateReview[],
  gateId: CastingGateId
): CastingGateReview {
  const review = reviews.find((candidate) => candidate.gateId === gateId);
  if (review === undefined) {
    throw new Error(`The ${gateId} review was unavailable.`);
  }
  return review;
}

function requiredPhase2GateDecisionId(
  phase2: PackagedStoryAnalysisEvidence,
  gateId: PackagedStoryAnalysisEvidence["gates"][number]["gateId"]
): string {
  const gate = requiredItem(
    phase2.gates.find((candidate) => candidate.gateId === gateId),
    `persisted Phase 2 ${gateId} gate`
  );
  expect(gate.beforeRestart.decisionId).toBe(
    gate.afterRestart.decisionId
  );
  return gate.afterRestart.decisionId;
}

function requiredRole(
  roles: readonly ProductionRole[],
  predicate: (role: ProductionRole) => boolean,
  label: string
): ProductionRole {
  return requiredItem(roles.find(predicate), label);
}

function requiredItem<T>(
  value: T | null | undefined,
  label: string
): T {
  if (value === undefined || value === null) {
    throw new Error(`The ${label} evidence was unavailable.`);
  }
  return value;
}

function isNarratorRole(role: ProductionRole): boolean {
  return (
    role.roleType === "primary_narrator" ||
    role.roleType === "secondary_narrator"
  );
}

function gateTitle(gateId: CastingGateId): string {
  switch (gateId) {
    case "narrator_casting_review":
      return "Narrator Casting Review";
    case "character_casting_review":
      return "Character Casting Review";
    case "complete_cast_review":
      return "Complete Cast Review";
  }
}

function gateFlowOperation(
  gateId: CastingGateId
):
  | "approve_narrator_casting_review"
  | "approve_character_casting_review"
  | "approve_complete_cast_review" {
  switch (gateId) {
    case "narrator_casting_review":
      return "approve_narrator_casting_review";
    case "character_casting_review":
      return "approve_character_casting_review";
    case "complete_cast_review":
      return "approve_complete_cast_review";
  }
}

function catalogIdentity(catalog: VoiceCatalogResponse) {
  return {
    revision: revisionIdentity(catalog.catalogRevision),
    providers: catalog.providers.map(providerIdentity),
    models: catalog.models.map(modelIdentity),
    profiles: catalog.items.map(profileIdentity),
    rights: catalog.rights.map(rightsIdentity)
  };
}

function revisionIdentity(revision: VoiceCatalogRevision) {
  return {
    catalogRevisionId: revision.catalogRevisionId,
    revision: revision.revision,
    catalogFingerprint: revision.catalogFingerprint
  };
}

function providerIdentity(provider: VoiceProviderDescriptor) {
  return {
    providerId: provider.providerId,
    providerVersion: provider.providerVersion,
    runtimeAvailability: provider.runtimeAvailability,
    healthStatus: provider.healthStatus
  };
}

function modelIdentity(model: VoiceModelDescriptor) {
  return {
    modelId: model.modelId,
    providerId: model.providerId,
    modelVersion: model.modelVersion,
    availability: model.availability,
    deprecated: model.deprecated
  };
}

function profileIdentity(profile: CastingVoiceProfile) {
  return {
    voiceProfileId: profile.voiceProfileId,
    version: profile.version,
    rightsRecordId: profile.rightsRecordId,
    rightsState: profile.rightsState,
    state: profile.state
  };
}

function rightsIdentity(rights: VoiceRightsRecord) {
  return {
    rightsRecordId: rights.rightsRecordId,
    voiceProfileId: rights.voiceProfileId,
    revision: rights.revision,
    state: rights.state,
    consentStatus: rights.consentStatus
  };
}

function roleIdentity(roles: readonly ProductionRole[]) {
  return roles.map((role) => ({
    roleId: role.roleId,
    roleType: role.roleType,
    revision: role.revision,
    status: role.status,
    effectiveDisplayLabel: role.effectiveDisplayLabel,
    effectiveFingerprint: role.effectiveFingerprint
  }));
}

function candidateIdentity(
  candidates: Readonly<Record<string, readonly CastingCandidate[]>>
) {
  return Object.fromEntries(
    Object.entries(candidates).map(([roleId, items]) => [
      roleId,
      items.map((candidate) => ({
        candidateId: candidate.candidateId,
        voiceProfileId: candidate.voiceProfileId,
        rank: candidate.rank,
        inputFingerprint: candidate.inputFingerprint,
        baseEvidenceFingerprint: candidate.baseEvidenceFingerprint,
        outputFingerprint: candidate.outputFingerprint,
        assessmentInputFingerprint:
          candidate.assessment.inputFingerprint,
        assessmentBaseEvidenceFingerprint:
          candidate.assessment.baseEvidenceFingerprint,
        assessmentFingerprint:
          candidate.assessment.outputFingerprint,
        rejectedByCorrectionId: candidate.rejectedByCorrectionId
      }))
    ])
  );
}

function effectiveAssignmentIdentity(
  assignments: readonly CastAssignment[]
) {
  return assignments
    .filter((assignment) => assignment.effective)
    .map((assignment) => ({
      assignmentId: assignment.assignmentId,
      roleId: assignment.roleId,
      voiceProfileId: assignment.voiceProfileId,
      voiceProfileVersion: assignment.voiceProfileVersion,
      voiceEvidenceFingerprint: assignment.voiceEvidenceFingerprint,
      rightsRecordId: assignment.rightsRecordId,
      rightsRecordRevision: assignment.rightsRecordRevision,
      rightsEvidenceFingerprint: assignment.rightsEvidenceFingerprint,
      catalogRevisionId: assignment.catalogRevisionId,
      castingProfileFingerprint:
        assignment.castingProfileFingerprint,
      phase2SnapshotFingerprint:
        assignment.phase2SnapshotFingerprint,
      effectiveCorrectionSetFingerprint:
        assignment.effectiveCorrectionSetFingerprint,
      authority: assignment.authority,
      rightsState: assignment.rightsState,
      revision: assignment.revision,
      supersedesAssignmentId: assignment.supersedesAssignmentId
    }));
}

function phase3RoleVoiceAssignmentEvidence(
  assignment: CastAssignment,
  rolesById: ReadonlyMap<string, ProductionRole>
): Phase3RoleVoiceAssignmentEvidence {
  const role = requiredItem(
    rolesById.get(assignment.roleId),
    `production role ${assignment.roleId}`
  );
  if (
    assignment.authority !== "human_locked" ||
    assignment.supersedesAssignmentId === null ||
    (assignment.rightsState !== "verified" &&
      assignment.rightsState !== "restricted")
  ) {
    throw new Error(
      "Phase 3A packaged evidence requires a locked assignment with approvable rights."
    );
  }
  return {
    assignmentId: assignment.assignmentId,
    roleId: assignment.roleId,
    roleType: role.roleType,
    voiceProfileId: assignment.voiceProfileId,
    voiceProfileVersion: assignment.voiceProfileVersion,
    voiceEvidenceFingerprint: assignment.voiceEvidenceFingerprint,
    rightsRecordId: assignment.rightsRecordId,
    rightsRecordRevision: assignment.rightsRecordRevision,
    rightsEvidenceFingerprint: assignment.rightsEvidenceFingerprint,
    catalogRevisionId: assignment.catalogRevisionId,
    castingProfileFingerprint: assignment.castingProfileFingerprint,
    phase2SnapshotFingerprint:
      assignment.phase2SnapshotFingerprint,
    effectiveCorrectionSetFingerprint:
      assignment.effectiveCorrectionSetFingerprint,
    authority: assignment.authority,
    rightsState: assignment.rightsState,
    revision: assignment.revision,
    supersedesAssignmentId: assignment.supersedesAssignmentId
  };
}

function conflictIdentity(conflicts: readonly CastingConflict[]) {
  return conflicts.map((conflict) => ({
    conflictId: conflict.conflictId,
    category: conflict.category,
    roleIds: conflict.roleIds,
    voiceProfileIds: conflict.voiceProfileIds,
    resolutionState: conflict.resolutionState,
    dispositionCorrectionId: conflict.dispositionCorrectionId,
    inputFingerprint: conflict.inputFingerprint,
    baseEvidenceFingerprint: conflict.baseEvidenceFingerprint,
    outputFingerprint: conflict.outputFingerprint
  }));
}

function reviewIdentity(reviews: readonly CastingGateReview[]) {
  return reviews.map((review) => ({
    reviewId: review.reviewId,
    gateId: review.gateId,
    state: review.state,
    revision: review.revision,
    evidenceFingerprint: review.evidence.evidenceFingerprint,
    latestDecision: review.latestDecision
  }));
}
