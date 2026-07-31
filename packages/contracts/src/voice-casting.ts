import type {
  EntityId,
  IsoDateTime,
  Sha256
} from "./domain.js";
import type {
  AnalysisConfidence,
  AnalysisWarning
} from "./story-analysis.js";

export const VOICE_CASTING_CONTRACT_VERSION = "3.0.0" as const;
export const GOVERNED_VOICE_CASTING_PROFILE_ID =
  "governed-voice-casting-v1@1.0.0" as const;
export const VOICE_CASTING_PRODUCER_ID =
  "voice-casting-orchestrator@1.0.0" as const;
export const VOICE_RIGHTS_POLICY_ID =
  "voice-rights-policy-v1" as const;
export const GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT =
  "3eaa6b4d1333b49e55707b1e9aa20606f262e1315a043bff2912a0fe77f97fa6" as const;

export const VOICE_CASTING_LIMITS = Object.freeze({
  maximumProductionRoles: 300,
  maximumVoiceProfiles: 5_000,
  maximumPreReductionCandidatesPerRole: 50,
  maximumFinalCandidatesPerRole: 12,
  defaultPageSize: 50,
  maximumPageSize: 200,
  maximumExplanationCodePoints: 2_000,
  maximumWarningsPerEntity: 32,
  maximumHardConstraintResults: 16,
  maximumSoftPreferenceResults: 16,
  maximumConflictsPerRun: 10_000,
  maximumVoiceReusePerProfile: 2
});

export const CASTING_JOB_STAGES = [
  "validate_phase_2_approvals",
  "freeze_source_analysis_evidence",
  "load_voice_catalog_revision",
  "create_production_roles",
  "evaluate_role_constraints",
  "generate_bounded_candidates",
  "evaluate_differentiation_conflicts",
  "publish_casting_run",
  "publish_reviewable_cast_snapshot"
] as const;

export const CASTING_COMPATIBILITY_RULES = [
  "hard_constraints_fail_closed",
  "soft_preferences_score_separately",
  "unknown_remains_unknown",
  "no_automatic_assignment",
  "declared_metadata_only"
] as const;

export const CASTING_RIGHTS_ELIGIBILITY_RULES = [
  "verified_eligible",
  "restricted_requires_acknowledgement",
  "unknown_ineligible",
  "prohibited_ineligible"
] as const;

export const CASTING_HARD_CONSTRAINT_IDS = [
  "language_support",
  "provider_available",
  "model_available",
  "rights_not_prohibited",
  "rights_known",
  "required_consent",
  "voice_not_blocked",
  "declared_capabilities",
  "role_length_suitability"
] as const;

export const CASTING_SOFT_PREFERENCE_IDS = [
  "locale_match",
  "narration_suitability",
  "dialogue_suitability",
  "expressive_range",
  "age_presentation",
  "vocal_presentation",
  "vocal_texture",
  "speaking_rate",
  "emotional_range",
  "long_form_preference"
] as const;

export const CASTING_CONFLICT_CATEGORIES = [
  "incompatible_voice_reuse",
  "narrator_major_character_reuse",
  "metadata_similarity_risk",
  "accent_or_locale_mismatch",
  "insufficient_expressive_range",
  "rights_conflict",
  "provider_or_model_unavailable",
  "deprecated_voice",
  "role_length_suitability",
  "unresolved_role_assignment",
  "voice_reuse_threshold_exceeded"
] as const;

export const CASTING_CORRECTION_CATEGORIES = [
  "select_voice",
  "clear_assignment",
  "lock_assignment",
  "unlock_assignment",
  "mark_intentionally_uncast",
  "change_role_label",
  "change_casting_requirement",
  "acknowledge_restricted_rights",
  "approve_voice_reuse",
  "reject_candidate",
  "record_custom_rationale"
] as const;

export const CASTING_GATE_IDS = [
  "narrator_casting_review",
  "character_casting_review",
  "complete_cast_review"
] as const;

/**
 * Canonical UTF-8 JSON for the governed profile values. Object keys are
 * lexicographically ordered; rule-array order is policy order.
 */
