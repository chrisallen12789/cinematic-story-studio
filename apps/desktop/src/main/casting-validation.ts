import { createHash } from "node:crypto";

import {
  CASTING_CONFLICT_CATEGORIES,
  CASTING_HARD_CONSTRAINT_IDS,
  CASTING_SOFT_PREFERENCE_IDS,
  GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
  GOVERNED_VOICE_CASTING_PROFILE_ID,
  LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
  LEGACY_GOVERNED_VOICE_CASTING_PROFILE_ID,
  VOICE_CASTING_CONTRACT_VERSION,
  VOICE_CASTING_PRODUCER_ID,
  VOICE_RIGHTS_POLICY_ID,
  type AnalysisWarning,
  type CastAssignment,
  type CastingCandidate,
  type CastingConflict,
  type CastingCorrection,
  type CastingGateReview,
  type CastingProvenance,
  type CastingReviewDecision,
  type CastingRun,
  type CastingVoiceProfile,
  type ProductionRole,
  type ProductionRoleRequirement,
  type VoiceCatalogRevision,
  type VoiceModelDescriptor,
  type VoiceProviderDescriptor,
  type VoiceRightsRecord
} from "@cinematic-story-studio/contracts";

import {
  CASTING_CORRECTION_OPERATIONS,
  CASTING_GATE_IDS,
  CASTING_JOB_STAGES,
  CASTING_LIMITS,
  CASTING_ROLE_TYPES,
  type AppendCastingCorrectionInput,
  type AppendCastingCorrectionResponse,
  type CastAssignmentsResponse,
  type CastingCandidatesResponse,
  type CastingConflictsResponse,
  type CastingCorrectedValue,
  type CastingCorrectionOperation,
  type CastingCorrectionsResponse,
  type CastingRunEvidenceInput,
  type CastingRunInput,
  type CastingRunResponse,
  type CastingRunsResponse,
  type CastingReviewsResponse,
  type CreateCustomProductionRoleInput,
  type CreateCustomProductionRoleResponse,
  type CreateCastingRunInput,
  type CreateCastingRunResponse,
  type DecideCastingReviewInput,
  type DecideCastingReviewResponse,
  type ListCastAssignmentsInput,
  type ListCastingCandidatesInput,
  type ListCastingConflictsInput,
  type ListCastingCorrectionsInput,
  type ListCastingReviewsInput,
  type ListCastingRunsInput,
  type ListProductionRolesInput,
  type ListVoiceCatalogInput,
  type ProductionRolesResponse,
  type VoiceCatalogResponse
} from "../shared/casting-api.js";
import {
  DESKTOP_CONTRACT_VERSION,
  type DesktopRequest
} from "../shared/desktop-api.js";
import { ValidationError } from "./validation.js";

const IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$/u;
const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const SEMVER_PATTERN =
  /^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/u;
const MAX_CURSOR_LENGTH = 512;
const MAX_ARRAY = 256;
const MAX_WARNINGS = 32;
const MAX_REQUIREMENT_VALUES = 64;
const phase2GateIds = [
  "storyStructureReview",
  "characterRegistryReview",
  "dialogueAttributionReview",
  "wholeBookAnalysisReview"
] as const;

const runEvidenceFields = [
  "projectId",
  "runId",
  "expectedRunFingerprint",
  "expectedCatalogRevisionId",
  "expectedCatalogFingerprint",
  "expectedSnapshotId",
  "expectedSnapshotRevision",
  "expectedSnapshotFingerprint",
  "expectedCastingProfileFingerprint"
] as const;

export function parseListVoiceCatalogRequest(
  value: unknown
): DesktopRequest<ListVoiceCatalogInput> {
  const payload = parseEnvelope(value);
  allowed(payload, [
    "projectId",
    "cursor",
    "limit",
    "expectedCatalogRevisionId",
    "expectedCatalogFingerprint"
  ]);
  required(payload, ["projectId"]);
  const revision =
    payload.expectedCatalogRevisionId === undefined
      ? undefined
      : identifier(payload.expectedCatalogRevisionId, "expectedCatalogRevisionId");
  const fingerprint =
    payload.expectedCatalogFingerprint === undefined
      ? undefined
      : sha256(payload.expectedCatalogFingerprint, "expectedCatalogFingerprint");
  if ((revision === undefined) !== (fingerprint === undefined)) {
    throw invalid(
      "Catalog revision identity and fingerprint must be supplied together."
    );
  }
  return wrap({
    projectId: identifier(payload.projectId, "projectId"),
    ...pageInput(payload, CASTING_LIMITS.maximumPageSize),
    ...(revision === undefined
      ? {}
      : {
          expectedCatalogRevisionId: revision,
          expectedCatalogFingerprint: fingerprint
        })
  });
}

export function parseCreateCastingRunRequest(
  value: unknown
): DesktopRequest<CreateCastingRunInput> {
  const payload = parseEnvelope(value);
  const fields = [
    "projectId",
    "expectedAnalysisRunId",
    "expectedSnapshotId",
    "expectedSnapshotRevision",
    "expectedSnapshotFingerprint",
    "expectedCorrectionSetFingerprint",
    "expectedImportReviewDecisionId",
    "expectedAnalysisGateDecisionIds",
    "expectedCatalogRevisionId",
    "expectedCatalogFingerprint",
    "expectedCastingProfileFingerprint",
    "idempotencyKey"
  ] as const;
  allowed(payload, fields);
  required(payload, fields);
  const gateValues = exact(
    payload.expectedAnalysisGateDecisionIds,
    "expectedAnalysisGateDecisionIds",
    phase2GateIds
  );
  return wrap({
    projectId: identifier(payload.projectId, "projectId"),
    expectedAnalysisRunId: identifier(
      payload.expectedAnalysisRunId,
      "expectedAnalysisRunId"
    ),
    expectedSnapshotId: identifier(
      payload.expectedSnapshotId,
      "expectedSnapshotId"
    ),
    expectedSnapshotRevision: positiveInteger(
      payload.expectedSnapshotRevision,
      "expectedSnapshotRevision"
    ),
    expectedSnapshotFingerprint: sha256(
      payload.expectedSnapshotFingerprint,
      "expectedSnapshotFingerprint"
    ),
    expectedCorrectionSetFingerprint: sha256(
      payload.expectedCorrectionSetFingerprint,
      "expectedCorrectionSetFingerprint"
    ),
    expectedImportReviewDecisionId: identifier(
      payload.expectedImportReviewDecisionId,
      "expectedImportReviewDecisionId"
    ),
    expectedAnalysisGateDecisionIds: {
      storyStructureReview: identifier(
        gateValues.storyStructureReview,
        "expectedAnalysisGateDecisionIds.storyStructureReview"
      ),
      characterRegistryReview: identifier(
        gateValues.characterRegistryReview,
        "expectedAnalysisGateDecisionIds.characterRegistryReview"
      ),
      dialogueAttributionReview: identifier(
        gateValues.dialogueAttributionReview,
        "expectedAnalysisGateDecisionIds.dialogueAttributionReview"
      ),
      wholeBookAnalysisReview: identifier(
        gateValues.wholeBookAnalysisReview,
        "expectedAnalysisGateDecisionIds.wholeBookAnalysisReview"
      )
    },
    expectedCatalogRevisionId: identifier(
      payload.expectedCatalogRevisionId,
      "expectedCatalogRevisionId"
    ),
    expectedCatalogFingerprint: sha256(
      payload.expectedCatalogFingerprint,
      "expectedCatalogFingerprint"
    ),
    expectedCastingProfileFingerprint: sha256(
      payload.expectedCastingProfileFingerprint,
      "expectedCastingProfileFingerprint"
    ),
    idempotencyKey: identifier(payload.idempotencyKey, "idempotencyKey")
  });
}

export function parseListCastingRunsRequest(
  value: unknown
): DesktopRequest<ListCastingRunsInput> {
  const payload = parseEnvelope(value);
  allowed(payload, ["projectId", "cursor", "limit"]);
  required(payload, ["projectId"]);
  return wrap({
    projectId: identifier(payload.projectId, "projectId"),
    ...pageInput(payload, CASTING_LIMITS.maximumPageSize)
  });
}

export function parseCastingRunRequest(
  value: unknown
): DesktopRequest<CastingRunInput> {
  const payload = parseEnvelope(value);
  allowed(payload, ["projectId", "runId"]);
  required(payload, ["projectId", "runId"]);
  return wrap({
    projectId: identifier(payload.projectId, "projectId"),
    runId: identifier(payload.runId, "runId")
  });
}

export function parseListProductionRolesRequest(
  value: unknown
): DesktopRequest<ListProductionRolesInput> {
  return wrap(parseEvidencePage(value, CASTING_LIMITS.maximumPageSize));
}

export function parseCreateCustomProductionRoleRequest(
  value: unknown
): DesktopRequest<CreateCustomProductionRoleInput> {
  const payload = parseEnvelope(value);
  const fields = [
    ...runEvidenceFields,
    "definitionId",
    "label",
    "performanceRequirements",
    "reason",
    "expectedCorrectionSetFingerprint",
    "idempotencyKey"
  ] as const;
  allowed(payload, fields);
  required(payload, fields);
  return wrap({
    ...parseEvidence(payload),
    definitionId: identifier(payload.definitionId, "definitionId"),
    label: text(payload.label, "label", 1, 200),
    performanceRequirements: validateRequirement(
      payload.performanceRequirements,
      "performanceRequirements"
    ),
    reason: text(
      payload.reason,
      "reason",
      1,
      CASTING_LIMITS.maximumCorrectionReasonCodePoints
    ),
    expectedCorrectionSetFingerprint: sha256(
      payload.expectedCorrectionSetFingerprint,
      "expectedCorrectionSetFingerprint"
    ),
    idempotencyKey: identifier(payload.idempotencyKey, "idempotencyKey")
  });
}

export function parseListCastingCandidatesRequest(
  value: unknown
): DesktopRequest<ListCastingCandidatesInput> {
  const payload = parseEnvelope(value);
  allowed(payload, [
    ...runEvidenceFields,
    "roleId",
    "expectedRoleRevision",
    "cursor",
    "limit"
  ]);
  required(payload, [
    ...runEvidenceFields,
    "roleId",
    "expectedRoleRevision"
  ]);
  return wrap({
    ...parseEvidence(payload),
    roleId: identifier(payload.roleId, "roleId"),
    expectedRoleRevision: positiveInteger(
      payload.expectedRoleRevision,
      "expectedRoleRevision"
    ),
    ...pageInput(payload, CASTING_LIMITS.maximumCandidatesPerRole)
  });
}

export function parseListCastingConflictsRequest(
  value: unknown
): DesktopRequest<ListCastingConflictsInput> {
  return wrap(parseEvidencePage(value, CASTING_LIMITS.maximumPageSize));
}

export function parseListCastAssignmentsRequest(
  value: unknown
): DesktopRequest<ListCastAssignmentsInput> {
  return wrap(parseEvidencePage(value, CASTING_LIMITS.maximumPageSize));
}

export function parseListCastingCorrectionsRequest(
  value: unknown
): DesktopRequest<ListCastingCorrectionsInput> {
  return wrap(parseEvidencePage(value, CASTING_LIMITS.maximumPageSize));
}

export function parseAppendCastingCorrectionRequest(
  value: unknown
): DesktopRequest<AppendCastingCorrectionInput> {
  const payload = parseEnvelope(value);
  const fields = [
    "projectId",
    "runId",
    "operation",
    "targetRoleId",
    "expectedRoleRevision",
    "expectedRunFingerprint",
    "expectedCatalogFingerprint",
    "expectedSnapshotFingerprint",
    "expectedCorrectionSetFingerprint",
    "previousEffectiveFingerprint",
    "voiceProfileId",
    "correctedValue",
    "reason",
    "supersedesCorrectionId",
    "idempotencyKey"
  ] as const;
  allowed(payload, fields);
  required(payload, fields);
  const operation = enumeration(
    payload.operation,
    CASTING_CORRECTION_OPERATIONS,
    "operation"
  );
  const voiceProfileId = nullableIdentifier(
    payload.voiceProfileId,
    "voiceProfileId"
  );
  const correctedValue = parseCorrectedValue(
    operation,
    payload.correctedValue
  );
  if (
    ["select_voice", "acknowledge_restricted_rights", "reject_candidate"].includes(
      operation
    ) &&
    voiceProfileId === null
  ) {
    throw invalid(`${operation} requires voiceProfileId.`);
  }
  if (
    [
      "clear_assignment",
      "lock_assignment",
      "unlock_assignment",
      "mark_intentionally_uncast",
      "change_role_label",
      "change_casting_requirement",
      "approve_voice_reuse",
      "record_custom_rationale"
    ].includes(operation) &&
    voiceProfileId !== null
  ) {
    throw invalid(`${operation} does not accept voiceProfileId.`);
  }
  if (
    operation === "select_voice" &&
    (!("voiceProfileId" in correctedValue) ||
      correctedValue.voiceProfileId !== voiceProfileId)
  ) {
    throw invalid("Selecting a voice requires one matching voice value.");
  }
  return wrap({
    projectId: identifier(payload.projectId, "projectId"),
    runId: identifier(payload.runId, "runId"),
    operation,
    targetRoleId: identifier(payload.targetRoleId, "targetRoleId"),
    expectedRoleRevision: positiveInteger(
      payload.expectedRoleRevision,
      "expectedRoleRevision"
    ),
    expectedRunFingerprint: sha256(
      payload.expectedRunFingerprint,
      "expectedRunFingerprint"
    ),
    expectedCatalogFingerprint: sha256(
      payload.expectedCatalogFingerprint,
      "expectedCatalogFingerprint"
    ),
    expectedSnapshotFingerprint: sha256(
      payload.expectedSnapshotFingerprint,
      "expectedSnapshotFingerprint"
    ),
    expectedCorrectionSetFingerprint: sha256(
      payload.expectedCorrectionSetFingerprint,
      "expectedCorrectionSetFingerprint"
    ),
    previousEffectiveFingerprint: sha256(
      payload.previousEffectiveFingerprint,
      "previousEffectiveFingerprint"
    ),
    voiceProfileId,
    correctedValue,
    reason: text(
      payload.reason,
      "reason",
      1,
      CASTING_LIMITS.maximumCorrectionReasonCodePoints
    ),
    supersedesCorrectionId: nullableIdentifier(
      payload.supersedesCorrectionId,
      "supersedesCorrectionId"
    ),
    idempotencyKey: identifier(payload.idempotencyKey, "idempotencyKey")
  });
}

