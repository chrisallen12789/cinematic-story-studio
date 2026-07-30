export const SCHEMA_VERSION = "1.0.0" as const;

export type SchemaVersion = typeof SCHEMA_VERSION;
export type EntityId = string;
export type Sha256 = string;
export type IsoDateTime = string;
export type JsonPrimitive = null | boolean | number | string;
export type JsonValue =
  | JsonPrimitive
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

export interface EntityReference {
  readonly entityType: string;
  readonly entityId: EntityId;
  readonly revision?: number;
}

export interface TextSpan {
  readonly sourceDocumentId: EntityId;
  readonly offsetUnit: "unicode-code-point";
  readonly startOffset: number;
  readonly endOffset: number;
  readonly textSha256: Sha256;
}

export interface Provenance {
  readonly origin: "import" | "runtime_agent" | "human" | "system";
  readonly recordedAt: IsoDateTime;
  readonly actorId: string;
  readonly agentExecutionId?: EntityId;
  readonly sourceReferences?: readonly EntityReference[];
  readonly inputFingerprint?: Sha256;
  readonly notes?: string;
}

export interface VersionedEntity {
  readonly schemaVersion: SchemaVersion;
  readonly revision: number;
  readonly provenance: Provenance;
}

export interface Confidence {
  readonly score: number;
  readonly basis: string;
  readonly calibrationId?: string;
  readonly fieldScores?: Readonly<Record<string, number>>;
}

export interface ContractWarning {
  readonly code: string;
  readonly severity: "info" | "warning" | "error";
  readonly message: string;
  readonly requiresHumanReview: boolean;
  readonly relatedEntities?: readonly EntityReference[];
}

export interface HumanCorrection {
  readonly correctionId: EntityId;
  readonly target: EntityReference;
  readonly fieldPath: `/${string}`;
  readonly previousValueFingerprint?: Sha256;
  readonly correctedValue: JsonValue;
  readonly reason: string;
  readonly authority: {
    readonly source: "human";
    readonly actorId: string;
  };
  readonly recordedAt: IsoDateTime;
  readonly immutable: true;
  readonly lockedAgainstAutomation: true;
  readonly supersedesCorrectionId?: EntityId;
}

export type ApprovalGateId =
  | "import_review"
  | "scene_segmentation_review"
  | "character_review"
  | "dialogue_attribution_review"
  | "casting_approval"
  | "performance_direction_approval"
  | "sound_design_approval"
  | "first_scene_render_approval"
  | "chapter_approval"
  | "final_master_approval";

export type ApprovalState =
  | "not_ready"
  | "pending"
  | "approved"
  | "changes_requested"
  | "rejected"
  | "invalidated";

export interface Project extends VersionedEntity {
  readonly projectId: EntityId;
  readonly name: string;
  readonly status:
    | "draft"
    | "analysis"
    | "production"
    | "rendering"
    | "completed"
    | "archived";
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly storyId?: EntityId | null;
  readonly sourceDocumentIds: readonly EntityId[];
  readonly activeTimelineId?: EntityId | null;
  readonly approvalDecisionIds: readonly EntityId[];
  readonly dataClassification: "private_local_content";
  readonly settings: {
    readonly defaultLanguage: string;
    readonly cloudTransmissionPolicy:
      | "local_only"
      | "explicit_per_operation";
    readonly audioProfile:
      | "cinematic_stereo_v1"
      | "audiobook_stereo_v1"
      | "audiobook_mono_v1";
  };
}

export interface ImportedStory extends VersionedEntity {
  readonly storyId: EntityId;
  readonly projectId: EntityId;
  readonly title: string;
  readonly sourceDocumentIds: readonly EntityId[];
  readonly contentFingerprint: Sha256;
  readonly originalTextPreserved: true;
  readonly importedAt: IsoDateTime;
  readonly chapterIds: readonly EntityId[];
  readonly warnings?: readonly ContractWarning[];
}

export type SourceMediaType =
  | "text/plain"
  | "text/markdown"
  | "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  | "application/epub+zip"
  | "application/pdf";

export type DocumentFormat =
  | "txt"
  | "markdown"
  | "docx"
  | "epub"
  | "pdf";