export const GOVERNED_VOICE_CASTING_PROFILE_CANONICAL_JSON =
  '{"castingContractVersion":"3.0.0","compatibilityRules":["hard_constraints_fail_closed","soft_preferences_score_separately","unknown_remains_unknown","no_automatic_assignment","declared_metadata_only"],"conflictRules":["incompatible_voice_reuse","narrator_major_character_reuse","metadata_similarity_risk","accent_or_locale_mismatch","insufficient_expressive_range","rights_conflict","provider_or_model_unavailable","deprecated_voice","role_length_suitability","unresolved_role_assignment","voice_reuse_threshold_exceeded"],"deterministic":true,"explanationRequired":true,"externalSemanticDependency":false,"hardConstraints":["language_support","provider_available","model_available","rights_not_prohibited","rights_known","required_consent","voice_not_blocked","declared_capabilities","role_length_suitability"],"limits":{"defaultPageSize":50,"maximumConflictsPerRun":10000,"maximumExplanationCodePoints":2000,"maximumFinalCandidatesPerRole":12,"maximumHardConstraintResults":16,"maximumPageSize":200,"maximumPreReductionCandidatesPerRole":50,"maximumProductionRoles":300,"maximumSoftPreferenceResults":16,"maximumVoiceProfiles":5000,"maximumVoiceReusePerProfile":2,"maximumWarningsPerEntity":32},"producerId":"voice-casting-orchestrator@1.0.0","profileId":"governed-voice-casting-v1@1.0.0","providerNeutral":true,"rightsEligibilityRules":["verified_eligible","restricted_requires_acknowledgement","unknown_ineligible","prohibited_ineligible"],"rightsPolicyId":"voice-rights-policy-v1","softPreferences":["locale_match","narration_suitability","dialogue_suitability","expressive_range","age_presentation","vocal_presentation","vocal_texture","speaking_rate","emotional_range","long_form_preference"]}' as const;

export type VoiceCastingContractVersion =
  typeof VOICE_CASTING_CONTRACT_VERSION;
export type CastingJobStage = (typeof CASTING_JOB_STAGES)[number];
export type CastingHardConstraintId =
  (typeof CASTING_HARD_CONSTRAINT_IDS)[number];
export type CastingSoftPreferenceId =
  (typeof CASTING_SOFT_PREFERENCE_IDS)[number];
export type CastingConflictCategory =
  (typeof CASTING_CONFLICT_CATEGORIES)[number];
export type CastingCorrectionCategory =
  (typeof CASTING_CORRECTION_CATEGORIES)[number];
export type CastingGateId = (typeof CASTING_GATE_IDS)[number];

export interface CastingProvenance {
  readonly origin:
    | "development_fixture"
    | "local_catalog"
    | "runtime_agent"
    | "human"
    | "system";
  readonly producerId: string;
  readonly producerVersion: string;
  readonly recordedAt: IsoDateTime;
  readonly inputFingerprint?: Sha256;
  readonly sourceRevisionId?: EntityId;
  readonly reason?: string;
}

export type VoiceProviderType =
  | "local"
  | "cloud_capable_disabled"
  | "development_fixture";

export type DescriptorAvailability =
  | "available"
  | "unavailable"
  | "disabled";

export interface VoiceOutputCapability {
  readonly formats: readonly ("pcm_s16le" | "wav" | "mp3" | "unknown")[];
  readonly sampleRatesHz: readonly number[];
}

export interface VoiceProviderDescriptor {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly providerId: string;
  readonly providerVersion: string;
  readonly providerType: VoiceProviderType;
  readonly runtimeAvailability: DescriptorAvailability;
  readonly catalogAvailability: DescriptorAvailability;
  readonly synthesisImplemented: boolean;
  readonly networkUseRequired: boolean;
  readonly credentialsRequired: boolean;
  readonly supportedOperatingSystems: readonly (
    | "windows"
    | "macos"
    | "linux"
  )[];
  readonly supportedLanguages: readonly string[];
  readonly outputCapability: VoiceOutputCapability;
  readonly rightsMetadataCapabilities: readonly (
    | "license_identifier"
    | "commercial_use"
    | "attribution"
    | "distribution_limits"
    | "consent"
    | "effective_dates"
    | "evidence_reference"
  )[];
  readonly healthStatus:
    | "healthy"
    | "degraded"
    | "unavailable"
    | "disabled";
  readonly provenance: CastingProvenance;
}