export function parseListCastingReviewsRequest(
  value: unknown
): DesktopRequest<ListCastingReviewsInput> {
  const payload = parseEnvelope(value);
  allowed(payload, [
    ...runEvidenceFields,
    "expectedApprovedCastSnapshotId",
    "expectedApprovedCastSnapshotRevision"
  ]);
  required(payload, [
    ...runEvidenceFields,
    "expectedApprovedCastSnapshotId",
    "expectedApprovedCastSnapshotRevision"
  ]);
  return wrap({
    ...parseEvidence(payload),
    expectedApprovedCastSnapshotId: identifier(
      payload.expectedApprovedCastSnapshotId,
      "expectedApprovedCastSnapshotId"
    ),
    expectedApprovedCastSnapshotRevision: positiveInteger(
      payload.expectedApprovedCastSnapshotRevision,
      "expectedApprovedCastSnapshotRevision"
    )
  });
}

export function parseDecideCastingReviewRequest(
  value: unknown
): DesktopRequest<DecideCastingReviewInput> {
  const payload = parseEnvelope(value);
  const fields = [
    "projectId",
    "runId",
    "gateId",
    "decision",
    "expectedRevision",
    "expectedEvidenceFingerprint",
    "expectedRunFingerprint",
    "expectedApprovedCastSnapshotId",
    "expectedApprovedCastSnapshotRevision",
    "warningAcknowledgementIds",
    "rationale",
    "supersedesDecisionId",
    "idempotencyKey"
  ] as const;
  allowed(payload, fields);
  required(payload, fields);
  return wrap({
    projectId: identifier(payload.projectId, "projectId"),
    runId: identifier(payload.runId, "runId"),
    gateId: enumeration(payload.gateId, CASTING_GATE_IDS, "gateId"),
    decision: enumeration(
      payload.decision,
      ["approve", "request_changes", "reject"] as const,
      "decision"
    ),
    expectedRevision: positiveInteger(
      payload.expectedRevision,
      "expectedRevision"
    ),
    expectedEvidenceFingerprint: sha256(
      payload.expectedEvidenceFingerprint,
      "expectedEvidenceFingerprint"
    ),
    expectedRunFingerprint: sha256(
      payload.expectedRunFingerprint,
      "expectedRunFingerprint"
    ),
    expectedApprovedCastSnapshotId: identifier(
      payload.expectedApprovedCastSnapshotId,
      "expectedApprovedCastSnapshotId"
    ),
    expectedApprovedCastSnapshotRevision: positiveInteger(
      payload.expectedApprovedCastSnapshotRevision,
      "expectedApprovedCastSnapshotRevision"
    ),
    warningAcknowledgementIds: identifierArray(
      payload.warningAcknowledgementIds,
      "warningAcknowledgementIds",
      CASTING_LIMITS.maximumWarningAcknowledgements
    ),
    rationale: text(
      payload.rationale,
      "rationale",
      1,
      CASTING_LIMITS.maximumReviewRationaleCodePoints
    ),
    supersedesDecisionId: nullableIdentifier(
      payload.supersedesDecisionId,
      "supersedesDecisionId"
    ),
    idempotencyKey: identifier(payload.idempotencyKey, "idempotencyKey")
  });
}

export function validateVoiceCatalogResponse(
  value: unknown,
  input: ListVoiceCatalogInput
): VoiceCatalogResponse {
  const root = exact(value, "voice catalog response", [
    "correlationId",
    "catalogRevision",
    "providers",
    "models",
    "items",
    "rights",
    "total",
    "pageSize"
  ], ["nextCursor"]);
  identifier(root.correlationId, "correlationId");
  const revision = validateCatalogRevision(root.catalogRevision);
  if (
    (input.expectedCatalogRevisionId !== undefined &&
      revision.catalogRevisionId !== input.expectedCatalogRevisionId) ||
    (input.expectedCatalogFingerprint !== undefined &&
      revision.catalogFingerprint !== input.expectedCatalogFingerprint)
  ) {
    throw invalid("The catalog response did not match the requested revision.");
  }
  const providers = boundedArray(root.providers, "providers", MAX_ARRAY).map(
    validateProvider
  );
  const models = boundedArray(root.models, "models", MAX_ARRAY).map(validateModel);
  const rights = boundedArray(root.rights, "rights", MAX_ARRAY).map(validateRights);
  const providerIds = new Set(providers.map((item) => item.providerId));
  const modelIds = new Set(models.map((item) => item.modelId));
  const rightsByProfile = new Map(
    rights.map((recordValue) => [recordValue.voiceProfileId, recordValue])
  );
  const items = boundedArray(
    root.items,
    "items",
    input.limit ?? CASTING_LIMITS.defaultPageSize
  ).map((item) =>
    validateVoiceProfile(
      item,
      revision.catalogRevisionId,
      providerIds,
      modelIds,
      rightsByProfile
    )
  );
  validatePage(root, items.length, input.limit);
  return value as VoiceCatalogResponse;
}

export function validateCreateCastingRunResponse(
  value: unknown,
  input: CreateCastingRunInput
): CreateCastingRunResponse {
  const root = exact(value, "create casting run response", [
    "correlationId",
    "run",
    "job"
  ]);
  identifier(root.correlationId, "correlationId");
  const run = validateRun(root.run, input.projectId);
  assertCreateEvidence(run, input);
  validateJob(
    root.job,
    input.projectId,
    run.jobId,
    run.castingRunId
  );
  return value as CreateCastingRunResponse;
}

export function validateCastingRunsResponse(
  value: unknown,
  input: ListCastingRunsInput
): CastingRunsResponse {
  return validateRunPage(value, input);
}

export function validateCastingRunResponse(
  value: unknown,
  input: CastingRunInput
): CastingRunResponse {
  const root = exact(value, "casting run response", [
    "correlationId",
    "run"
  ]);
  identifier(root.correlationId, "correlationId");
  const run = validateRun(root.run, input.projectId);
  if (run.castingRunId !== input.runId) {
    throw invalid("The returned casting run did not match the requested run.");
  }
  return value as CastingRunResponse;
}

export function validateProductionRolesResponse(
  value: unknown,
  input: ListProductionRolesInput
): ProductionRolesResponse {
  return validateOwnedPage(
    value,
    input,
    "roles",
    (item) => validateRole(item, input),
    CASTING_LIMITS.maximumPageSize
  ) as ProductionRolesResponse;
}

export function validateCreateCustomProductionRoleResponse(
  value: unknown,
  input: CreateCustomProductionRoleInput
): CreateCustomProductionRoleResponse {
  const root = exact(value, "custom production role response", [
    "correlationId",
    "role",
    "invalidatedGateIds",
    "run",
    "reviews"
  ]);
  identifier(root.correlationId, "correlationId");
  const run = validateRun(root.run, input.projectId);
  if (
    run.castingRunId !== input.runId ||
    run.catalogRevisionId !== input.expectedCatalogRevisionId ||
    run.catalogFingerprint !== input.expectedCatalogFingerprint ||
    run.effectiveCorrectionSetFingerprint !==
      input.expectedCorrectionSetFingerprint ||
    run.profile.fingerprint !== input.expectedCastingProfileFingerprint ||
    run.prerequisites.analysisSnapshotId !== input.expectedSnapshotId ||
    run.prerequisites.analysisSnapshotRevision !==
      input.expectedSnapshotRevision ||
    run.prerequisites.analysisSnapshotFingerprint !==
      input.expectedSnapshotFingerprint
  ) {
    throw invalid("The custom role response changed immutable casting evidence.");
  }
  const evidence: CastingRunEvidenceInput = {
    projectId: input.projectId,
    runId: input.runId,
    expectedRunFingerprint: runFingerprint(run),
    expectedCatalogRevisionId: run.catalogRevisionId,
    expectedCatalogFingerprint: run.catalogFingerprint,
    expectedSnapshotId: run.prerequisites.analysisSnapshotId,
    expectedSnapshotRevision: run.prerequisites.analysisSnapshotRevision,
    expectedSnapshotFingerprint:
      run.prerequisites.analysisSnapshotFingerprint,
    expectedCastingProfileFingerprint: run.profile.fingerprint
  };
  const role = validateRole(root.role, evidence);
  const customRange = role.range;
  const customWarning =
    role.warnings.length === 1 ? role.warnings[0] : undefined;
  if (
    role.roleType !== "custom" ||
    role.phase2EntityId !== null ||
    role.characterId !== null ||
    role.dialogueLineCount !== 0 ||
    role.narrationSpanCount !== 0 ||
    role.approximateWordCount !== 0 ||
    customRange.firstChapterOrdinal !== null ||
    customRange.lastChapterOrdinal !== null ||
    customRange.firstSceneOrdinal !== null ||
    customRange.lastSceneOrdinal !== null ||
    role.effectiveDisplayLabel !== input.label ||
    role.analysisRunId !== run.prerequisites.analysisRunId ||
    role.languageRequirements.length !== 1 ||
    role.languageRequirements[0] !== input.performanceRequirements.language ||
    !sameRequirement(
      role.performanceRequirements,
      input.performanceRequirements
    ) ||
    role.roleImportance !== "supporting" ||
    role.unresolvedMaterialExplicitlyRepresented !== false ||
    role.status !== "active" ||
    role.provenance.origin !== "human" ||
    role.provenance.sourceRevisionId !== input.definitionId ||
    role.provenance.reason !== input.reason ||
    role.provenance.inputFingerprint === undefined ||
    customWarning?.code !== "CUSTOM_ROLE_CONTENT_FREE" ||
    customWarning.severity !== "warning" ||
    customWarning.requiresHumanReview !== true ||
    customWarning.relatedEntityIds.length !== 0 ||
    customWarning.evidence.length !== 0
  ) {
    throw invalid("The returned custom role did not match its definition.");
  }
  const invalidatedGateIds = enumArray(
    root.invalidatedGateIds,
    "invalidatedGateIds",
    CASTING_GATE_IDS,
    CASTING_GATE_IDS.length
  );
  if (
    invalidatedGateIds.length !== CASTING_GATE_IDS.length ||
    CASTING_GATE_IDS.some(
      (gateId) => !invalidatedGateIds.includes(gateId)
    )
  ) {
    throw invalid("A custom role must invalidate every casting gate.");
  }
  const reviews = boundedArray(
    root.reviews,
    "reviews",
    CASTING_GATE_IDS.length
  ).map((review) => validateReview(review, evidence));
  validateReviewSet(reviews);
  if (reviews.some((review) => review.state !== "pending")) {
    throw invalid("A custom role must publish fresh pending casting reviews.");
  }
  const snapshot = run.approvedCastSnapshot;
  if (
    snapshot === null ||
    snapshot.effectiveCorrectionSetFingerprint !==
      input.expectedCorrectionSetFingerprint ||
    reviews.some(
      (review) =>
        review.evidence.effectiveCorrectionSetFingerprint !==
          input.expectedCorrectionSetFingerprint ||
        review.evidence.approvedCastSnapshotId !== snapshot.snapshotId ||
        review.evidence.approvedCastSnapshotRevision !== snapshot.revision
    )
  ) {
    throw invalid(
      "The custom role response changed correction or cast-snapshot evidence."
    );
  }
  return value as CreateCustomProductionRoleResponse;
}

export function validateCastingCandidatesResponse(
  value: unknown,
  input: ListCastingCandidatesInput
): CastingCandidatesResponse {
  return validateOwnedPage(
    value,
    input,
    "candidates",
    (item) => validateCandidate(item, input),
    CASTING_LIMITS.maximumCandidatesPerRole
  ) as CastingCandidatesResponse;
}

export function validateCastingConflictsResponse(
  value: unknown,
  input: ListCastingConflictsInput
): CastingConflictsResponse {
  return validateOwnedPage(
    value,
    input,
    "conflicts",
    (item) => validateConflict(item, input),
    CASTING_LIMITS.maximumPageSize
  ) as CastingConflictsResponse;
}

export function validateCastAssignmentsResponse(
  value: unknown,
  input: ListCastAssignmentsInput
): CastAssignmentsResponse {
  return validateOwnedPage(
    value,
    input,
    "assignments",
    (item) => validateAssignment(item, input),
    CASTING_LIMITS.maximumPageSize
  ) as CastAssignmentsResponse;
}

export function validateCastingCorrectionsResponse(
  value: unknown,
  input: ListCastingCorrectionsInput
): CastingCorrectionsResponse {
  return validateOwnedPage(
    value,
    input,
    "corrections",
    (item) => validateCorrection(item, input),
    CASTING_LIMITS.maximumPageSize
  ) as CastingCorrectionsResponse;
}