export interface SourceDocument extends VersionedEntity {
  readonly documentId: EntityId;
  readonly projectId: EntityId;
  readonly displayName: string;
  readonly mediaType: SourceMediaType;
  readonly declaredFormat: DocumentFormat;
  readonly contentSha256: Sha256;
  readonly byteLength: number;
  readonly importedAt: IsoDateTime;
  readonly originalTextPreserved: true;
  readonly originalBytesPreserved: true;
  readonly storageKey: string;
  readonly extractionStatus:
    | "pending"
    | "running"
    | "complete"
    | "partial"
    | "failed";
  readonly sourceRevision: number;
  readonly supersedesDocumentId?: EntityId;
  readonly textSha256?: Sha256;
  readonly encoding?: string;
  readonly newlineStyle?: "none" | "mixed" | "crlf" | "lf" | "cr";
  readonly warnings: readonly ContractWarning[];
}

export interface Chapter extends VersionedEntity {
  readonly chapterId: EntityId;
  readonly projectId: EntityId;
  readonly storyId: EntityId;
  readonly ordinal: number;
  readonly title?: string;
  readonly sourceSpan: TextSpan;
  readonly sceneIds: readonly EntityId[];
  readonly approvalState: ApprovalState;
}

export interface Scene extends VersionedEntity {
  readonly sceneId: EntityId;
  readonly projectId: EntityId;
  readonly chapterId: EntityId;
  readonly ordinal: number;
  readonly heading?: string;
  readonly location?: string;
  readonly mood?: string;
  readonly sourceSpan: TextSpan;
  readonly beatIds: readonly EntityId[];
  readonly dialogueLineIds: readonly EntityId[];
  readonly characterIds: readonly EntityId[];
  readonly approvalState: ApprovalState;
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
}

export interface StoryBeat extends VersionedEntity {
  readonly beatId: EntityId;
  readonly projectId: EntityId;
  readonly sceneId: EntityId;
  readonly ordinal: number;
  readonly kind:
    | "narration"
    | "dialogue"
    | "action"
    | "description"
    | "transition";
  readonly sourceSpan: TextSpan;
  readonly summary?: string;
  readonly dialogueLineId?: EntityId;
}

export interface Character extends VersionedEntity {
  readonly characterId: EntityId;
  readonly projectId: EntityId;
  readonly storyId: EntityId;
  readonly displayName: string;
  readonly aliases: readonly string[];
  readonly description?: string;
  readonly sourceReferences: readonly TextSpan[];
  readonly voiceProfileId?: EntityId | null;
  readonly humanCorrections: readonly HumanCorrection[];
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
}

export interface DialogueLine extends VersionedEntity {
  readonly lineId: EntityId;
  readonly projectId: EntityId;
  readonly sceneId: EntityId;
  readonly beatId: EntityId;
  readonly ordinal: number;
  readonly sourceSpan: TextSpan;
  readonly verbatimText: string;
  readonly textSha256: Sha256;
  readonly originalTextPreserved: true;
  readonly attributionId: EntityId;
}

export interface DialogueAttribution extends VersionedEntity {
  readonly attributionId: EntityId;
  readonly projectId: EntityId;
  readonly lineId: EntityId;
  readonly proposedSpeakerId: EntityId | null;
  readonly effectiveSpeakerId: EntityId | null;
  readonly effectiveAuthority: "runtime_agent" | "human";
  readonly evidence: readonly TextSpan[];
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
  readonly humanCorrections: readonly HumanCorrection[];
  readonly updatedAt: IsoDateTime;
}

export interface VoiceProfile extends VersionedEntity {
  readonly voiceProfileId: EntityId;
  readonly projectId: EntityId;
  readonly displayName: string;
  readonly providerId: string;
  readonly voiceId: string;
  readonly voiceVersion: string;
  readonly continuityKey: Sha256;
  readonly executionLocation: "local" | "cloud";
  readonly characteristics: readonly string[];
  readonly usageRights:
    | "verified"
    | "user_attested"
    | "unknown"
    | "restricted";
  readonly createdAt: IsoDateTime;
  readonly settings?: Readonly<Record<string, string | number | boolean>>;
}