export interface VoiceCapability {
  readonly supportedLanguages: readonly string[];
  readonly supportedLocales: readonly string[];
  readonly expressiveControls: readonly (
    | "energy"
    | "emotion"
    | "pace"
    | "pitch"
    | "style"
  )[];
  readonly speakingRateRange: {
    readonly minimum: number;
    readonly maximum: number;
    readonly unit: "multiplier";
  };
  readonly pitchControl:
    | "none"
    | "categorical"
    | "continuous";
  readonly styleControl:
    | "none"
    | "categorical"
    | "continuous";
  readonly outputCapability: VoiceOutputCapability;
}

export interface VoiceModelDescriptor {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly modelId: string;
  readonly providerId: string;
  readonly modelName: string;
  readonly modelVersion: string;
  readonly capability: VoiceCapability;
  readonly executionLocation: "local" | "remote";
  readonly licenseClassification:
    | "fixture_only"
    | "commercial"
    | "restricted"
    | "unknown"
    | "prohibited";
  readonly availability: DescriptorAvailability;
  readonly deprecated: boolean;
  readonly provenance: CastingProvenance;
}

export interface VoiceCatalogRevision {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly catalogRevisionId: EntityId;
  readonly revision: number;
  readonly semanticVersion: string;
  readonly rightsPolicyId: typeof VOICE_RIGHTS_POLICY_ID;
  readonly providerDescriptorIds: readonly string[];
  readonly modelDescriptorIds: readonly string[];
  readonly voiceProfileIds: readonly EntityId[];
  readonly createdAt: IsoDateTime;
  readonly immutable: true;
  readonly provenance: CastingProvenance;
  readonly catalogFingerprint: Sha256;
}

export type VoiceProfileState =
  | "active"
  | "unavailable"
  | "deprecated"
  | "blocked";
export type VoiceRightsState =
  | "verified"
  | "restricted"
  | "unknown"
  | "prohibited";
export type VocalPresentation =
  | "feminine"
  | "masculine"
  | "androgynous"
  | "neutral"
  | "varied"
  | "unspecified";
export type VocalTexture =
  | "airy"
  | "bright"
  | "clear"
  | "crisp"
  | "gravelly"
  | "resonant"
  | "smooth"
  | "warm"
  | "textured"
  | "varied"
  | "unspecified";
export type PitchRange =
  | "low"
  | "low_mid"
  | "mid"
  | "mid_high"
  | "high"
  | "wide"
  | "unspecified";
export type Suitability =
  | "preferred"
  | "suitable"
  | "limited"
  | "unsuitable"
  | "unknown";

export interface CastingVoiceProfile {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly voiceProfileId: EntityId;
  readonly providerId: string;
  readonly modelId: string;
  readonly providerVoiceId: string;
  readonly catalogRevisionId: EntityId;
  readonly displayLabel: string;
  readonly language: string;
  readonly locale: string;
  readonly accentOrDialect: string;
  readonly agePresentationRange: {
    readonly minimum: number;
    readonly maximum: number;
  };
  readonly vocalPresentation: VocalPresentation;
  readonly vocalTexture: VocalTexture;
  readonly pitchRange: PitchRange;
  readonly speakingRateRange: {
    readonly minimum: number;
    readonly maximum: number;
    readonly unit: "multiplier";
  };
  readonly energyRange: {
    readonly minimum: number;
    readonly maximum: number;
  };
  readonly expressiveRange: readonly string[];
  readonly narrationSuitability: Suitability;
  readonly dialogueSuitability: Suitability;
  readonly longFormSuitability: Suitability;
  readonly characterRoleSuitability: readonly (
    | "lead"
    | "supporting"
    | "minor"
    | "group"
    | "announcement"
    | "internal_thought"
  )[];
  readonly maximumRecommendedWords: number | null;
  readonly knownLimitations: readonly string[];
  readonly rightsRecordId: EntityId;
  readonly rightsState: VoiceRightsState;
  readonly licenseScope: string;
  readonly commercialUse:
    | "permitted"
    | "restricted"
    | "unknown"
    | "prohibited";
  readonly attributionRequired: boolean;
  readonly voiceCloningClassification:
    | "not_cloned_synthetic_fixture"
    | "provider_declared_non_cloned"
    | "unknown"
    | "prohibited";
  readonly consentStatus:
    | "not_applicable_synthetic_fixture"
    | "verified"
    | "restricted"
    | "missing"
    | "unknown"
    | "prohibited";
  readonly metadataSimilarityGroup: string | null;
  readonly reuseRiskGroup: string | null;
  readonly version: string;
  readonly state: VoiceProfileState;
  readonly provenance: CastingProvenance;
}