export function validateAppendCastingCorrectionResponse(
  value: unknown,
  input: AppendCastingCorrectionInput
): AppendCastingCorrectionResponse {
  const root = exact(value, "casting correction response", [
    "correlationId",
    "correction",
    "assignment",
    "invalidatedGateIds",
    "run",
    "reviews"
  ]);
  identifier(root.correlationId, "correlationId");
  const run = validateRun(root.run, input.projectId);
  if (
    run.castingRunId !== input.runId ||
    run.catalogFingerprint !== input.expectedCatalogFingerprint ||
    run.prerequisites.analysisSnapshotFingerprint !==
      input.expectedSnapshotFingerprint
  ) {
    throw invalid("The correction response changed immutable casting evidence.");
  }
  const evidence: CastingRunEvidenceInput = {
    projectId: input.projectId,
    runId: input.runId,
    expectedRunFingerprint: runFingerprint(run),
    expectedCatalogRevisionId: run.catalogRevisionId,
    expectedCatalogFingerprint: run.catalogFingerprint,
    expectedSnapshotId: run.prerequisites.analysisSnapshotId,
    expectedSnapshotRevision: run.prerequisites.analysisSnapshotRevision,
    expectedSnapshotFingerprint:
      run.prerequisites.analysisSnapshotFingerprint,
    expectedCastingProfileFingerprint: run.profile.fingerprint
  };
  const correction = validateCorrection(root.correction, evidence);
  if (
    correction.category !== input.operation ||
    correction.targetRoleId !== input.targetRoleId ||
    correction.priorEffectiveFingerprint !== input.previousEffectiveFingerprint
  ) {
    throw invalid("The returned correction did not match the submitted overlay.");
  }
  if (root.assignment !== null) {
    validateAssignment(root.assignment, evidence);
  }
  enumArray(
    root.invalidatedGateIds,
    "invalidatedGateIds",
    CASTING_GATE_IDS,
    CASTING_GATE_IDS.length
  );
  const reviews = boundedArray(
    root.reviews,
    "reviews",
    CASTING_GATE_IDS.length
  ).map((review) => validateReview(review, evidence));
  validateReviewSet(reviews);
  return value as AppendCastingCorrectionResponse;
}

export function validateCastingReviewsResponse(
  value: unknown,
  input: ListCastingReviewsInput
): CastingReviewsResponse {
  const root = exact(value, "casting reviews response", [
    "correlationId",
    "castingRunId",
    "items"
  ]);
  identifier(root.correlationId, "correlationId");
  if (identifier(root.castingRunId, "castingRunId") !== input.runId) {
    throw invalid("The review page belonged to another casting run.");
  }
  const items = boundedArray(
    root.items,
    "items",
    CASTING_GATE_IDS.length
  ).map((review) => validateReview(review, input));
  validateReviewSet(items);
  for (const review of items) {
    if (
      review.evidence.approvedCastSnapshotId !==
        input.expectedApprovedCastSnapshotId ||
      review.evidence.approvedCastSnapshotRevision !==
        input.expectedApprovedCastSnapshotRevision
    ) {
      throw invalid("The review evidence did not match the requested cast snapshot.");
    }
  }
  return value as CastingReviewsResponse;
}

export function validateDecideCastingReviewResponse(
  value: unknown,
  input: DecideCastingReviewInput
): DecideCastingReviewResponse {
  const root = exact(value, "casting review decision response", [
    "correlationId",
    "review",
    "decision",
    "snapshot",
    "run"
  ]);
  identifier(root.correlationId, "correlationId");
  const run = validateRun(root.run, input.projectId);
  if (
    run.castingRunId !== input.runId ||
    runFingerprint(run) !== input.expectedRunFingerprint
  ) {
    throw invalid("The review response changed casting-run identity.");
  }
  const snapshot = validateSnapshot(
    root.snapshot,
    input.projectId,
    input.runId,
    run.profile.fingerprint
  );
  if (
    snapshot.snapshotId !== input.expectedApprovedCastSnapshotId ||
    snapshot.revision !== input.expectedApprovedCastSnapshotRevision
  ) {
    throw invalid("The review response changed approved-cast snapshot identity.");
  }
  const evidence: CastingRunEvidenceInput = {
    projectId: input.projectId,
    runId: input.runId,
    expectedRunFingerprint: input.expectedRunFingerprint,
    expectedCatalogRevisionId: snapshot.catalogRevisionId,
    expectedCatalogFingerprint: snapshot.catalogFingerprint,
    expectedSnapshotId: run.prerequisites.analysisSnapshotId,
    expectedSnapshotRevision: run.prerequisites.analysisSnapshotRevision,
    expectedSnapshotFingerprint: run.prerequisites.analysisSnapshotFingerprint,
    expectedCastingProfileFingerprint: run.profile.fingerprint
  };
  const review = validateReview(root.review, evidence);
  const decision = validateDecision(root.decision, evidence);
  const expectedDecision =
    input.decision === "approve"
      ? "approved"
      : input.decision === "request_changes"
        ? "changes_requested"
        : "rejected";
  if (
    review.gateId !== input.gateId ||
    decision.gateId !== input.gateId ||
    review.state !== expectedDecision ||
    decision.decision !== expectedDecision ||
    decision.evidenceFingerprint !== input.expectedEvidenceFingerprint ||
    decision.approvedCastSnapshotId !== input.expectedApprovedCastSnapshotId ||
    decision.approvedCastSnapshotRevision !==
      input.expectedApprovedCastSnapshotRevision ||
    review.latestDecision?.decisionId !== decision.decisionId
  ) {
    throw invalid("The review decision response did not match the submitted decision.");
  }
  return value as DecideCastingReviewResponse;
}

function validateRunPage(
  value: unknown,
  input: ListCastingRunsInput
): CastingRunsResponse {
  const root = exact(
    value,
    "casting run page",
    ["correlationId", "pageSize", "total", "items"],
    ["nextCursor"]
  );
  identifier(root.correlationId, "correlationId");
  const items = boundedArray(
    root.items,
    "items",
    input.limit ?? CASTING_LIMITS.defaultPageSize
  ).map((item) => validateRun(item, input.projectId));
  validatePage(root, items.length, input.limit);
  return value as CastingRunsResponse;
}

function validateOwnedPage(
  value: unknown,
  input: CastingRunEvidenceInput & { readonly limit?: number },
  field: string,
  validateItem: (item: unknown) => unknown,
  maximum: number
): unknown {
  const root = exact(
    value,
    `${field} page`,
    ["correlationId", "castingRunId", "pageSize", "total", "items"],
    ["nextCursor"]
  );
  identifier(root.correlationId, "correlationId");
  if (identifier(root.castingRunId, "castingRunId") !== input.runId) {
    throw invalid(`The ${field} page belonged to another casting run.`);
  }
  const items = boundedArray(
    root.items,
    "items",
    Math.min(input.limit ?? CASTING_LIMITS.defaultPageSize, maximum)
  );
  items.forEach(validateItem);
  validatePage(root, items.length, input.limit, maximum);
  return value;
}

function validateCatalogRevision(value: unknown): VoiceCatalogRevision {
  const item = exact(value, "catalog revision", [
    "contractVersion",
    "catalogRevisionId",
    "revision",
    "semanticVersion",
    "rightsPolicyId",
    "providerDescriptorIds",
    "modelDescriptorIds",
    "voiceProfileIds",
    "createdAt",
    "immutable",
    "provenance",
    "catalogFingerprint"
  ]);
  contractVersion(item.contractVersion, "catalogRevision.contractVersion");
  identifier(item.catalogRevisionId, "catalogRevision.catalogRevisionId");
  positiveInteger(item.revision, "catalogRevision.revision");
  semver(item.semanticVersion, "catalogRevision.semanticVersion");
  if (item.rightsPolicyId !== VOICE_RIGHTS_POLICY_ID || item.immutable !== true) {
    throw invalid("The catalog revision policy or immutability marker was invalid.");
  }
  identifierArray(
    item.providerDescriptorIds,
    "catalogRevision.providerDescriptorIds",
    MAX_ARRAY
  );
  identifierArray(
    item.modelDescriptorIds,
    "catalogRevision.modelDescriptorIds",
    MAX_ARRAY
  );
  identifierArray(
    item.voiceProfileIds,
    "catalogRevision.voiceProfileIds",
    5_000
  );
  isoDate(item.createdAt, "catalogRevision.createdAt");
  validateProvenance(item.provenance, "catalogRevision.provenance");
  sha256(item.catalogFingerprint, "catalogRevision.catalogFingerprint");
  return value as VoiceCatalogRevision;
}

function validateProvider(value: unknown): VoiceProviderDescriptor {
  const item = exact(value, "provider descriptor", [
    "contractVersion",
    "providerId",
    "providerVersion",
    "providerType",
    "runtimeAvailability",
    "catalogAvailability",
    "synthesisImplemented",
    "networkUseRequired",
    "credentialsRequired",
    "supportedOperatingSystems",
    "supportedLanguages",
    "outputCapability",
    "rightsMetadataCapabilities",
    "healthStatus",
    "provenance"
  ]);
  contractVersion(item.contractVersion, "provider.contractVersion");
  identifier(item.providerId, "provider.providerId");
  semver(item.providerVersion, "provider.providerVersion");
  enumeration(
    item.providerType,
    ["local", "cloud_capable_disabled", "development_fixture"] as const,
    "provider.providerType"
  );
  availability(item.runtimeAvailability, "provider.runtimeAvailability");
  availability(item.catalogAvailability, "provider.catalogAvailability");
  booleanValue(item.synthesisImplemented, "provider.synthesisImplemented");
  booleanValue(item.networkUseRequired, "provider.networkUseRequired");
  booleanValue(item.credentialsRequired, "provider.credentialsRequired");
  enumArray(
    item.supportedOperatingSystems,
    "provider.supportedOperatingSystems",
    ["windows", "macos", "linux"] as const,
    3
  );
  stringArray(item.supportedLanguages, "provider.supportedLanguages", 64, 40);
  validateOutputCapability(item.outputCapability, "provider.outputCapability");
  enumArray(
    item.rightsMetadataCapabilities,
    "provider.rightsMetadataCapabilities",
    [
      "license_identifier",
      "commercial_use",
      "attribution",
      "distribution_limits",
      "consent",
      "effective_dates",
      "evidence_reference"
    ] as const,
    7
  );
  enumeration(
    item.healthStatus,
    ["healthy", "degraded", "unavailable", "disabled"] as const,
    "provider.healthStatus"
  );
  validateProvenance(item.provenance, "provider.provenance");
  return value as VoiceProviderDescriptor;
}

function validateModel(value: unknown): VoiceModelDescriptor {
  const item = exact(value, "model descriptor", [
    "contractVersion",
    "modelId",
    "providerId",
    "modelName",
    "modelVersion",
    "capability",
    "executionLocation",
    "licenseClassification",
    "availability",
    "deprecated",
    "provenance"
  ]);
  contractVersion(item.contractVersion, "model.contractVersion");
  identifier(item.modelId, "model.modelId");
  identifier(item.providerId, "model.providerId");
  text(item.modelName, "model.modelName", 1, 120);
  semver(item.modelVersion, "model.modelVersion");
  const capability = exact(item.capability, "model.capability", [
    "supportedLanguages",
    "supportedLocales",
    "expressiveControls",
    "speakingRateRange",
    "pitchControl",
    "styleControl",
    "outputCapability"
  ]);
  stringArray(
    capability.supportedLanguages,
    "model.capability.supportedLanguages",
    64,
    40
  );
  stringArray(
    capability.supportedLocales,
    "model.capability.supportedLocales",
    64,
    40
  );
  enumArray(
    capability.expressiveControls,
    "model.capability.expressiveControls",
    ["energy", "emotion", "pace", "pitch", "style"] as const,
    5
  );
  validateUnitRange(
    capability.speakingRateRange,
    "model.capability.speakingRateRange",
    0.1,
    5
  );
  enumeration(
    capability.pitchControl,
    ["none", "categorical", "continuous"] as const,
    "model.capability.pitchControl"
  );
  enumeration(
    capability.styleControl,
    ["none", "categorical", "continuous"] as const,
    "model.capability.styleControl"
  );
  validateOutputCapability(
    capability.outputCapability,
    "model.capability.outputCapability"
  );
  enumeration(
    item.executionLocation,
    ["local", "remote"] as const,
    "model.executionLocation"
  );
  enumeration(
    item.licenseClassification,
    ["fixture_only", "commercial", "restricted", "unknown", "prohibited"] as const,
    "model.licenseClassification"
  );
  availability(item.availability, "model.availability");
  booleanValue(item.deprecated, "model.deprecated");
  validateProvenance(item.provenance, "model.provenance");
  return value as VoiceModelDescriptor;
}

