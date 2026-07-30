import type {
  EntityId,
  IsoDateTime,
  Sha256
} from "./domain.js";

export const ANALYSIS_CONTRACT_VERSION = "2.0.0" as const;
export const WHOLE_BOOK_ANALYSIS_PROFILE_ID =
  "whole-book-intelligence-v1" as const;
export const WHOLE_BOOK_ANALYSIS_PROFILE_VERSION = "1.0.0" as const;
export const WHOLE_BOOK_ANALYSIS_PRODUCER_ID =
  "whole-book-analysis-orchestrator" as const;
export const WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION = "1.0.0" as const;
export const WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT =
  "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec" as const;

export const STORY_ANALYSIS_LIMITS = Object.freeze({
  defaultPageSize: 50,
  maximumAgentEnvelopeBytes: 32_768,
  maximumAnalysisEntities: 250_000,
  maximumAnalysisWords: 150_000,
  maximumAttributionCandidatesPerLine: 8,
  maximumCheckpointBytes: 67_108_864,
  maximumEvidenceExcerptCodePoints: 512,
  maximumEvidenceSpansPerClaim: 16,
  maximumExactTextCodePoints: 16_384,
  maximumPageSize: 200,
  maximumSnapshotStages: 5,
  maximumWarningsPerEntity: 32
});

export const ANALYSIS_CONFIDENCE_CLASSIFICATION = Object.freeze({
  unknown: Object.freeze({ score: 0 }),
  low: Object.freeze({
    minimumExclusive: 0,
    maximumExclusive: 0.75
  }),
  medium: Object.freeze({
    minimumInclusive: 0.75,
    maximumExclusive: 0.85
  }),
  high: Object.freeze({
    minimumInclusive: 0.85,
    maximumInclusive: 1
  })
});

export const PHASE_2_RUNTIME_AGENTS = [
  { agentId: "story-structure", version: "1.0.0" },
  { agentId: "story-beats", version: "1.0.0" },
  { agentId: "character-identity", version: "1.0.0" },
  { agentId: "dialogue-attribution", version: "1.0.0" },
  { agentId: "point-of-view", version: "1.0.0" },
  { agentId: "story-setting", version: "1.0.0" },
  { agentId: "story-timeline", version: "1.0.0" },
  { agentId: "character-relationships", version: "1.0.0" },
  { agentId: "emotion-dramatic-intent", version: "1.0.0" },
  { agentId: "story-continuity", version: "1.0.0" },
  { agentId: "analysis-synthesis", version: "1.0.0" }
] as const;

export const ANALYSIS_JOB_STAGES = [
  "validate_approved_input",
  "initialize_run",
  "analyze_structure",
  "analyze_beats",
  "analyze_character_identity",
  "analyze_dialogue_attribution",
  "analyze_point_of_view",
  "analyze_locations",
  "analyze_timeline",
  "analyze_relationships",
  "analyze_emotion_intent",
  "analyze_continuity",
  "synthesize_analysis",
  "publish_analysis"
] as const;

/**
 * Canonical UTF-8 JSON for the profile values, without a trailing newline.
 * Object keys are lexicographically ordered. Agent array order is pipeline
 * order and therefore contributes to the profile fingerprint.
 */
export const WHOLE_BOOK_ANALYSIS_PROFILE_CANONICAL_JSON =
  '{"agentVersions":[{"agentId":"story-structure","version":"1.0.0"},{"agentId":"story-beats","version":"1.0.0"},{"agentId":"character-identity","version":"1.0.0"},{"agentId":"dialogue-attribution","version":"1.0.0"},{"agentId":"point-of-view","version":"1.0.0"},{"agentId":"story-setting","version":"1.0.0"},{"agentId":"story-timeline","version":"1.0.0"},{"agentId":"character-relationships","version":"1.0.0"},{"agentId":"emotion-dramatic-intent","version":"1.0.0"},{"agentId":"story-continuity","version":"1.0.0"},{"agentId":"analysis-synthesis","version":"1.0.0"}],"analysisContractVersion":"2.0.0","confidenceClassification":{"high":{"maximumInclusive":1,"minimumInclusive":0.85},"low":{"maximumExclusive":0.75,"minimumExclusive":0},"medium":{"maximumExclusive":0.85,"minimumInclusive":0.75},"unknown":{"score":0}},"deterministic":true,"limits":{"defaultPageSize":50,"maximumAgentEnvelopeBytes":32768,"maximumAnalysisEntities":250000,"maximumAnalysisWords":150000,"maximumAttributionCandidatesPerLine":8,"maximumCheckpointBytes":67108864,"maximumEvidenceExcerptCodePoints":512,"maximumEvidenceSpansPerClaim":16,"maximumExactTextCodePoints":16384,"maximumPageSize":200,"maximumSnapshotStages":5,"maximumWarningsPerEntity":32},"offsetUnit":"unicode-code-point","producer":{"producerId":"whole-book-analysis-orchestrator","producerVersion":"1.0.0"},"profileId":"whole-book-intelligence-v1","semanticVersion":"1.0.0"}' as const;

export type AnalysisContractVersion = typeof ANALYSIS_CONTRACT_VERSION;
export type WholeBookAnalysisProfileId =
  typeof WHOLE_BOOK_ANALYSIS_PROFILE_ID;
export type Phase2RuntimeAgent =
  (typeof PHASE_2_RUNTIME_AGENTS)[number];
export type Phase2RuntimeAgentId = Phase2RuntimeAgent["agentId"];
export type Phase2RuntimeAgentVersion = Phase2RuntimeAgent["version"];
export type AnalysisJobStage = (typeof ANALYSIS_JOB_STAGES)[number];