export interface VoiceRightsRecord {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly rightsRecordId: EntityId;
  readonly voiceProfileId: EntityId;
  readonly providerId: string;
  readonly revision: number;
  readonly state: VoiceRightsState;
  readonly licenseIdentifier: string;
  readonly rightsBasis: string;
  readonly commercialUsePermission:
    | "permitted"
    | "restricted"
    | "unknown"
    | "prohibited";
  readonly attributionRequirement:
    | "none"
    | "required"
    | "unknown"
    | "prohibited";
  readonly geographicLimitations: readonly string[];
  readonly distributionLimitations: readonly string[];
  readonly voiceCloningStatus:
    | "not_applicable_synthetic_fixture"
    | "not_permitted"
    | "permitted_with_consent"
    | "unknown"
    | "prohibited";
  readonly consentStatus:
    | "not_applicable_synthetic_fixture"
    | "verified"
    | "restricted"
    | "missing"
    | "unknown"
    | "prohibited";
  readonly effectiveDate: IsoDateTime | null;
  readonly expiresAt: IsoDateTime | null;
  readonly evidenceReference: string;
  readonly humanVerificationStatus:
    | "verified"
    | "not_required_fixture"
    | "pending"
    | "rejected";
  readonly provenance: CastingProvenance;
}

export interface SyntheticVoiceCatalog {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly catalogRevision: VoiceCatalogRevision;
  readonly providers: readonly VoiceProviderDescriptor[];
  readonly models: readonly VoiceModelDescriptor[];
  readonly voices: readonly CastingVoiceProfile[];
  readonly rights: readonly VoiceRightsRecord[];
  readonly fingerprint: Sha256;
}

export type ProductionRoleType =
  | "primary_narrator"
  | "secondary_narrator"
  | "named_character"
  | "unresolved_speaker"
  | "group_or_crowd"
  | "quoted_document_or_announcement"
  | "internal_thought"
  | "custom";

export interface ProductionRoleRange {
  readonly firstChapterOrdinal: number | null;
  readonly lastChapterOrdinal: number | null;
  readonly firstSceneOrdinal: number | null;
  readonly lastSceneOrdinal: number | null;
}

export interface ProductionRoleRequirement {
  readonly language: string;
  readonly locales: readonly string[];
  readonly agePresentationRange: {
    readonly minimum: number;
    readonly maximum: number;
  } | null;
  readonly vocalPresentations: readonly VocalPresentation[];
  readonly preferredTextures: readonly VocalTexture[];
  readonly speakingRateRange: {
    readonly minimum: number;
    readonly maximum: number;
    readonly unit: "multiplier";
  } | null;
  readonly requiredExpressiveRange: readonly string[];
  readonly longFormRequired: boolean;
}

interface ProductionRoleBase {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly roleId: EntityId;
  readonly projectId: EntityId;
  readonly roleType: ProductionRoleType;
  readonly phase2EntityId: EntityId | null;
  readonly effectiveDisplayLabel: string;
  readonly analysisRunId: EntityId;
  readonly analysisSnapshotId: EntityId;
  readonly analysisSnapshotRevision: number;
  readonly analysisSnapshotFingerprint: Sha256;
  readonly dialogueLineCount: number;
  readonly narrationSpanCount: number;
  readonly approximateWordCount: number;
  readonly range: ProductionRoleRange;
  readonly languageRequirements: readonly string[];
  readonly performanceRequirements: ProductionRoleRequirement;
  readonly warnings: readonly AnalysisWarning[];
  readonly provenance: CastingProvenance;
  readonly effectiveFingerprint: Sha256;
  readonly status:
    | "active"
    | "intentionally_uncast"
    | "unresolved"
    | "invalidated";
  readonly revision: number;
}