function validateVoiceProfile(
  value: unknown,
  catalogRevisionId: string,
  providerIds: ReadonlySet<string>,
  modelIds: ReadonlySet<string>,
  rightsByProfile: ReadonlyMap<string, VoiceRightsRecord>
): CastingVoiceProfile {
  const item = exact(value, "voice profile", [
    "contractVersion",
    "voiceProfileId",
    "providerId",
    "modelId",
    "providerVoiceId",
    "catalogRevisionId",
    "displayLabel",
    "language",
    "locale",
    "accentOrDialect",
    "agePresentationRange",
    "vocalPresentation",
    "vocalTexture",
    "pitchRange",
    "speakingRateRange",
    "energyRange",
    "expressiveRange",
    "narrationSuitability",
    "dialogueSuitability",
    "longFormSuitability",
    "characterRoleSuitability",
    "maximumRecommendedWords",
    "knownLimitations",
    "rightsRecordId",
    "rightsState",
    "licenseScope",
    "commercialUse",
    "attributionRequired",
    "voiceCloningClassification",
    "consentStatus",
    "metadataSimilarityGroup",
    "reuseRiskGroup",
    "version",
    "state",
    "provenance"
  ]);
  contractVersion(item.contractVersion, "voice.contractVersion");
  const voiceProfileId = identifier(item.voiceProfileId, "voice.voiceProfileId");
  const providerId = identifier(item.providerId, "voice.providerId");
  const modelId = identifier(item.modelId, "voice.modelId");
  if (
    !providerIds.has(providerId) ||
    !modelIds.has(modelId) ||
    identifier(item.catalogRevisionId, "voice.catalogRevisionId") !==
      catalogRevisionId
  ) {
    throw invalid("The voice profile referenced another catalog, provider, or model.");
  }
  identifier(item.providerVoiceId, "voice.providerVoiceId");
  text(item.displayLabel, "voice.displayLabel", 1, 120);
  text(item.language, "voice.language", 1, 40);
  text(item.locale, "voice.locale", 1, 40);
  text(item.accentOrDialect, "voice.accentOrDialect", 1, 80);
  if (item.agePresentationRange !== null) {
    validateIntegerRange(
      item.agePresentationRange,
      "voice.agePresentationRange",
      0,
      130
    );
  }
  vocalPresentation(item.vocalPresentation, "voice.vocalPresentation");
  vocalTexture(item.vocalTexture, "voice.vocalTexture");
  enumeration(
    item.pitchRange,
    ["low", "low_mid", "mid", "mid_high", "high", "wide", "unspecified"] as const,
    "voice.pitchRange"
  );
  validateUnitRange(item.speakingRateRange, "voice.speakingRateRange", 0.1, 5);
  if (item.energyRange !== null) {
    validateNumberRange(item.energyRange, "voice.energyRange", 0, 1);
  }
  stringArray(item.expressiveRange, "voice.expressiveRange", 64, 80);
  suitability(item.narrationSuitability, "voice.narrationSuitability");
  suitability(item.dialogueSuitability, "voice.dialogueSuitability");
  suitability(item.longFormSuitability, "voice.longFormSuitability");
  enumArray(
    item.characterRoleSuitability,
    "voice.characterRoleSuitability",
    ["lead", "supporting", "minor", "group", "announcement", "internal_thought"] as const,
    6
  );
  if (item.maximumRecommendedWords !== null) {
    nonNegativeInteger(
      item.maximumRecommendedWords,
      "voice.maximumRecommendedWords"
    );
  }
  stringArray(item.knownLimitations, "voice.knownLimitations", 64, 200);
  const rightsRecordId = identifier(item.rightsRecordId, "voice.rightsRecordId");
  rightsState(item.rightsState, "voice.rightsState");
  text(item.licenseScope, "voice.licenseScope", 1, 500);
  enumeration(
    item.commercialUse,
    ["permitted", "restricted", "unknown", "prohibited"] as const,
    "voice.commercialUse"
  );
  booleanValue(item.attributionRequired, "voice.attributionRequired");
  enumeration(
    item.voiceCloningClassification,
    [
      "not_cloned_synthetic_fixture",
      "provider_declared_non_cloned",
      "unknown",
      "prohibited"
    ] as const,
    "voice.voiceCloningClassification"
  );
  enumeration(
    item.consentStatus,
    [
      "not_applicable_synthetic_fixture",
      "verified",
      "restricted",
      "missing",
      "unknown",
      "prohibited"
    ] as const,
    "voice.consentStatus"
  );
  nullableIdentifier(item.metadataSimilarityGroup, "voice.metadataSimilarityGroup");
  nullableIdentifier(item.reuseRiskGroup, "voice.reuseRiskGroup");
  semver(item.version, "voice.version");
  enumeration(
    item.state,
    ["active", "unavailable", "deprecated", "blocked"] as const,
    "voice.state"
  );
  validateProvenance(item.provenance, "voice.provenance");
  const rightsRecord = rightsByProfile.get(voiceProfileId);
  if (
    rightsRecord === undefined ||
    rightsRecord.rightsRecordId !== rightsRecordId ||
    rightsRecord.state !== item.rightsState
  ) {
    throw invalid("The voice profile and rights record were inconsistent.");
  }
  return value as CastingVoiceProfile;
}

function validateRights(value: unknown): VoiceRightsRecord {
  const item = exact(value, "voice rights", [
    "contractVersion",
    "rightsRecordId",
    "voiceProfileId",
    "providerId",
    "revision",
    "state",
    "licenseIdentifier",
    "rightsBasis",
    "commercialUsePermission",
    "attributionRequirement",
    "geographicLimitations",
    "distributionLimitations",
    "voiceCloningStatus",
    "consentStatus",
    "effectiveDate",
    "expiresAt",
    "evidenceReference",
    "humanVerificationStatus",
    "provenance"
  ]);
  contractVersion(item.contractVersion, "rights.contractVersion");
  identifier(item.rightsRecordId, "rights.rightsRecordId");
  identifier(item.voiceProfileId, "rights.voiceProfileId");
  identifier(item.providerId, "rights.providerId");
  positiveInteger(item.revision, "rights.revision");
  rightsState(item.state, "rights.state");
  text(item.licenseIdentifier, "rights.licenseIdentifier", 1, 160);
  text(item.rightsBasis, "rights.rightsBasis", 1, 500);
  enumeration(
    item.commercialUsePermission,
    ["permitted", "restricted", "unknown", "prohibited"] as const,
    "rights.commercialUsePermission"
  );
  enumeration(
    item.attributionRequirement,
    ["none", "required", "unknown", "prohibited"] as const,
    "rights.attributionRequirement"
  );
  stringArray(item.geographicLimitations, "rights.geographicLimitations", 32, 120);
  stringArray(
    item.distributionLimitations,
    "rights.distributionLimitations",
    32,
    120
  );
  enumeration(
    item.voiceCloningStatus,
    [
      "not_applicable_synthetic_fixture",
      "not_permitted",
      "permitted_with_consent",
      "unknown",
      "prohibited"
    ] as const,
    "rights.voiceCloningStatus"
  );
  enumeration(
    item.consentStatus,
    [
      "not_applicable_synthetic_fixture",
      "verified",
      "restricted",
      "missing",
      "unknown",
      "prohibited"
    ] as const,
    "rights.consentStatus"
  );
  nullableDate(item.effectiveDate, "rights.effectiveDate");
  nullableDate(item.expiresAt, "rights.expiresAt");
  text(item.evidenceReference, "rights.evidenceReference", 1, 500);
  enumeration(
    item.humanVerificationStatus,
    ["verified", "not_required_fixture", "pending", "rejected"] as const,
    "rights.humanVerificationStatus"
  );
  validateProvenance(item.provenance, "rights.provenance");
  return value as VoiceRightsRecord;
}

function validateRun(value: unknown, projectId: string): CastingRun {
  const item = exact(value, "casting run", [
    "contractVersion",
    "castingRunId",
    "projectId",
    "prerequisites",
    "profile",
    "producerId",
    "catalogRevisionId",
    "catalogFingerprint",
    "effectiveCorrectionSetFingerprint",
    "inputFingerprint",
    "outputFingerprint",
    "idempotencyFingerprint",
    "jobId",
    "status",
    "currentStage",
    "progress",
    "checkpoint",
    "attempt",
    "retryPolicy",
    "failurePolicy",
    "resumeOfCastingRunId",
    "retryOfCastingRunId",
    "retryClassification",
    "cancellationRequested",
    "warnings",
    "summary",
    "approvedCastSnapshot",
    "createdAt",
    "updatedAt",
    "completedAt",
    "failure"
  ]);
  contractVersion(item.contractVersion, "run.contractVersion");
  identifier(item.castingRunId, "run.castingRunId");
  if (identifier(item.projectId, "run.projectId") !== projectId) {
    throw invalid("The casting run belonged to another project.");
  }
  validatePrerequisites(item.prerequisites, projectId);
  const profile = exact(item.profile, "run.profile", ["profileId", "fingerprint"]);
  const profileFingerprint =
    profile.profileId === GOVERNED_VOICE_CASTING_PROFILE_ID &&
    profile.fingerprint === GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
      ? GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
      : profile.profileId === LEGACY_GOVERNED_VOICE_CASTING_PROFILE_ID &&
          profile.fingerprint ===
            LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
        ? LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
        : null;
  if (
    profileFingerprint === null ||
    item.producerId !== VOICE_CASTING_PRODUCER_ID
  ) {
    throw invalid("The casting run profile or producer was not governed.");
  }
  identifier(item.catalogRevisionId, "run.catalogRevisionId");
  sha256(item.catalogFingerprint, "run.catalogFingerprint");
  sha256(
    item.effectiveCorrectionSetFingerprint,
    "run.effectiveCorrectionSetFingerprint"
  );
  sha256(item.inputFingerprint, "run.inputFingerprint");
  if (item.outputFingerprint !== null) {
    sha256(item.outputFingerprint, "run.outputFingerprint");
  }
  sha256(item.idempotencyFingerprint, "run.idempotencyFingerprint");
  identifier(item.jobId, "run.jobId");
  const status = enumeration(
    item.status,
    [
      "queued",
      "running",
      "succeeded",
      "failed",
      "cancelled",
      "interrupted",
      "invalidated"
    ] as const,
    "run.status"
  );
  if (
    item.currentStage !== "queued" &&
    item.currentStage !== "complete"
  ) {
    enumeration(item.currentStage, CASTING_JOB_STAGES, "run.currentStage");
  }
  number(item.progress, "run.progress", 0, 1);
  if (item.checkpoint !== null) {
    const checkpoint = exact(item.checkpoint, "run.checkpoint", [
      "checkpointId",
      "stage",
      "fingerprint",
      "recordedAt"
    ]);
    identifier(checkpoint.checkpointId, "run.checkpoint.checkpointId");
    enumeration(checkpoint.stage, CASTING_JOB_STAGES, "run.checkpoint.stage");
    sha256(checkpoint.fingerprint, "run.checkpoint.fingerprint");
    isoDate(checkpoint.recordedAt, "run.checkpoint.recordedAt");
  }
  positiveInteger(item.attempt, "run.attempt");
  const retryPolicy = exact(item.retryPolicy, "run.retryPolicy", [
    "maxAttempts",
    "retryableFailureCodes"
  ]);
  positiveInteger(retryPolicy.maxAttempts, "run.retryPolicy.maxAttempts");
  stringArray(
    retryPolicy.retryableFailureCodes,
    "run.retryPolicy.retryableFailureCodes",
    64,
    120
  );
  if (
    item.failurePolicy !==
    "fail_closed_preserve_effective_cast_snapshot"
  ) {
    throw invalid("The casting run failure policy was not fail-closed.");
  }
  nullableIdentifier(item.resumeOfCastingRunId, "run.resumeOfCastingRunId");
  nullableIdentifier(item.retryOfCastingRunId, "run.retryOfCastingRunId");
  enumeration(
    item.retryClassification,
    ["retryable", "not_retryable", "retry_exhausted"] as const,
    "run.retryClassification"
  );
  booleanValue(item.cancellationRequested, "run.cancellationRequested");
  validateWarnings(item.warnings, "run.warnings");
  if (item.summary !== null) {
    validateSummary(item.summary, "run.summary");
  }
  if (item.approvedCastSnapshot !== null) {
    validateSnapshot(
      item.approvedCastSnapshot,
      projectId,
      String(item.castingRunId),
      profileFingerprint
    );
  } else if (status === "succeeded") {
    throw invalid("A succeeded casting run must publish a cast snapshot.");
  }
  isoDate(item.createdAt, "run.createdAt");
  isoDate(item.updatedAt, "run.updatedAt");
  nullableDate(item.completedAt, "run.completedAt");
  if (item.failure !== null) {
    const failure = exact(item.failure, "run.failure", [
      "code",
      "redactedMessage",
      "retryable",
      "redacted"
    ]);
    text(failure.code, "run.failure.code", 1, 120);
    text(failure.redactedMessage, "run.failure.redactedMessage", 1, 1_000);
    booleanValue(failure.retryable, "run.failure.retryable");
    if (failure.redacted !== true) {
      throw invalid("Casting failure detail must be redacted.");
    }
  }
  return value as CastingRun;
}

function validatePrerequisites(value: unknown, projectId: string): void {
  const item = exact(value, "casting prerequisites", [
    "projectId",
    "sourceDocumentId",
    "sourceRevision",
    "extractionId",
    "extractionRevision",
    "extractedTextSha256",
    "importReviewDecisionId",
    "analysisRunId",
    "analysisSnapshotId",
    "analysisSnapshotRevision",
    "analysisSnapshotFingerprint",
    "analysisCorrectionSetFingerprint",
    "characterRegistryFingerprint",
    "phase2GateDecisionIds",
    "evidenceFingerprint"
  ]);
  if (identifier(item.projectId, "prerequisites.projectId") !== projectId) {
    throw invalid("Casting prerequisites belonged to another project.");
  }
  identifier(item.sourceDocumentId, "prerequisites.sourceDocumentId");
  positiveInteger(item.sourceRevision, "prerequisites.sourceRevision");
  identifier(item.extractionId, "prerequisites.extractionId");
  positiveInteger(item.extractionRevision, "prerequisites.extractionRevision");
  sha256(item.extractedTextSha256, "prerequisites.extractedTextSha256");
  identifier(
    item.importReviewDecisionId,
    "prerequisites.importReviewDecisionId"
  );
  identifier(item.analysisRunId, "prerequisites.analysisRunId");
  identifier(item.analysisSnapshotId, "prerequisites.analysisSnapshotId");
  positiveInteger(
    item.analysisSnapshotRevision,
    "prerequisites.analysisSnapshotRevision"
  );
  sha256(
    item.analysisSnapshotFingerprint,
    "prerequisites.analysisSnapshotFingerprint"
  );
  sha256(
    item.analysisCorrectionSetFingerprint,
    "prerequisites.analysisCorrectionSetFingerprint"
  );
  sha256(
    item.characterRegistryFingerprint,
    "prerequisites.characterRegistryFingerprint"
  );
  const gates = exact(item.phase2GateDecisionIds, "phase2GateDecisionIds", [
    "storyStructureReview",
    "characterRegistryReview",
    "dialogueAttributionReview",
    "wholeBookAnalysisReview"
  ]);
  for (const field of Object.keys(gates)) {
    identifier(gates[field], `phase2GateDecisionIds.${field}`);
  }
  sha256(item.evidenceFingerprint, "prerequisites.evidenceFingerprint");
}

