import {
  ANALYSIS_CONTRACT_VERSION,
  ANALYSIS_JOB_STAGES,
  PHASE_2_RUNTIME_AGENTS,
  STORY_ANALYSIS_LIMITS,
  WHOLE_BOOK_ANALYSIS_PRODUCER_ID,
  WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION
} from "@cinematic-story-studio/contracts";

import {
  ANALYSIS_ENTITY_COLLECTIONS,
  ANALYSIS_GATE_IDS,
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
  type AnalysisCollection,
  type AnalysisConfidence,
  type AnalysisCorrection,
  type AnalysisCorrectionCategory,
  type AnalysisCorrectionPatch,
  type AnalysisCorrectionRequestPatch,
  type AnalysisCorrectionRequestSelection,
  type AnalysisCorrectionSelection,
  type AnalysisCorrectionsResponse,
  type AnalysisEntity,
  type AnalysisEntityPageResponse,
  type AnalysisEvidenceExcerpt,
  type AnalysisGateId,
  type AnalysisReview,
  type AnalysisReviewsResponse,
  type AnalysisRun,
  type AnalysisRunResponse,
  type AnalysisRunsResponse,
  type AppendAnalysisCorrectionInput,
  type AppendAnalysisCorrectionResponse,
  type CreateAnalysisRunInput,
  type CreateAnalysisRunResponse,
  type DecideAnalysisReviewInput,
  type DecideAnalysisReviewResponse,
  type DialogueSpeakerState,
  type ListAnalysisCorrectionsInput,
  type ListAnalysisEntitiesInput,
  type ListAnalysisReviewsInput,
  type ListAnalysisRunsInput
} from "../shared/analysis-api.js";
import {
  DESKTOP_CONTRACT_VERSION,
  type AnalysisRunInput,
  type DesktopRequest
} from "../shared/desktop-api.js";
import { ValidationError } from "./validation.js";

const IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u;
const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const MAX_CURSOR_LENGTH = 512;
const MAX_CORRECTION_TEXT_CODE_POINTS = 2_000;
const MAX_CORRECTION_IDENTIFIERS = 256;
const MAX_CHARACTER_RESPONSE_IDENTIFIERS = 100_000;
const MAX_LOCATION_RESPONSE_ASSIGNMENTS = 10_000;
const MAX_RUNS_PER_PAGE = STORY_ANALYSIS_LIMITS.maximumPageSize;
const MAX_CORRECTIONS_PER_PAGE = STORY_ANALYSIS_LIMITS.maximumPageSize;
const MAX_GATE_WARNINGS = 32;

interface GateEvidenceRun {
  readonly sourceDocumentId: string;
  readonly extractionId: string;
  readonly extractionRevision: number;
  readonly storyId: string;
  readonly runFingerprint: string;
  readonly profile: {
    readonly profileId: string;
    readonly fingerprint: string;
  };
  readonly currentSnapshot: {
    readonly snapshotId: string;
    readonly revision: number;
    readonly snapshotFingerprint: string;
  } | null;
}

const correctionCollections: Readonly<
  Record<AnalysisCorrectionCategory, readonly AnalysisCollection[]>
> = {
  structure_boundary: ["chapters", "scenes"],
  structure_label: ["chapters", "scenes"],
  character_identity: ["characters"],
  character_alias: ["characters"],
  character_merge: ["characters"],
  character_split: ["characters"],
  mention_resolution: ["mentions"],
  dialogue_speaker: ["dialogue-lines"],
  point_of_view: ["pov-segments"],
  location_identity: ["locations"],
  location_alias: ["locations"],
  temporal_order: ["temporal-constraints"],
  relationship: ["relationships"],
  emotional_state: ["emotional-states"],
  dramatic_intent: ["dramatic-intents"],
  continuity_disposition: ["continuity-findings"]
};

const entityFields: Readonly<
  Record<AnalysisCollection, readonly string[]>
> = {
  "agent-executions": [
    "contractVersion",
    "executionId",
    "runId",
    "snapshotId",
    "ordinal",
    "agentId",
    "agentVersion",
    "status",
    "attempt",
    "progress",
    "currentStage",
    "checkpoint",
    "retryClassification",
    "retryPolicy",
    "failurePolicy",
    "inputFingerprint",
    "outputFingerprint",
    "outputCollections",
    "outputArtifactId",
    "confidence",
    "warnings",
    "provenance",
    "startedAt",
    "finishedAt",
    "failure"
  ],
  chapters: [
    ...analysisHeaderFields(),
    "chapterId",
    "title",
    "sourceSpan",
    "firstSceneId",
    "lastSceneId",
    "sceneCount",
    "effectiveBoundary"
  ],
  scenes: [
    ...analysisHeaderFields(),
    "sceneId",
    "chapterId",
    "heading",
    "sourceSpan",
    "boundaryKind",
    "firstBeatId",
    "lastBeatId",
    "beatCount",
    "effectiveBoundary"
  ],
  beats: [
    ...analysisHeaderFields(),
    "beatId",
    "chapterId",
    "sceneId",
    "kind",
    "sourceSpan",
    "summary",
    "dialogueLineId",
    "narration"
  ],
  characters: [
    ...analysisHeaderFields(),
    "characterId",
    "registryCharacterId",
    "projectId",
    "storyId",
    "registryScope",
    "stableAcrossCompatibleRuns",
    "canonicalName",
    "normalizedCanonicalName",
    "kind",
    "identityStatus",
    "aliases",
    "honorifics",
    "pronounEvidence",
    "firstMentionId",
    "lastMentionId",
    "namedMentionIds",
    "ambiguousMentionIds",
    "firstEvidence",
    "lastEvidence",
    "mentionCount",
    "effectiveRegistry"
  ],
  mentions: [
    ...analysisHeaderFields(),
    "mentionId",
    "chapterId",
    "sceneId",
    "exactText",
    "mentionKind",
    "resolution",
    "effectiveCharacterId",
    "candidateCharacterIds"
  ],
  "dialogue-lines": [
    ...analysisHeaderFields(),
    "dialogueLineId",
    "chapterId",
    "sceneId",
    "beatId",
    "exactText",
    "distinction",
    "candidates",
    "speakerState",
    "effectiveAttribution"
  ],
  "narration-spans": [
    ...analysisHeaderFields(),
    "narrationSpanId",
    "chapterId",
    "sceneId",
    "exactText",
    "classification",
    "narratorCharacterId"
  ],
  "pov-segments": [
    ...analysisHeaderFields(),
    "povSegmentId",
    "chapterId",
    "sceneId",
    "sourceSpan",
    "mode",
    "viewpointCharacterId",
    "narratorCharacterId",
    "shiftFromPovSegmentId",
    "shiftKind"
  ],
  locations: [
    ...analysisHeaderFields(),
    "locationId",
    "canonicalName",
    "normalizedCanonicalName",
    "aliases",
    "kind",
    "parentLocationId",
    "firstSceneId",
    "sceneIds",
    "sceneAssignments",
    "sceneCount"
  ],
  "timeline-events": [
    ...analysisHeaderFields(),
    "timelineEventId",
    "chapterId",
    "sceneId",
    "kind",
    "label",
    "narrativeOrdinal",
    "chronologicalOrdinal",
    "exactTimeExpression",
    "locationId",
    "participantCharacterIds"
  ],
  "temporal-constraints": [
    ...analysisHeaderFields(),
    "temporalConstraintId",
    "sourceEventId",
    "targetEventId",
    "relation",
    "approximate",
    "status"
  ],
  relationships: [
    ...analysisHeaderFields(),
    "relationshipId",
    "sourceCharacterId",
    "targetCharacterId",
    "sourceCandidateCharacterIds",
    "targetCandidateCharacterIds",
    "resolution",
    "sceneId",
    "chapterId",
    "scope",
    "validFromEventId",
    "validThroughEventId",
    "kind",
    "state",
    "change",
    "previousRelationshipId"
  ],
  "emotional-states": [
    ...analysisHeaderFields(),
    "emotionalStateId",
    "subjectType",
    "characterId",
    "sceneId",
    "emotion",
    "customEmotion",
    "note",
    "valence",
    "arousal",
    "intensity",
    "progression",
    "previousEmotionalStateId"
  ],
  "dramatic-intents": [
    ...analysisHeaderFields(),
    "dramaticIntentId",
    "subjectType",
    "characterId",
    "sceneId",
    "dialogueLineId",
    "beatId",
    "intent",
    "customIntent",
    "dramaticFunction",
    "customDramaticFunction",
    "note",
    "targetCharacterId",
    "status"
  ],
  "continuity-findings": [
    ...analysisHeaderFields(),
    "continuityFindingId",
    "category",
    "severity",
    "machineStatus",
    "explanation",
    "suggestedReviewAction",
    "relatedEntityIds",
    "requiresHumanReview",
    "humanDisposition"
  ]
};

const entityIdField: Readonly<Record<AnalysisCollection, string>> = {
  "agent-executions": "executionId",
  chapters: "chapterId",
  scenes: "sceneId",
  beats: "beatId",
  characters: "characterId",
  mentions: "mentionId",
  "dialogue-lines": "dialogueLineId",
  "narration-spans": "narrationSpanId",
  "pov-segments": "povSegmentId",
  locations: "locationId",
  "timeline-events": "timelineEventId",
  "temporal-constraints": "temporalConstraintId",
  relationships: "relationshipId",
  "emotional-states": "emotionalStateId",
  "dramatic-intents": "dramaticIntentId",
  "continuity-findings": "continuityFindingId"
};

export function parseListAnalysisRunsRequest(
  value: unknown
): DesktopRequest<ListAnalysisRunsInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(
    payload,
    ["projectId", "cursor", "limit"],
    "analysis run list request"
  );
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      projectId: parseIdentifier(payload.projectId, "projectId"),
      ...parseCursorPage(payload)
    }
  };
}

export function parseAnalysisRunRequest(
  value: unknown
): DesktopRequest<AnalysisRunInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(payload, ["projectId", "runId"], "analysis run request");
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: parseRunIdentity(payload)
  };
}

export function parseListAnalysisReviewsRequest(
  value: unknown
): DesktopRequest<ListAnalysisReviewsInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(
    payload,
    [
      "projectId",
      "runId",
      "expectedSourceDocumentId",
      "expectedExtractionId",
      "expectedExtractionRevision",
      "expectedStoryId",
      "expectedProfileId",
      "expectedProfileFingerprint",
      "expectedRunFingerprint",
      "expectedSnapshotId",
      "expectedSnapshotRevision",
      "expectedSnapshotFingerprint"
    ],
    "analysis review list request"
  );
  if (payload.expectedProfileId !== WHOLE_BOOK_ANALYSIS_PROFILE_ID) {
    throw new ValidationError(
      "The analysis review profile identity is unsupported."
    );
  }
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      ...parseRunIdentity(payload),
      expectedSourceDocumentId: parseIdentifier(
        payload.expectedSourceDocumentId,
        "expectedSourceDocumentId"
      ),
      expectedExtractionId: parseIdentifier(
        payload.expectedExtractionId,
        "expectedExtractionId"
      ),
      expectedExtractionRevision: parsePositiveInteger(
        payload.expectedExtractionRevision,
        "expectedExtractionRevision"
      ),
      expectedStoryId: parseIdentifier(
        payload.expectedStoryId,
        "expectedStoryId"
      ),
      expectedProfileId: payload.expectedProfileId,
      expectedProfileFingerprint: parseSha256(
        payload.expectedProfileFingerprint,
        "expectedProfileFingerprint"
      ),
      expectedRunFingerprint: parseSha256(
        payload.expectedRunFingerprint,
        "expectedRunFingerprint"
      ),
      expectedSnapshotId: parseIdentifier(
        payload.expectedSnapshotId,
        "expectedSnapshotId"
      ),
      expectedSnapshotRevision: parsePositiveInteger(
        payload.expectedSnapshotRevision,
        "expectedSnapshotRevision"
      ),
      expectedSnapshotFingerprint: parseSha256(
        payload.expectedSnapshotFingerprint,
        "expectedSnapshotFingerprint"
      )
    }
  };
}

export function parseCreateAnalysisRunRequest(
  value: unknown
): DesktopRequest<CreateAnalysisRunInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(
    payload,
    [
      "projectId",
      "expectedExtractionId",
      "expectedExtractionRevision",
      "expectedReviewId",
      "expectedReviewRevision",
      "expectedEvidenceFingerprint",
      "expectedProfileFingerprint",
      "profile",
      "idempotencyKey"
    ],
    "create analysis run request"
  );
  const profile = parseProfileReference(payload.profile);
  const expectedProfileFingerprint = parseSha256(
    payload.expectedProfileFingerprint,
    "expectedProfileFingerprint"
  );
  if (
    expectedProfileFingerprint !==
    WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
  ) {
    throw new ValidationError(
      "The expected analysis profile fingerprint is unsupported."
    );
  }
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      projectId: parseIdentifier(payload.projectId, "projectId"),
      expectedExtractionId: parseIdentifier(
        payload.expectedExtractionId,
        "expectedExtractionId"
      ),
      expectedExtractionRevision: parsePositiveInteger(
        payload.expectedExtractionRevision,
        "expectedExtractionRevision"
      ),
      expectedReviewId: parseIdentifier(
        payload.expectedReviewId,
        "expectedReviewId"
      ),
      expectedReviewRevision: parsePositiveInteger(
        payload.expectedReviewRevision,
        "expectedReviewRevision"
      ),
      expectedEvidenceFingerprint: parseSha256(
        payload.expectedEvidenceFingerprint,
        "expectedEvidenceFingerprint"
      ),
      expectedProfileFingerprint,
      profile,
      idempotencyKey: parseIdentifier(
        payload.idempotencyKey,
        "idempotencyKey"
      )
    }
  };
}

export function parseListAnalysisEntitiesRequest(
  value: unknown
): DesktopRequest<ListAnalysisEntitiesInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(
    payload,
    [
      "projectId",
      "runId",
      "expectedSnapshotId",
      "collection",
      "cursor",
      "limit",
      "confidenceMax",
      "requiresReview",
      "speakerState"
    ],
    "analysis entity request"
  );
  const identity = parseRunIdentity(payload);
  const collection = parseCollection(payload.collection);
  if (
    collection !== "dialogue-lines" &&
    payload.speakerState !== undefined
  ) {
    throw new ValidationError(
      "Speaker state cannot be applied to this analysis collection."
    );
  }
  const confidenceMax =
    payload.confidenceMax === undefined
      ? undefined
      : parseUnitInterval(payload.confidenceMax, "confidenceMax");
  const requiresReview =
    payload.requiresReview === undefined
      ? undefined
      : parseBoolean(payload.requiresReview, "requiresReview");
  const speakerState =
    payload.speakerState === undefined
      ? undefined
      : parseSpeakerState(payload.speakerState);
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      ...identity,
      expectedSnapshotId: parseIdentifier(
        payload.expectedSnapshotId,
        "expectedSnapshotId"
      ),
      collection,
      ...parseCursorPage(payload),
      ...(confidenceMax === undefined ? {} : { confidenceMax }),
      ...(requiresReview === undefined ? {} : { requiresReview }),
      ...(speakerState === undefined ? {} : { speakerState })
    }
  };
}

export function parseListAnalysisCorrectionsRequest(
  value: unknown
): DesktopRequest<ListAnalysisCorrectionsInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(
    payload,
    ["projectId", "runId", "cursor", "limit"],
    "analysis correction list request"
  );
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      ...parseRunIdentity(payload),
      ...parseCursorPage(payload)
    }
  };
}