export interface NarratorRole extends ProductionRoleBase {
  readonly roleType:
    | "primary_narrator"
    | "secondary_narrator";
  readonly narratorKind: "primary" | "secondary";
}

export interface CharacterVoiceRole extends ProductionRoleBase {
  readonly roleType:
    | "named_character"
    | "unresolved_speaker"
    | "group_or_crowd"
    | "quoted_document_or_announcement"
    | "internal_thought"
    | "custom";
  readonly characterId: EntityId | null;
  readonly roleImportance:
    | "major"
    | "supporting"
    | "minor"
    | "unresolved";
  readonly unresolvedMaterialExplicitlyRepresented: boolean;
}

export type ProductionRole = NarratorRole | CharacterVoiceRole;

/**
 * An explicit, content-free human role. Its durable definition identity is
 * recorded in `provenance.sourceRevisionId`; it never implies manuscript
 * content or a Phase 2 entity.
 */
export type CustomProductionRole = CharacterVoiceRole & {
  readonly roleType: "custom";
  readonly phase2EntityId: null;
  readonly dialogueLineCount: 0;
  readonly narrationSpanCount: 0;
  readonly approximateWordCount: 0;
  readonly range: {
    readonly firstChapterOrdinal: null;
    readonly lastChapterOrdinal: null;
    readonly firstSceneOrdinal: null;
    readonly lastSceneOrdinal: null;
  };
  readonly characterId: null;
  readonly roleImportance: "supporting";
  readonly unresolvedMaterialExplicitlyRepresented: false;
  readonly provenance: CastingProvenance & {
    readonly origin: "human";
    readonly sourceRevisionId: EntityId;
    readonly reason: string;
  };
};

export interface CastingProfileValues {
  readonly profileId: typeof GOVERNED_VOICE_CASTING_PROFILE_ID;
  readonly castingContractVersion: VoiceCastingContractVersion;
  readonly producerId: typeof VOICE_CASTING_PRODUCER_ID;
  readonly rightsPolicyId: typeof VOICE_RIGHTS_POLICY_ID;
  readonly deterministic: true;
  readonly providerNeutral: true;
  readonly externalSemanticDependency: false;
  readonly explanationRequired: true;
  readonly compatibilityRules: typeof CASTING_COMPATIBILITY_RULES;
  readonly hardConstraints: typeof CASTING_HARD_CONSTRAINT_IDS;
  readonly softPreferences: typeof CASTING_SOFT_PREFERENCE_IDS;
  readonly conflictRules: typeof CASTING_CONFLICT_CATEGORIES;
  readonly rightsEligibilityRules:
    typeof CASTING_RIGHTS_ELIGIBILITY_RULES;
  readonly limits: typeof VOICE_CASTING_LIMITS;
}

export interface CastingProfile {
  readonly values: CastingProfileValues;
  readonly canonicalJson:
    typeof GOVERNED_VOICE_CASTING_PROFILE_CANONICAL_JSON;
  readonly fingerprint:
    typeof GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT;
}

export interface CastingProfileReference {
  readonly profileId: typeof GOVERNED_VOICE_CASTING_PROFILE_ID;
  readonly fingerprint:
    typeof GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT;
}

export interface CastingPhase2Prerequisites {
  readonly projectId: EntityId;
  readonly sourceDocumentId: EntityId;
  readonly sourceRevision: number;
  readonly extractionId: EntityId;
  readonly extractionRevision: number;
  readonly extractedTextSha256: Sha256;
  readonly importReviewDecisionId: EntityId;
  readonly analysisRunId: EntityId;
  readonly analysisSnapshotId: EntityId;
  readonly analysisSnapshotRevision: number;
  readonly analysisSnapshotFingerprint: Sha256;
  readonly analysisCorrectionSetFingerprint: Sha256;
  readonly characterRegistryFingerprint: Sha256;
  readonly phase2GateDecisionIds: {
    readonly storyStructureReview: EntityId;
    readonly characterRegistryReview: EntityId;
    readonly dialogueAttributionReview: EntityId;
    readonly wholeBookAnalysisReview: EntityId;
  };
  readonly evidenceFingerprint: Sha256;
}

export type CastingRunState =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted"
  | "invalidated";