function validateRole(
  value: unknown,
  input: CastingRunEvidenceInput
): ProductionRole {
  const item = object(value, "production role");
  const base = [
    "contractVersion",
    "roleId",
    "projectId",
    "roleType",
    "phase2EntityId",
    "effectiveDisplayLabel",
    "effectiveFingerprint",
    "analysisRunId",
    "analysisSnapshotId",
    "analysisSnapshotRevision",
    "analysisSnapshotFingerprint",
    "dialogueLineCount",
    "narrationSpanCount",
    "approximateWordCount",
    "range",
    "languageRequirements",
    "performanceRequirements",
    "warnings",
    "provenance",
    "status",
    "revision"
  ];
  const roleType = enumeration(item.roleType, CASTING_ROLE_TYPES, "role.roleType");
  const branch =
    roleType === "primary_narrator" || roleType === "secondary_narrator"
      ? ["narratorKind"]
      : [
          "characterId",
          "roleImportance",
          "unresolvedMaterialExplicitlyRepresented"
        ];
  allowed(item, [...base, ...branch]);
  required(item, [...base, ...branch]);
  contractVersion(item.contractVersion, "role.contractVersion");
  identifier(item.roleId, "role.roleId");
  if (
    identifier(item.projectId, "role.projectId") !== input.projectId ||
    identifier(item.analysisSnapshotId, "role.analysisSnapshotId") !==
      input.expectedSnapshotId ||
    positiveInteger(
      item.analysisSnapshotRevision,
      "role.analysisSnapshotRevision"
    ) !== input.expectedSnapshotRevision ||
    sha256(
      item.analysisSnapshotFingerprint,
      "role.analysisSnapshotFingerprint"
    ) !== input.expectedSnapshotFingerprint
  ) {
    throw invalid("The production role did not match requested snapshot ownership.");
  }
  nullableIdentifier(item.phase2EntityId, "role.phase2EntityId");
  text(item.effectiveDisplayLabel, "role.effectiveDisplayLabel", 1, 200);
  sha256(item.effectiveFingerprint, "role.effectiveFingerprint");
  identifier(item.analysisRunId, "role.analysisRunId");
  nonNegativeInteger(item.dialogueLineCount, "role.dialogueLineCount");
  nonNegativeInteger(item.narrationSpanCount, "role.narrationSpanCount");
  nonNegativeInteger(item.approximateWordCount, "role.approximateWordCount");
  validateRoleRange(item.range, "role.range");
  stringArray(item.languageRequirements, "role.languageRequirements", 32, 40);
  validateRequirement(item.performanceRequirements, "role.performanceRequirements");
  validateWarnings(item.warnings, "role.warnings");
  validateProvenance(item.provenance, "role.provenance");
  enumeration(
    item.status,
    ["active", "intentionally_uncast", "unresolved", "invalidated"] as const,
    "role.status"
  );
  positiveInteger(item.revision, "role.revision");
  if (branch[0] === "narratorKind") {
    enumeration(item.narratorKind, ["primary", "secondary"] as const, "role.narratorKind");
  } else {
    nullableIdentifier(item.characterId, "role.characterId");
    enumeration(
      item.roleImportance,
      ["major", "supporting", "minor", "unresolved"] as const,
      "role.roleImportance"
    );
    booleanValue(
      item.unresolvedMaterialExplicitlyRepresented,
      "role.unresolvedMaterialExplicitlyRepresented"
    );
  }
  return value as ProductionRole;
}

function validateCandidate(
  value: unknown,
  input: ListCastingCandidatesInput
): CastingCandidate {
  const item = exact(value, "casting candidate", [
    "contractVersion",
    "candidateId",
    "castingRunId",
    "roleId",
    "voiceProfileId",
    "rank",
    "preReductionRank",
    "assessment",
    "conflictIds",
    "conflictWarnings",
    "rejectedByCorrectionId",
    "provenance",
    "inputFingerprint",
    "baseEvidenceFingerprint",
    "outputFingerprint"
  ]);
  contractVersion(item.contractVersion, "candidate.contractVersion");
  identifier(item.candidateId, "candidate.candidateId");
  if (
    identifier(item.castingRunId, "candidate.castingRunId") !== input.runId ||
    identifier(item.roleId, "candidate.roleId") !== input.roleId
  ) {
    throw invalid("The casting candidate belonged to another run or role.");
  }
  identifier(item.voiceProfileId, "candidate.voiceProfileId");
  positiveInteger(item.rank, "candidate.rank");
  positiveInteger(item.preReductionRank, "candidate.preReductionRank");
  validateAssessment(item.assessment, input);
  identifierArray(item.conflictIds, "candidate.conflictIds", MAX_ARRAY);
  validateWarnings(item.conflictWarnings, "candidate.conflictWarnings");
  nullableIdentifier(
    item.rejectedByCorrectionId,
    "candidate.rejectedByCorrectionId"
  );
  validateProvenance(item.provenance, "candidate.provenance");
  sha256(item.inputFingerprint, "candidate.inputFingerprint");
  sha256(
    item.baseEvidenceFingerprint,
    "candidate.baseEvidenceFingerprint"
  );
  validateProjectionFingerprint(item, "candidate");
  return value as CastingCandidate;
}

function validateAssessment(
  value: unknown,
  input: ListCastingCandidatesInput
): void {
  const item = exact(value, "compatibility assessment", [
    "contractVersion",
    "assessmentId",
    "roleId",
    "voiceProfileId",
    "compatibilityStatus",
    "compatibilityScore",
    "confidence",
    "hardConstraints",
    "softPreferences",
    "rightsEligibility",
    "languageEligibility",
    "providerAvailability",
    "modelAvailability",
    "longFormSuitability",
    "explanation",
    "provenance",
    "inputFingerprint",
    "baseEvidenceFingerprint",
    "outputFingerprint"
  ]);
  contractVersion(item.contractVersion, "assessment.contractVersion");
  identifier(item.assessmentId, "assessment.assessmentId");
  if (identifier(item.roleId, "assessment.roleId") !== input.roleId) {
    throw invalid("The compatibility assessment belonged to another role.");
  }
  identifier(item.voiceProfileId, "assessment.voiceProfileId");
  const status = enumeration(
    item.compatibilityStatus,
    ["compatible", "compatible_with_warnings", "incompatible", "unknown"] as const,
    "assessment.compatibilityStatus"
  );
  number(item.compatibilityScore, "assessment.compatibilityScore", 0, 1);
  validateConfidence(item.confidence, "assessment.confidence");
  const hard = boundedArray(
    item.hardConstraints,
    "assessment.hardConstraints",
    CASTING_HARD_CONSTRAINT_IDS.length
  ).map((resultValue, index) => {
    const result = exact(resultValue, `hardConstraints[${index}]`, [
      "constraintId",
      "result",
      "explanation"
    ]);
    enumeration(
      result.constraintId,
      CASTING_HARD_CONSTRAINT_IDS,
      `hardConstraints[${index}].constraintId`
    );
    const outcome = enumeration(
      result.result,
      ["pass", "fail", "unknown"] as const,
      `hardConstraints[${index}].result`
    );
    text(
      result.explanation,
      `hardConstraints[${index}].explanation`,
      1,
      CASTING_LIMITS.maximumExplanationCodePoints
    );
    return outcome;
  });
  boundedArray(
    item.softPreferences,
    "assessment.softPreferences",
    CASTING_SOFT_PREFERENCE_IDS.length
  ).forEach((preferenceValue, index) => {
    const preference = exact(preferenceValue, `softPreferences[${index}]`, [
      "preferenceId",
      "score",
      "explanation"
    ]);
    enumeration(
      preference.preferenceId,
      CASTING_SOFT_PREFERENCE_IDS,
      `softPreferences[${index}].preferenceId`
    );
    number(preference.score, `softPreferences[${index}].score`, 0, 1);
    text(
      preference.explanation,
      `softPreferences[${index}].explanation`,
      1,
      CASTING_LIMITS.maximumExplanationCodePoints
    );
  });
  enumeration(
    item.rightsEligibility,
    [
      "eligible",
      "restricted_requires_acknowledgement",
      "ineligible_unknown",
      "ineligible_prohibited"
    ] as const,
    "assessment.rightsEligibility"
  );
  enumeration(
    item.languageEligibility,
    ["pass", "fail", "unknown"] as const,
    "assessment.languageEligibility"
  );
  availability(item.providerAvailability, "assessment.providerAvailability");
  availability(item.modelAvailability, "assessment.modelAvailability");
  suitability(item.longFormSuitability, "assessment.longFormSuitability");
  text(
    item.explanation,
    "assessment.explanation",
    1,
    CASTING_LIMITS.maximumExplanationCodePoints
  );
  validateProvenance(item.provenance, "assessment.provenance");
  sha256(item.inputFingerprint, "assessment.inputFingerprint");
  sha256(
    item.baseEvidenceFingerprint,
    "assessment.baseEvidenceFingerprint"
  );
  validateProjectionFingerprint(item, "assessment");
  if (
    (status === "compatible" && hard.some((result) => result !== "pass")) ||
    (status === "unknown" && !hard.some((result) => result === "unknown"))
  ) {
    throw invalid("Compatibility status did not agree with hard constraints.");
  }
}

function validateConflict(
  value: unknown,
  input: CastingRunEvidenceInput
): CastingConflict {
  const item = exact(value, "casting conflict", [
    "contractVersion",
    "conflictId",
    "castingRunId",
    "category",
    "severity",
    "roleIds",
    "voiceProfileIds",
    "explanation",
    "metadataOnly",
    "acousticSimilarityClaimed",
    "resolutionState",
    "dispositionCorrectionId",
    "provenance",
    "inputFingerprint",
    "baseEvidenceFingerprint",
    "outputFingerprint"
  ]);
  contractVersion(item.contractVersion, "conflict.contractVersion");
  identifier(item.conflictId, "conflict.conflictId");
  if (identifier(item.castingRunId, "conflict.castingRunId") !== input.runId) {
    throw invalid("The casting conflict belonged to another run.");
  }
  enumeration(item.category, CASTING_CONFLICT_CATEGORIES, "conflict.category");
  enumeration(
    item.severity,
    ["info", "warning", "error", "blocker"] as const,
    "conflict.severity"
  );
  const roleIds = identifierArray(
    item.roleIds,
    "conflict.roleIds",
    CASTING_LIMITS.maximumProductionRoles
  );
  if (roleIds.length === 0) {
    throw invalid("conflict.roleIds must have at least one item.");
  }
  identifierArray(
    item.voiceProfileIds,
    "conflict.voiceProfileIds",
    CASTING_LIMITS.maximumProductionRoles
  );
  text(
    item.explanation,
    "conflict.explanation",
    1,
    CASTING_LIMITS.maximumExplanationCodePoints
  );
  if (item.metadataOnly !== true || item.acousticSimilarityClaimed !== false) {
    throw invalid("Phase 3A conflicts must be metadata-only.");
  }
  enumeration(
    item.resolutionState,
    ["open", "acknowledged", "approved_reuse", "resolved", "superseded"] as const,
    "conflict.resolutionState"
  );
  nullableIdentifier(
    item.dispositionCorrectionId,
    "conflict.dispositionCorrectionId"
  );
  validateProvenance(item.provenance, "conflict.provenance");
  sha256(item.inputFingerprint, "conflict.inputFingerprint");
  sha256(
    item.baseEvidenceFingerprint,
    "conflict.baseEvidenceFingerprint"
  );
  validateProjectionFingerprint(item, "conflict");
  return value as CastingConflict;
}