export function parseAppendAnalysisCorrectionRequest(
  value: unknown
): DesktopRequest<AppendAnalysisCorrectionInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(
    payload,
    [
      "projectId",
      "runId",
      "category",
      "targetCollection",
      "targetEntityId",
      "expectedTargetRevision",
      "expectedRunFingerprint",
      "previousValueFingerprint",
      "patch",
      "reason",
      "supersedesCorrectionId",
      "idempotencyKey"
    ],
    "analysis correction request"
  );
  const category = parseCorrectionCategory(payload.category);
  const targetCollection = parseCollection(payload.targetCollection);
  if (!correctionCollections[category].includes(targetCollection)) {
    throw new ValidationError(
      "The correction category cannot target this collection."
    );
  }
  const selection = parseCorrectionSelection(
    category,
    targetCollection,
    payload.patch,
    "request"
  );
  const reason = parseBoundedText(payload.reason, "reason", 1, 1_000);
  const supersedesCorrectionId =
    payload.supersedesCorrectionId === undefined
      ? undefined
      : parseIdentifier(
          payload.supersedesCorrectionId,
          "supersedesCorrectionId"
        );
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      ...parseRunIdentity(payload),
      ...selection,
      targetEntityId: parseIdentifier(
        payload.targetEntityId,
        "targetEntityId"
      ),
      expectedTargetRevision: parsePositiveInteger(
        payload.expectedTargetRevision,
        "expectedTargetRevision"
      ),
      expectedRunFingerprint: parseSha256(
        payload.expectedRunFingerprint,
        "expectedRunFingerprint"
      ),
      previousValueFingerprint: parseSha256(
        payload.previousValueFingerprint,
        "previousValueFingerprint"
      ),
      reason,
      ...(supersedesCorrectionId === undefined
        ? {}
        : { supersedesCorrectionId }),
      idempotencyKey: parseIdentifier(
        payload.idempotencyKey,
        "idempotencyKey"
      )
    }
  };
}

export function parseDecideAnalysisReviewRequest(
  value: unknown
): DesktopRequest<DecideAnalysisReviewInput> {
  const payload = parseEnvelope(value);
  rejectUnknownFields(
    payload,
    [
      "projectId",
      "runId",
      "gateId",
      "decision",
      "expectedRevision",
      "expectedArtifactFingerprint",
      "expectedEvidenceFingerprint",
      "acknowledgedWarningIds",
      "rationale",
      "idempotencyKey"
    ],
    "analysis review decision request"
  );
  const decision = parseGateDecisionAction(payload.decision);
  const rationale = parseBoundedText(
    payload.rationale,
    "rationale",
    1,
    4_000
  );
  return {
    contractVersion: DESKTOP_CONTRACT_VERSION,
    payload: {
      ...parseRunIdentity(payload),
      gateId: parseGateId(payload.gateId),
      decision,
      expectedRevision: parsePositiveInteger(
        payload.expectedRevision,
        "expectedRevision"
      ),
      expectedArtifactFingerprint: parseSha256(
        payload.expectedArtifactFingerprint,
        "expectedArtifactFingerprint"
      ),
      expectedEvidenceFingerprint: parseSha256(
        payload.expectedEvidenceFingerprint,
        "expectedEvidenceFingerprint"
      ),
      acknowledgedWarningIds: parseIdentifierArray(
        payload.acknowledgedWarningIds,
        "acknowledgedWarningIds",
        MAX_GATE_WARNINGS
      ),
      rationale,
      idempotencyKey: parseIdentifier(
        payload.idempotencyKey,
        "idempotencyKey"
      )
    }
  };
}

export function validateCreateAnalysisRunResponse(
  value: unknown,
  expected: CreateAnalysisRunInput
): CreateAnalysisRunResponse {
  const response = expectRecord(value, "create analysis run response");
  rejectUnknownFields(
    response,
    ["correlationId", "run", "job"],
    "create analysis run response"
  );
  parseIdentifier(response.correlationId, "correlationId");
  const run = validateRun(response.run, expected.projectId);
  if (
    run.extractionId !== expected.expectedExtractionId ||
    run.extractionRevision !== expected.expectedExtractionRevision ||
    run.importReviewId !== expected.expectedReviewId ||
    run.importReviewRevision !== expected.expectedReviewRevision ||
    run.approvedEvidenceFingerprint !==
      expected.expectedEvidenceFingerprint ||
    run.profile.profileId !== expected.profile.profileId ||
    run.profile.semanticVersion !== expected.profile.semanticVersion ||
    run.profile.fingerprint !== expected.profile.fingerprint ||
    run.profile.fingerprint !== expected.expectedProfileFingerprint
  ) {
    throw new ValidationError(
      "The analysis run did not bind the exact approved input."
    );
  }
  const job = validateAnalysisJob(response.job, run);
  if (
    job.jobId !== run.jobId ||
    job.projectId !== run.projectId ||
    job.type !== "analyze_whole_book" ||
    job.inputRevision !== run.storyRevision ||
    job.inputFingerprint !== run.storyFingerprint
  ) {
    throw new ValidationError(
      "The analysis job did not bind the created analysis run."
    );
  }
  return value as CreateAnalysisRunResponse;
}

function validateAnalysisJob(
  value: unknown,
  expectedRun: AnalysisRun
): CreateAnalysisRunResponse["job"] {
  const job = expectRecord(value, "analysis job");
  rejectUnknownFields(
    job,
    [
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
    ],
    "analysis job"
  );
  parseIdentifier(job.jobId, "analysis jobId");
  parseIdentifier(job.projectId, "analysis projectId");
  if (job.type !== "analyze_whole_book") {
    throw new ValidationError("The analysis job type is invalid.");
  }
  const target = expectRecord(job.target, "analysis job target");
  rejectUnknownFields(
    target,
    ["type", "id"],
    "analysis job target"
  );
  if (
    target.type !== "analysis_run" ||
    parseIdentifier(target.id, "analysis job target id") !==
      expectedRun.runId
  ) {
    throw new ValidationError("The analysis job target is invalid.");
  }
  requireOneOf(
    job.state,
    [
      "queued",
      "running",
      "cancel_requested",
      "cancelled",
      "failed",
      "interrupted",
      "paused",
      "succeeded"
    ],
    "analysis job state"
  );
  parsePositiveInteger(job.inputRevision, "analysis inputRevision");
  parseSha256(job.inputFingerprint, "analysis inputFingerprint");
  parsePositiveInteger(job.attempt, "analysis attempt");
  const allowedStages = new Set<string>([
    "queued",
    "starting",
    "cancelling",
    "checkpointed",
    "queued_for_retry",
    "queued_for_resume",
    "queued_for_restart",
    "complete",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
    ...ANALYSIS_JOB_STAGES
  ]);
  const stage = parseBoundedText(job.stage, "analysis job stage", 1, 128);
  if (!allowedStages.has(stage)) {
    throw new ValidationError("The analysis job stage is invalid.");
  }
  parseUnitInterval(job.progress, "analysis job progress");
  parseBoolean(job.checkpointAvailable, "analysis checkpointAvailable");
  parseBoolean(
    job.cancellationRequested,
    "analysis cancellationRequested"
  );
  const warnings = parseBoundedArray(
    job.warnings,
    "analysis job warnings",
    STORY_ANALYSIS_LIMITS.maximumWarningsPerEntity
  );
  for (const warningValue of warnings) {
    const warning = expectRecord(warningValue, "analysis job warning");
    rejectUnknownFields(
      warning,
      [
        "code",
        "severity",
        "message",
        "requiresHumanReview",
        "relatedEntities"
      ],
      "analysis job warning"
    );
    parseBoundedText(warning.code, "analysis job warning code", 1, 80);
    requireOneOf(
      warning.severity,
      ["info", "warning", "error"],
      "analysis job warning severity"
    );
    parseBoundedText(
      warning.message,
      "analysis job warning message",
      1,
      1_000
    );
    parseBoolean(
      warning.requiresHumanReview,
      "analysis job warning requiresHumanReview"
    );
    if (warning.relatedEntities !== undefined) {
      const references = parseBoundedArray(
        warning.relatedEntities,
        "analysis job warning relatedEntities",
        64
      );
      for (const referenceValue of references) {
        const reference = expectRecord(
          referenceValue,
          "analysis job warning entity reference"
        );
        rejectUnknownFields(
          reference,
          ["entityType", "entityId", "revision"],
          "analysis job warning entity reference"
        );
        parseBoundedText(
          reference.entityType,
          "analysis job warning entityType",
          1,
          128
        );
        parseIdentifier(
          reference.entityId,
          "analysis job warning entityId"
        );
        if (reference.revision !== undefined) {
          parsePositiveInteger(
            reference.revision,
            "analysis job warning entity revision"
          );
        }
      }
    }
  }
  if (job.error !== undefined) {
    const error = expectRecord(job.error, "analysis job error");
    rejectUnknownFields(
      error,
      ["code", "message", "retryable"],
      "analysis job error"
    );
    parseBoundedText(error.code, "analysis job error code", 1, 80);
    parseBoundedText(error.message, "analysis job error message", 1, 2_000);
    parseBoolean(error.retryable, "analysis job error retryable");
  }
  parseIsoDate(job.createdAt, "analysis job createdAt");
  parseIsoDate(job.updatedAt, "analysis job updatedAt");
  if (job.terminalAt !== undefined) {
    parseIsoDate(job.terminalAt, "analysis job terminalAt");
  }
  return value as CreateAnalysisRunResponse["job"];
}

export function validateAnalysisRunResponse(
  value: unknown,
  expected: { readonly projectId: string; readonly runId?: string }
): AnalysisRunResponse {
  const response = expectRecord(value, "analysis run response");
  rejectUnknownFields(
    response,
    ["correlationId", "run"],
    "analysis run response"
  );
  parseIdentifier(response.correlationId, "correlationId");
  const run = validateRun(response.run, expected.projectId);
  if (expected.runId !== undefined && run.runId !== expected.runId) {
    throw new ValidationError("The analysis run identity is inconsistent.");
  }
  return value as AnalysisRunResponse;
}

export function validateAnalysisRunsResponse(
  value: unknown,
  expected: ListAnalysisRunsInput
): AnalysisRunsResponse {
  const response = expectRecord(value, "analysis run page");
  validatePageEnvelope(response, "analysis run page", "runs");
  const runs = parseBoundedArray(
    response.runs,
    "analysis runs",
    MAX_RUNS_PER_PAGE
  );
  for (const run of runs) {
    validateRun(run, expected.projectId);
  }
  return value as AnalysisRunsResponse;
}

export function validateAnalysisEntityPageResponse(
  value: unknown,
  expected: ListAnalysisEntitiesInput
): AnalysisEntityPageResponse {
  const response = expectRecord(value, "analysis entity page");
  validatePageEnvelope(response, "analysis entity page", "items", [
    "runId",
    "snapshotId",
    "collection"
  ]);
  if (
    response.runId !== expected.runId ||
    response.snapshotId !== expected.expectedSnapshotId ||
    response.collection !== expected.collection
  ) {
    throw new ValidationError("The analysis page identity is inconsistent.");
  }
  parseIdentifier(response.runId, "runId");
  parseIdentifier(response.snapshotId, "snapshotId");
  const collection = parseCollection(response.collection);
  const items = parseBoundedArray(
    response.items,
    "analysis entities",
    STORY_ANALYSIS_LIMITS.maximumPageSize
  );
  for (const item of items) {
    validateEntity(item, collection, {
      runId: expected.runId,
      snapshotId: expected.expectedSnapshotId
    });
  }
  return value as AnalysisEntityPageResponse;
}

export function validateAnalysisCorrectionsResponse(
  value: unknown,
  expected: ListAnalysisCorrectionsInput
): AnalysisCorrectionsResponse {
  const response = expectRecord(value, "analysis correction page");
  validatePageEnvelope(response, "analysis correction page", "items", [
    "runId"
  ]);
  if (response.runId !== expected.runId) {
    throw new ValidationError("The correction page run identity is invalid.");
  }
  parseIdentifier(response.runId, "runId");
  const items = parseBoundedArray(
    response.items,
    "analysis corrections",
    MAX_CORRECTIONS_PER_PAGE
  );
  for (const correction of items) {
    validateCorrection(correction, expected);
  }
  return value as AnalysisCorrectionsResponse;
}

export function validateAppendAnalysisCorrectionResponse(
  value: unknown,
  expected: AppendAnalysisCorrectionInput
): AppendAnalysisCorrectionResponse {
  const response = expectRecord(value, "analysis correction response");
  rejectUnknownFields(
    response,
    ["correlationId", "correction", "invalidatedGateIds", "run", "reviews"],
    "analysis correction response"
  );
  parseIdentifier(response.correlationId, "correlationId");
  const correction = validateCorrection(response.correction, expected);
  if (
    correction.category !== expected.category ||
    correction.targetCollection !== expected.targetCollection ||
    correction.targetEntityId !== expected.targetEntityId ||
    correction.expectedTargetRevision !==
      expected.expectedTargetRevision ||
    correction.expectedRunFingerprint !==
      expected.expectedRunFingerprint ||
    correction.previousValueFingerprint !==
      expected.previousValueFingerprint ||
    correction.reason !== expected.reason ||
    correction.supersedesCorrectionId !==
      expected.supersedesCorrectionId ||
    !jsonValuesEqual(correction.patch, expected.patch)
  ) {
    throw new ValidationError(
      "The saved correction did not bind the exact request preconditions."
    );
  }
  const invalidatedGateIds = validateGateIdArray(
    response.invalidatedGateIds,
    "invalidatedGateIds"
  );
  const run = validateRun(response.run, expected.projectId);
  if (
    run.runId !== expected.runId ||
    run.runFingerprint !== expected.expectedRunFingerprint ||
    run.currentSnapshot === null ||
    correction.snapshotId !== run.currentSnapshot.snapshotId
  ) {
    throw new ValidationError(
      "The corrected run and snapshot identity is inconsistent."
    );
  }
  const reviews = parseBoundedArray(response.reviews, "reviews", 4);
  const reviewByGate = new Map<AnalysisGateId, AnalysisReview>();
  for (const item of reviews) {
    const review = validateReview(item, expected, run);
    if (reviewByGate.has(review.gateId)) {
      throw new ValidationError(
        "The correction response repeated an analysis gate."
      );
    }
    reviewByGate.set(review.gateId, review);
  }
  requireCompleteGateSet(
    reviewByGate.keys(),
    "The correction response omitted an analysis gate."
  );
  for (const gateId of invalidatedGateIds) {
    if (reviewByGate.get(gateId)?.state !== "invalidated") {
      throw new ValidationError(
        "The correction response invalidated gate evidence is inconsistent."
      );
    }
  }
  return value as AppendAnalysisCorrectionResponse;
}

export function validateAnalysisReviewsResponse(
  value: unknown,
  expected: ListAnalysisReviewsInput
): AnalysisReviewsResponse {
  const response = expectRecord(value, "analysis review list");
  rejectUnknownFields(
    response,
    ["correlationId", "runId", "items"],
    "analysis review list"
  );
  parseIdentifier(response.correlationId, "correlationId");
  if (response.runId !== expected.runId) {
    throw new ValidationError("The review list run identity is invalid.");
  }
  const reviews = parseBoundedArray(response.items, "reviews", 4);
  const gates = new Set<AnalysisGateId>();
  const expectedRun: GateEvidenceRun = {
    sourceDocumentId: expected.expectedSourceDocumentId,
    extractionId: expected.expectedExtractionId,
    extractionRevision: expected.expectedExtractionRevision,
    storyId: expected.expectedStoryId,
    runFingerprint: expected.expectedRunFingerprint,
    profile: {
      profileId: expected.expectedProfileId,
      fingerprint: expected.expectedProfileFingerprint
    },
    currentSnapshot: {
      snapshotId: expected.expectedSnapshotId,
      revision: expected.expectedSnapshotRevision,
      snapshotFingerprint: expected.expectedSnapshotFingerprint
    }
  };
  for (const item of reviews) {
    const review = validateReview(item, expected, expectedRun);
    if (gates.has(review.gateId)) {
      throw new ValidationError("The review list repeated a gate.");
    }
    gates.add(review.gateId);
  }
  requireCompleteGateSet(
    gates,
    "The review list omitted an analysis gate."
  );
  return value as AnalysisReviewsResponse;
}