export interface CastingAssignment extends VersionedEntity {
  readonly assignmentId: EntityId;
  readonly projectId: EntityId;
  readonly characterId: EntityId;
  readonly voiceProfileId: EntityId;
  readonly status: "proposed" | "approved" | "rejected" | "superseded";
  readonly continuityKey: Sha256;
  readonly rationale: string;
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
  readonly approvalDecisionId?: EntityId;
  readonly humanCorrections: readonly HumanCorrection[];
}

export interface PerformanceDirection extends VersionedEntity {
  readonly directionId: EntityId;
  readonly projectId: EntityId;
  readonly target: EntityReference;
  readonly delivery: string;
  readonly emotion?: string;
  readonly paceWordsPerMinute?: number;
  readonly intensity: number;
  readonly pauseBeforeMs: number;
  readonly pauseAfterMs: number;
  readonly pronunciationNotes?: readonly string[];
  readonly status: "proposed" | "approved" | "superseded";
  readonly approvalDecisionId?: EntityId;
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
  readonly humanCorrections: readonly HumanCorrection[];
}

export interface Fade {
  readonly inMs: number;
  readonly outMs: number;
  readonly curve: "linear" | "equal_power" | "s_curve";
}

export interface CuePlacement {
  readonly startMs: number;
  readonly durationMs: number;
  readonly gainDb: number;
  readonly pan: number;
  readonly fade: Fade;
  readonly placementFingerprint: Sha256;
}

export type CueStatus = "proposed" | "approved" | "superseded";
export type SoundSourceKind = "recorded" | "library" | "generated";

export interface AmbienceCue extends VersionedEntity {
  readonly cueId: EntityId;
  readonly projectId: EntityId;
  readonly sceneId: EntityId;
  readonly description: string;
  readonly placement: CuePlacement;
  readonly loop: boolean;
  readonly sourceKind: SoundSourceKind;
  readonly assetId?: EntityId;
  readonly status: CueStatus;
  readonly approvalDecisionId?: EntityId;
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
}

export interface FoleyCue extends VersionedEntity {
  readonly cueId: EntityId;
  readonly projectId: EntityId;
  readonly sceneId: EntityId;
  readonly event: string;
  readonly placement: CuePlacement;
  readonly sourceKind: SoundSourceKind;
  readonly assetId?: EntityId;
  readonly status: CueStatus;
  readonly approvalDecisionId?: EntityId;
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
}

export interface MusicCue extends VersionedEntity {
  readonly cueId: EntityId;
  readonly projectId: EntityId;
  readonly sceneId: EntityId;
  readonly purpose: string;
  readonly placement: CuePlacement;
  readonly sourceKind: "original" | "licensed" | "generated";
  readonly assetId?: EntityId;
  readonly rightsStatus:
    | "verified"
    | "user_attested"
    | "unknown"
    | "restricted";
  readonly duckUnderDialogue: boolean;
  readonly status: CueStatus;
  readonly approvalDecisionId?: EntityId;
  readonly confidence: Confidence;
  readonly warnings: readonly ContractWarning[];
}

export interface TimelineSegment {
  readonly segmentId: EntityId;
  readonly sceneId: EntityId;
  readonly ordinal: number;
  readonly startMs: number;
  readonly durationMs: number;
}

export interface ProductionTimeline extends VersionedEntity {
  readonly timelineId: EntityId;
  readonly projectId: EntityId;
  readonly sampleRateHz: 44100 | 48000 | 96000;
  readonly channelCount: 1 | 2;
  readonly timebase: "milliseconds";
  readonly segments: readonly TimelineSegment[];
  readonly ambienceCueIds: readonly EntityId[];
  readonly foleyCueIds: readonly EntityId[];
  readonly musicCueIds: readonly EntityId[];
  readonly lockedApprovalDecisionIds: readonly EntityId[];
  readonly orderingFingerprint: Sha256;
}

export interface ContinuityRecord extends VersionedEntity {
  readonly recordId: EntityId;
  readonly projectId: EntityId;
  readonly category:
    | "voice"
    | "pronunciation"
    | "performance"
    | "location"
    | "ambience"
    | "foley"
    | "music"
    | "timeline";
  readonly subject: EntityReference;
  readonly canonicalValue: JsonValue;
  readonly status: "active" | "superseded";
  readonly authority: "runtime_agent" | "human";
  readonly lockedAgainstAutomation: boolean;
  readonly humanCorrections: readonly HumanCorrection[];
  readonly warnings: readonly ContractWarning[];
}