export interface AnalysisProfileValues {
  readonly profileId: WholeBookAnalysisProfileId;
  readonly semanticVersion: typeof WHOLE_BOOK_ANALYSIS_PROFILE_VERSION;
  readonly analysisContractVersion: AnalysisContractVersion;
  readonly deterministic: true;
  readonly offsetUnit: "unicode-code-point";
  readonly producer: {
    readonly producerId: typeof WHOLE_BOOK_ANALYSIS_PRODUCER_ID;
    readonly producerVersion: typeof WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION;
  };
  readonly limits: typeof STORY_ANALYSIS_LIMITS;
  readonly confidenceClassification:
    typeof ANALYSIS_CONFIDENCE_CLASSIFICATION;
  readonly agentVersions: typeof PHASE_2_RUNTIME_AGENTS;
}

export interface AnalysisProfile {
  readonly values: AnalysisProfileValues;
  readonly canonicalJson: typeof WHOLE_BOOK_ANALYSIS_PROFILE_CANONICAL_JSON;
  readonly fingerprint: typeof WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT;
}

export interface AnalysisProfileReference {
  readonly profileId: WholeBookAnalysisProfileId;
  readonly semanticVersion: typeof WHOLE_BOOK_ANALYSIS_PROFILE_VERSION;
  readonly fingerprint: typeof WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT;
}

export type ConfidenceClassification =
  | "unknown"
  | "low"
  | "medium"
  | "high";

interface AnalysisConfidenceBase {
  readonly basis: string;
  readonly calibrationId?: string;
}

export type AnalysisConfidence =
  | (AnalysisConfidenceBase & {
      readonly score: 0;
      readonly classification: "unknown";
    })
  | (AnalysisConfidenceBase & {
      /**
       * Runtime validation requires 0 < score < 0.75.
       */
      readonly score: number;
      readonly classification: "low";
    })
  | (AnalysisConfidenceBase & {
      /**
       * Runtime validation requires 0.75 <= score < 0.85.
       */
      readonly score: number;
      readonly classification: "medium";
    })
  | (AnalysisConfidenceBase & {
      /**
       * Runtime validation requires 0.85 <= score <= 1.
       */
      readonly score: number;
      readonly classification: "high";
    });

export interface AnalysisSourceSpan {
  readonly sourceDocumentId: EntityId;
  readonly extractionId: EntityId;
  readonly extractionRevision: number;
  readonly offsetUnit: "unicode-code-point";
  readonly startOffset: number;
  readonly endOffset: number;
  readonly textSha256: Sha256;
}

/**
 * A human-selected canonical range. The service binds it to the frozen
 * approved extraction and derives `textSha256` inside the append transaction.
 */
export type AnalysisSourceSpanSelection = Omit<
  AnalysisSourceSpan,
  "textSha256"
>;

/**
 * A bounded, exact slice of approved extracted text. This is private story
 * content and must not enter logs, diagnostics, or generic list summaries.
 */
export interface ExactAnalysisText extends AnalysisSourceSpan {
  readonly exactText: string;
  readonly exactTextSha256: Sha256;
  readonly originalCodePointCount: number;
  readonly exactTextTruncated: boolean;
  /**
   * The complete canonical source remains immutable at `AnalysisSourceSpan`;
   * this flag does not claim the bounded `exactText` field is complete.
   */
  readonly originalTextPreserved: true;
}

/**
 * Evidence includes an exact bounded excerpt, not a generated paraphrase.
 * `excerptSha256` hashes `excerptText`; `textSha256` hashes the complete
 * half-open source span, which may be longer than the excerpt.
 */
export interface AnalysisEvidenceSpan extends AnalysisSourceSpan {
  readonly excerptStartOffset: number;
  readonly excerptEndOffset: number;
  readonly excerptText: string;
  readonly excerptSha256: Sha256;
  readonly excerptTruncated: boolean;
}

export interface AnalysisProvenance {
  readonly origin:
    | "runtime_agent"
    | "human_correction"
    | "human_review"
    | "analysis_synthesis"
    | "migration";
  readonly recordedAt: IsoDateTime;
  readonly inputFingerprint: Sha256;
  readonly agentExecutionId?: EntityId;
  readonly agentId?: Phase2RuntimeAgentId;
  readonly agentVersion?: Phase2RuntimeAgentVersion;
  readonly correctionId?: EntityId;
  readonly producerId?: typeof WHOLE_BOOK_ANALYSIS_PRODUCER_ID;
  readonly producerVersion?: typeof WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION;
  readonly deterministic: boolean;
}

export interface AnalysisWarning {
  readonly code: string;
  readonly severity: "info" | "warning" | "error" | "blocker";
  /**
   * Operator-safe wording. Exact source text belongs only in `evidence`.
   */
  readonly message: string;
  readonly requiresHumanReview: boolean;
  readonly relatedEntityIds: readonly EntityId[];
  readonly evidence: readonly AnalysisEvidenceSpan[];
}

export interface AnalysisEntityHeader {
  readonly contractVersion: AnalysisContractVersion;
  readonly entityId: EntityId;
  readonly stableSemanticId: string;
  readonly runId: EntityId;
  readonly snapshotId: EntityId;
  readonly revision: number;
  readonly effectiveRevision: number;
  readonly machineEntityFingerprint: Sha256;
  readonly effectiveValueFingerprint: Sha256;
  readonly effectiveAuthority: "runtime_agent" | "human";
  readonly ordinal: number;
  readonly confidence: AnalysisConfidence;
  readonly warnings: readonly AnalysisWarning[];
  readonly provenance: AnalysisProvenance;
  readonly evidence: readonly AnalysisEvidenceSpan[];
}

export const ANALYSIS_ENTITY_COLLECTIONS = [
  "agent-executions",
  "chapters",
  "scenes",
  "beats",
  "characters",
  "mentions",
  "dialogue-lines",
  "narration-spans",
  "pov-segments",
  "locations",
  "timeline-events",
  "temporal-constraints",
  "relationships",
  "emotional-states",
  "dramatic-intents",
  "continuity-findings"
] as const;

export type AnalysisEntityCollection =
  (typeof ANALYSIS_ENTITY_COLLECTIONS)[number];

export type AnalysisRunState =
  | "queued"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface AnalysisCollectionSummary {
  readonly collection: AnalysisEntityCollection;
  readonly itemCount: number;
  readonly fingerprint: Sha256;
}