export function validateDecideAnalysisReviewResponse(
  value: unknown,
  expected: DecideAnalysisReviewInput
): DecideAnalysisReviewResponse {
  const response = expectRecord(value, "analysis review decision response");
  rejectUnknownFields(
    response,
    ["correlationId", "review", "decision", "run"],
    "analysis review decision response"
  );
  parseIdentifier(response.correlationId, "correlationId");
  const run = validateRun(response.run, expected.projectId);
  if (run.runId !== expected.runId) {
    throw new ValidationError("The decided run identity is inconsistent.");
  }
  const review = validateReview(response.review, expected, run);
  const decision = validateGateDecision(response.decision, expected, run);
  const expectedDecision = {
    approve: "approved",
    request_changes: "changes_requested",
    reject: "rejected"
  }[expected.decision];
  if (
    review.gateId !== expected.gateId ||
    review.state !== expectedDecision ||
    review.revision !== expected.expectedRevision + 1 ||
    review.artifactFingerprint !==
      expected.expectedArtifactFingerprint ||
    review.evidenceFingerprint !==
      expected.expectedEvidenceFingerprint ||
    decision.decision !== expectedDecision ||
    decision.artifactFingerprint !==
      expected.expectedArtifactFingerprint ||
    decision.evidenceFingerprint !==
      expected.expectedEvidenceFingerprint ||
    decision.rationale !== expected.rationale ||
    !sameIdentifierSet(
      decision.acknowledgedWarningIds,
      expected.acknowledgedWarningIds
    ) ||
    decision.gateId !== review.gateId ||
    decision.reviewId !== review.reviewId ||
    decision.snapshotId !== review.snapshotId ||
    review.latestDecisionId !== decision.decisionId ||
    !jsonValuesEqual(review.latestDecision, decision) ||
    !jsonValuesEqual(review.evidence, decision.evidence)
  ) {
    throw new ValidationError(
      "The gate decision did not bind the exact request and review evidence."
    );
  }
  return value as DecideAnalysisReviewResponse;
}

export function validateProjectAnalysisProjection(
  value: unknown,
  expectedProjectId: string
): void {
  const detail = expectRecord(value, "project analysis projection");
  const currentRun = detail.currentAnalysisRun;
  const reviews = parseBoundedArray(
    detail.analysisGateReviews,
    "project analysisGateReviews",
    ANALYSIS_GATE_IDS.length
  );
  if (currentRun === null) {
    if (reviews.length !== 0) {
      throw new ValidationError(
        "A project without an analysis run cannot expose analysis reviews."
      );
    }
    return;
  }
  const run = validateRun(currentRun, expectedProjectId);
  const seen = new Set<AnalysisGateId>();
  for (const item of reviews) {
    const review = validateReview(item, {
      projectId: expectedProjectId,
      runId: run.runId
    }, run);
    if (seen.has(review.gateId)) {
      throw new ValidationError("The project repeated an analysis gate.");
    }
    if (
      run.currentSnapshot !== null &&
      review.snapshotId !== run.currentSnapshot.snapshotId
    ) {
      throw new ValidationError(
        "The project analysis review snapshot is inconsistent."
      );
    }
    seen.add(review.gateId);
  }
}

function validateRun(value: unknown, expectedProjectId: string): AnalysisRun {
  const run = expectRecord(value, "analysis run");
  rejectUnknownFields(
    run,
    [
      "contractVersion",
      "runId",
      "projectId",
      "storyId",
      "storyRevision",
      "storyFingerprint",
      "sourceDocumentId",
      "sourceRevision",
      "sourceSha256",
      "extractionId",
      "extractionRevision",
      "extractedTextSha256",
      "importReviewId",
      "importReviewRevision",
      "importReviewDecisionId",
      "approvedEvidenceFingerprint",
      "inputFingerprint",
      "runFingerprint",
      "profile",
      "producer",
      "agentVersions",
      "jobId",
      "status",
      "currentStage",
      "progress",
      "warnings",
      "snapshotCount",
      "currentSnapshot",
      "latestExecution",
      "summary",
      "reviewEligibility",
      "createdAt",
      "updatedAt",
      "completedAt"
    ],
    "analysis run"
  );
  requireContractVersion(run.contractVersion);
  for (const field of [
    "runId",
    "projectId",
    "storyId",
    "sourceDocumentId",
    "extractionId",
    "importReviewId",
    "importReviewDecisionId",
    "jobId"
  ] as const) {
    parseIdentifier(run[field], field);
  }
  if (run.projectId !== expectedProjectId) {
    throw new ValidationError("The analysis run project identity is invalid.");
  }
  for (const field of [
    "storyRevision",
    "sourceRevision",
    "extractionRevision",
    "importReviewRevision"
  ] as const) {
    parsePositiveInteger(run[field], field);
  }
  for (const field of [
    "storyFingerprint",
    "sourceSha256",
    "extractedTextSha256",
    "approvedEvidenceFingerprint",
    "inputFingerprint",
    "runFingerprint"
  ] as const) {
    parseSha256(run[field], field);
  }
  parseProfileReference(run.profile);
  const producer = expectRecord(run.producer, "analysis producer");
  rejectUnknownFields(
    producer,
    ["producerId", "producerVersion"],
    "analysis producer"
  );
  if (
    producer.producerId !== WHOLE_BOOK_ANALYSIS_PRODUCER_ID ||
    producer.producerVersion !== WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION
  ) {
    throw new ValidationError("The analysis producer is unsupported.");
  }
  validateAgentVersions(run.agentVersions);
  if (
    run.status !== "queued" &&
    run.status !== "running" &&
    run.status !== "succeeded" &&
    run.status !== "partial" &&
    run.status !== "failed" &&
    run.status !== "cancelled" &&
    run.status !== "interrupted"
  ) {
    throw new ValidationError("The analysis run status is invalid.");
  }
  if (
    run.currentStage !== "queued" &&
    run.currentStage !== "complete" &&
    !(ANALYSIS_JOB_STAGES as readonly string[]).includes(
      run.currentStage as string
    )
  ) {
    throw new ValidationError("The analysis run stage is invalid.");
  }
  parseUnitInterval(run.progress, "run progress");
  validateWarnings(run.warnings, "run warnings");
  parseBoundedInteger(run.snapshotCount, "snapshotCount", 0, 1_000_000);
  if (run.currentSnapshot !== null) {
    const snapshot = validateSnapshot(run.currentSnapshot, run.runId as string);
    if (snapshot.inputFingerprint !== run.inputFingerprint) {
      throw new ValidationError(
        "The analysis snapshot input fingerprint is inconsistent."
      );
    }
  }
  if (run.latestExecution !== undefined) {
    validateEntity(run.latestExecution, "agent-executions", {
      runId: run.runId as string,
      snapshotId: undefined
    });
  }
  if (run.summary !== undefined) {
    validateCountSummary(run.summary);
  }
  if (
    run.reviewEligibility !== "not_ready" &&
    run.reviewEligibility !== "ready" &&
    run.reviewEligibility !== "blocked_by_warnings" &&
    run.reviewEligibility !== "invalidated"
  ) {
    throw new ValidationError("The analysis review eligibility is invalid.");
  }
  if (
    (run.reviewEligibility === "ready" ||
      run.reviewEligibility === "blocked_by_warnings") &&
    (run.status !== "succeeded" ||
      run.currentStage !== "complete" ||
      run.progress !== 1 ||
      run.currentSnapshot === null)
  ) {
    throw new ValidationError(
      "A reviewable analysis run must be fully completed with an immutable snapshot."
    );
  }
  parseIsoDate(run.createdAt, "createdAt");
  parseIsoDate(run.updatedAt, "updatedAt");
  if (run.completedAt !== undefined) {
    parseIsoDate(run.completedAt, "completedAt");
  }
  return value as AnalysisRun;
}

function validateSnapshot(value: unknown, expectedRunId: string) {
  const snapshot = expectRecord(value, "analysis snapshot");
  rejectUnknownFields(
    snapshot,
    [
      "contractVersion",
      "snapshotId",
      "runId",
      "revision",
      "inputFingerprint",
      "snapshotFingerprint",
      "correctionSetFingerprint",
      "counts",
      "collections",
      "createdAt",
      "immutable"
    ],
    "analysis snapshot"
  );
  requireContractVersion(snapshot.contractVersion);
  parseIdentifier(snapshot.snapshotId, "snapshotId");
  if (parseIdentifier(snapshot.runId, "snapshot runId") !== expectedRunId) {
    throw new ValidationError("The snapshot run identity is invalid.");
  }
  parsePositiveInteger(snapshot.revision, "snapshot revision");
  parseSha256(snapshot.inputFingerprint, "snapshot inputFingerprint");
  parseSha256(snapshot.snapshotFingerprint, "snapshot fingerprint");
  parseSha256(
    snapshot.correctionSetFingerprint,
    "snapshot correctionSetFingerprint"
  );
  validateCountSummary(snapshot.counts);
  const collections = parseBoundedArray(
    snapshot.collections,
    "snapshot collections",
    ANALYSIS_ENTITY_COLLECTIONS.length
  );
  const seen = new Set<AnalysisCollection>();
  for (const value of collections) {
    const collection = expectRecord(value, "snapshot collection");
    rejectUnknownFields(
      collection,
      ["collection", "itemCount", "fingerprint"],
      "snapshot collection"
    );
    const name = parseCollection(collection.collection);
    if (seen.has(name)) {
      throw new ValidationError("The snapshot repeated a collection.");
    }
    seen.add(name);
    parseBoundedInteger(collection.itemCount, "itemCount", 0, 10_000_000);
    parseSha256(collection.fingerprint, "collection fingerprint");
  }
  parseIsoDate(snapshot.createdAt, "snapshot createdAt");
  if (snapshot.immutable !== true) {
    throw new ValidationError("Analysis snapshots must be immutable.");
  }
  return value as AnalysisRun["currentSnapshot"] & {};
}

function validateEntity(
  value: unknown,
  collection: AnalysisCollection,
  expected: {
    readonly runId: string;
    readonly snapshotId: string | undefined;
  }
): AnalysisEntity {
  const entity = expectRecord(value, `${collection} entity`);
  rejectUnknownFields(entity, entityFields[collection], `${collection} entity`);
  requireContractVersion(entity.contractVersion);
  const idField = entityIdField[collection];
  if (collection !== "agent-executions") {
    parseIdentifier(entity.entityId, "entityId");
    parseIdentifier(entity.stableSemanticId, "stableSemanticId");
  }
  parseIdentifier(entity[idField], idField);
  if (parseIdentifier(entity.runId, "entity runId") !== expected.runId) {
    throw new ValidationError("The analysis entity run identity is invalid.");
  }
  if (collection === "agent-executions") {
    validateAgentExecution(entity, expected.snapshotId);
  } else {
    if (
      parseIdentifier(entity.snapshotId, "entity snapshotId") !==
      expected.snapshotId
    ) {
      throw new ValidationError(
        "The analysis entity snapshot identity is invalid."
      );
    }
    const revision = parsePositiveInteger(entity.revision, "entity revision");
    const effectiveRevision = parsePositiveInteger(
      entity.effectiveRevision,
      "entity effectiveRevision"
    );
    if (effectiveRevision < revision) {
      throw new ValidationError(
        "The effective entity revision cannot precede its machine revision."
      );
    }
    parseSha256(
      entity.machineEntityFingerprint,
      "machineEntityFingerprint"
    );
    parseSha256(
      entity.effectiveValueFingerprint,
      "effectiveValueFingerprint"
    );
    if (
      entity.effectiveAuthority !== "runtime_agent" &&
      entity.effectiveAuthority !== "human"
    ) {
      throw new ValidationError("The effective entity authority is invalid.");
    }
    parseBoundedInteger(entity.ordinal, "entity ordinal", 0, 10_000_000);
    validateConfidence(entity.confidence);
    validateWarnings(entity.warnings, "entity warnings");
    validateProvenance(entity.provenance);
    validateEvidenceList(entity.evidence, "entity evidence");
    validateEntitySpecific(entity, collection);
  }
  const structuralLimit =
    collection === "characters"
      ? { fields: MAX_CHARACTER_RESPONSE_IDENTIFIERS, nodes: 250_000 }
      : collection === "locations"
        ? { fields: MAX_LOCATION_RESPONSE_ASSIGNMENTS, nodes: 250_000 }
        : { fields: 1_000, nodes: 5_000 };
  validateBoundedJson(
    entity,
    `${collection} entity`,
    0,
    { nodes: 0 },
    structuralLimit.fields,
    10,
    structuralLimit.nodes
  );
  return value as AnalysisEntity;
}

function validateAgentExecution(
  execution: Record<string, unknown>,
  expectedSnapshotId: string | undefined
): void {
  const snapshotId = parseIdentifier(
    execution.snapshotId,
    "execution snapshotId"
  );
  if (
    expectedSnapshotId !== undefined &&
    snapshotId !== expectedSnapshotId
  ) {
    throw new ValidationError(
      "The analysis execution snapshot identity is invalid."
    );
  }
  parseBoundedInteger(execution.ordinal, "execution ordinal", 0, 1_000);
  const agentIndex = PHASE_2_RUNTIME_AGENTS.findIndex(
    (agent) =>
      agent.agentId === execution.agentId &&
      agent.version === execution.agentVersion
  );
  if (agentIndex < 0) {
    throw new ValidationError("The analysis execution agent is unsupported.");
  }
  if (
    execution.status !== "queued" &&
    execution.status !== "running" &&
    execution.status !== "succeeded" &&
    execution.status !== "partial" &&
    execution.status !== "failed" &&
    execution.status !== "cancelled" &&
    execution.status !== "interrupted"
  ) {
    throw new ValidationError("The analysis execution status is invalid.");
  }
  parsePositiveInteger(execution.attempt, "execution attempt");
  parseUnitInterval(execution.progress, "execution progress");
  if (
    !(ANALYSIS_JOB_STAGES as readonly unknown[]).includes(
      execution.currentStage
    )
  ) {
    throw new ValidationError("The analysis execution stage is invalid.");
  }
  if (execution.checkpoint !== null) {
    const checkpoint = expectRecord(
      execution.checkpoint,
      "execution checkpoint"
    );
    rejectUnknownFields(
      checkpoint,
      [
        "checkpointId",
        "checkpointFingerprint",
        "stage",
        "schemaVersion",
        "recordedAt"
      ],
      "execution checkpoint"
    );
    parseIdentifier(checkpoint.checkpointId, "checkpointId");
    parseSha256(
      checkpoint.checkpointFingerprint,
      "checkpointFingerprint"
    );
    if (
      !(ANALYSIS_JOB_STAGES as readonly unknown[]).includes(
        checkpoint.stage
      )
    ) {
      throw new ValidationError("The execution checkpoint stage is invalid.");
    }
    requireContractVersion(checkpoint.schemaVersion);
    parseIsoDate(checkpoint.recordedAt, "checkpoint recordedAt");
  }
  requireOneOf(
    execution.retryClassification,
    ["retryable", "not_retryable", "retry_exhausted"],
    "execution retryClassification"
  );
  const retryPolicy = expectRecord(
    execution.retryPolicy,
    "execution retryPolicy"
  );
  rejectUnknownFields(
    retryPolicy,
    ["maxAttempts", "retryableFailureCodes"],
    "execution retryPolicy"
  );
  parseBoundedInteger(
    retryPolicy.maxAttempts,
    "retryPolicy maxAttempts",
    1,
    100
  );
  const retryableFailureCodes = parseBoundedArray(
    retryPolicy.retryableFailureCodes,
    "retryableFailureCodes",
    100
  );
  for (const failureCode of retryableFailureCodes) {
    parseBoundedText(
      failureCode,
      "retryableFailureCode",
      1,
      80
    );
  }
  requireOneOf(
    execution.failurePolicy,
    [
      "fail_closed_without_partial_publication",
      "preserve_validated_partial",
      "require_human"
    ],
    "execution failurePolicy"
  );
  parseSha256(execution.inputFingerprint, "execution inputFingerprint");
  if (execution.outputFingerprint !== undefined) {
    parseSha256(execution.outputFingerprint, "execution outputFingerprint");
  }
  const outputs = parseBoundedArray(
    execution.outputCollections,
    "execution outputCollections",
    ANALYSIS_ENTITY_COLLECTIONS.length
  );
  for (const output of outputs) {
    parseCollection(output);
  }
  if (execution.outputArtifactId !== undefined) {
    parseIdentifier(execution.outputArtifactId, "outputArtifactId");
  }
  validateConfidence(execution.confidence);
  validateWarnings(execution.warnings, "execution warnings");
  validateProvenance(execution.provenance);
  parseIsoDate(execution.startedAt, "execution startedAt");
  if (execution.finishedAt !== undefined) {
    parseIsoDate(execution.finishedAt, "execution finishedAt");
  }
  if (execution.failure !== null) {
    const failure = expectRecord(execution.failure, "execution failure");
    rejectUnknownFields(
      failure,
      [
        "code",
        "classification",
        "retryable",
        "message",
        "redacted"
      ],
      "execution failure"
    );
    parseBoundedText(failure.code, "failure code", 1, 80);
    requireOneOf(
      failure.classification,
      [
        "transient",
        "permanent",
        "cancelled",
        "interrupted",
        "unknown"
      ],
      "failure classification"
    );
    parseBoolean(failure.retryable, "failure retryable");
    parseBoundedText(failure.message, "failure message", 1, 2_000);
    if (failure.redacted !== true) {
      throw new ValidationError("The execution failure must be redacted.");
    }
  }
}