export interface RenderJob extends VersionedEntity {
  readonly jobId: EntityId;
  readonly projectId: EntityId;
  readonly scope: {
    readonly kind: "scene" | "chapter" | "project";
    readonly entityId: EntityId;
  };
  readonly timelineId: EntityId;
  readonly manifestId?: EntityId;
  readonly status:
    | "queued"
    | "running"
    | "cancel_requested"
    | "cancelled"
    | "failed"
    | "succeeded";
  readonly progress: number;
  readonly attempt: number;
  readonly inputFingerprint: Sha256;
  readonly checkpointKey?: string;
  readonly resumeOfJobId?: EntityId;
  readonly retryOfJobId?: EntityId;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly cancellationRequested: boolean;
  readonly failure?: {
    readonly code: string;
    readonly redactedMessage: string;
    readonly retryable: boolean;
  };
}

export interface ProviderModel {
  readonly providerId: string;
  readonly modelId: string;
  readonly modelVersion: string;
  readonly executionLocation: "local" | "cloud";
  readonly configurationFingerprint: Sha256;
}

export interface RenderAsset {
  readonly assetId: EntityId;
  readonly role:
    | "narration"
    | "dialogue"
    | "ambience"
    | "foley"
    | "music"
    | "mixed_scene"
    | "mixed_chapter"
    | "master";
  readonly contentSha256: Sha256;
  readonly durationMs: number;
  readonly sampleRateHz: number;
  readonly channelCount: number;
}

export interface RenderManifest extends VersionedEntity {
  readonly manifestId: EntityId;
  readonly projectId: EntityId;
  readonly jobId: EntityId;
  readonly timelineId: EntityId;
  readonly timelineRevision: number;
  readonly createdAt: IsoDateTime;
  readonly deterministic: boolean;
  readonly inputFingerprint: Sha256;
  readonly configurationFingerprint: Sha256;
  readonly providerModels: readonly ProviderModel[];
  readonly assets: readonly RenderAsset[];
  readonly output: {
    readonly format: "wav" | "mp3" | "m4b";
    readonly sampleRateHz: 44100 | 48000;
    readonly channelCount: 1 | 2;
    readonly targetLufs: number;
    readonly maximumTruePeakDbtp: number;
    readonly contentSha256?: Sha256;
  };
  readonly seed: number;
}

export interface QualityControlFinding extends VersionedEntity {
  readonly findingId: EntityId;
  readonly projectId: EntityId;
  readonly renderManifestId: EntityId;
  readonly category:
    | "clipping"
    | "loudness"
    | "silence_boundary"
    | "missing_clip"
    | "sample_rate"
    | "channel_count"
    | "cue_placement"
    | "provider_failure"
    | "voice_continuity"
    | "approval"
    | "other";
  readonly severity: "info" | "warning" | "error" | "blocker";
  readonly status: "open" | "accepted" | "resolved" | "waived";
  readonly message: string;
  readonly metricValue?: number;
  readonly thresholdValue?: number;
  readonly timelineStartMs?: number;
  readonly timelineEndMs?: number;
  readonly requiresHumanReview: boolean;
  readonly detectedAt: IsoDateTime;
  readonly resolutionCorrectionId?: EntityId;
}

export interface ApprovalDecision extends VersionedEntity {
  readonly decisionId: EntityId;
  readonly projectId: EntityId;
  readonly gateId: ApprovalGateId;
  readonly scope: EntityReference;
  readonly decision:
    | "pending"
    | "approved"
    | "changes_requested"
    | "rejected"
    | "revoked";
  readonly actor: {
    readonly type: "human" | "system";
    readonly actorId: string;
  };
  readonly rationale: string;
  readonly evidenceFingerprint: Sha256;
  readonly decidedAt: IsoDateTime;
  readonly immutable: true;
  readonly supersedesDecisionId?: EntityId;
  readonly invalidatesEntityRevisions?: readonly EntityReference[];
}