export interface StoryAnalysisCountSummary {
  readonly agentExecutions: number;
  readonly chapters: number;
  readonly scenes: number;
  readonly beats: number;
  readonly characters: number;
  readonly mentions: number;
  readonly dialogueLines: number;
  readonly narrationSpans: number;
  readonly povSegments: number;
  readonly locations: number;
  readonly timelineEvents: number;
  readonly temporalConstraints: number;
  readonly relationships: number;
  readonly emotionalStates: number;
  readonly dramaticIntents: number;
  readonly continuityFindings: number;
  readonly corrections: number;
}

export interface AnalysisSnapshot {
  readonly contractVersion: AnalysisContractVersion;
  readonly snapshotId: EntityId;
  readonly runId: EntityId;
  readonly revision: number;
  readonly inputFingerprint: Sha256;
  readonly snapshotFingerprint: Sha256;
  readonly correctionSetFingerprint: Sha256;
  readonly counts: StoryAnalysisCountSummary;
  readonly collections: readonly AnalysisCollectionSummary[];
  readonly createdAt: IsoDateTime;
  readonly immutable: true;
}

export type AnalysisReviewEligibility =
  | "not_ready"
  | "ready"
  | "blocked_by_warnings"
  | "invalidated";

export interface StoryAnalysisRun {
  readonly contractVersion: AnalysisContractVersion;
  readonly runId: EntityId;
  readonly projectId: EntityId;
  readonly storyId: EntityId;
  readonly storyRevision: number;
  readonly storyFingerprint: Sha256;
  readonly sourceDocumentId: EntityId;
  readonly sourceRevision: number;
  readonly sourceSha256: Sha256;
  readonly extractionId: EntityId;
  readonly extractionRevision: number;
  readonly extractedTextSha256: Sha256;
  readonly importReviewId: EntityId;
  readonly importReviewRevision: number;
  readonly importReviewDecisionId: EntityId;
  readonly approvedEvidenceFingerprint: Sha256;
  readonly inputFingerprint: Sha256;
  readonly runFingerprint: Sha256;
  readonly profile: AnalysisProfileReference;
  readonly producer: {
    readonly producerId: typeof WHOLE_BOOK_ANALYSIS_PRODUCER_ID;
    readonly producerVersion: typeof WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION;
  };
  readonly agentVersions: typeof PHASE_2_RUNTIME_AGENTS;
  readonly jobId: EntityId;
  readonly status: AnalysisRunState;
  readonly currentStage:
    | "queued"
    | AnalysisJobStage
    | "complete";
  readonly progress: number;
  readonly warnings: readonly AnalysisWarning[];
  readonly snapshotCount: number;
  readonly currentSnapshot: AnalysisSnapshot | null;
  readonly latestExecution?: AnalysisAgentExecution;
  readonly summary?: StoryAnalysisCountSummary;
  readonly reviewEligibility: AnalysisReviewEligibility;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly completedAt?: IsoDateTime;
}

export interface AnalysisAgentExecution {
  readonly contractVersion: AnalysisContractVersion;
  readonly executionId: EntityId;
  readonly runId: EntityId;
  readonly snapshotId?: EntityId;
  readonly ordinal: number;
  readonly agentId: Phase2RuntimeAgentId;
  readonly agentVersion: Phase2RuntimeAgentVersion;
  readonly status:
    | "queued"
    | "running"
    | "succeeded"
    | "partial"
    | "failed"
    | "cancelled"
    | "interrupted";
  readonly attempt: number;
  /**
   * Durable, monotonic progress for this attempt. Runtime validation enforces
   * 0 <= progress <= 1 and prevents regressions within an attempt.
   */
  readonly progress: number;
  readonly currentStage: AnalysisJobStage;
  readonly checkpoint:
    | {
        readonly checkpointId: EntityId;
        readonly checkpointFingerprint: Sha256;
        readonly stage: AnalysisJobStage;
        readonly schemaVersion: AnalysisContractVersion;
        readonly recordedAt: IsoDateTime;
      }
    | null;
  readonly retryClassification:
    | "retryable"
    | "not_retryable"
    | "retry_exhausted";
  readonly retryPolicy: {
    readonly maxAttempts: number;
    readonly retryableFailureCodes: readonly string[];
  };
  readonly failurePolicy:
    | "fail_closed_without_partial_publication"
    | "preserve_validated_partial"
    | "require_human";
  readonly inputFingerprint: Sha256;
  readonly outputFingerprint?: Sha256;
  readonly outputCollections: readonly AnalysisEntityCollection[];
  readonly outputArtifactId?: EntityId;
  readonly confidence: AnalysisConfidence;
  readonly warnings: readonly AnalysisWarning[];
  readonly provenance: AnalysisProvenance;
  readonly startedAt: IsoDateTime;
  readonly finishedAt?: IsoDateTime;
  readonly failure:
    | {
        readonly code: string;
        readonly classification:
          | "transient"
          | "permanent"
          | "cancelled"
          | "interrupted"
          | "unknown";
        readonly retryable: boolean;
        /**
         * Redacted operator-safe detail; story text and provider payloads are
         * never persisted in an execution failure.
         */
        readonly message: string;
        readonly redacted: true;
      }
    | null;
}

export interface StoryStructure extends AnalysisEntityHeader {
  readonly structureId: EntityId;
  readonly title?: string;
  readonly chapterCount: number;
  readonly sceneCount: number;
  readonly beatCount: number;
  readonly firstChapterId: EntityId | null;
  readonly lastChapterId: EntityId | null;
  readonly structureFingerprint: Sha256;
}

interface HumanEffectiveBoundaryBase {
  readonly parentEntityId: EntityId;
  readonly ordinal: number;
  readonly sourceSpan: AnalysisSourceSpan;
  readonly authority: "human";
  readonly correctionId: EntityId;
}

/**
 * The canonical, human-controlled structural projection for a chapter or
 * scene. Machine-authored fields remain immutable; this overlay records
 * whether the effective view includes the boundary and why.
 */