function validateEntitySpecific(
  entity: Record<string, unknown>,
  collection: Exclude<AnalysisCollection, "agent-executions">
): void {
  validateCoreEntityShape(entity, collection);
  const optionalIds = new Set([
    "firstSceneId",
    "lastSceneId",
    "firstBeatId",
    "lastBeatId",
    "dialogueLineId",
    "effectiveCharacterId",
    "viewpointCharacterId",
    "narratorCharacterId",
    "shiftFromPovSegmentId",
    "parentLocationId",
    "locationId",
    "validFromEventId",
    "validThroughEventId",
    "previousRelationshipId",
    "previousEmotionalStateId",
    "beatId",
    "targetCharacterId",
    "resolutionCorrectionId",
    "sourceCharacterId",
    "targetCharacterId"
  ]);
  const requiredIds = new Set([
    "chapterId",
    "sceneId",
    "characterId",
    "mentionId",
    "beatId",
    "registryCharacterId",
    "projectId",
    "storyId",
    "sourceEventId",
    "targetEventId"
  ]);
  for (const [key, item] of Object.entries(entity)) {
    if (requiredIds.has(key)) {
      parseIdentifier(item, key);
    } else if (optionalIds.has(key) && item !== null && item !== undefined) {
      parseIdentifier(item, key);
    }
  }
  if ("sourceSpan" in entity) {
    validateSourceSpan(entity.sourceSpan, "sourceSpan");
  }
  if ("exactText" in entity) {
    validateExactText(entity.exactText, "exactText");
  }
  if ("exactTimeExpression" in entity && entity.exactTimeExpression !== undefined) {
    validateExactText(entity.exactTimeExpression, "exactTimeExpression");
  }
  if (
    (collection === "chapters" || collection === "scenes") &&
    entity.effectiveBoundary !== undefined
  ) {
    requireHumanEffectiveAuthority(entity, "effective boundary");
    validateHumanEffectiveBoundary(entity.effectiveBoundary);
  }
  if (collection === "dialogue-lines") {
    const candidates = parseBoundedArray(
      entity.candidates,
      "dialogue candidates",
      STORY_ANALYSIS_LIMITS.maximumAttributionCandidatesPerLine
    );
    for (const candidateValue of candidates) {
      const candidate = expectRecord(candidateValue, "dialogue candidate");
      rejectUnknownFields(
        candidate,
        [
          "candidateId",
          "characterId",
          "rank",
          "confidence",
          "evidence",
          "rationale"
        ],
        "dialogue candidate"
      );
      parseIdentifier(candidate.candidateId, "candidateId");
      if (candidate.characterId !== null) {
        parseIdentifier(candidate.characterId, "candidate characterId");
      }
      parsePositiveInteger(candidate.rank, "candidate rank");
      validateConfidence(candidate.confidence);
      validateEvidenceList(candidate.evidence, "candidate evidence");
      parseBoundedText(candidate.rationale, "candidate rationale", 1, 2_000);
    }
    const attribution = expectRecord(
      entity.effectiveAttribution,
      "effective attribution"
    );
    rejectUnknownFields(
      attribution,
      [
        "speakerCharacterId",
        "selectedCandidateId",
        "authority",
        "correctionId",
        "confidence",
        "requiresHumanReview"
      ],
      "effective attribution"
    );
    for (const field of [
      "speakerCharacterId",
      "selectedCandidateId",
      "correctionId"
    ] as const) {
      if (attribution[field] !== null && attribution[field] !== undefined) {
        parseIdentifier(attribution[field], field);
      }
    }
    if (
      attribution.authority !== "runtime_agent" &&
      attribution.authority !== "human_correction" &&
      attribution.authority !== "unresolved"
    ) {
      throw new ValidationError("The dialogue authority is invalid.");
    }
    validateConfidence(attribution.confidence);
    parseBoolean(attribution.requiresHumanReview, "requiresHumanReview");
    const speakerState = parseSpeakerState(entity.speakerState);
    const expectedSpeakerState =
      attribution.authority === "human_correction"
        ? "corrected"
        : attribution.authority === "runtime_agent"
          ? "proposed"
          : candidates.length > 0
            ? "ambiguous"
            : "unknown";
    if (speakerState !== expectedSpeakerState) {
      throw new ValidationError(
        "Dialogue speaker state does not match effective attribution."
      );
    }
  }
  if (collection === "narration-spans") {
    if (entity.narratorCharacterId !== null) {
      parseIdentifier(entity.narratorCharacterId, "narratorCharacterId");
    }
    if (
      entity.classification !== "direct_narration" &&
      entity.classification !== "internal_thought" &&
      entity.classification !== "quoted_material" &&
      entity.classification !== "epigraph_or_document" &&
      entity.classification !== "unresolved"
    ) {
      throw new ValidationError("The narration classification is invalid.");
    }
  }
  if (collection === "characters") {
    if (
      entity.registryScope !== "project_story" ||
      entity.stableAcrossCompatibleRuns !== true
    ) {
      throw new ValidationError("The character registry scope is invalid.");
    }
    parseBoundedText(entity.canonicalName, "canonicalName", 1, 512);
    parseBoundedText(
      entity.normalizedCanonicalName,
      "normalizedCanonicalName",
      1,
      512
    );
    requireOneOf(
      entity.kind,
      ["person", "group", "nonhuman", "unknown"],
      "character kind"
    );
    requireOneOf(
      entity.identityStatus,
      ["resolved", "ambiguous", "unresolved", "unknown"],
      "identityStatus"
    );
    const aliases = parseBoundedArray(entity.aliases, "character aliases", 256);
    for (const aliasValue of aliases) {
      validateCharacterAliasValue(aliasValue);
    }
    const honorifics = parseBoundedArray(
      entity.honorifics,
      "character honorifics",
      256
    );
    for (const honorificValue of honorifics) {
      const honorific = expectRecord(
        honorificValue,
        "character honorific"
      );
      rejectUnknownFields(
        honorific,
        [
          "honorific",
          "normalizedHonorific",
          "confidence",
          "evidence"
        ],
        "character honorific"
      );
      parseBoundedText(honorific.honorific, "honorific", 1, 512);
      parseBoundedText(
        honorific.normalizedHonorific,
        "normalizedHonorific",
        1,
        512
      );
      validateConfidence(honorific.confidence);
      validateEvidenceList(honorific.evidence, "honorific evidence");
    }
    const pronouns = parseBoundedArray(
      entity.pronounEvidence,
      "character pronoun evidence",
      256
    );
    for (const pronounValue of pronouns) {
      const pronoun = expectRecord(pronounValue, "character pronoun");
      rejectUnknownFields(
        pronoun,
        [
          "pronoun",
          "normalizedPronoun",
          "resolution",
          "confidence",
          "evidence"
        ],
        "character pronoun"
      );
      parseBoundedText(pronoun.pronoun, "pronoun", 1, 128);
      parseBoundedText(
        pronoun.normalizedPronoun,
        "normalizedPronoun",
        1,
        128
      );
      requireOneOf(
        pronoun.resolution,
        ["resolved", "ambiguous", "unresolved"],
        "pronoun resolution"
      );
      validateConfidence(pronoun.confidence);
      validateEvidenceList(pronoun.evidence, "pronoun evidence");
    }
    parseIdentifierArray(
      entity.namedMentionIds,
      "namedMentionIds",
      MAX_CHARACTER_RESPONSE_IDENTIFIERS
    );
    parseIdentifierArray(
      entity.ambiguousMentionIds,
      "ambiguousMentionIds",
      MAX_CHARACTER_RESPONSE_IDENTIFIERS
    );
    parseRequiredNullableIdentifier(
      entity.firstMentionId,
      "firstMentionId"
    );
    parseRequiredNullableIdentifier(
      entity.lastMentionId,
      "lastMentionId"
    );
    validateEvidenceList(entity.firstEvidence, "firstEvidence");
    validateEvidenceList(entity.lastEvidence, "lastEvidence");
    const mentionCount = parseBoundedInteger(
      entity.mentionCount,
      "mentionCount",
      0,
      10_000_000
    );
    const firstEvidence = entity.firstEvidence as readonly unknown[];
    const lastEvidence = entity.lastEvidence as readonly unknown[];
    if (mentionCount === 0) {
      if (
        entity.firstMentionId !== null ||
        entity.lastMentionId !== null ||
        firstEvidence.length !== 0 ||
        lastEvidence.length !== 0
      ) {
        throw new ValidationError(
          "A character without effective mentions must have null mention bounds and empty boundary evidence."
        );
      }
    } else if (
      entity.firstMentionId === null ||
      entity.lastMentionId === null ||
      firstEvidence.length === 0 ||
      lastEvidence.length === 0
    ) {
      throw new ValidationError(
        "A character with effective mentions must have mention bounds and boundary evidence."
      );
    }
    if (entity.effectiveRegistry !== undefined) {
      requireHumanEffectiveAuthority(entity, "effective registry");
      validateHumanEffectiveRegistry(entity.effectiveRegistry);
    }
  }
  if (collection === "locations") {
    requireOneOf(
      entity.kind,
      [
        "interior",
        "exterior",
        "vehicle",
        "region",
        "abstract",
        "unknown"
      ],
      "location kind"
    );
    parseIdentifierArray(
      entity.sceneIds,
      "sceneIds",
      MAX_LOCATION_RESPONSE_ASSIGNMENTS
    );
    const assignments = parseBoundedArray(
      entity.sceneAssignments,
      "location scene assignments",
      MAX_LOCATION_RESPONSE_ASSIGNMENTS
    );
    for (const assignmentValue of assignments) {
      const assignment = expectRecord(
        assignmentValue,
        "location scene assignment"
      );
      rejectUnknownFields(
        assignment,
        [
          "assignmentId",
          "locationId",
          "sceneId",
          "role",
          "confidence",
          "evidence"
        ],
        "location scene assignment"
      );
      parseIdentifier(assignment.assignmentId, "assignmentId");
      parseIdentifier(assignment.locationId, "assignment locationId");
      parseIdentifier(assignment.sceneId, "assignment sceneId");
      requireOneOf(
        assignment.role,
        ["primary", "secondary", "mentioned"],
        "location assignment role"
      );
      validateConfidence(assignment.confidence);
      validateEvidenceList(assignment.evidence, "assignment evidence");
    }
  }
  if (collection === "temporal-constraints") {
    requireOneOf(
      entity.relation,
      [
        "before",
        "after",
        "same_time",
        "overlaps",
        "during",
        "contains",
        "unknown"
      ],
      "temporal relation"
    );
    parseBoolean(entity.approximate, "temporal approximate");
    requireOneOf(
      entity.status,
      ["consistent", "conflicting", "unresolved"],
      "temporal status"
    );
  }
  if (collection === "relationships") {
    parseIdentifierArray(
      entity.sourceCandidateCharacterIds,
      "sourceCandidateCharacterIds",
      MAX_CORRECTION_IDENTIFIERS
    );
    parseIdentifierArray(
      entity.targetCandidateCharacterIds,
      "targetCandidateCharacterIds",
      MAX_CORRECTION_IDENTIFIERS
    );
    requireOneOf(
      entity.resolution,
      ["resolved", "ambiguous", "unresolved"],
      "relationship resolution"
    );
    if (
      entity.resolution === "resolved" &&
      (entity.sourceCharacterId === null ||
        entity.targetCharacterId === null)
    ) {
      throw new ValidationError(
        "A resolved relationship requires both character identities."
      );
    }
    validateRelationshipValue(entity);
  }
  if (collection === "emotional-states") {
    validateSubjectDiscriminant(entity, "emotional state");
    parseEmotionalStatePatch({
      emotion: entity.emotion,
      customEmotion: entity.customEmotion ?? null,
      note: entity.note,
      valence: entity.valence,
      arousal: entity.arousal,
      intensity: entity.intensity,
      progression: entity.progression
    });
  }
  if (collection === "dramatic-intents") {
    validateSubjectDiscriminant(entity, "dramatic intent");
    parseDramaticIntentPatch({
      intent: entity.intent,
      customIntent: entity.customIntent ?? null,
      dramaticFunction: entity.dramaticFunction,
      customDramaticFunction:
        entity.customDramaticFunction ?? null,
      note: entity.note,
      targetCharacterId: entity.targetCharacterId ?? null,
      status: entity.status
    });
  }
}