function validateAssignment(
  value: unknown,
  input: CastingRunEvidenceInput
): CastAssignment {
  const item = exact(value, "cast assignment", [
    "contractVersion",
    "assignmentId",
    "projectId",
    "roleId",
    "voiceProfileId",
    "voiceProfileVersion",
    "voiceEvidenceFingerprint",
    "rightsRecordId",
    "rightsRecordRevision",
    "rightsEvidenceFingerprint",
    "catalogRevisionId",
    "castingRunId",
    "castingProfileFingerprint",
    "phase2SnapshotFingerprint",
    "effectiveCorrectionSetFingerprint",
    "authority",
    "rationale",
    "warnings",
    "rightsState",
    "revision",
    "provenance",
    "supersedesAssignmentId",
    "effective"
  ]);
  contractVersion(item.contractVersion, "assignment.contractVersion");
  identifier(item.assignmentId, "assignment.assignmentId");
  if (
    identifier(item.projectId, "assignment.projectId") !== input.projectId ||
    identifier(item.castingRunId, "assignment.castingRunId") !== input.runId ||
    identifier(item.catalogRevisionId, "assignment.catalogRevisionId") !==
      input.expectedCatalogRevisionId ||
    sha256(
      item.phase2SnapshotFingerprint,
      "assignment.phase2SnapshotFingerprint"
    ) !== input.expectedSnapshotFingerprint
  ) {
    throw invalid("The assignment did not match requested casting evidence.");
  }
  identifier(item.roleId, "assignment.roleId");
  identifier(item.voiceProfileId, "assignment.voiceProfileId");
  semver(item.voiceProfileVersion, "assignment.voiceProfileVersion");
  sha256(
    item.voiceEvidenceFingerprint,
    "assignment.voiceEvidenceFingerprint"
  );
  identifier(item.rightsRecordId, "assignment.rightsRecordId");
  positiveInteger(
    item.rightsRecordRevision,
    "assignment.rightsRecordRevision"
  );
  sha256(
    item.rightsEvidenceFingerprint,
    "assignment.rightsEvidenceFingerprint"
  );
  if (
    castingProfileFingerprint(
      item.castingProfileFingerprint,
      "assignment.castingProfileFingerprint"
    ) !== input.expectedCastingProfileFingerprint
  ) {
    throw invalid("The assignment used another casting profile.");
  }
  sha256(
    item.effectiveCorrectionSetFingerprint,
    "assignment.effectiveCorrectionSetFingerprint"
  );
  enumeration(
    item.authority,
    ["machine_proposal", "human_selection", "human_locked"] as const,
    "assignment.authority"
  );
  text(item.rationale, "assignment.rationale", 1, 2_000);
  validateWarnings(item.warnings, "assignment.warnings");
  rightsState(item.rightsState, "assignment.rightsState");
  positiveInteger(item.revision, "assignment.revision");
  validateProvenance(item.provenance, "assignment.provenance");
  nullableIdentifier(
    item.supersedesAssignmentId,
    "assignment.supersedesAssignmentId"
  );
  booleanValue(item.effective, "assignment.effective");
  return value as CastAssignment;
}

function validateCorrection(
  value: unknown,
  input: CastingRunEvidenceInput
): CastingCorrection {
  const item = object(value, "casting correction");
  const base = [
    "contractVersion",
    "correctionId",
    "projectId",
    "castingRunId",
    "targetRoleId",
    "priorEffectiveFingerprint",
    "correctedValueFingerprint",
    "actor",
    "reason",
    "recordedAt",
    "provenance",
    "immutable",
    "lockedAgainstAutomation",
    "supersedesCorrectionId",
    "idempotencyFingerprint",
    "category",
    "correctedValue"
  ];
  allowed(item, base);
  required(item, base);
  contractVersion(item.contractVersion, "correction.contractVersion");
  identifier(item.correctionId, "correction.correctionId");
  if (
    identifier(item.projectId, "correction.projectId") !== input.projectId ||
    identifier(item.castingRunId, "correction.castingRunId") !== input.runId
  ) {
    throw invalid("The correction belonged to another project or casting run.");
  }
  identifier(item.targetRoleId, "correction.targetRoleId");
  sha256(
    item.priorEffectiveFingerprint,
    "correction.priorEffectiveFingerprint"
  );
  sha256(
    item.correctedValueFingerprint,
    "correction.correctedValueFingerprint"
  );
  const actor = exact(item.actor, "correction.actor", [
    "classification",
    "actorId"
  ]);
  if (actor.classification !== "human") {
    throw invalid("Casting corrections must have human authority.");
  }
  identifier(actor.actorId, "correction.actor.actorId");
  text(
    item.reason,
    "correction.reason",
    1,
    CASTING_LIMITS.maximumCorrectionReasonCodePoints
  );
  isoDate(item.recordedAt, "correction.recordedAt");
  validateProvenance(item.provenance, "correction.provenance");
  if (item.immutable !== true || item.lockedAgainstAutomation !== true) {
    throw invalid("Casting corrections must be immutable and automation-locked.");
  }
  nullableIdentifier(
    item.supersedesCorrectionId,
    "correction.supersedesCorrectionId"
  );
  sha256(item.idempotencyFingerprint, "correction.idempotencyFingerprint");
  const category = enumeration(
    item.category,
    CASTING_CORRECTION_OPERATIONS,
    "correction.category"
  );
  parseCorrectedValue(category, item.correctedValue);
  return value as CastingCorrection;
}

function validateReview(
  value: unknown,
  input: CastingRunEvidenceInput
): CastingGateReview {
  const item = exact(value, "casting gate review", [
    "contractVersion",
    "reviewId",
    "gateId",
    "projectId",
    "castingRunId",
    "state",
    "revision",
    "prerequisiteGateIds",
    "evidence",
    "openWarningIds",
    "acknowledgedWarningIds",
    "latestDecision",
    "provenance",
    "updatedAt"
  ]);
  contractVersion(item.contractVersion, "review.contractVersion");
  identifier(item.reviewId, "review.reviewId");
  const gateId = enumeration(item.gateId, CASTING_GATE_IDS, "review.gateId");
  if (
    identifier(item.projectId, "review.projectId") !== input.projectId ||
    identifier(item.castingRunId, "review.castingRunId") !== input.runId
  ) {
    throw invalid("The casting review belonged to another run.");
  }
  const state = enumeration(
    item.state,
    ["pending", "approved", "changes_requested", "rejected", "invalidated"] as const,
    "review.state"
  );
  positiveInteger(item.revision, "review.revision");
  const prerequisiteGateIds = enumArray(
    item.prerequisiteGateIds,
    "review.prerequisiteGateIds",
    CASTING_GATE_IDS,
    CASTING_GATE_IDS.length
  );
  const expectedPrerequisites =
    gateId === "complete_cast_review"
      ? ([
          "narrator_casting_review",
          "character_casting_review"
        ] as const)
      : ([] as const);
  if (
    prerequisiteGateIds.length !== expectedPrerequisites.length ||
    expectedPrerequisites.some(
      (expected) => !prerequisiteGateIds.includes(expected)
    )
  ) {
    throw invalid("The casting review gate dependencies were inconsistent.");
  }
  validateGateEvidence(item.evidence, input);
  identifierArray(item.openWarningIds, "review.openWarningIds", MAX_WARNINGS);
  identifierArray(
    item.acknowledgedWarningIds,
    "review.acknowledgedWarningIds",
    MAX_WARNINGS
  );
  if (item.latestDecision === null) {
    if (state !== "pending" && state !== "invalidated") {
      throw invalid("A decided review must retain its latest decision.");
    }
  } else {
    const decision = validateDecision(item.latestDecision, input);
    if (decision.gateId !== gateId || decision.decision !== state) {
      throw invalid("The review state and latest decision were inconsistent.");
    }
  }
  validateProvenance(item.provenance, "review.provenance");
  isoDate(item.updatedAt, "review.updatedAt");
  return value as CastingGateReview;
}

function validateDecision(
  value: unknown,
  input: CastingRunEvidenceInput
): CastingReviewDecision {
  const item = exact(value, "casting decision", [
    "contractVersion",
    "decisionId",
    "reviewId",
    "gateId",
    "projectId",
    "castingRunId",
    "approvedCastSnapshotId",
    "approvedCastSnapshotRevision",
    "evidenceFingerprint",
    "decision",
    "actor",
    "acknowledgedWarningIds",
    "rationale",
    "decidedAt",
    "provenance",
    "immutable",
    "supersedesDecisionId"
  ]);
  contractVersion(item.contractVersion, "decision.contractVersion");
  identifier(item.decisionId, "decision.decisionId");
  identifier(item.reviewId, "decision.reviewId");
  enumeration(item.gateId, CASTING_GATE_IDS, "decision.gateId");
  if (
    identifier(item.projectId, "decision.projectId") !== input.projectId ||
    identifier(item.castingRunId, "decision.castingRunId") !== input.runId
  ) {
    throw invalid("The casting decision belonged to another run.");
  }
  identifier(
    item.approvedCastSnapshotId,
    "decision.approvedCastSnapshotId"
  );
  positiveInteger(
    item.approvedCastSnapshotRevision,
    "decision.approvedCastSnapshotRevision"
  );
  sha256(item.evidenceFingerprint, "decision.evidenceFingerprint");
  enumeration(
    item.decision,
    ["approved", "changes_requested", "rejected", "invalidated"] as const,
    "decision.decision"
  );
  const actor = exact(item.actor, "decision.actor", [
    "classification",
    "actorId"
  ]);
  enumeration(
    actor.classification,
    ["human", "system"] as const,
    "decision.actor.classification"
  );
  if (
    (item.decision === "invalidated" &&
      actor.classification !== "system") ||
    (item.decision !== "invalidated" &&
      actor.classification !== "human")
  ) {
    throw invalid(
      "Only the system may invalidate a casting review; all other decisions require human authority."
    );
  }
  identifier(actor.actorId, "decision.actor.actorId");
  identifierArray(
    item.acknowledgedWarningIds,
    "decision.acknowledgedWarningIds",
    MAX_WARNINGS
  );
  text(item.rationale, "decision.rationale", 1, 4_000);
  isoDate(item.decidedAt, "decision.decidedAt");
  validateProvenance(item.provenance, "decision.provenance");
  if (item.immutable !== true) {
    throw invalid("Casting review decisions must be immutable.");
  }
  nullableIdentifier(
    item.supersedesDecisionId,
    "decision.supersedesDecisionId"
  );
  return value as CastingReviewDecision;
}

function validateGateEvidence(
  value: unknown,
  input: CastingRunEvidenceInput
): void {
  const item = exact(value, "casting gate evidence", [
    "projectId",
    "castingRunId",
    "approvedCastSnapshotId",
    "approvedCastSnapshotRevision",
    "approvedCastSnapshotFingerprint",
    "phase2SnapshotFingerprint",
    "catalogRevisionId",
    "catalogFingerprint",
    "castingProfileFingerprint",
    "effectiveCorrectionSetFingerprint",
    "evidenceFingerprint"
  ]);
  if (
    identifier(item.projectId, "evidence.projectId") !== input.projectId ||
    identifier(item.castingRunId, "evidence.castingRunId") !== input.runId ||
    identifier(item.catalogRevisionId, "evidence.catalogRevisionId") !==
      input.expectedCatalogRevisionId ||
    sha256(item.catalogFingerprint, "evidence.catalogFingerprint") !==
      input.expectedCatalogFingerprint ||
    sha256(
      item.phase2SnapshotFingerprint,
      "evidence.phase2SnapshotFingerprint"
    ) !== input.expectedSnapshotFingerprint ||
    castingProfileFingerprint(
      item.castingProfileFingerprint,
      "evidence.castingProfileFingerprint"
    ) !== input.expectedCastingProfileFingerprint
  ) {
    throw invalid("The casting review evidence did not match requested ownership.");
  }
  identifier(
    item.approvedCastSnapshotId,
    "evidence.approvedCastSnapshotId"
  );
  positiveInteger(
    item.approvedCastSnapshotRevision,
    "evidence.approvedCastSnapshotRevision"
  );
  sha256(
    item.approvedCastSnapshotFingerprint,
    "evidence.approvedCastSnapshotFingerprint"
  );
  sha256(
    item.effectiveCorrectionSetFingerprint,
    "evidence.effectiveCorrectionSetFingerprint"
  );
  sha256(item.evidenceFingerprint, "evidence.evidenceFingerprint");
}

function validateSnapshot(
  value: unknown,
  projectId: string,
  runId: string,
  expectedCastingProfileFingerprint: string
) {
  const item = exact(value, "approved cast snapshot", [
    "contractVersion",
    "snapshotId",
    "castingRunId",
    "projectId",
    "revision",
    "phase2SnapshotFingerprint",
    "catalogRevisionId",
    "catalogFingerprint",
    "castingProfileFingerprint",
    "effectiveCorrectionSetFingerprint",
    "assignmentIds",
    "intentionallyUncastRoleIds",
    "unresolvedConflictIds",
    "counts",
    "snapshotFingerprint",
    "reviewEligible",
    "createdAt",
    "immutable"
  ]);
  contractVersion(item.contractVersion, "snapshot.contractVersion");
  identifier(item.snapshotId, "snapshot.snapshotId");
  if (
    identifier(item.castingRunId, "snapshot.castingRunId") !== runId ||
    identifier(item.projectId, "snapshot.projectId") !== projectId
  ) {
    throw invalid("The cast snapshot belonged to another project or run.");
  }
  positiveInteger(item.revision, "snapshot.revision");
  sha256(
    item.phase2SnapshotFingerprint,
    "snapshot.phase2SnapshotFingerprint"
  );
  identifier(item.catalogRevisionId, "snapshot.catalogRevisionId");
  sha256(item.catalogFingerprint, "snapshot.catalogFingerprint");
  if (
    sha256(
      item.castingProfileFingerprint,
      "snapshot.castingProfileFingerprint"
    ) !== expectedCastingProfileFingerprint
  ) {
    throw invalid("The cast snapshot used another casting profile.");
  }
  sha256(
    item.effectiveCorrectionSetFingerprint,
    "snapshot.effectiveCorrectionSetFingerprint"
  );
  identifierArray(item.assignmentIds, "snapshot.assignmentIds", 300);
  identifierArray(
    item.intentionallyUncastRoleIds,
    "snapshot.intentionallyUncastRoleIds",
    300
  );
  identifierArray(
    item.unresolvedConflictIds,
    "snapshot.unresolvedConflictIds",
    10_000
  );
  validateSummary(item.counts, "snapshot.counts");
  sha256(item.snapshotFingerprint, "snapshot.snapshotFingerprint");
  booleanValue(item.reviewEligible, "snapshot.reviewEligible");
  isoDate(item.createdAt, "snapshot.createdAt");
  if (item.immutable !== true) {
    throw invalid("Approved cast snapshots must be immutable.");
  }
  return value as CastingRun["approvedCastSnapshot"] extends infer T
    ? NonNullable<T>
    : never;
}