export type HumanEffectiveBoundary =
  | (HumanEffectiveBoundaryBase & {
      readonly operation: "add" | "move";
      readonly included: true;
    })
  | (HumanEffectiveBoundaryBase & {
      readonly operation: "remove";
      readonly included: false;
    });

export interface AnalysisChapter extends AnalysisEntityHeader {
  readonly chapterId: EntityId;
  readonly title?: string;
  readonly sourceSpan: AnalysisSourceSpan;
  readonly firstSceneId: EntityId | null;
  readonly lastSceneId: EntityId | null;
  readonly sceneCount: number;
  readonly effectiveBoundary?: HumanEffectiveBoundary;
}

export type SceneBoundaryKind =
  | "chapter_start"
  | "explicit_scene_break"
  | "heading"
  | "inferred";

export interface AnalysisScene extends AnalysisEntityHeader {
  readonly sceneId: EntityId;
  readonly chapterId: EntityId;
  readonly heading?: string;
  readonly sourceSpan: AnalysisSourceSpan;
  readonly boundaryKind: SceneBoundaryKind;
  readonly firstBeatId: EntityId | null;
  readonly lastBeatId: EntityId | null;
  readonly beatCount: number;
  readonly effectiveBoundary?: HumanEffectiveBoundary;
}

export type BeatKind =
  | "narration"
  | "dialogue"
  | "action"
  | "description"
  | "transition";

export interface AnalysisBeat extends AnalysisEntityHeader {
  readonly beatId: EntityId;
  readonly chapterId: EntityId;
  readonly sceneId: EntityId;
  readonly kind: BeatKind;
  readonly sourceSpan: AnalysisSourceSpan;
  readonly summary: string;
  readonly dialogueLineId?: EntityId;
  readonly narration?: NarrationSpan;
}

export type CharacterAliasKind =
  | "full_name"
  | "given_name"
  | "family_name"
  | "nickname"
  | "honorific"
  | "title"
  | "description"
  | "other";

export interface CharacterAlias {
  readonly aliasId: EntityId;
  readonly characterId: EntityId;
  readonly alias: string;
  readonly normalizedAlias: string;
  readonly kind: CharacterAliasKind;
  readonly ambiguous: boolean;
  readonly effectiveRange: {
    readonly sourceRange: AnalysisSourceSpan;
    readonly validFromEventId: EntityId | null;
    readonly validThroughEventId: EntityId | null;
  };
  readonly change:
    | "introduced"
    | "continued"
    | "retired"
    | "uncertain";
  readonly previousAliasId?: EntityId;
  readonly confidence: AnalysisConfidence;
  readonly evidence: readonly AnalysisEvidenceSpan[];
}

export interface CharacterHonorificEvidence {
  readonly honorific: string;
  readonly normalizedHonorific: string;
  readonly confidence: AnalysisConfidence;
  readonly evidence: readonly AnalysisEvidenceSpan[];
}

export interface CharacterPronounEvidence {
  readonly pronoun: string;
  readonly normalizedPronoun: string;
  readonly resolution:
    | "resolved"
    | "ambiguous"
    | "unresolved";
  readonly confidence: AnalysisConfidence;
  readonly evidence: readonly AnalysisEvidenceSpan[];
}

interface HumanEffectiveRegistryBase {
  readonly authority: "human";
  readonly correctionId: EntityId;
}

export type HumanEffectiveRegistry =
  | (HumanEffectiveRegistryBase & {
      readonly operation: "merge";
      readonly mergeIntoCharacterId: EntityId;
    })
  | (HumanEffectiveRegistryBase & {
      readonly operation: "split";
      readonly splitIdentity: {
        readonly registryCharacterId: EntityId;
        readonly canonicalName: string;
        readonly normalizedCanonicalName: string;
        readonly mentionIds: readonly EntityId[];
      };
    });

export interface CharacterIdentity extends AnalysisEntityHeader {
  readonly characterId: EntityId;
  readonly registryCharacterId: EntityId;
  readonly projectId: EntityId;
  readonly storyId: EntityId;
  readonly registryScope: "project_story";
  readonly stableAcrossCompatibleRuns: true;
  readonly canonicalName: string;
  readonly normalizedCanonicalName: string;
  readonly kind: "person" | "group" | "nonhuman" | "unknown";
  readonly identityStatus:
    | "resolved"
    | "ambiguous"
    | "unresolved"
    | "unknown";
  readonly aliases: readonly CharacterAlias[];
  readonly honorifics: readonly CharacterHonorificEvidence[];
  readonly pronounEvidence: readonly CharacterPronounEvidence[];
  /**
   * Effective mention bounds are null when a retained registry identity has
   * no surviving mentions after a human merge, split, or structure correction.
   */
  readonly firstMentionId: EntityId | null;
  readonly lastMentionId: EntityId | null;
  readonly namedMentionIds: readonly EntityId[];
  readonly ambiguousMentionIds: readonly EntityId[];
  readonly firstEvidence: readonly AnalysisEvidenceSpan[];
  readonly lastEvidence: readonly AnalysisEvidenceSpan[];
  readonly mentionCount: number;
  /**
   * Human-controlled registry projection. Referenced IDs must be remapped to
   * the current compatible run before a carried-forward correction is applied.
   */
  readonly effectiveRegistry?: HumanEffectiveRegistry;
}

export interface CharacterMention extends AnalysisEntityHeader {
  readonly mentionId: EntityId;
  readonly chapterId: EntityId;
  readonly sceneId: EntityId;
  readonly exactText: ExactAnalysisText;
  readonly mentionKind:
    | "proper_name"
    | "alias"
    | "honorific"
    | "pronoun"
    | "description";
  readonly resolution: "resolved" | "ambiguous" | "unresolved";
  readonly effectiveCharacterId: EntityId | null;
  readonly candidateCharacterIds: readonly EntityId[];
}

export type NarrationDialogueDistinction =
  | "spoken_dialogue"
  | "internal_thought"
  | "quoted_material"
  | "epigraph_or_document"
  | "unresolved_speech"
  | "narration"
  | "ambiguous";