function validateCoreEntityShape(
  entity: Record<string, unknown>,
  collection: Exclude<AnalysisCollection, "agent-executions">
): void {
  switch (collection) {
    case "chapters": {
      if (entity.title !== undefined) {
        parseBoundedText(entity.title, "chapter title", 1, 512);
      }
      validateStrictSourceSpan(entity.sourceSpan, "chapter sourceSpan");
      parseRequiredNullableIdentifier(entity.firstSceneId, "firstSceneId");
      parseRequiredNullableIdentifier(entity.lastSceneId, "lastSceneId");
      const count = parseBoundedInteger(
        entity.sceneCount,
        "sceneCount",
        0,
        10_000_000
      );
      assertNullableRangeEndpoints(
        count,
        entity.firstSceneId,
        entity.lastSceneId,
        "chapter scenes"
      );
      break;
    }
    case "scenes": {
      if (entity.heading !== undefined) {
        parseBoundedText(entity.heading, "scene heading", 1, 512);
      }
      validateStrictSourceSpan(entity.sourceSpan, "scene sourceSpan");
      requireOneOf(
        entity.boundaryKind,
        [
          "chapter_start",
          "explicit_scene_break",
          "heading",
          "inferred"
        ],
        "scene boundaryKind"
      );
      parseRequiredNullableIdentifier(entity.firstBeatId, "firstBeatId");
      parseRequiredNullableIdentifier(entity.lastBeatId, "lastBeatId");
      const count = parseBoundedInteger(
        entity.beatCount,
        "beatCount",
        0,
        10_000_000
      );
      assertNullableRangeEndpoints(
        count,
        entity.firstBeatId,
        entity.lastBeatId,
        "scene beats"
      );
      break;
    }
    case "beats": {
      requireOneOf(
        entity.kind,
        ["narration", "dialogue", "action", "description", "transition"],
        "beat kind"
      );
      validateStrictSourceSpan(entity.sourceSpan, "beat sourceSpan");
      parseBoundedText(entity.summary, "beat summary", 1, 2_000);
      if (entity.dialogueLineId !== undefined) {
        parseIdentifier(entity.dialogueLineId, "beat dialogueLineId");
      }
      if (entity.narration !== undefined) {
        validateEntity(entity.narration, "narration-spans", {
          runId: parseIdentifier(entity.runId, "beat runId"),
          snapshotId: parseIdentifier(
            entity.snapshotId,
            "beat snapshotId"
          )
        });
      }
      break;
    }
    case "mentions":
      validateExactText(entity.exactText, "mention exactText");
      requireOneOf(
        entity.mentionKind,
        ["proper_name", "alias", "honorific", "pronoun", "description"],
        "mention kind"
      );
      requireOneOf(
        entity.resolution,
        ["resolved", "ambiguous", "unresolved"],
        "mention resolution"
      );
      parseRequiredNullableIdentifier(
        entity.effectiveCharacterId,
        "mention effectiveCharacterId"
      );
      parseIdentifierArray(
        entity.candidateCharacterIds,
        "mention candidateCharacterIds",
        MAX_CORRECTION_IDENTIFIERS
      );
      if (
        entity.resolution === "resolved" &&
        entity.effectiveCharacterId === null
      ) {
        throw new ValidationError(
          "A resolved mention requires an effective character identity."
        );
      }
      break;
    case "dialogue-lines":
      validateExactText(entity.exactText, "dialogue exactText");
      requireOneOf(
        entity.distinction,
        [
          "spoken_dialogue",
          "internal_thought",
          "quoted_material",
          "epigraph_or_document",
          "unresolved_speech",
          "narration",
          "ambiguous"
        ],
        "dialogue distinction"
      );
      break;
    case "narration-spans":
      validateExactText(entity.exactText, "narration exactText");
      break;
    case "pov-segments":
      validateStrictSourceSpan(entity.sourceSpan, "POV sourceSpan");
      requireOneOf(
        entity.mode,
        [
          "first_person",
          "second_person",
          "third_person_limited",
          "third_person_omniscient",
          "mixed",
          "experimental",
          "unknown"
        ],
        "POV mode"
      );
      parseRequiredNullableIdentifier(
        entity.viewpointCharacterId,
        "POV viewpointCharacterId"
      );
      parseRequiredNullableIdentifier(
        entity.narratorCharacterId,
        "POV narratorCharacterId"
      );
      if (entity.shiftFromPovSegmentId !== undefined) {
        parseIdentifier(
          entity.shiftFromPovSegmentId,
          "POV shiftFromPovSegmentId"
        );
      }
      requireOneOf(
        entity.shiftKind,
        ["initial", "scene_boundary", "mid_scene", "uncertain"],
        "POV shiftKind"
      );
      break;
    case "locations": {
      parseBoundedText(
        entity.canonicalName,
        "location canonicalName",
        1,
        512
      );
      parseBoundedText(
        entity.normalizedCanonicalName,
        "location normalizedCanonicalName",
        1,
        512
      );
      const aliases = parseBoundedArray(
        entity.aliases,
        "location aliases",
        256
      );
      for (const alias of aliases) {
        parseBoundedText(alias, "location alias", 1, 512);
      }
      parseRequiredNullableIdentifier(
        entity.parentLocationId,
        "location parentLocationId"
      );
      parseBoundedInteger(
        entity.sceneCount,
        "location sceneCount",
        0,
        10_000_000
      );
      break;
    }
    case "timeline-events":
      requireOneOf(
        entity.kind,
        [
          "present_action",
          "flashback",
          "flashforward",
          "backstory",
          "relative_time",
          "ellipsis",
          "unknown"
        ],
        "timeline event kind"
      );
      parseBoundedText(entity.label, "timeline event label", 1, 1_000);
      parseBoundedInteger(
        entity.narrativeOrdinal,
        "timeline narrativeOrdinal",
        0,
        10_000_000
      );
      if (entity.chronologicalOrdinal !== null) {
        parseBoundedInteger(
          entity.chronologicalOrdinal,
          "timeline chronologicalOrdinal",
          0,
          10_000_000
        );
      }
      if (entity.exactTimeExpression !== undefined) {
        validateExactText(
          entity.exactTimeExpression,
          "timeline exactTimeExpression"
        );
      }
      parseRequiredNullableIdentifier(
        entity.locationId,
        "timeline locationId"
      );
      parseIdentifierArray(
        entity.participantCharacterIds,
        "timeline participantCharacterIds",
        MAX_CORRECTION_IDENTIFIERS
      );
      break;
    case "continuity-findings": {
      requireOneOf(
        entity.category,
        [
          "possible_duplicate_character",
          "identity_contradiction",
          "alias_conflict",
          "dialogue_speaker_conflict",
          "chronology_conflict",
          "location_conflict",
          "attribute_conflict",
          "unexplained_object_state_change",
          "unexplained_character_state_change",
          "pov_discontinuity",
          "scene_boundary_uncertainty",
          "unresolved_reference",
          "extraction_uncertainty",
          "other"
        ],
        "continuity category"
      );
      requireOneOf(
        entity.severity,
        ["info", "warning", "error", "blocker"],
        "continuity severity"
      );
      requireOneOf(
        entity.machineStatus,
        ["open", "superseded", "resolved_by_correction"],
        "continuity machineStatus"
      );
      parseBoundedText(
        entity.explanation,
        "continuity explanation",
        1,
        2_000
      );
      parseBoundedText(
        entity.suggestedReviewAction,
        "continuity suggestedReviewAction",
        1,
        2_000
      );
      parseIdentifierArray(
        entity.relatedEntityIds,
        "continuity relatedEntityIds",
        MAX_CORRECTION_IDENTIFIERS
      );
      parseBoolean(
        entity.requiresHumanReview,
        "continuity requiresHumanReview"
      );
      if (entity.humanDisposition !== undefined) {
        requireHumanEffectiveAuthority(entity, "continuity disposition");
        validateHumanContinuityDisposition(entity.humanDisposition);
      }
      break;
    }
    case "characters":
    case "temporal-constraints":
    case "relationships":
    case "emotional-states":
    case "dramatic-intents":
      break;
  }
}

function parseRequiredNullableIdentifier(
  value: unknown,
  field: string
): string | null {
  if (value === null) {
    return null;
  }
  return parseIdentifier(value, field);
}

function assertNullableRangeEndpoints(
  count: number,
  first: unknown,
  last: unknown,
  field: string
): void {
  if (
    (count === 0 && (first !== null || last !== null)) ||
    (count > 0 && (first === null || last === null))
  ) {
    throw new ValidationError(`${field} count and endpoints disagree.`);
  }
}

function validateHumanContinuityDisposition(value: unknown): void {
  const disposition = expectRecord(value, "human continuity disposition");
  rejectUnknownFields(
    disposition,
    [
      "disposition",
      "explanation",
      "actorId",
      "recordedAt",
      "provenance",
      "correctionId"
    ],
    "human continuity disposition"
  );
  requireOneOf(
    disposition.disposition,
    [
      "confirmed_issue",
      "intentional",
      "false_positive",
      "deferred",
      "corrected",
      "unresolved"
    ],
    "human continuity disposition"
  );
  parseBoundedText(
    disposition.explanation,
    "human continuity explanation",
    1,
    MAX_CORRECTION_TEXT_CODE_POINTS
  );
  parseIdentifier(disposition.actorId, "human continuity actorId");
  parseIsoDate(disposition.recordedAt, "human continuity recordedAt");
  validateProvenance(disposition.provenance);
  if (disposition.correctionId !== undefined) {
    parseIdentifier(
      disposition.correctionId,
      "human continuity correctionId"
    );
  }
}

function requireHumanEffectiveAuthority(
  entity: Record<string, unknown>,
  field: string
): void {
  if (entity.effectiveAuthority !== "human") {
    throw new ValidationError(
      `The ${field} requires human effective authority.`
    );
  }
}

function validateHumanEffectiveBoundary(value: unknown): void {
  const boundary = expectRecord(value, "effective boundary");
  rejectUnknownFields(
    boundary,
    [
      "parentEntityId",
      "ordinal",
      "sourceSpan",
      "authority",
      "correctionId",
      "operation",
      "included"
    ],
    "effective boundary"
  );
  parseIdentifier(boundary.parentEntityId, "effective boundary parentEntityId");
  parseBoundedInteger(
    boundary.ordinal,
    "effective boundary ordinal",
    0,
    10_000_000
  );
  validateStrictSourceSpan(
    boundary.sourceSpan,
    "effective boundary sourceSpan"
  );
  if (boundary.authority !== "human") {
    throw new ValidationError(
      "The effective boundary authority must be human."
    );
  }
  parseIdentifier(boundary.correctionId, "effective boundary correctionId");
  const operation = requireOneOf(
    boundary.operation,
    ["add", "move", "remove"],
    "effective boundary operation"
  );
  const expectedIncluded = operation !== "remove";
  if (boundary.included !== expectedIncluded) {
    throw new ValidationError(
      `The effective boundary ${operation} operation has an invalid inclusion state.`
    );
  }
}

function validateHumanEffectiveRegistry(value: unknown): void {
  const registry = expectRecord(value, "effective registry");
  if (registry.authority !== "human") {
    throw new ValidationError(
      "The effective registry authority must be human."
    );
  }
  parseIdentifier(registry.correctionId, "effective registry correctionId");
  const operation = requireOneOf(
    registry.operation,
    ["merge", "split"],
    "effective registry operation"
  );
  if (operation === "merge") {
    rejectUnknownFields(
      registry,
      [
        "authority",
        "correctionId",
        "operation",
        "mergeIntoCharacterId"
      ],
      "effective registry merge"
    );
    parseIdentifier(
      registry.mergeIntoCharacterId,
      "effective registry mergeIntoCharacterId"
    );
    return;
  }

  rejectUnknownFields(
    registry,
    ["authority", "correctionId", "operation", "splitIdentity"],
    "effective registry split"
  );
  const splitIdentity = expectRecord(
    registry.splitIdentity,
    "effective registry split identity"
  );
  rejectUnknownFields(
    splitIdentity,
    [
      "registryCharacterId",
      "canonicalName",
      "normalizedCanonicalName",
      "mentionIds"
    ],
    "effective registry split identity"
  );
  parseIdentifier(
    splitIdentity.registryCharacterId,
    "split identity registryCharacterId"
  );
  parseBoundedText(splitIdentity.canonicalName, "split identity canonicalName", 1, 512);
  parseBoundedText(
    splitIdentity.normalizedCanonicalName,
    "split identity normalizedCanonicalName",
    1,
    512
  );
  parseIdentifierArray(
    splitIdentity.mentionIds,
    "split identity mentionIds",
    MAX_CORRECTION_IDENTIFIERS,
    1
  );
}

function validateCorrection(
  value: unknown,
  expected: { readonly projectId: string; readonly runId: string }
): AnalysisCorrection {
  const correction = expectRecord(value, "analysis correction");
  rejectUnknownFields(
    correction,
    [
      "contractVersion",
      "correctionId",
      "projectId",
      "runId",
      "snapshotId",
      "category",
      "targetCollection",
      "targetEntityId",
      "expectedTargetRevision",
      "expectedRunFingerprint",
      "previousValueFingerprint",
      "correctedValueFingerprint",
      "patch",
      "actor",
      "reason",
      "recordedAt",
      "immutable",
      "lockedAgainstAutomation",
      "supersedesCorrectionId",
      "idempotencyFingerprint"
    ],
    "analysis correction"
  );
  requireContractVersion(correction.contractVersion);
  for (const field of [
    "correctionId",
    "projectId",
    "runId",
    "snapshotId",
    "targetEntityId"
  ] as const) {
    parseIdentifier(correction[field], field);
  }
  if (
    correction.projectId !== expected.projectId ||
    correction.runId !== expected.runId
  ) {
    throw new ValidationError("The analysis correction identity is invalid.");
  }
  const category = parseCorrectionCategory(correction.category);
  const targetCollection = parseCollection(correction.targetCollection);
  if (!correctionCollections[category].includes(targetCollection)) {
    throw new ValidationError("The saved correction target is invalid.");
  }
  parsePositiveInteger(
    correction.expectedTargetRevision,
    "expectedTargetRevision"
  );
  for (const field of [
    "expectedRunFingerprint",
    "previousValueFingerprint",
    "correctedValueFingerprint",
    "idempotencyFingerprint"
  ] as const) {
    parseSha256(correction[field], field);
  }
  parseCorrectionSelection(
    category,
    targetCollection,
    correction.patch,
    "record"
  );
  const actor = expectRecord(correction.actor, "correction actor");
  rejectUnknownFields(actor, ["classification", "actorId"], "correction actor");
  if (actor.classification !== "human") {
    throw new ValidationError("The correction actor must be human.");
  }
  parseIdentifier(actor.actorId, "correction actorId");
  parseBoundedText(correction.reason, "correction reason", 1, 1_000);
  parseIsoDate(correction.recordedAt, "correction recordedAt");
  if (
    correction.immutable !== true ||
    correction.lockedAgainstAutomation !== true
  ) {
    throw new ValidationError(
      "Analysis corrections must be immutable and locked."
    );
  }
  if (correction.supersedesCorrectionId !== undefined) {
    parseIdentifier(
      correction.supersedesCorrectionId,
      "supersedesCorrectionId"
    );
  }
  return value as AnalysisCorrection;
}

function validateReview(
  value: unknown,
  expected: { readonly projectId: string; readonly runId: string },
  expectedRun: GateEvidenceRun
): AnalysisReview {
  const review = expectRecord(value, "analysis review");
  rejectUnknownFields(
    review,
    [
      "contractVersion",
      "reviewId",
      "projectId",
      "gateId",
      "runId",
      "snapshotId",
      "state",
      "revision",
      "artifactFingerprint",
      "evidenceFingerprint",
      "evidence",
      "openWarningIds",
      "acknowledgedWarningIds",
      "latestDecisionId",
      "latestDecision",
      "provenance",
      "updatedAt"
    ],
    "analysis review"
  );
  requireContractVersion(review.contractVersion);
  for (const field of [
    "reviewId",
    "projectId",
    "runId",
    "snapshotId"
  ] as const) {
    parseIdentifier(review[field], field);
  }
  if (
    review.projectId !== expected.projectId ||
    review.runId !== expected.runId
  ) {
    throw new ValidationError("The analysis review identity is invalid.");
  }
  parseGateId(review.gateId);
  if (
    review.state !== "pending" &&
    review.state !== "approved" &&
    review.state !== "changes_requested" &&
    review.state !== "rejected" &&
    review.state !== "invalidated"
  ) {
    throw new ValidationError("The analysis review state is invalid.");
  }
  parsePositiveInteger(review.revision, "review revision");
  parseSha256(review.artifactFingerprint, "artifactFingerprint");
  parseSha256(review.evidenceFingerprint, "evidenceFingerprint");
  const evidence = validateGateEvidence(
    review.evidence,
    expected,
    expectedRun
  );
  if (
    review.snapshotId !== evidence.snapshotId ||
    review.artifactFingerprint !== evidence.artifactFingerprint ||
    review.evidenceFingerprint !== evidence.evidenceFingerprint
  ) {
    throw new ValidationError(
      "The analysis review evidence cross-links are inconsistent."
    );
  }
  parseIdentifierArray(review.openWarningIds, "openWarningIds", MAX_GATE_WARNINGS);
  parseIdentifierArray(
    review.acknowledgedWarningIds,
    "acknowledgedWarningIds",
    MAX_GATE_WARNINGS
  );
  if (review.latestDecisionId === undefined) {
    if (review.latestDecision !== null) {
      throw new ValidationError(
        "A review without a latest decision id must expose a null latest decision."
      );
    }
  } else {
    parseIdentifier(review.latestDecisionId, "latestDecisionId");
    if (review.latestDecision === null) {
      throw new ValidationError(
        "The latest review decision record is unavailable."
      );
    }
    const latestDecision = validateGateDecision(
      review.latestDecision,
      {
        projectId: expected.projectId,
        runId: expected.runId,
        gateId: review.gateId as AnalysisGateId
      },
      undefined
    );
    const latestEvidence = expectRecord(
      latestDecision.evidence,
      "latest decision evidence"
    );
    const decisionProvenance = expectRecord(
      latestDecision.provenance,
      "latest decision provenance"
    );
    const decisionIsCurrent =
      review.state === "approved" ||
      review.state === "changes_requested" ||
      review.state === "rejected";
    if (
      latestDecision.decisionId !== review.latestDecisionId ||
      latestDecision.reviewId !== review.reviewId ||
      latestDecision.projectId !== review.projectId ||
      latestDecision.gateId !== review.gateId ||
      latestDecision.runId !== review.runId ||
      latestDecision.snapshotId !== review.snapshotId ||
      latestEvidence.sourceDocumentId !==
        expectedRun.sourceDocumentId ||
      latestEvidence.extractionId !== expectedRun.extractionId ||
      latestEvidence.extractionRevision !==
        expectedRun.extractionRevision ||
      latestEvidence.storyId !== expectedRun.storyId ||
      latestEvidence.profileId !== expectedRun.profile.profileId ||
      latestEvidence.profileFingerprint !==
        expectedRun.profile.fingerprint ||
      latestEvidence.runFingerprint !== expectedRun.runFingerprint ||
      latestEvidence.snapshotId !== review.snapshotId ||
      (decisionIsCurrent &&
        (latestDecision.decision !== review.state ||
          latestDecision.artifactFingerprint !==
            review.artifactFingerprint ||
          latestDecision.evidenceFingerprint !==
            review.evidenceFingerprint ||
          !jsonValuesEqual(latestDecision.evidence, review.evidence))) ||
      decisionProvenance.origin !== "human_review" ||
      decisionProvenance.inputFingerprint !==
        latestDecision.evidenceFingerprint ||
      decisionProvenance.recordedAt !== latestDecision.decidedAt ||
      decisionProvenance.deterministic !== false
    ) {
      throw new ValidationError(
        "The latest review decision cross-links are inconsistent."
      );
    }
  }
  validateProvenance(review.provenance);
  parseIsoDate(review.updatedAt, "review updatedAt");
  return value as AnalysisReview;
}