function validateSummary(value: unknown, field: string): void {
  const item = exact(value, field, [
    "productionRoles",
    "narratorRoles",
    "characterRoles",
    "preReductionCandidates",
    "finalCandidates",
    "conflicts",
    "assignments",
    "corrections"
  ]);
  for (const [key, count] of Object.entries(item)) {
    nonNegativeInteger(count, `${field}.${key}`);
  }
  const productionRoles = Number(item.productionRoles);
  if (
    Number(item.narratorRoles) + Number(item.characterRoles) >
    productionRoles
  ) {
    throw invalid(`${field} role counts were inconsistent.`);
  }
}

function validateReviewSet(items: readonly CastingGateReview[]): void {
  if (items.length !== CASTING_GATE_IDS.length) {
    throw invalid("The casting response must include every approval gate.");
  }
  unique(items.map((item) => item.gateId), "casting review gate IDs");
  const complete = items.find((item) => item.gateId === "complete_cast_review");
  if (complete?.state === "approved") {
    for (const gateId of [
      "narrator_casting_review",
      "character_casting_review"
    ] as const) {
      if (!items.some((item) => item.gateId === gateId && item.state === "approved")) {
        throw invalid("Complete Cast Review requires both prerequisite approvals.");
      }
    }
  }
}

function validateRequirement(
  value: unknown,
  field: string
): ProductionRoleRequirement {
  const item = exact(value, field, [
    "language",
    "locales",
    "agePresentationRange",
    "vocalPresentations",
    "preferredTextures",
    "speakingRateRange",
    "requiredExpressiveRange",
    "longFormRequired"
  ]);
  text(item.language, `${field}.language`, 1, 40);
  stringArray(item.locales, `${field}.locales`, 32, 40);
  if (item.agePresentationRange !== null) {
    validateIntegerRange(
      item.agePresentationRange,
      `${field}.agePresentationRange`,
      0,
      130
    );
  }
  enumArray(
    item.vocalPresentations,
    `${field}.vocalPresentations`,
    [
      "feminine",
      "masculine",
      "androgynous",
      "neutral",
      "varied",
      "unspecified"
    ] as const,
    MAX_REQUIREMENT_VALUES
  );
  enumArray(
    item.preferredTextures,
    `${field}.preferredTextures`,
    [
      "airy",
      "bright",
      "clear",
      "crisp",
      "gravelly",
      "resonant",
      "smooth",
      "warm",
      "textured",
      "varied",
      "unspecified"
    ] as const,
    MAX_REQUIREMENT_VALUES
  );
  if (item.speakingRateRange !== null) {
    validateUnitRange(
      item.speakingRateRange,
      `${field}.speakingRateRange`,
      0.25,
      4
    );
  }
  stringArray(
    item.requiredExpressiveRange,
    `${field}.requiredExpressiveRange`,
    MAX_REQUIREMENT_VALUES,
    80
  );
  booleanValue(item.longFormRequired, `${field}.longFormRequired`);
  return value as ProductionRoleRequirement;
}

function sameRequirement(
  left: ProductionRoleRequirement,
  right: ProductionRoleRequirement
): boolean {
  return (
    left.language === right.language &&
    sameArray(left.locales, right.locales) &&
    sameNullableRange(
      left.agePresentationRange,
      right.agePresentationRange
    ) &&
    sameArray(left.vocalPresentations, right.vocalPresentations) &&
    sameArray(left.preferredTextures, right.preferredTextures) &&
    sameNullableUnitRange(
      left.speakingRateRange,
      right.speakingRateRange
    ) &&
    sameArray(
      left.requiredExpressiveRange,
      right.requiredExpressiveRange
    ) &&
    left.longFormRequired === right.longFormRequired
  );
}