export interface CastingRunCountSummary {
  readonly productionRoles: number;
  readonly narratorRoles: number;
  readonly characterRoles: number;
  readonly preReductionCandidates: number;
  readonly finalCandidates: number;
  readonly conflicts: number;
  readonly assignments: number;
  readonly corrections: number;
}

export interface CastingRun {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly castingRunId: EntityId;
  readonly projectId: EntityId;
  readonly prerequisites: CastingPhase2Prerequisites;
  readonly profile: CastingProfileReference;
  readonly producerId: typeof VOICE_CASTING_PRODUCER_ID;
  readonly catalogRevisionId: EntityId;
  readonly catalogFingerprint: Sha256;
  readonly effectiveCorrectionSetFingerprint: Sha256;
  readonly inputFingerprint: Sha256;
  readonly outputFingerprint: Sha256 | null;
  readonly idempotencyFingerprint: Sha256;
  readonly jobId: EntityId;
  readonly status: CastingRunState;
  readonly currentStage:
    | "queued"
    | CastingJobStage
    | "complete";
  readonly progress: number;
  readonly checkpoint: {
    readonly checkpointId: EntityId;
    readonly stage: CastingJobStage;
    readonly fingerprint: Sha256;
    readonly recordedAt: IsoDateTime;
  } | null;
  readonly attempt: number;
  readonly retryPolicy: {
    readonly maxAttempts: number;
    readonly retryableFailureCodes: readonly string[];
  };
  readonly failurePolicy:
    "fail_closed_preserve_effective_cast_snapshot";
  readonly resumeOfCastingRunId: EntityId | null;
  readonly retryOfCastingRunId: EntityId | null;
  readonly retryClassification:
    | "retryable"
    | "not_retryable"
    | "retry_exhausted";
  readonly cancellationRequested: boolean;
  readonly warnings: readonly AnalysisWarning[];
  readonly summary: CastingRunCountSummary | null;
  readonly approvedCastSnapshot: ApprovedCastSnapshot | null;
  readonly createdAt: IsoDateTime;
  readonly updatedAt: IsoDateTime;
  readonly completedAt: IsoDateTime | null;
  readonly failure: {
    readonly code: string;
    readonly redactedMessage: string;
    readonly retryable: boolean;
    readonly redacted: true;
  } | null;
}

export type CompatibilityStatus =
  | "compatible"
  | "compatible_with_warnings"
  | "incompatible"
  | "unknown";
export type ConstraintResult = "pass" | "fail" | "unknown";

export interface CastingConstraintResult {
  readonly constraintId: CastingHardConstraintId;
  readonly result: ConstraintResult;
  readonly explanation: string;
}

export interface CastingPreferenceResult {
  readonly preferenceId: CastingSoftPreferenceId;
  readonly score: number;
  readonly explanation: string;
}

export type RightsEligibility =
  | "eligible"
  | "restricted_requires_acknowledgement"
  | "ineligible_unknown"
  | "ineligible_prohibited";

export interface CastingCompatibilityAssessment {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly assessmentId: EntityId;
  readonly roleId: EntityId;
  readonly voiceProfileId: EntityId;
  readonly compatibilityStatus: CompatibilityStatus;
  readonly compatibilityScore: number;
  readonly confidence: AnalysisConfidence;
  readonly hardConstraints: readonly CastingConstraintResult[];
  readonly softPreferences: readonly CastingPreferenceResult[];
  readonly rightsEligibility: RightsEligibility;
  readonly languageEligibility: ConstraintResult;
  readonly providerAvailability: DescriptorAvailability;
  readonly modelAvailability: DescriptorAvailability;
  readonly longFormSuitability: Suitability;
  readonly explanation: string;
  readonly provenance: CastingProvenance;
  readonly inputFingerprint: Sha256;
  /**
   * Immutable fingerprint of the governed machine-assessment evidence before
   * it is reshaped for this public contract.
   */
  readonly baseEvidenceFingerprint: Sha256;
  /** Fingerprint of this exact public projection, excluding this field. */
  readonly outputFingerprint: Sha256;
}