function validateGateDecision(
  value: unknown,
  expected: {
    readonly projectId: string;
    readonly runId: string;
    readonly gateId: AnalysisGateId;
  },
  expectedRun?: GateEvidenceRun
) {
  const decision = expectRecord(value, "analysis gate decision");
  rejectUnknownFields(
    decision,
    [
      "contractVersion",
      "decisionId",
      "reviewId",
      "projectId",
      "gateId",
      "runId",
      "snapshotId",
      "decision",
      "artifactFingerprint",
      "evidenceFingerprint",
      "evidence",
      "actor",
      "rationale",
      "acknowledgedWarningIds",
      "provenance",
      "decidedAt",
      "immutable",
      "supersedesDecisionId"
    ],
    "analysis gate decision"
  );
  requireContractVersion(decision.contractVersion);
  for (const field of [
    "decisionId",
    "reviewId",
    "projectId",
    "runId",
    "snapshotId"
  ] as const) {
    parseIdentifier(decision[field], field);
  }
  if (
    decision.projectId !== expected.projectId ||
    decision.runId !== expected.runId ||
    decision.gateId !== expected.gateId
  ) {
    throw new ValidationError("The gate decision identity is invalid.");
  }
  parseGateId(decision.gateId);
  if (
    decision.decision !== "approved" &&
    decision.decision !== "rejected" &&
    decision.decision !== "changes_requested"
  ) {
    throw new ValidationError("The gate decision action is invalid.");
  }
  parseSha256(decision.artifactFingerprint, "artifactFingerprint");
  parseSha256(decision.evidenceFingerprint, "evidenceFingerprint");
  const evidence = validateGateEvidence(
    decision.evidence,
    expected,
    expectedRun
  );
  if (
    decision.snapshotId !== evidence.snapshotId ||
    decision.artifactFingerprint !== evidence.artifactFingerprint ||
    decision.evidenceFingerprint !== evidence.evidenceFingerprint
  ) {
    throw new ValidationError(
      "The gate decision evidence cross-links are inconsistent."
    );
  }
  const actor = expectRecord(decision.actor, "gate decision actor");
  rejectUnknownFields(actor, ["classification", "actorId"], "gate decision actor");
  if (actor.classification !== "human") {
    throw new ValidationError("The gate decision actor must be human.");
  }
  parseIdentifier(actor.actorId, "gate decision actorId");
  parseBoundedText(decision.rationale, "decision rationale", 1, 4_000);
  parseIdentifierArray(
    decision.acknowledgedWarningIds,
    "decision acknowledgedWarningIds",
    MAX_GATE_WARNINGS
  );
  validateProvenance(decision.provenance);
  parseIsoDate(decision.decidedAt, "decidedAt");
  if (decision.immutable !== true) {
    throw new ValidationError("Gate decisions must be immutable.");
  }
  if (decision.supersedesDecisionId !== undefined) {
    parseIdentifier(decision.supersedesDecisionId, "supersedesDecisionId");
    if (decision.supersedesDecisionId === decision.decisionId) {
      throw new ValidationError(
        "A gate decision cannot supersede itself."
      );
    }
  }
  return value as DecideAnalysisReviewResponse["decision"];
}

function validateGateEvidence(
  value: unknown,
  expected: { readonly projectId: string; readonly runId: string },
  expectedRun?: GateEvidenceRun
): Record<string, unknown> {
  const evidence = expectRecord(value, "gate evidence");
  rejectUnknownFields(
    evidence,
    [
      "projectId",
      "sourceDocumentId",
      "extractionId",
      "extractionRevision",
      "storyId",
      "profileId",
      "profileFingerprint",
      "runId",
      "runFingerprint",
      "snapshotId",
      "snapshotRevision",
      "snapshotFingerprint",
      "artifactFingerprint",
      "evidenceFingerprint"
    ],
    "gate evidence"
  );
  for (const field of [
    "projectId",
    "sourceDocumentId",
    "extractionId",
    "storyId",
    "runId",
    "snapshotId"
  ] as const) {
    parseIdentifier(evidence[field], field);
  }
  if (
    evidence.projectId !== expected.projectId ||
    evidence.runId !== expected.runId
  ) {
    throw new ValidationError("The gate evidence identity is invalid.");
  }
  parsePositiveInteger(evidence.extractionRevision, "extractionRevision");
  parsePositiveInteger(evidence.snapshotRevision, "snapshotRevision");
  if (evidence.profileId !== WHOLE_BOOK_ANALYSIS_PROFILE_ID) {
    throw new ValidationError("The gate evidence profile is invalid.");
  }
  for (const field of [
    "profileFingerprint",
    "runFingerprint",
    "snapshotFingerprint",
    "artifactFingerprint",
    "evidenceFingerprint"
  ] as const) {
    parseSha256(evidence[field], field);
  }
  if (
    evidence.profileFingerprint !==
      WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
  ) {
    throw new ValidationError(
      "The gate evidence profile fingerprint is invalid."
    );
  }
  if (
    expectedRun !== undefined &&
    (evidence.sourceDocumentId !== expectedRun.sourceDocumentId ||
      evidence.extractionId !== expectedRun.extractionId ||
      evidence.extractionRevision !== expectedRun.extractionRevision ||
      evidence.storyId !== expectedRun.storyId ||
      evidence.profileId !== expectedRun.profile.profileId ||
      evidence.profileFingerprint !== expectedRun.profile.fingerprint ||
      evidence.runFingerprint !== expectedRun.runFingerprint ||
      expectedRun.currentSnapshot === null ||
      evidence.snapshotId !== expectedRun.currentSnapshot.snapshotId ||
      evidence.snapshotRevision !== expectedRun.currentSnapshot.revision ||
      evidence.snapshotFingerprint !==
        expectedRun.currentSnapshot.snapshotFingerprint)
  ) {
    throw new ValidationError(
      "The gate evidence did not cross-link the returned run and snapshot."
    );
  }
  return evidence;
}

function validateCountSummary(value: unknown): void {
  const summary = expectRecord(value, "analysis count summary");
  const fields = [
    "agentExecutions",
    "chapters",
    "scenes",
    "beats",
    "characters",
    "mentions",
    "dialogueLines",
    "narrationSpans",
    "povSegments",
    "locations",
    "timelineEvents",
    "temporalConstraints",
    "relationships",
    "emotionalStates",
    "dramaticIntents",
    "continuityFindings",
    "corrections"
  ] as const;
  rejectUnknownFields(summary, fields, "analysis count summary");
  for (const field of fields) {
    parseBoundedInteger(summary[field], field, 0, 10_000_000);
  }
}

function validateConfidence(value: unknown): AnalysisConfidence {
  const confidence = expectRecord(value, "analysis confidence");
  rejectUnknownFields(
    confidence,
    ["score", "classification", "basis", "calibrationId"],
    "analysis confidence"
  );
  const score = parseUnitInterval(confidence.score, "confidence score");
  const classification = confidence.classification;
  if (
    (classification === "unknown" && score === 0) ||
    (classification === "low" && score > 0 && score < 0.75) ||
    (classification === "medium" && score >= 0.75 && score < 0.85) ||
    (classification === "high" && score >= 0.85)
  ) {
    // Valid classification band.
  } else {
    throw new ValidationError(
      "Analysis confidence score and classification disagree."
    );
  }
  parseBoundedText(confidence.basis, "confidence basis", 1, 1_000);
  if (confidence.calibrationId !== undefined) {
    parseBoundedText(confidence.calibrationId, "calibrationId", 1, 128);
  }
  return value as AnalysisConfidence;
}

function validateWarnings(value: unknown, field: string): void {
  const warnings = parseBoundedArray(
    value,
    field,
    STORY_ANALYSIS_LIMITS.maximumWarningsPerEntity
  );
  for (const warningValue of warnings) {
    const warning = expectRecord(warningValue, "analysis warning");
    rejectUnknownFields(
      warning,
      [
        "code",
        "severity",
        "message",
        "requiresHumanReview",
        "relatedEntityIds",
        "evidence"
      ],
      "analysis warning"
    );
    parseBoundedText(warning.code, "warning code", 1, 80);
    if (
      warning.severity !== "info" &&
      warning.severity !== "warning" &&
      warning.severity !== "error" &&
      warning.severity !== "blocker"
    ) {
      throw new ValidationError("The warning severity is invalid.");
    }
    parseBoundedText(warning.message, "warning message", 1, 1_000);
    parseBoolean(warning.requiresHumanReview, "requiresHumanReview");
    parseIdentifierArray(warning.relatedEntityIds, "relatedEntityIds", 64);
    validateEvidenceList(warning.evidence, "warning evidence");
  }
}

function validateEvidenceList(value: unknown, field: string): void {
  const evidence = parseBoundedArray(
    value,
    field,
    STORY_ANALYSIS_LIMITS.maximumEvidenceSpansPerClaim
  );
  for (const item of evidence) {
    validateEvidence(item);
  }
}

function validateEvidence(value: unknown): AnalysisEvidenceExcerpt {
  const evidence = expectRecord(value, "analysis evidence");
  rejectUnknownFields(
    evidence,
    [
      "sourceDocumentId",
      "extractionId",
      "extractionRevision",
      "offsetUnit",
      "startOffset",
      "endOffset",
      "textSha256",
      "excerptStartOffset",
      "excerptEndOffset",
      "excerptText",
      "excerptSha256",
      "excerptTruncated"
    ],
    "analysis evidence"
  );
  validateSourceSpan(evidence, "analysis evidence");
  const start = parseBoundedInteger(
    evidence.startOffset,
    "startOffset",
    0,
    Number.MAX_SAFE_INTEGER
  );
  const end = parseBoundedInteger(
    evidence.endOffset,
    "endOffset",
    start + 1,
    Number.MAX_SAFE_INTEGER
  );
  const excerptStart = parseBoundedInteger(
    evidence.excerptStartOffset,
    "excerptStartOffset",
    start,
    end
  );
  parseBoundedInteger(
    evidence.excerptEndOffset,
    "excerptEndOffset",
    excerptStart,
    end
  );
  parseBoundedText(
    evidence.excerptText,
    "excerptText",
    0,
    STORY_ANALYSIS_LIMITS.maximumEvidenceExcerptCodePoints,
    false
  );
  parseSha256(evidence.excerptSha256, "excerptSha256");
  parseBoolean(evidence.excerptTruncated, "excerptTruncated");
  return value as AnalysisEvidenceExcerpt;
}

function validateSourceSpan(value: unknown, field: string): void {
  const span = expectRecord(value, field);
  const allowed = [
    "sourceDocumentId",
    "extractionId",
    "extractionRevision",
    "offsetUnit",
    "startOffset",
    "endOffset",
    "textSha256"
  ];
  if (field === "sourceSpan") {
    rejectUnknownFields(span, allowed, field);
  }
  parseIdentifier(span.sourceDocumentId, `${field} sourceDocumentId`);
  parseIdentifier(span.extractionId, `${field} extractionId`);
  parsePositiveInteger(span.extractionRevision, `${field} extractionRevision`);
  if (span.offsetUnit !== "unicode-code-point") {
    throw new ValidationError(`${field} offset unit is invalid.`);
  }
  const start = parseBoundedInteger(
    span.startOffset,
    `${field} startOffset`,
    0,
    Number.MAX_SAFE_INTEGER
  );
  parseBoundedInteger(
    span.endOffset,
    `${field} endOffset`,
    start + 1,
    Number.MAX_SAFE_INTEGER
  );
  parseSha256(span.textSha256, `${field} textSha256`);
}

function validateSourceSpanSelection(
  value: unknown,
  field: string
): void {
  const span = expectRecord(value, field);
  rejectUnknownFields(
    span,
    [
      "sourceDocumentId",
      "extractionId",
      "extractionRevision",
      "offsetUnit",
      "startOffset",
      "endOffset"
    ],
    field
  );
  parseIdentifier(span.sourceDocumentId, `${field} sourceDocumentId`);
  parseIdentifier(span.extractionId, `${field} extractionId`);
  parsePositiveInteger(
    span.extractionRevision,
    `${field} extractionRevision`
  );
  if (span.offsetUnit !== "unicode-code-point") {
    throw new ValidationError(`${field} offset unit is invalid.`);
  }
  const start = parseBoundedInteger(
    span.startOffset,
    `${field} startOffset`,
    0,
    Number.MAX_SAFE_INTEGER
  );
  parseBoundedInteger(
    span.endOffset,
    `${field} endOffset`,
    start + 1,
    Number.MAX_SAFE_INTEGER
  );
}

function validateStrictSourceSpan(value: unknown, field: string): void {
  const span = expectRecord(value, field);
  rejectUnknownFields(
    span,
    [
      "sourceDocumentId",
      "extractionId",
      "extractionRevision",
      "offsetUnit",
      "startOffset",
      "endOffset",
      "textSha256"
    ],
    field
  );
  validateSourceSpan(span, field);
}

function validateExactText(value: unknown, field: string): void {
  const exact = expectRecord(value, field);
  rejectUnknownFields(
    exact,
    [
      "sourceDocumentId",
      "extractionId",
      "extractionRevision",
      "offsetUnit",
      "startOffset",
      "endOffset",
      "textSha256",
      "exactText",
      "exactTextSha256",
      "originalCodePointCount",
      "exactTextTruncated",
      "originalTextPreserved"
    ],
    field
  );
  validateSourceSpan(exact, field);
  parseBoundedText(
    exact.exactText,
    `${field} exactText`,
    0,
    STORY_ANALYSIS_LIMITS.maximumExactTextCodePoints,
    false
  );
  parseSha256(exact.exactTextSha256, `${field} exactTextSha256`);
  parseBoundedInteger(
    exact.originalCodePointCount,
    `${field} originalCodePointCount`,
    0,
    Number.MAX_SAFE_INTEGER
  );
  parseBoolean(exact.exactTextTruncated, `${field} exactTextTruncated`);
  if (exact.originalTextPreserved !== true) {
    throw new ValidationError(`${field} must preserve original text.`);
  }
}