export interface DialogueAttributionCandidate {
  readonly candidateId: EntityId;
  readonly characterId: EntityId | null;
  readonly rank: number;
  readonly confidence: AnalysisConfidence;
  readonly evidence: readonly AnalysisEvidenceSpan[];
  readonly rationale: string;
}

export interface EffectiveDialogueAttribution {
  readonly speakerCharacterId: EntityId | null;
  readonly selectedCandidateId: EntityId | null;
  readonly authority:
    | "runtime_agent"
    | "human_correction"
    | "unresolved";
  readonly correctionId?: EntityId;
  readonly confidence: AnalysisConfidence;
  readonly requiresHumanReview: boolean;
}

export interface AnalysisDialogueLine extends AnalysisEntityHeader {
  readonly dialogueLineId: EntityId;
  readonly chapterId: EntityId;
  readonly sceneId: EntityId;
  readonly beatId: EntityId;
  readonly exactText: ExactAnalysisText;
  readonly distinction: NarrationDialogueDistinction;
  readonly candidates: readonly DialogueAttributionCandidate[];
  readonly speakerState:
    | "unknown"
    | "ambiguous"
    | "proposed"
    | "corrected";
  readonly effectiveAttribution: EffectiveDialogueAttribution;
}

export interface NarrationSpan extends AnalysisEntityHeader {
  readonly narrationSpanId: EntityId;
  readonly chapterId: EntityId;
  readonly sceneId: EntityId;
  readonly exactText: ExactAnalysisText;
  readonly classification:
    | "direct_narration"
    | "internal_thought"
    | "quoted_material"
    | "epigraph_or_document"
    | "unresolved";
  readonly narratorCharacterId: EntityId | null;
}

export type PointOfViewMode =
  | "first_person"
  | "second_person"
  | "third_person_limited"
  | "third_person_omniscient"
  | "mixed"
  | "experimental"
  | "unknown";

export interface PovSegment extends AnalysisEntityHeader {
  readonly povSegmentId: EntityId;
  readonly chapterId: EntityId;
  readonly sceneId: EntityId;
  readonly sourceSpan: AnalysisSourceSpan;
  readonly mode: PointOfViewMode;
  readonly viewpointCharacterId: EntityId | null;
  readonly narratorCharacterId: EntityId | null;
  readonly shiftFromPovSegmentId?: EntityId;
  readonly shiftKind: "initial" | "scene_boundary" | "mid_scene" | "uncertain";
}

export interface StoryLocation extends AnalysisEntityHeader {
  readonly locationId: EntityId;
  readonly canonicalName: string;
  readonly normalizedCanonicalName: string;
  readonly aliases: readonly string[];
  readonly kind:
    | "interior"
    | "exterior"
    | "vehicle"
    | "region"
    | "abstract"
    | "unknown";
  readonly parentLocationId: EntityId | null;
  readonly firstSceneId: EntityId;
  readonly sceneIds: readonly EntityId[];
  readonly sceneAssignments: readonly LocationSceneAssignment[];
  readonly sceneCount: number;
}

export interface LocationSceneAssignment {
  readonly assignmentId: EntityId;
  readonly locationId: EntityId;
  readonly sceneId: EntityId;
  readonly role: "primary" | "secondary" | "mentioned";
  readonly confidence: AnalysisConfidence;
  readonly evidence: readonly AnalysisEvidenceSpan[];
}

export type TimelineEventKind =
  | "present_action"
  | "flashback"
  | "flashforward"
  | "backstory"
  | "relative_time"
  | "ellipsis"
  | "unknown";

export interface TimelineEvent extends AnalysisEntityHeader {
  readonly timelineEventId: EntityId;
  readonly chapterId: EntityId;
  readonly sceneId: EntityId;
  readonly kind: TimelineEventKind;
  readonly label: string;
  readonly narrativeOrdinal: number;
  readonly chronologicalOrdinal: number | null;
  readonly exactTimeExpression?: ExactAnalysisText;
  readonly locationId: EntityId | null;
  readonly participantCharacterIds: readonly EntityId[];
}

export type TemporalRelation =
  | "before"
  | "after"
  | "same_time"
  | "overlaps"
  | "during"
  | "contains"
  | "unknown";

export interface TemporalConstraint extends AnalysisEntityHeader {
  readonly temporalConstraintId: EntityId;
  readonly sourceEventId: EntityId;
  readonly targetEventId: EntityId;
  readonly relation: TemporalRelation;
  readonly approximate: boolean;
  readonly status: "consistent" | "conflicting" | "unresolved";
}

export type CharacterRelationshipKind =
  | "family"
  | "friendship"
  | "romantic"
  | "professional"
  | "adversarial"
  | "authority"
  | "dependency"
  | "alliance"
  | "unknown"
  | "custom";

export interface CharacterRelationshipScope {
  readonly kind: "scene" | "chapter" | "scene_range";
  readonly firstSceneId: EntityId;
  readonly lastSceneId: EntityId;
  readonly sourceRange: AnalysisSourceSpan;
}

export type CharacterRelationshipChange =
  | "established"
  | "strengthened"
  | "weakened"
  | "reversed"
  | "unchanged"
  | "uncertain";

export interface CharacterRelationship extends AnalysisEntityHeader {
  readonly relationshipId: EntityId;
  readonly sourceCharacterId: EntityId | null;
  readonly targetCharacterId: EntityId | null;
  readonly sourceCandidateCharacterIds: readonly EntityId[];
  readonly targetCandidateCharacterIds: readonly EntityId[];
  readonly resolution: "resolved" | "ambiguous" | "unresolved";
  readonly sceneId: EntityId;
  readonly chapterId: EntityId;
  readonly scope: CharacterRelationshipScope;
  readonly validFromEventId: EntityId | null;
  readonly validThroughEventId: EntityId | null;
  readonly kind: CharacterRelationshipKind;
  readonly state: string;
  readonly change: CharacterRelationshipChange;
  readonly previousRelationshipId?: EntityId;
}

