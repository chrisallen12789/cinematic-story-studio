import type {
  AmbienceCue,
  ApprovalGateId,
  CastingAssignment,
  Chapter,
  Character,
  Confidence,
  ContinuityRecord,
  ContractWarning,
  DialogueAttribution,
  DialogueLine,
  EntityId,
  EntityReference,
  FoleyCue,
  ImportedStory,
  IsoDateTime,
  JsonValue,
  MusicCue,
  PerformanceDirection,
  ProductionTimeline,
  ProviderModel,
  Provenance,
  QualityControlFinding,
  RenderJob,
  RenderManifest,
  Scene,
  Sha256,
  SourceDocument,
  StoryBeat,
  VoiceProfile
} from "./domain.js";

export interface HumanReviewRequirement {
  readonly required: boolean;
  readonly reasons: readonly string[];
  readonly gateIds: readonly ApprovalGateId[];
}

export interface RetryPolicy {
  readonly maxAttempts: number;
  readonly initialBackoffMs: number;
  readonly maximumBackoffMs: number;
  readonly backoffMultiplier: number;
  readonly retryableFailureCodes: readonly string[];
}

export interface FailurePolicy {
  readonly mode:
    | "fail_fast"
    | "preserve_partial"
    | "skip_with_warning"
    | "require_human";
  readonly preservePartialOutput: boolean;
  readonly terminalFailureCodes: readonly string[];
}

export interface CostMetadata {
  readonly known: boolean;
  readonly currency: string;
  readonly estimatedMinorUnits: number;
  readonly actualMinorUnits: number | null;
  readonly inputUnits: number;
  readonly outputUnits: number;
  readonly unitName:
    | "none"
    | "characters"
    | "tokens"
    | "seconds"
    | "requests";
}

export interface AgentInputSnapshot {
  readonly schemaRef: string;
  readonly entity: EntityReference;
  readonly contentFingerprint: Sha256;
}

export interface RuntimeAgentDefinition {
  readonly id: EntityId;
  readonly version: `${number}.${number}.${number}`;
  readonly purpose: string;
  readonly acceptedInputSchemas: readonly string[];
  readonly outputSchemaRef: string;
  readonly humanReview: HumanReviewRequirement;
  readonly retryPolicy: RetryPolicy;
  readonly failurePolicy: FailurePolicy;
}

export interface AgentExecutionEnvelope {
  readonly executionId: EntityId;
  readonly agentId: EntityId;
  readonly agentVersion: `${number}.${number}.${number}`;
  readonly purpose: string;
  readonly acceptedInputs: readonly AgentInputSnapshot[];
  readonly outputSchemaRef: string;
  readonly status:
    | "queued"
    | "running"
    | "succeeded"
    | "partial"
    | "failed"
    | "cancelled";
  readonly attempt: number;
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
  readonly humanReview: HumanReviewRequirement;
  readonly retryPolicy: RetryPolicy;
  readonly failurePolicy: FailurePolicy;
  readonly provenance: Provenance;
  readonly providerModel: ProviderModel;
  readonly cost: CostMetadata;
  readonly startedAt: IsoDateTime;
  readonly finishedAt?: IsoDateTime;
  readonly failureCode?: string;
}

export interface AgentResult<TOutput = JsonValue> {
  readonly execution: AgentExecutionEnvelope;
  readonly output: TOutput | null;
}

export interface ManuscriptIngestOutput {
  readonly sourceDocuments: readonly SourceDocument[];
  readonly importedStory: ImportedStory;
}

export interface StoryStructureOutput {
  readonly chapters: readonly Chapter[];
  readonly scenes: readonly Scene[];
  readonly storyBeats: readonly StoryBeat[];
}

export interface CharacterDialogueOutput {
  readonly characters: readonly Character[];
  readonly dialogueLines: readonly DialogueLine[];
  readonly dialogueAttributions: readonly DialogueAttribution[];
}

export interface CastingDirectorOutput {
  readonly voiceProfiles: readonly VoiceProfile[];
  readonly castingAssignments: readonly CastingAssignment[];
}

export interface PerformanceDirectorOutput {
  readonly performanceDirections: readonly PerformanceDirection[];
}

export interface SoundDesignerOutput {
  readonly ambienceCues: readonly AmbienceCue[];
  readonly foleyCues: readonly FoleyCue[];
}

export interface MusicSupervisorOutput {
  readonly musicCues: readonly MusicCue[];
}

export interface ContinuityOutput {
  readonly continuityRecords: readonly ContinuityRecord[];
}

export interface MixMasterOutput {
  readonly productionTimeline: ProductionTimeline;
  readonly renderJob: RenderJob;
  readonly renderManifest: RenderManifest | null;
}

export interface QualityControlOutput {
  readonly findings: readonly QualityControlFinding[];
}

export const RUNTIME_AGENT_IDS = [
  "manuscript-ingest",
  "story-structure",
  "character-dialogue",
  "casting-director",
  "performance-director",
  "sound-designer",
  "music-supervisor",
  "continuity",
  "mix-master",
  "quality-control"
] as const;

export type RuntimeAgentId = (typeof RUNTIME_AGENT_IDS)[number];