function validateProvenance(value: unknown): void {
  const provenance = expectRecord(value, "analysis provenance");
  rejectUnknownFields(
    provenance,
    [
      "origin",
      "recordedAt",
      "inputFingerprint",
      "agentExecutionId",
      "agentId",
      "agentVersion",
      "correctionId",
      "producerId",
      "producerVersion",
      "deterministic"
    ],
    "analysis provenance"
  );
  if (
    provenance.origin !== "runtime_agent" &&
    provenance.origin !== "human_correction" &&
    provenance.origin !== "human_review" &&
    provenance.origin !== "analysis_synthesis" &&
    provenance.origin !== "migration"
  ) {
    throw new ValidationError("The analysis provenance origin is invalid.");
  }
  parseIsoDate(provenance.recordedAt, "provenance recordedAt");
  parseSha256(provenance.inputFingerprint, "provenance inputFingerprint");
  for (const field of [
    "agentExecutionId",
    "correctionId"
  ] as const) {
    if (provenance[field] !== undefined) {
      parseIdentifier(provenance[field], field);
    }
  }
  if (provenance.agentId !== undefined || provenance.agentVersion !== undefined) {
    if (
      !PHASE_2_RUNTIME_AGENTS.some(
        (agent) =>
          agent.agentId === provenance.agentId &&
          agent.version === provenance.agentVersion
      )
    ) {
      throw new ValidationError("The provenance agent identity is invalid.");
    }
  }
  if (
    provenance.producerId !== undefined ||
    provenance.producerVersion !== undefined
  ) {
    if (
      provenance.producerId !== WHOLE_BOOK_ANALYSIS_PRODUCER_ID ||
      provenance.producerVersion !== WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION
    ) {
      throw new ValidationError("The provenance producer identity is invalid.");
    }
  }
  parseBoolean(provenance.deterministic, "provenance deterministic");
}

function validateAgentVersions(value: unknown): void {
  const versions = parseBoundedArray(
    value,
    "agentVersions",
    PHASE_2_RUNTIME_AGENTS.length
  );
  if (versions.length !== PHASE_2_RUNTIME_AGENTS.length) {
    throw new ValidationError("The analysis agent registry is incomplete.");
  }
  for (const [index, canonical] of PHASE_2_RUNTIME_AGENTS.entries()) {
    const version = expectRecord(versions[index], "analysis agent version");
    rejectUnknownFields(
      version,
      ["agentId", "version"],
      "analysis agent version"
    );
    if (
      version.agentId !== canonical.agentId ||
      version.version !== canonical.version
    ) {
      throw new ValidationError("The analysis agent registry is unsupported.");
    }
  }
}

function validatePageEnvelope(
  response: Record<string, unknown>,
  field: string,
  itemField: string,
  additionalFields: readonly string[] = []
): void {
  rejectUnknownFields(
    response,
    [
      "correlationId",
      "pageSize",
      "total",
      "nextCursor",
      itemField,
      ...additionalFields
    ],
    field
  );
  parseIdentifier(response.correlationId, "correlationId");
  parseBoundedInteger(
    response.pageSize,
    "pageSize",
    0,
    STORY_ANALYSIS_LIMITS.maximumPageSize
  );
  parseBoundedInteger(response.total, "total", 0, 10_000_000);
  if (response.nextCursor !== undefined) {
    parseBoundedText(response.nextCursor, "nextCursor", 1, MAX_CURSOR_LENGTH);
  }
}

function validateGateIdArray(
  value: unknown,
  field: string
): readonly AnalysisGateId[] {
  const values = parseBoundedArray(value, field, ANALYSIS_GATE_IDS.length);
  const seen = new Set<AnalysisGateId>();
  const gateIds: AnalysisGateId[] = [];
  for (const item of values) {
    const gateId = parseGateId(item);
    if (seen.has(gateId)) {
      throw new ValidationError(`${field} repeated a gate.`);
    }
    seen.add(gateId);
    gateIds.push(gateId);
  }
  return gateIds;
}

function requireCompleteGateSet(
  values: Iterable<AnalysisGateId>,
  message: string
): void {
  const gates = new Set(values);
  if (
    gates.size !== ANALYSIS_GATE_IDS.length ||
    ANALYSIS_GATE_IDS.some((gateId) => !gates.has(gateId))
  ) {
    throw new ValidationError(message);
  }
}

function parseCorrectionSelection(
  category: AnalysisCorrectionCategory,
  targetCollection: AnalysisCollection,
  value: unknown,
  mode: "request"
): AnalysisCorrectionRequestSelection;
function parseCorrectionSelection(
  category: AnalysisCorrectionCategory,
  targetCollection: AnalysisCollection,
  value: unknown,
  mode: "record"
): AnalysisCorrectionSelection;
function parseCorrectionSelection(
  category: AnalysisCorrectionCategory,
  targetCollection: AnalysisCollection,
  value: unknown,
  mode: "request" | "record"
): AnalysisCorrectionRequestSelection | AnalysisCorrectionSelection {
  if (!correctionCollections[category].includes(targetCollection)) {
    throw new ValidationError(
      "The correction category cannot target this collection."
    );
  }
  const patch = parseCorrectionPatch(
    category,
    targetCollection,
    value,
    mode
  );
  return {
    category,
    targetCollection,
    patch
  } as AnalysisCorrectionRequestSelection | AnalysisCorrectionSelection;
}

function parseCorrectionPatch(
  category: AnalysisCorrectionCategory,
  targetCollection: AnalysisCollection,
  value: unknown,
  mode: "request" | "record"
): AnalysisCorrectionRequestPatch | AnalysisCorrectionPatch {
  const patch = expectRecord(value, `${category} correction patch`);
  switch (category) {
    case "structure_boundary": {
      const fields =
        targetCollection === "scenes"
          ? [
              "operation",
              "parentEntityId",
              "ordinal",
              "sourceSpan",
              "boundaryKind"
            ]
          : ["operation", "parentEntityId", "ordinal", "sourceSpan"];
      rejectUnknownFields(patch, fields, "structure boundary patch");
      requireOneOf(
        patch.operation,
        ["add", "remove", "move"],
        "structure operation"
      );
      parseIdentifier(patch.parentEntityId, "parentEntityId");
      parseBoundedInteger(
        patch.ordinal,
        "structure ordinal",
        0,
        Number.MAX_SAFE_INTEGER
      );
      if (mode === "request") {
        validateSourceSpanSelection(patch.sourceSpan, "sourceSpan");
      } else {
        validateSourceSpan(patch.sourceSpan, "sourceSpan");
      }
      if (
        targetCollection === "scenes" &&
        !isOneOf(patch.boundaryKind, [
          "chapter_start",
          "explicit_scene_break",
          "heading",
          "inferred"
        ])
      ) {
        throw new ValidationError("Scene boundary kind is invalid.");
      }
      break;
    }
    case "structure_label": {
      const field = targetCollection === "chapters" ? "title" : "heading";
      rejectUnknownFields(patch, [field], "structure label patch");
      parseNullableText(patch[field], field, 512);
      break;
    }
    case "character_identity":
      rejectUnknownFields(
        patch,
        ["canonicalName", "normalizedCanonicalName", "identityStatus"],
        "character identity patch"
      );
      parseBoundedText(patch.canonicalName, "canonicalName", 1, 512);
      parseBoundedText(
        patch.normalizedCanonicalName,
        "normalizedCanonicalName",
        1,
        512
      );
      requireOneOf(
        patch.identityStatus,
        ["resolved", "ambiguous", "unresolved", "unknown"],
        "identityStatus"
      );
      break;
    case "character_alias":
      parseCharacterAliasPatch(patch);
      break;
    case "character_merge":
      rejectUnknownFields(
        patch,
        ["mergeIntoCharacterId"],
        "character merge patch"
      );
      parseIdentifier(patch.mergeIntoCharacterId, "mergeIntoCharacterId");
      break;
    case "character_split":
      rejectUnknownFields(
        patch,
        [
          "newRegistryCharacterId",
          "canonicalName",
          "normalizedCanonicalName",
          "mentionIds"
        ],
        "character split patch"
      );
      parseIdentifier(
        patch.newRegistryCharacterId,
        "newRegistryCharacterId"
      );
      parseBoundedText(patch.canonicalName, "canonicalName", 1, 512);
      parseBoundedText(
        patch.normalizedCanonicalName,
        "normalizedCanonicalName",
        1,
        512
      );
      parseIdentifierArray(
        patch.mentionIds,
        "mentionIds",
        MAX_CORRECTION_IDENTIFIERS,
        1
      );
      break;
    case "mention_resolution":
      rejectUnknownFields(
        patch,
        ["resolution", "effectiveCharacterId", "candidateCharacterIds"],
        "mention resolution patch"
      );
      requireOneOf(
        patch.resolution,
        ["resolved", "ambiguous", "unresolved"],
        "resolution"
      );
      parseNullableIdentifier(
        patch.effectiveCharacterId,
        "effectiveCharacterId"
      );
      parseIdentifierArray(
        patch.candidateCharacterIds,
        "candidateCharacterIds",
        STORY_ANALYSIS_LIMITS.maximumAttributionCandidatesPerLine
      );
      break;
    case "dialogue_speaker":
      rejectUnknownFields(
        patch,
        [
          "speakerCharacterId",
          "selectedCandidateId",
          "requiresHumanReview"
        ],
        "dialogue speaker patch"
      );
      parseNullableIdentifier(
        patch.speakerCharacterId,
        "speakerCharacterId"
      );
      parseNullableIdentifier(
        patch.selectedCandidateId,
        "selectedCandidateId"
      );
      parseBoolean(patch.requiresHumanReview, "requiresHumanReview");
      break;
    case "point_of_view":
      rejectUnknownFields(
        patch,
        ["mode", "viewpointCharacterId", "narratorCharacterId"],
        "point of view patch"
      );
      requireOneOf(
        patch.mode,
        [
          "first_person",
          "second_person",
          "third_person_limited",
          "third_person_omniscient",
          "mixed",
          "experimental",
          "unknown"
        ],
        "mode"
      );
      parseNullableIdentifier(
        patch.viewpointCharacterId,
        "viewpointCharacterId"
      );
      parseNullableIdentifier(
        patch.narratorCharacterId,
        "narratorCharacterId"
      );
      break;
    case "location_identity":
      rejectUnknownFields(
        patch,
        [
          "canonicalName",
          "normalizedCanonicalName",
          "kind",
          "parentLocationId"
        ],
        "location identity patch"
      );
      parseBoundedText(patch.canonicalName, "canonicalName", 1, 512);
      parseBoundedText(
        patch.normalizedCanonicalName,
        "normalizedCanonicalName",
        1,
        512
      );
      requireOneOf(
        patch.kind,
        [
          "interior",
          "exterior",
          "vehicle",
          "region",
          "abstract",
          "unknown"
        ],
        "location kind"
      );
      parseNullableIdentifier(patch.parentLocationId, "parentLocationId");
      break;
    case "location_alias":
      rejectUnknownFields(
        patch,
        ["operation", "alias"],
        "location alias patch"
      );
      requireOneOf(patch.operation, ["add", "remove"], "alias operation");
      parseBoundedText(patch.alias, "alias", 1, 512);
      break;
    case "temporal_order":
      rejectUnknownFields(
        patch,
        ["relation", "approximate", "status"],
        "temporal order patch"
      );
      requireOneOf(
        patch.relation,
        [
          "before",
          "after",
          "same_time",
          "overlaps",
          "during",
          "contains",
          "unknown"
        ],
        "temporal relation"
      );
      parseBoolean(patch.approximate, "approximate");
      requireOneOf(
        patch.status,
        ["consistent", "conflicting", "unresolved"],
        "temporal status"
      );
      break;
    case "relationship":
      parseRelationshipPatch(patch);
      break;
    case "emotional_state":
      parseEmotionalStatePatch(patch);
      break;
    case "dramatic_intent":
      parseDramaticIntentPatch(patch);
      break;
    case "continuity_disposition":
      rejectUnknownFields(
        patch,
        ["disposition", "explanation"],
        "continuity disposition patch"
      );
      requireOneOf(
        patch.disposition,
        [
          "confirmed_issue",
          "intentional",
          "false_positive",
          "deferred",
          "corrected",
          "unresolved"
        ],
        "continuity disposition"
      );
      parseBoundedText(
        patch.explanation,
        "continuity explanation",
        1,
        MAX_CORRECTION_TEXT_CODE_POINTS
      );
      break;
  }
  return patch as unknown as
    | AnalysisCorrectionRequestPatch
    | AnalysisCorrectionPatch;
}

function parseCharacterAliasPatch(
  patch: Record<string, unknown>
): void {
  const operation = requireOneOf(
    patch.operation,
    ["add", "replace", "remove"],
    "character alias operation"
  );
  if (operation === "remove") {
    rejectUnknownFields(
      patch,
      ["operation", "aliasId"],
      "character alias removal patch"
    );
    parseIdentifier(patch.aliasId, "aliasId");
    return;
  }
  rejectUnknownFields(
    patch,
    ["operation", "alias"],
    "character alias patch"
  );
  validateCharacterAliasValue(patch.alias);
}

function validateCharacterAliasValue(value: unknown): void {
  const alias = expectRecord(value, "character alias");
  rejectUnknownFields(
    alias,
    [
      "aliasId",
      "characterId",
      "alias",
      "normalizedAlias",
      "kind",
      "ambiguous",
      "effectiveRange",
      "change",
      "previousAliasId",
      "confidence",
      "evidence"
    ],
    "character alias"
  );
  parseIdentifier(alias.aliasId, "aliasId");
  parseIdentifier(alias.characterId, "alias characterId");
  parseBoundedText(alias.alias, "alias", 1, 512);
  parseBoundedText(alias.normalizedAlias, "normalizedAlias", 1, 512);
  requireOneOf(
    alias.kind,
    [
      "full_name",
      "given_name",
      "family_name",
      "nickname",
      "honorific",
      "title",
      "description",
      "other"
    ],
    "alias kind"
  );
  parseBoolean(alias.ambiguous, "alias ambiguous");
  const effectiveRange = expectRecord(
    alias.effectiveRange,
    "alias effectiveRange"
  );
  rejectUnknownFields(
    effectiveRange,
    ["sourceRange", "validFromEventId", "validThroughEventId"],
    "alias effectiveRange"
  );
  validateStrictSourceSpan(
    effectiveRange.sourceRange,
    "alias sourceRange"
  );
  parseNullableIdentifier(
    effectiveRange.validFromEventId,
    "validFromEventId"
  );
  parseNullableIdentifier(
    effectiveRange.validThroughEventId,
    "validThroughEventId"
  );
  requireOneOf(
    alias.change,
    ["introduced", "continued", "retired", "uncertain"],
    "alias change"
  );
  if (alias.previousAliasId !== undefined) {
    parseIdentifier(alias.previousAliasId, "previousAliasId");
  }
  validateConfidence(alias.confidence);
  validateEvidenceList(alias.evidence, "alias evidence");
}

function parseRelationshipPatch(
  patch: Record<string, unknown>
): void {
  rejectUnknownFields(
    patch,
    [
      "sourceCharacterId",
      "targetCharacterId",
      "kind",
      "state",
      "change",
      "scope",
      "validFromEventId",
      "validThroughEventId"
    ],
    "relationship patch"
  );
  parseIdentifier(patch.sourceCharacterId, "sourceCharacterId");
  parseIdentifier(patch.targetCharacterId, "targetCharacterId");
  validateRelationshipValue(patch);
}