export type EmotionalStateKind =
  | "fear"
  | "anger"
  | "sadness"
  | "joy"
  | "surprise"
  | "calm"
  | "disgust"
  | "anticipation"
  | "trust"
  | "confusion"
  | "hope"
  | "guilt"
  | "shame"
  | "grief"
  | "relief"
  | "neutral"
  | "mixed"
  | "unknown"
  | "custom";

interface EmotionalStateBase extends AnalysisEntityHeader {
  readonly emotionalStateId: EntityId;
  readonly sceneId: EntityId;
  readonly emotion: EmotionalStateKind;
  readonly customEmotion?: string;
  readonly note: string;
  readonly valence: number;
  readonly arousal: number;
  readonly intensity: number;
  readonly progression:
    | "initial"
    | "rising"
    | "falling"
    | "shifted"
    | "stable"
    | "uncertain";
  readonly previousEmotionalStateId?: EntityId;
}

export type EmotionalState = EmotionalStateBase &
  (
    | {
        readonly subjectType: "scene";
        readonly characterId?: never;
      }
    | {
        readonly subjectType: "character";
        readonly characterId: EntityId;
      }
  );

export type DramaticIntentKind =
  | "question"
  | "direct"
  | "persuade"
  | "reassure"
  | "reveal"
  | "conceal"
  | "deflect"
  | "threaten"
  | "comfort"
  | "seek_information"
  | "command"
  | "negotiate"
  | "connect"
  | "withdraw"
  | "deceive"
  | "unknown"
  | "custom";

export type DramaticFunction =
  | "setup"
  | "inciting_action"
  | "complication"
  | "reversal"
  | "revelation"
  | "crisis"
  | "climax"
  | "resolution"
  | "transition"
  | "character_development"
  | "relationship_change"
  | "tension"
  | "comic_relief"
  | "exposition"
  | "foreshadowing"
  | "unknown"
  | "custom";

interface DramaticIntentBase extends AnalysisEntityHeader {
  readonly dramaticIntentId: EntityId;
  readonly sceneId: EntityId;
  readonly intent: DramaticIntentKind;
  readonly customIntent?: string;
  readonly dramaticFunction: DramaticFunction;
  readonly customDramaticFunction?: string;
  readonly note: string;
  readonly targetCharacterId?: EntityId;
  readonly status:
    | "pursued"
    | "achieved"
    | "blocked"
    | "abandoned"
    | "concealed"
    | "uncertain";
}

export type DramaticIntent = DramaticIntentBase &
  (
    | {
        readonly subjectType: "scene";
        readonly characterId?: never;
        readonly dialogueLineId?: never;
        readonly beatId?: never;
      }
    | {
        readonly subjectType: "character";
        readonly characterId: EntityId;
        readonly dialogueLineId?: never;
        readonly beatId?: never;
      }
    | {
        readonly subjectType: "dialogue";
        readonly characterId?: never;
        readonly dialogueLineId: EntityId;
        readonly beatId?: never;
      }
    | {
        readonly subjectType: "beat";
        readonly characterId?: never;
        readonly dialogueLineId?: never;
        readonly beatId: EntityId;
      }
  );

export type ContinuityFindingCategory =
  | "possible_duplicate_character"
  | "identity_contradiction"
  | "alias_conflict"
  | "dialogue_speaker_conflict"
  | "chronology_conflict"
  | "location_conflict"
  | "attribute_conflict"
  | "unexplained_object_state_change"
  | "unexplained_character_state_change"
  | "pov_discontinuity"
  | "scene_boundary_uncertainty"
  | "unresolved_reference"
  | "extraction_uncertainty"
  | "other";

export type ContinuityFindingDisposition =
  | "confirmed_issue"
  | "intentional"
  | "false_positive"
  | "deferred"
  | "corrected"
  | "unresolved";

export interface HumanContinuityDisposition {
  readonly disposition: ContinuityFindingDisposition;
  readonly explanation: string;
  readonly actorId: EntityId;
  readonly recordedAt: IsoDateTime;
  readonly provenance: AnalysisProvenance;
  readonly correctionId?: EntityId;
}

export interface ContinuityFinding extends AnalysisEntityHeader {
  readonly continuityFindingId: EntityId;
  readonly category: ContinuityFindingCategory;
  readonly severity: "info" | "warning" | "error" | "blocker";
  readonly machineStatus:
    | "open"
    | "superseded"
    | "resolved_by_correction";
  readonly explanation: string;
  readonly suggestedReviewAction: string;
  readonly relatedEntityIds: readonly EntityId[];
  readonly requiresHumanReview: boolean;
  readonly humanDisposition?: HumanContinuityDisposition;
}

export type AnalysisCorrectionCategory =
  | "structure_boundary"
  | "structure_label"
  | "character_identity"
  | "character_alias"
  | "character_merge"
  | "character_split"
  | "mention_resolution"
  | "dialogue_speaker"
  | "point_of_view"
  | "location_identity"
  | "location_alias"
  | "temporal_order"
  | "relationship"
  | "emotional_state"
  | "dramatic_intent"
  | "continuity_disposition";

interface StructureBoundaryCorrectionPosition {
  readonly parentEntityId: EntityId;
  readonly ordinal: number;
  readonly sourceSpan: AnalysisSourceSpan;
}

export type StructureBoundaryCorrectionPatch =
  StructureBoundaryCorrectionPosition &
    (
      | { readonly operation: "add" }
      | { readonly operation: "remove" }
      | { readonly operation: "move" }
    );

export type SceneBoundaryCorrectionPatch =
  StructureBoundaryCorrectionPatch & {
    readonly boundaryKind: SceneBoundaryKind;
  };

interface StructureBoundaryCorrectionRequestPosition {
  readonly parentEntityId: EntityId;
  readonly ordinal: number;
  readonly sourceSpan: AnalysisSourceSpanSelection;
}

export type StructureBoundaryCorrectionRequestPatch =
  StructureBoundaryCorrectionRequestPosition &
    (
      | { readonly operation: "add" }
      | { readonly operation: "remove" }
      | { readonly operation: "move" }
    );