function sameArray<T>(
  left: readonly T[],
  right: readonly T[]
): boolean {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

function sameNullableRange(
  left: { readonly minimum: number; readonly maximum: number } | null,
  right: { readonly minimum: number; readonly maximum: number } | null
): boolean {
  return (
    (left === null && right === null) ||
    (left !== null &&
      right !== null &&
      left.minimum === right.minimum &&
      left.maximum === right.maximum)
  );
}

function sameNullableUnitRange(
  left:
    | {
        readonly minimum: number;
        readonly maximum: number;
        readonly unit: "multiplier";
      }
    | null,
  right:
    | {
        readonly minimum: number;
        readonly maximum: number;
        readonly unit: "multiplier";
      }
    | null
): boolean {
  return (
    sameNullableRange(left, right) &&
    (left === null ||
      right === null ||
      left.unit === right.unit)
  );
}

function validateRoleRange(value: unknown, field: string): void {
  const item = exact(value, field, [
    "firstChapterOrdinal",
    "lastChapterOrdinal",
    "firstSceneOrdinal",
    "lastSceneOrdinal"
  ]);
  for (const key of Object.keys(item)) {
    if (item[key] !== null) {
      nonNegativeInteger(item[key], `${field}.${key}`);
    }
  }
}

function validateProvenance(value: unknown, field: string): CastingProvenance {
  const item = object(value, field);
  allowed(item, [
    "origin",
    "producerId",
    "producerVersion",
    "recordedAt",
    "inputFingerprint",
    "sourceRevisionId",
    "reason"
  ]);
  required(item, ["origin", "producerId", "producerVersion", "recordedAt"]);
  enumeration(
    item.origin,
    ["development_fixture", "local_catalog", "runtime_agent", "human", "system"] as const,
    `${field}.origin`
  );
  identifier(item.producerId, `${field}.producerId`);
  semver(item.producerVersion, `${field}.producerVersion`);
  isoDate(item.recordedAt, `${field}.recordedAt`);
  if (item.inputFingerprint !== undefined) {
    sha256(item.inputFingerprint, `${field}.inputFingerprint`);
  }
  if (item.sourceRevisionId !== undefined) {
    identifier(item.sourceRevisionId, `${field}.sourceRevisionId`);
  }
  if (item.reason !== undefined) {
    text(item.reason, `${field}.reason`, 1, 1_000);
  }
  return value as CastingProvenance;
}

function validateWarnings(value: unknown, field: string): readonly AnalysisWarning[] {
  const warnings = boundedArray(value, field, MAX_WARNINGS);
  warnings.forEach((warningValue, index) => {
    const warning = exact(warningValue, `${field}[${index}]`, [
      "code",
      "severity",
      "message",
      "requiresHumanReview",
      "relatedEntityIds",
      "evidence"
    ]);
    text(warning.code, `${field}[${index}].code`, 1, 120);
    enumeration(
      warning.severity,
      ["info", "warning", "error", "blocker"] as const,
      `${field}[${index}].severity`
    );
    text(warning.message, `${field}[${index}].message`, 1, 1_000);
    booleanValue(
      warning.requiresHumanReview,
      `${field}[${index}].requiresHumanReview`
    );
    identifierArray(
      warning.relatedEntityIds,
      `${field}[${index}].relatedEntityIds`,
      16
    );
    if (boundedArray(warning.evidence, `${field}[${index}].evidence`, 0).length) {
      throw invalid("Casting list responses must not contain manuscript evidence.");
    }
  });
  return value as readonly AnalysisWarning[];
}

function validateConfidence(value: unknown, field: string): void {
  const item = object(value, field);
  allowed(item, ["score", "classification", "basis", "calibrationId"]);
  required(item, ["score", "classification", "basis"]);
  number(item.score, `${field}.score`, 0, 1);
  enumeration(
    item.classification,
    ["unknown", "low", "medium", "high"] as const,
    `${field}.classification`
  );
  text(item.basis, `${field}.basis`, 1, 500);
  if (item.calibrationId !== undefined) {
    identifier(item.calibrationId, `${field}.calibrationId`);
  }
}

function validateJob(
  value: unknown,
  projectId: string,
  jobId: string,
  castingRunId: string
): void {
  const item = object(value, "job");
  allowed(item, [
    "jobId",
    "projectId",
    "type",
    "state",
    "target",
    "inputRevision",
    "inputFingerprint",
    "attempt",
    "stage",
    "progress",
    "checkpointAvailable",
    "cancellationRequested",
    "warnings",
    "error",
    "createdAt",
    "updatedAt",
    "terminalAt"
  ]);
  required(item, [
    "jobId",
    "projectId",
    "type",
    "state",
    "target",
    "inputRevision",
    "inputFingerprint",
    "attempt",
    "stage",
    "progress",
    "checkpointAvailable",
    "cancellationRequested",
    "warnings",
    "createdAt",
    "updatedAt"
  ]);
  if (
    identifier(item.jobId, "job.jobId") !== jobId ||
    identifier(item.projectId, "job.projectId") !== projectId
  ) {
    throw invalid("The casting job belonged to another project or run.");
  }
  if (item.type !== "analyze_casting") {
    throw invalid("The casting run must be backed by an analyze_casting job.");
  }
  const target = exact(item.target, "job.target", ["type", "id"]);
  if (
    target.type !== "casting_run" ||
    identifier(target.id, "job.target.id") !== castingRunId
  ) {
    throw invalid("The casting job target did not match the created run.");
  }
  enumeration(
    item.state,
    [
      "queued",
      "running",
      "cancel_requested",
      "cancelled",
      "failed",
      "interrupted",
      "paused",
      "succeeded"
    ] as const,
    "job.state"
  );
  nonNegativeInteger(item.inputRevision, "job.inputRevision");
  sha256(item.inputFingerprint, "job.inputFingerprint");
  positiveInteger(item.attempt, "job.attempt");
  text(item.stage, "job.stage", 1, 120);
  number(item.progress, "job.progress", 0, 1);
  booleanValue(item.checkpointAvailable, "job.checkpointAvailable");
  booleanValue(item.cancellationRequested, "job.cancellationRequested");
  boundedArray(item.warnings, "job.warnings", MAX_WARNINGS);
  if (item.error !== undefined) {
    const error = exact(item.error, "job.error", [
      "code",
      "message",
      "retryable"
    ]);
    text(error.code, "job.error.code", 1, 120);
    text(error.message, "job.error.message", 1, 1_000);
    booleanValue(error.retryable, "job.error.retryable");
  }
  isoDate(item.createdAt, "job.createdAt");
  isoDate(item.updatedAt, "job.updatedAt");
  if (item.terminalAt !== undefined) {
    isoDate(item.terminalAt, "job.terminalAt");
  }
}

function validateOutputCapability(value: unknown, field: string): void {
  const item = exact(value, field, ["formats", "sampleRatesHz"]);
  enumArray(
    item.formats,
    `${field}.formats`,
    ["pcm_s16le", "wav", "mp3", "unknown"] as const,
    4
  );
  boundedArray(item.sampleRatesHz, `${field}.sampleRatesHz`, 32).forEach(
    (rate, index) =>
      integer(rate, `${field}.sampleRatesHz[${index}]`, 1, 384_000)
  );
}

function parseCorrectedValue(
  operation: CastingCorrectionOperation,
  value: unknown
): CastingCorrectedValue {
  const item = object(value, "correctedValue");
  switch (operation) {
    case "select_voice":
      allowed(item, ["voiceProfileId"]);
      required(item, ["voiceProfileId"]);
      return {
        voiceProfileId: identifier(
          item.voiceProfileId,
          "correctedValue.voiceProfileId"
        )
      };
    case "clear_assignment":
      allowed(item, ["expectedAssignmentId"]);
      required(item, ["expectedAssignmentId"]);
      return {
        expectedAssignmentId: identifier(
          item.expectedAssignmentId,
          "correctedValue.expectedAssignmentId"
        )
      };
    case "lock_assignment":
      allowed(item, ["assignmentId"]);
      required(item, ["assignmentId"]);
      return {
        assignmentId: identifier(
          item.assignmentId,
          "correctedValue.assignmentId"
        )
      };
    case "unlock_assignment":
      allowed(item, ["lockedAssignmentId"]);
      required(item, ["lockedAssignmentId"]);
      return {
        lockedAssignmentId: identifier(
          item.lockedAssignmentId,
          "correctedValue.lockedAssignmentId"
        )
      };
    case "mark_intentionally_uncast":
      allowed(item, ["intentionallyUncast"]);
      required(item, ["intentionallyUncast"]);
      if (item.intentionallyUncast !== true) {
        throw invalid("intentionallyUncast must be true.");
      }
      return { intentionallyUncast: true };
    case "change_role_label":
      allowed(item, ["effectiveDisplayLabel"]);
      required(item, ["effectiveDisplayLabel"]);
      return {
        effectiveDisplayLabel: text(
          item.effectiveDisplayLabel,
          "correctedValue.effectiveDisplayLabel",
          1,
          200
        )
      };
    case "change_casting_requirement":
      allowed(item, ["requirement"]);
      required(item, ["requirement"]);
      return {
        requirement: validateRequirement(
          item.requirement,
          "correctedValue.requirement"
        )
      };
    case "acknowledge_restricted_rights":
      allowed(item, ["rightsRecordId", "rightsRecordRevision"]);
      required(item, ["rightsRecordId", "rightsRecordRevision"]);
      return {
        rightsRecordId: identifier(
          item.rightsRecordId,
          "correctedValue.rightsRecordId"
        ),
        rightsRecordRevision: positiveInteger(
          item.rightsRecordRevision,
          "correctedValue.rightsRecordRevision"
        )
      };
    case "approve_voice_reuse": {
      allowed(item, ["conflictId", "approvedRoleIds"]);
      required(item, ["conflictId", "approvedRoleIds"]);
      const approvedRoleIds = identifierArray(
        item.approvedRoleIds,
        "correctedValue.approvedRoleIds",
        CASTING_LIMITS.maximumProductionRoles
      );
      if (approvedRoleIds.length < 2) {
        throw invalid(
          "correctedValue.approvedRoleIds must have at least two items."
        );
      }
      return {
        conflictId: identifier(
          item.conflictId,
          "correctedValue.conflictId"
        ),
        approvedRoleIds
      };
    }
    case "reject_candidate":
      allowed(item, ["candidateId"]);
      required(item, ["candidateId"]);
      return {
        candidateId: identifier(
          item.candidateId,
          "correctedValue.candidateId"
        )
      };
    case "record_custom_rationale":
      allowed(item, ["rationale"]);
      required(item, ["rationale"]);
      return {
        rationale: text(
          item.rationale,
          "correctedValue.rationale",
          1,
          CASTING_LIMITS.maximumCustomRationaleCodePoints
        )
      };
  }
}

function parseEvidencePage(
  value: unknown,
  maximum: number
): CastingRunEvidenceInput & { readonly cursor?: string; readonly limit?: number } {
  const payload = parseEnvelope(value);
  allowed(payload, [...runEvidenceFields, "cursor", "limit"]);
  required(payload, runEvidenceFields);
  return { ...parseEvidence(payload), ...pageInput(payload, maximum) };
}

function parseEvidence(
  payload: Record<string, unknown>
): CastingRunEvidenceInput {
  return {
    projectId: identifier(payload.projectId, "projectId"),
    runId: identifier(payload.runId, "runId"),
    expectedRunFingerprint: sha256(
      payload.expectedRunFingerprint,
      "expectedRunFingerprint"
    ),
    expectedCatalogRevisionId: identifier(
      payload.expectedCatalogRevisionId,
      "expectedCatalogRevisionId"
    ),
    expectedCatalogFingerprint: sha256(
      payload.expectedCatalogFingerprint,
      "expectedCatalogFingerprint"
    ),
    expectedSnapshotId: identifier(
      payload.expectedSnapshotId,
      "expectedSnapshotId"
    ),
    expectedSnapshotRevision: positiveInteger(
      payload.expectedSnapshotRevision,
      "expectedSnapshotRevision"
    ),
    expectedSnapshotFingerprint: sha256(
      payload.expectedSnapshotFingerprint,
      "expectedSnapshotFingerprint"
    ),
    expectedCastingProfileFingerprint: castingProfileFingerprint(
      payload.expectedCastingProfileFingerprint,
      "expectedCastingProfileFingerprint"
    )
  };
}

function assertCreateEvidence(
  run: CastingRun,
  input: CreateCastingRunInput
): void {
  const prerequisites = run.prerequisites;
  const gates = prerequisites.phase2GateDecisionIds;
  if (
    prerequisites.analysisRunId !== input.expectedAnalysisRunId ||
    prerequisites.analysisSnapshotId !== input.expectedSnapshotId ||
    prerequisites.analysisSnapshotRevision !== input.expectedSnapshotRevision ||
    prerequisites.analysisSnapshotFingerprint !==
      input.expectedSnapshotFingerprint ||
    prerequisites.analysisCorrectionSetFingerprint !==
      input.expectedCorrectionSetFingerprint ||
    prerequisites.importReviewDecisionId !==
      input.expectedImportReviewDecisionId ||
    gates.storyStructureReview !==
      input.expectedAnalysisGateDecisionIds.storyStructureReview ||
    gates.characterRegistryReview !==
      input.expectedAnalysisGateDecisionIds.characterRegistryReview ||
    gates.dialogueAttributionReview !==
      input.expectedAnalysisGateDecisionIds.dialogueAttributionReview ||
    gates.wholeBookAnalysisReview !==
      input.expectedAnalysisGateDecisionIds.wholeBookAnalysisReview ||
    run.catalogRevisionId !== input.expectedCatalogRevisionId ||
    run.catalogFingerprint !== input.expectedCatalogFingerprint ||
    run.profile.fingerprint !== input.expectedCastingProfileFingerprint
  ) {
    throw invalid("The created casting run did not freeze the requested evidence.");
  }
}

function runFingerprint(run: CastingRun): string {
  return run.outputFingerprint ?? run.inputFingerprint;
}

function validatePage(
  root: Record<string, unknown>,
  itemCount: number,
  requestedLimit?: number,
  maximum: number = CASTING_LIMITS.maximumPageSize
): void {
  const pageSize = nonNegativeInteger(root.pageSize, "pageSize");
  const total = nonNegativeInteger(root.total, "total");
  const allowedSize = Math.min(
    requestedLimit ?? CASTING_LIMITS.defaultPageSize,
    maximum
  );
  if (pageSize !== itemCount || pageSize > total || pageSize > allowedSize) {
    throw invalid("The response page exceeded the requested bounds.");
  }
  if (root.nextCursor !== undefined) {
    text(root.nextCursor, "nextCursor", 1, MAX_CURSOR_LENGTH);
  }
}

function parseEnvelope(value: unknown): Record<string, unknown> {
  const request = exact(value, "desktop request", [
    "contractVersion",
    "payload"
  ]);
  if (request.contractVersion !== DESKTOP_CONTRACT_VERSION) {
    throw invalid("The desktop contract version was not supported.");
  }
  return object(request.payload, "payload");
}

function wrap<T>(payload: T): DesktopRequest<T> {
  return { contractVersion: DESKTOP_CONTRACT_VERSION, payload };
}

function pageInput(
  value: Record<string, unknown>,
  maximum: number
): { readonly cursor?: string; readonly limit?: number } {
  return {
    ...(value.cursor === undefined
      ? {}
      : { cursor: text(value.cursor, "cursor", 1, MAX_CURSOR_LENGTH) }),
    ...(value.limit === undefined
      ? {}
      : { limit: integer(value.limit, "limit", 1, maximum) })
  };
}

function exact(
  value: unknown,
  field: string,
  requiredFields: readonly string[],
  optionalFields: readonly string[] = []
): Record<string, unknown> {
  const item = object(value, field);
  allowed(item, [...requiredFields, ...optionalFields]);
  required(item, requiredFields);
  return item;
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw invalid(`${field} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function allowed(
  value: Record<string, unknown>,
  fields: readonly string[]
): void {
  const expected = new Set(fields);
  const unexpected = Object.keys(value).find((key) => !expected.has(key));
  if (unexpected !== undefined) {
    throw invalid(`Unexpected field: ${unexpected}.`);
  }
}

function required(
  value: Record<string, unknown>,
  fields: readonly string[]
): void {
  const missing = fields.find((key) => !(key in value));
  if (missing !== undefined) {
    throw invalid(`Missing field: ${missing}.`);
  }
}

function contractVersion(value: unknown, field: string): void {
  if (value !== VOICE_CASTING_CONTRACT_VERSION) {
    throw invalid(`${field} was not the supported voice-casting contract.`);
  }
}

function identifier(value: unknown, field: string): string {
  const result = text(value, field, 1, 160);
  if (!IDENTIFIER_PATTERN.test(result)) {
    throw invalid(`${field} must be an identifier.`);
  }
  return result;
}

function nullableIdentifier(value: unknown, field: string): string | null {
  return value === null ? null : identifier(value, field);
}

function canonicalJson(value: unknown): string {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return JSON.stringify(value);
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    return `{${Object.keys(item)
      .sort()
      .map(
        (key) => `${JSON.stringify(key)}:${canonicalJson(item[key])}`
      )
      .join(",")}}`;
  }
  throw invalid("Fingerprint projection contained a non-JSON value.");
}

function validateProjectionFingerprint(
  item: Readonly<Record<string, unknown>>,
  field: string
): void {
  const claimed = sha256(item.outputFingerprint, `${field}.outputFingerprint`);
  const projection = Object.fromEntries(
    Object.entries(item).filter(([key]) => key !== "outputFingerprint")
  );
  const actual = createHash("sha256")
    .update(canonicalJson(projection), "utf8")
    .digest("hex");
  if (claimed !== actual) {
    throw invalid(`${field}.outputFingerprint did not bind its exact projection.`);
  }
}

function sha256(value: unknown, field: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    throw invalid(`${field} must be a lowercase SHA-256 digest.`);
  }
  return value;
}

function castingProfileFingerprint(
  value: unknown,
  field: string
):
  | typeof GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT
  | typeof LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT {
  const fingerprint = sha256(value, field);
  if (fingerprint === GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT) {
    return GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT;
  }
  if (fingerprint === LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT) {
    return LEGACY_GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT;
  }
  throw invalid(`${field} was not a governed current or legacy profile.`);
}

function semver(value: unknown, field: string): string {
  if (typeof value !== "string" || !SEMVER_PATTERN.test(value)) {
    throw invalid(`${field} must be a semantic version.`);
  }
  return value;
}

function text(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): string {
  if (typeof value !== "string") {
    throw invalid(`${field} must be text.`);
  }
  const length = [...value].length;
  if (
    length < minimum ||
    length > maximum ||
    value.trim().length === 0 ||
    value.includes("\0")
  ) {
    throw invalid(`${field} was outside the allowed bounds.`);
  }
  return value;
}

function booleanValue(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw invalid(`${field} must be a boolean.`);
  }
  return value;
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw invalid(`${field} must be an integer from ${minimum} to ${maximum}.`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, field: string): number {
  return integer(value, field, 0, Number.MAX_SAFE_INTEGER);
}

function positiveInteger(value: unknown, field: string): number {
  return integer(value, field, 1, Number.MAX_SAFE_INTEGER);
}

function number(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw invalid(`${field} must be a finite number from ${minimum} to ${maximum}.`);
  }
  return value;
}

function isoDate(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    !Number.isFinite(Date.parse(value)) ||
    !/^\d{4}-\d{2}-\d{2}T/u.test(value)
  ) {
    throw invalid(`${field} must be an ISO timestamp.`);
  }
  return value;
}

function nullableDate(value: unknown, field: string): string | null {
  return value === null ? null : isoDate(value, field);
}

function boundedArray(
  value: unknown,
  field: string,
  maximum: number
): readonly unknown[] {
  if (!Array.isArray(value) || value.length > maximum) {
    throw invalid(`${field} must have at most ${maximum} items.`);
  }
  return value;
}

function stringArray(
  value: unknown,
  field: string,
  maximum: number,
  itemMaximum: number
): readonly string[] {
  const result = boundedArray(value, field, maximum).map((item, index) =>
    text(item, `${field}[${index}]`, 1, itemMaximum)
  );
  unique(result, field);
  return result;
}

function identifierArray(
  value: unknown,
  field: string,
  maximum: number
): readonly string[] {
  const result = boundedArray(value, field, maximum).map((item, index) =>
    identifier(item, `${field}[${index}]`)
  );
  unique(result, field);
  return result;
}

function enumArray<T extends string>(
  value: unknown,
  field: string,
  values: readonly T[],
  maximum: number
): readonly T[] {
  const result = boundedArray(value, field, maximum).map((item, index) =>
    enumeration(item, values, `${field}[${index}]`)
  );
  unique(result, field);
  return result;
}

function enumeration<T extends string>(
  value: unknown,
  values: readonly T[],
  field: string
): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    throw invalid(`${field} must be a supported value.`);
  }
  return value as T;
}

function availability(
  value: unknown,
  field: string
): "available" | "unavailable" | "disabled" {
  return enumeration(
    value,
    ["available", "unavailable", "disabled"] as const,
    field
  );
}

function rightsState(
  value: unknown,
  field: string
): "verified" | "restricted" | "unknown" | "prohibited" {
  return enumeration(
    value,
    ["verified", "restricted", "unknown", "prohibited"] as const,
    field
  );
}

function suitability(
  value: unknown,
  field: string
): "preferred" | "suitable" | "limited" | "unsuitable" | "unknown" {
  return enumeration(
    value,
    ["preferred", "suitable", "limited", "unsuitable", "unknown"] as const,
    field
  );
}

function vocalPresentation(value: unknown, field: string): void {
  enumeration(
    value,
    ["feminine", "masculine", "androgynous", "neutral", "varied", "unspecified"] as const,
    field
  );
}

function vocalTexture(value: unknown, field: string): void {
  enumeration(
    value,
    [
      "airy",
      "bright",
      "clear",
      "crisp",
      "gravelly",
      "resonant",
      "smooth",
      "warm",
      "textured",
      "varied",
      "unspecified"
    ] as const,
    field
  );
}

function validateIntegerRange(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): void {
  const item = exact(value, field, ["minimum", "maximum"]);
  const lower = integer(item.minimum, `${field}.minimum`, minimum, maximum);
  const upper = integer(item.maximum, `${field}.maximum`, minimum, maximum);
  if (upper < lower) {
    throw invalid(`${field} maximum was below its minimum.`);
  }
}

function validateNumberRange(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): void {
  const item = exact(value, field, ["minimum", "maximum"]);
  const lower = number(item.minimum, `${field}.minimum`, minimum, maximum);
  const upper = number(item.maximum, `${field}.maximum`, minimum, maximum);
  if (upper < lower) {
    throw invalid(`${field} maximum was below its minimum.`);
  }
}

function validateUnitRange(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): void {
  const item = exact(value, field, ["minimum", "maximum", "unit"]);
  const lower = number(item.minimum, `${field}.minimum`, minimum, maximum);
  const upper = number(item.maximum, `${field}.maximum`, minimum, maximum);
  if (upper < lower || item.unit !== "multiplier") {
    throw invalid(`${field} range or unit was invalid.`);
  }
}

function unique(values: readonly string[], field: string): void {
  if (new Set(values).size !== values.length) {
    throw invalid(`${field} must not contain duplicates.`);
  }
}

function invalid(message: string): ValidationError {
  return new ValidationError(message);
}