export interface CastingCandidate {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly candidateId: EntityId;
  readonly castingRunId: EntityId;
  readonly roleId: EntityId;
  readonly voiceProfileId: EntityId;
  readonly rank: number;
  readonly preReductionRank: number;
  readonly assessment: CastingCompatibilityAssessment;
  readonly conflictIds: readonly EntityId[];
  readonly conflictWarnings: readonly AnalysisWarning[];
  readonly rejectedByCorrectionId: EntityId | null;
  readonly provenance: CastingProvenance;
  readonly inputFingerprint: Sha256;
  /**
   * Immutable fingerprint of the persisted governed machine-candidate
   * evidence. It does not change when correction/conflict projections change.
   */
  readonly baseEvidenceFingerprint: Sha256;
  /** Fingerprint of this exact public projection, excluding this field. */
  readonly outputFingerprint: Sha256;
}

export interface CastingConflict {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly conflictId: EntityId;
  readonly castingRunId: EntityId;
  readonly category: CastingConflictCategory;
  readonly severity: "info" | "warning" | "error" | "blocker";
  readonly roleIds: readonly EntityId[];
  readonly voiceProfileIds: readonly EntityId[];
  readonly explanation: string;
  readonly metadataOnly: true;
  readonly acousticSimilarityClaimed: false;
  readonly resolutionState:
    | "open"
    | "acknowledged"
    | "approved_reuse"
    | "resolved"
    | "superseded";
  readonly dispositionCorrectionId: EntityId | null;
  readonly provenance: CastingProvenance;
  readonly inputFingerprint: Sha256;
  /**
   * Immutable fingerprint of the persisted metadata-only conflict evidence.
   * It does not change when the resolution projection changes.
   */
  readonly baseEvidenceFingerprint: Sha256;
  /** Fingerprint of this exact public projection, excluding this field. */
  readonly outputFingerprint: Sha256;
}

export type AssignmentAuthority =
  | "machine_proposal"
  | "human_selection"
  | "human_locked";

export interface CastAssignment {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly assignmentId: EntityId;
  readonly projectId: EntityId;
  readonly roleId: EntityId;
  readonly voiceProfileId: EntityId;
  readonly voiceProfileVersion: string;
  readonly voiceEvidenceFingerprint: Sha256;
  readonly rightsRecordId: EntityId;
  readonly rightsRecordRevision: number;
  readonly rightsEvidenceFingerprint: Sha256;
  readonly catalogRevisionId: EntityId;
  readonly castingRunId: EntityId;
  readonly castingProfileFingerprint: Sha256;
  readonly phase2SnapshotFingerprint: Sha256;
  readonly effectiveCorrectionSetFingerprint: Sha256;
  readonly authority: AssignmentAuthority;
  readonly rationale: string;
  readonly warnings: readonly AnalysisWarning[];
  readonly rightsState: VoiceRightsState;
  readonly revision: number;
  readonly provenance: CastingProvenance;
  readonly supersedesAssignmentId: EntityId | null;
  readonly effective: boolean;
}

interface CastingCorrectionBase {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly correctionId: EntityId;
  readonly projectId: EntityId;
  readonly castingRunId: EntityId;
  readonly targetRoleId: EntityId;
  readonly priorEffectiveFingerprint: Sha256;
  readonly correctedValueFingerprint: Sha256;
  readonly actor: {
    readonly classification: "human";
    readonly actorId: EntityId;
  };
  readonly reason: string;
  readonly recordedAt: IsoDateTime;
  readonly provenance: CastingProvenance;
  readonly immutable: true;
  readonly lockedAgainstAutomation: true;
  readonly supersedesCorrectionId: EntityId | null;
  readonly idempotencyFingerprint: Sha256;
}