export type SceneBoundaryCorrectionRequestPatch =
  StructureBoundaryCorrectionRequestPatch & {
    readonly boundaryKind: SceneBoundaryKind;
  };

export interface CharacterIdentityCorrectionPatch {
  readonly canonicalName: string;
  readonly normalizedCanonicalName: string;
  readonly identityStatus:
    | "resolved"
    | "ambiguous"
    | "unresolved"
    | "unknown";
}

export type CharacterAliasCorrectionPatch =
  | {
      readonly operation: "add" | "replace";
      readonly alias: CharacterAlias;
      readonly aliasId?: never;
    }
  | {
      readonly operation: "remove";
      readonly aliasId: EntityId;
      readonly alias?: never;
    };

export interface CharacterMergeCorrectionPatch {
  readonly mergeIntoCharacterId: EntityId;
}

export interface CharacterSplitCorrectionPatch {
  readonly newRegistryCharacterId: EntityId;
  readonly canonicalName: string;
  readonly normalizedCanonicalName: string;
  readonly mentionIds: readonly EntityId[];
}

export interface MentionResolutionCorrectionPatch {
  readonly resolution: "resolved" | "ambiguous" | "unresolved";
  readonly effectiveCharacterId: EntityId | null;
  readonly candidateCharacterIds: readonly EntityId[];
}

export interface DialogueSpeakerCorrectionPatch {
  readonly speakerCharacterId: EntityId | null;
  readonly selectedCandidateId: EntityId | null;
  readonly requiresHumanReview: boolean;
}

export interface PointOfViewCorrectionPatch {
  readonly mode: PointOfViewMode;
  readonly viewpointCharacterId: EntityId | null;
  readonly narratorCharacterId: EntityId | null;
}

export interface LocationIdentityCorrectionPatch {
  readonly canonicalName: string;
  readonly normalizedCanonicalName: string;
  readonly kind:
    | "interior"
    | "exterior"
    | "vehicle"
    | "region"
    | "abstract"
    | "unknown";
  readonly parentLocationId: EntityId | null;
}

export interface LocationAliasCorrectionPatch {
  readonly operation: "add" | "remove";
  readonly alias: string;
}

export interface TemporalOrderCorrectionPatch {
  readonly relation: TemporalRelation;
  readonly approximate: boolean;
  readonly status: "consistent" | "conflicting" | "unresolved";
}

export interface RelationshipCorrectionPatch {
  readonly sourceCharacterId: EntityId;
  readonly targetCharacterId: EntityId;
  readonly kind: CharacterRelationshipKind;
  readonly state: string;
  readonly change: CharacterRelationshipChange;
  readonly scope: CharacterRelationshipScope;
  readonly validFromEventId: EntityId | null;
  readonly validThroughEventId: EntityId | null;
}

export interface EmotionalStateCorrectionPatch {
  readonly emotion: EmotionalStateKind;
  readonly customEmotion: string | null;
  readonly note: string;
  readonly valence: number;
  readonly arousal: number;
  readonly intensity: number;
  readonly progression:
    | "initial"
    | "rising"
    | "falling"
    | "shifted"
    | "stable"
    | "uncertain";
}

export interface DramaticIntentCorrectionPatch {
  readonly intent: DramaticIntentKind;
  readonly customIntent: string | null;
  readonly dramaticFunction: DramaticFunction;
  readonly customDramaticFunction: string | null;
  readonly note: string;
  readonly targetCharacterId: EntityId | null;
  readonly status:
    | "pursued"
    | "achieved"
    | "blocked"
    | "abandoned"
    | "concealed"
    | "uncertain";
}

export interface ContinuityDispositionCorrectionPatch {
  readonly disposition: ContinuityFindingDisposition;
  readonly explanation: string;
}

export type AnalysisCorrectionSelection =
  | {
      readonly category: "structure_boundary";
      readonly targetCollection: "chapters";
      readonly patch: StructureBoundaryCorrectionPatch;
    }
  | {
      readonly category: "structure_boundary";
      readonly targetCollection: "scenes";
      readonly patch: SceneBoundaryCorrectionPatch;
    }
  | {
      readonly category: "structure_label";
      readonly targetCollection: "chapters";
      readonly patch: { readonly title: string | null };
    }
  | {
      readonly category: "structure_label";
      readonly targetCollection: "scenes";
      readonly patch: { readonly heading: string | null };
    }
  | {
      readonly category: "character_identity";
      readonly targetCollection: "characters";
      readonly patch: CharacterIdentityCorrectionPatch;
    }
  | {
      readonly category: "character_alias";
      readonly targetCollection: "characters";
      readonly patch: CharacterAliasCorrectionPatch;
    }
  | {
      readonly category: "character_merge";
      readonly targetCollection: "characters";
      readonly patch: CharacterMergeCorrectionPatch;
    }
  | {
      readonly category: "character_split";
      readonly targetCollection: "characters";
      readonly patch: CharacterSplitCorrectionPatch;
    }
  | {
      readonly category: "mention_resolution";
      readonly targetCollection: "mentions";
      readonly patch: MentionResolutionCorrectionPatch;
    }
  | {
      readonly category: "dialogue_speaker";
      readonly targetCollection: "dialogue-lines";
      readonly patch: DialogueSpeakerCorrectionPatch;
    }
  | {
      readonly category: "point_of_view";
      readonly targetCollection: "pov-segments";
      readonly patch: PointOfViewCorrectionPatch;
    }
  | {
      readonly category: "location_identity";
      readonly targetCollection: "locations";
      readonly patch: LocationIdentityCorrectionPatch;
    }
  | {
      readonly category: "location_alias";
      readonly targetCollection: "locations";
      readonly patch: LocationAliasCorrectionPatch;
    }
  | {
      readonly category: "temporal_order";
      readonly targetCollection: "temporal-constraints";
      readonly patch: TemporalOrderCorrectionPatch;
    }
  | {
      readonly category: "relationship";
      readonly targetCollection: "relationships";
      readonly patch: RelationshipCorrectionPatch;
    }
  | {
      readonly category: "emotional_state";
      readonly targetCollection: "emotional-states";
      readonly patch: EmotionalStateCorrectionPatch;
    }
  | {
      readonly category: "dramatic_intent";
      readonly targetCollection: "dramatic-intents";
      readonly patch: DramaticIntentCorrectionPatch;
    }
  | {
      readonly category: "continuity_disposition";
      readonly targetCollection: "continuity-findings";
      readonly patch: ContinuityDispositionCorrectionPatch;
    };