function validateRelationshipValue(
  relationship: Record<string, unknown>
): void {
  requireOneOf(
    relationship.kind,
    [
      "family",
      "friendship",
      "romantic",
      "professional",
      "adversarial",
      "authority",
      "dependency",
      "alliance",
      "unknown",
      "custom"
    ],
    "relationship kind"
  );
  parseBoundedText(
    relationship.state,
    "relationship state",
    1,
    MAX_CORRECTION_TEXT_CODE_POINTS
  );
  requireOneOf(
    relationship.change,
    [
      "established",
      "strengthened",
      "weakened",
      "reversed",
      "unchanged",
      "uncertain"
    ],
    "relationship change"
  );
  const scope = expectRecord(
    relationship.scope,
    "relationship scope"
  );
  rejectUnknownFields(
    scope,
    ["kind", "firstSceneId", "lastSceneId", "sourceRange"],
    "relationship scope"
  );
  requireOneOf(
    scope.kind,
    ["scene", "chapter", "scene_range"],
    "relationship scope kind"
  );
  parseIdentifier(scope.firstSceneId, "scope firstSceneId");
  parseIdentifier(scope.lastSceneId, "scope lastSceneId");
  validateStrictSourceSpan(scope.sourceRange, "scope sourceRange");
  parseNullableIdentifier(
    relationship.validFromEventId,
    "validFromEventId"
  );
  parseNullableIdentifier(
    relationship.validThroughEventId,
    "validThroughEventId"
  );
}

function parseEmotionalStatePatch(
  patch: Record<string, unknown>
): void {
  rejectUnknownFields(
    patch,
    [
      "emotion",
      "customEmotion",
      "note",
      "valence",
      "arousal",
      "intensity",
      "progression"
    ],
    "emotional state patch"
  );
  requireOneOf(
    patch.emotion,
    [
      "fear",
      "anger",
      "sadness",
      "joy",
      "surprise",
      "calm",
      "disgust",
      "anticipation",
      "trust",
      "confusion",
      "hope",
      "guilt",
      "shame",
      "grief",
      "relief",
      "neutral",
      "mixed",
      "unknown",
      "custom"
    ],
    "emotion"
  );
  parseNullableText(
    patch.customEmotion,
    "customEmotion",
    160
  );
  parseBoundedText(
    patch.note,
    "emotional note",
    0,
    1_000
  );
  parseSignedUnitInterval(patch.valence, "valence");
  parseUnitInterval(patch.arousal, "arousal");
  parseUnitInterval(patch.intensity, "intensity");
  requireOneOf(
    patch.progression,
    ["initial", "rising", "falling", "shifted", "stable", "uncertain"],
    "emotional progression"
  );
}

function parseDramaticIntentPatch(
  patch: Record<string, unknown>
): void {
  rejectUnknownFields(
    patch,
    [
      "intent",
      "customIntent",
      "dramaticFunction",
      "customDramaticFunction",
      "note",
      "targetCharacterId",
      "status"
    ],
    "dramatic intent patch"
  );
  requireOneOf(
    patch.intent,
    [
      "question",
      "direct",
      "persuade",
      "reassure",
      "reveal",
      "conceal",
      "deflect",
      "threaten",
      "comfort",
      "seek_information",
      "command",
      "negotiate",
      "connect",
      "withdraw",
      "deceive",
      "unknown",
      "custom"
    ],
    "dramatic intent"
  );
  parseNullableText(
    patch.customIntent,
    "customIntent",
    160
  );
  requireOneOf(
    patch.dramaticFunction,
    [
      "setup",
      "inciting_action",
      "complication",
      "reversal",
      "revelation",
      "crisis",
      "climax",
      "resolution",
      "transition",
      "character_development",
      "relationship_change",
      "tension",
      "comic_relief",
      "exposition",
      "foreshadowing",
      "unknown",
      "custom"
    ],
    "dramatic function"
  );
  parseNullableText(
    patch.customDramaticFunction,
    "customDramaticFunction",
    160
  );
  parseBoundedText(
    patch.note,
    "dramatic intent note",
    0,
    1_000
  );
  parseNullableIdentifier(patch.targetCharacterId, "targetCharacterId");
  requireOneOf(
    patch.status,
    [
      "pursued",
      "achieved",
      "blocked",
      "abandoned",
      "concealed",
      "uncertain"
    ],
    "dramatic intent status"
  );
}

function validateSubjectDiscriminant(
  entity: Record<string, unknown>,
  field: "emotional state" | "dramatic intent"
): void {
  const subjectType = entity.subjectType;
  if (field === "emotional state") {
    if (subjectType === "scene") {
      if (entity.characterId !== undefined) {
        throw new ValidationError(
          "A scene emotional state cannot carry a character identity."
        );
      }
      return;
    }
    if (subjectType === "character") {
      parseIdentifier(entity.characterId, "emotional characterId");
      return;
    }
    throw new ValidationError("The emotional subject type is invalid.");
  }
  const subjectFields = [
    "characterId",
    "dialogueLineId",
    "beatId"
  ] as const;
  const expectedField =
    subjectType === "character"
      ? "characterId"
      : subjectType === "dialogue"
        ? "dialogueLineId"
        : subjectType === "beat"
          ? "beatId"
          : subjectType === "scene"
            ? null
            : undefined;
  if (expectedField === undefined) {
    throw new ValidationError("The dramatic intent subject type is invalid.");
  }
  for (const candidate of subjectFields) {
    if (candidate === expectedField) {
      parseIdentifier(entity[candidate], `dramatic ${candidate}`);
    } else if (entity[candidate] !== undefined) {
      throw new ValidationError(
        "The dramatic intent carried a cross-scope identity."
      );
    }
  }
}

function jsonValuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {
    return true;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    if (
      !Array.isArray(left) ||
      !Array.isArray(right) ||
      left.length !== right.length
    ) {
      return false;
    }
    return left.every((item, index) =>
      jsonValuesEqual(item, right[index])
    );
  }
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object"
  ) {
    return false;
  }
  const leftRecord = left as Readonly<Record<string, unknown>>;
  const rightRecord = right as Readonly<Record<string, unknown>>;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        jsonValuesEqual(leftRecord[key], rightRecord[key])
    )
  );
}

function sameIdentifierSet(
  left: readonly string[],
  right: readonly string[]
): boolean {
  return (
    left.length === right.length &&
    left.every((value) => right.includes(value))
  );
}

function validateBoundedJson(
  value: unknown,
  field: string,
  depth: number,
  state: { nodes: number },
  maximumFields: number,
  maximumDepth: number,
  maximumNodes: number
): void {
  state.nodes += 1;
  if (state.nodes > maximumNodes || depth > maximumDepth) {
    throw new ValidationError(`${field} exceeded its structural limit.`);
  }
  if (
    value === null ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return;
  }
  if (typeof value === "string") {
    if (
      [...value].length > STORY_ANALYSIS_LIMITS.maximumExactTextCodePoints ||
      Buffer.byteLength(value, "utf8") >
        STORY_ANALYSIS_LIMITS.maximumExactTextCodePoints * 4
    ) {
      throw new ValidationError(`${field} contained oversized text.`);
    }
    return;
  }
  if (Array.isArray(value)) {
    if (value.length > maximumFields) {
      throw new ValidationError(`${field} contained an oversized array.`);
    }
    for (const item of value) {
      validateBoundedJson(
        item,
        field,
        depth + 1,
        state,
        maximumFields,
        maximumDepth,
        maximumNodes
      );
    }
    return;
  }
  const record = expectRecord(value, field);
  if (Object.keys(record).length > maximumFields) {
    throw new ValidationError(`${field} contained too many fields.`);
  }
  for (const item of Object.values(record)) {
    validateBoundedJson(
      item,
      field,
      depth + 1,
      state,
      maximumFields,
      maximumDepth,
      maximumNodes
    );
  }
}

function parseEnvelope(value: unknown): Record<string, unknown> {
  const envelope = expectRecord(value, "desktop request");
  rejectUnknownFields(
    envelope,
    ["contractVersion", "payload"],
    "desktop request"
  );
  if (envelope.contractVersion !== DESKTOP_CONTRACT_VERSION) {
    throw new ValidationError("Desktop contract version is incompatible.");
  }
  return expectRecord(envelope.payload, "desktop request payload");
}

function parseRunIdentity(value: Record<string, unknown>): AnalysisRunInput {
  return {
    projectId: parseIdentifier(value.projectId, "projectId"),
    runId: parseIdentifier(value.runId, "runId")
  };
}

function parseCursorPage(
  value: Record<string, unknown>
): Pick<ListAnalysisRunsInput, "cursor" | "limit"> {
  const cursor =
    value.cursor === undefined
      ? undefined
      : parseBoundedText(value.cursor, "cursor", 1, MAX_CURSOR_LENGTH);
  const limit =
    value.limit === undefined
      ? undefined
      : parseBoundedInteger(
          value.limit,
          "limit",
          1,
          STORY_ANALYSIS_LIMITS.maximumPageSize
        );
  return {
    ...(cursor === undefined ? {} : { cursor }),
    ...(limit === undefined ? {} : { limit })
  };
}

function parseProfileReference(value: unknown) {
  const profile = expectRecord(value, "analysis profile");
  rejectUnknownFields(
    profile,
    ["profileId", "semanticVersion", "fingerprint"],
    "analysis profile"
  );
  if (
    profile.profileId !== WHOLE_BOOK_ANALYSIS_PROFILE_ID ||
    profile.semanticVersion !== WHOLE_BOOK_ANALYSIS_PROFILE_VERSION ||
    profile.fingerprint !== WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
  ) {
    throw new ValidationError("The analysis profile is unsupported.");
  }
  return {
    profileId: WHOLE_BOOK_ANALYSIS_PROFILE_ID,
    semanticVersion: WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
    fingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
  };
}

function parseCollection(value: unknown): AnalysisCollection {
  if (
    typeof value !== "string" ||
    !(ANALYSIS_ENTITY_COLLECTIONS as readonly string[]).includes(value)
  ) {
    throw new ValidationError("Analysis collection is invalid.");
  }
  return value as AnalysisCollection;
}

function parseGateId(value: unknown): AnalysisGateId {
  if (
    typeof value !== "string" ||
    !(ANALYSIS_GATE_IDS as readonly string[]).includes(value)
  ) {
    throw new ValidationError("Analysis gate is invalid.");
  }
  return value as AnalysisGateId;
}

function parseCorrectionCategory(
  value: unknown
): AnalysisCorrectionCategory {
  if (
    value !== "structure_boundary" &&
    value !== "structure_label" &&
    value !== "character_identity" &&
    value !== "character_alias" &&
    value !== "character_merge" &&
    value !== "character_split" &&
    value !== "mention_resolution" &&
    value !== "dialogue_speaker" &&
    value !== "point_of_view" &&
    value !== "location_identity" &&
    value !== "location_alias" &&
    value !== "temporal_order" &&
    value !== "relationship" &&
    value !== "emotional_state" &&
    value !== "dramatic_intent" &&
    value !== "continuity_disposition"
  ) {
    throw new ValidationError("Analysis correction category is invalid.");
  }
  return value;
}

function parseGateDecisionAction(
  value: unknown
): DecideAnalysisReviewInput["decision"] {
  if (
    value !== "approve" &&
    value !== "reject" &&
    value !== "request_changes"
  ) {
    throw new ValidationError("Analysis gate decision is invalid.");
  }
  return value;
}

function parseSpeakerState(value: unknown): DialogueSpeakerState {
  if (
    value !== "unknown" &&
    value !== "ambiguous" &&
    value !== "proposed" &&
    value !== "corrected"
  ) {
    throw new ValidationError("Dialogue speaker state is invalid.");
  }
  return value;
}

function expectRecord(
  value: unknown,
  field: string
): Record<string, unknown> {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    throw new ValidationError(`${field} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function rejectUnknownFields(
  value: Record<string, unknown>,
  allowedFields: readonly string[],
  field: string
): void {
  const allowed = new Set(allowedFields);
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new ValidationError(`${field} contained an unknown field.`);
  }
}

function parseBoundedArray(
  value: unknown,
  field: string,
  maximumLength: number
): readonly unknown[] {
  if (!Array.isArray(value) || value.length > maximumLength) {
    throw new ValidationError(`${field} must be a bounded collection.`);
  }
  return value;
}

function parseIdentifierArray(
  value: unknown,
  field: string,
  maximumLength: number,
  minimumLength = 0
): readonly string[] {
  const values = parseBoundedArray(value, field, maximumLength);
  if (values.length < minimumLength) {
    throw new ValidationError(
      `${field} must contain at least ${minimumLength} identifier.`
    );
  }
  const output = values.map((item) => parseIdentifier(item, field));
  if (new Set(output).size !== output.length) {
    throw new ValidationError(`${field} contained duplicate identities.`);
  }
  return output;
}

function parseIdentifier(value: unknown, field: string): string {
  if (typeof value !== "string" || !IDENTIFIER_PATTERN.test(value)) {
    throw new ValidationError(`${field} is not a valid opaque identifier.`);
  }
  return value;
}

function parseNullableIdentifier(
  value: unknown,
  field: string
): string | null {
  return value === null ? null : parseIdentifier(value, field);
}

function parseNullableText(
  value: unknown,
  field: string,
  maximumLength: number
): string | null {
  return value === null
    ? null
    : parseBoundedText(value, field, 1, maximumLength);
}

function isOneOf(
  value: unknown,
  choices: readonly string[]
): value is string {
  return (
    typeof value === "string" &&
    choices.includes(value)
  );
}

function requireOneOf(
  value: unknown,
  choices: readonly string[],
  field: string
): string {
  if (!isOneOf(value, choices)) {
    throw new ValidationError(`${field} is invalid.`);
  }
  return value;
}

function parseSha256(value: unknown, field: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    throw new ValidationError(`${field} must be a lowercase SHA-256 digest.`);
  }
  return value;
}

function parseBoundedText(
  value: unknown,
  field: string,
  minimumLength: number,
  maximumLength: number,
  trim = true
): string {
  if (typeof value !== "string") {
    throw new ValidationError(`${field} must be text.`);
  }
  const checked = trim ? value.trim() : value;
  if (
    [...checked].length < minimumLength ||
    [...checked].length > maximumLength ||
    Buffer.byteLength(checked, "utf8") > maximumLength * 4
  ) {
    throw new ValidationError(`${field} exceeded its text limit.`);
  }
  return checked;
}

function parseBoundedInteger(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < minimum ||
    (value as number) > maximum
  ) {
    throw new ValidationError(`${field} is outside its integer range.`);
  }
  return value as number;
}

function parsePositiveInteger(value: unknown, field: string): number {
  return parseBoundedInteger(value, field, 1, Number.MAX_SAFE_INTEGER);
}

function parseUnitInterval(value: unknown, field: string): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 1
  ) {
    throw new ValidationError(`${field} must be between zero and one.`);
  }
  return value;
}

function parseSignedUnitInterval(
  value: unknown,
  field: string
): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < -1 ||
    value > 1
  ) {
    throw new ValidationError(`${field} must be between negative one and one.`);
  }
  return value;
}

function parseBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new ValidationError(`${field} must be a boolean.`);
  }
  return value;
}

function parseIsoDate(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.length > 40 ||
    !Number.isFinite(Date.parse(value))
  ) {
    throw new ValidationError(`${field} must be an ISO date-time.`);
  }
  return value;
}

function requireContractVersion(value: unknown): void {
  if (value !== ANALYSIS_CONTRACT_VERSION) {
    throw new ValidationError("Analysis contract version is incompatible.");
  }
}

function analysisHeaderFields(): readonly string[] {
  return [
    "contractVersion",
    "entityId",
    "stableSemanticId",
    "runId",
    "snapshotId",
    "revision",
    "effectiveRevision",
    "machineEntityFingerprint",
    "effectiveValueFingerprint",
    "effectiveAuthority",
    "ordinal",
    "confidence",
    "warnings",
    "provenance",
    "evidence"
  ];
}