export type CastingCorrectionSelection =
  | {
      readonly category: "select_voice";
      readonly correctedValue: {
        readonly voiceProfileId: EntityId;
      };
    }
  | {
      readonly category: "clear_assignment";
      readonly correctedValue: {
        readonly expectedAssignmentId: EntityId;
      };
    }
  | {
      readonly category: "lock_assignment";
      readonly correctedValue: {
        readonly assignmentId: EntityId;
      };
    }
  | {
      readonly category: "unlock_assignment";
      readonly correctedValue: {
        readonly lockedAssignmentId: EntityId;
      };
    }
  | {
      readonly category: "mark_intentionally_uncast";
      readonly correctedValue: {
        readonly intentionallyUncast: true;
      };
    }
  | {
      readonly category: "change_role_label";
      readonly correctedValue: {
        readonly effectiveDisplayLabel: string;
      };
    }
  | {
      readonly category: "change_casting_requirement";
      readonly correctedValue: {
        readonly requirement: ProductionRoleRequirement;
      };
    }
  | {
      readonly category: "acknowledge_restricted_rights";
      readonly correctedValue: {
        readonly rightsRecordId: EntityId;
        readonly rightsRecordRevision: number;
      };
    }
  | {
      readonly category: "approve_voice_reuse";
      readonly correctedValue: {
        readonly conflictId: EntityId;
        readonly approvedRoleIds: readonly EntityId[];
      };
    }
  | {
      readonly category: "reject_candidate";
      readonly correctedValue: {
        readonly candidateId: EntityId;
      };
    }
  | {
      readonly category: "record_custom_rationale";
      readonly correctedValue: {
        readonly rationale: string;
      };
    };

export type CastingCorrection =
  CastingCorrectionBase & CastingCorrectionSelection;

export type CastingGateState =
  | "pending"
  | "approved"
  | "changes_requested"
  | "rejected"
  | "invalidated";

export interface CastingGateEvidence {
  readonly projectId: EntityId;
  readonly castingRunId: EntityId;
  readonly approvedCastSnapshotId: EntityId;
  readonly approvedCastSnapshotRevision: number;
  readonly approvedCastSnapshotFingerprint: Sha256;
  readonly phase2SnapshotFingerprint: Sha256;
  readonly catalogRevisionId: EntityId;
  readonly catalogFingerprint: Sha256;
  readonly castingProfileFingerprint: Sha256;
  readonly effectiveCorrectionSetFingerprint: Sha256;
  readonly evidenceFingerprint: Sha256;
}

export interface CastingGateReview {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly reviewId: EntityId;
  readonly gateId: CastingGateId;
  readonly projectId: EntityId;
  readonly castingRunId: EntityId;
  readonly state: CastingGateState;
  readonly revision: number;
  readonly prerequisiteGateIds: readonly CastingGateId[];
  readonly evidence: CastingGateEvidence;
  readonly openWarningIds: readonly EntityId[];
  readonly acknowledgedWarningIds: readonly EntityId[];
  readonly latestDecision: CastingReviewDecision | null;
  readonly provenance: CastingProvenance;
  readonly updatedAt: IsoDateTime;
}

export interface CastingReviewDecision {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly decisionId: EntityId;
  readonly reviewId: EntityId;
  readonly gateId: CastingGateId;
  readonly projectId: EntityId;
  readonly castingRunId: EntityId;
  readonly approvedCastSnapshotId: EntityId;
  readonly approvedCastSnapshotRevision: number;
  readonly evidenceFingerprint: Sha256;
  readonly decision:
    | "approved"
    | "changes_requested"
    | "rejected"
    | "invalidated";
  readonly actor: {
    readonly classification: "human" | "system";
    readonly actorId: EntityId;
  };
  readonly acknowledgedWarningIds: readonly EntityId[];
  readonly rationale: string;
  readonly decidedAt: IsoDateTime;
  readonly provenance: CastingProvenance;
  readonly immutable: true;
  readonly supersedesDecisionId: EntityId | null;
}

export interface ApprovedCastSnapshot {
  readonly contractVersion: VoiceCastingContractVersion;
  readonly snapshotId: EntityId;
  readonly castingRunId: EntityId;
  readonly projectId: EntityId;
  readonly revision: number;
  readonly phase2SnapshotFingerprint: Sha256;
  readonly catalogRevisionId: EntityId;
  readonly catalogFingerprint: Sha256;
  readonly castingProfileFingerprint: Sha256;
  readonly effectiveCorrectionSetFingerprint: Sha256;
  readonly assignmentIds: readonly EntityId[];
  readonly intentionallyUncastRoleIds: readonly EntityId[];
  readonly unresolvedConflictIds: readonly EntityId[];
  readonly counts: CastingRunCountSummary;
  readonly snapshotFingerprint: Sha256;
  /** Frozen eligibility at snapshot publication; current authority lives in gate reviews. */
  readonly reviewEligible: boolean;
  readonly createdAt: IsoDateTime;
  readonly immutable: true;
}