export type AnalysisCorrectionPatch =
  AnalysisCorrectionSelection["patch"];

export type AnalysisCorrectionRequestSelection =
  | {
      readonly category: "structure_boundary";
      readonly targetCollection: "chapters";
      readonly patch: StructureBoundaryCorrectionRequestPatch;
    }
  | {
      readonly category: "structure_boundary";
      readonly targetCollection: "scenes";
      readonly patch: SceneBoundaryCorrectionRequestPatch;
    }
  | Exclude<
      AnalysisCorrectionSelection,
      { readonly category: "structure_boundary" }
    >;

export type AnalysisCorrectionRequestPatch =
  AnalysisCorrectionRequestSelection["patch"];

interface AnalysisCorrectionBase {
  readonly contractVersion: AnalysisContractVersion;
  readonly correctionId: EntityId;
  readonly projectId: EntityId;
  readonly runId: EntityId;
  readonly snapshotId: EntityId;
  readonly targetEntityId: EntityId;
  readonly expectedTargetRevision: number;
  readonly expectedRunFingerprint: Sha256;
  readonly previousValueFingerprint: Sha256;
  readonly correctedValueFingerprint: Sha256;
  readonly actor: {
    readonly classification: "human";
    readonly actorId: EntityId;
  };
  /**
   * Nonblank human rationale, bounded to 1,000 Unicode code points.
   */
  readonly reason: string;
  readonly recordedAt: IsoDateTime;
  readonly immutable: true;
  readonly lockedAgainstAutomation: true;
  readonly supersedesCorrectionId?: EntityId;
  readonly idempotencyFingerprint: Sha256;
}

export type AnalysisCorrection =
  AnalysisCorrectionBase & AnalysisCorrectionSelection;

export const ANALYSIS_GATE_IDS = [
  "story_structure_review",
  "character_registry_review",
  "dialogue_attribution_review",
  "whole_book_analysis_review"
] as const;

export type AnalysisGateId = (typeof ANALYSIS_GATE_IDS)[number];
export type AnalysisGateState =
  | "pending"
  | "approved"
  | "changes_requested"
  | "rejected"
  | "invalidated";

export interface AnalysisGateEvidence {
  readonly projectId: EntityId;
  readonly sourceDocumentId: EntityId;
  readonly extractionId: EntityId;
  readonly extractionRevision: number;
  readonly storyId: EntityId;
  readonly profileId: WholeBookAnalysisProfileId;
  readonly profileFingerprint:
    typeof WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT;
  readonly runId: EntityId;
  readonly runFingerprint: Sha256;
  readonly snapshotId: EntityId;
  readonly snapshotRevision: number;
  readonly snapshotFingerprint: Sha256;
  readonly artifactFingerprint: Sha256;
  readonly evidenceFingerprint: Sha256;
}

export interface AnalysisGateReview {
  readonly contractVersion: AnalysisContractVersion;
  readonly reviewId: EntityId;
  readonly projectId: EntityId;
  readonly gateId: AnalysisGateId;
  readonly runId: EntityId;
  readonly snapshotId: EntityId;
  readonly state: AnalysisGateState;
  readonly revision: number;
  readonly artifactFingerprint: Sha256;
  readonly evidenceFingerprint: Sha256;
  readonly evidence: AnalysisGateEvidence;
  readonly openWarningIds: readonly EntityId[];
  readonly acknowledgedWarningIds: readonly EntityId[];
  readonly latestDecisionId?: EntityId;
  readonly latestDecision: AnalysisGateDecision | null;
  readonly provenance: AnalysisProvenance;
  readonly updatedAt: IsoDateTime;
}

export interface AnalysisGateDecision {
  readonly contractVersion: AnalysisContractVersion;
  readonly decisionId: EntityId;
  readonly reviewId: EntityId;
  readonly projectId: EntityId;
  readonly gateId: AnalysisGateId;
  readonly runId: EntityId;
  readonly snapshotId: EntityId;
  readonly decision:
    | "approved"
    | "changes_requested"
    | "rejected";
  readonly artifactFingerprint: Sha256;
  readonly evidenceFingerprint: Sha256;
  readonly evidence: AnalysisGateEvidence;
  readonly actor: {
    readonly classification: "human";
    readonly actorId: EntityId;
  };
  /**
   * Nonblank human rationale, bounded to 4,000 Unicode code points.
   */
  readonly rationale: string;
  readonly acknowledgedWarningIds: readonly EntityId[];
  readonly provenance: AnalysisProvenance;
  readonly decidedAt: IsoDateTime;
  readonly immutable: true;
  readonly supersedesDecisionId?: EntityId;
}

export interface AnalysisEntityMap {
  readonly "agent-executions": AnalysisAgentExecution;
  readonly chapters: AnalysisChapter;
  readonly scenes: AnalysisScene;
  readonly beats: AnalysisBeat;
  readonly characters: CharacterIdentity;
  readonly mentions: CharacterMention;
  readonly "dialogue-lines": AnalysisDialogueLine;
  readonly "narration-spans": NarrationSpan;
  readonly "pov-segments": PovSegment;
  readonly locations: StoryLocation;
  readonly "timeline-events": TimelineEvent;
  readonly "temporal-constraints": TemporalConstraint;
  readonly relationships: CharacterRelationship;
  readonly "emotional-states": EmotionalState;
  readonly "dramatic-intents": DramaticIntent;
  readonly "continuity-findings": ContinuityFinding;
}

export type AnalysisEntity =
  AnalysisEntityMap[keyof AnalysisEntityMap];
